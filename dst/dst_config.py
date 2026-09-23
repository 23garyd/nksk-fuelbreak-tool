"""
NKSK Decision Support (DST) layer -- configuration.

THIS IS THE ONLY FILE YOU SHOULD NEED TO EDIT when new data arrives.

How it works
------------
1. Each criterion below has a `source`. While `source` is None the criterion is
   shown in the web tool as "awaiting data" (greyed out, weight ignored).
2. When Rachel sends a layer: put the file path in `source`, set `enabled=True`,
   check `kind` / `stat` / `higher_is`, then re-run:
       python dst/build_dst_data.py
3. That rewrites data/dst/units.geojson + data/dst/criteria.json. Commit, push.
   The web tool picks everything up automatically -- no JS edits needed.

Criteria tree follows Rachel's 6/12/2026 deck ("NKSK final tool development").
Users weight the BRANCHES (anchor + 0-9 sliders) and, inside each branch,
the individual criteria (0-9 sliders).
"""
from pathlib import Path

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent          # nksk-fuelbreak-tool/
DATA_ROOT = Path.home() / "Desktop"                    # where the source GIS lives
OUT_DIR = REPO / "data" / "dst"

# NKSK management / reporting units (polygons).
#   None  -> a PLACEHOLDER square grid is generated over the NKSK boundary so the
#            tool can be built and tested. Replace with Rachel/Nik's layer.
UNITS_SOURCE = None                 # e.g. DATA_ROOT / "NKSK_units" / "nksk_units.shp"
UNITS_ID_FIELD = None               # column holding a unique unit ID (None -> auto)
UNITS_NAME_FIELD = None             # optional human-readable name column
PLACEHOLDER_CELL_KM = 4             # grid size used only while UNITS_SOURCE is None

BOUNDARY = REPO / "data" / "nksk_boundary.geojson"
SEGMENTS = REPO / "data" / "segments.geojson"
WORK_CRS = "EPSG:32605"             # UTM 5N, metres -- used for areas/overlays

# ----------------------------------------------------------------------------
# Weighting options (read by the web tool)
# ----------------------------------------------------------------------------
# How the 0-9 branch sliders are read (the open question for Nik):
#   "points": 9 = as important as the anchor, 0 = ignore.  weight = score / sum
#   "ratio" : N = anchor is N times more important.        weight = 1 / N
ANCHOR_MODE = "points"
DEFAULT_BRANCH_SCORE = 5            # starting slider position for non-anchor branches
DEFAULT_CRITERION_SCORE = 5         # starting slider position inside a branch

# ----------------------------------------------------------------------------
# Criterion fields
# ----------------------------------------------------------------------------
#   id         short key, no spaces (becomes the property name c_<id>)
#   label      what land managers see
#   desc       one-line explanation (shown on hover)
#   enabled    False until real data is wired in
#   kind       how the per-unit value is computed:
#                "raster"        zonal statistic of a raster (stat = mean|median|max|min|sum)
#                "polygon_cover" share (0-1) of the unit covered by polygons in `source`
#                "point_density" points per km^2 (e.g. ignitions)
#                "field"         value already stored as a column in the units layer
#                                (source = column name)
#                "live"          computed in the browser from the road-segment cost
#                                model (source = name of a function in dst.js LIVE)
#   source     file path / column / live-function name. None = awaiting data.
#   stat       for "raster" only
#   value_map  optional {raster_code: value} lookup before the stat (categorical rasters)
#   higher_is  "priority"  -> bigger raw value = higher treatment priority
#              "lower"     -> bigger raw value = LOWER priority (e.g. cost)
#   bounds     optional (lo, hi) in raw units for the 0-1 rescale. None = min/max
#              across all units. Fixed bounds keep scores stable between data updates.
#   units      label for raw values in the popup
#   owner      who is producing the layer
#   note       where the data is / what is still needed (shown on hover)

BRANCHES = [
    {
        "id": "wildfire", "label": "Wildfire vulnerability", "owner": "Rachel",
        "criteria": [
            dict(id="avail_burn", label="Availability to burn",
                 desc="Months with NDVI below study-area median, 2001-2024 (MODIS via HCDP)",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="months", owner="Rachel",
                 note="Rachel's NDVI surface - not yet received."),
            dict(id="fuel_hazard", label="Fuel hazard",
                 desc="Fuel loading by land cover",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="index", owner="Rachel",
                 note="Rachel's fuel-loading surface - not yet received. Interim candidate: "
                      "NKSK_FuelTreatmentFiles/HI_LANDFIRE_FBFM40/LH23_F40_240.tif "
                      "(categorical; needs a value_map from FBFM40 code to fuel load)."),
            dict(id="ignition", label="Ignition hazard",
                 desc="Road density weighted by road class, 500 m radius",
                 enabled=False, kind="raster", stat="mean",
                 source=None,  # DATA_ROOT / "NKSK_FuelTreatmentFiles/RoadDensity_export011326/RoadDensity_weighted_500m.tif"
                 higher_is="priority", bounds=None, units="density", owner="Rachel",
                 note="On disk: RoadDensity_weighted_500m.tif (matches Rachel's method) - "
                      "confirm it is her final version, then set source + enabled."),
        ],
    },
    {
        "id": "nearshore", "label": "Nearshore vulnerability", "owner": "Rachel / Jasper",
        "criteria": [
            dict(id="sediment", label="Sediment yield",
                 desc="Modeled sediment delivery to nearshore reefs",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="t/ha/yr", owner="Rachel / Jasper",
                 note="Wai'ula'ula monitoring - in progress."),
        ],
    },
    {
        "id": "values", "label": "Values to be protected", "owner": "Rachel",
        "criteria": [
            dict(id="pop_density", label="Population density",
                 desc="Community value: residents per km^2",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="people/km2", owner="Rachel",
                 note="Not yet received (Census)."),
            dict(id="wui", label="Wildland-urban interface",
                 desc="Community value: share of unit in WUI",
                 enabled=False, kind="polygon_cover", source=None,
                 higher_is="priority", bounds=(0, 1), units="share", owner="Rachel",
                 note="Not yet received."),
            dict(id="crit_habitat", label="Critical habitat",
                 desc="Conservation value: share of unit in USFWS critical habitat",
                 enabled=False, kind="polygon_cover", source=None,
                 higher_is="priority", bounds=(0, 1), units="share", owner="Rachel",
                 note="Not yet received (USFWS)."),
            dict(id="native_bio", label="Native biodiversity",
                 desc="Conservation value: native biodiversity index",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="index", owner="Rachel",
                 note="Not yet received."),
            dict(id="protected", label="Protected areas",
                 desc="Conservation value: share of unit in protected areas",
                 enabled=False, kind="polygon_cover", source=None,
                 higher_is="priority", bounds=(0, 1), units="share", owner="Rachel",
                 note="Not yet received."),
        ],
    },
    {
        "id": "response", "label": "Responsive management logistics", "owner": "Rachel / HWMO",
        "criteria": [
            dict(id="transmissivity", label="Fire transmissivity",
                 desc="Potential for fire to spread from hazardous fuels",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="index", owner="Rachel",
                 note="Rachel's exposure analysis - not yet received."),
            dict(id="emergency", label="Distance to emergency resources",
                 desc="Travel time to fire equipment / shelters / hospitals",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="min", owner="Rachel / HWMO",
                 note="Requested from HWMO. Direction (farther = higher priority?) to confirm."),
            dict(id="evacuation", label="Evacuation difficulty",
                 desc="Ingress / egress constraints",
                 enabled=False, kind="raster", source=None, stat="mean",
                 higher_is="priority", bounds=None, units="index", owner="Rachel / HWMO",
                 note="Requested from HWMO."),
        ],
    },
    {
        "id": "prevent", "label": "Preventative management logistics", "owner": "Gary",
        "criteria": [
            # "live" criteria are calculated in the browser from the existing road-
            # segment cost model, so they follow the throughput / fence / palette
            # controls. Flip enabled=True to use them (units must exist first).
            dict(id="impl_cost", label="Implementation cost",
                 desc="Green-fuel-break build cost per km of road in the unit",
                 enabled=False, kind="live", source="impl_per_km",
                 higher_is="lower", bounds=None, units="$/km", owner="Gary",
                 note="Ready - from the tool's own cost model. Enable when units are final."),
            dict(id="maint_cost", label="Maintenance cost",
                 desc="3-year maintenance cost per km of road in the unit",
                 enabled=False, kind="live", source="maint_per_km",
                 higher_is="lower", bounds=None, units="$/km", owner="Gary",
                 note="Ready - from the tool's own cost model. Enable when units are final."),
            dict(id="establish", label="Likelihood of establishment",
                 desc="Chance plantings survive: 1 - mean Year-2 drought-trigger probability",
                 enabled=False, kind="live", source="establish",
                 higher_is="priority", bounds=None, units="0-1", owner="Gary",
                 note="Interim proxy from drought probability; could be replaced by a "
                      "species-suitability score per unit."),
        ],
    },
]
