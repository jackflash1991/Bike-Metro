# OSM Trails to LOOM Transit Map Pipeline

This document provides an overview of the pipeline for converting OpenStreetMap (OSM) trails into the LOOM transit map format.

## Overview
The LOOM transit map pipeline is designed to facilitate the integration of OSM trail data, allowing for improved visualization and accessibility of transit routes crossed by these trails. This enables better planning and reporting for outdoor activities.

## Key Steps
1. **Data Extraction**: Extract OSM trail data relevant to the area of interest.
2. **Data Transformation**: Convert the extracted data into a format suitable for LOOM.
3. **Integration**: Combine the transformed data with existing transit map data.
4. **Validation**: Ensure the data accuracy and completeness.

## Prerequisites

- Python 3.7 or higher

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/jackflash1991/Bike-Metro.git
   cd Bike-Metro
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

```bash
# Download OSM data for a specific geographic area
python osm_trails_to_loom.py --download --bbox "37.7749,-122.4194,37.8049,-122.3894"

# Build LOOM dataset from previously downloaded OSM data
python osm_trails_to_loom.py --build

# Download and build in one command
python osm_trails_to_loom.py --download --build --bbox "37.7749,-122.4194,37.8049,-122.3894"

# Filter trails by difficulty level
python osm_trails_to_loom.py --build --difficulty "easy"

# Filter trails by type
python osm_trails_to_loom.py --build --type "footway"

# Show trail statistics
python osm_trails_to_loom.py --stats
```

### Command-line Options

| Option | Description |
|---|---|
| `--download` | Download OSM data from Overpass API |
| `--build` | Build LOOM JSON dataset from OSM data |
| `--bbox` | Bounding box coordinates: `min_lat,min_lon,max_lat,max_lon` (required with `--download`) |
| `--difficulty` | Filter trails by difficulty level |
| `--type` | Filter trails by trail type |
| `--stats` | Display statistics about downloaded trails |

### Output Files

| File | Description |
|---|---|
| `osm_data.xml` | Raw OSM data downloaded from Overpass API |
| `circuit_trails_loom.json` | Converted trail data in GeoJSON FeatureCollection format |
| `osm_trails_to_loom.log` | Application logs |