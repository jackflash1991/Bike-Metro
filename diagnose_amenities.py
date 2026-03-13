#!/usr/bin/env python3
"""Diagnostic script: check why specific amenities fail to snap.

Run after build_map.py has cached circuit_trails.json:
    python3 diagnose_amenities.py
"""

import json
import math

# Target amenities to investigate
TARGETS = [
    ("drinking_water node/5589331389", -75.4216525, 40.1098097),
    ("drinking_water node/11878661783", -75.4233555, 40.1095457),
    ("toilets way/48612863 (estimate)", -75.4225, 40.1093),  # approx center
]

# Current thresholds
AMENITY_MATCH_DIST = 0.001
AMENITY_INSERT_DIST = 0.0015
AMENITY_MIN_SPACING = 0.002
COS_LAT = math.cos(math.radians(40.1))  # longitude scaling at this latitude


def project_onto_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return ax, ay, 0.0, math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return qx, qy, t, math.sqrt((px - qx) ** 2 + (py - qy) ** 2)


def project_scaled(px, py, ax, ay, bx, by):
    """Same projection but with longitude scaled by cos(lat)."""
    spx, sax, sbx = px * COS_LAT, ax * COS_LAT, bx * COS_LAT
    dx, dy = sbx - sax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return ax, ay, 0.0, math.sqrt((spx - sax) ** 2 + (py - ay) ** 2)
    t = ((spx - sax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    qx_s, qy = sax + t * dx, ay + t * dy
    qx = qx_s / COS_LAT
    dist = math.sqrt((spx - qx_s) ** 2 + (py - qy) ** 2)
    return qx, qy, t, dist


def deg_to_meters(deg):
    return deg * 111_000


with open("circuit_trails.json") as f:
    data = json.load(f)

edges = [(i, f) for i, f in enumerate(data["features"])
         if f["geometry"]["type"] == "LineString"]
points = [f for f in data["features"]
          if f["geometry"]["type"] == "Point"]

print(f"Loaded {len(edges)} edges, {len(points)} point nodes\n")

# Filter edges near Valley Forge area (lon ~ -75.45 to -75.40, lat ~ 40.09 to 40.12)
vf_edges = []
for fi, feat in edges:
    coords = feat["geometry"]["coordinates"]
    for c in coords:
        if -75.45 <= c[0] <= -75.40 and 40.09 <= c[1] <= 40.12:
            lines = feat["properties"].get("lines", [])
            labels = [l.get("label", "?") for l in lines]
            vf_edges.append((fi, feat, labels))
            break

print(f"Edges near Valley Forge area: {len(vf_edges)}")
for fi, feat, labels in vf_edges[:15]:
    coords = feat["geometry"]["coordinates"]
    print(f"  edge #{fi}: {labels[0] if labels else '?'} ({len(coords)} coords)")

print()

for name, lon, lat in TARGETS:
    print(f"=== {name} (lon={lon}, lat={lat}) ===")

    # Nearest graph node
    best_node_dist, best_node_id, best_node_label = float("inf"), None, ""
    for feat in points:
        nlon, nlat = feat["geometry"]["coordinates"]
        d = math.sqrt((lon - nlon) ** 2 + (lat - nlat) ** 2)
        if d < best_node_dist:
            best_node_dist = d
            best_node_id = feat["properties"]["id"]
            best_node_label = feat["properties"].get("station_label", "")
    print(f"  Nearest node: {best_node_id} ('{best_node_label}')")
    print(f"    raw dist: {best_node_dist:.6f} deg (~{deg_to_meters(best_node_dist):.0f}m)")
    print(f"    Pass 1 AMENITY_MATCH_DIST ({AMENITY_MATCH_DIST}): "
          f"{'PASS' if best_node_dist <= AMENITY_MATCH_DIST else 'FAIL'}")

    # Nearest edge (raw degrees)
    best_edge_dist = float("inf")
    best_edge_info = None
    for fi, feat, labels in edges:
        coords = feat["geometry"]["coordinates"]
        for si in range(len(coords) - 1):
            ax, ay = coords[si]
            bx, by = coords[si + 1]
            qx, qy, t, d = project_onto_segment(lon, lat, ax, ay, bx, by)
            if d < best_edge_dist:
                best_edge_dist = d
                route_labels = [l.get("label", "?")
                                for l in feat["properties"].get("lines", [])]
                best_edge_info = (fi, si, t, qx, qy, route_labels)

    if best_edge_info:
        fi, si, t, qx, qy, route_labels = best_edge_info
        print(f"  Nearest edge (raw deg): edge #{fi} seg #{si} ({route_labels[0] if route_labels else '?'})")
        print(f"    raw dist: {best_edge_dist:.6f} deg (~{deg_to_meters(best_edge_dist):.0f}m)")
        print(f"    Pass 2 AMENITY_INSERT_DIST ({AMENITY_INSERT_DIST}): "
              f"{'PASS' if best_edge_dist <= AMENITY_INSERT_DIST else 'FAIL'}")

    # Nearest edge (lat-scaled)
    best_scaled_dist = float("inf")
    best_scaled_info = None
    for fi, feat, labels in edges:
        coords = feat["geometry"]["coordinates"]
        for si in range(len(coords) - 1):
            ax, ay = coords[si]
            bx, by = coords[si + 1]
            qx, qy, t, d = project_scaled(lon, lat, ax, ay, bx, by)
            if d < best_scaled_dist:
                best_scaled_dist = d
                route_labels = [l.get("label", "?")
                                for l in feat["properties"].get("lines", [])]
                best_scaled_info = (fi, si, t, qx, qy, route_labels)

    if best_scaled_info:
        fi, si, t, qx, qy, route_labels = best_scaled_info
        print(f"  Nearest edge (lat-scaled): edge #{fi} seg #{si} ({route_labels[0] if route_labels else '?'})")
        print(f"    scaled dist: {best_scaled_dist:.6f} deg (~{deg_to_meters(best_scaled_dist):.0f}m)")

    # What AMENITY_INSERT_DIST would be needed?
    print(f"  → Need AMENITY_INSERT_DIST >= {best_edge_dist:.6f} (raw) "
          f"or >= {best_scaled_dist:.6f} (scaled) to catch this amenity")
    print()
