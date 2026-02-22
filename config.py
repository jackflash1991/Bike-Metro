# config.py — Circuit Trails map pipeline configuration
# Edit this file to change bbox, rendering options, excluded routes, etc.

# ── Bounding box ─────────────────────────────────────────────────────
# Greater Philadelphia / Circuit Trails region
# Format: (south, west, north, east)
BBOX = (39.85, -75.65, 40.35, -74.85)

# Convenience string form for Overpass queries
BBOX_STR = f"{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}"

# ── Overpass ─────────────────────────────────────────────────────────
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 180

# ── Route filtering ──────────────────────────────────────────────────
# Route names to exclude from the final map (e.g. state-level connectors
# that clutter the Circuit Trails view)
EXCLUDE_ROUTES = {
    "BicyclePA Route S",
    "BicyclePA Route L",
    "BicyclePA Route E",
}

# ── Trailhead matching ───────────────────────────────────────────────
# Max distance (degrees) to snap a trailhead label to a graph node (~200m)
TRAILHEAD_MATCH_DIST = 0.002

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
