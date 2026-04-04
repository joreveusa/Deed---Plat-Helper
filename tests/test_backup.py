"""
tests/test_backup.py — Unit tests for the rotating users.json backup system.
"""
import json
import pytest

import helpers.backup as backup_mod


@pytest.fixture(autouse=True)
def patch_paths(monkeypatch, tmp_path):
    """Redirect all backup paths to a temp directory."""
    monkeypatch.setattr(backup_mod, "_USERS_FILE",  tmp_path / "users.json")
    monkeypatch.setattr(backup_mod, "_BACKUP_DIR",  tmp_path / "backups")
    monkeypatch.setattr(backup_mod, "_MAX_BACKUPS", 3)
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
        for _ in range(5):  # create 5 with MAX=3
            backup_mod.backup_users_file()
        backups = list(backup_mod._BACKUP_DIR.glob("users_*.json"))
        assert len(backups) <= 3


# ── list_backups ──────────────────────────────────────────────────────────────

class TestListBackups:
    def test_empty_before_any_backup(self):
        assert backup_mod.list_backups() == []

    def test_returns_metadata(self):
        _write_users()
        backup_mod.backup_users_file()
        items = backup_mod.list_backups()
        assert len(items) == 1
        assert "filename" in items[0]
        assert "size_kb" in items[0]
        assert "created" in items[0]

    def test_newest_first(self):
        _write_users()
        backup_mod.backup_users_file()
        import time; time.sleep(0.05)
        backup_mod.backup_users_file()
        items = backup_mod.list_backups()
        assert items[0]["filename"] > items[1]["filename"]


# ── restore_backup ────────────────────────────────────────────────────────────

class TestRestoreBackup:
    def test_restore_overwrites_current(self):
        original = {"u1": {"email": "original@test.com"}}
        _write_users(original)
        path = backup_mod.backup_users_file()

        # Overwrite with different data
        backup_mod._USERS_FILE.write_text(
            json.dumps({"u1": {"email": "corrupted@test.com"}}), encoding="utf-8"
        )

        backup_mod.restore_backup(path.name)
        restored = json.loads(backup_mod._USERS_FILE.read_text(encoding="utf-8"))
        assert restored == original

    def test_restore_raises_on_missing_backup(self):
        with pytest.raises(FileNotFoundError):
            backup_mod.restore_backup("users_nonexistent.json")

    def test_restore_creates_safety_backup(self):
        _write_users()
        path = backup_mod.backup_users_file()
        backup_mod.restore_backup(path.name)
        backups = list(backup_mod._BACKUP_DIR.glob("users_*.json"))
        assert len(backups) >= 2   # original backup + safety backup made before restore
