"""
helpers/adjoiner.py — Adjoiner name parsing from deed legal descriptions.

Extracted from app.py to improve testability and separation of concerns.
"""

import re


# Common New Mexico legal description patterns that name adjoining owners
_ADJ_PATTERNS = [
    # "LANDS OF RAEL, CARLOS A"  /  "LAND OF GARCIA"
    re.compile(
        r'\blands?\s+of\s+(?:the\s+(?:heirs?\s+of\s+)?)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and\s+on|to\s+a)|$)',
        re.I | re.MULTILINE
    ),
    # "PROPERTY OF GARCIA, JUAN"
    re.compile(
        r'\bproperty\s+of\s+([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence)|$)',
        re.I | re.MULTILINE
    ),
    # "ADJOINS GARCIA, JUAN"
    re.compile(
        r'\badjoins?\s+([A-Z][A-Z\s,\.\'-]{2,40}?)(?=\s*[,;]|\s+(?:on|bounded|thence)|$)',
        re.I | re.MULTILINE
    ),
]

_NOISE_WORDS = {
    'the', 'said', 'above', 'herein', 'grantor', 'grantee', 'county', 'state',
    'new', 'mexico', 'united', 'states', 'government', 'public', 'road',
    'street', 'acequia', 'ditch', 'right', 'way', 'river', 'creek', 'arroyo',
    'unknown', 'parties', 'record', 'described', 'following', 'certain',
}


def parse_adjoiner_names(detail: dict) -> list[dict]:
    """
    Scan all text fields in the deed detail for 'Lands of [Name]' patterns.
    Returns list of {name, raw, field} dicts, de-duplicated.
    """
    found = []
    seen  = set()

    # Fields most likely to contain legal description text
    priority_fields = [
        "Other_Legal", "Subdivision_Legal", "Comments",
        "Reference", "Legal Description", "Legal", "Description",
    ]
    # Build ordered list: priority first, then everything else
    all_fields = priority_fields + [k for k in detail if k not in priority_fields]

    for field in all_fields:
        val = detail.get(field, "")
        if not val or not isinstance(val, str):
            continue
        for pat in _ADJ_PATTERNS:
            for m in pat.finditer(val):
                raw  = m.group(0).strip()
                name = m.group(1).strip().rstrip(".,;")
                # Clean: collapse whitespace, title-case
                name = re.sub(r'\s+', ' ', name).title()
                name_key = name.lower()
                # Filter noise
                if any(w in name_key for w in _NOISE_WORDS):
                    continue
                if len(name) < 3 or name_key in seen:
                    continue
                seen.add(name_key)
                found.append({"name": name, "raw": raw, "field": field, "source": "legal_desc"})

    return found
