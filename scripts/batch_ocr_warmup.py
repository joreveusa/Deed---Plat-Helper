"""
scripts/batch_ocr_warmup.py — Pre-OCR unprocessed cabinet PDFs.

Usage:
    python scripts/batch_ocr_warmup.py <cabinet_path> [max_per_cabinet]
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
from helpers.pdf_extract import setup_tesseract, extract_pdf_text
from helpers.cabinet import CABINET_FOLDERS, _init_index_path, _INDEX, _INDEX_LOCK


def main():
    if len(sys.argv) < 2:
        print("Usage: batch_ocr_warmup.py <cabinet_path> [max_per_cabinet]")
        sys.exit(1)

    cabinet_path = sys.argv[1]
    max_per_cabinet = int(sys.argv[2]) if len(sys.argv) >= 3 else 50

    setup_tesseract()
    _init_index_path(".")

    ocr_cache_dir = Path("data") / "ocr_cache"
    ocr_cache_dir.mkdir(parents=True, exist_ok=True)

    total_processed = 0
    total_cached = 0
    total_errors = 0

    for letter, folder in CABINET_FOLDERS.items():
        cab_dir = Path(cabinet_path) / folder
        if not cab_dir.exists():
            continue

        # Get indexed files for this cabinet
        with _INDEX_LOCK:
            entry = _INDEX.get(letter, {})
        files = entry.get("files", [])
        if not files:
            continue

        processed_this_cab = 0
        for row in files:
            fname, display, fname_norm, name_norm, doc_num, fpath = row
            if not fpath or not os.path.exists(fpath):
                continue

            # Check if already cached
            cache_key = f"{letter}_{doc_num}_{fname}".replace(" ", "_").replace(".", "_")
            cache_file = ocr_cache_dir / f"{cache_key}.txt"
            if cache_file.exists():
                total_cached += 1
                continue

            if processed_this_cab >= max_per_cabinet:
                break

            try:
                text, method = extract_pdf_text(fpath)
                if text and len(text.strip()) > 20:
                    cache_file.write_text(text[:5000], encoding="utf-8")
                    total_processed += 1
                    processed_this_cab += 1
                    if total_processed % 10 == 0:
                        print(f"  Processed {total_processed} PDFs...", flush=True)
            except Exception:
                total_errors += 1

        if processed_this_cab > 0:
            print(f"  Cabinet {letter}: {processed_this_cab} new PDFs processed")

    print(
        f"[batch] OCR warm-up complete: {total_processed} new, "
        f"{total_cached} cached, {total_errors} errors"
    )


if __name__ == "__main__":
    main()
