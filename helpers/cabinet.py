"""
helpers/cabinet.py — Cabinet file searching and reference parsing.

Extracted from app.py to improve testability and separation of concerns.
"""

import re
from pathlib import Path


# Cabinet folder name mapping  (letter → folder name on disk)
CABINET_FOLDERS = {
    "A": "Cabinet A",
    "B": "Cabinet B",
    "C": "Cabinet C",
    "D": "Cabinet D",
    "E": "Cabinet E",
    "F": "Cabinet F (from RGSS scans & 1st NM website)",
}


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
    # Strip leading cabinet ref (CAB. X-NNN-A or X-NNN-A format)
    name_part = re.sub(
        r'(?:CAB(?:INET)?\.?\s*)?[A-Fa-f]\s*-\s*\d{1,4}(?:-[A-Za-z])?\s*',
        '', plat_str, count=1, flags=re.I
    ).strip()
    if not name_part or len(name_part) < 3:
        return []
    tokens = []
    tokens.append(name_part)   # full name as substring (e.g. 'ADELA RAEL')
    # Also add last-name portion (before comma if present)
    last = name_part.split(',')[0].strip()
    if last and last != name_part and len(last) >= 3:
        tokens.append(last)
    # Add individual words >= 4 chars, excluding noise words
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
    """
    Extract every cabinet reference from any field in the deed detail.
    Handles both long form (CAB C-191A) and short form (C-191-A / C-191A).
    Returns list of {"cabinet": "C", "doc": "191A", "raw": "..."}.
    """
    refs = []
    seen = set()
    # Long form:  CAB C-191A  /  Cabinet C-191  /  CAB. F-5B
    pat_long = re.compile(r'\bCAB(?:INET)?[\s.]?([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b', re.I)
    # Short form: C-191-A  /  C-191A  (standalone, not part of a longer word)
    pat_short = re.compile(r'(?<![A-Za-z0-9])([A-Fa-f])[-–](\d{1,4})[-.–]?([A-Za-z]?)(?![A-Za-z0-9])')
    for val in detail.values():
        text = str(val)
        for m in pat_long.finditer(text):
            cab = m.group(1).upper()
            doc = m.group(2).upper()
            key = f"{cab}-{doc}"
            if key not in seen:
                seen.add(key)
                refs.append({"cabinet": cab, "doc": doc, "raw": m.group(0)})
        for m in pat_short.finditer(text):
            cab = m.group(1).upper()
            num = m.group(2)
            suffix = m.group(3).upper()
            doc = num + suffix  # e.g. "191A"
            key = f"{cab}-{doc}"
            if key not in seen:
                seen.add(key)
                refs.append({"cabinet": cab, "doc": doc, "raw": m.group(0)})
    return refs


# ══════════════════════════════════════════════════════════════════════════════
# FILENAME PARSING
# ══════════════════════════════════════════════════════════════════════════════

def extract_cabinet_display_name(filename: str) -> str:
    """
    Strip the leading numeric document-number prefix from a cabinet filename
    to expose just the owner name portion for display.

    Filename pattern:  195554.001   Adela Rael.PDF
                       ^^^^^^^^^^   ^^^^^^^^^^^^^^^
                       doc number   owner name  ← display name

    Examples:
      '195554.001   Adela Rael.PDF'  → 'Adela Rael'
      '100191.001 Rael Adela.pdf'    → 'Rael Adela'
      '003721 Torres C-191A.pdf'     → 'Torres C-191A'
      'Rael Adela.PDF'               → 'Rael Adela'   (no prefix — unchanged)
    """
    stem = Path(filename).stem.strip()
    clean = re.sub(r'^\d+(?:\.\d+)?\s+', '', stem).strip()
    return clean or stem


def extract_cabinet_doc_number(filename: str) -> str:
    """
    Extract the leading numeric document number from a cabinet filename.

    Filename pattern:  195554.001   Adela Rael.PDF  →  '195554'
                       003721 Torres C-191A.pdf      →  '3721'
                       Rael Adela.PDF                →  ''  (no number)
    """
    stem = Path(filename).stem.strip()
    m = re.match(r'^(\d+)', stem)
    return m.group(1) if m else ""


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL CABINET SEARCH
# ══════════════════════════════════════════════════════════════════════════════

# Per-cabinet file listing cache: { "C": (mtime, file_list), ... }
_cab_scan_cache = {}


def search_local_cabinet(cabinet: str, doc_num: str,
                          cabinet_path: str,
                          grantor: str = "", grantee: str = "") -> list[dict]:
    """
    Walk the cabinet folder and return files matching by document number or owner name.

    Cabinet files follow the naming convention:
        195554.001   Adela Rael.PDF
        ^^^^^^^^^^   ^^^^^^^^^^^^^^^
        doc number   owner name

    IMPORTANT: The leading number in the filename IS the plat document number
    recorded in the county clerk index.  It is NOT a meaningless file index.

    Match strategies (highest to lowest priority):
      doc_number — doc_num matches the file's leading numeric prefix exactly.
      name_match — name token appears in the filename.
      page_ref   — cabinet letter + doc_num found anywhere in filename.

    ``cabinet_path`` is the base path to the cabinets directory.
    """
    folder_name = CABINET_FOLDERS.get(cabinet)
    if not folder_name:
        return []
    cab_dir = Path(cabinet_path) / folder_name
    if not cab_dir.exists():
        return []

    results   = []
    doc_clean = (doc_num or "").strip()

    # Strip any non-numeric suffix from doc_num so "191A" → "191" for prefix matching,
    doc_numeric = re.sub(r'[^0-9]', '', doc_clean)

    # Pattern for cabinet page-ref embedded in filename, e.g. "C-191A" or "C 191A"
    page_ref_pat = re.compile(
        r'(?<![A-Za-z])' + re.escape(cabinet) + r'[\-\s]?' + re.escape(doc_clean) + r'(?![A-Za-z0-9])',
        re.I
    ) if doc_clean else None

    # Name tokens — accept either "last" from "Last, First" format OR full raw token
    name_tokens = []
    for person in [grantor, grantee]:
        if not person:
            continue
        person = person.strip()
        if len(person) >= 3 and person.lower() not in [t.lower() for t in name_tokens]:
            name_tokens.append(person)
        last = person.split(',')[0].strip()
        if last and last != person and len(last) >= 3 and last.lower() not in [t.lower() for t in name_tokens]:
            name_tokens.append(last)
        for w in re.split(r'[\s,]+', person):
            if len(w) >= 4 and w.lower() not in [t.lower() for t in name_tokens]:
                name_tokens.append(w)

    if not doc_clean and not name_tokens:
        return []

    # ── Per-cabinet file listing cache (keyed on folder mtime) ───────────────
    try:
        cur_mtime = cab_dir.stat().st_mtime
    except OSError:
        return []
    cached      = _cab_scan_cache.get(cabinet)
    if cached and cached[0] == cur_mtime:
        all_files = cached[1]
    else:
        all_files = [
            (f.name, extract_cabinet_display_name(f.name),
             extract_cabinet_doc_number(f.name), str(f),
             round(f.stat().st_size / 1024))
            for f in sorted(cab_dir.iterdir())
            if f.is_file() and f.suffix.lower() == '.pdf'
        ]
        _cab_scan_cache[cabinet] = (cur_mtime, all_files)

    for fname, display_name, file_doc_num, fpath, size_kb in all_files:
        fname_lower  = fname.lower()
        name_lower   = display_name.lower()

        match_strategy = ""
        tok_len        = 0

        # TOP PRIORITY: doc number match
        if doc_numeric and file_doc_num:
            if file_doc_num == doc_numeric:
                match_strategy = "doc_number"
                tok_len = 1000

        # SECONDARY: name token appears anywhere in filename
        if not match_strategy:
            for tok in name_tokens:
                tok_l = tok.lower()
                if tok_l in fname_lower or tok_l in name_lower:
                    match_strategy = "name_match"
                    tok_len = len(tok)
                    break

        # TERTIARY: cabinet page-ref like "C-191A" embedded anywhere in filename
        if not match_strategy and page_ref_pat and page_ref_pat.search(fname):
            match_strategy = "page_ref"

        if match_strategy:
            results.append({
                "file":         fname,
                "display_name": display_name,
                "doc_number":   file_doc_num,
                "path":         fpath,
                "cabinet":      cabinet,
                "doc":          doc_clean,
                "size_kb":      size_kb,
                "strategy":     match_strategy,
                "_tok_len":     tok_len,
            })

    # Sort: doc_number first, then name_match (longer token = more specific), then page_ref
    results.sort(key=lambda r: (
        0 if r['strategy'] == 'doc_number' else
        1 if r['strategy'] == 'name_match' else 2,
        -r.get('_tok_len', 0)
    ))
    return results
