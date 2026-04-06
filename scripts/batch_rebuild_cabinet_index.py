"""
scripts/batch_rebuild_cabinet_index.py — Rebuild all cabinet indexes.

Usage:
    python scripts/batch_rebuild_cabinet_index.py <cabinet_path>
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from helpers.cabinet import (
    CABINET_FOLDERS, _init_index_path, _scan_cabinet_dir,
    _INDEX, _INDEX_LOCK, _save_index_to_disk,
)


def main():
    if len(sys.argv) < 2:
        print("Usage: batch_rebuild_cabinet_index.py <cabinet_path>")
        sys.exit(1)

    cabinet_path = sys.argv[1]
    _init_index_path(".")

    t0 = time.time()
    total_files = 0

    for letter, folder in CABINET_FOLDERS.items():
        cab_dir = Path(cabinet_path) / folder
        if not cab_dir.exists():
            print(f"  [skip] Cabinet {letter}: folder not found ({folder})")
            continue
        print(f"  Scanning Cabinet {letter} ({folder})...", flush=True)
        try:
            mtime = cab_dir.stat().st_mtime
            files = _scan_cabinet_dir(cab_dir)
            with _INDEX_LOCK:
                _INDEX[letter] = {"mtime": mtime, "files": files}
            count = len(files)
            total_files += count
            print(f"  [OK] Cabinet {letter}: {count} PDFs indexed")
        except Exception as e:
            print(f"  [ERR] Cabinet {letter}: {e}", file=sys.stderr)

    _save_index_to_disk()
    elapsed = round(time.time() - t0, 1)
    print(f"[batch] Cabinet index rebuild complete: {total_files} total PDFs in {elapsed}s")


if __name__ == "__main__":
    main()
