"""
scripts/batch_ocr_warmup.py — Pre-OCR unprocessed cabinet PDFs.

Uses multiprocessing (default 4 workers) for dramatically faster throughput
on multi-core machines.  Each worker independently OCRs one PDF and writes
the cache file — no locking needed since cache filenames are unique per PDF.

Usage:
    python scripts/batch_ocr_warmup.py <cabinet_path> [max_per_cabinet] [workers]

Examples:
    python scripts/batch_ocr_warmup.py "J:/.../Cabs A-B- C-D - E"         # 50/cab, 4 workers
    python scripts/batch_ocr_warmup.py "J:/.../Cabs A-B- C-D - E" 9999    # all PDFs, 4 workers
    python scripts/batch_ocr_warmup.py "J:/.../Cabs A-B- C-D - E" 9999 6  # all PDFs, 6 workers
"""

import os
import sys
import time
import signal
import multiprocessing as mp
from pathlib import Path

sys.path.insert(0, ".")


# ── Worker function (runs in child process) ─────────────────────────────────
def _ocr_one_pdf(args: tuple) -> tuple:
    """Process a single PDF.  Returns (status, letter, fname, error_msg).

    status: 'ok' | 'skip_short' | 'error'
    """
    fpath, cache_file_str, letter, fname = args

    # Late imports inside worker so each child process loads its own copies
    from helpers.pdf_extract import setup_tesseract, extract_pdf_text, _warmup_cache_path

    try:
        setup_tesseract()
        # Check cache first (avoids re-OCRing if warmup was interrupted and restarted)
        cache_file = Path(_warmup_cache_path(fpath))
        if cache_file.exists() and cache_file.stat().st_size > 0:
            return ("skip_cached", letter, fname, "")
        text, method = extract_pdf_text(fpath)
        if text and len(text.strip()) > 20:
            Path(cache_file_str).write_text(text[:5000], encoding="utf-8")
            return ("ok", letter, fname, "")
        return ("skip_short", letter, fname, f"only {len((text or '').strip())} chars")
    except Exception as e:
        return ("error", letter, fname, str(e))


def main():
    if len(sys.argv) < 2:
        print("Usage: batch_ocr_warmup.py <cabinet_path> [max_per_cabinet] [workers]")
        sys.exit(1)

    cabinet_path = sys.argv[1]
    max_per_cabinet = int(sys.argv[2]) if len(sys.argv) >= 3 else 50
    num_workers = int(sys.argv[3]) if len(sys.argv) >= 4 else 4

    # Clamp workers to CPU count
    cpu_count = os.cpu_count() or 4
    num_workers = min(num_workers, cpu_count)

    # Read cabinet index directly from JSON (avoids module-level caching issues)
    import json
    from helpers.cabinet import CABINET_FOLDERS

    index_path = Path("data") / "cabinet_index.json"
    if not index_path.exists():
        print(f"[batch] ERROR: Cabinet index not found at {index_path}", flush=True)
        print(f"[batch] Run: python scripts/batch_rebuild_cabinet_index.py <cabinet_path>", flush=True)
        sys.exit(1)

    print(f"[batch] Loading index from {index_path} ({index_path.stat().st_size // 1024}KB)...", flush=True)
    with open(index_path, "r", encoding="utf-8") as f:
        raw_index = json.load(f)
    print(f"[batch] Index loaded: {len(raw_index)} cabinets", flush=True)

    ocr_cache_dir = Path("data") / "ocr_cache"
    ocr_cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Build work queue ────────────────────────────────────────────────────
    work_items: list[tuple] = []
    total_cached = 0
    total_skipped_missing = 0

    for letter, folder in CABINET_FOLDERS.items():
        cab_dir = Path(cabinet_path) / folder
        if not cab_dir.exists():
            print(f"  Cabinet {letter}: folder not found, skipping", flush=True)
            continue

        entry = raw_index.get(letter, {})
        files = entry.get("files", [])
        if not files:
            print(f"  Cabinet {letter}: no indexed files, skipping", flush=True)
            continue

        queued_this_cab = 0
        for row in files:
            fname, display, fname_norm, name_norm, doc_num, fpath = row
            if not fpath or not os.path.exists(fpath):
                total_skipped_missing += 1
                continue

            # Use shared cache path function — same key as extract_pdf_text()
            from helpers.pdf_extract import _warmup_cache_path
            cache_file = _warmup_cache_path(fpath)
            if cache_file.exists():
                total_cached += 1
                continue

            if queued_this_cab >= max_per_cabinet:
                break

            work_items.append((fpath, str(cache_file), letter, fname))
            queued_this_cab += 1

        print(f"  Cabinet {letter}: {queued_this_cab} PDFs queued for OCR", flush=True)

    print(flush=True)
    print(f"[batch] Summary: {len(work_items)} PDFs to process, "
          f"{total_cached} already cached, "
          f"{total_skipped_missing} missing files", flush=True)
    print(f"[batch] Using {num_workers} parallel workers", flush=True)
    print(flush=True)

    if not work_items:
        print("[batch] Nothing to do — all PDFs are already cached!", flush=True)
        return

    # ── Process with multiprocessing pool ───────────────────────────────────
    total_ok = 0
    total_short = 0
    total_errors = 0
    error_log: list[str] = []
    start_time = time.time()

    # Graceful Ctrl+C handling
    original_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        with mp.Pool(processes=num_workers) as pool:
            signal.signal(signal.SIGINT, original_sigint)

            for i, result in enumerate(pool.imap_unordered(_ocr_one_pdf, work_items), 1):
                status, letter, fname, err_msg = result

                if status == "ok":
                    total_ok += 1
                elif status == "skip_short":
                    total_short += 1
                elif status == "error":
                    total_errors += 1
                    error_log.append(f"  [{letter}] {fname}: {err_msg}")

                # Progress reporting every 10 files
                if i % 10 == 0 or i == len(work_items):
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    remaining = (len(work_items) - i) / rate if rate > 0 else 0

                    elapsed_min = int(elapsed // 60)
                    remaining_min = int(remaining // 60)

                    print(
                        f"  [{i}/{len(work_items)}] "
                        f"{total_ok} ok, {total_errors} err | "
                        f"{elapsed_min}m elapsed, ~{remaining_min}m remaining | "
                        f"{rate:.1f} PDFs/sec",
                        flush=True
                    )

    except KeyboardInterrupt:
        print("\n[batch] Interrupted by user — saving progress...", flush=True)
    except Exception as e:
        print(f"[batch] Pool error: {e}", flush=True)

    # ── Final report ────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    elapsed_min = int(elapsed // 60)
    elapsed_sec = int(elapsed % 60)

    print(flush=True)
    print(f"[batch] ═══════════════════════════════════════════════════", flush=True)
    print(f"[batch] OCR WARM-UP COMPLETE", flush=True)
    print(f"[batch]   Processed: {total_ok} new PDFs cached", flush=True)
    print(f"[batch]   Skipped:   {total_cached} already cached", flush=True)
    print(f"[batch]   Too short: {total_short} (< 20 chars extracted)", flush=True)
    print(f"[batch]   Errors:    {total_errors}", flush=True)
    print(f"[batch]   Time:      {elapsed_min}m {elapsed_sec}s", flush=True)
    print(f"[batch]   Workers:   {num_workers}", flush=True)
    print(f"[batch] ═══════════════════════════════════════════════════", flush=True)

    if error_log:
        print(f"\n[batch] Error details ({len(error_log)} files):", flush=True)
        for line in error_log[:50]:  # Cap at 50 to avoid spam
            print(line, flush=True)
        if len(error_log) > 50:
            print(f"  ... and {len(error_log) - 50} more", flush=True)


if __name__ == "__main__":
    # Required for Windows multiprocessing
    mp.freeze_support()
    main()
