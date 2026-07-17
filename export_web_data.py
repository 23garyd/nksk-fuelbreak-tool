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
from rasterio.windows import from_bounds
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
# Source data lives one level up (the 26X_GFB_Data project folder).
ROOT = os.path.abspath(os.path.join(HERE, ".."))
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
            "a": latlon[0],
            "b": latlon[-1],
        },
    })
json.dump({"type": "FeatureCollection", "features": feats}, open(out("segments.geojson"), "w"))
print(f"segments.geojson: {len(feats)} segments  "
      f"(high={sum(f['properties']['elev']=='high' for f in feats)}, "
      f"excl={sum(f['properties']['excl'] for f in feats)})")

# ------------------------------------------------------------ 2. MOISTURE ZONES
mz = gpd.read_file(src("inputs", "Moisture_Zones", "Moisture_Zones.shp")).to_crs(4326)
zone_col = "moisturezo" if "moisturezo" in mz.columns else "zone"
# clip to a padded segment bbox so we only ship the relevant polygons
minx, miny, maxx, maxy = seg.total_bounds
pad = 0.06
mz = mz.clip((minx - pad, miny - pad, maxx + pad, maxy + pad))
mz["geometry"] = mz.geometry.simplify(0.0002, preserve_topology=True)
mz = mz[[zone_col, "geometry"]].rename(columns={zone_col: "zone"})
mz = mz[~mz.geometry.is_empty & mz.geometry.notna()]
# serialize with json (GDAL can't overwrite files on some mounted filesystems)
from shapely.geometry import mapping
def _round_geom(g, nd=5):
    return json.loads(json.dumps(mapping(g)),
                      parse_float=lambda x: round(float(x), nd))
mz_feats = [{"type": "Feature", "properties": {"zone": row["zone"]},
             "geometry": _round_geom(row["geometry"])}
            for _, row in mz.iterrows()]
json.dump({"type": "FeatureCollection", "features": mz_feats},
          open(out("moisture_zones.geojson"), "w"))
print(f"moisture_zones.geojson: {len(mz_feats)} polygons, zones={sorted(mz['zone'].dropna().unique())}")

# ------------------------------------------------------------- 2b. NKSK BOUNDARY
bnd = gpd.read_file(src("inputs", "NKSK-boundaries", "nksk.shp")).to_crs(4326)
bnd_geom = bnd.geometry.simplify(0.0003, preserve_topology=True).union_all()
json.dump({"type": "FeatureCollection",
           "features": [{"type": "Feature", "properties": {"name": "NKSK"},
                         "geometry": _round_geom(bnd_geom)}]},
          open(out("nksk_boundary.geojson"), "w"))
print("nksk_boundary.geojson: 1 polygon")

# ------------------------------------------------------------------ 3. RASTERS
# clip window (lon/lat) = padded segment bbox
CLIP = (minx - 0.03, miny - 0.03, maxx + 0.03, maxy + 0.03)  # (W,S,E,N)
MAXDIM = 1100

def export_raster(path, name, cmap):
    with rasterio.open(path) as s:
        w, sth, e, n = CLIP
        # clip bbox -> source CRS
        if s.crs.to_epsg() != 4326:
            l, b, rr, t = transform_bounds("EPSG:4326", s.crs, w, sth, e, n)
        else:
            l, b, rr, t = w, sth, e, n
        win = from_bounds(l, b, rr, t, s.transform).round_offsets().round_lengths()
        # decimate so the longest side <= MAXDIM
        sc = max(win.width / MAXDIM, win.height / MAXDIM, 1)
        oh, ow = max(1, int(win.height / sc)), max(1, int(win.width / sc))
        arr = s.read(1, window=win, out_shape=(oh, ow),
                     resampling=rasterio.enums.Resampling.average).astype("float64")
        wt = s.window_transform(win)
        wb = array_bounds(win.height, win.width, wt)  # (left,bottom,right,top) in src CRS
        nod = s.nodata
    if nod is not None:
        arr[arr == nod] = np.nan
    arr[arr < -1e30] = np.nan
    # reproject bounds to lon/lat -> (W,S,E,N)
    with rasterio.open(path) as s:
        if s.crs.to_epsg() != 4326:
            W, S, E, N = transform_bounds(s.crs, "EPSG:4326", *wb)
        else:
            W, S, E, N = wb
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
