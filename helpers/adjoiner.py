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
    # "ADJOINS GARCIA, JUAN"  /  "ADJOINING THE PROPERTY OF TORRES"
    re.compile(
        r'\badjoins?\s+(?:the\s+(?:property|lands?)\s+of\s+)?([A-Z][A-Z\s,\.\'-]{2,40}?)(?=\s*[,;]|\s+(?:on|bounded|thence)|$)',
        re.I | re.MULTILINE
    ),
    # "BOUNDED BY GARCIA"  /  "BOUNDED ON THE NORTH BY LANDS OF RAEL"
    re.compile(
        r'\bbounded\s+(?:on\s+the\s+(?:north|south|east|west)\w*\s+)?by\s+(?:(?:the\s+)?(?:lands?|property)\s+of\s+)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and)|$)',
        re.I | re.MULTILINE
    ),
    # Directional: "ON THE NORTH BY LANDS OF RAEL"  /  "NORTHERLY BY GARCIA"
    re.compile(
        r'\b(?:on\s+the\s+)?(?:north|south|east|west)\w*\s+(?:by\s+)?(?:(?:the\s+)?(?:lands?|property)\s+of\s+)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and\s+on|to\s+a)|$)',
        re.I | re.MULTILINE
    ),
    # "FORMERLY OF MARTINEZ"  /  "NOW OR FORMERLY GARCIA"
    re.compile(
        r'\b(?:now\s+or\s+)?formerly\s+(?:of\s+)?(?:the\s+)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and)|$)',
        re.I | re.MULTILINE
    ),
    # "ESTATE OF MARTINEZ, CARLOS"
    re.compile(
        r'\bestate\s+of\s+(?:the\s+)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and)|$)',
        re.I | re.MULTILINE
    ),
    # "ALONG THE [direction] LINE OF GARCIA"  /  "ALONG THE RAEL PROPERTY"
    re.compile(
        r'\balong\s+(?:the\s+)?(?:(?:north|south|east|west)\w*\s+)?'
        r'(?:line|boundary|fence)\s+of\s+(?:the\s+)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and|to\s+a)|$)',
        re.I | re.MULTILINE
    ),
    # "THENCE ALONG [NAME] PROPERTY"  /  "ALONG THE [NAME] TRACT"
    re.compile(
        r'\balong\s+(?:the\s+)?([A-Z][A-Z\s,\.\'-]{2,40}?)\s+(?:property|tract|land|parcel|line|fence)',
        re.I | re.MULTILINE
    ),
    # "CORNER COMMON TO [NAME]"  /  "CORNER WITH [NAME]"
    re.compile(
        r'\bcorner\s+(?:common\s+to|with|of)\s+(?:the\s+)?(?:(?:lands?|property)\s+of\s+)?'
        r'([A-Z][A-Z\s,\.\'-]{2,50}?)(?=\s*[,;]|\s+(?:on|bounded|thence|and)|$)',
        re.I | re.MULTILINE
    ),
]

_NOISE_WORDS = {
    'the', 'said', 'above', 'herein', 'grantor', 'grantee', 'county', 'state',
    'new', 'mexico', 'united', 'states', 'government', 'public', 'road',
    'street', 'acequia', 'ditch', 'right', 'way', 'river', 'creek', 'arroyo',
    'unknown', 'parties', 'record', 'described', 'following', 'certain',
    # Directional / legal filler that gets captured as names
    'north', 'south', 'east', 'west', 'northerly', 'southerly', 'easterly',
    'westerly', 'northeast', 'northwest', 'southeast', 'southwest',
    'tract', 'parcel', 'lot', 'block', 'section', 'survey', 'plat',
    'beginning', 'thence', 'along', 'point', 'corner', 'line', 'boundary',
    'thereof', 'therein', 'portion', 'remainder', 'being', 'lying',
    'taos', 'santa', 'bernalillo',  # common county false positives
    'subdivision', 'addition', 'unit', 'phase',
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
