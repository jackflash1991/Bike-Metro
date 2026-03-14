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

# ── Amenity icons ─────────────────────────────────────────────────────
# Default max distance (degrees) to snap an amenity POI to an existing graph
# node (~100m).  Used for repair stations and info maps.
AMENITY_MATCH_DIST = 0.001

# Per-type snap overrides (degrees).  Restrooms and drinking water are critical
# trail amenities that are often set back 150-300m from the trail centerline
# (e.g. park restroom buildings, picnic-area fountains in Valley Forge).
# Parking already used TRAILHEAD_MATCH_DIST; now all three share a generous
# snap radius (~200m) so they aren't missed.
AMENITY_SNAP_OVERRIDES: dict[str, float] = {
    "water": 0.002,      # ~200m — fountains at park facilities
    "toilets": 0.002,    # ~200m — restroom buildings off-trail
    "parking": 0.002,    # ~200m — (unchanged, was TRAILHEAD_MATCH_DIST)
}

# Max perpendicular distance (degrees) to insert a new node on a route edge
# for an amenity POI (~300m).  Used as a second-pass fallback when no existing
# graph node is close enough.  Set generously because amenity centroids
# (especially restroom buildings and parking lots) can be 200m+ from the
# trail centerline, and the distance is latitude-corrected.
AMENITY_INSERT_DIST = 0.003

# Per-type insert overrides (degrees).  Water and toilets get a larger insert
# radius (~500m) to catch facilities set well back from the trail.
AMENITY_INSERT_OVERRIDES: dict[str, float] = {
    "water": 0.005,      # ~500m
    "toilets": 0.005,    # ~500m
    "parking": 0.005,    # ~500m
}

# Minimum spacing (degrees) between icons of the same type (~100m).
# Prevents icon clutter when multiple OSM elements map to the same real-world
# facility, while still allowing distinct nearby facilities (e.g. two water
# fountains 148m apart at different Valley Forge picnic areas).
AMENITY_MIN_SPACING = 0.001

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
