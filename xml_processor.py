"""
xml_processor.py  — KML / KMZ Parcel Data Engine
=================================================
Parses Taos County KML / KMZ parcel files and builds a fast-search JSON index.

Key features:
  • Memory-efficient SAX-style parsing (xml.etree.ElementTree.iterparse)
  • KMZ support via zipfile
  • JSON index with owner name, UPC, book/page, plat ref, centroid, cabinet refs
  • Fast in-memory search: owner name, UPC, book/page, cabinet cross-reference
  • Polygon coordinate extraction for individual parcel detail
"""

import json
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# KML namespace
KML_NS = "{http://www.opengis.net/kml/2.2}"

# ── Index file location ────────────────────────────────────────────────────────

def _default_xml_dir(survey_data_path: str) -> Path:
    return Path(survey_data_path) / "XML"


def _index_path(survey_data_path: str) -> Path:
    return _default_xml_dir(survey_data_path) / "parcel_index.json"


# ── KML / KMZ file discovery ──────────────────────────────────────────────────

def discover_xml_files(survey_data_path: str) -> list[dict]:
    """Find all KML / KMZ files in the XML folder."""
    xml_dir = _default_xml_dir(survey_data_path)
    if not xml_dir.exists():
        return []

    files = []
    for f in sorted(xml_dir.iterdir()):
        if f.suffix.lower() in (".kml", ".kmz"):
            files.append({
                "name":    f.name,
                "path":    str(f),
                "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                "format":  f.suffix.lower().lstrip("."),
            })
    return files


# ── KML parsing  (memory-efficient iterparse) ─────────────────────────────────

def _parse_kml_stream(file_obj, source_name: str = "") -> list[dict]:
    """
    Parse a KML file stream and extract Placemark records.
    Uses iterparse for memory efficiency on large files.
    """
    records = []
    context = ET.iterparse(file_obj, events=("end",))

    for event, elem in context:
        if elem.tag == f"{KML_NS}Placemark":
            record = _extract_placemark(elem, source_name)
            if record:
                records.append(record)
            # Free memory immediately
            elem.clear()

    return records


def _extract_placemark(elem, source_name: str) -> Optional[dict]:
    """Extract a single Placemark's data into a record dict."""
    # Owner name
    name_el = elem.find(f"{KML_NS}name")
    owner = name_el.text.strip() if name_el is not None and name_el.text else ""
    if not owner:
        return None

    # Extended data (UPC, PLAT, BOOK, PAGE)
    upc = ""
    plat = ""
    book = ""
    page = ""

    ext_data = elem.find(f"{KML_NS}ExtendedData")
    if ext_data is not None:
        for sd in ext_data.iter():
            if sd.tag.endswith("SimpleData") or sd.tag == "SimpleData":
                attr_name = sd.get("name", "")
                val = (sd.text or "").strip()
                if attr_name == "UPC":
                    upc = val
                elif attr_name == "PLAT":
                    plat = val
                elif attr_name == "BOOK":
                    book = val
                elif attr_name == "PAGE":
                    page = val

    # Coordinates — extract centroid from first polygon
    centroid = None
    coords_raw = []
    for coord_el in elem.iter(f"{KML_NS}coordinates"):
        if coord_el.text:
            coords_raw.append(coord_el.text.strip())

    if coords_raw:
        centroid = _compute_centroid(coords_raw[0])

    # Parse cabinet references from PLAT field
    cab_refs = _parse_cab_refs_from_plat(plat)

    return {
        "owner":     owner,
        "upc":       upc,
        "plat":      plat,
        "book":      book,
        "page":      page,
        "centroid":  centroid,     # [lng, lat] or None
        "cab_refs":  cab_refs,    # ["C-191A", "E-139-A", ...]
        "source":    source_name,
    }


def _compute_centroid(coords_text: str) -> Optional[list]:
    """Compute centroid [lng, lat] from a KML coordinates string."""
    try:
        points = []
        for chunk in coords_text.split():
            parts = chunk.split(",")
            if len(parts) >= 2:
                lng = float(parts[0])
                lat = float(parts[1])
                points.append((lng, lat))
        if not points:
            return None
        avg_lng = sum(p[0] for p in points) / len(points)
        avg_lat = sum(p[1] for p in points) / len(points)
        return [round(avg_lng, 8), round(avg_lat, 8)]
    except (ValueError, IndexError):
        return None


def _parse_cab_refs_from_plat(plat: str) -> list[str]:
    """Extract cabinet references from the PLAT field.

    Handles both common formats found in Taos County KML data:
      - ""CAB. C-191A"" / ""CAB C-84""  (explicit CAB prefix)
      - ""C-191-A ADELA RAEL"" / ""C-84""  (short form, no CAB prefix)

    Examples::
      ""C-191-A ADELA RAEL""  -> [""C-191A""]
      ""CAB C-84""            -> [""C-84""]
    """
    if not plat:
        return []
    refs = []
    seen = set()

    # Pattern 1: explicit CAB prefix  e.g. CAB. C-191A or CAB C-84
    pat1 = re.compile(r'\bCAB\.?\s*([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b', re.I)
    for m in pat1.finditer(plat):
        cab = m.group(1).upper()
        doc = m.group(2).upper()
        key = f"{cab}-{doc}"
        if key not in seen:
            seen.add(key)
            refs.append(key)

    # Pattern 2: short form  e.g. C-191-A or C-84 (no CAB prefix)
    # Only match 1-4 digit doc numbers to avoid false positives
    pat2 = re.compile(
        r'(?<![A-Za-z0-9])([A-Fa-f])-(\d{1,4})(?:-([A-Za-z]))?(?=[\s,;.]|$)',
        re.I
    )
    for m in pat2.finditer(plat):
        cab = m.group(1).upper()
        doc_num = m.group(2)
        doc_suf = (m.group(3) or '').upper()
        doc = doc_num + doc_suf
        key = f"{cab}-{doc}"
        if key not in seen:
            seen.add(key)
            refs.append(key)

    return refs


# ── KMZ handling ──────────────────────────────────────────────────────────────

def _parse_kmz_file(kmz_path: str) -> list[dict]:
    """Open a KMZ file, find the KML inside, and parse it."""
    records = []
    source = Path(kmz_path).name
    try:
        with zipfile.ZipFile(kmz_path, 'r') as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith('.kml')]
            for kml_name in kml_names:
                with zf.open(kml_name) as kml_file:
                    records.extend(_parse_kml_stream(kml_file, source))
    except Exception as e:
        print(f"[xml] Error reading KMZ {kmz_path}: {e}")
    return records


# ── Full polygon extraction (for detail view — not stored in index) ──────────

def extract_parcel_polygon(survey_data_path: str, upc: str) -> Optional[list]:
    """
    Given a UPC, re-scan the KML to extract the full polygon coordinates.
    Returns list of [lng, lat] pairs, or None.
    """
    xml_dir = _default_xml_dir(survey_data_path)
    if not xml_dir.exists():
        return None

    for f in xml_dir.iterdir():
        if f.suffix.lower() == ".kml":
            polygon = _find_polygon_in_kml(str(f), upc)
            if polygon:
                return polygon
        elif f.suffix.lower() == ".kmz":
            polygon = _find_polygon_in_kmz(str(f), upc)
            if polygon:
                return polygon
    return None


def _find_polygon_in_kml(kml_path: str, target_upc: str) -> Optional[list]:
    """Stream through a KML looking for a specific UPC's polygon."""
    try:
        context = ET.iterparse(kml_path, events=("end",))
        for event, elem in context:
            if elem.tag == f"{KML_NS}Placemark":
                # Check if this Placemark has the target UPC
                ext = elem.find(f"{KML_NS}ExtendedData")
                if ext is not None:
                    for sd in ext.iter():
                        if (sd.tag.endswith("SimpleData") or sd.tag == "SimpleData"):
                            if sd.get("name") == "UPC" and (sd.text or "").strip() == target_upc:
                                # Found it — extract coordinates
                                return _extract_all_coords(elem)
                elem.clear()
    except Exception:
        pass
    return None


def _find_polygon_in_kmz(kmz_path: str, target_upc: str) -> Optional[list]:
    """Search inside a KMZ for a specific UPC's polygon."""
    try:
        with zipfile.ZipFile(kmz_path, 'r') as zf:
            for name in zf.namelist():
                if name.lower().endswith('.kml'):
                    with zf.open(name) as kml_file:
                        context = ET.iterparse(kml_file, events=("end",))
                        for event, elem in context:
                            if elem.tag == f"{KML_NS}Placemark":
                                ext = elem.find(f"{KML_NS}ExtendedData")
                                if ext is not None:
                                    for sd in ext.iter():
                                        if (sd.tag.endswith("SimpleData") or sd.tag == "SimpleData"):
                                            if sd.get("name") == "UPC" and (sd.text or "").strip() == target_upc:
                                                return _extract_all_coords(elem)
                                elem.clear()
    except Exception:
        pass
    return None


def _extract_all_coords(placemark_elem) -> list:
    """Extract all coordinate points from a Placemark's geometry."""
    points = []
    for coord_el in placemark_elem.iter(f"{KML_NS}coordinates"):
        if coord_el.text:
            for chunk in coord_el.text.strip().split():
                parts = chunk.split(",")
                if len(parts) >= 2:
                    try:
                        points.append([float(parts[0]), float(parts[1])])
                    except ValueError:
                        pass
    return points if points else None


# ══════════════════════════════════════════════════════════════════════════════
# INDEX: Build & Load
# ══════════════════════════════════════════════════════════════════════════════

def build_index(survey_data_path: str, progress_callback=None) -> dict:
    """
    Parse all KML/KMZ files in the XML folder and build a JSON index.

    Returns summary dict: {total, sources, elapsed_sec, index_path}
    """
    xml_dir = _default_xml_dir(survey_data_path)
    if not xml_dir.exists():
        return {"total": 0, "error": "XML folder not found"}

    t0 = time.time()
    all_records = []

    files = [f for f in xml_dir.iterdir() if f.suffix.lower() in (".kml", ".kmz")]
    sources = []

    for fi, f in enumerate(files):
        source_name = f.name
        if progress_callback:
            progress_callback(fi, len(files), f"Parsing {source_name}...")

        if f.suffix.lower() == ".kml":
            with open(f, "rb") as fobj:
                records = _parse_kml_stream(fobj, source_name)
        else:
            records = _parse_kmz_file(str(f))

        sources.append({"file": source_name, "records": len(records)})
        all_records.extend(records)
        print(f"[xml] Parsed {source_name}: {len(records)} parcels")

    # De-duplicate by UPC (prefer record with more data)
    by_upc = {}
    no_upc = []
    for r in all_records:
        if r["upc"]:
            existing = by_upc.get(r["upc"])
            if not existing or len(r.get("plat", "")) > len(existing.get("plat", "")):
                by_upc[r["upc"]] = r
        else:
            no_upc.append(r)

    deduped = list(by_upc.values()) + no_upc

    # Build search-optimized index
    index = {
        "version":    2,
        "built_at":   time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total":      len(deduped),
        "sources":    sources,
        "parcels":    deduped,
    }

    # Save
    idx_path = _index_path(survey_data_path)
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    elapsed = round(time.time() - t0, 1)
    print(f"[xml] Index built: {len(deduped)} parcels in {elapsed}s → {idx_path}")

    return {
        "total":      len(deduped),
        "sources":    sources,
        "elapsed_sec": elapsed,
        "index_path": str(idx_path),
    }


_cached_index: Optional[dict] = None
_cached_index_mtime: float = 0


def load_index(survey_data_path: str, force: bool = False) -> Optional[dict]:
    """Load the parcel index from disk (cached in memory)."""
    global _cached_index, _cached_index_mtime

    idx_path = _index_path(survey_data_path)
    if not idx_path.exists():
        return None

    mtime = idx_path.stat().st_mtime
    if _cached_index and not force and mtime == _cached_index_mtime:
        return _cached_index

    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        _cached_index = data
        _cached_index_mtime = mtime
        return data
    except Exception as e:
        print(f"[xml] Failed to load index: {e}")
        return None


def index_status(survey_data_path: str) -> dict:
    """Return status info about the index."""
    idx_path = _index_path(survey_data_path)
    xml_files = discover_xml_files(survey_data_path)

    if not idx_path.exists():
        return {
            "exists":    False,
            "total":     0,
            "built_at":  None,
            "xml_files": xml_files,
        }

    idx = load_index(survey_data_path)
    return {
        "exists":    True,
        "total":     idx.get("total", 0) if idx else 0,
        "built_at":  idx.get("built_at", "") if idx else "",
        "sources":   idx.get("sources", []) if idx else [],
        "xml_files": xml_files,
        "size_mb":   round(idx_path.stat().st_size / (1024 * 1024), 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ══════════════════════════════════════════════════════════════════════════════

def search_parcels(
    survey_data_path: str,
    owner: str = "",
    upc: str = "",
    book: str = "",
    page: str = "",
    cabinet_ref: str = "",
    operator: str = "contains",
    limit: int = 50,
) -> list[dict]:
    """
    Search the parcel index.

    Parameters:
        owner:       Owner name to search
        upc:         UPC code (exact match)
        book/page:   Book and/or page number
        cabinet_ref: Cabinet reference like "C-191A"
        operator:    "contains", "begins", "exact"
        limit:       Max results

    Returns list of matching parcel records.
    """
    idx = load_index(survey_data_path)
    if not idx:
        return []

    parcels = idx.get("parcels", [])
    results = []

    owner_q     = owner.strip().upper()
    upc_q       = upc.strip()
    book_q      = book.strip()
    page_q      = page.strip()
    cab_q       = cabinet_ref.strip().upper()

    for p in parcels:
        match = True

        # Owner name match
        if owner_q:
            p_owner = p.get("owner", "").upper()
            if operator == "exact":
                if p_owner != owner_q:
                    match = False
            elif operator == "begins":
                if not p_owner.startswith(owner_q):
                    match = False
            else:  # contains
                if owner_q not in p_owner:
                    match = False

        # UPC exact match
        if match and upc_q:
            if p.get("upc", "") != upc_q:
                match = False

        # Book/page match
        if match and book_q:
            if p.get("book", "") != book_q:
                match = False
        if match and page_q:
            if p.get("page", "") != page_q:
                match = False

        # Cabinet reference match
        if match and cab_q:
            p_refs = p.get("cab_refs", [])
            if cab_q not in p_refs:
                # Also try substring match in the PLAT field
                if cab_q not in p.get("plat", "").upper():
                    match = False

        if match:
            results.append(p)
            if len(results) >= limit:
                break

    return results


def cross_reference_deed(survey_data_path: str, deed_detail: dict) -> list[dict]:
    """
    Given a deed detail dict (from 1stNMTitle), find matching parcels.

    Cross-references via:
      1. Grantor/Grantee name match
      2. Book/page (Location field like "M568-482")
      3. Cabinet references in the deed
    """
    idx = load_index(survey_data_path)
    if not idx:
        return []

    results = []
    seen_upcs = set()

    # Strategy 1: Name match
    for name_field in ["Grantor", "Grantee"]:
        name = deed_detail.get(name_field, "")
        if name:
            last_name = name.split(",")[0].strip().upper()
            if last_name and len(last_name) >= 3:
                hits = search_parcels(survey_data_path, owner=last_name, operator="contains", limit=10)
                for h in hits:
                    if h.get("upc") and h["upc"] not in seen_upcs:
                        h["_match_reason"] = f"Name match: {name_field}"
                        results.append(h)
                        seen_upcs.add(h["upc"])

    # Strategy 2: Book/page from Location field
    location = deed_detail.get("Location", "")
    if location:
        m = re.match(r'^[A-Za-z]?(\d+)-(\d+)', location.strip())
        if m:
            book_num = m.group(1)
            page_num = m.group(2)
            hits = search_parcels(survey_data_path, book=book_num, page=page_num, limit=5)
            for h in hits:
                if h.get("upc") and h["upc"] not in seen_upcs:
                    h["_match_reason"] = f"Book/Page: {book_num}-{page_num}"
                    results.append(h)
                    seen_upcs.add(h["upc"])

    # Strategy 3: Cabinet references
    cab_pat = re.compile(r'\bCAB(?:INET)?\.?\s*([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b', re.I)
    for val in deed_detail.values():
        if not isinstance(val, str):
            continue
        for cm in cab_pat.finditer(val):
            cab_key = f"{cm.group(1).upper()}-{cm.group(2).upper()}"
            hits = search_parcels(survey_data_path, cabinet_ref=cab_key, limit=5)
            for h in hits:
                if h.get("upc") and h["upc"] not in seen_upcs:
                    h["_match_reason"] = f"Cabinet: {cab_key}"
                    results.append(h)
                    seen_upcs.add(h["upc"])

    return results[:30]  # cap total results
