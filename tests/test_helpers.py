"""
tests/test_helpers.py
=====================
Basic unit tests for the extracted helper modules.
Run with:  py -m pytest tests/ -v
"""

import math

# ── helpers.metes_bounds ──────────────────────────────────────────────────────

from helpers.metes_bounds import (
    parse_metes_bounds, calls_to_coords, extract_trs,
    detect_monuments, classify_description_type,
    shoelace_area, has_pob, _bearing_to_azimuth,
)


class TestBearingToAzimuth:
    def test_north_east(self):
        assert _bearing_to_azimuth("N", 45, 0, 0, "E") == 45.0

    def test_south_east(self):
        assert _bearing_to_azimuth("S", 45, 0, 0, "E") == 135.0

    def test_south_west(self):
        assert _bearing_to_azimuth("S", 45, 0, 0, "W") == 225.0

    def test_north_west(self):
        assert _bearing_to_azimuth("N", 45, 0, 0, "W") == 315.0

    def test_minutes_seconds(self):
        az = _bearing_to_azimuth("N", 45, 30, 0, "E")
        assert abs(az - 45.5) < 0.001


class TestParseMetesBounds:
    def test_basic_bearing_distance(self):
        text = "S 45°30'00\" E, 125.50 feet"
        calls = parse_metes_bounds(text)
        assert len(calls) == 1
        assert calls[0]["type"] == "straight"
        assert calls[0]["distance"] == 125.50

    def test_multiple_calls(self):
        text = """
        N 0°00'00" E 100 feet,
        S 90°00'00" E 100 feet,
        S 0°00'00" W 100 feet,
        N 90°00'00" W 100 feet
        """
        calls = parse_metes_bounds(text)
        assert len(calls) == 4

    def test_empty_text(self):
        assert parse_metes_bounds("") == []
        assert parse_metes_bounds("no bearings here") == []


class TestCallsToCoords:
    def test_square_closure(self):
        """A 100ft square should close perfectly."""
        text = """
        N 0°00'00" E 100 feet,
        S 90°00'00" E 100 feet,
        S 0°00'00" W 100 feet,
        N 90°00'00" W 100 feet
        """
        calls = parse_metes_bounds(text)
        pts = calls_to_coords(calls)
        # Should close: last point ≈ first point
        assert len(pts) == 5  # start + 4 calls
        closure = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        assert closure < 0.01

    def test_empty_calls(self):
        pts = calls_to_coords([])
        assert pts == [(0.0, 0.0)]


class TestExtractTrs:
    def test_basic_trs(self):
        text = "located in T5N R5E Section 12"
        refs = extract_trs(text)
        assert len(refs) == 1
        assert refs[0]["township"] == "T5N"
        assert refs[0]["range"] == "R5E"
        assert refs[0]["section"] == "12"

    def test_multiple_trs(self):
        text = "T5N R5E S12 and T6N R4W Sec 3"
        refs = extract_trs(text)
        assert len(refs) == 2

    def test_no_trs(self):
        assert extract_trs("lot 5 block 3") == []


class TestDetectMonuments:
    def test_iron_pin(self):
        assert "iron pin" in detect_monuments("set an iron pin at the corner")

    def test_rebar(self):
        assert "rebar" in detect_monuments("found a rebar with cap")

    def test_no_monuments(self):
        assert detect_monuments("the lot is bounded by Main Street") == []


class TestClassifyDescType:
    def test_metes_and_bounds(self):
        assert classify_description_type("text", [{"type": "straight"}], []) == "metes_and_bounds"

    def test_lot_block(self):
        assert classify_description_type("lot 5 block 3 of Sunrise", [], []) == "lot_block"

    def test_tract(self):
        assert classify_description_type("Tract A of MRGCD", [], []) == "tract"

    def test_trs_only(self):
        assert classify_description_type("section 12", [], [{"trs": "T5N R5E S12"}]) == "trs_only"

    def test_unknown(self):
        assert classify_description_type("none of the above", [], []) == "unknown"


class TestShoelaceArea:
    def test_unit_square(self):
        pts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert shoelace_area(pts) == 1.0

    def test_rectangle(self):
        pts = [(0, 0), (100, 0), (100, 50), (0, 50)]
        assert shoelace_area(pts) == 5000.0

    def test_too_few_points(self):
        assert shoelace_area([(0, 0), (1, 0)]) == 0.0
        assert shoelace_area([]) == 0.0


class TestHasPob:
    def test_pob_present(self):
        assert has_pob("BEGINNING at the point of beginning of said tract")
        assert has_pob("returning to the P.O.B.")

    def test_no_pob(self):
        assert not has_pob("thence along the north line 100 feet")


# ── helpers.adjoiner ──────────────────────────────────────────────────────────

from helpers.adjoiner import parse_adjoiner_names


class TestParseAdjoinerNames:
    def test_lands_of_pattern(self):
        detail = {"Other_Legal": "bounded on the north by lands of GARCIA, JUAN"}
        results = parse_adjoiner_names(detail)
        assert len(results) >= 1
        assert any("Garcia" in r["name"] for r in results)

    def test_no_adjoiners(self):
        detail = {"Other_Legal": "lot 5 block 3"}
        results = parse_adjoiner_names(detail)
        assert results == []


# ── helpers.cabinet ───────────────────────────────────────────────────────────

from helpers.cabinet import (
    CABINET_FOLDERS, parse_cabinet_refs,
    extract_plat_name_tokens, extract_cabinet_display_name,
    extract_cabinet_doc_number,
)


class TestCabinetFolders:
    def test_all_letters_present(self):
        for letter in "ABCDEF":
            assert letter in CABINET_FOLDERS


class TestParseCabinetRefs:
    def test_long_form(self):
        detail = {"Comments": "See CAB C-191A for plat"}
        refs = parse_cabinet_refs(detail)
        assert len(refs) >= 1
        assert refs[0]["cabinet"] == "C"
        assert "191" in refs[0]["doc"]

    def test_no_refs(self):
        detail = {"Comments": "nothing special here"}
        assert parse_cabinet_refs(detail) == []


class TestExtractPlatNameTokens:
    def test_with_cab_prefix(self):
        tokens = extract_plat_name_tokens("C-191-A ADELA RAEL")
        assert "ADELA RAEL" in tokens
        assert "RAEL" in tokens

    def test_empty(self):
        assert extract_plat_name_tokens("") == []


class TestExtractCabinetDisplayName:
    def test_with_prefix(self):
        assert extract_cabinet_display_name("195554.001   Adela Rael.PDF") == "Adela Rael"

    def test_without_prefix(self):
        name = extract_cabinet_display_name("Rael Adela.PDF")
        assert "Rael" in name


class TestExtractCabinetDocNumber:
    def test_with_number(self):
        assert extract_cabinet_doc_number("195554.001   Adela Rael.PDF") == "195554"

    def test_without_number(self):
        assert extract_cabinet_doc_number("Rael Adela.PDF") == ""


# ── helpers.deed_analysis ─────────────────────────────────────────────────────

from helpers.deed_analysis import analyze_deed, isolate_legal_description


class TestAnalyzeDeed:
    def test_basic_analysis_no_pdf(self):
        detail = {
            "Grantor": "GARCIA, JUAN",
            "Grantee": "RAEL, ADELA",
            "Location": "M568-482",
            "doc_no": "12345",
            "Recorded Date": "01/15/2020",
            "Instrument Type": "Warranty Deed",
            "Other_Legal": "T5N R5E Section 12",
        }
        result = analyze_deed(detail)
        assert "score" in result
        assert "grade" in result
        assert "issues" in result
        assert "categories" in result
        assert result["score"] >= 0
        assert result["grade"] in ("good", "fair", "poor")

    def test_missing_parties(self):
        detail = {}
        result = analyze_deed(detail)
        critical_issues = [i for i in result["issues"] if i["severity"] == "critical"]
        assert len(critical_issues) >= 2  # missing grantor + grantee

    def test_self_conveyance(self):
        detail = {"Grantor": "GARCIA, JUAN", "Grantee": "GARCIA, JUAN"}
        result = analyze_deed(detail)
        warn_titles = [i["title"] for i in result["issues"] if i["severity"] == "warn"]
        assert any("self-conveyance" in t.lower() for t in warn_titles)


class TestIsolateLegalDescription:
    def test_header_extraction(self):
        text = "SOME PREFIX\nLEGAL DESCRIPTION:\nLot 5 Block 3 of Sunrise Subdivision\nIN WITNESS WHEREOF"
        result = isolate_legal_description(text)
        assert "Lot 5" in result
        assert "IN WITNESS" not in result

    def test_pob_extraction(self):
        text = "blah blah BEGINNING at a point thence N45E 100 ft to POB containing 1 acre"
        result = isolate_legal_description(text)
        assert "BEGINNING" in result

    def test_short_text_passthrough(self):
        text = "Lot 5 Block 3"
        assert isolate_legal_description(text) == text

    def test_empty(self):
        assert isolate_legal_description("") == ""
