"""
helpers/cabinet.py — Cabinet file searching and reference parsing.

Performance design
------------------
Cabinet drives may be slow (HDD or network). Scanning 7500+ PDFs per cabinet
on every request is unacceptable.

Solution: a **persistent local JSON index** stored inside the app directory.

  data/cabinet_index.json   ← fast local file (on the app SSD)

The index maps each cabinet letter to a snapshot of its directory:
  {
    "C": {
      "mtime": 1711234567.0,      ← cab_dir.stat().st_mtime at index build time
      "files": [                   ← sorted list of pre-normalised rows
        [fname, display, fname_norm, name_norm, doc_num, fpath], ...
      ]
    }, ...
  }

On startup _load_cabinet_index() loads this file into memory.
On first search for a cabinet whose folder mtime has changed (or no entry),
  _rebuild_cabinet_entry() re-scans that cabinet and saves the updated index.
All subsequent searches hit the in-memory dict — O(n) string scans on
pre-normalised data, no disk I/O.

The index file lives next to the app (not on the slow cabinet drive) so
reads and writes are fast.
"""

import json
import os as _os
import re
import threading
import unicodedata as _unicodedata
from pathlib import Path


# ── Cabinet folder mapping ────────────────────────────────────────────────────
CABINET_FOLDERS = {
    "A": "Cabinet A",
    "B": "Cabinet B",
    "C": "Cabinet C",
    "D": "Cabinet D",
    "E": "Cabinet E",
    "F": "Cabinet F (from RGSS scans & 1st NM website)",
}

# Path to the local index file (set by _init_index_path, called from app.py)
_INDEX_PATH: Path | None = None

# In-memory index: { "C": {"mtime": float, "files": [[...], ...]}, ... }
_INDEX: dict = {}
_INDEX_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# PLAT NAME TOKEN EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_plat_name_tokens(plat_str: str) -> list[str]:
    """Extract searchable name tokens from a KML PLAT field.

    Removes the cabinet reference prefix and returns individual name strings
    so that search_local_cabinet can do name-based file matching.

    Examples::
      'C-191-A ADELA RAEL'        -> ['ADELA RAEL', 'ADELA', 'RAEL']
      'CAB C-84-B TORRES, GARCIA' -> ['TORRES, GARCIA', 'TORRES', 'GARCIA']
    """
    if not plat_str:
        return []
    name_part = re.sub(
        r'(?:CAB(?:INET)?\.?\s*)?[A-Fa-f]\s*-\s*\d{1,4}(?:-[A-Za-z])?\s*',
        '', plat_str, count=1, flags=re.I
    ).strip()
    if not name_part or len(name_part) < 3:
        return []
    tokens = [name_part]
    last = name_part.split(',')[0].strip()
    if last and last != name_part and len(last) >= 3:
        tokens.append(last)
    _NOISE = {'AND', 'THE', 'DEL', 'LOS', 'LAS', 'DES', 'EST', 'CORP', 'LLC'}
    for word in re.split(r'[\s,;&]+', name_part):
        w = word.strip()
        if len(w) >= 4 and w.upper() not in _NOISE and w not in tokens:
            tokens.append(w)
    return tokens


# ══════════════════════════════════════════════════════════════════════════════
# CABINET REFERENCE PARSING
# ══════════════════════════════════════════════════════════════════════════════

def parse_cabinet_refs(detail: dict) -> list[dict]:
    """Extract every cabinet reference from any field in the deed detail."""
    refs = []
    seen = set()
    pat_long  = re.compile(r'\bCAB(?:INET)?[\s.]?([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b', re.I)
    pat_short = re.compile(r'(?<![A-Za-z0-9])([A-Fa-f])[-–](\d{1,4})[-.–]?([A-Za-z]?)(?![A-Za-z0-9])')
    for val in detail.values():
        text = str(val)
        for m in pat_long.finditer(text):
            cab, doc = m.group(1).upper(), m.group(2).upper()
            key = f"{cab}-{doc}"
            if key not in seen:
                seen.add(key)
                refs.append({"cabinet": cab, "doc": doc, "raw": m.group(0)})
        for m in pat_short.finditer(text):
            cab = m.group(1).upper()
            doc = m.group(2) + m.group(3).upper()
            key = f"{cab}-{doc}"
            if key not in seen:
                seen.add(key)
                refs.append({"cabinet": cab, "doc": doc, "raw": m.group(0)})
    return refs


# ══════════════════════════════════════════════════════════════════════════════
# FILENAME PARSING
# ══════════════════════════════════════════════════════════════════════════════

def extract_cabinet_display_name(filename: str) -> str:
    """Strip the leading numeric doc-number prefix to get the owner name.

    '195554.001   Adela Rael.PDF'  -> 'Adela Rael'
    """
    stem = Path(filename).stem.strip()
    clean = re.sub(r'^\d+(?:\.\d+)?\s+', '', stem).strip()
    return clean or stem


def extract_cabinet_doc_number(filename: str) -> str:
    """Extract the leading numeric recording number from a cabinet filename.

    '195554.001   Adela Rael.PDF'  -> '195554'
    """
    stem = Path(filename).stem.strip()
    m = re.match(r'^(\d+)', stem)
    return m.group(1) if m else ""


# ══════════════════════════════════════════════════════════════════════════════
# NAME NORMALISATION & VARIANTS
# ══════════════════════════════════════════════════════════════════════════════

_NAME_PREFIXES = {'de', 'la', 'los', 'las', 'del', 'da', 'di'}


def _normalize_name(name: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace.

    ASCII fast-path skips the expensive NFKD decomposition.
    """
    try:
        name.encode('ascii')
        return re.sub(r'\s+', ' ', name.lower().strip())
    except UnicodeEncodeError:
        nfkd = _unicodedata.normalize('NFKD', name)
        stripped = ''.join(c for c in nfkd if _unicodedata.category(c) != 'Mn')
        return re.sub(r'\s+', ' ', stripped.lower().strip())


def _build_name_variants(person: str) -> list[str]:
    """Build all reasonable name variants for matching against filenames."""
    if not person or not person.strip():
        return []
    norm = _normalize_name(person)
    variants = [norm]
    seen = {norm}

    def _add(v: str):
        v = v.strip()
        if v and v not in seen and len(v) >= 3:
            seen.add(v)
            variants.append(v)

    no_comma = re.sub(r'\s+', ' ', norm.replace(',', ' ').strip())
    _add(no_comma)

    if ',' in norm:
        parts = [p.strip() for p in norm.split(',', 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            _add(f"{parts[1]} {parts[0]}")
            _add(parts[0])
            _add(parts[1])
    else:
        words = norm.split()
        if len(words) >= 2:
            _add(' '.join(words[1:]) + ' ' + words[0])
            for w in words:
                if len(w) >= 3 and w not in _NAME_PREFIXES:
                    _add(w)

    for w in re.split(r'[\s,]+', norm):
        if len(w) >= 4 and w not in _NAME_PREFIXES:
            _add(w)

    return variants


def _token_overlap_score(name_tokens: list, fname_norm: str) -> int:
    if not name_tokens:
        return 0
    fname_words = set(re.split(r'[\s,._\-]+', fname_norm))
    word_hits   = sum(1 for t in name_tokens if t in fname_words)
    substr_hits = sum(1 for t in name_tokens if t not in fname_words and t in fname_norm)
    if word_hits == 0 and substr_hits == 0:
        return 0
    return min(int(((word_hits + substr_hits * 0.5) / max(len(name_tokens), 1)) * 100), 100)


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENT LOCAL INDEX
# ══════════════════════════════════════════════════════════════════════════════

def _init_index_path(app_dir: str):
    """Call once at startup with the path to the app directory."""
    global _INDEX_PATH
    data_dir = Path(app_dir) / "data"
    data_dir.mkdir(exist_ok=True)
    _INDEX_PATH = data_dir / "cabinet_index.json"
    _load_index_from_disk()


def _load_index_from_disk():
    """Load the persisted index file into _INDEX (if it exists)."""
    global _INDEX
    if _INDEX_PATH and _INDEX_PATH.exists():
        try:
            with open(_INDEX_PATH, encoding="utf-8") as f:
                _INDEX = json.load(f)
            print(f"[cabinet] Loaded index: {sum(len(v.get('files',[])) for v in _INDEX.values())} entries across {len(_INDEX)} cabinets", flush=True)
        except Exception as e:
            print(f"[cabinet] Index load failed ({e}) — will rebuild on first search", flush=True)
            _INDEX = {}


def _save_index_to_disk():
    """Persist _INDEX to disk (called after any rebuild)."""
    if not _INDEX_PATH:
        return
    try:
        with open(_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(_INDEX, f, ensure_ascii=False)
        print(f"[cabinet] Index saved to {_INDEX_PATH}", flush=True)
    except Exception as e:
        print(f"[cabinet] Index save failed: {e}", flush=True)


def _scan_cabinet_dir(cab_dir: Path) -> list:
    """Scan a cabinet directory and return pre-normalised file rows.

    Each row: [fname, display_name, fname_norm, name_norm, doc_num, fpath]
    """
    entries = []
    try:
        with _os.scandir(str(cab_dir)) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                if not entry.name.lower().endswith('.pdf'):
                    continue
                display    = extract_cabinet_display_name(entry.name)
                doc_num    = extract_cabinet_doc_number(entry.name)
                fname_norm = _normalize_name(entry.name)
                name_norm  = _normalize_name(display)
                entries.append([
                    entry.name, display, fname_norm, name_norm, doc_num, entry.path
                ])
    except OSError as e:
        print(f"[cabinet] scandir failed: {e}", flush=True)
    entries.sort(key=lambda e: e[0])
    return entries


def _rebuild_cabinet_entry(letter: str, cab_dir: Path, mtime: float):
    """Re-scan one cabinet and update _INDEX + save to disk. Thread-safe."""
    print(f"[cabinet] Rebuilding index for Cabinet {letter} ({cab_dir}) …", flush=True)
    files = _scan_cabinet_dir(cab_dir)
    with _INDEX_LOCK:
        _INDEX[letter] = {"mtime": mtime, "files": files}
    _save_index_to_disk()
    print(f"[cabinet] Cabinet {letter}: {len(files)} PDFs indexed", flush=True)


def _warm_cabinet_caches(base_path: str):
    """Rebuild any stale or missing cabinet index entries in a background thread."""
    def _do_warm():
        for letter, folder in CABINET_FOLDERS.items():
            cab_dir = Path(base_path) / folder
            if not cab_dir.exists():
                continue
            try:
                mtime = cab_dir.stat().st_mtime
            except OSError:
                continue
            entry = _INDEX.get(letter)
            if entry and entry.get("mtime") == mtime:
                print(f"[cabinet] Cabinet {letter} index is current ({len(entry['files'])} files)", flush=True)
                continue   # still fresh
            _rebuild_cabinet_entry(letter, cab_dir, mtime)
    t = threading.Thread(target=_do_warm, daemon=True, name="cabinet-warmer")
    t.start()


# Legacy alias kept for compatibility
_cab_scan_cache: dict = {}


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ══════════════════════════════════════════════════════════════════════════════

def search_local_cabinet(cabinet: str, doc_num: str,
                          cabinet_path: str,
                          grantor: str = "", grantee: str = "") -> list[dict]:
    """Search a cabinet folder for PDFs matching doc_num or grantor/grantee name.

    Uses the persistent local index for speed.  Falls back to a live directory
    scan only if the index has no entry for this cabinet.
    """
    folder_name = CABINET_FOLDERS.get(cabinet)
    if not folder_name:
        return []
    cab_dir = Path(cabinet_path) / folder_name

    doc_clean   = (doc_num or "").strip()
    doc_numeric = re.sub(r'[^0-9]', '', doc_clean)

    page_ref_pat = re.compile(
        r'(?<![A-Za-z])' + re.escape(cabinet) + r'[\-\s]?' + re.escape(doc_clean) + r'(?![A-Za-z0-9])',
        re.I
    ) if doc_clean else None

    all_name_variants: list[str] = []
    all_name_words:    set[str]  = set()
    for person in (grantor, grantee):
        if not person:
            continue
        for v in _build_name_variants(person):
            if v not in all_name_variants:
                all_name_variants.append(v)
        for w in re.split(r'[\s,]+', _normalize_name(person)):
            if len(w) >= 3 and w not in _NAME_PREFIXES:
                all_name_words.add(w)

    if not doc_clean and not all_name_variants:
        return []

    # ── Get file list from index ──────────────────────────────────────────────
    with _INDEX_LOCK:
        entry = _INDEX.get(cabinet)

    if entry:
        all_files = entry["files"]
    else:
        # Index missing for this cabinet — do a live scan (slow first time)
        if not cab_dir.exists():
            return []
        print(f"[cabinet] No index for Cabinet {cabinet} — live scan (slow!)", flush=True)
        all_files = _scan_cabinet_dir(cab_dir)
        try:
            mtime = cab_dir.stat().st_mtime
        except OSError:
            mtime = 0.0
        with _INDEX_LOCK:
            _INDEX[cabinet] = {"mtime": mtime, "files": all_files}
        _save_index_to_disk()

    results:    list[dict] = []
    name_words: list[str]  = list(all_name_words)

    # ── Doc-number fast path ──────────────────────────────────────────────────
    if doc_numeric:
        for row in all_files:
            fname, display, fname_norm, name_norm, dn, fpath = row
            if dn == doc_numeric:
                results.append({
                    "file": fname, "display_name": display, "doc_number": dn,
                    "path": fpath, "cabinet": cabinet, "doc": doc_clean,
                    "size_kb": 0, "strategy": "doc_number", "_tok_len": 1000,
                })
        if results:
            return results

    # ── Name-variant scan ─────────────────────────────────────────────────────
    seen_paths: set[str] = set()
    for row in all_files:
        fname, display, fname_norm, name_norm, dn, fpath = row
        match_strategy = ""
        tok_len        = 0

        if all_name_variants:
            best_score = 0
            for variant in all_name_variants:
                if variant in fname_norm or variant in name_norm:
                    score = _token_overlap_score(name_words, name_norm)
                    if score > best_score:
                        best_score = score
                    match_strategy = "name_match"
                    tok_len = max(tok_len, len(variant) + best_score)

        if not match_strategy and page_ref_pat and page_ref_pat.search(fname):
            match_strategy = "page_ref"

        if match_strategy and fpath not in seen_paths:
            seen_paths.add(fpath)
            results.append({
                "file": fname, "display_name": display, "doc_number": dn,
                "path": fpath, "cabinet": cabinet, "doc": doc_clean,
                "size_kb": 0, "strategy": match_strategy, "_tok_len": tok_len,
            })

    results.sort(key=lambda r: (
        0 if r["strategy"] == "doc_number" else
        1 if r["strategy"] == "name_match" else 2,
        -r.get("_tok_len", 0)
    ))
    return results
