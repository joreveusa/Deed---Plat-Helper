"""
helpers/metes_bounds.py — Metes-and-bounds parsing, coordinate computation, and shared analysis helpers.

Extracted from app.py to eliminate duplication and improve testability.
"""

import re
import math


# ══════════════════════════════════════════════════════════════════════════════
# UNIT CONVERSION  (all → feet)
# ══════════════════════════════════════════════════════════════════════════════

_UNIT_TO_FEET = {
    "feet":   1.0,
    "foot":   1.0,
    "ft":     1.0,
    "yards":  3.0,
    "yard":   3.0,
    "yd":     3.0,
    "chains": 66.0,
    "chain":  66.0,
    "ch":     66.0,
    "poles":  16.5,
    "pole":   16.5,
    "rods":   16.5,
    "rod":    16.5,
    "rd":     16.5,
    "links":  0.66,
    "link":   0.66,
    "lk":     0.66,
    "meters": 3.28084,
    "meter":  3.28084,
    "m":      3.28084,
    "varas":  2.7778,    # NM vara — critical for old NM land grant deeds
    "vara":   2.7778,
    "v":      2.7778,    # shorthand common in old plats
}

# Regex fragment for matching unit names after a distance value
_UNIT_RE_FRAGMENT = r'(?:feet|foot|ft|yards?|yd|chains?|ch|poles?|rods?|rd|links?|lk|meters?|m|varas?|v(?=\b))'


def _to_feet(value: float, unit: str) -> float:
    """Convert a distance value in the given unit to feet."""
    unit = (unit or "feet").lower().strip().rstrip(".")
    factor = _UNIT_TO_FEET.get(unit, 1.0)
    return value * factor


# ══════════════════════════════════════════════════════════════════════════════
# BEARING / DISTANCE REGEX PATTERNS
# ══════════════════════════════════════════════════════════════════════════════

# Matches patterns like:
#   S 45°30'00" E, 125.50 feet
#   N45-30-00E 125.50'
#   N 45 30 00 E 125.50 ft
#   N45°W 87.20
#   S45E 125.50
_BEARING_PAT = re.compile(
    r'\b([NS])\s*'                                  # N or S
    r'(\d{1,3})'                                    # degrees
    r'[°\-\s]*(\d{0,2})[\'′\-\s]*(\d{0,2})["″\-\s]*'  # opt min/sec
    r'([EW])\b'                                     # E or W
    r'[,\s]*'
    r'([\d,]+\.?\d*)'                               # distance
    r'\s*(' + _UNIT_RE_FRAGMENT + r'|\')?',
    re.IGNORECASE
)

# Also catch written-out degrees: "45 degrees 30 minutes 00 seconds"
_BEARING_VERBOSE = re.compile(
    r'\b([NS])\s*'
    r'(\d+)\s*(?:deg(?:rees?)?)?\s*'
    r'(\d*)\s*(?:min(?:utes?)?)?\s*'
    r'(\d*)\s*(?:sec(?:onds?)?)?\s*'
    r'([EW])\b'
    r'[,\s]*'
    r'([\d,]+\.?\d*)'
    r'\s*(' + _UNIT_RE_FRAGMENT + r'|\')?',
    re.IGNORECASE
)

# Curve / arc call pattern
# Handles: "curve to the left, radius 150 feet, arc length 75.23 feet, chord bears N45°30'00"E"
_CURVE_PAT = re.compile(
    r'\bcurve\s+to\s+the\s*(left|right)'
    r'[^.]{0,200}?'
    r'radius[\s:]+([\d.]+)\s*(?:feet|ft)?'
    r'[^.]{0,200}?'
    r'(?:arc\s+(?:length|len)[\s:]+([\d.]+)\s*(?:feet|ft)?)?'
    r'[^.]{0,200}?'
    r'(?:delta[\s:=]+([\d.]+)(?:°|\s*deg(?:rees?)?)?)?'
    r'[^.]{0,200}?'
    r'(?:chord\s+(?:bears?\s+|bearing\s+)([NS]\s*\d+[^,;.]{0,30}?[EW]))?',
    re.I | re.S
)


# ══════════════════════════════════════════════════════════════════════════════
# SHARED ANALYSIS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_MONUMENT_PATTERNS = {
    "iron pin":   re.compile(r'\biron\s+(?:pin|rod|pipe)\b', re.I),
    "rebar":      re.compile(r'\brebar\b', re.I),
    "pipe":       re.compile(r'\bpipe\b', re.I),
    "rock":       re.compile(r'\b(?:rock|stone)\s+(?:mound|monument|cairn|marker)\b', re.I),
    "concrete":   re.compile(r'\bconcrete\s+(?:monument|post|marker)\b', re.I),
    "fence":      re.compile(r'\bfence\s*(?:corner|line|post)?\b', re.I),
    "cap":        re.compile(r'\bcap\b', re.I),
    "nail":       re.compile(r'\bnail\b', re.I),
}

_LOT_BLOCK_RE = re.compile(r'\b(?:lot|block|unit)\s+\d', re.I)
_TRACT_RE     = re.compile(r'\b(?:tract)\s+[A-Z0-9]', re.I)
_POB_RE       = re.compile(r'\bpoint\s+of\s+(?:beginning|commencement)|POB|P\.?O\.?B\.?|POINT\s+OF\s+BEGINNING', re.I)


def detect_monuments(text: str) -> list[str]:
    """Scan text for survey monument references. Returns list of monument type names."""
    return [name for name, pat in _MONUMENT_PATTERNS.items() if pat.search(text)]


def classify_description_type(text: str, calls: list, trs_refs: list) -> str:
    """Classify the legal description type based on content."""
    if calls:
        return "metes_and_bounds"
    if _LOT_BLOCK_RE.search(text):
        return "lot_block"
    if _TRACT_RE.search(text):
        return "tract"
    if trs_refs:
        return "trs_only"
    return "unknown"


def shoelace_area(pts: list[tuple]) -> float:
    """Compute area in square feet using the Shoelace formula."""
    if len(pts) < 3:
        return 0.0
    n = len(pts)
    a = 0.0
    for i in range(n):
        j = (i + 1) % n
        a += pts[i][0] * pts[j][1]
        a -= pts[j][0] * pts[i][1]
    return round(abs(a) / 2.0, 2)


def has_pob(text: str) -> bool:
    """Check if text contains a Point of Beginning reference."""
    return bool(_POB_RE.search(text))


# ══════════════════════════════════════════════════════════════════════════════
# TRS (Township / Range / Section) EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_trs(text: str) -> list[dict]:
    """
    Extract Township / Range / Section references from deed text.
    Handles: T5N R5E S12, T 5 N R 5 E Sec 12, etc.
    Returns list of {trs, township, range, section} dicts.
    """
    pat = re.compile(
        r'\bT\.?\s*(\d+)\s*([NS])\b'
        r'[\s,]*'
        r'\bR\.?\s*(\d+)\s*([EW])\b'
        r'(?:[\s,]*\bSec(?:tion)?\.?\s*(\d+)\b)?',
        re.I
    )
    results = []
    seen = set()
    for m in pat.finditer(text):
        t_num = m.group(1); t_dir = m.group(2).upper()
        r_num = m.group(3); r_dir = m.group(4).upper()
        sec   = m.group(5) or ""
        trs   = f"T{t_num}{t_dir} R{r_num}{r_dir}" + (f" S{sec}" if sec else "")
        if trs not in seen:
            seen.add(trs)
            results.append({"trs": trs, "township": f"T{t_num}{t_dir}",
                            "range": f"R{r_num}{r_dir}", "section": sec})
    return results


# ══════════════════════════════════════════════════════════════════════════════
# CORE PARSING
# ══════════════════════════════════════════════════════════════════════════════

def _bearing_to_azimuth(ns: str, deg: float, mn: float, sec: float, ew: str) -> float:
    """Convert quadrant bearing to azimuth (0=N, clockwise positive)."""
    dd = deg + mn / 60.0 + sec / 3600.0
    ns = ns.upper(); ew = ew.upper()
    if ns == 'N' and ew == 'E':
        return dd
    if ns == 'S' and ew == 'E':
        return 180.0 - dd
    if ns == 'S' and ew == 'W':
        return 180.0 + dd
    if ns == 'N' and ew == 'W':
        return 360.0 - dd
    return 0.0


def parse_metes_bounds(text: str) -> list[dict]:
    """
    Parse metes-and-bounds calls from deed text.

    Detects:
      1. Standard bearing/distance (N 45°30'00" E, 125.50 ft)
      2. Verbose bearing/distance (North 45 degrees 30 minutes East 125.50 feet)
      3. Curve/arc calls (curve to the left, radius 150 feet ...)

    Returns list of dicts with:
      { type, bearing_label, bearing_raw, distance, azimuth_deg, ... }
    """
    calls = []
    used_ranges = []  # track character ranges to prevent double-matching

    def _overlaps(start, end):
        for s, e in used_ranges:
            if start < e and end > s:
                return True
        return False

    # 1. Curves first (they are longer and might contain bearing-like substrings)
    for m in _CURVE_PAT.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        used_ranges.append((m.start(), m.end()))

        direction = m.group(1).lower()  # left or right
        radius    = float(m.group(2))
        arc_len   = float(m.group(3)) if m.group(3) else 0.0
        delta_deg = float(m.group(4)) if m.group(4) else 0.0
        chord_brg = m.group(5) or ""

        # Compute arc length from delta+radius if not given directly
        if not arc_len and delta_deg and radius:
            arc_len = radius * math.radians(delta_deg)

        # Compute chord length for plotting
        chord_len = 0.0
        if delta_deg and radius:
            chord_len = 2 * radius * math.sin(math.radians(delta_deg) / 2)
        elif arc_len and radius:
            # approx delta from arc
            delta_deg = math.degrees(arc_len / radius)
            chord_len = 2 * radius * math.sin(math.radians(delta_deg) / 2)

        # Derive azimuth_deg from chord bearing for downstream compatibility
        curve_azimuth = 0.0
        if chord_brg:
            cb_m = re.match(
                r'([NS])\s*(\d+)[°\-\s]*(\d{0,2})[\'′\-\s]*(\d{0,2})[\"″\-\s]*([EW])',
                chord_brg.strip().upper()
            )
            if cb_m:
                curve_azimuth = _bearing_to_azimuth(
                    cb_m.group(1), float(cb_m.group(2)),
                    float(cb_m.group(3) or 0), float(cb_m.group(4) or 0),
                    cb_m.group(5)
                )

        calls.append({
            "type":         "curve",
            "direction":    direction,
            "radius":       radius,
            "arc_length":   round(arc_len, 2),
            "delta_deg":    round(delta_deg, 4),
            "chord_length": round(chord_len, 2),
            "chord_bearing": chord_brg,
            "distance":     round(arc_len or chord_len, 2),
            "azimuth_deg":  round(curve_azimuth, 6),
            "bearing_raw":  m.group(0).strip(),
            "bearing_label": f"Curve {direction} R={radius:.1f}' Δ={delta_deg:.2f}°",
            "pos":          m.start(),
        })

    # 2. Standard bearing pattern
    for m in _BEARING_PAT.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        used_ranges.append((m.start(), m.end()))

        ns   = m.group(1).upper()
        deg  = float(m.group(2))
        mn   = float(m.group(3)) if m.group(3) else 0.0
        sec  = float(m.group(4)) if m.group(4) else 0.0
        ew   = m.group(5).upper()
        raw_dist = float(m.group(6).replace(',', ''))
        unit_str = (m.group(7) or "feet").strip().rstrip(".")
        dist = _to_feet(raw_dist, unit_str)

        azimuth = _bearing_to_azimuth(ns, deg, mn, sec, ew)

        # Build human-readable label
        label_parts = [ns, f"{int(deg)}°"]
        if mn or sec:
            label_parts.append(f"{int(mn):02d}'")
        if sec:
            label_parts.append(f'{int(sec):02d}"')
        label_parts.append(ew)
        label = " ".join(label_parts)

        # Unit annotation if not feet
        unit_label = unit_str if unit_str.lower() not in ("feet", "foot", "ft", "'") else ""

        calls.append({
            "type":          "straight",
            "bearing_label": label,
            "bearing_raw":   m.group(0).strip(),
            "distance":      dist,
            "raw_distance":  raw_dist,
            "unit":          unit_label or "ft",
            "azimuth_deg":   round(azimuth, 6),
            "ns": ns, "deg": deg, "min": mn, "sec": sec, "ew": ew,
            "pos":           m.start(),
        })

    # 3. Verbose bearing pattern (only if not already matched)
    for m in _BEARING_VERBOSE.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        used_ranges.append((m.start(), m.end()))

        ns   = m.group(1).upper()
        deg  = float(m.group(2))
        mn   = float(m.group(3)) if m.group(3) else 0.0
        sec  = float(m.group(4)) if m.group(4) else 0.0
        ew   = m.group(5).upper()
        raw_dist = float(m.group(6).replace(',', ''))
        unit_str = (m.group(7) or "feet").strip().rstrip(".")
        dist = _to_feet(raw_dist, unit_str)

        azimuth = _bearing_to_azimuth(ns, deg, mn, sec, ew)

        label_parts = [ns, f"{int(deg)}°"]
        if mn or sec:
            label_parts.append(f"{int(mn):02d}'")
        if sec:
            label_parts.append(f'{int(sec):02d}"')
        label_parts.append(ew)
        label = " ".join(label_parts)

        unit_label = unit_str if unit_str.lower() not in ("feet", "foot", "ft", "'") else ""

        calls.append({
            "type":          "straight",
            "bearing_label": label,
            "bearing_raw":   m.group(0).strip(),
            "distance":      dist,
            "raw_distance":  raw_dist,
            "unit":          unit_label or "ft",
            "azimuth_deg":   round(azimuth, 6),
            "ns": ns, "deg": deg, "min": mn, "sec": sec, "ew": ew,
            "pos":           m.start(),
        })

    # Sort by position in text for correct traversal order
    calls.sort(key=lambda c: c.get("pos", 0))

    # Strip 'pos' key — not needed downstream
    for c in calls:
        c.pop("pos", None)

    return calls


def calls_to_coords(calls: list[dict], start_x: float = 0.0, start_y: float = 0.0) -> list[tuple]:
    """Convert a list of metes-and-bounds calls to (x, y) coordinate pairs."""
    pts = [(start_x, start_y)]
    x, y = start_x, start_y
    for c in calls:
        if c.get("type") == "straight":
            az = math.radians(c.get("azimuth_deg", c.get("azimuth", 0)))
            dx = c["distance"] * math.sin(az)
            dy = c["distance"] * math.cos(az)
            x += dx
            y += dy
            pts.append((round(x, 6), round(y, 6)))
        elif c.get("type") == "curve":
            # Approximate curve as chord for simple plotting
            dist = c.get("chord_length", 0) or c.get("arc_length", 0)
            if dist and c.get("chord_bearing"):
                # Try to parse the chord bearing for direction
                cb_match = _BEARING_PAT.match(c["chord_bearing"])
                if cb_match:
                    ns  = cb_match.group(1).upper()
                    deg = float(cb_match.group(2))
                    mn  = float(cb_match.group(3)) if cb_match.group(3) else 0.0
                    sec = float(cb_match.group(4)) if cb_match.group(4) else 0.0
                    ew  = cb_match.group(5).upper()
                    az  = math.radians(_bearing_to_azimuth(ns, deg, mn, sec, ew))
                    x += dist * math.sin(az)
                    y += dist * math.cos(az)
                    pts.append((round(x, 6), round(y, 6)))
    return pts
