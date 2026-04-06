"""
tests/test_xml_processor.py
============================
Unit tests for the xml_processor module — KML/KMZ parsing, indexing, and search.

Run with:  py -m pytest tests/ -v
"""

import pytest

from xml_processor import (
    _parse_cab_refs_from_plat,
    _compute_centroid,
    _parse_polygon_coords,
    _filter_parcels,
    _simplify_ring,
)


# ── Cabinet reference parsing from PLAT field ─────────────────────────────────

class TestParseCabRefsFromPlat:
    def test_explicit_cab_prefix(self):
        assert _parse_cab_refs_from_plat("CAB C-191A") == ["C-191A"]

    def test_explicit_cab_dot_prefix(self):
        assert _parse_cab_refs_from_plat("CAB. E-139A") == ["E-139A"]

    def test_short_form_with_suffix(self):
        refs = _parse_cab_refs_from_plat("C-191-A ADELA RAEL")
        assert "C-191A" in refs

    def test_short_form_no_suffix(self):
        assert _parse_cab_refs_from_plat("C-84") == ["C-84"]

    def test_multiple_refs(self):
        refs = _parse_cab_refs_from_plat("CAB C-191A, D-55")
        assert len(refs) == 2
        assert "C-191A" in refs
        assert "D-55" in refs

    def test_empty_string(self):
        assert _parse_cab_refs_from_plat("") == []

    def test_none_input(self):
        assert _parse_cab_refs_from_plat(None) == []

    def test_no_refs_in_text(self):
        assert _parse_cab_refs_from_plat("ADELA RAEL LOT 5 BLOCK 3") == []

    def test_deduplication(self):
        """Same ref via both CAB prefix and short form should appear once."""
        refs = _parse_cab_refs_from_plat("CAB C-191A and C-191-A")
        assert refs.count("C-191A") == 1


# ── Centroid computation ──────────────────────────────────────────────────────

class TestComputeCentroid:
    def test_single_point(self):
        result = _compute_centroid("-105.5,36.4,0")
        assert result is not None
        assert abs(result[0] - (-105.5)) < 0.0001
        assert abs(result[1] - 36.4) < 0.0001

    def test_multiple_points(self):
        coords = "-105.0,36.0,0 -106.0,37.0,0"
        result = _compute_centroid(coords)
        assert result is not None
        assert abs(result[0] - (-105.5)) < 0.0001
        assert abs(result[1] - 36.5) < 0.0001

    def test_square(self):
        coords = "-105.0,36.0,0 -106.0,36.0,0 -106.0,37.0,0 -105.0,37.0,0"
        result = _compute_centroid(coords)
        assert result is not None
        assert abs(result[0] - (-105.5)) < 0.0001
        assert abs(result[1] - 36.5) < 0.0001

    def test_empty_string(self):
        assert _compute_centroid("") is None

    def test_invalid_coords(self):
        assert _compute_centroid("not,valid") is None or _compute_centroid("abc") is None


# ── Polygon coordinate parsing ────────────────────────────────────────────────

class TestParsePolygonCoords:
    def test_simple_polygon(self):
        coords = "-105.0,36.0,0 -106.0,36.0,0 -106.0,37.0,0 -105.0,37.0,0 -105.0,36.0,0"
        result = _parse_polygon_coords(coords)
        assert result is not None
        assert len(result) == 5
        # First and last should match (closed ring)
        assert result[0] == result[-1]

    def test_too_few_points(self):
        coords = "-105.0,36.0,0 -106.0,36.0,0"
        assert _parse_polygon_coords(coords) is None

    def test_empty(self):
        assert _parse_polygon_coords("") is None

    def test_2d_coords(self):
        """KML coords without altitude component."""
        coords = "-105.0,36.0 -106.0,36.0 -106.0,37.0"
        result = _parse_polygon_coords(coords)
        assert result is not None
        assert len(result) == 3


# ── Parcel filtering ──────────────────────────────────────────────────────────

class TestFilterParcels:
    @pytest.fixture
    def sample_parcels(self):
        return [
            {"owner": "GARCIA, JUAN", "upc": "001", "book": "568", "page": "482",
             "plat": "C-191-A GARCIA", "cab_refs": ["C-191A"], "source": "test.kml"},
            {"owner": "RAEL, ADELA", "upc": "002", "book": "570", "page": "100",
             "plat": "D-55 RAEL", "cab_refs": ["D-55"], "source": "test.kml"},
            {"owner": "TORRES, MARIA", "upc": "003", "book": "568", "page": "483",
             "plat": "", "cab_refs": [], "source": "other.kml"},
        ]

    def test_owner_contains(self, sample_parcels):
        results = _filter_parcels(sample_parcels, owner="GARCIA")
        assert len(results) == 1
        assert results[0]["upc"] == "001"

    def test_owner_case_insensitive(self, sample_parcels):
        results = _filter_parcels(sample_parcels, owner="garcia")
        assert len(results) == 1

    def test_owner_begins(self, sample_parcels):
        results = _filter_parcels(sample_parcels, owner="RAE", operator="begins")
        assert len(results) == 1
        assert results[0]["upc"] == "002"

    def test_owner_exact(self, sample_parcels):
        results = _filter_parcels(sample_parcels, owner="GARCIA, JUAN", operator="exact")
        assert len(results) == 1

    def test_upc_exact(self, sample_parcels):
        results = _filter_parcels(sample_parcels, upc="002")
        assert len(results) == 1
        assert results[0]["owner"] == "RAEL, ADELA"

    def test_book_page(self, sample_parcels):
        results = _filter_parcels(sample_parcels, book="568", page="482")
        assert len(results) == 1
        assert results[0]["upc"] == "001"

    def test_book_only(self, sample_parcels):
        results = _filter_parcels(sample_parcels, book="568")
        assert len(results) == 2  # GARCIA and TORRES

    def test_cabinet_ref(self, sample_parcels):
        results = _filter_parcels(sample_parcels, cabinet_ref="C-191A")
        assert len(results) == 1
        assert results[0]["upc"] == "001"

    def test_cabinet_ref_in_plat_text(self, sample_parcels):
        """Cabinet ref that appears in plat text but not in cab_refs list."""
        results = _filter_parcels(sample_parcels, cabinet_ref="D-55")
        assert len(results) == 1
        assert results[0]["upc"] == "002"

    def test_limit(self, sample_parcels):
        results = _filter_parcels(sample_parcels, owner="", limit=2)
        assert len(results) == 2

    def test_no_match(self, sample_parcels):
        results = _filter_parcels(sample_parcels, owner="NONEXISTENT")
        assert len(results) == 0

    def test_empty_parcels(self):
        assert _filter_parcels([], owner="test") == []

    def test_combined_filters(self, sample_parcels):
        results = _filter_parcels(sample_parcels, owner="GARCIA", book="568")
        assert len(results) == 1
        assert results[0]["upc"] == "001"


# ── Ring simplification ──────────────────────────────────────────────────────

class TestSimplifyRing:
    def test_short_ring_unchanged(self):
        ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
        result = _simplify_ring(ring, max_pts=10)
        assert len(result) == 4

    def test_long_ring_simplified(self):
        """A ring with 100 points should be reduced to max_pts."""
        ring = [[i * 0.01, i * 0.01] for i in range(100)]
        result = _simplify_ring(ring, max_pts=20)
        assert len(result) <= 20
        # First and last points should be preserved
        assert result[0] == [round(ring[0][0], 4), round(ring[0][1], 4)]
        assert result[-1] == [round(ring[-1][0], 4), round(ring[-1][1], 4)]

    def test_coords_rounded(self):
        ring = [[0.123456789, 1.987654321]]
        result = _simplify_ring(ring, max_pts=10)
        assert result[0] == [0.1235, 1.9877]
