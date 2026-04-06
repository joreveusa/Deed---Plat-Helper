"""
tests/test_pdf_extract.py
==========================
Unit tests for helpers/pdf_extract.py.

Run with:  py -m pytest tests/test_pdf_extract.py -v
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from helpers.pdf_extract import _find_tesseract, _ocr_cache_path, setup_tesseract


# ── Tesseract binary discovery ────────────────────────────────────────────────

class TestFindTesseract:
    def test_returns_string(self):
        """_find_tesseract should always return a string path."""
        result = _find_tesseract()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_default_path_is_program_files(self):
        """When no tesseract exists, should fall back to Program Files path."""
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", return_value=None):
            result = _find_tesseract()
            assert "Tesseract-OCR" in result

    def test_found_on_path(self):
        """If shutil.which finds tesseract, that path should be returned."""
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", return_value="/usr/bin/tesseract"):
            result = _find_tesseract()
            assert result == "/usr/bin/tesseract"


# ── OCR cache path ───────────────────────────────────────────────────────────

class TestOcrCachePath:
    def test_suffix_replacement(self):
        cache = _ocr_cache_path("/some/path/document.pdf")
        assert str(cache).endswith(".ocr.json")

    def test_preserves_stem(self):
        cache = _ocr_cache_path("/path/to/My Plat.pdf")
        assert "My Plat" in str(cache)

    def test_returns_path_object(self):
        cache = _ocr_cache_path("test.pdf")
        assert isinstance(cache, Path)


# ── setup_tesseract ──────────────────────────────────────────────────────────

class TestSetupTesseract:
    def test_sets_tesseract_cmd(self):
        """setup_tesseract should set pytesseract.pytesseract.tesseract_cmd."""
        pytesseract = pytest.importorskip("pytesseract", reason="pytesseract not installed")
        setup_tesseract()
        assert pytesseract.pytesseract.tesseract_cmd is not None
        assert isinstance(pytesseract.pytesseract.tesseract_cmd, str)
