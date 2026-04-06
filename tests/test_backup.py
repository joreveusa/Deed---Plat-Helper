"""
tests/test_backup.py — Unit tests for the rotating users.json backup system.
"""
import json
import time
import pytest
from unittest.mock import patch

import helpers.backup as backup_mod


@pytest.fixture(autouse=True)
def patch_paths(tmp_path):
    """Redirect all backup AND auth paths to the same temp directory."""
    with (
        patch.object(backup_mod, "_USERS_FILE",  tmp_path / "users.json"),
        patch.object(backup_mod, "_BACKUP_DIR",  tmp_path / "backups"),
        patch.object(backup_mod, "_MAX_BACKUPS", 5),
    ):
        yield


def _write_users(content=None):
    backup_mod._USERS_FILE.write_text(
        json.dumps(content or {"u1": {"email": "a@b.com"}}), encoding="utf-8"
    )


# ── backup_users_file ─────────────────────────────────────────────────────────

class TestBackupUsersFile:
    def test_returns_none_if_no_source(self):
        result = backup_mod.backup_users_file()
        assert result is None

    def test_creates_backup_file(self):
        _write_users()
        path = backup_mod.backup_users_file()
        assert path is not None
        assert path.exists()
        assert path.suffix == ".json"

    def test_backup_contains_source_content(self):
        data = {"u1": {"email": "test@example.com"}}
        _write_users(data)
        path = backup_mod.backup_users_file()
        restored = json.loads(path.read_text(encoding="utf-8"))
        assert restored == data

    def test_backup_dir_is_created(self):
        _write_users()
        backup_mod.backup_users_file()
        assert backup_mod._BACKUP_DIR.exists()

    def test_prunes_to_max_backups(self):
        _write_users()
        for _ in range(8):   # create 8 with MAX=5
            backup_mod.backup_users_file()
        backups = list(backup_mod._BACKUP_DIR.glob("users_*.json"))
        assert len(backups) <= 5


# ── list_backups ──────────────────────────────────────────────────────────────

class TestListBackups:
    def test_empty_before_any_backup(self):
        assert backup_mod.list_backups() == []

    def test_returns_metadata(self):
        _write_users()
        backup_mod.backup_users_file()
        items = backup_mod.list_backups()
        assert len(items) >= 1
        assert "filename" in items[0]
        assert "size_kb" in items[0]
        assert "created" in items[0]

    def test_newest_first(self):
        _write_users()
        backup_mod.backup_users_file()
        time.sleep(1.1)   # guarantee a different second in the timestamp filename
        backup_mod.backup_users_file()
        items = backup_mod.list_backups()
        assert len(items) >= 2
        assert items[0]["filename"] > items[1]["filename"]


# ── restore_backup ────────────────────────────────────────────────────────────

class TestRestoreBackup:
    def test_restore_overwrites_current(self):
        original = {"u1": {"email": "original@test.com"}}
        _write_users(original)
        path = backup_mod.backup_users_file()

        time.sleep(1.1)  # ensure safety-backup inside restore gets a different timestamp
        # Overwrite the source with corrupted data
        backup_mod._USERS_FILE.write_text(
            '{"u1": {"email": "corrupted@test.com"}}', encoding="utf-8"
        )

        backup_mod.restore_backup(path.name)
        restored = json.loads(backup_mod._USERS_FILE.read_text(encoding="utf-8"))
        assert restored == original

    def test_restore_raises_on_missing_backup(self):
        with pytest.raises(FileNotFoundError):
            backup_mod.restore_backup("users_nonexistent.json")

    def test_restore_round_trip(self):
        """Restore returns the data that was present when the backup was taken."""
        _write_users({"version": "before"})
        path = backup_mod.backup_users_file()

        time.sleep(1.1)  # ensure safety-backup inside restore gets a different timestamp
        backup_mod._USERS_FILE.write_text('{"version": "after"}', encoding="utf-8")
        backup_mod.restore_backup(path.name)

        result = json.loads(backup_mod._USERS_FILE.read_text(encoding="utf-8"))
        assert result == {"version": "before"}
