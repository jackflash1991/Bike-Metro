# OSM Trails to LOOM Transit Map Pipeline

Download OpenStreetMap trail data and convert it to a GeoJSON-based LOOM transit map format for visualization and route planning.

## Overview

The pipeline queries the Overpass API for hiking trails, cycling routes, footways, and tracks within a geographic bounding box, then converts the results into a GeoJSON FeatureCollection that can be consumed by LOOM or other mapping tools.

## Dependencies

- Python 3.8+
- [requests](https://pypi.org/project/requests/) — HTTP client for Overpass API
- [Shapely](https://pypi.org/project/shapely/) — geometric operations on trail data

## Setup

```bash
# Clone the repository
git clone https://github.com/jackflash1991/Bike-Metro.git
cd Bike-Metro

# Install dependencies
pip install -r requirements.txt
```

## Usage

```bash
# Download OSM trail data for a bounding box (min_lat,min_lon,max_lat,max_lon)
python osm_trails_to_loom.py --download --bbox "39.9,-75.3,40.1,-75.0"

# Build LOOM JSON from previously downloaded data
python osm_trails_to_loom.py --build

# Download and build in one step
python osm_trails_to_loom.py --download --build --bbox "39.9,-75.3,40.1,-75.0"

# Filter by difficulty or trail type
python osm_trails_to_loom.py --build --difficulty easy
python osm_trails_to_loom.py --build --type footway

# Show trail statistics
python osm_trails_to_loom.py --build --stats
python osm_trails_to_loom.py --stats
```

## Output

- `osm_data.xml` — Raw OSM XML response from Overpass API
- `circuit_trails_loom.json` — GeoJSON FeatureCollection with trail features
- `osm_trails_to_loom.log` — Processing log

## Key Steps

1. **Data Extraction** — Query the Overpass API for trails within a bounding box
2. **Parsing** — Extract trail geometry and metadata from OSM XML
3. **Filtering** — Optionally filter by trail type or difficulty
4. **Conversion** — Transform into GeoJSON FeatureCollection (LOOM format)
5. **Statistics** — Compute trail counts, types, and total length in km
