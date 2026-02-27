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
        after_routes = re.sub(r"\b" + re.escape(rname) + r"\b", "", after_routes, flags=re.IGNORECASE)

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
            if not props.get("station_label"):
                props["station_label"] = name
                props["station_id"] = props["id"]
                props["osm_named"] = True
                added += 1
        else:
            unmatched.append((elem["id"], name, lon, lat, _is_parking(elem)))

    log("trailheads", f"Pass 1: labelled {added} existing nodes ({len(unmatched)} unmatched)")

    # ── Pass 2: project unmatched trailheads onto nearest route edge ──
    inserted = 0
    split_edge_indices: set[int] = set()  # each original edge consumed at most once
    new_items: list[tuple] = []           # (fi, edge1, edge2, point_feat)

    for osm_id, name, lon, lat, is_park in unmatched:
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
        new_label = normalize_label(old_label, route_names)
        # If the final label is just a route name and the node was NOT
        # explicitly named by an OSM trailhead/parking element, clear it.
        # This removes auto-assigned route names from bare endpoint nodes
        # while preserving official trailhead names that happen to match.
        if new_label.lower() in route_names_lower and not props.get("osm_named"):
            new_label = ""
            cleared += 1
        if new_label != old_label:
            props["station_label"] = new_label
            normalized += 1
        if props.get("has_parking"):
            props["station_label"] = "🅿 " + props["station_label"].strip()

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
        label = props.get("station_label", "").strip()
        nid = props.get("id", "")
        if not label and node_deg.get(nid, 0) != 1:
            props.update({"station_id": "", "station_label": "", "deg": "0", "deg_in": "0", "deg_out": "0"})
            hidden += 1

    log("nodes", f"Hidden {hidden} unnamed non-endpoint nodes")
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

    # Trail pipeline
    data = fetch_trails(offline=args.offline)
    data = filter_routes(data)
    if not args.no_trailheads and not args.offline:
        data = add_trailheads(data)
    else:
        log("trailheads", "Skipped")
    data = normalize_labels(data)   # strip route names + generic suffixes from all labels
    data = filter_nodes(data)

    Path(FILTERED_FILE).write_text(json.dumps(data))
    log("trails", f"Filtered trail data saved to {FILTERED_FILE}")

    # Rail pipeline
    rail = {"type": "FeatureCollection", "features": []}
    if not args.no_rail:
        rail = process_rail()
    else:
        log("rail", "Skipped (--no-rail)")

    # Combine and render
    merge_and_render(data, rail, out_dir)


if __name__ == "__main__":
    main()
