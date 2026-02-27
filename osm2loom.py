#!/usr/bin/env python3
"""
osm2loom.py — Fetch OSM bicycle route relations and emit loom-format GeoJSON.

Standalone usage (stdout pipe into loom tools):
    python3 osm2loom.py > circuit_trails.json
    cat circuit_trails.json | ./topo | ./loom | ./transitmap -l --random-colors > trails.svg

Importable usage (called by build_map.py):
    from osm2loom import fetch_and_build
    data = fetch_and_build()

How it works:
  1. Queries Overpass for all bicycle route relations in BBOX.
  2. Splits each way at trailhead nodes so trailheads become labeled graph nodes.
  3. Outputs a GeoJSON FeatureCollection with Point nodes first, then LineString edges,
     matching the convention expected by loom's topo/loom/transitmap pipeline.
"""

import json
import math
import sys
import hashlib
import urllib.request
import urllib.parse
from collections import defaultdict

from config import BBOX_STR, OVERPASS_URL, OVERPASS_TIMEOUT, OVERPASS_MIRRORS, TRAILHEAD_SNAP_DIST, TRAIL_PARKING_RE


# ── Color helpers ────────────────────────────────────────────────────

def deterministic_color(name: str) -> str:
    """Generate a consistent, saturated hex color from a route name."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
    r = min(((h >> 16) & 0xFF) | 0x40, 255)
    g = min(((h >> 8) & 0xFF) | 0x40, 255)
    b = min((h & 0xFF) | 0x40, 255)
    return f"{r:02x}{g:02x}{b:02x}"


# ── Overpass fetch ───────────────────────────────────────────────────

def query_overpass(bbox: str) -> dict:
    """Query Overpass for bicycle route relations + full geometry.

    Tries each mirror in OVERPASS_MIRRORS in order, falling back on 5xx / timeout.
    """
    query = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}];
relation["type"="route"]["route"="bicycle"]({bbox});
(._;>;);
out body;
"""
    _log("Querying Overpass API...")
    encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_exc: Exception = RuntimeError("No mirrors configured")
    for mirror in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(mirror, data=encoded)
            with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT + 30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _log(f"Received {len(result['elements'])} elements (via {mirror})")
            return result
        except Exception as exc:
            _log(f"  {mirror} failed ({exc.__class__.__name__}: {exc}) — trying next mirror")
            last_exc = exc
    raise last_exc


# ── Core build logic ─────────────────────────────────────────────────

def fetch_and_build(bbox: str = BBOX_STR) -> dict:
    """
    Fetch OSM data and build a loom-compatible GeoJSON FeatureCollection.

    Returns the dict (does NOT write to disk or stdout — callers decide that).
    """
    raw = query_overpass(bbox)
    return _build_geojson(raw)


def _build_geojson(data: dict) -> dict:
    # Index nodes
    osm_nodes: dict[int, tuple[float, float]] = {}
    for elem in data["elements"]:
        if elem["type"] == "node":
            osm_nodes[elem["id"]] = (elem["lon"], elem["lat"])

    # Index ways
    osm_ways: dict[int, list[int]] = {}
    for elem in data["elements"]:
        if elem["type"] == "way":
            osm_ways[elem["id"]] = elem.get("nodes", [])

    # Named nodes and trailhead detection
    trailhead_nodes: set[int] = set()
    node_names: dict[int, str] = {}
    for elem in data["elements"]:
        if elem["type"] == "node" and "tags" in elem:
            tags = elem["tags"]
            name = tags.get("name", "")
            if name:
                node_names[elem["id"]] = name
            if tags.get("highway") == "trailhead":
                trailhead_nodes.add(elem["id"])
            if tags.get("tourism") == "information" and tags.get("information") == "trailhead":
                trailhead_nodes.add(elem["id"])

    _log(f"Found {len(trailhead_nodes)} trailhead nodes in bbox")

    # Extract route relations
    routes = []
    for elem in data["elements"]:
        if elem["type"] != "relation":
            continue
        tags = elem.get("tags", {})
        name = tags.get("name", tags.get("ref", f"Route {elem['id']}"))
        color = tags.get("colour", tags.get("color", ""))
        if color.startswith("#"):
            color = color[1:]
        if not color:
            color = deterministic_color(name)
        way_refs = [m["ref"] for m in elem.get("members", []) if m["type"] == "way"]
        routes.append({"id": str(elem["id"]), "name": name, "color": color, "way_refs": way_refs})

    _log(f"Found {len(routes)} bicycle routes")
    for r in routes:
        _log(f"  - {r['name']} ({len(r['way_refs'])} ways)")

    # Filter trailheads to only those sitting on a route way
    route_node_ids: set[int] = set()
    for route in routes:
        for wid in route["way_refs"]:
            if wid in osm_ways:
                route_node_ids.update(osm_ways[wid])
    trailhead_on_routes = trailhead_nodes & route_node_ids
    _log(f"  ({len(trailhead_on_routes)} trailheads are on route ways)")

    # ── Snap nearby external trailheads to route nodes ───────────────
    # Run a separate Overpass query for ALL trailheads in the bbox (not just
    # those that happen to be tagged as part of a bicycle route relation).
    # For each external trailhead within TRAILHEAD_SNAP_DIST of a route-way
    # node, snap it: that node joins the split-point set so it becomes a
    # labelled station, and the trailhead's name is used as the label.
    _log("Querying Overpass for external trailheads to snap...")
    _snap_query = f"""
[out:json][timeout:60];
(
  node["highway"="trailhead"]({BBOX_STR});
  node["tourism"="information"]["information"="trailhead"]({BBOX_STR});
  node["amenity"="parking"]["name"~"{TRAIL_PARKING_RE}",i]({BBOX_STR});
  way["amenity"="parking"]["name"~"{TRAIL_PARKING_RE}",i]({BBOX_STR});
);
out center;
"""
    try:
        _enc = urllib.parse.urlencode({"data": _snap_query}).encode("utf-8")
        _snap_elems = None
        for _mirror in OVERPASS_MIRRORS:
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(_mirror, data=_enc), timeout=90
                ) as _resp:
                    _snap_elems = json.loads(_resp.read().decode("utf-8"))["elements"]
                break
            except Exception as _me:
                _log(f"  {_mirror} failed ({_me.__class__.__name__}: {_me}) — trying next mirror")
        if _snap_elems is None:
            raise RuntimeError("All mirrors failed for snap query")
        _log(f"  Found {len(_snap_elems)} total trailheads/parking in bbox")

        # Flat list of all route-way nodes for nearest-neighbour scan.
        _route_nodes = [(nid, osm_nodes[nid]) for nid in route_node_ids if nid in osm_nodes]

        _snapped = 0
        for _e in _snap_elems:
            if _e["id"] in trailhead_on_routes:
                continue  # already a member of a route way
            _name = _e.get("tags", {}).get("name", "")
            # Nodes have lon/lat directly; ways return a center object.
            _tlon = _e.get("lon") or (_e.get("center") or {}).get("lon")
            _tlat = _e.get("lat") or (_e.get("center") or {}).get("lat")
            if _tlon is None or _tlat is None:
                continue

            _best_dist, _best_nid = float("inf"), None
            for _nid, (_nlon, _nlat) in _route_nodes:
                _d = math.sqrt((_tlon - _nlon) ** 2 + (_tlat - _nlat) ** 2)
                if _d < _best_dist:
                    _best_dist, _best_nid = _d, _nid

            if _best_nid is not None and _best_dist < TRAILHEAD_SNAP_DIST:
                trailhead_on_routes.add(_best_nid)
                if _name and _best_nid not in node_names:
                    node_names[_best_nid] = _name
                _snapped += 1

        _log(f"  Snapped {_snapped} external trailheads to route nodes "
             f"(total split-points: {len(trailhead_on_routes)})")
    except Exception as _exc:
        _log(f"  Warning: external trailhead snap failed ({_exc}) — skipping")

    # Build edge features, splitting ways at mid-way trailheads
    edge_features = []
    graph_nodes: set[int] = set()
    node_degree: dict[int, int] = defaultdict(int)
    node_routes: dict[int, list[str]] = defaultdict(list)
    node_routes: dict[int, list[str]] = defaultdict(list)

    for route in routes:
        for wid in route["way_refs"]:
            if wid not in osm_ways:
                continue
            nds = osm_ways[wid]
            coords, valid_nodes = [], []
            for n in nds:
                if n in osm_nodes:
                    coords.append(list(osm_nodes[n]))
                    valid_nodes.append(n)
            if len(coords) < 2:
                continue

            # Split points: endpoints + any trailhead in the interior
            split_idx = sorted({0, len(valid_nodes) - 1} | {
                i for i, n in enumerate(valid_nodes)
                if 0 < i < len(valid_nodes) - 1 and n in trailhead_on_routes
            })

            for j in range(len(split_idx) - 1):
                s, e = split_idx[j], split_idx[j + 1]
                sub_coords = coords[s:e + 1]
                fn, tn = valid_nodes[s], valid_nodes[e]
                if len(sub_coords) < 2:
                    continue
                graph_nodes.add(fn)
                graph_nodes.add(tn)
                node_degree[fn] += 1
                node_routes[fn].append(route["name"])
                node_routes[tn].append(route["name"])
                node_degree[tn] += 1
                node_routes[fn].append(route["name"])
                node_routes[tn].append(route["name"])
                edge_features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": sub_coords},
                    "properties": {
                        "from": str(fn),
                        "to": str(tn),
                        "lines": [{"id": route["id"], "label": route["name"], "color": route["color"]}],
                    },
                })

    _log(f"Created {len(edge_features)} edge features")

    # Build point features for graph nodes
    point_features = []
    for node_id in graph_nodes:
        if node_id not in osm_nodes:
            continue
        lon, lat = osm_nodes[node_id]
        deg = node_degree.get(node_id, 1)
        label = node_names.get(node_id, "")
        if not label and deg == 1:
            label = " / ".join(dict.fromkeys(node_routes[node_id]))
        point_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": str(node_id),
                "station_id": str(node_id) if label else "",
                "station_label": label,
                "deg": str(deg),
                "deg_in": str(deg),
                "deg_out": str(deg),
            },
        })

    trailhead_labeled = sum(1 for nid in trailhead_on_routes if nid in graph_nodes)
    _log(f"Created {len(point_features)} node features ({trailhead_labeled} are trailhead nodes)")

    # Points first, then edges — loom convention
    return {"type": "FeatureCollection", "features": point_features + edge_features}


# ── Logging helper ───────────────────────────────────────────────────

def _log(msg: str) -> None:
    """Write a progress message to stderr (keeps stdout clean for piping)."""
    print(msg, file=sys.stderr)


# ── Standalone entry point ───────────────────────────────────────────

def main():
    data = fetch_and_build()
    json.dump(data, sys.stdout)
    _log(f"\nDone! Output {len(data['features'])} total features")


if __name__ == "__main__":
    main()
