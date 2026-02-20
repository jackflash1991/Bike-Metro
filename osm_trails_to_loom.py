import argparse
import requests
import os

# Function to download OSM data

def download_osm_data(bbox, overpass_url='http://overpass-api.de/api/interpreter'):
    query = f'[out:xml];(node({bbox});<;);
out body;'
    response = requests.post(overpass_url, data={'data': query})

    if response.status_code != 200:
        print(f'Error downloading data: {response.status_code}')
        return None
    return response.text

# Function to build dataset

def build_dataset(data):
    # Dummy implementation for building dataset
    print('Building dataset...')
    # Add your processing logic here

# Command-line argument parsing

def main():
    parser = argparse.ArgumentParser(description='Download OSM data.')
    parser.add_argument('--download', action='store_true', help='Download OSM data')
    parser.add_argument('--build', action='store_true', help='Build dataset from downloaded OSM data')
    parser.add_argument('--bbox', type=str, required=True, help='Bounding box for the download in the format: min_lat,min_lon,max_lat,max_lon')

    args = parser.parse_args()

    if args.download:
        print('Downloading OSM data...')
        data = download_osm_data(args.bbox)
        if data:
            print('Data downloaded successfully.')
            # Save data to file (optional)
            with open('osm_data.xml', 'w') as f:
                f.write(data)

    if args.build:
        print('Building dataset...')
        if os.path.exists('osm_data.xml'):
            with open('osm_data.xml', 'r') as f:
                data = f.read()
                build_dataset(data)
        else:
            print('No data to build. Please download first.')

if __name__ == '__main__':
    main()