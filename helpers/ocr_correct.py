"""
helpers/ocr_correct.py — Survey-domain OCR post-processing and text correction.

Fixes common Tesseract OCR errors in land survey and legal description text:
  • Degree symbol recovery (mangled °, ', " characters)
  • Direction letter recovery ($ → S, | → I, etc.)
  • Numeric OCR substitutions (l → 1, O → 0 in numeric contexts)
  • Survey term spell correction (thance → thence, begining → beginning)
  • Unit normalization (teet → feet, ft. → feet)
  • Whitespace cleanup in bearing/distance patterns
"""

import re


# ══════════════════════════════════════════════════════════════════════════════
# SURVEY TERM DICTIONARY
# ══════════════════════════════════════════════════════════════════════════════

# Common OCR misspellings → correct survey terms
_TERM_CORRECTIONS: dict[str, str] = {
    # Bearings / directions
    "thance":       "thence",
    "thense":       "thence",
    "th ence":      "thence",
    "thenee":       "thence",
    "thanee":       "thence",
    "theuce":       "thence",
    "thcnce":       "thence",

    # Points
    "begining":     "beginning",
    "beginng":      "beginning",
    "begimning":    "beginning",
    "begmning":     "beginning",
    "heginning":    "beginning",
    "begiiming":    "beginning",
    "begiruming":   "beginning",
    "commencing":   "commencing",
    "comrnencing":  "commencing",
    "commensing":   "commencing",

    # Units
    "teet":         "feet",
    "fect":         "feet",
    "fcet":         "feet",
    "feot":         "feet",
    "feat":         "feet",
    "feei":         "feet",
    "ieet":         "feet",
    "foet":         "feet",

    # Monuments
    "rchar":        "rebar",
    "rcbar":        "rebar",
    "re bar":       "rebar",
    "rehbar":       "rebar",
    "iroh":         "iron",
    "lron":         "iron",  # lowercase L → I
    "ircn":         "iron",

    # Survey terms
    "hounding":     "bounding",
    "boundcd":      "bounded",
    "hounded":      "bounded",
    "haaring":      "bearing",
    "hcaring":      "bearing",
    "bcaring":      "bearing",
    "distanec":     "distance",
    "distanee":     "distance",
    "distancc":     "distance",
    "corvey":       "survey",
    "survcy":       "survey",
    "survcey":      "survey",
    "platted":      "platted",
    "plattcd":      "platted",
    "seetion":      "section",
    "sectiou":      "section",
    "towuship":     "township",
    "townsbip":     "township",

    # Legal terms
    "grautor":      "grantor",
    "grantec":      "grantee",
    "grantcc":      "grantee",
    "conveyancc":   "conveyance",
    "conveyauce":   "conveyance",
    "easemeut":     "easement",
    "cabiuet":      "cabinet",
    "cabinct":      "cabinet",

    # NM-specific
    "accquia":      "acequia",
    "acequla":      "acequia",

    # Common whole-word OCR errors
    "ol":           "of",
    "ot":           "of",
    "arid":         "and",
    "aud":          "and",
    "tbe":          "the",
    "tiie":         "the",
    "poinl":        "point",
    "poiut":        "point",
}

# Build a compiled regex for term replacement (word-boundary matching)
_TERM_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(
        _TERM_CORRECTIONS.keys(), key=len, reverse=True
    )) + r')\b',
    re.IGNORECASE
)


# ══════════════════════════════════════════════════════════════════════════════
# CHARACTER-LEVEL CORRECTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _fix_degree_symbols(text: str) -> str:
    """Recover degree (°), minute ('), and second (") symbols mangled by OCR.

    Common OCR errors:
      - ° rendered as: 0, o, O, *, @, ^, ~, blank
      - ' rendered as: ', `, l
      - " rendered as: '', ``, Il, rl
    """
    # Fix bearing patterns where degree symbol is missing or mangled:
    # "S 45 30 00 E" → "S 45°30'00\" E"
    # "N45-30-00E"   → "N 45°30'00\" E"
    # "S 4530'00\" E" → "S 45°30'00\" E"  (missing °)

    # Pattern: direction + degrees (possibly mangled separator) + minutes + seconds + direction
    # Handles: N 45 30 00 E, N45-30-00E, S45o30'00"E, N 45*30'00"E
    def _fix_bearing(m):
        ns = m.group(1).upper()
        deg = m.group(2)
        sep1 = m.group(3) or ""
        mn = m.group(4) or ""
        sep2 = m.group(5) or ""
        sec = m.group(6) or ""
        ew = m.group(7).upper()

        result = f"{ns} {deg}°"
        if mn:
            result += f"{mn}'"
        if sec:
            result += f'{sec}"'
        result += f" {ew}"
        return result

    text = re.sub(
        r'([NS])\s*'
        r'(\d{1,3})'
        r'([°oO\*@\^~\-\s])\s*'       # degree separator (mangled or space)
        r'(\d{1,2})?'
        r'([\'′`\-\s])?\s*'            # minute separator
        r'(\d{1,2})?'
        r'[\"″`\-\s]*'                 # second separator
        r'\s*([EW])',
        _fix_bearing,
        text,
        flags=re.IGNORECASE
    )

    return text


def _fix_direction_letters(text: str) -> str:
    """Fix direction letters mangled by OCR.

    Common substitutions in bearing context:
      $ → S, 5 → S (at start of bearing), | → I, l → I
    """
    # $ or 5 before digits in bearing context → S
    text = re.sub(r'(?<!\w)\$\s*(\d{1,3}[°\s])', r'S \1', text)

    # At start of a bearing-like context, 5 followed by space+digits → S
    # Only fix when it clearly looks like a bearing (5 45° → S 45°)
    text = re.sub(r'(?<!\d)5\s+(\d{1,3}\s*°)', r'S \1', text)

    return text


def _fix_numeric_ocr(text: str) -> str:
    """Fix common OCR letter↔digit substitutions in numeric contexts.

    - 'l' (lowercase L) → '1' when surrounded by digits
    - 'O' (uppercase O) → '0' when surrounded by digits
    - 'I' → '1' when between digits
    """
    # 'l' between digits → '1': "1l5.50" → "115.50"
    text = re.sub(r'(?<=\d)l(?=\d)', '1', text)
    # 'O' between digits → '0': "1O5.50" → "105.50"
    text = re.sub(r'(?<=\d)O(?=\d)', '0', text)
    # 'I' between digits → '1': "1I5.50" → "115.50"
    text = re.sub(r'(?<=\d)I(?=\d)', '1', text)

    # Leading 'l' before digits in distance context → '1': "l25.50 feet" → "125.50 feet"
    text = re.sub(r'(?<!\w)l(\d+\.?\d*)\s*(?:feet|foot|ft|teet|fect|fcet|\')', r'1\1 feet', text, flags=re.I)

    return text


def _fix_unit_variations(text: str) -> str:
    """Normalize unit representations.

    "ft." → "feet", "ft " → "feet", trailing ' as feet marker
    """
    # "ft." or "ft" → "feet" (but not "ft" as part of another word)
    text = re.sub(r'\bft\.?\b', 'feet', text, flags=re.I)

    return text


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of spaces (but preserve newlines) and fix common spacing issues."""
    # Collapse multiple spaces to single
    text = re.sub(r' {2,}', ' ', text)
    # Fix spaces injected into middle of numbers: "12 5.50" when it should be "125.50"
    # Only do this when the context is clearly a distance (followed by feet/ft)
    text = re.sub(r'(\d) (\d+\.\d+)\s*(?:feet|ft)', r'\1\2 feet', text, flags=re.I)

    return text


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def clean_survey_text(text: str) -> str:
    """Apply all survey-domain OCR corrections to the given text.

    This is the main entry point — call this on OCR output before parsing
    for bearings, distances, adjoiners, etc.

    The corrections are applied in a specific order to avoid cascading errors:
      1. Whitespace cleanup (first, so patterns match cleanly)
      2. Character-level fixes (direction letters, digits)
      3. Degree/minute/second symbol recovery
      4. Unit normalization
      5. Survey term spell correction (last, most aggressive)

    Returns the corrected text.
    """
    if not text or len(text.strip()) < 5:
        return text

    # Step 1: Whitespace
    text = _collapse_whitespace(text)

    # Step 2: Character-level numeric fixes
    text = _fix_numeric_ocr(text)

    # Step 3: Direction letters
    text = _fix_direction_letters(text)

    # Step 4: Degree symbols
    text = _fix_degree_symbols(text)

    # Step 5: Unit normalization
    text = _fix_unit_variations(text)

    # Step 6: Survey term dictionary
    def _replace_term(m):
        word = m.group(0)
        # Preserve original casing style
        corrected = _TERM_CORRECTIONS.get(word.lower(), word)
        if word[0].isupper():
            return corrected.capitalize()
        return corrected

    text = _TERM_PATTERN.sub(_replace_term, text)

    return text


def correction_stats(original: str, corrected: str) -> dict:
    """Compare original and corrected text, returning a summary of changes.

    Useful for logging and diagnostics.
    """
    if original == corrected:
        return {"changed": False, "corrections": 0}

    # Count character-level differences
    changes = sum(1 for a, b in zip(original, corrected) if a != b)
    changes += abs(len(original) - len(corrected))

    return {
        "changed": True,
        "corrections": changes,
        "original_len": len(original),
        "corrected_len": len(corrected),
        "delta_chars": len(corrected) - len(original),
    }
