"""
User profile management for multi-user support.

Each profile is stored as a separate JSON file in the `profiles/` directory.
Profiles hold per-user settings: display name, credentials, last session,
and theme preference.  The server-level config.json retains only global
settings (survey_drive, server_name).
"""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional

_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def _ensure_dir():
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _profile_path(profile_id: str) -> Path:
    # Sanitise to prevent path traversal
    safe = re.sub(r'[^a-zA-Z0-9_-]', '', profile_id)
    return _PROFILES_DIR / f"{safe}.json"


def _blank_profile(display_name: str, profile_id: str | None = None) -> dict:
    return {
        "id":               profile_id or uuid.uuid4().hex[:12],
        "display_name":     display_name,
        "firstnm_user":     "",
        "firstnm_pass":     "",
        "last_session":     None,
        "theme":            "dark",
        "created":          __import__("datetime").datetime.now().isoformat(),
    }


# ── CRUD ───────────────────────────────────────────────────────────────────────

def list_profiles() -> list[dict]:
    """Return all profiles (sorted by display_name)."""
    _ensure_dir()
    profiles = []
    for fp in _PROFILES_DIR.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            profiles.append(data)
        except Exception:
            pass
    profiles.sort(key=lambda p: p.get("display_name", "").lower())
    return profiles


def get_profile(profile_id: str) -> Optional[dict]:
    p = _profile_path(profile_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def save_profile(profile: dict) -> dict:
    """Create or update a profile.  Returns the saved profile dict."""
    _ensure_dir()
    pid = profile.get("id")
    if not pid:
        pid = uuid.uuid4().hex[:12]
        profile["id"] = pid
    fp = _profile_path(pid)
    fp.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return profile


def create_profile(display_name: str) -> dict:
    """Create a new profile with the given display name."""
    profile = _blank_profile(display_name)
    return save_profile(profile)


def delete_profile(profile_id: str) -> bool:
    p = _profile_path(profile_id)
    if p.exists():
        p.unlink()
        return True
    return False


def update_profile_field(profile_id: str, field: str, value) -> Optional[dict]:
    """Update a single field on a profile.  Returns updated profile or None."""
    profile = get_profile(profile_id)
    if profile is None:
        return None
    profile[field] = value
    return save_profile(profile)


# ── Migration helper ───────────────────────────────────────────────────────────

def migrate_from_config(config: dict) -> dict:
    """
    If there are no profiles yet but config.json has legacy user data,
    create a single 'Default' profile from it and return the profile.
    """
    existing = list_profiles()
    if existing:
        return existing[0]  # already migrated

    display_name = "Default User"
    profile = _blank_profile(display_name)
    profile["firstnm_user"] = config.get("firstnm_user", "")
    profile["firstnm_pass"] = config.get("firstnm_pass", "")
    profile["last_session"]  = config.get("last_session")
    return save_profile(profile)
