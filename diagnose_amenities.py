#!/usr/bin/env python3
"""Diagnostic script: check why specific amenities fail to snap.

Run after build_map.py has cached circuit_trails.json:
    python3 diagnose_amenities.py
"""

import json
import math
import urllib.request
import urllib.parse

from config import (
    BBOX, OVERPASS_MIRRORS, OVERPASS_TIMEOUT,
    AMENITY_MATCH_DIST, AMENITY_MIN_SPACING, AMENITY_INSERT_DIST,
    TRAILHEAD_MATCH_DIST, TRAIL_PARKING_RE,
)

# Target amenity OSM IDs to investigate
TARGET_IDS = {5589331389, 11878661783}
TARGET_WAY_IDS = {48612863}

COS_LAT = math.cos(math.radians((BBOX[0] + BBOX[2]) / 2))


def deg_to_meters(deg):
    return deg * 111_000


def scaled_dist(lon1, lat1, lon2, lat2):
    return math.sqrt(((lon1 - lon2) * COS_LAT) ** 2 + (lat1 - lat2) ** 2)


# ── Load trail graph ─────────────────────────────────────────────────
with open("circuit_trails.json") as f:
    data = json.load(f)

edges = [(i, f) for i, f in enumerate(data["features"])
         if f["geometry"]["type"] == "LineString"]
points = [f for f in data["features"]
          if f["geometry"]["type"] == "Point"]

print(f"Loaded {len(edges)} edges, {len(points)} point nodes\n")

# ── Fetch the same amenity elements that build_map.py queries ────────
south, west, north, east = BBOX
query = f"""[out:json][timeout:60];
(
  node["amenity"="bicycle_repair_station"]({south},{west},{north},{east});
  node["tourism"="information"]["information"="map"]({south},{west},{north},{east});
  node["amenity"="drinking_water"]({south},{west},{north},{east});
  node["amenity"="toilets"]["access"!="private"]({south},{west},{north},{east});
  way["amenity"="toilets"]["access"!="private"]({south},{west},{north},{east});
  way["amenity"="parking"]["name"~"{TRAIL_PARKING_RE}",i]({south},{west},{north},{east});
);
out center;"""

print("Querying Overpass for amenity elements (same query as build_map.py)...")
encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
elements = None
for mirror in OVERPASS_MIRRORS:
    try:
        req = urllib.request.Request(mirror, data=encoded)
        with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT + 30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        elements = result["elements"]
        print(f"  Got {len(elements)} elements from {mirror}\n")
        break
    except Exception as exc:
        print(f"  {mirror} failed: {exc}")
if elements is None:
    print("  ERROR: All mirrors failed. Cannot diagnose.")
    raise SystemExit(1)

# ── Find our target amenities in the Overpass results ────────────────
targets = []
for elem in elements:
    eid = elem["id"]
    etype = elem["type"]
    if (etype == "node" and eid in TARGET_IDS) or (etype == "way" and eid in TARGET_WAY_IDS):
        lon = elem.get("lon") or (elem.get("center") or {}).get("lon")
        lat = elem.get("lat") or (elem.get("center") or {}).get("lat")
        tags = elem.get("tags", {})
        amenity = tags.get("amenity", tags.get("tourism", ""))
        targets.append((f"{etype}/{eid} ({amenity})", lon, lat, elem))

if not targets:
    print("WARNING: None of the target IDs were found in Overpass results!")
    print("  Check that the IDs are correct and within BBOX.")
    # Still useful to show what amenities ARE near the area
else:
    print(f"Found {len(targets)} target amenities in Overpass results.\n")

# ── Simulate the snapping pipeline ───────────────────────────────────
# Reproduce the exact logic from add_amenities() to find WHY each target
# is skipped.

# First, simulate Pass 1 for ALL amenities to build the `placed` dict
# (this tells us what icons were placed before our targets are processed).
placed: dict[str, list[tuple[float, float]]] = {}
snap_log: list[tuple] = []  # (elem_id, icon_type, snapped_to_node, dist)

for elem in elements:
    tags = elem.get("tags", {})
    lon = elem.get("lon") or (elem.get("center") or {}).get("lon")
    lat = elem.get("lat") or (elem.get("center") or {}).get("lat")
    if lon is None or lat is None:
        continue

    amenity = tags.get("amenity", "")
    tourism = tags.get("tourism", "")
    info = tags.get("information", "")

    if amenity == "bicycle_repair_station":
        icon_type = "repair"
    elif tourism == "information" and info == "map":
        icon_type = "map"
    elif amenity == "drinking_water":
        icon_type = "water"
    elif amenity == "toilets":
        icon_type = "toilets"
    elif amenity == "parking":
        icon_type = "parking"
    else:
        continue

    # Min-spacing check
    spacing_blocked = any(
        scaled_dist(lon, lat, plon, plat) < AMENITY_MIN_SPACING
        for plat, plon in placed.get(icon_type, [])
    )

    # Nearest node check
    snap_dist = TRAILHEAD_MATCH_DIST if icon_type == "parking" else AMENITY_MATCH_DIST
    best_dist, best_id = float("inf"), None
    for feat in points:
        nlon, nlat = feat["geometry"]["coordinates"]
        d = scaled_dist(lon, lat, nlon, nlat)
        if d < best_dist:
            best_dist, best_id = d, feat["properties"]["id"]

    snapped = not spacing_blocked and best_dist <= snap_dist and best_id is not None

    eid = elem["id"]
    is_target = (elem["type"] == "node" and eid in TARGET_IDS) or \
                (elem["type"] == "way" and eid in TARGET_WAY_IDS)

    if is_target:
        print(f"=== {elem['type']}/{eid} ({icon_type}) at lon={lon}, lat={lat} ===")
        print(f"  Nearest node: {best_id}")
        print(f"    lat-corrected dist: {best_dist:.6f} deg (~{deg_to_meters(best_dist):.0f}m)")
        print(f"    snap threshold ({icon_type}): {snap_dist}")
        print(f"    distance check: {'PASS' if best_dist <= snap_dist else 'FAIL'}")
        print(f"  Spacing check:")
        if not placed.get(icon_type):
            print(f"    No prior {icon_type} icons placed — PASS")
        else:
            nearby = []
            for plat, plon in placed.get(icon_type, []):
                d = scaled_dist(lon, lat, plon, plat)
                if d < AMENITY_MIN_SPACING:
                    nearby.append((d, plat, plon))
            if nearby:
                print(f"    BLOCKED by {len(nearby)} nearby {icon_type} icon(s):")
                for d, plat, plon in sorted(nearby):
                    print(f"      {d:.6f} deg (~{deg_to_meters(d):.0f}m) at lat={plat:.6f}, lon={plon:.6f}")
            else:
                print(f"    No {icon_type} icons within {AMENITY_MIN_SPACING} deg — PASS")
        print(f"  RESULT: {'SNAPPED' if snapped else 'SKIPPED'}")
        print()

    if snapped:
        placed.setdefault(icon_type, []).append((lat, lon))

# ── Show all water/toilet icons placed near Valley Forge ─────────────
print("--- All water/toilet icons placed near Valley Forge (lat 40.08-40.12) ---")
for icon_type in ("water", "toilets"):
    nearby = [(lat, lon) for lat, lon in placed.get(icon_type, [])
              if 40.08 <= lat <= 40.12 and -75.45 <= lon <= -75.40]
    print(f"  {icon_type}: {len(nearby)} placed in area")
    for lat, lon in nearby:
        print(f"    lat={lat:.6f}, lon={lon:.6f}")
