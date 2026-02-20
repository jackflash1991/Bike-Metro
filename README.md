# OSM Trails to LOOM Transit Map Pipeline

A pipeline for extracting trail data from OpenStreetMap and converting it to LOOM (GeoJSON) format for transit map visualization and outdoor activity planning.

## Overview

The pipeline downloads trail data from the [Overpass API](https://overpass-api.de/), parses the OSM XML response, and transforms it into a GeoJSON-compatible LOOM format. Supported trail types include paths, tracks, footways, hiking routes, and bicycle routes.

### Key Steps

1. **Data Extraction** — Query the Overpass API for OSM trail data within a bounding box.
2. **Data Transformation** — Parse XML and convert to LOOM GeoJSON with standardized properties.
3. **Filtering** — Optionally filter trails by difficulty level or trail type.
4. **Validation** — Validate output against the GeoJSON/LOOM schema.

## Installation

```bash
# Clone the repository
git clone https://github.com/jackflash1991/Bike-Metro.git
cd Bike-Metro

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

- **requests** — HTTP client for Overpass API queries
- **shapely** — Geometric operations for trail length calculations

## Usage

### Download OSM trail data

```bash
python osm_trails_to_loom.py --download --bbox "37.7749,-122.4194,37.8049,-122.3894"
```

The `--bbox` argument takes `min_lat,min_lon,max_lat,max_lon`. This saves raw XML to `osm_data.xml`.

### Build LOOM dataset from downloaded data

```bash
python osm_trails_to_loom.py --build
```

Reads `osm_data.xml` and writes `circuit_trails_loom.json`.

### Download and build in one step

```bash
python osm_trails_to_loom.py --download --build --bbox "37.7749,-122.4194,37.8049,-122.3894"
```

### Filter by difficulty or trail type

```bash
python osm_trails_to_loom.py --build --difficulty "easy"
python osm_trails_to_loom.py --build --type "track"
```

### View trail statistics

```bash
python osm_trails_to_loom.py --build --stats
python osm_trails_to_loom.py --stats          # uses existing osm_data.xml
```

Statistics include total trail count, trail types distribution, difficulty levels, and total length.

## CLI Reference

| Flag | Description |
|------|-------------|
| `--download` | Download OSM data from the Overpass API |
| `--build` | Parse OSM XML and generate LOOM JSON output |
| `--bbox` | Bounding box (`min_lat,min_lon,max_lat,max_lon`). Required with `--download` |
| `--difficulty` | Filter trails by difficulty level (e.g. `easy`, `hard`) |
| `--type` | Filter trails by type (e.g. `path`, `track`, `footway`) |
| `--stats` | Display trail statistics after building |

## Output Format

The pipeline produces a GeoJSON `FeatureCollection`:

```json
{
  "type": "FeatureCollection",
  "timestamp": "2026-02-20T12:00:00",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "LineString",
        "coordinates": [[-122.4194, 37.7749], [-122.4184, 37.7759]]
      },
      "properties": {
        "id": "12345",
        "name": "Bay Trail",
        "type": "path",
        "difficulty": "easy",
        "tags": { "highway": "path", "surface": "gravel" }
      }
    }
  ]
}
```

## Running Tests

```bash
python -m unittest discover tests -v
```

## Project Structure

```
Bike-Metro/
├── osm_trails_to_loom.py   # Main pipeline script
├── requirements.txt         # Python dependencies
├── tests/
│   └── test_osm_trails_to_loom.py  # Unit tests
├── .gitignore
└── README.md
```
