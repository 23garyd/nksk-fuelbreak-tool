"""
Build the data files for the NKSK Decision Support (DST) layer.

    python dst/build_dst_data.py

Reads dst/dst_config.py and writes:
    data/dst/units.geojson    unit polygons (WGS84) + one raw value column per
                              enabled criterion (c_<id>), null where no data
    data/dst/criteria.json    the criteria tree, labels, directions, 0-1 rescale
                              bounds, status (ready / awaiting data), options
    data/dst/seg_units.json   road segment id -> unit id (for live cost criteria
                              and the "Priority score" plan rule)

Nothing in here is specific to a particular data layer: add layers by editing
dst_config.py only.

Requires: geopandas, rasterio, shapely, numpy (same env as the pipeline).
"""
import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dst_config as C  # noqa: E402


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def load_units():
    """Return (GeoDataFrame in WORK_CRS with columns unit_id, name, area_ha, geometry,
    is_placeholder)."""
    if C.UNITS_SOURCE is None:
        bnd = gpd.read_file(C.BOUNDARY).to_crs(C.WORK_CRS)
        poly = bnd.union_all() if hasattr(bnd, "union_all") else bnd.unary_union
        minx, miny, maxx, maxy = poly.bounds
        step = C.PLACEHOLDER_CELL_KM * 1000
        cells = []
        for x in np.arange(minx, maxx, step):
            for y in np.arange(miny, maxy, step):
                g = box(x, y, x + step, y + step).intersection(poly)
                if not g.is_empty and g.area > 0.05 * step * step:
                    cells.append(g)
        u = gpd.GeoDataFrame(geometry=cells, crs=C.WORK_CRS)
        u["unit_id"] = [f"PH-{i + 1:03d}" for i in range(len(u))]
        u["name"] = ["Placeholder cell " + s[3:] for s in u["unit_id"]]
        placeholder = True
    else:
        u = gpd.read_file(C.UNITS_SOURCE).to_crs(C.WORK_CRS)
        u["unit_id"] = (u[C.UNITS_ID_FIELD].astype(str) if C.UNITS_ID_FIELD
                        else [f"U-{i + 1:03d}" for i in range(len(u))])
        u["name"] = (u[C.UNITS_NAME_FIELD].astype(str) if C.UNITS_NAME_FIELD
                     else u["unit_id"])
        if u["unit_id"].duplicated().any():
            raise ValueError("UNITS_ID_FIELD is not unique")
        placeholder = False
    u["area_ha"] = u.geometry.area / 1e4
    return u[["unit_id", "name", "area_ha", "geometry"]], placeholder


# ---------------------------------------------------------------------------
# Per-kind value calculators. Each returns a list (one value per unit, or None).
# ---------------------------------------------------------------------------
def calc_raster(units, crit):
    import rasterio
    from rasterio.mask import mask

    stat = crit.get("stat", "mean")
    vmap = crit.get("value_map")
    out = []
    with rasterio.open(crit["source"]) as src:
        g = units.to_crs(src.crs)
        nod = src.nodata
        for geom in g.geometry:
            try:
                arr, _ = mask(src, [mapping(geom)], crop=True, filled=False)
            except ValueError:            # unit falls outside the raster
                out.append(None)
                continue
            a = arr[0]
            vals = a.compressed() if np.ma.isMaskedArray(a) else a.ravel()
            if nod is not None:
                vals = vals[vals != nod]
            vals = vals[np.isfinite(vals)] if vals.dtype.kind == "f" else vals
            if vmap:
                vals = np.array([vmap[v] for v in vals if v in vmap], dtype=float)
            if vals.size == 0:
                out.append(None)
                continue
            fn = {"mean": np.mean, "median": np.median, "max": np.max,
                  "min": np.min, "sum": np.sum}[stat]
            out.append(float(fn(vals)))
    return out


def calc_polygon_cover(units, crit):
    src = gpd.read_file(crit["source"]).to_crs(C.WORK_CRS)
    layer = src.union_all() if hasattr(src, "union_all") else src.unary_union
    return [float(geom.intersection(layer).area / geom.area) if geom.area else None
            for geom in units.geometry]


def calc_point_density(units, crit):
    pts = gpd.read_file(crit["source"]).to_crs(C.WORK_CRS)
    j = gpd.sjoin(pts[["geometry"]], units[["unit_id", "geometry"]], predicate="within")
    counts = j.groupby("unit_id").size()
    return [float(counts.get(uid, 0) / (a / 100)) for uid, a in zip(units.unit_id, units.area_ha)]


def calc_field(units_raw, crit):
    col = crit["source"]
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)
            for v in units_raw[col]]


CALC = {"raster": calc_raster, "polygon_cover": calc_polygon_cover,
        "point_density": calc_point_density}


# ---------------------------------------------------------------------------
# Segment -> unit lookup
# ---------------------------------------------------------------------------
def segment_units(units):
    seg = gpd.read_file(C.SEGMENTS).to_crs(C.WORK_CRS)
    mids = seg.copy()
    mids["geometry"] = seg.geometry.interpolate(0.5, normalized=True)
    j = gpd.sjoin(mids[["id", "geometry"]], units[["unit_id", "geometry"]],
                  predicate="within", how="left")
    j = j.drop_duplicates("id")
    return {int(r.id): (None if not isinstance(r.unit_id, str) else r.unit_id)
            for r in j.itertuples()}


# ---------------------------------------------------------------------------
def main():
    C.OUT_DIR.mkdir(parents=True, exist_ok=True)
    units, placeholder = load_units()
    units_raw = gpd.read_file(C.UNITS_SOURCE) if C.UNITS_SOURCE else None

    tree = []
    for b in C.BRANCHES:
        crits = []
        for c in b["criteria"]:
            ready = bool(c.get("enabled")) and c.get("source") is not None
            status = "ready" if ready else "awaiting"
            lo = hi = None
            if ready and c["kind"] != "live":
                try:
                    if c["kind"] == "field":
                        vals = calc_field(units_raw, c)
                    else:
                        c = {**c, "source": str(c["source"])}
                        vals = CALC[c["kind"]](units, c)
                    units["c_" + c["id"]] = vals
                    good = [v for v in vals if v is not None]
                    lo, hi = (min(good), max(good)) if good else (None, None)
                    print(f"  {c['id']:15s} {len(good):4d}/{len(vals)} units with data")
                except Exception as e:  # keep building; show the problem in the tool
                    status = "error"
                    print(f"  {c['id']:15s} ERROR: {e}")
            if c.get("bounds"):
                lo, hi = c["bounds"]
            crits.append({
                "id": c["id"], "label": c["label"], "desc": c.get("desc", ""),
                "kind": c["kind"], "live": c["source"] if c["kind"] == "live" else None,
                "dir": 1 if c.get("higher_is", "priority") == "priority" else -1,
                "lo": lo, "hi": hi, "units": c.get("units", ""),
                "owner": c.get("owner", b.get("owner", "")), "note": c.get("note", ""),
                "status": status,
            })
        tree.append({"id": b["id"], "label": b["label"], "owner": b.get("owner", ""),
                     "criteria": crits})

    seg_map = segment_units(units)

    out = units.to_crs("EPSG:4326")
    out["geometry"] = out.geometry.simplify(0.0002, preserve_topology=True)
    out["area_ha"] = out["area_ha"].round(1)
    out.to_file(C.OUT_DIR / "units.geojson", driver="GeoJSON",
                COORDINATE_PRECISION=5)

    meta = {
        "placeholder_units": placeholder,
        "anchor_mode": C.ANCHOR_MODE,
        "default_branch_score": C.DEFAULT_BRANCH_SCORE,
        "default_criterion_score": C.DEFAULT_CRITERION_SCORE,
        "n_units": len(units),
        "branches": tree,
    }
    (C.OUT_DIR / "criteria.json").write_text(json.dumps(meta, indent=1))
    (C.OUT_DIR / "seg_units.json").write_text(json.dumps(seg_map))

    n_ready = sum(c["status"] == "ready" for b in tree for c in b["criteria"])
    n_all = sum(len(b["criteria"]) for b in tree)
    mapped = sum(v is not None for v in seg_map.values())
    print(f"units: {len(units)} ({'PLACEHOLDER grid' if placeholder else C.UNITS_SOURCE})")
    print(f"criteria ready: {n_ready}/{n_all}")
    print(f"segments mapped to a unit: {mapped}/{len(seg_map)}")
    print(f"wrote {C.OUT_DIR}")


if __name__ == "__main__":
    main()
