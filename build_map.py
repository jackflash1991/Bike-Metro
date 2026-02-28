#!/usr/bin/env python3
"""
build_map.py — Circuit Trails + SEPTA Regional Rail transit map pipeline.

Stages:
  1. Fetch   — query Overpass for bicycle route relations (or load cache)
  2. Filter  — remove excluded routes (BicyclePA, etc.)
  3. Enrich  — label graph nodes that match OSM trailhead locations
  4. Prune   — hide unnamed interior nodes, keep endpoints + trailheads
  5. Render  — pipe through loom | transitmap to produce an SVG

Usage:
    python3 build_map.py                  # full rebuild
    python3 build_map.py --offline        # skip Overpass, use cached JSON
    python3 build_map.py --no-rail        # trails only, skip SEPTA
    python3 build_map.py --no-trailheads  # skip trailhead enrichment step
    python3 build_map.py --no-amenities   # skip amenity icon pass
    python3 build_map.py --out DIR        # write SVG to DIR instead of default
    python3 build_map.py -h               # show this help

Output:
    combined.svg — written to the current directory, and optionally copied
    to an output directory (auto-detected for Windows WSL or macOS Desktop).
"""

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from config import (
    BBOX, OVERPASS_URL, OVERPASS_TIMEOUT, OVERPASS_MIRRORS,
    EXCLUDE_ROUTES, TRAILHEAD_MATCH_DIST, TRAILHEAD_INSERT_DIST, TRAIL_PARKING_RE,
    AMENITY_MATCH_DIST, AMENITY_MIN_SPACING, ENDPOINT_MERGE_DIST,
    RAIL_STATION_MERGE_DIST, RAIL_NODE_MIN_SPACING,
    LINE_WIDTH, LINE_SPACING, STATION_LABEL_SIZE, LINE_LABEL_SIZE,
    CACHE_FILE, FILTERED_FILE, COMBINED_FILE, OUTPUT_SVG,
)
import urllib.request
import urllib.parse


# ── Helpers ───────────────────────────────────────────────────────────

def log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def check_binaries(need_rail: bool) -> None:
    """Warn early if required loom binaries are missing from PATH or repo root."""
    required = ["loom", "topo", "transitmap"]
    if need_rail:
        required.append("gtfs2graph")

    missing = []
    for name in required:
        local = Path(f"./{name}")
        on_path = shutil.which(name)
        if not local.exists() and not on_path:
            missing.append(name)

    if missing:
        print(
            f"[setup] WARNING: missing binaries: {', '.join(missing)}\n"
            f"[setup] Download the loom tools for your platform from:\n"
            f"[setup]   https://github.com/ad-freiburg/loom/releases\n"
            f"[setup] Place them in this directory (or add to PATH) before running.",
            flush=True,
        )


def overpass_query(query: str) -> list:
    """POST a query to Overpass, falling back through mirrors on 5xx / timeout."""
    encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_exc: Exception = RuntimeError("No mirrors configured")
    for mirror in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(mirror, data=encoded)
            with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT + 30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result["elements"]
        except Exception as exc:
            log("overpass", f"{mirror} failed ({exc.__class__.__name__}: {exc}) — trying next mirror")
            last_exc = exc
    raise last_exc


def detect_output_dir(cli_out: str | None) -> Path | None:
    """
    Resolve the best output directory for the SVG:
      - CLI --out flag wins if provided
      - Windows WSL: /mnt/c/Users/<user>/Downloads
      - macOS: ~/Desktop
      - Otherwise: None (current directory only)
    """
    if cli_out:
        p = Path(cli_out).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # WSL
    wsl_base = Path("/mnt/c/Users")
    if wsl_base.exists():
        win_user = os.environ.get("USER") or os.environ.get("USERNAME")
        candidate = wsl_base / win_user if win_user else None
        if candidate and candidate.exists():
            dl = candidate / "Downloads"
            if dl.exists():
                return dl

    # macOS
    if platform.system() == "Darwin":
        return Path.home() / "Desktop"

    return None


# ── Stage 1: Fetch ────────────────────────────────────────────────────

def fetch_trails(offline: bool) -> dict:
    cache = Path(CACHE_FILE)

    if offline:
        if cache.exists():
            log("fetch", f"Offline mode — loading {CACHE_FILE}")
            return json.loads(cache.read_text())
        else:
            log("fetch", f"ERROR: --offline requested but {CACHE_FILE} not found")
            sys.exit(1)

    log("fetch", "Running osm2loom.py...")
    # osm2loom writes JSON to stdout, progress to stderr
    result = subprocess.run(
        [sys.executable, "osm2loom.py"],
        capture_output=True, text=True
    )
    # Forward osm2loom's stderr (progress messages) to our stderr
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        log("fetch", f"osm2loom.py failed (exit {result.returncode})")
        sys.exit(1)

    data = json.loads(result.stdout)
    cache.write_text(result.stdout)
    log("fetch", f"Cached to {CACHE_FILE}")
    return data


# ── Stage 2: Filter excluded routes ──────────────────────────────────

def filter_routes(data: dict) -> dict:
    kept, removed = [], 0
    for feat in data["features"]:
        props = feat.get("properties", {})
        if "lines" in props:
            lines = [line for line in props["lines"] if line.get("label") not in EXCLUDE_ROUTES]
            if not lines:
                removed += 1
                continue
            props["lines"] = lines
        kept.append(feat)
    data["features"] = kept
    log("filter", f"Removed {removed} features belonging to excluded routes")
    return data


# ── Node type classification ─────────────────────────────────────

# Priority values — higher number wins conflicts.
_NODE_PRIORITY = {
    "rail_station":        100,
    "named_trailhead":      50,
    "synthetic_trailhead":  40,
    "amenity_bearing":      30,
    "unnamed_endpoint":     20,
    "unnamed_interior":     10,
}


def _classify_trail_node(props: dict) -> tuple[str, int]:
    """Return (node_type, priority) for an existing trail Point feature."""
    if props.get("node_type"):
        # Already classified (e.g. rail station).
        ntype = props["node_type"]
        return ntype, _NODE_PRIORITY.get(ntype, 10)
    label = props.get("station_label", "").strip()
    osm_named = props.get("osm_named", False)
    deg = int(props.get("deg", 2))
    nid = props.get("id", "")

    if osm_named and label:
        ntype = "named_trailhead"
    elif label and nid.startswith("th_"):
        ntype = "synthetic_trailhead"
    elif label:
        ntype = "named_trailhead"
    elif deg == 1:
        ntype = "unnamed_endpoint"
    else:
        ntype = "unnamed_interior"
    return ntype, _NODE_PRIORITY[ntype]


def _make_rail_id(stop_id: str) -> str:
    """Prefix GTFS stop_id to avoid collision with numeric OSM node IDs."""
    return f"rail_{stop_id}"


# ── Stage 2b: Merge rail stations into trail graph ───────────────

def merge_rail_into_trails(trails: dict, rail: dict) -> dict:
    """Integrate rail station nodes into the trail graph as primary anchors.

    1. Prefix all rail IDs to prevent collision with OSM IDs.
    2. Tag every rail Point with node_type="rail_station", priority=100.
    3. Classify every existing trail Point by its properties.
    4. For each rail station, find the nearest trail node within
       RAIL_STATION_MERGE_DIST.  If found, absorb the trail node: keep
       the rail station's position and label, re-point all edges touching
       the trail node to the rail station, and hide the trail node.
    5. Append all rail features (with updated IDs) into the trail graph.
    6. Validate edge references.

    Returns a single unified FeatureCollection.
    """
    features = trails["features"]

    # ── Step 1 & 2: prefix rail IDs, tag rail nodes ──────────────
    rail_points: list[dict] = []
    rail_edges: list[dict] = []
    for feat in rail["features"]:
        geom_type = feat["geometry"]["type"]
        props = feat["properties"]
        if geom_type == "Point":
            old_id = props.get("id", props.get("station_id", ""))
            new_id = _make_rail_id(old_id)
            props["id"] = new_id
            if props.get("station_id"):
                props["station_id"] = new_id
            props["node_type"] = "rail_station"
            props["priority"] = _NODE_PRIORITY["rail_station"]
            rail_points.append(feat)
        elif geom_type == "LineString":
            old_from = props.get("from", "")
            old_to = props.get("to", "")
            props["from"] = _make_rail_id(old_from)
            props["to"] = _make_rail_id(old_to)
            rail_edges.append(feat)

    log("merge_rail", f"Rail input: {len(rail_points)} stations, {len(rail_edges)} edges")

    # ── Step 3: classify trail nodes ─────────────────────────────
    trail_points: list[dict] = []
    for feat in features:
        if feat["geometry"]["type"] == "Point":
            ntype, pri = _classify_trail_node(feat["properties"])
            feat["properties"]["node_type"] = ntype
            feat["properties"]["priority"] = pri
            trail_points.append(feat)

    # ── Step 4: spatial merge — absorb nearby trail nodes ────────
    # Build a map of trail-point id → feature for fast lookup.
    id_to_trail_pt: dict[str, dict] = {
        f["properties"]["id"]: f for f in trail_points
    }

    absorbed = 0
    # Track which trail node IDs got replaced by which rail ID.
    remap: dict[str, str] = {}

    for rail_feat in rail_points:
        rlon, rlat = rail_feat["geometry"]["coordinates"]

        best_dist, best_trail_id = float("inf"), None
        for tp in trail_points:
            tp_id = tp["properties"]["id"]
            if tp_id in remap:
                continue  # already absorbed by another rail station
            tlon, tlat = tp["geometry"]["coordinates"]
            d = math.sqrt((rlon - tlon) ** 2 + (rlat - tlat) ** 2)
            if d < best_dist:
                best_dist, best_trail_id = d, tp_id

        if best_dist < RAIL_STATION_MERGE_DIST and best_trail_id is not None:
            remap[best_trail_id] = rail_feat["properties"]["id"]
            dist_m = best_dist * 111_000
            trail_label = id_to_trail_pt[best_trail_id]["properties"].get("station_label", "")
            rail_label = rail_feat["properties"].get("station_label", "")
            log("merge_rail", f"  Absorb trail node {best_trail_id} ({trail_label}) "
                f"→ rail {rail_feat['properties']['id']} ({rail_label}) — {dist_m:.0f} m")
            absorbed += 1

    # Re-point trail edges that reference absorbed nodes.
    repointed = 0
    for feat in features:
        if feat["geometry"]["type"] != "LineString":
            continue
        props = feat["properties"]
        coords = feat["geometry"]["coordinates"]
        for key, coord_idx in [("from", 0), ("to", -1)]:
            old_id = props.get(key, "")
            if old_id in remap:
                new_id = remap[old_id]
                props[key] = new_id
                # Update coordinate to rail station position.
                rail_pt = next(
                    (r for r in rail_points if r["properties"]["id"] == new_id), None
                )
                if rail_pt:
                    coords[coord_idx] = list(rail_pt["geometry"]["coordinates"])
                repointed += 1

    # Hide absorbed trail nodes (zero out their labels so filter_nodes drops them).
    for old_id in remap:
        tp = id_to_trail_pt.get(old_id)
        if tp:
            tp["properties"].update({
                "station_id": "", "station_label": "",
                "deg": "0", "deg_in": "0", "deg_out": "0",
                "node_type": "absorbed",
            })

    # ── Step 5: append rail features to the unified graph ────────
    # Points first (loom convention), then edges.
    features = rail_points + features + rail_edges

    # ── Step 6: validate edge references ─────────────────────────
    point_ids = {
        f["properties"]["id"]
        for f in features
        if f["geometry"]["type"] == "Point" and f["properties"].get("id")
    }
    orphans = 0
    for feat in features:
        if feat["geometry"]["type"] != "LineString":
            continue
        for key in ("from", "to"):
            ref = feat["properties"].get(key, "")
            if ref and ref not in point_ids:
                orphans += 1
    if orphans:
        log("merge_rail", f"  WARNING: {orphans} edge endpoint(s) reference non-existent nodes")

    log("merge_rail", f"Absorbed {absorbed} trail nodes into rail stations, "
        f"re-pointed {repointed} edge endpoints")

    return {"type": "FeatureCollection", "features": features}


# ── Label normalisation helpers ───────────────────────────────────────

# Generic words / phrases that add no useful location information.
_SUFFIX_RE = re.compile(
    r"[\s,\-]*\b("
    r"trailhead|trail\s+head|parking\s+area|parking\s+lot"
    r"|parking|access\s+point|access\s+area|access"
    r")\b[\s,\-]*$",
    re.IGNORECASE,
)
# Leftover connector words after route names have been removed.
_CONNECTOR_RE = re.compile(
    r"^\s*(\band\b|\bor\b|&|\bat\b|\bnear\b|[-,/])\s*"
    r"|\s*(\band\b|\bor\b|&|\bat\b|\bnear\b|[-,/])\s*$",
    re.IGNORECASE,
)


def normalize_label(name: str, route_names: set) -> str:
    """Shorten a trailhead / parking label for use as a metro-map station name.

    Steps (in order):
      1. Strip generic suffixes: "Trailhead", "Parking", "Parking Area", etc.
      2. Strip substrings that exactly match a known route name — they are
         redundant because the station already appears on those lines.
         Route names are stripped longest-first to avoid leaving fragments.
      3. Clean up leftover connectors ("and", "&", "-", …).
      4. Fall back to the suffix-stripped version if route stripping leaves
         less than 3 characters (e.g. "Audubon Loop Trail Trailhead" →
         suffix-strip → "Audubon Loop Trail" → route-strip → "" → fall back
         to "Audubon Loop Trail").
      5. Fall back to the original name if even the suffix-stripped version
         is empty.
    """
    # Step 1 – strip suffix
    after_suffix = _SUFFIX_RE.sub("", name).strip()

    # Step 2 – strip route names (longest first)
    after_routes = after_suffix
    for rname in sorted(route_names, key=len, reverse=True):
        if len(rname) < 5:
            continue  # skip very short names to avoid false positives
        # Only strip if the route name is NOT a substring of a larger word
        # in the label.  E.g. don't strip "Washington" from "Fort Washington"
        # if the result would be shorter than 3 chars or a single short word.
        candidate = re.sub(r"\b" + re.escape(rname) + r"\b", "", after_routes, flags=re.IGNORECASE)
        candidate_clean = re.sub(r"[\s,.\-&/]+", " ", candidate).strip()
        # If stripping this route name leaves less than 3 meaningful chars,
        # skip it — the route name is probably part of a proper noun.
        if len(candidate_clean) < 3:
            continue
        after_routes = candidate

    # Step 3 – clean connectors (repeat a few times to handle chains)
    for _ in range(3):
        after_routes = _CONNECTOR_RE.sub("", after_routes).strip(" ,.-&/")

    # Step 4 – fall back to suffix-stripped version if route-stripping went too far
    result = after_routes if len(after_routes) >= 3 else after_suffix

    # Step 5 – ultimate fallback
    return result.strip() if len(result.strip()) >= 3 else name


# ── Stage 3: Enrich with trailhead labels ────────────────────────────

def _project_onto_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> tuple[float, float, float, float]:
    """Return (proj_x, proj_y, t, dist) — nearest point on segment AB to P."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return ax, ay, 0.0, math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return qx, qy, t, math.sqrt((px - qx) ** 2 + (py - qy) ** 2)


def _nearest_edge(
    lon: float, lat: float,
    features: list,
    max_dist: float,
) -> tuple | None:
    """Find nearest point on any LineString feature within max_dist.

    Returns (feature_index, segment_index, t, proj_lon, proj_lat) or None.
    """
    best_dist = max_dist
    best: tuple | None = None
    for fi, feat in enumerate(features):
        if feat["geometry"]["type"] != "LineString":
            continue
        coords = feat["geometry"]["coordinates"]
        for si in range(len(coords) - 1):
            ax, ay = coords[si]
            bx, by = coords[si + 1]
            qx, qy, t, d = _project_onto_segment(lon, lat, ax, ay, bx, by)
            if d < best_dist:
                best_dist = d
                best = (fi, si, t, qx, qy)
    return best


def add_trailheads(data: dict) -> dict:
    """Enrich the graph with trailhead station labels.

    Pass 1 — label nearest existing graph node (≤ TRAILHEAD_MATCH_DIST, ~200 m).
    Pass 2 — for still-unmatched trailheads, project perpendicularly onto the
              nearest route edge (≤ TRAILHEAD_INSERT_DIST, ~100 m), split that
              edge, and insert a new synthetic station node at the projection
              point.  This handles trailheads beside long segments with no
              nearby OSM node.
    """
    south, west, north, east = BBOX
    query = f"""[out:json][timeout:60];
(
  node["highway"="trailhead"]({south},{west},{north},{east});
  node["tourism"="information"]["information"="trailhead"]({south},{west},{north},{east});
  node["amenity"="parking"]["name"~"{TRAIL_PARKING_RE}",i]({south},{west},{north},{east});
  way["amenity"="parking"]["name"~"{TRAIL_PARKING_RE}",i]({south},{west},{north},{east});
);
out center;"""

    log("trailheads", "Querying Overpass for trailhead nodes and trail parking...")
    try:
        elements = overpass_query(query)
    except Exception as exc:
        log("trailheads", f"Overpass error: {exc} — skipping enrichment")
        return data

    # ── Pass 1: label nearest existing graph node ─────────────────────
    # Process tagged trailheads before parking lots so a parking lot can never
    # overwrite a proper trailhead label on the same nearby node.
    def _is_parking(e):
        return e.get("tags", {}).get("amenity") == "parking"
    elements = (
        [e for e in elements if not _is_parking(e)] +
        [e for e in elements if _is_parking(e)]
    )

    added = 0
    unmatched: list[tuple] = []  # (osm_id, name, lon, lat) — candidates for pass 2
    for elem in elements:
        name = elem.get("tags", {}).get("name", "")
        if not name:
            continue
        # Nodes have lon/lat directly; ways return a center object.
        lon = elem.get("lon") or (elem.get("center") or {}).get("lon")
        lat = elem.get("lat") or (elem.get("center") or {}).get("lat")
        if lon is None or lat is None:
            continue

        best_dist, best_feat = float("inf"), None
        for feat in data["features"]:
            if feat["geometry"]["type"] != "Point":
                continue
            nlon, nlat = feat["geometry"]["coordinates"]
            d = math.sqrt((lon - nlon) ** 2 + (lat - nlat) ** 2)
            if d < best_dist:
                best_dist, best_feat = d, feat

        if best_dist < TRAILHEAD_MATCH_DIST and best_feat:
            props = best_feat["properties"]
            if _is_parking(elem):
                props["has_parking"] = True
            # Rail station labels take priority — don't overwrite them.
            if props.get("node_type") == "rail_station" and props.get("station_label"):
                continue
            if not props.get("station_label"):
                props["station_label"] = name
                props["station_id"] = props["id"]
                props["osm_named"] = True
                props["node_type"] = "named_trailhead"
                props["priority"] = _NODE_PRIORITY["named_trailhead"]
                added += 1
        else:
            unmatched.append((elem["id"], name, lon, lat, _is_parking(elem)))

    log("trailheads", f"Pass 1: labelled {added} existing nodes ({len(unmatched)} unmatched)")

    # ── Pass 2: project unmatched trailheads onto nearest route edge ──
    inserted = 0
    split_edge_indices: set[int] = set()  # each original edge consumed at most once
    new_items: list[tuple] = []           # (fi, edge1, edge2, point_feat)

    for osm_id, name, lon, lat, is_park in unmatched:
        # Skip insertion if a rail station is nearby — the rail station serves
        # as the anchor and we don't need a synthetic trailhead competing with it.
        rail_nearby = any(
            math.sqrt((lon - f["geometry"]["coordinates"][0]) ** 2 +
                       (lat - f["geometry"]["coordinates"][1]) ** 2) < TRAILHEAD_INSERT_DIST
            for f in data["features"]
            if f["geometry"]["type"] == "Point"
            and f["properties"].get("node_type") == "rail_station"
        )
        if rail_nearby:
            continue

        result = _nearest_edge(lon, lat, data["features"], TRAILHEAD_INSERT_DIST)
        if result is None:
            continue
        fi, si, _t, qlon, qlat = result
        if fi in split_edge_indices:
            continue  # another trailhead already claimed this edge

        orig = data["features"][fi]
        coords = orig["geometry"]["coordinates"]
        props = orig["properties"]

        # Synthetic node id — deterministic, won't collide with real OSM ids.
        new_id = f"th_{osm_id}"

        first_half = coords[: si + 1] + [[qlon, qlat]]
        second_half = [[qlon, qlat]] + coords[si + 1 :]
        if len(first_half) < 2 or len(second_half) < 2:
            continue  # degenerate split — skip

        edge1 = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": first_half},
            "properties": {"from": props["from"], "to": new_id, "lines": props["lines"]},
        }
        edge2 = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": second_half},
            "properties": {"from": new_id, "to": props["to"], "lines": props["lines"]},
        }
        point_props = {
            "id": new_id,
            "station_id": new_id,
            "station_label": name,
            "osm_named": True,
            "node_type": "synthetic_trailhead",
            "priority": _NODE_PRIORITY["synthetic_trailhead"],
            "deg": "2",
            "deg_in": "1",
            "deg_out": "1",
        }
        if is_park:
            point_props["has_parking"] = True
        point = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [qlon, qlat]},
            "properties": point_props,
        }

        split_edge_indices.add(fi)
        new_items.append((fi, edge1, edge2, point))
        inserted += 1

    if new_items:
        remove = {item[0] for item in new_items}
        data["features"] = [f for i, f in enumerate(data["features"]) if i not in remove]
        for _, e1, e2, pt in new_items:
            data["features"].extend([pt, e1, e2])

    log("trailheads", f"Pass 2: inserted {inserted} new trailhead stations on edges")

    labeled = sum(
        1 for f in data["features"]
        if f["geometry"]["type"] == "Point" and f["properties"].get("station_label")
    )
    log("trailheads", f"Total labelled stations: {labeled}")
    return data


# ── Stage 3b: Normalize station labels ───────────────────────────────

def normalize_labels(data: dict) -> dict:
    """Strip redundant route names and generic suffixes from all station labels.

    Route names are collected from the graph itself so they are always current
    without any hardcoding.  Normalisation is applied to every labelled Point
    feature, including labels set by osm2loom's snap pass.
    """
    # Collect every route name present in the graph.
    route_names: set[str] = set()
    for feat in data["features"]:
        if feat["geometry"]["type"] == "LineString":
            for line in feat.get("properties", {}).get("lines", []):
                label = line.get("label", "").strip()
                if label:
                    route_names.add(label)

    route_names_lower = {r.lower() for r in route_names}

    normalized = 0
    cleared = 0
    for feat in data["features"]:
        if feat["geometry"]["type"] != "Point":
            continue
        props = feat["properties"]
        old_label = props.get("station_label", "").strip()
        if not old_label:
            continue
        # Rail station labels come from GTFS — skip normalization entirely.
        # Route-name stripping can damage proper nouns (e.g. "Fort Washington"
        # losing "Washington" because a route named "Washington" exists).
        if props.get("node_type") == "rail_station":
            continue
        new_label = normalize_label(old_label, route_names)
        # If the final label is just a route name and the node was NOT
        # explicitly named by an OSM trailhead/parking element, clear it.
        # This removes auto-assigned route names from bare endpoint nodes
        # while preserving official trailhead names that happen to match.
        elif new_label.lower() in route_names_lower and not props.get("osm_named"):
            new_label = ""
            cleared += 1
        if new_label != old_label:
            props["station_label"] = new_label
            normalized += 1
        # Parking icon (🅿️) is now applied in add_amenities() alongside other
        # amenity icons so all icons appear after the name in priority order.

    # De-duplicate: loom corrupts labels when it receives two nearby nodes
    # with identical station_label values.  This can happen when an OSM node
    # has name="X" AND a separate route node within snap range gets labelled
    # "X Trailhead" (both normalize to "X").  Keep the more authoritative one
    # (osm_named > not osm_named; otherwise lower degree = endpoint wins).
    _dedup_dist_sq = (2 * TRAILHEAD_MATCH_DIST) ** 2
    labeled_pts = [
        (feat, feat["geometry"]["coordinates"][0], feat["geometry"]["coordinates"][1])
        for feat in data["features"]
        if feat["geometry"]["type"] == "Point"
        and feat["properties"].get("station_label", "").strip()
    ]
    deduped = 0
    for i in range(len(labeled_pts)):
        feat_i, lon_i, lat_i = labeled_pts[i]
        label_i = feat_i["properties"].get("station_label", "").strip()
        if not label_i:
            continue
        for j in range(i + 1, len(labeled_pts)):
            feat_j, lon_j, lat_j = labeled_pts[j]
            label_j = feat_j["properties"].get("station_label", "").strip()
            if not label_j or label_i.lower() != label_j.lower():
                continue
            if (lon_i - lon_j) ** 2 + (lat_i - lat_j) ** 2 >= _dedup_dist_sq:
                continue
            # Same label within ~400 m — drop the less authoritative one.
            # Use priority first (rail_station=100 > named_trailhead=50 > ...),
            # then fall back to the original osm_named + degree tiebreaker.
            i_pri = int(feat_i["properties"].get("priority", 0))
            j_pri = int(feat_j["properties"].get("priority", 0))
            if i_pri != j_pri:
                drop_j = (j_pri < i_pri)
            else:
                i_named = bool(feat_i["properties"].get("osm_named"))
                j_named = bool(feat_j["properties"].get("osm_named"))
                i_deg = int(feat_i["properties"].get("deg", 2))
                j_deg = int(feat_j["properties"].get("deg", 2))
                drop_j = (i_named and not j_named) or (i_named == j_named and i_deg <= j_deg)
            victim = feat_j if drop_j else feat_i
            victim["properties"]["station_label"] = ""
            victim["properties"]["station_id"] = ""
            deduped += 1
            break

    log("labels", f"Normalized {normalized} station labels, cleared {cleared} route-name-only labels"
        + (f", de-duplicated {deduped}" if deduped else ""))
    return data


# ── Stage 3c: Merge nearby trail endpoints ───────────────────────────

def merge_nearby_endpoints(data: dict) -> dict:
    """Merge degree-1 trail endpoints into labelled stations on other trails.

    When the terminus of one trail sits within ENDPOINT_MERGE_DIST of a
    labelled station on a *different* trail, re-point the terminus's edge(s)
    to the station node.  The station becomes a transfer point where both
    trails meet (like Norristown, where the Chester Valley Trail ends near
    the Schuylkill River Trail).
    """
    # Index point features by id.
    id_to_point: dict[str, dict] = {}
    for feat in data["features"]:
        if feat["geometry"]["type"] == "Point":
            id_to_point[feat["properties"]["id"]] = feat

    # Build node → set of line ids from edges.
    node_lines: dict[str, set[str]] = {}
    for feat in data["features"]:
        if feat["geometry"]["type"] != "LineString":
            continue
        props = feat["properties"]
        line_ids = set()
        for ln in (props.get("lines") or []):
            lid = ln.get("id", "")
            if lid:
                line_ids.add(lid)
        for key in ("from", "to"):
            nid = props.get(key, "")
            if nid:
                node_lines.setdefault(nid, set()).update(line_ids)

    # Compute degree from edges (number of edge endpoints referencing each node).
    node_deg: dict[str, int] = {}
    for feat in data["features"]:
        if feat["geometry"]["type"] == "LineString":
            for key in ("from", "to"):
                nid = feat["properties"].get(key, "")
                if nid:
                    node_deg[nid] = node_deg.get(nid, 0) + 1

    # Find degree-1 endpoints.
    endpoints = [
        nid for nid, deg in node_deg.items()
        if deg == 1 and nid in id_to_point
    ]

    # Candidate targets: labelled stations + rail stations (even unlabeled).
    labelled = [
        feat for feat in data["features"]
        if feat["geometry"]["type"] == "Point"
        and (feat["properties"].get("station_label", "").strip()
             or feat["properties"].get("node_type") == "rail_station")
    ]

    def _repoint(old_id: str, new_id: str, new_coords: list) -> None:
        """Re-point every edge referencing old_id to new_id."""
        for feat in data["features"]:
            if feat["geometry"]["type"] != "LineString":
                continue
            props = feat["properties"]
            coords = feat["geometry"]["coordinates"]
            if props.get("from") == old_id:
                props["from"] = new_id
                coords[0] = list(new_coords)
            if props.get("to") == old_id:
                props["to"] = new_id
                coords[-1] = list(new_coords)

    def _hide(nid: str) -> None:
        """Zero-out a node so filter_nodes drops it."""
        feat = id_to_point.get(nid)
        if feat:
            feat["properties"].update({
                "station_id": "", "station_label": "",
                "deg": "0", "deg_in": "0", "deg_out": "0",
            })

    def _edge_neighbors(nid: str) -> set[str]:
        """Return all node IDs directly connected to nid by an edge."""
        nbrs: set[str] = set()
        for feat in data["features"]:
            if feat["geometry"]["type"] != "LineString":
                continue
            p = feat["properties"]
            if p.get("from") == nid and p.get("to"):
                nbrs.add(p["to"])
            if p.get("to") == nid and p.get("from"):
                nbrs.add(p["from"])
        return nbrs

    # ── Pass 1: merge degree-1 endpoints ──────────────────────────────
    merged = 0
    merged_targets: set[str] = set()   # track which stations received merges

    for ep_id in endpoints:
        ep_feat = id_to_point[ep_id]
        ep_lon, ep_lat = ep_feat["geometry"]["coordinates"]
        ep_lines = node_lines.get(ep_id, set())

        # Rail terminus stations should never merge into other rail stations.
        # They are distinct stops on distinct lines (e.g. CHW vs CHE).
        if ep_feat["properties"].get("node_type") == "rail_station":
            continue

        # Find the closest labelled station on a DIFFERENT set of trail lines.
        # Rail stations are always valid targets (different transport mode).
        best_dist, best_id = float("inf"), None
        for target in labelled:
            t_id = target["properties"]["id"]
            if t_id == ep_id:
                continue
            t_node_type = target["properties"].get("node_type")
            if t_node_type == "rail_station":
                pass  # always allow trail→rail merge (different mode)
            else:
                t_lines = node_lines.get(t_id, set())
                # Must share NO lines — otherwise they're on the same trail.
                if ep_lines & t_lines:
                    continue
            t_lon, t_lat = target["geometry"]["coordinates"]
            d = math.sqrt((ep_lon - t_lon) ** 2 + (ep_lat - t_lat) ** 2)
            if d < best_dist:
                best_dist, best_id = d, t_id

        if best_dist > ENDPOINT_MERGE_DIST or best_id is None:
            continue

        target_feat = id_to_point[best_id]
        target_label = target_feat["properties"].get("station_label", "")
        dist_m = best_dist * 111000
        log("merge", f"Merging endpoint {ep_id} into {best_id} ({target_label}) — {dist_m:.0f} m")

        target_coords = target_feat["geometry"]["coordinates"]
        _repoint(ep_id, best_id, target_coords)
        _hide(ep_id)
        merged_targets.add(best_id)
        merged += 1

    # ── Pass 2: cascade — absorb intermediate nodes left dangling ─────
    # After re-pointing degree-1 endpoints, a synthetic trailhead node
    # (e.g. "Norristown Transit Center", inserted by add_trailheads)
    # can end up one hop from the target station.  Walk outward from each
    # merge target and absorb labeled nodes within ENDPOINT_MERGE_DIST
    # that share no lines with the target (i.e. they belong to the trail
    # that just merged in).
    cascaded = 0
    for t_id in merged_targets:
        t_feat = id_to_point.get(t_id)
        if not t_feat:
            continue
        t_coords = t_feat["geometry"]["coordinates"]
        t_lines_orig = node_lines.get(t_id, set())

        # Walk outward up to a few hops.
        visited: set[str] = {t_id}
        frontier = _edge_neighbors(t_id) - visited
        for _ in range(3):           # max 3 hops from target
            if not frontier:
                break
            next_frontier: set[str] = set()
            for nbr_id in frontier:
                visited.add(nbr_id)
                nbr_feat = id_to_point.get(nbr_id)
                if not nbr_feat:
                    continue
                nbr_lon, nbr_lat = nbr_feat["geometry"]["coordinates"]
                d = math.sqrt((t_coords[0] - nbr_lon) ** 2 +
                              (t_coords[1] - nbr_lat) ** 2)
                if d > ENDPOINT_MERGE_DIST:
                    continue
                # Must not share any original lines with the target.
                nbr_lines = node_lines.get(nbr_id, set())
                if nbr_lines & t_lines_orig:
                    continue
                dist_m = d * 111000
                nbr_label = nbr_feat["properties"].get("station_label", "")
                log("merge", f"  Cascade: absorbing {nbr_id} ({nbr_label}) into {t_id} — {dist_m:.0f} m")
                _repoint(nbr_id, t_id, t_coords)
                _hide(nbr_id)
                cascaded += 1
                # Continue walking past this absorbed node.
                next_frontier |= _edge_neighbors(t_id) - visited
            frontier = next_frontier

    log("merge", f"Merged {merged} trail endpoints into transfer stations"
        + (f" (+{cascaded} cascaded)" if cascaded else ""))
    return data


# ── Stage 3d: Add amenity icons ──────────────────────────────────────

# Bike-centric icon priority: rider needs first, car access last.
# All icons appear after the station name.
_ICON_ORDER = ["🚻️", "🚰️", "🔧️", "ℹ️", "🅿️"]


def add_amenities(data: dict) -> dict:
    """Snap amenity POIs to trail nodes and append emoji icons to their labels.

    Amenity types queried:
      🔧️  amenity=bicycle_repair_station
      🚰️  amenity=drinking_water
      🚻️  amenity=toilets (public / unspecified access only)
      ℹ️   tourism=information + information=map
      🅿️  amenity=parking (name~trail|greenway) — queried as ways with out center;
           also pre-seeded from has_parking flag set by add_trailheads

    Icon priority order (bike-centric): 🚻️ 🚰️ 🔧️ ℹ️ 🅿️
    Parking appears last — it's useful context but not the primary concern
    for a cyclist already on the trail.

    Only snaps to *existing* graph nodes (no edge splitting) within
    AMENITY_MATCH_DIST (~100 m).  A minimum-spacing rule (AMENITY_MIN_SPACING,
    ~200 m) prevents the same icon appearing on back-to-back nodes in
    dense areas like Fairmount Park.  Icons are collected per node then
    written in _ICON_ORDER so label format is always consistent regardless
    of Overpass return order.
    """
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

    log("amenities", "Querying Overpass for amenity POIs (repair, water, restrooms, maps)...")
    try:
        elements = overpass_query(query)
    except Exception as exc:
        log("amenities", f"Overpass error: {exc} — skipping amenity icons")
        return data

    log("amenities", f"Found {len(elements)} amenity elements in bbox")

    points = [f for f in data["features"] if f["geometry"]["type"] == "Point"]

    # Process parking elements last so that water/toilets/repair/map icons are
    # already in node_icons when parking runs.  This lets parking prefer nodes
    # that already carry other amenity icons (so 🅿️ clusters with 🚰️🚻️ on
    # the same dot rather than landing on a separate nearby orphan node).
    parking_elems = [e for e in elements if e.get("tags", {}).get("amenity") == "parking"]
    other_elems   = [e for e in elements if e.get("tags", {}).get("amenity") != "parking"]
    elements = other_elems + parking_elems

    # Collect icons per node id — assembled in _ICON_ORDER at the end.
    node_icons: dict[str, set[str]] = {}

    # Best amenity name per node — used as label fallback for nodes that have
    # no trailhead name.  Parking names take priority (they're processed last
    # and are most descriptive for trail users).
    node_amenity_name: dict[str, str] = {}

    # Pre-seed parking from the has_parking flag set by add_trailheads().
    for feat in points:
        if feat["properties"].get("has_parking"):
            node_icons.setdefault(feat["properties"]["id"], set()).add("🅿️")

    # Minimum-spacing tracking: icon_type → [(lat, lon), ...]
    placed: dict[str, list[tuple[float, float]]] = {}

    snapped = 0
    for elem in elements:
        tags = elem.get("tags", {})
        lon = elem.get("lon") or (elem.get("center") or {}).get("lon")
        lat = elem.get("lat") or (elem.get("center") or {}).get("lat")
        if lon is None or lat is None:
            continue

        amenity = tags.get("amenity", "")
        tourism  = tags.get("tourism", "")
        info     = tags.get("information", "")

        if amenity == "bicycle_repair_station":
            icon, icon_type = "🔧️", "repair"
        elif tourism == "information" and info == "map":
            icon, icon_type = "ℹ️", "map"
        elif amenity == "drinking_water":
            icon, icon_type = "🚰️", "water"
        elif amenity == "toilets":
            icon, icon_type = "🚻️", "toilets"
        elif amenity == "parking":
            icon, icon_type = "🅿️", "parking"
        else:
            continue

        # Minimum-spacing check
        spacing_blocked = any(
            math.sqrt((lat - plat) ** 2 + (lon - plon) ** 2) < AMENITY_MIN_SPACING
            for plat, plon in placed.get(icon_type, [])
        )
        if icon_type == "parking":
            log("amenities:parking", f"  id={elem.get('id')} center=({lat:.6f},{lon:.6f}) spacing_blocked={spacing_blocked}")
        if spacing_blocked:
            continue

        # Nearest graph node — parking lots use a larger snap distance because
        # their centroid can be 100–200m from the trail edge.
        snap_dist = TRAILHEAD_MATCH_DIST if icon_type == "parking" else AMENITY_MATCH_DIST
        best_dist, best_id = float("inf"), None
        for feat in points:
            nlon, nlat = feat["geometry"]["coordinates"]
            d = math.sqrt((lon - nlon) ** 2 + (lat - nlat) ** 2)
            if d < best_dist:
                best_dist, best_id = d, feat["properties"]["id"]

        # For parking: if another amenity icon (water, toilets, repair, map)
        # already landed on a nearby node, prefer that node so all icons
        # cluster on the same visible dot rather than scattering across
        # several adjacent orphan nodes.
        if icon_type == "parking":
            best_amenity_dist, best_amenity_id = float("inf"), None
            for feat in points:
                nid = feat["properties"]["id"]
                if nid not in node_icons:
                    continue
                # Only consider nodes whose existing icons are NOT solely 🅿️
                # (pre-seeded has_parking nodes are fine targets too, but skip
                # nodes that have only a parking icon already — they offer no
                # consolidation benefit).
                if node_icons[nid] == {"🅿️"}:
                    continue
                nlon, nlat = feat["geometry"]["coordinates"]
                d = math.sqrt((lon - nlon) ** 2 + (lat - nlat) ** 2)
                if d < best_amenity_dist:
                    best_amenity_dist, best_amenity_id = d, nid
            if best_amenity_id is not None and best_amenity_dist <= snap_dist:
                best_dist, best_id = best_amenity_dist, best_amenity_id

        if icon_type == "parking":
            dist_m = best_dist * 111000
            log("amenities:parking", f"  → nearest node={best_id} dist={dist_m:.0f}m snap_dist={snap_dist*111000:.0f}m {'✓ SNAP' if best_dist <= snap_dist else '✗ TOO FAR'}")

        if best_dist > snap_dist or best_id is None:
            continue

        node_icons.setdefault(best_id, set()).add(icon)
        placed.setdefault(icon_type, []).append((lat, lon))
        # Record the amenity's name for label fallback.  Any non-empty name
        # is stored; parking names overwrite earlier ones since they tend to
        # be the most useful trail-facing label (e.g. "Chester Valley Trail
        # Parking").  Water/repair/etc. names fill in when no parking name
        # is available.
        amenity_name = tags.get("name", "").strip()
        if amenity_name and (best_id not in node_amenity_name or icon_type == "parking"):
            node_amenity_name[best_id] = amenity_name
        snapped += 1

    # Apply icons to nodes in defined priority order.
    assembled = 0
    id_to_feat = {f["properties"]["id"]: f for f in points}
    for nid, icons in node_icons.items():
        feat = id_to_feat.get(nid)
        if not feat:
            continue
        props = feat["properties"]
        ordered = [ic for ic in _ICON_ORDER if ic in icons]
        if not ordered:
            continue
        base = props.get("station_label", "").strip()
        # For nodes with no trailhead name, fall back to the nearest amenity's
        # name (e.g. "Chester Valley Trail Parking") so the dot gets a label.
        if not base:
            base = node_amenity_name.get(nid, "")
        icon_str = " ".join(ordered)
        props["station_label"] = (icon_str + " " + base).strip() if base else icon_str
        if not props.get("station_label", "").strip():
            props["station_id"] = nid  # make unlabeled node visible to transitmap
        assembled += 1

    counts = {k: len(v) for k, v in placed.items()}
    log("amenities", f"Snapped {snapped} amenity POIs, applied icons to {assembled} nodes: {counts}")
    return data


# ── Stage 4: Prune unnamed interior nodes ────────────────────────────

def filter_nodes(data: dict) -> dict:
    """Zero-out unnamed non-endpoint nodes so transitmap ignores them."""
    node_deg: dict[str, int] = {}
    for feat in data["features"]:
        if feat["geometry"]["type"] == "LineString":
            props = feat["properties"]
            for key in ("from", "to"):
                nid = props.get(key, "")
                node_deg[nid] = node_deg.get(nid, 0) + 1

    hidden = 0
    for feat in data["features"]:
        if feat["geometry"]["type"] != "Point":
            continue
        props = feat["properties"]
        # Rail stations are never hidden — they're primary anchors.
        if props.get("node_type") == "rail_station":
            continue
        label = props.get("station_label", "").strip()
        nid = props.get("id", "")
        if not label and node_deg.get(nid, 0) != 1:
            props.update({"station_id": "", "station_label": "", "deg": "0", "deg_in": "0", "deg_out": "0"})
            hidden += 1

    log("nodes", f"Hidden {hidden} unnamed non-endpoint nodes (rail stations preserved)")
    return data


# ── Stage 5b: Optional SEPTA rail ────────────────────────────────────

def _gtfs_to_loom(gtfs_dir: str, label: str) -> dict:
    """Run a GTFS directory through gtfs2graph | topo and return topo-format GeoJSON.

    We stop *before* loom so that trail data (also in topo format from osm2loom)
    and rail data can be merged at the same stage and processed together by the
    single  ./loom | ./transitmap  call in merge_and_render.  Running loom on
    rail first and then again on the merged output causes rail features to be
    silently dropped.
    """
    r1 = subprocess.run(f"./gtfs2graph -m rail {gtfs_dir}", shell=True, capture_output=True)
    r2 = subprocess.run("./topo", shell=True, input=r1.stdout, capture_output=True)
    if r2.returncode != 0:
        log("rail", f"WARNING: topo failed on {label} — skipping")
        return {"type": "FeatureCollection", "features": []}
    data = json.loads(r2.stdout)
    log("rail", f"{label}: {len(data['features'])} features loaded")
    return data


def process_rail() -> dict:
    log("rail", "Processing rail (SEPTA + Keystone merged)...")
    return _gtfs_to_loom("combined_rail_gtfs/", "SEPTA + Keystone")


# ── Stage 6: Merge + render SVG ──────────────────────────────────────

_STLBLP_RE = re.compile(r'<path\b[^>]*\bid="stlblp\d+"[^>]*/>', re.DOTALL)
_PATH_D_RE  = re.compile(r'\bd="([^"]+)"')
_COORD_RE   = re.compile(r'[ML]\s*([-\d.]+)\s+([-\d.]+)')


def _fix_label_paths(svg_file: str, extra: float = 20.0) -> None:
    """Extend every station-label textPath by `extra` SVG units.

    transitmap computes label-path lengths from font metrics that can
    underestimate the rendered glyph width by a fraction of a character,
    causing the last character to be silently clipped at the path end
    (e.g. "Cromby" → "Cromb").  Extending each path by a small constant
    gives all labels a safety margin without changing their visual position.
    """
    try:
        content = Path(svg_file).read_text(encoding="utf-8")
    except OSError:
        return

    def _extend(m: re.Match) -> str:
        tag = m.group(0)
        d_m = _PATH_D_RE.search(tag)
        if not d_m:
            return tag
        pts = [(float(x), float(y)) for x, y in _COORD_RE.findall(d_m.group(1))]
        if len(pts) < 2:
            return tag
        dx = pts[-1][0] - pts[-2][0]
        dy = pts[-1][1] - pts[-2][1]
        seg = math.sqrt(dx * dx + dy * dy)
        if seg < 0.001:
            return tag
        nx = pts[-1][0] + (dx / seg) * extra
        ny = pts[-1][1] + (dy / seg) * extra
        new_d = re.sub(r'([-\d.]+)\s+([-\d.]+)\s*$',
                       f'{nx:.1f} {ny:.1f}', d_m.group(1))
        return tag.replace(d_m.group(0), f'd="{new_d}"', 1)

    fixed = _STLBLP_RE.sub(_extend, content)
    if fixed != content:
        Path(svg_file).write_text(fixed, encoding="utf-8")
        log("render", f"Extended station label paths (+{extra:.0f} units) to fix font-metric clipping")

def merge_and_render(trails: dict, rail: dict, out_dir: Path | None) -> None:
    merged = {
        "type": "FeatureCollection",
        "features": trails["features"] + rail["features"],
    }
    Path(COMBINED_FILE).write_text(json.dumps(merged))
    log("merge", f"{len(merged['features'])} total features → {COMBINED_FILE}")

    log("render", "Generating SVG via loom | transitmap...")
    cmd = (
        f"cat {COMBINED_FILE} | ./loom | ./transitmap -l "
        f"--line-width={LINE_WIDTH} --line-spacing={LINE_SPACING} "
        f"--station-label-textsize={STATION_LABEL_SIZE} "
        f"--line-label-textsize={LINE_LABEL_SIZE} "
        f"> {OUTPUT_SVG}"
    )
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        log("render", "WARNING: transitmap exited non-zero — SVG may be incomplete")

    _fix_label_paths(OUTPUT_SVG)

    if out_dir:
        dest = out_dir / OUTPUT_SVG
        shutil.copy(OUTPUT_SVG, dest)
        log("done", f"SVG copied to {dest}")

        # Try to open in default viewer (best-effort, silent on failure)
        _try_open(dest)
    else:
        log("done", f"SVG saved to {OUTPUT_SVG}")


def _try_open(path: Path) -> None:
    """Open a file in the platform's default viewer, silently ignoring errors."""
    try:
        if platform.system() == "Darwin":
            subprocess.run(["open", str(path)], stderr=subprocess.DEVNULL)
        elif Path("/mnt/c").exists():  # WSL
            win_path = str(path).replace("/mnt/c/", "C:\\").replace("/", "\\")
            subprocess.run(f'explorer.exe "{win_path}"', shell=True, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["xdg-open", str(path)], stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Circuit Trails + SEPTA Rail Map Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--offline", action="store_true",
                   help="Skip Overpass fetch, use cached circuit_trails.json")
    p.add_argument("--no-rail", action="store_true",
                   help="Skip SEPTA regional rail processing")
    p.add_argument("--no-trailheads", action="store_true",
                   help="Skip trailhead label enrichment")
    p.add_argument("--no-amenities", action="store_true",
                   help="Skip amenity icon pass (repair stands, maps, water, restrooms)")
    p.add_argument("--out", metavar="DIR",
                   help="Directory to copy the output SVG into")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = detect_output_dir(args.out)

    print("=" * 60)
    print("  Circuit Trails + SEPTA Rail Map Builder")
    print("=" * 60)
    check_binaries(need_rail=not args.no_rail)

    # Trail pipeline — fetch and filter
    data = fetch_trails(offline=args.offline)
    data = filter_routes(data)

    # Rail pipeline — process EARLY so rail stations become primary anchors
    rail = {"type": "FeatureCollection", "features": []}
    if not args.no_rail:
        rail = process_rail()
    else:
        log("rail", "Skipped (--no-rail)")

    # Merge rail stations into trail graph BEFORE enrichment
    if rail["features"]:
        data = merge_rail_into_trails(data, rail)

    # Enrichment pipeline — now runs on unified graph (rail + trails)
    if not args.no_trailheads and not args.offline:
        data = add_trailheads(data)
    else:
        log("trailheads", "Skipped")
    data = normalize_labels(data)   # strip route names + generic suffixes from all labels
    data = merge_nearby_endpoints(data)  # merge trail termini into nearby transfer stations
    if not args.no_amenities and not args.offline:
        data = add_amenities(data)
    else:
        log("amenities", "Skipped")
    data = filter_nodes(data)

    Path(FILTERED_FILE).write_text(json.dumps(data))
    log("trails", f"Filtered data (incl. integrated rail) saved to {FILTERED_FILE}")

    # Render — rail is already integrated into data; pass empty rail dict
    merge_and_render(data, {"type": "FeatureCollection", "features": []}, out_dir)


if __name__ == "__main__":
    main()
