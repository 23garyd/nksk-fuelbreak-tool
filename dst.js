/* ==========================================================================
   NKSK Decision Support (DST) layer — priority scoring of management units
   --------------------------------------------------------------------------
   Data comes from data/dst/ (built by dst/build_dst_data.py from
   dst/dst_config.py). This file does not need editing when new layers arrive:
   criteria, labels, directions and "awaiting data" status all come from
   data/dst/criteria.json.

   Score for each unit  =  Σ_branches  W_b × ( Σ_criteria w_c × u_c )
     W_b  branch weight from the anchor + 0–9 sliders
     w_c  criterion weight inside the branch (0–9 sliders, normalised)
     u_c  the unit's criterion value rescaled to 0–1 (1 = higher priority)
   Criteria with no data (awaiting, or null for that unit) are left out and
   the remaining weights are renormalised, so the score stays on 0–1.

   Test without any data:  open the tool with  ?dstdemo=1  in the URL
   (e.g. http://localhost:8000/?dstdemo=1). Every criterion gets synthetic
   values so the sliders, map, legend and popups can be exercised.
   ========================================================================== */
const DST = (() => {
  const PATH = 'data/dst/';
  const DEMO = /[?&]dstdemo=1\b/.test(location.search);
  const RAMP = ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725']; // purple → yellow (colour-blind safe)
  const NODATA = '#d9d8cf';

  let META = null, UNITS = null, SEGU = {}, UNIT_SEGS = {}, layer = null, ready = false, pendingState = null;
  let SCORE = {}, CONTRIB = {}, RANKED = [];
  const S = { on: true, anchor: null, bw: {}, cw: {}, op: 0.55 };

  /* ---------- live criteria: computed from the road-segment cost model ----------
     key = the `source` name used for kind="live" in dst_config.py.
     Each gets the list of (non-young-lava) segments inside a unit.            */
  const LIVE = {
    impl_per_km: segs => perKm(segs, c => c.impl_t),
    maint_per_km: segs => perKm(segs, c => c.maint_t),
    establish: segs => segs.length ? 1 - segs.reduce((a, s) => a + segDroughtP(s)[0], 0) / segs.length : null,
  };
  function perKm(segs, f) {
    let v = 0, km = 0;
    segs.forEach(s => { v += f(compute(s)); km += s.length_m / 1000; });
    return km ? v / km : null;
  }

  /* ---------- helpers ---------- */
  const allCrit = () => META.branches.flatMap(b => b.criteria);
  const isActive = c => DEMO || c.status === 'ready';
  const activeCrit = b => b.criteria.filter(isActive);
  const activeBranches = () => META.branches.filter(b => activeCrit(b).length);
  const fmt = v => v == null ? '—' : Math.abs(v) >= 1000 ? Math.round(v).toLocaleString() : (+v.toPrecision(3)).toString();
  function hexMix(t) {
    t = Math.max(0, Math.min(1, t)) * (RAMP.length - 1);
    const i = Math.min(RAMP.length - 2, Math.floor(t)), f = t - i;
    const a = RAMP[i].match(/\w\w/g).map(h => parseInt(h, 16)), b = RAMP[i + 1].match(/\w\w/g).map(h => parseInt(h, 16));
    return '#' + a.map((x, k) => Math.round(x + (b[k] - x) * f).toString(16).padStart(2, '0')).join('');
  }

  /* ---------- raw values ---------- */
  function demoValue(f, c, k) {                // smooth fake surface, different per criterion
    const [x, y] = f._ctr, h = k * 1.7 + 0.3;
    return 0.5 + 0.5 * Math.sin(x * 9 * (1 + k % 3) + h) * Math.cos(y * 7 + h * 2);
  }
  function rawValues(c, k) {                  // -> {unit_id: value|null}
    const out = {};
    UNITS.features.forEach(f => {
      const id = f.properties.unit_id;
      if (DEMO && c.status !== 'ready') out[id] = demoValue(f, c, k);
      else if (c.kind === 'live') { const fn = LIVE[c.live]; out[id] = fn ? fn(UNIT_SEGS[id] || []) : null; }
      else { const v = f.properties['c_' + c.id]; out[id] = (v == null || Number.isNaN(v)) ? null : v; }
    });
    return out;
  }

  /* ---------- weights ---------- */
  function branchWeights() {
    const act = activeBranches(), w = {};
    if (!act.length) return w;
    if (!act.some(b => b.id === S.anchor)) S.anchor = act[0].id;
    act.forEach(b => {
      const s = b.id === S.anchor ? null : (S.bw[b.id] ?? META.default_branch_score);
      w[b.id] = META.anchor_mode === 'ratio'
        ? (s == null ? 1 : 1 / Math.max(1, s))       // anchor = 1, others 1/N
        : (s == null ? 9 : s);                        // anchor = 9, others 0–9
    });
    const t = Object.values(w).reduce((a, b) => a + b, 0) || 1;
    Object.keys(w).forEach(k => w[k] /= t);
    return w;
  }

  /* ---------- scoring ---------- */
  function score() {
    SCORE = {}; CONTRIB = {};
    const W = branchWeights();
    const U = {};                               // crit id -> {unit: 0–1 utility}
    allCrit().forEach((c, k) => {
      if (!isActive(c)) return;
      const raw = rawValues(c, k), vals = Object.values(raw).filter(v => v != null);
      let lo = c.lo, hi = c.hi;
      if (DEMO && c.status !== 'ready') { lo = 0; hi = 1; }
      if (lo == null) lo = Math.min(...vals);
      if (hi == null) hi = Math.max(...vals);
      U[c.id] = {};
      for (const [id, v] of Object.entries(raw)) {
        if (v == null) { U[c.id][id] = null; continue; }
        let u = hi > lo ? (v - lo) / (hi - lo) : 0.5;
        u = Math.max(0, Math.min(1, u));
        U[c.id][id] = { u: c.dir < 0 ? 1 - u : u, raw: v };
      }
    });
    UNITS.features.forEach(f => {
      const id = f.properties.unit_id;
      let num = 0, den = 0; const parts = [];
      activeBranches().forEach(b => {
        const Wb = W[b.id] || 0; if (!Wb) return;
        const cs = activeCrit(b).map(c => ({ c, w: S.cw[c.id] ?? META.default_criterion_score, x: U[c.id][id] }))
          .filter(o => o.x && o.w > 0);
        const sw = cs.reduce((a, o) => a + o.w, 0); if (!sw) return;
        cs.forEach(o => parts.push({ c: o.c, b, share: Wb * o.w / sw, u: o.x.u, raw: o.x.raw }));
        num += Wb * cs.reduce((a, o) => a + o.w * o.x.u, 0) / sw; den += Wb;
      });
      SCORE[id] = den ? num / den : null;
      CONTRIB[id] = parts.map(p => ({ ...p, share: p.share / den }));
    });
    RANKED = Object.entries(SCORE).filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]).map(([k]) => k);
  }

  /* ---------- map ---------- */
  function style(f) {
    const s = SCORE[f.properties.unit_id];
    return { color: '#ffffff', weight: 0.7, opacity: S.on ? 0.9 : 0,
      fillColor: s == null ? NODATA : hexMix(s), fillOpacity: S.on ? (s == null ? 0.25 : S.op) : 0 };
  }
  function drawLayer() {
    if (!layer) {
      if (!map.getPane('dstPane')) { map.createPane('dstPane').style.zIndex = 350; } // below road segments
      layer = L.geoJSON(UNITS, {
        pane: 'dstPane', style,
        onEachFeature: (f, l) => {
          l.bindTooltip(() => { const s = SCORE[f.properties.unit_id];
            return `${f.properties.name}<br>Priority: ${s == null ? 'no data' : s.toFixed(2)}`; }, { className: 'tip', sticky: true });
          l.on('click', e => { if (S.on) openPopup(f, e.latlng); });
        }
      }).addTo(map);
    }
    layer.setStyle(style);
    layer.eachLayer(l => { const el = l.getElement && l.getElement(); if (el) el.style.pointerEvents = S.on ? '' : 'none'; });
    drawLegend();
  }
  function drawLegend() {
    let el = document.getElementById('dstlegend');
    if (!el) { el = document.createElement('div'); el.id = 'dstlegend'; el.className = 'legend'; el.style.left = 'auto'; el.style.right = '11px';
      document.querySelector('.mapwrap').appendChild(el); }
    el.style.display = S.on ? 'block' : 'none';
    const mz = document.getElementById('mzlegend');
    el.style.bottom = (mz && mz.style.display !== 'none' ? mz.offsetHeight + 22 : 11) + 'px';
    const any = RANKED.length;
    el.innerHTML = `<div class="lt">Priority score${META.placeholder_units ? ' · placeholder units' : ''}</div>` +
      (any ? `<div class="ramp" style="background:linear-gradient(90deg,${RAMP.join(',')})"></div>
              <div class="ramplab"><span>lower</span><span>higher</span></div>` :
             `<div style="font-size:12px;color:var(--muted)">No criteria have data yet —<br>unit outlines only.</div>`) +
      `<div class="legdiv"></div><div class="legcat"><span><i style="background:${NODATA}"></i>No data</span></div>` +
      (DEMO ? `<div class="hint flag" style="margin-top:4px">DEMO values — not real data</div>` : '');
  }
  let popup = null;
  function popupHTML(f) {
    const id = f.properties.unit_id, s = SCORE[id], r = RANKED.indexOf(id);
    const parts = (CONTRIB[id] || []).slice().sort((a, b) => b.share * b.u - a.share * a.u);
    const missing = allCrit().filter(c => !parts.some(p => p.c.id === c.id));
    return `<div class="dtitle">${f.properties.name}</div>
      <div class="dsub">${id}${f.properties.type ? ' · ' + f.properties.type : ''} · ${Math.round(f.properties.area_ha).toLocaleString()} ha${(UNIT_SEGS[id] || []).length ? ' · ' + UNIT_SEGS[id].length + ' adjacent road segments' : ''}</div>
      <div class="kv"><span class="k">Priority score</span><span class="v">${s == null ? '—' : s.toFixed(3)}</span></div>
      <div class="kv"><span class="k">Rank</span><span class="v">${r < 0 ? '—' : (r + 1) + ' of ' + RANKED.length}</span></div>
      ${parts.length ? `<div class="dhd" style="margin-top:10px">What drives the score</div>
      <div class="bars">${parts.map(p => `<div class="bar" title="${p.c.label}: ${fmt(p.raw)} ${p.c.units} → ${p.u.toFixed(2)} on 0–1 · weight ${(p.share * 100).toFixed(0)}%">
        <span class="bn">${p.c.label}</span><span class="bt"><span class="bf" style="width:${p.share * p.u * 100 / (s || 1)}%"></span></span>
        <span class="bv">${(p.share * p.u).toFixed(3)}</span></div>`).join('')}</div>
      <div class="hint">Bars = weight × rescaled value; they add up to the score. Hover a bar for the raw value.</div>` : ''}
      ${missing.length ? `<div class="dhd" style="margin-top:10px">Not included</div>
      <div class="hint" style="margin-top:0">${missing.map(c => c.label + (c.status === 'ready' ? ' (no value here / weight 0)' : ' (awaiting data)')).join(' · ')}</div>` : ''}`;
  }
  function openPopup(f, ll) {
    popup = L.popup({ className: 'segpop dstpop', maxWidth: 480, minWidth: 440 })
      .setLatLng(ll || L.geoJSON(f).getBounds().getCenter())
      .setContent('<div class="segpopbody">' + popupHTML(f) + '</div>').openOn(map);
    popup._dstUnit = f;
    map.once('popupclose', () => { popup = null; });
  }

  /* ---------- sidebar ---------- */
  function sliderRow(id, kind, val, disabled, lbl, extra) {
    return `<div class="dst-row ${disabled ? 'off' : ''}" ${extra || ''}>
      ${lbl}
      <input type="range" min="${kind === 'b' && META.anchor_mode === 'ratio' ? 1 : 0}" max="9" step="1" value="${val}"
        data-k="${kind}" data-id="${id}" ${disabled ? 'disabled' : ''}>
    </div>`;
  }
  function buildPanel() {
    const el = document.getElementById('dstblk'); if (!el) return;
    el.style.display = '';
    const W = branchWeights();
    const modeTxt = META.anchor_mode === 'ratio'
      ? 'Pick the value that matters most (anchor). For each other value, set how many <b>times</b> more important the anchor is (1 = equal, 9 = far more).'
      : 'Pick the value that matters most (anchor). Rate each other value 0–9 compared to it (<b>9 = as important</b> as the anchor, 0 = ignore).';
    const banners = [];
    if (DEMO) banners.push('<div class="hint flag">Demo mode: synthetic values for every criterion. Remove <span class="mono">?dstdemo=1</span> for real data.</div>');
    if (META.placeholder_units) banners.push('<div class="hint flag">Placeholder grid — replace with the NKSK management units in <span class="mono">dst/dst_config.py</span>.</div>');
    if (!activeBranches().length) banners.push('<div class="hint">No criteria have data yet. Values show as <i>awaiting data</i> until their layers are added.</div>');
    el.innerHTML = `
      <h2>Priority scoring <label class="dst-tog"><input type="checkbox" id="dst_on" ${S.on ? 'checked' : ''}> show</label></h2>
      ${banners.join('')}
      <div class="hint" style="margin:6px 0 8px">${modeTxt}</div>
      ${META.branches.map(b => {
        const act = activeCrit(b).length > 0, isA = b.id === S.anchor;
        const bval = S.bw[b.id] ?? META.default_branch_score;
        const lbl = `<div class="dst-hd">
            <label class="dst-an" title="Make this the anchor"><input type="radio" name="dst_anchor" value="${b.id}" ${isA ? 'checked' : ''} ${act ? '' : 'disabled'}>
              <span class="nm">${b.label}</span></label>
            <span class="dst-w">${act ? (isA ? 'anchor · ' : (META.anchor_mode === 'ratio' ? '1/' + bval + ' · ' : bval + ' · ')) + Math.round((W[b.id] || 0) * 100) + '%' : '<span class="pill excl">awaiting data</span>'}</span>
          </div>`;
        const crits = b.criteria.map(c => {
          const on = isActive(c), v = S.cw[c.id] ?? META.default_criterion_score;
          return sliderRow(c.id, 'c', v, !on,
            `<div class="dst-sub"><span title="${(c.desc + (c.note ? ' — ' + c.note : '')).replace(/"/g, '&quot;')}">${c.label}${c.dir < 0 ? ' <em>(lower = better)</em>' : ''}</span>
             <span class="dst-w">${on ? v : (c.status === 'error' ? '<span class="pill excl">error</span>' : '<span class="pill excl">awaiting</span>')}</span></div>`);
        }).join('');
        return `<div class="dst-br ${act ? '' : 'off'}">
          ${isA ? `<div class="dst-row">${lbl}<div class="dst-anchorbar">anchor</div></div>` : sliderRow(b.id, 'b', bval, !act, lbl)}
          ${b.criteria.length === 1 ? `<div class="hint" style="margin:1px 0 0 20px">${b.criteria[0].desc}</div>`
            : `<details><summary>${b.criteria.length} criteria · ${activeCrit(b).length} with data</summary>${crits}</details>`}
        </div>`;
      }).join('')}
      <label class="fld">Layer opacity <span class="val">${Math.round(S.op * 100)}%</span></label>
      <input type="range" id="dst_op" min="10" max="100" step="5" value="${Math.round(S.op * 100)}">
      <div class="dst-top">${RANKED.length ? '<div class="dhd">Top priority units</div>' + RANKED.slice(0, 5).map((id, i) =>
        `<div class="dst-u" data-u="${id}"><span>${i + 1}. ${unitById(id).properties.name} <span class="mono" style="color:var(--muted);font-size:11px">${id}</span></span><span class="mono">${SCORE[id].toFixed(2)}</span></div>`).join('') : ''}</div>
      <div class="row2" style="margin-top:9px"><button class="btn gho" id="dst_reset">Reset weights</button></div>`;
    wire(el);
  }
  const unitById = id => UNITS.features.find(f => f.properties.unit_id === id);
  function wire(el) {
    el.querySelector('#dst_on').addEventListener('change', e => { S.on = e.target.checked; drawLayer(); syncHash(); });
    el.querySelectorAll('input[name=dst_anchor]').forEach(r => r.addEventListener('change', e => { S.anchor = e.target.value; update(); }));
    el.querySelectorAll('input[type=range][data-k]').forEach(r => {
      r.addEventListener('input', e => {                       // live label update while dragging
        const t = e.target; (t.dataset.k === 'b' ? S.bw : S.cw)[t.dataset.id] = +t.value;
        score(); drawLayer();
      });
      r.addEventListener('change', () => update());              // full rebuild on release
    });
    el.querySelector('#dst_op').addEventListener('input', e => { S.op = +e.target.value / 100;
      e.target.previousElementSibling.querySelector('.val').textContent = e.target.value + '%'; drawLayer(); });
    el.querySelector('#dst_op').addEventListener('change', () => syncHash());
    el.querySelector('#dst_reset').addEventListener('click', () => { S.bw = {}; S.cw = {}; S.anchor = null; update(); });
    el.querySelectorAll('.dst-u').forEach(d => d.addEventListener('click', () => {
      const f = unitById(d.dataset.u), b = L.geoJSON(f).getBounds();
      map.fitBounds(b, { maxZoom: 12, padding: [40, 40] }); openPopup(f, b.getCenter());
    }));
    // keep <details> open state across rebuilds
    el.querySelectorAll('details').forEach((d, i) => { if (openDetails.has(i)) d.open = true;
      d.addEventListener('toggle', () => d.open ? openDetails.add(i) : openDetails.delete(i)); });
  }
  const openDetails = new Set();
  function update() {
    score(); drawLayer(); buildPanel();
    if (popup && popup._dstUnit) popup.setContent('<div class="segpopbody">' + popupHTML(popup._dstUnit) + '</div>');
    syncHash();
  }

  function injectCSS() {
    const css = `
    #dstblk .dst-tog{font-family:"Barlow";font-size:12px;letter-spacing:0;text-transform:none;color:var(--ink2);display:flex;gap:4px;align-items:center;cursor:pointer}
    #dstblk .dst-br{border-top:1px dotted var(--line);padding:7px 0 4px}
    #dstblk .dst-br.off{opacity:.55}
    #dstblk .dst-hd,#dstblk .dst-sub{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
    #dstblk .dst-an{display:flex;align-items:center;gap:6px;cursor:pointer;min-width:0}
    #dstblk .dst-an input{accent-color:var(--accent)}
    #dstblk .dst-an .nm{font-family:"Barlow Condensed";font-weight:600;font-size:15px;line-height:1.1}
    #dstblk .dst-w{font-family:"IBM Plex Mono";font-size:12px;color:var(--accent);white-space:nowrap}
    #dstblk .dst-anchorbar{height:20px;display:flex;align-items:center;justify-content:center;font-family:"Barlow Condensed";font-weight:600;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#fff;background:var(--accent2);border-radius:3px;margin:3px 0}
    #dstblk details{margin:2px 0 0 20px}
    #dstblk summary{font-size:12px;color:var(--muted);cursor:pointer}
    #dstblk .dst-sub{font-size:13px;color:var(--ink2);margin-top:5px}
    #dstblk .dst-sub em{font-style:normal;color:var(--muted);font-size:11.5px}
    #dstblk .dst-row.off input[type=range]{opacity:.35;cursor:not-allowed}
    #dstblk .dst-top{margin-top:10px}
    #dstblk .dhd{font-family:"Barlow Condensed";font-weight:600;font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin:0 0 3px}
    #dstblk .dst-u{display:flex;justify-content:space-between;font-size:13.5px;padding:3px 0;cursor:pointer;border-bottom:1px dotted var(--line)}
    #dstblk .dst-u:hover{color:var(--accent)}
    #dstblk .pill{margin-left:0}
    .dstpop .bar .bn{width:150px;text-align:left}`;
    const s = document.createElement('style'); s.textContent = css; document.head.appendChild(s);
  }

  /* ---------- public API (called from index.html) ---------- */
  async function init() {
    try {
      const v = typeof DV === 'string' ? DV : '';
      [META, UNITS] = await Promise.all([
        fetch(PATH + 'criteria.json' + v).then(r => { if (!r.ok) throw 0; return r.json(); }),
        fetch(PATH + 'units.geojson' + v).then(r => { if (!r.ok) throw 0; return r.json(); })]);
      try { SEGU = await (await fetch(PATH + 'seg_units.json' + v)).json(); } catch (e) { SEGU = {}; }
    } catch (e) {                       // no DST data deployed -> feature stays hidden
      const o = document.querySelector('#prule option[value=dst]'); if (o) o.remove();
      return;
    }
    UNITS.features.forEach(f => { const c = L.geoJSON(f).getBounds().getCenter(); f._ctr = [c.lng, c.lat]; });
    buildUnitSegs();
    if (pendingState) applyState(pendingState);
    injectCSS(); ready = true;
    score(); drawLayer(); buildPanel();
  }
  // seg_units.json: segment id -> [[unit_id, area weight], ...] (older builds: a single unit id)
  const segLinks = id => { const v = SEGU[id]; return !v ? [] : typeof v === 'string' ? [[v, 1]] : v; };
  function buildUnitSegs() {                // unit -> segments along it (a border segment counts for each side)
    UNIT_SEGS = {};
    SEG.forEach(s => { if (!s.excl) segLinks(s.id).forEach(([u]) => (UNIT_SEGS[u] = UNIT_SEGS[u] || []).push(s)); });
  }
  function segScore(id) {                   // area-weighted mean of the adjacent units' scores
    let n = 0, d = 0;
    segLinks(id).forEach(([u, w]) => { const s = SCORE[u]; if (s != null) { n += w * s; d += w; } });
    return d ? n / d : null;
  }
  function segHTML(id) {                    // block for the road-segment popup
    const links = segLinks(id);
    if (!ready || !S.on || !links.length) return '';
    const s = segScore(id);
    return `<div class="dhd" style="margin-top:11px">Priority score · ${s == null ? '—' : s.toFixed(2)}</div>
      <div class="hint" style="margin-top:0">Area-weighted mean of the units along this road:</div>
      ${links.map(([u, w]) => { const f = unitById(u), su = SCORE[u];
        return `<div class="kv"><span class="k">${f ? f.properties.name : u} <span class="mono" style="font-size:11px;color:var(--muted)">${u}</span></span>
          <span class="v">${su == null ? '—' : su.toFixed(2)} <span style="color:var(--muted);font-weight:400">× ${Math.round(w * 100)}%</span></span></div>`; }).join('')}`;
  }
  function refresh() {                  // called from renderAll(): cost inputs changed
    if (!ready || !allCrit().some(c => isActive(c) && c.kind === 'live' && c.status === 'ready')) return;
    score(); drawLayer(); buildPanel();
    if (popup && popup._dstUnit) popup.setContent('<div class="segpopbody">' + popupHTML(popup._dstUnit) + '</div>');
  }
  function planKey(segId, c) {          // used by buildPlan(): smaller = picked first
    const s = segScore(segId);
    return s == null ? 1e9 + c.tot / c.area : -s;
  }
  function getState() { return ready ? { on: S.on ? 1 : 0, a: S.anchor, bw: S.bw, cw: S.cw, op: S.op } : pendingState; }
  function applyState(o) {
    if (!o) return;
    S.on = o.on !== 0; S.anchor = o.a || null; S.bw = o.bw || {}; S.cw = o.cw || {};
    if (o.op) S.op = o.op;
  }
  function setState(o) { if (ready) { applyState(o); update(); } else pendingState = o; }

  return { init, refresh, planKey, segHTML, getState, setState, scores: () => SCORE, segScore };
})();
