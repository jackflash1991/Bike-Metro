"""Tests for osm_trails_to_loom.py"""

import json
import os
import tempfile
import pytest

from osm_trails_to_loom import OSMTrailDownloader


# --- Sample XML fixtures -------------------------------------------------- #

# 'out body geom;' style: <nd> elements carry inline lat/lon attributes
SAMPLE_XML_INLINE_GEOM = """\
<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <way id="101">
    <nd ref="1" lat="39.95" lon="-75.16"/>
    <nd ref="2" lat="39.96" lon="-75.15"/>
    <nd ref="3" lat="39.97" lon="-75.14"/>
    <tag k="highway" v="path"/>
    <tag k="name" v="Schuylkill River Trail"/>
    <tag k="difficulty" v="easy"/>
  </way>
  <way id="102">
    <nd ref="4" lat="40.00" lon="-75.10"/>
    <nd ref="5" lat="40.01" lon="-75.09"/>
    <tag k="highway" v="footway"/>
  </way>
  <way id="103">
    <nd ref="6" lat="40.05" lon="-75.05"/>
    <tag k="highway" v="track"/>
    <tag k="name" v="Single Node Trail"/>
  </way>
</osm>
"""

# 'out body; >;' style: standalone <node> elements
SAMPLE_XML_STANDALONE_NODES = """\
<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="10" lat="39.95" lon="-75.16"/>
  <node id="11" lat="39.96" lon="-75.15"/>
  <way id="201">
    <nd ref="10"/>
    <nd ref="11"/>
    <tag k="highway" v="track"/>
    <tag k="name" v="Wissahickon Trail"/>
    <tag k="difficulty" v="moderate"/>
  </way>
</osm>
"""


# --- Helpers -------------------------------------------------------------- #

def _write_tmp_xml(content):
    """Write XML content to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".xml")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


# --- Tests ---------------------------------------------------------------- #

class TestParseBbox:
    def test_valid_bbox(self):
        dl = OSMTrailDownloader()
        result = dl.parse_bbox("39.9,-75.3,40.1,-75.0")
        assert result == (39.9, -75.3, 40.1, -75.0)

    def test_invalid_bbox_too_few_values(self):
        dl = OSMTrailDownloader()
        with pytest.raises(ValueError):
            dl.parse_bbox("39.9,-75.3,40.1")

    def test_invalid_bbox_non_numeric(self):
        dl = OSMTrailDownloader()
        with pytest.raises(ValueError):
            dl.parse_bbox("a,b,c,d")


class TestParseInlineGeom:
    """Tests for XML where <nd> elements have inline lat/lon (out body geom)."""

    def setup_method(self):
        self.xml_path = _write_tmp_xml(SAMPLE_XML_INLINE_GEOM)
        self.dl = OSMTrailDownloader()

    def teardown_method(self):
        os.unlink(self.xml_path)

    def test_parses_named_trail(self):
        assert self.dl.parse_osm_xml(self.xml_path) is True
        names = [t["name"] for t in self.dl.trails]
        assert "Schuylkill River Trail" in names

    def test_unnamed_trail_gets_default_name(self):
        self.dl.parse_osm_xml(self.xml_path)
        unnamed = [t for t in self.dl.trails if t["name"].startswith("Unnamed trail")]
        assert len(unnamed) == 1
        assert unnamed[0]["id"] == "102"

    def test_single_node_way_skipped(self):
        """A way with only one coordinate should not produce a trail."""
        self.dl.parse_osm_xml(self.xml_path)
        ids = [t["id"] for t in self.dl.trails]
        assert "103" not in ids

    def test_coordinates_order(self):
        self.dl.parse_osm_xml(self.xml_path)
        trail = next(t for t in self.dl.trails if t["id"] == "101")
        assert trail["coordinates"] == [
            (39.95, -75.16),
            (39.96, -75.15),
            (39.97, -75.14),
        ]

    def test_trail_count(self):
        self.dl.parse_osm_xml(self.xml_path)
        # way 101 (named, 3 coords) + way 102 (unnamed, 2 coords) = 2
        # way 103 has only 1 coord -> skipped
        assert len(self.dl.trails) == 2


class TestParseStandaloneNodes:
    """Tests for XML where standalone <node> elements provide coordinates."""

    def setup_method(self):
        self.xml_path = _write_tmp_xml(SAMPLE_XML_STANDALONE_NODES)
        self.dl = OSMTrailDownloader()

    def teardown_method(self):
        os.unlink(self.xml_path)

    def test_parses_trail(self):
        assert self.dl.parse_osm_xml(self.xml_path) is True
        assert len(self.dl.trails) == 1
        assert self.dl.trails[0]["name"] == "Wissahickon Trail"

    def test_coordinates_from_node_dict(self):
        self.dl.parse_osm_xml(self.xml_path)
        assert self.dl.trails[0]["coordinates"] == [
            (39.95, -75.16),
            (39.96, -75.15),
        ]


class TestBuildLoomJson:
    def setup_method(self):
        self.xml_path = _write_tmp_xml(SAMPLE_XML_INLINE_GEOM)
        self.dl = OSMTrailDownloader()
        self.dl.parse_osm_xml(self.xml_path)
        self.out_fd, self.out_path = tempfile.mkstemp(suffix=".json")
        os.close(self.out_fd)

    def teardown_method(self):
        os.unlink(self.xml_path)
        if os.path.exists(self.out_path):
            os.unlink(self.out_path)

    def test_output_is_valid_geojson(self):
        assert self.dl.build_loom_json(self.out_path) is True
        with open(self.out_path) as f:
            data = json.load(f)
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2

    def test_geojson_coordinates_are_lon_lat(self):
        """GeoJSON spec requires [lon, lat] order."""
        self.dl.build_loom_json(self.out_path)
        with open(self.out_path) as f:
            data = json.load(f)
        trail_101 = next(
            f for f in data["features"] if f["properties"]["id"] == "101"
        )
        first_coord = trail_101["geometry"]["coordinates"][0]
        # lon should be negative (Philadelphia area), lat ~39.95
        assert first_coord == [-75.16, 39.95]

    def test_build_with_no_trails_returns_false(self):
        empty_dl = OSMTrailDownloader()
        assert empty_dl.build_loom_json(self.out_path) is False


class TestFilterTrails:
    def setup_method(self):
        self.xml_path = _write_tmp_xml(SAMPLE_XML_INLINE_GEOM)
        self.dl = OSMTrailDownloader()
        self.dl.parse_osm_xml(self.xml_path)

    def teardown_method(self):
        os.unlink(self.xml_path)

    def test_filter_by_difficulty(self):
        filtered = self.dl.filter_trails(difficulty="easy")
        assert len(filtered) == 1
        assert filtered[0]["name"] == "Schuylkill River Trail"

    def test_filter_by_type(self):
        filtered = self.dl.filter_trails(trail_type="footway")
        assert len(filtered) == 1
        assert filtered[0]["id"] == "102"

    def test_filter_no_match(self):
        filtered = self.dl.filter_trails(difficulty="expert")
        assert len(filtered) == 0


class TestHaversine:
    def test_known_distance(self):
        """Philadelphia City Hall to the Art Museum is roughly 2 km."""
        dl = OSMTrailDownloader()
        dist = dl._haversine_km(39.9524, -75.1636, 39.9656, -75.1810)
        assert 1.5 < dist < 2.5

    def test_same_point_is_zero(self):
        dl = OSMTrailDownloader()
        assert dl._haversine_km(40.0, -75.0, 40.0, -75.0) == 0.0


class TestStatistics:
    def setup_method(self):
        self.xml_path = _write_tmp_xml(SAMPLE_XML_INLINE_GEOM)
        self.dl = OSMTrailDownloader()
        self.dl.parse_osm_xml(self.xml_path)

    def teardown_method(self):
        os.unlink(self.xml_path)

    def test_stats_structure(self):
        stats = self.dl.get_statistics()
        assert "total_trails" in stats
        assert "trail_types" in stats
        assert "difficulties" in stats
        assert "total_length_km" in stats

    def test_total_trails(self):
        stats = self.dl.get_statistics()
        assert stats["total_trails"] == 2

    def test_total_length_is_positive_km(self):
        stats = self.dl.get_statistics()
        assert stats["total_length_km"] > 0

    def test_empty_trails(self):
        empty_dl = OSMTrailDownloader()
        assert empty_dl.get_statistics() == {}


class TestOverpassQuery:
    def test_query_contains_bbox(self):
        dl = OSMTrailDownloader()
        query = dl.build_overpass_query((39.9, -75.3, 40.1, -75.0))
        assert "39.9" in query
        assert "-75.3" in query
        assert "40.1" in query
        assert "-75.0" in query

    def test_query_requests_xml(self):
        dl = OSMTrailDownloader()
        query = dl.build_overpass_query((0, 0, 1, 1))
        assert "[out:xml]" in query


class TestParseErrors:
    def test_missing_file(self):
        dl = OSMTrailDownloader()
        assert dl.parse_osm_xml("/nonexistent/path.xml") is False

    def test_malformed_xml(self):
        path = _write_tmp_xml("<not-valid-xml")
        dl = OSMTrailDownloader()
        result = dl.parse_osm_xml(path)
        os.unlink(path)
        assert result is False
