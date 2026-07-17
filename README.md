# NKSK Green Fuel Break — Planning Tool

An interactive, browser-based planning tool for the North Kona / South Kohala (NKSK)
green fuel break. It lets you explore the candidate road-segment network, pick a native
planting palette, adjust cost and drought assumptions, build a budget-constrained plan,
and export it — all running client-side on real pipeline outputs.

**Live site:** `https://23garyd.github.io/nksk-fuelbreak-tool/` 

## What the tool models

Each road segment carries a per-hectare green-fuel-break cost combining implementation
(plant material, outplanting labor, site prep, fencing, ungulate control) and three years
of maintenance (irrigation, weed control, fencing/fire maintenance). Costs derive from
Wada et al. 2017 (cited via Trauernicht & Kunz 2019), inflated by a factor of 1.332 and
discounted at 2% for present value. Delivered-water cost hinges on a labor-throughput
assumption (default ~180 gal/hr) that is **pending WDFI validation** — treat water-cost
figures as provisional and use the throughput slider to test sensitivity.

Segments over young lava (age groups flagged in the pipeline) are shown greyed/dotted and
can be excluded from totals with the "exclude young lava" toggle.

## Map layers

- **Road segments** — 280 candidate segments, colored by the selected metric (total cost,
  cost/ha, CWD, water, plants, or elevation class).
- **Raster overlays** — Climatic Water Deficit (CWD), Elevation (DEM), Potential ET
  (Penman annual), Actual ET (annual).
- **Moisture zones** — categorical polygon layer (Arid → Very Wet).

## Regenerating `data/` from the pipeline

The `data/` folder is produced from the pipeline outputs by `export_web_data.py`.
It reads the scored road segments, the CWD table/raster, the young-lava exclusion table,
the DEM, the annual Penman-ET0 and AET grids, and the moisture-zone polygons, then writes
GeoJSON + PNG/JSON overlays.

Requirements: `geopandas`, `rasterio`, `matplotlib`, `numpy`. The script expects the
source `inputs/` and `outputs/` folders one level up from the repo (as in the project
tree). To refresh:

```bash
python export_web_data.py     # rewrites data/segments.geojson, data/moisture_zones.geojson, data/grids/*
```

Then commit and push — GitHub Pages redeploys automatically within ~1 minute.

## Running locally

Because the page loads data with `fetch()`, opening `index.html` directly (`file://`) is
blocked by browser CORS rules and shows an empty map. Serve the folder instead:

```bash
python -m http.server 8000
# open http://localhost:8000
```

## Sharing a scenario

The full scenario state (palette, sliders, corridors, plan, overlay) is encoded in the URL
hash, so copying the address bar — or the in-app "Share" button — reproduces the exact view.

## Data & attribution

Basemap tiles © Esri (ArcGIS Online World Hillshade / Topo / Imagery); attribution is shown
on the map. Species cost basis: Wada et al. 2017 via Trauernicht & Kunz 2019.
