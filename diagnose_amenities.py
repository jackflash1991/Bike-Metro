#!/usr/bin/env python3
"""Diagnostic script: check why specific amenities fail to snap.

Run after build_map.py has cached circuit_trails.json:
    python3 diagnose_amenities.py            # with Overpass
    python3 diagnose_amenities.py --offline   # without Overpass (uses hardcoded targets)
"""

import argparse
import json
import math
import urllib.request
import urllib.parse

from config import (
    BBOX, OVERPASS_MIRRORS, OVERPASS_TIMEOUT,
    AMENITY_MATCH_DIST, AMENITY_MIN_SPACING, AMENITY_INSERT_DIST,
    AMENITY_SNAP_OVERRIDES, AMENITY_INSERT_OVERRIDES,
    TRAIL_PARKING_RE,
)

# Target amenity OSM IDs to investigate (used in online mode)
TARGET_IDS = {5589331389, 11878661783}
TARGET_WAY_IDS = {48612863}

# Hardcoded target amenities for offline mode (from OSM data)
OFFLINE_TARGETS = [
    {"type": "node", "id": 5589331389,
     "lat": 40.1098097, "lon": -75.4216525,
     "tags": {"amenity": "drinking_water"}},
    {"type": "node", "id": 11878661783,
     "lat": 40.1095457, "lon": -75.4233555,
     "tags": {"amenity": "drinking_water"}},
    {"type": "way", "id": 48612863,
     "center": {"lat": 40.1100, "lon": -75.4220},
     "tags": {"amenity": "toilets", "access": "yes"}},
]

COS_LAT = math.cos(math.radians((BBOX[0] + BBOX[2]) / 2))


def deg_to_meters(deg):
    return deg * 111_000


def scaled_dist(lon1, lat1, lon2, lat2):
    return math.sqrt(((lon1 - lon2) * COS_LAT) ** 2 + (lat1 - lat2) ** 2)


def project_onto_segment(px, py, ax, ay, bx, by):
    """Return (proj_x, proj_y, t, dist) for point P onto segment AB."""
    dx, dy = (bx - ax) * COS_LAT, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        d = math.sqrt(((px - ax) * COS_LAT) ** 2 + (py - ay) ** 2)
        return ax, ay, 0.0, d
    t = (((px - ax) * COS_LAT) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    qx = ax + t * (bx - ax)
    qy = ay + t * (by - ay)
    d = math.sqrt(((px - qx) * COS_LAT) ** 2 + (py - qy) ** 2)
    return qx, qy, t, d


# ── Load trail graph ─────────────────────────────────────────────────
with open("circuit_trails.json") as f:
    data = json.load(f)

edges = [(i, f) for i, f in enumerate(data["features"])
         if f["geometry"]["type"] == "LineString"]
points = [f for f in data["features"]
          if f["geometry"]["type"] == "Point"]

print(f"Loaded {len(edges)} edges, {len(points)} point nodes")

# Show coverage
if points:
    lats = [f["geometry"]["coordinates"][1] for f in points]
    lons = [f["geometry"]["coordinates"][0] for f in points]
    print(f"  lat range: {min(lats):.4f} - {max(lats):.4f}")
    print(f"  lon range: {min(lons):.4f} - {max(lons):.4f}")

# Count nodes in Valley Forge area
vf_nodes = [f for f in points
            if 40.08 < f["geometry"]["coordinates"][1] < 40.13
            and -75.45 < f["geometry"]["coordinates"][0] < -75.40]
vf_edges = [f for f in data["features"]
            if f["geometry"]["type"] == "LineString"
            and any(40.08 < c[1] < 40.13 and -75.45 < c[0] < -75.40
                    for c in f["geometry"]["coordinates"])]
print(f"  Valley Forge area (lat 40.08-40.13, lon -75.45 to -75.40):")
print(f"    {len(vf_nodes)} graph nodes, {len(vf_edges)} edges with coords in area")
if not vf_nodes and not vf_edges:
    print("    WARNING: No trail data in Valley Forge area!")
    print("    Amenities cannot snap — the trail graph must include this area first.")
print()

# ── Parse args ────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--offline", action="store_true",
                    help="Skip Overpass, use hardcoded target coordinates")
args = parser.parse_args()

# ── Get amenity elements ─────────────────────────────────────────────
if args.offline:
    print("Offline mode: using hardcoded target amenities\n")
    elements = OFFLINE_TARGETS
else:
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
        print("  ERROR: All mirrors failed. Try --offline mode.")
        raise SystemExit(1)

# ── Diagnose each target amenity ─────────────────────────────────────
for elem in elements:
    tags = elem.get("tags", {})
    lon = elem.get("lon") or (elem.get("center") or {}).get("lon")
    lat = elem.get("lat") or (elem.get("center") or {}).get("lat")
    if lon is None or lat is None:
        continue

    eid = elem["id"]
    etype = elem["type"]
    is_target = (etype == "node" and eid in TARGET_IDS) or \
                (etype == "way" and eid in TARGET_WAY_IDS)
    if not args.offline and not is_target:
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

    print(f"=== {etype}/{eid} ({icon_type}) at lon={lon}, lat={lat} ===")

    # ── Pass 1: nearest graph node ──
    snap_dist = AMENITY_SNAP_OVERRIDES.get(icon_type, AMENITY_MATCH_DIST)
    best_dist, best_id = float("inf"), None
    for feat in points:
        nlon, nlat = feat["geometry"]["coordinates"]
        d = scaled_dist(lon, lat, nlon, nlat)
        if d < best_dist:
            best_dist, best_id = d, feat["properties"]["id"]

    print(f"  Pass 1 — nearest graph node:")
    if best_id is not None:
        print(f"    node {best_id}: {best_dist:.6f} deg (~{deg_to_meters(best_dist):.0f}m)")
        print(f"    snap threshold ({icon_type}): {snap_dist} (~{deg_to_meters(snap_dist):.0f}m)")
        print(f"    {'PASS' if best_dist <= snap_dist else 'FAIL — too far'}")
    else:
        print(f"    No graph nodes found!")

    # ── Pass 2: nearest edge projection ──
    insert_dist = AMENITY_INSERT_OVERRIDES.get(icon_type, AMENITY_INSERT_DIST)
    best_edge_dist = float("inf")
    best_edge_info = None
    for fi, feat in edges:
        coords = feat["geometry"]["coordinates"]
        lines = feat["properties"].get("lines", [])
        route = lines[0].get("label", "?") if lines else "?"
        for si in range(len(coords) - 1):
            ax, ay = coords[si]
            bx, by = coords[si + 1]
            qx, qy, t, d = project_onto_segment(lon, lat, ax, ay, bx, by)
            if d < best_edge_dist:
                best_edge_dist = d
                best_edge_info = (fi, si, t, qx, qy, route, len(coords))

    print(f"  Pass 2 — nearest edge projection:")
    if best_edge_info:
        fi, si, t, qx, qy, route, ncoords = best_edge_info
        print(f"    edge feature[{fi}] seg {si}/{ncoords-1} (route: {route})")
        print(f"    projected dist: {best_edge_dist:.6f} deg (~{deg_to_meters(best_edge_dist):.0f}m)")
        print(f"    insert threshold ({icon_type}): {insert_dist} (~{deg_to_meters(insert_dist):.0f}m)")
        print(f"    {'PASS' if best_edge_dist < insert_dist else 'FAIL — too far'}")
    else:
        print(f"    No edges found in graph!")

    # ── Show 5 nearest edge coords (to verify trail geometry) ──
    if edges:
        coord_dists = []
        for fi, feat in edges:
            for ci, (cx, cy) in enumerate(feat["geometry"]["coordinates"]):
                d = scaled_dist(lon, lat, cx, cy)
                lines = feat["properties"].get("lines", [])
                route = lines[0].get("label", "?") if lines else "?"
                coord_dists.append((d, fi, ci, cx, cy, route))
        coord_dists.sort()
        print(f"  Nearest 5 edge coordinates (intermediate points along trail):")
        for d, fi, ci, cx, cy, route in coord_dists[:5]:
            print(f"    [{fi}][{ci}] ({cy:.6f}, {cx:.6f}) "
                  f"{d:.6f} deg (~{deg_to_meters(d):.0f}m) — {route}")

    print()

# ── Summary ──────────────────────────────────────────────────────────
print("--- Config values ---")
print(f"  AMENITY_MATCH_DIST:  {AMENITY_MATCH_DIST} (~{deg_to_meters(AMENITY_MATCH_DIST):.0f}m)")
print(f"  AMENITY_INSERT_DIST: {AMENITY_INSERT_DIST} (~{deg_to_meters(AMENITY_INSERT_DIST):.0f}m)")
print(f"  AMENITY_MIN_SPACING: {AMENITY_MIN_SPACING} (~{deg_to_meters(AMENITY_MIN_SPACING):.0f}m)")
print(f"  AMENITY_SNAP_OVERRIDES:   {AMENITY_SNAP_OVERRIDES}")
print(f"  AMENITY_INSERT_OVERRIDES: {AMENITY_INSERT_OVERRIDES}")
