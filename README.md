# Circuit Trails Transit Map

Generates a schematic transit-style map of the Greater Philadelphia [Circuit Trails](https://circuittrails.org/) bicycle network, optionally combined with SEPTA Regional Rail lines.

Built on the [loom](https://github.com/ad-freiburg/loom) transit map rendering toolkit.

---

## How it works

```
Overpass API
     │
     ▼
osm2loom.py          ← fetches bicycle route relations, builds loom GeoJSON
     │
     ▼
build_map.py         ← filters routes, enriches trailhead labels, prunes nodes
     │
     ├── ./loom       ← optimizes line bundling
     │
     └── ./transitmap ← renders final SVG
```

**Key design decisions:**
- Trailhead nodes (`highway=trailhead` or `tourism=information` + `information=trailhead`) split their parent ways so they appear as labeled "stations" on the map.
- Unnamed interior nodes are hidden; only endpoints and labeled trailheads are shown.
- BicyclePA state routes are excluded by default (they clutter the local Circuit Trails view). Edit `EXCLUDE_ROUTES` in `config.py` to change this.
- Colors are stripped before rendering so `--random-colors` assigns distinct per-route colors.

---

## Quick start

### Prerequisites

1. **Python 3.10+** — uses `str | None` union syntax
2. **loom tools** — `loom`, `topo`, `transitmap` binaries in the repo root (or on `$PATH`)  
   → Download from https://github.com/ad-freiburg/loom/releases
3. *(Optional)* **SEPTA GTFS** — `google_rail.zip` + `gtfs2graph` binary for rail overlay

### Install

```bash
git clone https://github.com/YOUR_USERNAME/circuit-trails.git
cd circuit-trails
pip install -r requirements.txt   # just flake8 for linting; no runtime deps
```

### Run

```bash
# Full build (fetches live OSM data, trails + SEPTA rail)
python3 build_map.py

# Trails only, no SEPTA
python3 build_map.py --no-rail

# Re-render from cached data (no network requests)
python3 build_map.py --offline --no-rail

# Write SVG to a specific folder
python3 build_map.py --no-rail --out ~/Desktop
```

The output SVG is saved as `combined.svg` in the current directory. On **macOS** it's also copied to `~/Desktop`; on **Windows WSL** it's copied to `C:\Users\<you>\Downloads`. Pass `--out DIR` to override.

---

## Configuration

All tunable parameters live in **`config.py`** — edit that file rather than the pipeline scripts.

| Setting | Default | Description |
|---|---|---|
| `BBOX` | Greater Philly | Bounding box for OSM queries `(south, west, north, east)` |
| `EXCLUDE_ROUTES` | BicyclePA S/L/E | Route names to drop from the map |
| `TRAILHEAD_MATCH_DIST` | `0.002` (~200m) | Snap radius for matching trailhead labels to graph nodes |
| `LINE_WIDTH` | `50` | SVG line width |
| `LINE_SPACING` | `25` | SVG spacing between parallel lines |
| `STATION_LABEL_SIZE` | `200` | Trailhead label font size |
| `LINE_LABEL_SIZE` | `160` | Route name label font size |

---

## Files

| File | Purpose |
|---|---|
| `config.py` | All configuration — edit this |
| `osm2loom.py` | Fetch OSM data → loom GeoJSON. Also usable standalone (`python3 osm2loom.py > trails.json`) |
| `build_map.py` | Full pipeline: fetch → filter → enrich → render |
| `audit_trailheads.overpassql` | Overpass Turbo query to audit missing trailhead tags in the area |
| `circuit_trails.json` | *(generated)* Raw OSM fetch cache |
| `circuit_trails_filtered.json` | *(generated)* Post-filter trail data |
| `combined.json` | *(generated)* Merged trails + rail |
| `combined.svg` | *(generated)* Final map output |

---

## Iterating quickly

The most common workflow when tweaking the map:

```bash
# First run: fetch and cache
python3 build_map.py --no-rail

# Subsequent runs: skip the Overpass fetch
python3 build_map.py --offline --no-rail
```

OSM data changes infrequently, so `--offline` is usually fine for days of iteration. Delete `circuit_trails.json` to force a fresh fetch.

---

## Trailhead audit

`audit_trailheads.overpassql` is a query you can paste into [Overpass Turbo](https://overpass-turbo.eu/) to visually inspect the area for:
- Existing tagged trailheads (green circles)
- Parking lots near trails that may be missing trailhead tags (red squares)
- Bicycle parking near trails (orange)
- Picnic sites and info boards near trails (purple/blue)

Use this to find tagging gaps in OSM and improve the map data at source.
