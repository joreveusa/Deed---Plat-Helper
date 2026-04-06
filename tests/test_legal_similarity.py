"""
tests/test_legal_similarity.py — Unit tests for legal description similarity search.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers.legal_similarity import (
    compute_similarity, search_similar_descriptions,
    _tokenize_legal, _extract_trs_fingerprints, _extract_cab_refs,
    _extract_names, _extract_lot_block_tract, _jaccard,
)


class TestTokenization:
    def test_basic_tokenization(self):
        tokens = _tokenize_legal("Thence along the boundary of Smith Property")
        assert "smith" in tokens
        assert "property" in tokens
        # Stop words excluded
        assert "the" not in tokens
        assert "along" not in tokens

    def test_empty_input(self):
        assert _tokenize_legal("") == set()
        assert _tokenize_legal(None) == set()

    def test_short_tokens_excluded(self):
        tokens = _tokenize_legal("Go to a set pin")
        assert "go" not in tokens  # 2 chars
        assert "pin" in tokens     # 3 chars


class TestTRSExtraction:
    def test_full_trs(self):
        fps = _extract_trs_fingerprints("T26N R13E Sec 12")
        assert "T26N-R13E-S12" in fps

    def test_trs_without_section(self):
        fps = _extract_trs_fingerprints("T26N R13E")
        assert "T26N-R13E" in fps

    def test_trs_with_dots(self):
        fps = _extract_trs_fingerprints("T.26N. R.13E. Section 5")
        assert any("T26N" in fp and "R13E" in fp for fp in fps)

    def test_multiple_trs(self):
        fps = _extract_trs_fingerprints("T26N R13E Sec 12 and T25N R12E Sec 1")
        assert len(fps) >= 2

    def test_no_trs(self):
        fps = _extract_trs_fingerprints("Lot 3 Block 7 of Sunset Subdivision")
        assert fps == []


class TestCabRefExtraction:
    def test_standard_cab_ref(self):
        refs = _extract_cab_refs("Cabinet C-191A")
        assert "C-191A" in refs

    def test_abbreviated(self):
        refs = _extract_cab_refs("Cab. B-45")
        assert "B-45" in refs

    def test_multiple_refs(self):
        refs = _extract_cab_refs("Cabinet C-191 and Cab A-100")
        assert "C-191" in refs
        assert "A-100" in refs

    def test_no_refs(self):
        refs = _extract_cab_refs("No cabinet references here")
        assert len(refs) == 0


class TestNameExtraction:
    def test_lastname_firstname(self):
        names = _extract_names("SMITH, JOHN conveyed to JONES, MARY")
        assert "smith" in names
        assert "jones" in names

    def test_lands_of_pattern(self):
        names = _extract_names("Lands of Martinez")
        assert "martinez" in names

    def test_empty(self):
        assert _extract_names("") == set()


class TestLotBlockTract:
    def test_lot_block(self):
        refs = _extract_lot_block_tract("Lot 3, Block 7")
        assert "lot-3" in refs
        assert "block-7" in refs

    def test_tract(self):
        refs = _extract_lot_block_tract("Tract 2A")
        assert "tract-2A" in refs

    def test_lot_only(self):
        refs = _extract_lot_block_tract("Lot 14")
        assert "lot-14" in refs

    def test_no_refs(self):
        refs = _extract_lot_block_tract("Metes and bounds description")
        assert len(refs) == 0


class TestJaccard:
    def test_identical(self):
        s = {"a", "b", "c"}
        assert _jaccard(s, s) == 1.0

    def test_disjoint(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        score = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert 0.4 < score < 0.6  # 2/4 = 0.5

    def test_empty(self):
        assert _jaccard(set(), {"a"}) == 0.0


class TestSimilarityScoring:
    def test_identical_descriptions(self):
        text = "T26N R13E Sec 12, Lot 3 Block 7, Cabinet C-191, Lands of SMITH"
        result = compute_similarity(text, text)
        assert result["score"] > 80

    def test_same_trs_different_content(self):
        a = "T26N R13E Sec 12, some property description"
        b = "T26N R13E Sec 12, completely different text"
        result = compute_similarity(a, b)
        # Should have high TRS match but lower text similarity
        assert result["components"]["trs_match"] == 100.0
        assert result["score"] > 30

    def test_same_township_different_section(self):
        a = "T26N R13E Sec 12"
        b = "T26N R13E Sec 5"
        result = compute_similarity(a, b)
        # Same T/R but different section = partial credit
        assert result["components"]["trs_match"] == 30.0

    def test_completely_different(self):
        a = "T26N R13E Sec 12, Lands of SMITH, Cabinet C-191"
        b = "T20N R10E Sec 1, Lands of JONES, Cabinet A-50"
        result = compute_similarity(a, b)
        assert result["score"] < 30

    def test_shared_cabinet_ref(self):
        a = "Property recorded in Cabinet C-191"
        b = "See also Cabinet C-191 for plat"
        result = compute_similarity(a, b)
        assert result["components"]["cab_overlap"] == 100.0
        assert "C-191" in result["shared_cabs"]

    def test_shared_names(self):
        a = "MARTINEZ, JOSE conveyed to SMITH"
        b = "Lands of Martinez boundary"
        result = compute_similarity(a, b)
        assert result["components"]["name_overlap"] > 0
        assert "martinez" in result["shared_names"]


class TestSearchSimilar:
    def _make_parcels(self):
        return [
            {
                "upc": "001", "owner": "SMITH, JOHN",
                "plat": "Cab C-191", "book": "100", "page": "200",
                "arcgis": {"legal_description": "T26N R13E Sec 12 Lot 3", "trs": "T26N R13E Sec 12"},
            },
            {
                "upc": "002", "owner": "JONES, MARY",
                "plat": "Cab B-45", "book": "101", "page": "201",
                "arcgis": {"legal_description": "T20N R10E Sec 1", "trs": "T20N R10E Sec 1"},
            },
            {
                "upc": "003", "owner": "MARTINEZ, JOSE",
                "plat": "Cab C-191", "book": "102", "page": "202",
                "arcgis": {"legal_description": "T26N R13E Sec 12 Lot 4", "trs": "T26N R13E Sec 12"},
            },
        ]

    def test_finds_similar_by_trs(self):
        parcels = self._make_parcels()
        results = search_similar_descriptions(
            "T26N R13E Sec 12 property description",
            parcels, min_score=10
        )
        # Should find parcels 001 and 003 (same TRS)
        upcs = [r["upc"] for r in results]
        assert "001" in upcs
        assert "003" in upcs

    def test_min_score_filtering(self):
        parcels = self._make_parcels()
        results = search_similar_descriptions(
            "T26N R13E Sec 12 Cabinet C-191 Lands of SMITH",
            parcels, min_score=50
        )
        # Only high-similarity matches should survive
        assert all(r["similarity"]["score"] >= 50 for r in results)

    def test_empty_query(self):
        results = search_similar_descriptions("", self._make_parcels())
        assert results == []

    def test_empty_index(self):
        results = search_similar_descriptions("some text", [])
        assert results == []

    def test_results_sorted_by_score(self):
        parcels = self._make_parcels()
        results = search_similar_descriptions(
            "T26N R13E Sec 12 Cabinet C-191",
            parcels, min_score=5
        )
        if len(results) >= 2:
            scores = [r["similarity"]["score"] for r in results]
            assert scores == sorted(scores, reverse=True)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
