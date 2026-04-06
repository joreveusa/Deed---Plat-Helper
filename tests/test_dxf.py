"""
tests/test_dxf.py
==================
Unit tests for helpers/dxf.py — DXF boundary drawing generation.

Run with:  py -m pytest tests/test_dxf.py -v
"""

import pytest
import tempfile
import shutil
from pathlib import Path

ezdxf = pytest.importorskip("ezdxf", reason="ezdxf not installed — skipping DXF tests")

from helpers.dxf import generate_boundary_dxf


@pytest.fixture
def temp_dir():
    """Create a temp directory for DXF output, cleaned up after test."""
    d = tempfile.mkdtemp(prefix="test_dxf_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestGenerateBoundaryDxf:
    """Test DXF generation with simple parcel data."""

    def _make_square_calls(self):
        """4 calls that form a 100ft × 100ft square."""
        return [
            {"type": "straight", "bearing_label": "N 0° E",  "azimuth_deg": 0,   "distance": 100},
            {"type": "straight", "bearing_label": "S 90° E", "azimuth_deg": 90,  "distance": 100},
            {"type": "straight", "bearing_label": "S 0° W",  "azimuth_deg": 180, "distance": 100},
            {"type": "straight", "bearing_label": "N 90° W", "azimuth_deg": 270, "distance": 100},
        ]

    def test_creates_dxf_file(self, temp_dir):
        parcels = [{"label": "Client", "calls": self._make_square_calls()}]
        path, errs = generate_boundary_dxf(parcels, temp_dir, 9999, "Test, Client", "BDY")
        assert Path(path).exists()
        assert path.endswith(".dxf")

    def test_returns_closure_errors(self, temp_dir):
        parcels = [{"label": "Client", "calls": self._make_square_calls()}]
        _, errs = generate_boundary_dxf(parcels, temp_dir, 9999, "Test, Client", "BDY")
        assert len(errs) == 1
        assert "label" in errs[0]
        assert "error" in errs[0]
        # Square should close well (< 0.01 ft error)
        assert errs[0]["error"] < 0.01

    def test_multiple_parcels(self, temp_dir):
        parcels = [
            {"label": "Client",   "calls": self._make_square_calls(), "layer": "CLIENT"},
            {"label": "Adjoiner", "calls": self._make_square_calls(), "layer": "ADJOINERS",
             "start_x": 200, "start_y": 0},
        ]
        path, errs = generate_boundary_dxf(parcels, temp_dir, 9999, "Test, Client", "BDY")
        assert Path(path).exists()
        assert len(errs) == 2

    def test_empty_parcels_still_produces_file(self, temp_dir):
        """A call with empty parcels should still create a valid DXF (with info block only)."""
        parcels = [{"label": "Empty", "calls": []}]
        path, errs = generate_boundary_dxf(parcels, temp_dir, 9999, "Test, Client", "BDY")
        assert Path(path).exists()
        assert len(errs) == 0  # no calls → no closure error entry

    def test_dxf_options_no_labels(self, temp_dir):
        parcels = [{"label": "Client", "calls": self._make_square_calls()}]
        path, errs = generate_boundary_dxf(
            parcels, temp_dir, 9999, "Test, Client", "BDY",
            options={"draw_labels": False, "draw_endpoints": True}
        )
        assert Path(path).exists()

    def test_filename_contains_job_number(self, temp_dir):
        parcels = [{"label": "Client", "calls": self._make_square_calls()}]
        path, _ = generate_boundary_dxf(parcels, temp_dir, 2938, "Garza, Veronica", "BDY")
        assert "2938" in Path(path).name
        assert "Garza" in Path(path).name

    def test_closure_error_for_open_polygon(self, temp_dir):
        """3 calls that don't close — should report a significant closure error."""
        calls = [
            {"type": "straight", "bearing_label": "N 0° E",  "azimuth_deg": 0,   "distance": 100},
            {"type": "straight", "bearing_label": "S 90° E", "azimuth_deg": 90,  "distance": 100},
            {"type": "straight", "bearing_label": "S 0° W",  "azimuth_deg": 180, "distance": 50},
        ]
        parcels = [{"label": "Open", "calls": calls}]
        _, errs = generate_boundary_dxf(parcels, temp_dir, 9999, "Test, Open", "BDY")
        assert errs[0]["error"] > 50  # clearly not closed
