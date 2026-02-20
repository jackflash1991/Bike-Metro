import argparse
import requests
import os
import json
import logging
import time
from xml.etree import ElementTree as ET
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('osm_trails_to_loom.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class OSMTrailDownloader:
    """Downloads and processes OpenStreetMap trail data."""
    
    def __init__(self, overpass_url='http://overpass-api.de/api/interpreter'):
        self.overpass_url = overpass_url
        self.osm_data = None
        self.trails = []
        self.data_file = 'osm_data.xml'
        self.json_file = 'circuit_trails_loom.json'
        self.max_retries = 3
        self.retry_delay = 5
        
    def parse_bbox(self, bbox_str):
        """Parse bounding box string to tuple."""
        try:
            parts = bbox_str.split(',')
            if len(parts) != 4:
                raise ValueError("Bbox must have 4 values: min_lat,min_lon,max_lat,max_lon")
            return tuple(float(p) for p in parts)
        except ValueError as e:
            logger.error(f"Invalid bounding box format: {e}")
            raise
    
    def build_overpass_query(self, bbox):
        """Build Overpass API query for trails and paths."""
        min_lat, min_lon, max_lat, max_lon = bbox
        
        query = f"""
        [out:xml];
        (
          way["highway"="path"]["access"~"yes|public|permissive"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["highway"="track"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["highway"="footway"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["tourism"="hiking"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["route"="hiking"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["route"="bicycle"]({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out body geom;
        """
        return query
    
    def download_osm_data(self, bbox_str):
        """Download OSM data with retry logic and progress tracking."""
        bbox = self.parse_bbox(bbox_str)
        query = self.build_overpass_query(bbox)
        
        logger.info(f"Starting OSM data download for bbox: {bbox}")
        
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Attempt {attempt + 1}/{self.max_retries}")
                response = requests.post(
                    self.overpass_url,
                    data={'data': query},
                    timeout=300
                )
                
                if response.status_code == 200:
                    logger.info("OSM data downloaded successfully")
                    self.osm_data = response.text
                    self._save_osm_data()
                    return True
                elif response.status_code == 429:
                    logger.warning("Rate limited by Overpass API, retrying...")
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    logger.error(f"Error downloading data: {response.status_code}")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout on attempt {attempt + 1}, retrying...")
                time.sleep(self.retry_delay)
            except requests.exceptions.RequestException as e:
                logger.error(f"Network error: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
        
        logger.error("Failed to download OSM data after all retries")
        return False
    
    def _save_osm_data(self):
        """Save OSM data to file."""
        try:
            with open(self.data_file, 'w') as f:
                f.write(self.osm_data)
            logger.info(f"OSM data saved to {self.data_file}")
        except IOError as e:
            logger.error(f"Error saving OSM data: {e}")
            raise
    
    def parse_osm_xml(self, file_path=None):
        """Parse OSM XML data and extract trail information."""
        if file_path is None:
            file_path = self.data_file
        
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False
        
        try:
            logger.info(f"Parsing OSM data from {file_path}")
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            self.trails = []
            node_dict = {}
            
            for node in root.findall('node'):
                node_id = node.get('id')
                lat = float(node.get('lat'))
                lon = float(node.get('lon'))
                node_dict[node_id] = (lat, lon)
            
            way_count = 0
            for way in root.findall('way'):
                way_id = way.get('id')
                tags = {tag.get('k'): tag.get('v') for tag in way.findall('tag')}
                
                if 'name' not in tags:
                    continue
                
                nd_refs = [nd.get('ref') for nd in way.findall('nd')]
                coords = []
                for nd_ref in nd_refs:
                    if nd_ref in node_dict:
                        coords.append(node_dict[nd_ref])
                
                if len(coords) > 1:
                    trail = {
                        'id': way_id,
                        'name': tags.get('name'),
                        'type': tags.get('highway', tags.get('tourism', 'unknown')),
                        'difficulty': tags.get('difficulty', 'unknown'),
                        'coordinates': coords,
                        'tags': tags
                    }
                    self.trails.append(trail)
                    way_count += 1
            
            logger.info(f"Parsed {way_count} trails from OSM data")
            return True
            
        except ET.ParseError as e:
            logger.error(f"Error parsing XML: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during parsing: {e}")
            return False
    
    def filter_trails(self, difficulty=None, trail_type=None):
        """Filter trails by difficulty or type."""
        filtered = self.trails
        
        if difficulty:
            filtered = [t for t in filtered if t['difficulty'] == difficulty]
            logger.info(f"Filtered to {len(filtered)} trails with difficulty '{difficulty}'")
        
        if trail_type:
            filtered = [t for t in filtered if t['type'] == trail_type]
            logger.info(f"Filtered to {len(filtered)} trails of type '{trail_type}'")
        
        return filtered
    
    def build_loom_json(self, output_file=None):
        """Convert trails to LOOM JSON format."""
        if output_file is None:
            output_file = self.json_file
        
        if not self.trails:
            logger.error("No trails to build. Please parse OSM data first.")
            return False
        
        try:
            logger.info(f"Building LOOM JSON with {len(self.trails)} trails")
            
            loom_data = {
                'type': 'FeatureCollection',
                'timestamp': datetime.now().isoformat(),
                'features': []
            }
            
            for trail in self.trails:
                feature = {
                    'type': 'Feature',
                    'geometry': {
                        'type': 'LineString',
                        'coordinates': [[lon, lat] for lat, lon in trail['coordinates']]
                    },
                    'properties': {
                        'id': trail['id'],
                        'name': trail['name'],
                        'type': trail['type'],
                        'difficulty': trail['difficulty'],
                        'tags': trail['tags']
                    }
                }
                loom_data['features'].append(feature)
            
            with open(output_file, 'w') as f:
                json.dump(loom_data, f, indent=2)
            
            logger.info(f"LOOM JSON saved to {output_file}")
            return True
            
        except IOError as e:
            logger.error(f"Error saving LOOM JSON: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error building LOOM JSON: {e}")
            return False
    
    def get_statistics(self):
        """Return statistics about downloaded trails."""
        if not self.trails:
            return {}
        
        trail_types = {}
        difficulties = {}
        
        for trail in self.trails:
            trail_type = trail['type']
            difficulty = trail['difficulty']
            
            trail_types[trail_type] = trail_types.get(trail_type, 0) + 1
            difficulties[difficulty] = difficulties.get(difficulty, 0) + 1
        
        total_length = sum(
            LineString(trail['coordinates']).length 
            for trail in self.trails
        )
        
        return {
            'total_trails': len(self.trails),
            'trail_types': trail_types,
            'difficulties': difficulties,
            'total_length_degrees': total_length
        }

def main():
    parser = argparse.ArgumentParser(
        description='Download OSM trail data and convert to LOOM format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python osm_trails_to_loom.py --download --bbox "37.7749,-122.4194,37.8049,-122.3894"
  python osm_trails_to_loom.py --build
  python osm_trails_to_loom.py --download --build --bbox "37.7749,-122.4194,37.8049,-122.3894"
  python osm_trails_to_loom.py --build --difficulty "easy"
        """
    )
    
    parser.add_argument('--download', action='store_true', help='Download OSM data')
    parser.add_argument('--build', action='store_true', help='Build LOOM dataset from OSM data')
    parser.add_argument('--bbox', type=str, help='Bounding box: min_lat,min_lon,max_lat,max_lon (required for --download)')
    parser.add_argument('--difficulty', type=str, help='Filter trails by difficulty level')
    parser.add_argument('--type', type=str, help='Filter trails by type')
    parser.add_argument('--stats', action='store_true', help='Display statistics about downloaded trails')
    
    args = parser.parse_args()
    
    downloader = OSMTrailDownloader()
    
    if args.download:
        if not args.bbox:
            logger.error("--bbox is required for --download")
            return False
        
        if not downloader.download_osm_data(args.bbox):
            return False
    
    if args.build:
        if not downloader.parse_osm_xml():
            return False
        
        if args.difficulty or args.type:
            downloader.trails = downloader.filter_trails(
                difficulty=args.difficulty,
                trail_type=args.type
            )
        
        if not downloader.build_loom_json():
            return False
        
        if args.stats:
            stats = downloader.get_statistics()
            logger.info(f"Trail Statistics: {json.dumps(stats, indent=2)}")
    
    if args.stats and not args.build:
        if os.path.exists(downloader.data_file):
            downloader.parse_osm_xml()
            stats = downloader.get_statistics()
            logger.info(f"Trail Statistics: {json.dumps(stats, indent=2)}")
    
    logger.info("Pipeline completed successfully")
    return True

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)