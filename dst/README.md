# Decision support (priority scoring) layer

Adds a **Priority scoring** panel and a management-unit map layer to the tool.
Land managers pick their most important value (the *anchor*) and rate the
others 0–9. Each unit gets a 0–1 priority score and a rank. "Priority score
(high first)" is also available as a plan-builder rule.

## Files

| File | Role | Edit? |
|---|---|---|
| `dst/dst_config.py` | Criteria tree, data sources, directions, options | **Yes — the only file to edit** |
| `dst/build_dst_data.py` | Turns the config + GIS layers into `data/dst/*` | No |
| `data/dst/units.geojson` | Unit polygons + one raw value per ready criterion | Generated |
| `data/dst/criteria.json` | Tree, labels, status, rescale bounds, anchor mode | Generated |
| `data/dst/seg_units.json` | Road segment → unit lookup | Generated |
| `dst.js` | Weights, scoring, map layer, panel, popup | No |
| `index.html` | 8 one-line hooks (search `DST.` / `dstblk`) | No |

## Current state (Sep 2026)

- **Units:** Rachel's 141 reporting units (NKSK management units × watersheds),
  `dst/source/reporting_units.shp` (EPSG:32605). IDs `U-000`… = shapefile FID,
  so they match the FID_ column in her `*_stats.dbf` tables.
- **Criteria:** Rachel's five composite inputs, each the unit **median** of her
  30 m 0–100 surface (fields `firevuln`, `consval`, `commval`, `respmgmt`,
  `prevmgmt`), one per branch. Rescaled with fixed bounds 0–100 (u = score/100),
  all read as "higher = higher priority". Nearshore vulnerability is still
  awaiting data.
- **Segments → units:** Rachel's units are split along roads, so most road
  segments are the edge between 2+ units. Each segment is buffered 30 m
  (`SEG_BUFFER_M`) and linked to every unit it overlaps, weighted by overlap
  area; its priority (plan rule, segment popup) is the area-weighted mean of
  those units' scores.
- The 30 m rasters and zonal-stat tables she sent are not in the repo (not
  needed for the web tool); keep them with the project GIS data.
- **Demo:** `?dstdemo=1` still fills any *awaiting* criterion with fake values.

## When a layer arrives

1. Open `dst/dst_config.py` and find the criterion.
2. Set `source=` to the file path and `enabled=True`.
3. Check the settings:
   - `kind`: `raster`, `polygon_cover`, `point_density`, `field` or `live`.
   - `stat`: for rasters only.
   - `higher_is`: whether a bigger value means higher priority.
   - `bounds`: optional fixed 0–1 range.
4. Run the build in the pipeline environment (geopandas + rasterio):
   `python dst/build_dst_data.py`.
5. Serve locally (`python -m http.server 8000`) to check, then commit and push.

When the real management units arrive, set `UNITS_SOURCE` (and
`UNITS_ID_FIELD` / `UNITS_NAME_FIELD`) the same way and rebuild.

The three cost/establishment criteria are `kind="live"`. They are computed in
the browser from the existing cost model, so they follow the fence, palette
and throughput controls. Enable them once the units are final.

## Anchor scale

`ANCHOR_MODE = "points"`: 9 = as important as the anchor, 0 = ignore.
Switch to `"ratio"` if Nik confirms the "N times less important" reading.

## Scoring rule

score = Σ W_branch × Σ w_criterion × u

u = the value rescaled to 0–1 (flipped for "lower is better"). Criteria with no
data for a unit are dropped and the weights renormalized, so scores stay on
0–1. Rescale bounds are fixed at build time (min/max across all units unless
`bounds` is set), so toggling corridors never reshuffles scores.
