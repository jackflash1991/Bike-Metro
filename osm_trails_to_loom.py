import requests
import os

# Define the function to download OSM data

def download_osm_data(area_name):
    base_url = 'http://download.openstreetmap.org/openstreetmap/"
    file_name = f'{area_name}.osm'
    url = f'{base_url}{file_name}'
    try:
        response = requests.get(url)
        response.raise_for_status()
        with open(file_name, 'wb') as file:
            file.write(response.content)
        print(f'Downloaded OSM data for {area_name}')
    except requests.exceptions.HTTPError as http_err:
        print(f'HTTP error occurred: {http_err}')
    except Exception as err:
        print(f'An error occurred: {err}')

# Example usage
if __name__ == '__main__':
    area_name = 'san-francisco'
    download_osm_data(area_name)