"""
helpers/backup.py — Rotating backup system for users.json.

Creates a timestamped backup before each write, keeps the last N copies.
Backups are stored in a sibling 'backups/' directory.

Usage (called from helpers/auth.py):
    from helpers.backup import backup_users_file
    backup_users_file()   # call before overwriting users.json
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_HERE        = Path(__file__).parent.parent   # repo root
_USERS_FILE  = _HERE / "users.json"
_BACKUP_DIR  = _HERE / "backups"
_MAX_BACKUPS = 10          # keep the last N backup files


def backup_users_file() -> Path | None:
    """Copy users.json → backups/users_YYYYMMDD_HHMMSS.json.

    Returns the backup path on success, None if the source doesn't exist.
    Old backups beyond _MAX_BACKUPS are pruned automatically.
    """
    import helpers.backup as _m
    if not _m._USERS_FILE.exists():
        return None

    _m._BACKUP_DIR.mkdir(exist_ok=True)

    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = _m._BACKUP_DIR / f"users_{ts}.json"
    try:
        shutil.copy2(_m._USERS_FILE, dest)
        log.debug(f"[backup] Created {dest.name}")
        _prune_old_backups()
        return dest
    except Exception as e:
        log.error(f"[backup] Failed to create backup: {e}")
        return None


def _prune_old_backups() -> None:
    """Delete oldest backups beyond _MAX_BACKUPS."""
    import helpers.backup as _m
    backups = sorted(_m._BACKUP_DIR.glob("users_*.json"))
    excess  = len(backups) - _m._MAX_BACKUPS
    for old in backups[:excess]:
        try:
            old.unlink()
            log.debug(f"[backup] Pruned {old.name}")
        except Exception as e:
            log.warning(f"[backup] Could not prune {old.name}: {e}")


def list_backups() -> list[dict]:
    """Return metadata for all available backups (newest first)."""
    import helpers.backup as _m
    if not _m._BACKUP_DIR.exists():
        return []
    result = []
    for p in sorted(_m._BACKUP_DIR.glob("users_*.json"), reverse=True):
        stat = p.stat()
        result.append({
            "filename": p.name,
            "path":     str(p),
            "size_kb":  round(stat.st_size / 1024, 1),
            "created":  datetime.utcfromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S UTC"),
        })
    return result


def restore_backup(filename: str) -> bool:
    """Restore users.json from a named backup file.

    Creates a backup of the current file before restoring.
    Returns True on success.
    """
    import helpers.backup as _m
    src = _m._BACKUP_DIR / filename
    if not src.exists():
        raise FileNotFoundError(f"Backup not found: {filename}")

    # Safety: back up current state before restoring
    backup_users_file()

    try:
        shutil.copy2(src, _m._USERS_FILE)
        log.info(f"[backup] Restored users.json from {filename}")
        return True
    except Exception as e:
        log.error(f"[backup] Restore failed: {e}")
        raise
