"""
helpers/legal_similarity.py — Legal description similarity search.

Pure-Python approach (no ML dependencies) for finding deeds/parcels
that describe the same or overlapping property. Uses multiple signal
types combined into a weighted score:

  1. TRS fingerprint matching (same Township/Range/Section = same area)
  2. Token-based Jaccard similarity on legal description text
  3. Cabinet reference overlap (shared cab refs = strong signal)
  4. Name overlap (shared grantor/grantee across deeds)
  5. Book/page proximity (nearby recordings = likely related)

Addresses the "Legal Description Similarity Search" gap from the
AI capabilities audit.
"""

import re
from collections import Counter


# ══════════════════════════════════════════════════════════════════════════════
# TEXT TOKENIZATION
# ══════════════════════════════════════════════════════════════════════════════

# Common words in legal descriptions that carry no discriminative value
_STOP_WORDS = frozenset({
    "the", "of", "and", "to", "a", "in", "for", "by", "at", "on", "an",
    "is", "or", "be", "as", "from", "with", "that", "said", "all",
    "being", "more", "less", "particularly", "described", "follows",
    "located", "situated", "lying", "county", "state", "new", "mexico",
    "thence", "along", "feet", "foot", "point", "beginning", "commencing",
    "boundary", "line", "also", "known", "record", "recorded", "filed",
    "per", "set", "found", "running", "true", "then", "accordance",
    "according", "northerly", "southerly", "easterly", "westerly",
})


def _tokenize_legal(text: str) -> set[str]:
    """Tokenize a legal description into a set of meaningful lowercased words.

    Strips stop words, short tokens (<3 chars), and pure numbers.
    Keeps survey-relevant terms like property names, subdivisions, lot/block refs.
    """
    if not text:
        return set()
    # Lowercase + split on non-alpha
    words = re.findall(r'[a-z]{3,}', text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def _jaccard(a: set, b: set) -> float:
    """Compute Jaccard similarity: |A ∩ B| / |A ∪ B|."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# TRS FINGERPRINTING
# ══════════════════════════════════════════════════════════════════════════════

_TRS_PATTERN = re.compile(
    r'T\.?\s*(\d+)\s*([NS])\s*'           # Township
    r'[,.\s]*R\.?\s*(\d+)\s*([EW])'        # Range
    r'(?:\s*(?:,?\s*S(?:ec(?:tion)?)?\.?\s*(\d+)))?',  # Optional Section
    re.IGNORECASE
)


def _extract_trs_fingerprints(text: str) -> list[str]:
    """Extract normalized TRS fingerprints from text.

    Returns list of strings like "T26N-R13E" or "T26N-R13E-S12".
    """
    if not text:
        return []
    fingerprints = []
    for m in _TRS_PATTERN.finditer(text):
        twp, twp_dir, rng, rng_dir = m.group(1), m.group(2).upper(), m.group(3), m.group(4).upper()
        fp = f"T{twp}{twp_dir}-R{rng}{rng_dir}"
        if m.group(5):
            fp += f"-S{m.group(5)}"
        if fp not in fingerprints:
            fingerprints.append(fp)
    return fingerprints


# ══════════════════════════════════════════════════════════════════════════════
# CABINET REFERENCE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

_CAB_PATTERN = re.compile(
    r'\bCAB(?:INET)?\.?\s*([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b',
    re.IGNORECASE
)


def _extract_cab_refs(text: str) -> set[str]:
    """Extract normalized cabinet references from text. Returns set like {"C-191", "B-45A"}."""
    if not text:
        return set()
    return {f"{m.group(1).upper()}-{m.group(2).upper()}" for m in _CAB_PATTERN.finditer(text)}


# ══════════════════════════════════════════════════════════════════════════════
# NAME EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _extract_names(text: str) -> set[str]:
    """Extract likely person/entity names from legal text.

    Captures "LASTNAME, FIRSTNAME" patterns and "Lands of NAME" patterns.
    Returns normalized lowercase name tokens.
    """
    if not text:
        return set()
    names = set()

    # "LASTNAME, FIRSTNAME" pattern
    for m in re.finditer(r'\b([A-Z][A-Za-z\'-]+)\s*,\s*([A-Z][A-Za-z\'-]+)', text):
        names.add(m.group(1).lower())
        names.add(m.group(2).lower())

    # "Lands of NAME" pattern
    for m in re.finditer(r'\blands?\s+of\s+(?:the\s+)?([A-Z][A-Za-z\'-]+)', text, re.I):
        names.add(m.group(1).lower())

    return names


# ══════════════════════════════════════════════════════════════════════════════
# LOT/BLOCK/TRACT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

_LOT_BLOCK_PATTERN = re.compile(
    r'\bLot\s+(\d+\w?)\s*[,.]?\s*(?:Block\s+(\d+\w?))?',
    re.IGNORECASE
)
_TRACT_PATTERN = re.compile(r'\bTract\s+(\d+\w?)', re.IGNORECASE)


def _extract_lot_block_tract(text: str) -> set[str]:
    """Extract lot/block/tract identifiers as normalized keys.

    Returns set like {"lot-3", "block-7", "tract-2A"}.
    """
    if not text:
        return set()
    refs = set()
    for m in _LOT_BLOCK_PATTERN.finditer(text):
        refs.add(f"lot-{m.group(1).upper()}")
        if m.group(2):
            refs.add(f"block-{m.group(2).upper()}")
    for m in _TRACT_PATTERN.finditer(text):
        refs.add(f"tract-{m.group(1).upper()}")
    return refs


# ══════════════════════════════════════════════════════════════════════════════
# SIMILARITY SCORING
# ══════════════════════════════════════════════════════════════════════════════

def compute_similarity(
    text_a: str,
    text_b: str,
    *,
    weight_trs: float = 40.0,
    weight_text: float = 25.0,
    weight_cab: float = 20.0,
    weight_names: float = 10.0,
    weight_lot_block: float = 5.0,
) -> dict:
    """Compute weighted similarity score between two legal descriptions.

    Returns:
      {
        "score": float (0-100),
        "components": {
          "trs_match": float (0-100),
          "text_similarity": float (0-100),
          "cab_overlap": float (0-100),
          "name_overlap": float (0-100),
          "lot_block_match": float (0-100),
        },
        "shared_trs": [str],
        "shared_cabs": [str],
        "shared_names": [str],
      }
    """
    # TRS fingerprint matching
    trs_a = set(_extract_trs_fingerprints(text_a))
    trs_b = set(_extract_trs_fingerprints(text_b))
    shared_trs = trs_a & trs_b
    trs_score = 100.0 if shared_trs else 0.0
    if trs_a and trs_b and not shared_trs:
        # Partial credit: same Township/Range but different Section
        tr_a = {t.rsplit("-S", 1)[0] for t in trs_a}
        tr_b = {t.rsplit("-S", 1)[0] for t in trs_b}
        if tr_a & tr_b:
            trs_score = 30.0  # Same T/R, different section

    # Text token similarity (Jaccard)
    tokens_a = _tokenize_legal(text_a)
    tokens_b = _tokenize_legal(text_b)
    text_score = _jaccard(tokens_a, tokens_b) * 100.0

    # Cabinet reference overlap
    cabs_a = _extract_cab_refs(text_a)
    cabs_b = _extract_cab_refs(text_b)
    shared_cabs = cabs_a & cabs_b
    cab_score = 100.0 if shared_cabs else 0.0

    # Name overlap
    names_a = _extract_names(text_a)
    names_b = _extract_names(text_b)
    shared_names = names_a & names_b
    if names_a and names_b:
        name_score = (len(shared_names) / min(len(names_a), len(names_b))) * 100.0
    else:
        name_score = 0.0

    # Lot/Block/Tract matching
    lbt_a = _extract_lot_block_tract(text_a)
    lbt_b = _extract_lot_block_tract(text_b)
    shared_lbt = lbt_a & lbt_b
    lbt_score = 100.0 if shared_lbt else 0.0

    # Weighted total
    total_weight = weight_trs + weight_text + weight_cab + weight_names + weight_lot_block
    weighted_score = (
        trs_score * weight_trs +
        text_score * weight_text +
        cab_score * weight_cab +
        name_score * weight_names +
        lbt_score * weight_lot_block
    ) / total_weight

    return {
        "score": round(weighted_score, 1),
        "components": {
            "trs_match":        round(trs_score, 1),
            "text_similarity":  round(text_score, 1),
            "cab_overlap":      round(cab_score, 1),
            "name_overlap":     round(name_score, 1),
            "lot_block_match":  round(lbt_score, 1),
        },
        "shared_trs":   sorted(shared_trs),
        "shared_cabs":  sorted(shared_cabs),
        "shared_names": sorted(shared_names),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

def search_similar_descriptions(
    query_text: str,
    parcel_index: list[dict],
    *,
    min_score: float = 20.0,
    limit: int = 20,
) -> list[dict]:
    """Search a parcel index for parcels with similar legal descriptions.

    Args:
        query_text: The legal description to search for matches.
        parcel_index: List of parcel dicts (from xml_processor index).
        min_score: Minimum similarity score (0-100) to include in results.
        limit: Maximum number of results.

    Returns a ranked list of dicts:
      [{ upc, owner, similarity: { score, components, shared_* }, ... }]
    """
    if not query_text or not parcel_index:
        return []

    results = []

    for parcel in parcel_index:
        # Build the parcel's "legal text" from all available fields
        parts = []
        if parcel.get("owner"):
            parts.append(parcel["owner"])
        if parcel.get("plat"):
            parts.append(parcel["plat"])

        # ArcGIS-enriched fields
        arc = parcel.get("arcgis", {})
        if arc:
            for key in ("legal_description", "subdivision", "trs"):
                val = arc.get(key, "")
                if val:
                    parts.append(val)

        # TRS from index
        if parcel.get("trs"):
            parts.append(parcel["trs"])

        parcel_text = " ".join(parts)
        if len(parcel_text.strip()) < 10:
            continue  # Skip parcels with no useful text

        sim = compute_similarity(query_text, parcel_text)
        if sim["score"] >= min_score:
            results.append({
                "upc":          parcel.get("upc", ""),
                "owner":        parcel.get("owner", ""),
                "plat":         parcel.get("plat", ""),
                "book":         parcel.get("book", ""),
                "page":         parcel.get("page", ""),
                "trs":          parcel.get("trs", "") or (arc.get("trs", "") if arc else ""),
                "similarity":   sim,
            })

    # Sort by score descending
    results.sort(key=lambda r: r["similarity"]["score"], reverse=True)
    return results[:limit]
