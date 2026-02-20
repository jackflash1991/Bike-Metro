import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from osm_trails_to_loom import OSMTrailDownloader


SAMPLE_OSM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="37.7749" lon="-122.4194"/>
  <node id="2" lat="37.7759" lon="-122.4184"/>
  <node id="3" lat="37.7769" lon="-122.4174"/>
  <node id="4" lat="37.7779" lon="-122.4164"/>
  <way id="100">
    <nd ref="1"/>
    <nd ref="2"/>
    <nd ref="3"/>
    <tag k="name" v="Test Trail"/>
    <tag k="highway" v="path"/>
    <tag k="difficulty" v="easy"/>
  </way>
  <way id="101">
    <nd ref="2"/>
    <nd ref="3"/>
    <nd ref="4"/>
    <tag k="name" v="Hard Route"/>
    <tag k="highway" v="track"/>
    <tag k="difficulty" v="hard"/>
  </way>
  <way id="102">
    <nd ref="1"/>
    <nd ref="3"/>
    <tag k="highway" v="footway"/>
  </way>
</osm>
"""


class TestParseBbox(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()

    def test_valid_bbox(self):
        result = self.downloader.parse_bbox("37.7749,-122.4194,37.8049,-122.3894")
        self.assertEqual(result, (37.7749, -122.4194, 37.8049, -122.3894))

    def test_bbox_wrong_count(self):
        with self.assertRaises(ValueError):
            self.downloader.parse_bbox("37.7749,-122.4194,37.8049")

    def test_bbox_non_numeric(self):
        with self.assertRaises(ValueError):
            self.downloader.parse_bbox("abc,def,ghi,jkl")


class TestBuildOverpassQuery(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()

    def test_query_contains_bbox(self):
        query = self.downloader.build_overpass_query((37.0, -122.0, 38.0, -121.0))
        self.assertIn("37.0", query)
        self.assertIn("-122.0", query)
        self.assertIn("38.0", query)
        self.assertIn("-121.0", query)

    def test_query_includes_trail_types(self):
        query = self.downloader.build_overpass_query((37.0, -122.0, 38.0, -121.0))
        self.assertIn('"highway"="path"', query)
        self.assertIn('"highway"="track"', query)
        self.assertIn('"highway"="footway"', query)
        self.assertIn('"route"="hiking"', query)
        self.assertIn('"route"="bicycle"', query)

    def test_query_requests_xml(self):
        query = self.downloader.build_overpass_query((37.0, -122.0, 38.0, -121.0))
        self.assertIn("[out:xml]", query)


class TestParseOsmXml(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()
        self.tmpdir = tempfile.mkdtemp()
        self.xml_path = os.path.join(self.tmpdir, "test_osm.xml")
        with open(self.xml_path, "w") as f:
            f.write(SAMPLE_OSM_XML)

    def tearDown(self):
        if os.path.exists(self.xml_path):
            os.remove(self.xml_path)
        os.rmdir(self.tmpdir)

    def test_parses_named_trails(self):
        result = self.downloader.parse_osm_xml(self.xml_path)
        self.assertTrue(result)
        # Only ways with a 'name' tag are included (way 102 has no name)
        self.assertEqual(len(self.downloader.trails), 2)

    def test_trail_attributes(self):
        self.downloader.parse_osm_xml(self.xml_path)
        trail = self.downloader.trails[0]
        self.assertEqual(trail["id"], "100")
        self.assertEqual(trail["name"], "Test Trail")
        self.assertEqual(trail["type"], "path")
        self.assertEqual(trail["difficulty"], "easy")
        self.assertEqual(len(trail["coordinates"]), 3)

    def test_missing_file(self):
        result = self.downloader.parse_osm_xml("/nonexistent/path.xml")
        self.assertFalse(result)

    def test_invalid_xml(self):
        bad_xml_path = os.path.join(self.tmpdir, "bad.xml")
        with open(bad_xml_path, "w") as f:
            f.write("<<<not valid xml>>>")
        result = self.downloader.parse_osm_xml(bad_xml_path)
        self.assertFalse(result)
        os.remove(bad_xml_path)


class TestFilterTrails(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()
        self.tmpdir = tempfile.mkdtemp()
        xml_path = os.path.join(self.tmpdir, "test_osm.xml")
        with open(xml_path, "w") as f:
            f.write(SAMPLE_OSM_XML)
        self.downloader.parse_osm_xml(xml_path)
        os.remove(xml_path)
        os.rmdir(self.tmpdir)

    def test_filter_by_difficulty(self):
        result = self.downloader.filter_trails(difficulty="easy")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Test Trail")

    def test_filter_by_type(self):
        result = self.downloader.filter_trails(trail_type="track")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Hard Route")

    def test_filter_no_match(self):
        result = self.downloader.filter_trails(difficulty="moderate")
        self.assertEqual(len(result), 0)

    def test_filter_none_returns_all(self):
        result = self.downloader.filter_trails()
        self.assertEqual(len(result), 2)


class TestBuildLoomJson(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()
        self.tmpdir = tempfile.mkdtemp()
        xml_path = os.path.join(self.tmpdir, "test_osm.xml")
        with open(xml_path, "w") as f:
            f.write(SAMPLE_OSM_XML)
        self.downloader.parse_osm_xml(xml_path)
        os.remove(xml_path)
        self.output_path = os.path.join(self.tmpdir, "output.json")

    def tearDown(self):
        if os.path.exists(self.output_path):
            os.remove(self.output_path)
        if os.path.exists(self.tmpdir):
            os.rmdir(self.tmpdir)

    def test_produces_valid_geojson(self):
        result = self.downloader.build_loom_json(self.output_path)
        self.assertTrue(result)

        with open(self.output_path) as f:
            data = json.load(f)

        self.assertEqual(data["type"], "FeatureCollection")
        self.assertIn("features", data)
        self.assertIn("timestamp", data)
        self.assertEqual(len(data["features"]), 2)

    def test_feature_structure(self):
        self.downloader.build_loom_json(self.output_path)
        with open(self.output_path) as f:
            data = json.load(f)

        feature = data["features"][0]
        self.assertEqual(feature["type"], "Feature")
        self.assertEqual(feature["geometry"]["type"], "LineString")
        self.assertIn("coordinates", feature["geometry"])
        self.assertIn("id", feature["properties"])
        self.assertIn("name", feature["properties"])
        self.assertIn("type", feature["properties"])
        self.assertIn("difficulty", feature["properties"])

    def test_coordinates_are_lon_lat(self):
        """GeoJSON coordinates must be [lon, lat], not [lat, lon]."""
        self.downloader.build_loom_json(self.output_path)
        with open(self.output_path) as f:
            data = json.load(f)

        coords = data["features"][0]["geometry"]["coordinates"]
        # Original node 1: lat=37.7749, lon=-122.4194
        # GeoJSON should be [lon, lat] = [-122.4194, 37.7749]
        self.assertAlmostEqual(coords[0][0], -122.4194)
        self.assertAlmostEqual(coords[0][1], 37.7749)

    def test_no_trails_returns_false(self):
        empty_downloader = OSMTrailDownloader()
        result = empty_downloader.build_loom_json(self.output_path)
        self.assertFalse(result)


class TestGetStatistics(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()
        self.tmpdir = tempfile.mkdtemp()
        xml_path = os.path.join(self.tmpdir, "test_osm.xml")
        with open(xml_path, "w") as f:
            f.write(SAMPLE_OSM_XML)
        self.downloader.parse_osm_xml(xml_path)
        os.remove(xml_path)
        os.rmdir(self.tmpdir)

    def test_statistics_keys(self):
        stats = self.downloader.get_statistics()
        self.assertIn("total_trails", stats)
        self.assertIn("trail_types", stats)
        self.assertIn("difficulties", stats)
        self.assertIn("total_length_degrees", stats)

    def test_statistics_values(self):
        stats = self.downloader.get_statistics()
        self.assertEqual(stats["total_trails"], 2)
        self.assertEqual(stats["trail_types"]["path"], 1)
        self.assertEqual(stats["trail_types"]["track"], 1)
        self.assertEqual(stats["difficulties"]["easy"], 1)
        self.assertEqual(stats["difficulties"]["hard"], 1)
        self.assertGreater(stats["total_length_degrees"], 0)

    def test_empty_trails(self):
        empty_downloader = OSMTrailDownloader()
        stats = empty_downloader.get_statistics()
        self.assertEqual(stats, {})


class TestDownloadOsmData(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()
        self.downloader.data_file = os.path.join(tempfile.mkdtemp(), "osm_data.xml")

    def tearDown(self):
        parent = os.path.dirname(self.downloader.data_file)
        if os.path.exists(self.downloader.data_file):
            os.remove(self.downloader.data_file)
        if os.path.exists(parent):
            os.rmdir(parent)

    @patch("osm_trails_to_loom.requests.post")
    def test_successful_download(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_OSM_XML
        mock_post.return_value = mock_response

        result = self.downloader.download_osm_data("37.7749,-122.4194,37.8049,-122.3894")
        self.assertTrue(result)
        self.assertTrue(os.path.exists(self.downloader.data_file))

    @patch("osm_trails_to_loom.requests.post")
    def test_rate_limited_then_success(self, mock_post):
        rate_limited = MagicMock()
        rate_limited.status_code = 429

        success = MagicMock()
        success.status_code = 200
        success.text = SAMPLE_OSM_XML

        mock_post.side_effect = [rate_limited, success]
        self.downloader.retry_delay = 0  # speed up test

        result = self.downloader.download_osm_data("37.7749,-122.4194,37.8049,-122.3894")
        self.assertTrue(result)

    @patch("osm_trails_to_loom.requests.post")
    def test_all_retries_fail(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response
        self.downloader.retry_delay = 0

        result = self.downloader.download_osm_data("37.7749,-122.4194,37.8049,-122.3894")
        self.assertFalse(result)


class TestValidateLoomJson(unittest.TestCase):
    def setUp(self):
        self.downloader = OSMTrailDownloader()

    def _valid_feature(self):
        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[-122.4194, 37.7749], [-122.4184, 37.7759]],
            },
            "properties": {"id": "1", "name": "Trail", "type": "path"},
        }

    def test_valid_data(self):
        data = {
            "type": "FeatureCollection",
            "features": [self._valid_feature()],
        }
        errors = self.downloader.validate_loom_json(data)
        self.assertEqual(errors, [])

    def test_wrong_root_type(self):
        data = {"type": "Feature", "features": []}
        errors = self.downloader.validate_loom_json(data)
        self.assertTrue(any("FeatureCollection" in e for e in errors))

    def test_missing_features(self):
        data = {"type": "FeatureCollection"}
        errors = self.downloader.validate_loom_json(data)
        self.assertTrue(any("features" in e for e in errors))

    def test_invalid_geometry_type(self):
        feature = self._valid_feature()
        feature["geometry"]["type"] = "Point"
        data = {"type": "FeatureCollection", "features": [feature]}
        errors = self.downloader.validate_loom_json(data)
        self.assertTrue(any("LineString" in e for e in errors))

    def test_too_few_coordinates(self):
        feature = self._valid_feature()
        feature["geometry"]["coordinates"] = [[-122.0, 37.0]]
        data = {"type": "FeatureCollection", "features": [feature]}
        errors = self.downloader.validate_loom_json(data)
        self.assertTrue(any("at least 2" in e for e in errors))

    def test_out_of_range_lon(self):
        feature = self._valid_feature()
        feature["geometry"]["coordinates"] = [[-200.0, 37.0], [-122.0, 37.0]]
        data = {"type": "FeatureCollection", "features": [feature]}
        errors = self.downloader.validate_loom_json(data)
        self.assertTrue(any("lon" in e for e in errors))

    def test_missing_required_property(self):
        feature = self._valid_feature()
        del feature["properties"]["name"]
        data = {"type": "FeatureCollection", "features": [feature]}
        errors = self.downloader.validate_loom_json(data)
        self.assertTrue(any("name" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
