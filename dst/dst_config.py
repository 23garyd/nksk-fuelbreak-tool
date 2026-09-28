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
# Rachel's reporting units (Sep 2026 "forGary" delivery): 141 polygons = NKSK
# management units x watersheds, with one median 0-100 score per decision input.
UNITS_SOURCE = REPO / "dst" / "source" / "reporting_units.shp"
UNITS_ID_FIELD = None               # column holding a unique unit ID (None -> auto: U-000 = shapefile FID 0)
UNITS_NAME_FIELD = "unit_name"      # optional human-readable name column
UNITS_TYPE_FIELD = "unit_type"      # optional unit category shown in the popup
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

# Rachel's model delivers ONE composite 0-100 score per decision input (the
# unit median of her 30 m surface), so each branch has a single "field" criterion.
# The finer sub-criteria (NDVI, fuel hazard, WUI, ...) are already folded into
# her composites. Fixed bounds (0, 100) keep her scale: u = score / 100.
# Direction: all five assumed "higher score = higher treatment priority"
# (to confirm with Rachel, esp. the two management-logistics layers).
def _rachel(id, label, col, desc):
    return dict(id=id, label=label, desc=desc, enabled=True, kind="field",
                source=col, higher_is="priority", bounds=(0, 100),
                units="score (0-100)", owner="Rachel",
                note=f"Unit median of Rachel's 30 m surface (field '{col}').")


BRANCHES = [
    {"id": "wildfire", "label": "Wildfire vulnerability", "owner": "Rachel",
     "criteria": [_rachel("firevuln", "Wildfire vulnerability", "firevuln",
                          "Availability to burn (NDVI), fuel hazard and ignition hazard")]},
    {"id": "nearshore", "label": "Nearshore vulnerability", "owner": "Rachel / Jasper",
     "criteria": [
         dict(id="sediment", label="Sediment yield",
              desc="Modeled sediment delivery to nearshore reefs",
              enabled=False, kind="field", source=None,
              higher_is="priority", bounds=(0, 100), units="score (0-100)", owner="Rachel / Jasper",
              note="Wai'ula'ula monitoring - in progress; not in the Sep 2026 delivery."),
     ]},
    {"id": "consval", "label": "Conservation values", "owner": "Rachel",
     "criteria": [_rachel("consval", "Conservation values", "consval",
                          "Critical habitat, native biodiversity, protected areas")]},
    {"id": "commval", "label": "Community values", "owner": "Rachel",
     "criteria": [_rachel("commval", "Community values", "commval",
                          "Population density and wildland-urban interface")]},
    {"id": "response", "label": "Responsive management logistics", "owner": "Rachel",
     "criteria": [_rachel("respmgmt", "Responsive management logistics", "respmgmt",
                          "Fire transmissivity, emergency resources, evacuation")]},
    {"id": "prevent", "label": "Preventative management logistics", "owner": "Rachel",
     "criteria": [_rachel("prevmgmt", "Preventative management logistics", "prevmgmt",
                          "Establishment likelihood and implementation / maintenance logistics")]},
    # Gary's browser-side cost criteria (follow the fence / palette / throughput
    # controls) can be added back to the "prevent" branch if wanted:
    #   dict(id="impl_cost", label="Implementation cost", enabled=True, kind="live",
    #        source="impl_per_km", higher_is="lower", bounds=None, units="$/km"),
    #   dict(id="maint_cost", label="Maintenance cost", enabled=True, kind="live",
    #        source="maint_per_km", higher_is="lower", bounds=None, units="$/km"),
    #   dict(id="establish", label="Likelihood of establishment", enabled=True, kind="live",
    #        source="establish", higher_is="priority", bounds=None, units="0-1"),
]
