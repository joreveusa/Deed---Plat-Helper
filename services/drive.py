"""
services/drive.py — Removable-drive detection and Survey Data path resolution.

The Survey Data folder lives on a removable drive whose letter changes
between computers.  This module scans all available drive letters at startup
and caches the result.
"""

import os
from pathlib import Path

from services.config import load_config

_SURVEY_RELATIVE = os.path.join("AI DATA CENTER", "Survey Data")
_CABINET_RELATIVE = os.path.join(
    "AI DATA CENTER", "Survey Data",
    "00 COUNTY CLERK SCANS Cabs A-B- C-D - E",
)

_detected_drive: str | None = None  # e.g. "F"


def detect_survey_drive(force: bool = False) -> str | None:
    """Scan all drive letters for the Survey Data folder.

    Returns the drive letter (e.g. 'F') or None if not found.
    Caches the result; pass *force=True* to rescan.
    """
    global _detected_drive

    # ── Dev mode: DEV_DATA_DIR env var bypasses drive scanning ──────────────
    dev_dir = os.environ.get("DEV_DATA_DIR", "").strip()
    if dev_dir and Path(dev_dir).exists():
        _detected_drive = "__dev__"
        if not force:
            return _detected_drive
        print(f"[drive] DEV MODE — using local data: {dev_dir}", flush=True)
        return _detected_drive

    if _detected_drive and not force:
        # Verify cached drive is still present
        if _detected_drive == "__dev__" or Path(f"{_detected_drive}:\\").exists():
            return _detected_drive

    # Try config override first
    cfg = load_config()
    override = cfg.get("survey_drive", "").strip().upper()
    if override and len(override) == 1 and Path(f"{override}:\\").exists():
        candidate = Path(f"{override}:\\") / _SURVEY_RELATIVE
        if candidate.exists():
            _detected_drive = override
            return _detected_drive

    # Scan all drive letters
    import string
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if not root.exists():
            continue
        candidate = root / _SURVEY_RELATIVE
        if candidate.exists():
            _detected_drive = letter
            print(f"[drive] Survey Data found on {letter}:\\", flush=True)
            return _detected_drive

    _detected_drive = None
    print("[drive] Survey Data NOT found on any drive.", flush=True)
    return None


def get_survey_data_path() -> str:
    """Return the current Survey Data path, auto-detecting the drive."""
    drive = detect_survey_drive()
    if drive == "__dev__":
        dev_dir = os.environ.get("DEV_DATA_DIR", "").strip()
        return dev_dir if dev_dir else ""
    if drive:
        return str(Path(f"{drive}:\\") / _SURVEY_RELATIVE)
    return ""  # drive not found — caller should check for empty string and warn user


def get_cabinet_path() -> str:
    """Return the current Cabinet path, auto-detecting the drive."""
    drive = detect_survey_drive()
    if drive:
        return str(Path(f"{drive}:\\") / _CABINET_RELATIVE)
    return ""  # drive not found — caller should check for empty string


# Kick off detection at startup (non-blocking — just sets module-level cache)
try:
    detect_survey_drive()
except Exception as e:
    print(f"[warn] drive detection at startup failed: {e}", flush=True)
