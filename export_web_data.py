#!/usr/bin/env python3
"""Regenerate the web tool's data/ folder from the NKSK pipeline outputs.

Run from the repo root (folder containing this script):  python export_web_data.py
Requires: geopandas, rasterio, matplotlib, numpy.

Reads the real pipeline products and writes:
  data/segments.geojson        road segments + attributes the tool needs
  data/moisture_zones.geojson  moisture-zone polygons (categorical overlay)
  data/grids/<key>.png + .json  raster overlays (image + bounds/min/max)
"""
import os, json, re, warnings
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.windows import from_bounds, intersection, Window
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from affine import Affine
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
# Source data root = the 26X_GFB_Data project folder (one level up by default).
# If this tool folder lives elsewhere, set NKSK_SRC=/path/to/26X_GFB_Data.
ROOT = os.environ.get("NKSK_SRC") or os.path.abspath(os.path.join(HERE, ".."))
def src(*p): return os.path.join(ROOT, *p)
def out(*p): return os.path.join(HERE, "data", *p)
os.makedirs(out("grids"), exist_ok=True)

# ---------------------------------------------------------------- 1. SEGMENTS
# Full network geometry (280 segments) from the scored shapefile; the 45
# young-lava segments are kept but flagged so the tool can grey them out.
import csv
seg = gpd.read_file(src("outputs", "road_segments_scored.shp")).to_crs(4326)

# cwd_mm for the 235 kept segments (young-lava ones aren't in this file)
cwd_lookup = {}
_cwd_gj = json.load(open(src("outputs", "NKSK_road_segments_CWD.geojson")))
for f in _cwd_gj["features"]:
    cwd_lookup[f["properties"]["seg_id"]] = f["properties"]["cwd_mm"]

# young-lava exclusion flag, joined by seg_id
excl_map = {}
with open(src("outputs", "road_segments_young_lava_exclusion.csv")) as f:
    for row in csv.DictReader(f):
        excl_map[row["seg_id"]] = 1 if str(row["exclude"]).strip().lower() == "true" else 0

# per-segment drought-trigger probabilities from HCDP (Cell 5b), joined by seg_id.
# p2 = P(wet season < 25th pctile) -> Year-2 contingency irrigation trigger
# p3 = P(wet season < 10th pctile) -> Year-3 severe-drought trigger
# Young-lava / uncovered segments aren't in this table; the tool falls back to
# the district-wide scalars (0.28 / 0.10) for them, matching the pipeline.
DROUGHT_FALLBACK = (0.28, 0.10)
drought_map = {}
_drought_csv = src("outputs", "NKSK_drought_probabilities.csv")
if os.path.exists(_drought_csv):
    with open(_drought_csv) as f:
        for row in csv.DictReader(f):
            try:
                drought_map[row["seg_id"]] = (round(float(row["p_drought_yr2"]), 4),
                                              round(float(row["p_drought_yr3"]), 4))
            except (KeyError, ValueError):
                pass
else:
    print("WARNING: NKSK_drought_probabilities.csv not found — "
          "all segments will use the district-wide drought fallback.")

# elevation class from the DEM, sampled at each segment midpoint
dem_path = src("inputs", "hawaii_dem")
with rasterio.open(dem_path) as dem:
    dem_crs = dem.crs
    mids = seg.geometry.interpolate(0.5, normalized=True)
    mids_dem = gpd.GeoSeries(mids, crs=4326).to_crs(dem_crs)
    elevs = [v[0] for v in dem.sample([(pt.x, pt.y) for pt in mids_dem])]
    dem_nod = dem.nodata

# CWD raster, to fill cwd_mm for the excluded segments that lack a table value
with rasterio.open(src("outputs", "NKSK_CWD_annual_mm.tif")) as cr:
    cwd_crs = cr.crs
    mids_cwd = gpd.GeoSeries(mids, crs=4326).to_crs(cwd_crs)
    cwd_samp = [v[0] for v in cr.sample([(pt.x, pt.y) for pt in mids_cwd])]
    cwd_nod = cr.nodata

def elev_class(z):
    if z is None or (dem_nod is not None and z == dem_nod) or (isinstance(z, float) and (np.isnan(z) or z < -1e30)):
        return "low"
    return "high" if z >= 1000 else "low"

# ---- per-segment recommended species (real, distribution-aware) + cost/sci lookup ----
import pandas as pd, unicodedata
def _norm(x):
    x = str(x).strip().lower().replace("ʻ", "").replace("'", "").replace("ʼ", "")
    return "".join(c for c in unicodedata.normalize("NFD", x) if unicodedata.category(c) != "Mn")

_costdf = pd.read_excel(src("inputs", "outplanting_costs_hawaii.xlsx"),
                        sheet_name="Outplanting Cost List", header=3)
SPCOST = {}
for _, r in _costdf.iterrows():
    if pd.isna(r.get("Hawaiian Name")):
        continue
    try: SPCOST[_norm(r["Hawaiian Name"])] = float(r["Highest Price ($)"])
    except Exception: pass

_gn = pd.read_excel(src("inputs", "gonative_combined_scored_3_13.xlsx"), sheet_name="Combined Results")
_hc = [c for c in _gn.columns if "Hawaiian" in str(c)][0]
_sc = [c for c in _gn.columns if "Scientific" in str(c)][0]
SPSCI = {}
for _, r in _gn.iterrows():
    if pd.isna(r[_hc]): continue
    SPSCI[_norm(r[_hc])] = str(r[_sc]).strip()
for _, r in _costdf.iterrows():  # fill any gaps from the cost list
    if pd.isna(r.get("Hawaiian Name")) or pd.isna(r.get("Scientific Name")): continue
    SPSCI.setdefault(_norm(r["Hawaiian Name"]), str(r["Scientific Name"]).strip())

DEFAULT_COST = 12.0
SPCOL = {"1": ["c1L_sp", "c1M_sp", "c1H_sp"],
         "2": ["c2L_sp", "c2M_sp", "c2H_sp"],
         "3": ["c3L_sp", "c3M_sp", "c3H_sp"]}
def _spval(r, c):
    v = r[c] if c in r else None
    return None if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)

feats = []
for i, (_, r) in enumerate(seg.iterrows()):
    geom = r.geometry
    if geom.geom_type == "MultiLineString":
        geom = max(geom.geoms, key=lambda g: g.length)
    geom = geom.simplify(0.0001, preserve_topology=True)  # ~11 m, keeps files small
    coords = list(geom.coords)
    sid_raw = str(r["seg_id"])
    m = re.search(r"(\d+)", sid_raw)
    sid = int(m.group(1)) if m else i + 1
    cwd_val = cwd_lookup.get(sid_raw)
    if cwd_val is None:  # excluded segment -> use sampled raster value
        s = cwd_samp[i]
        cwd_val = float(s) if s is not None and not (cwd_nod is not None and s == cwd_nod) and s > -1e30 else 0.0
    latlon = [[round(y, 5), round(x, 5)] for x, y in coords]  # Leaflet wants [lat,lon]
    feats.append({
        "type": "Feature",
        "geometry": {"type": "LineString",
                     "coordinates": [[round(x, 5), round(y, 5)] for x, y in coords]},
        "properties": {
            "id": sid,
            "road": r["road_name"],
            "elev": elev_class(elevs[i]),
            "length_m": round(float(r["length_m"]), 1),
            "cwd_mm": round(float(cwd_val), 1),
            "excl": excl_map.get(sid_raw, 0),
            # per-segment drought-trigger probabilities (Yr2 P25, Yr3 P10);
            # district-wide fallback where the segment lacks HCDP coverage
            "p2": drought_map.get(sid_raw, DROUGHT_FALLBACK)[0],
            "p3": drought_map.get(sid_raw, DROUGHT_FALLBACK)[1],
            "a": latlon[0],
            "b": latlon[-1],
            # real per-segment species by choice tier -> [Low, Med, High]
            "sp": {t: [_spval(r, c) for c in cols] for t, cols in SPCOL.items()},
        },
    })
json.dump({"type": "FeatureCollection", "features": feats},
          open(out("segments.geojson"), "w"), ensure_ascii=False)
_p2 = [f["properties"]["p2"] for f in feats]
print(f"segments.geojson: {len(feats)} segments  "
      f"(high={sum(f['properties']['elev']=='high' for f in feats)}, "
      f"excl={sum(f['properties']['excl'] for f in feats)}, "
      f"drought-covered={len(drought_map)}, "
      f"p2 {min(_p2):.3f}-{max(_p2):.3f})")

# species_costs.json: normalized-name -> {name, sci, cost} for every species used
_used = set()
for cols in SPCOL.values():
    for c in cols:
        _used |= set(seg[c].dropna().astype(str))
SPECIES = {_norm(nm): {"name": nm, "sci": SPSCI.get(_norm(nm), ""),
                       "cost": SPCOST.get(_norm(nm), DEFAULT_COST)} for nm in _used}
json.dump(SPECIES, open(out("species_costs.json"), "w"), ensure_ascii=False)
print(f"species_costs.json: {len(SPECIES)} species  "
      f"(missing cost -> default ${DEFAULT_COST:.0f}: "
      f"{sorted(nm for nm in _used if _norm(nm) not in SPCOST)})")

# ------------------------------------------------------------- 2. NKSK BOUNDARY
from shapely.geometry import mapping
def _round_geom(g, nd=5):
    return json.loads(json.dumps(mapping(g)),
                      parse_float=lambda x: round(float(x), nd))
bnd = gpd.read_file(src("inputs", "NKSK-boundaries", "nksk.shp")).to_crs(4326)
bnd_full = bnd.geometry.union_all()                              # exact, for masking
bnd_geom = bnd.geometry.simplify(0.0003, preserve_topology=True).union_all()  # for display
json.dump({"type": "FeatureCollection",
           "features": [{"type": "Feature", "properties": {"name": "NKSK"},
                         "geometry": _round_geom(bnd_geom)}]},
          open(out("nksk_boundary.geojson"), "w"))
print("nksk_boundary.geojson: 1 polygon")

# ------------------------------------------------------------ 3. MOISTURE ZONES
mz = gpd.read_file(src("inputs", "Moisture_Zones", "Moisture_Zones.shp")).to_crs(4326)
zone_col = "moisturezo" if "moisturezo" in mz.columns else "zone"
# clip to the actual NKSK boundary polygon so the layer fills the boundary shape
mz = mz.clip(bnd_full)
mz["geometry"] = mz.geometry.simplify(0.0002, preserve_topology=True)
mz = mz[[zone_col, "geometry"]].rename(columns={zone_col: "zone"})
mz = mz[~mz.geometry.is_empty & mz.geometry.notna()]
mz_feats = [{"type": "Feature", "properties": {"zone": row["zone"]},
             "geometry": _round_geom(row["geometry"])}
            for _, row in mz.iterrows()]
json.dump({"type": "FeatureCollection", "features": mz_feats},
          open(out("moisture_zones.geojson"), "w"))
print(f"moisture_zones.geojson: {len(mz_feats)} polygons, zones={sorted(mz['zone'].dropna().unique())}")

# ------------------------------------------------------------------ 3. RASTERS
# clip window (lon/lat) = the NKSK boundary bbox (padded), so every overlay
# spans and aligns to the boundary. export_raster then clamps to each raster's
# own extent, so bounds always match the actual data.
bminx, bminy, bmaxx, bmaxy = bnd.total_bounds
CLIP = (bminx - 0.01, bminy - 0.01, bmaxx + 0.01, bmaxy + 0.01)  # (W,S,E,N)
MAXDIM = 1100

def export_raster(path, name, cmap):
    w, sth, e, n = CLIP
    with rasterio.open(path) as ds:
        # reproject every raster to EPSG:4326 so its pixel grid is geographic
        # north-up (fixes UTM rasters like the DEM that were rotated on the map)
        with WarpedVRT(ds, crs="EPSG:4326", resampling=Resampling.bilinear) as vrt:
            win = from_bounds(w, sth, e, n, vrt.transform)
            win = intersection(win, Window(0, 0, vrt.width, vrt.height)).round_offsets().round_lengths()
            scd = max(win.width / MAXDIM, win.height / MAXDIM, 1)
            oh, ow = max(1, int(win.height / scd)), max(1, int(win.width / scd))
            arr = vrt.read(1, window=win, out_shape=(oh, ow),
                           resampling=Resampling.average).astype("float64")
            wt = vrt.window_transform(win)
            out_transform = wt * Affine.scale(win.width / ow, win.height / oh)  # decimated grid
            nod = vrt.nodata
    if nod is not None:
        arr[arr == nod] = np.nan
    arr[arr < -1e30] = np.nan
    # mask everything outside the NKSK boundary -> transparent (aligns to boundary)
    outside = geometry_mask([mapping(bnd_full)], out_shape=(oh, ow),
                            transform=out_transform, invert=False)
    arr[outside] = np.nan
    W, S, E, N = array_bounds(oh, ow, out_transform)  # already lon/lat
    vmin, vmax = float(np.nanmin(arr)), float(np.nanmax(arr))
    norm = plt.Normalize(vmin, vmax)
    rgba = plt.get_cmap(cmap)(norm(arr))
    rgba[np.isnan(arr)] = [0, 0, 0, 0]
    plt.imsave(out("grids", f"{name}.png"), rgba, origin="upper")
    json.dump({"bounds": [round(S, 5), round(W, 5), round(N, 5), round(E, 5)],  # [S,W,N,E]
               "min": vmin, "max": vmax},
              open(out("grids", f"{name}.json"), "w"))
    sz = os.path.getsize(out("grids", f"{name}.png")) / 1024
    print(f"grids/{name}.png: {ow}x{oh}px {sz:.0f}KB  range [{vmin:.1f},{vmax:.1f}]")

export_raster(src("outputs", "NKSK_CWD_annual_mm.tif"),                 "cwd",  "YlOrBr")
export_raster(src("inputs", "hawaii_dem"),                             "elev", "Greys")
export_raster(src("inputs", "Penman_ET0_mm_month_raster", "pen_mm_ann"), "pet",  "YlOrBr")
export_raster(src("inputs", "AET_mm_month_raster", "aet_mm_ann"),        "aet",  "GnBu")
print("done.")
