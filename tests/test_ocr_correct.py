"""
tests/test_ocr_correct.py — Unit tests for OCR post-processing module.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers.ocr_correct import clean_survey_text, correction_stats


class TestDegreeSymbolRecovery:
    """Tests for _fix_degree_symbols via clean_survey_text."""

    def test_spaced_bearing(self):
        """N 45 30 00 E  →  N 45°30'00" E"""
        result = clean_survey_text("N 45 30 00 E")
        assert "45°" in result
        assert "30'" in result

    def test_dash_separated_bearing(self):
        """N45-30-00E  →  N 45°30'00" E"""
        result = clean_survey_text("N45-30-00E")
        assert "45°" in result
        assert "N" in result
        assert "E" in result

    def test_star_degree(self):
        """S 89*15'30" W  →  S 89°15'30" W"""
        result = clean_survey_text('S 89*15\'30" W')
        assert "89°" in result

    def test_lowercase_o_degree(self):
        """N 45o30'00" E  →  N 45°30'00" E"""
        result = clean_survey_text('N 45o30\'00" E')
        assert "45°" in result


class TestDirectionLetters:
    """Tests for _fix_direction_letters."""

    def test_dollar_to_s(self):
        """$ → S in bearing context."""
        result = clean_survey_text("$ 45°30'00\" E")
        assert result.startswith("S")

    def test_five_to_s_bearing(self):
        """5 → S when followed by degrees."""
        result = clean_survey_text("5 45°30' E")
        assert "S" in result


class TestNumericOCR:
    """Tests for _fix_numeric_ocr."""

    def test_lowercase_l_to_one(self):
        """'l' between digits → '1'."""
        result = clean_survey_text("1l5.50 feet")
        assert "115.50" in result

    def test_uppercase_o_to_zero(self):
        """'O' between digits → '0'."""
        result = clean_survey_text("1O5.50 feet")
        assert "105.50" in result

    def test_leading_l_in_distance(self):
        """Leading 'l' before digits in distance → '1'."""
        result = clean_survey_text("l25.50 feet")
        assert "125.50" in result


class TestSurveyTerms:
    """Tests for survey term spell correction."""

    def test_thance_to_thence(self):
        result = clean_survey_text("thance along the boundary")
        assert "thence" in result

    def test_begining_to_beginning(self):
        result = clean_survey_text("point of begining")
        assert "beginning" in result

    def test_teet_to_feet(self):
        result = clean_survey_text("125.50 teet")
        assert "feet" in result

    def test_fect_to_feet(self):
        result = clean_survey_text("200.00 fect")
        assert "feet" in result

    def test_rebar_variations(self):
        result = clean_survey_text("found rchar cap")
        assert "rebar" in result

    def test_lron_to_iron(self):
        result = clean_survey_text("found lron pin")
        assert "iron" in result

    def test_accquia_to_acequia(self):
        result = clean_survey_text("along the accquia")
        assert "acequia" in result

    def test_cabinct_to_cabinet(self):
        result = clean_survey_text("cabinct B-123")
        assert "cabinet" in result

    def test_case_preservation(self):
        """Capitalized terms should stay capitalized."""
        result = clean_survey_text("Thance along the boundary")
        assert "Thence" in result


class TestUnitNormalization:
    """Tests for _fix_unit_variations."""

    def test_ft_dot_to_feet(self):
        result = clean_survey_text("125.50 ft.")
        assert "feet" in result

    def test_ft_to_feet(self):
        result = clean_survey_text("125.50 ft to a point")
        assert "feet" in result


class TestCorrectionStats:
    """Tests for the correction_stats helper."""

    def test_identical_text(self):
        stats = correction_stats("hello", "hello")
        assert stats["changed"] is False

    def test_changed_text(self):
        original = "thance 125 teet"
        corrected = clean_survey_text(original)
        stats = correction_stats(original, corrected)
        assert stats["changed"] is True
        assert stats["corrections"] > 0


class TestIntegration:
    """Integration tests — full pipeline on realistic OCR output."""

    def test_bearing_line_cleanup(self):
        """Full bearing line with multiple OCR errors."""
        ocr = "thance $ 45 30 00 E a distancc of l25.50 teet to a rchar"
        result = clean_survey_text(ocr)
        assert "thence" in result
        assert "S" in result
        assert "45°" in result
        assert "125.50" in result
        assert "feet" in result
        assert "rebar" in result

    def test_preserves_clean_text(self):
        """Clean text should pass through unchanged."""
        clean = 'thence N 45°30\'00" E a distance of 125.50 feet to a rebar cap'
        result = clean_survey_text(clean)
        assert result == clean

    def test_empty_input(self):
        assert clean_survey_text("") == ""
        assert clean_survey_text("abc") == "abc"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
