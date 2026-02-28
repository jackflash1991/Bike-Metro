# config.py — Circuit Trails map pipeline configuration
# Edit this file to change bbox, rendering options, excluded routes, etc.

# ── Bounding box ─────────────────────────────────────────────────────
# Greater Philadelphia / Circuit Trails region
# Format: (south, west, north, east)
BBOX = (39.85, -75.65, 40.35, -74.85)

# Convenience string form for Overpass queries
BBOX_STR = f"{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}"

# ── Overpass ─────────────────────────────────────────────────────────
# Primary mirror — tried first.  If it returns a 5xx error the code will
# automatically fall back through OVERPASS_MIRRORS in order.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 180

# Fallback mirrors tried in order when the primary returns a 5xx / times out.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# ── Route filtering ──────────────────────────────────────────────────
# Route names to exclude from the final map (e.g. state-level connectors
# that clutter the Circuit Trails view)
EXCLUDE_ROUTES = set()

# ── Trailhead matching ───────────────────────────────────────────────
# Max distance (degrees) to snap a trailhead label to a graph node (~200m)
TRAILHEAD_MATCH_DIST = 0.002

# Max distance (degrees) to snap an external trailhead to a route node in
# osm2loom.py (~100m).  Trailheads within this distance of any route-way node
# that are not already tagged as route members will be snapped to that node,
# causing it to become a labelled station on the map.
TRAILHEAD_SNAP_DIST = 0.001

# Max perpendicular distance (degrees) for inserting a synthetic station node
# on a route edge in build_map.py (~100m).  Used as a second-pass fallback for
# trailheads that sit beside a long segment with no nearby OSM node.
TRAILHEAD_INSERT_DIST = 0.001

# Case-insensitive Overpass regex used to identify trail-access parking lots
# (amenity=parking nodes/ways whose name matches this pattern).
TRAIL_PARKING_RE = "trail|greenway"

# ── Endpoint merging ─────────────────────────────────────────────────
# Max distance (degrees) to merge a trail endpoint (degree-1 node) into a
# labelled station on a *different* trail, creating a transfer point (~600m).
ENDPOINT_MERGE_DIST = 0.006

# ── Amenity icons ─────────────────────────────────────────────────────
# Max distance (degrees) to snap an amenity POI to an existing graph node (~100m).
AMENITY_MATCH_DIST = 0.001

# Minimum spacing (degrees) between icons of the same type (~200m).
# Prevents icon clutter in dense areas (e.g. many water fountains in Fairmount Park).
AMENITY_MIN_SPACING = 0.002

# ── Rail-trail integration ───────────────────────────────────────
# Max distance (degrees) to merge a trail node into a nearby rail station (~200m).
# Rail stations take priority when both exist within this distance.
RAIL_STATION_MERGE_DIST = 0.002

# Minimum spacing (degrees) between rail stations to prevent collapse (~300m).
RAIL_NODE_MIN_SPACING = 0.003

# ── Rendering ────────────────────────────────────────────────────────
LINE_WIDTH = 50
LINE_SPACING = 25
STATION_LABEL_SIZE = 200
LINE_LABEL_SIZE = 160

# ── Cache / output files ─────────────────────────────────────────────
CACHE_FILE = "circuit_trails.json"
FILTERED_FILE = "circuit_trails_filtered.json"
COMBINED_FILE = "combined.json"
OUTPUT_SVG = "combined.svg"
