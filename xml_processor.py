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

# KML namespaces — Taos County files use 2.2; some KMZ files omit the namespace entirely
_KML_NAMESPACES = [
    "{http://www.opengis.net/kml/2.2}",
    "{http://www.opengis.net/kml/2.1}",
    "",  # no namespace
]
KML_NS = "{http://www.opengis.net/kml/2.2}"  # default, overridden per-file

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

def _detect_kml_ns(file_obj) -> str:
    """
    Peek at the first 2KB of a KML stream to detect the KML namespace.
    Returns the namespace string (e.g. '{http://www.opengis.net/kml/2.2}') or ''.
    """
    try:
        start = file_obj.read(2048)
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        if isinstance(start, bytes):
            start = start.decode('utf-8', errors='ignore')
        import re
        # Look for xmlns="..." or xmlns:kml="..."
        m = re.search(r'xmlns(?::\w+)?="(http://www\.opengis\.net/kml/[^"]+)"', start)
        if m:
            return '{' + m.group(1) + '}'
        # If <Placemark> appears without namespace prefix, use empty
        if '<Placemark' in start:
            return ''
    except Exception:
        pass
    return '{http://www.opengis.net/kml/2.2}'


def _sanitize_kml_bytes(data: bytes) -> bytes:
    """
    Fix KML files that use namespace prefixes without declaring them,
    causing an 'unbound prefix' ParseError in ElementTree.

    Strategy: strip all 'prefix:' from tag names (e.g. 'kml:Placemark' → 'Placemark')
    and remove xmlns:prefix declarations so the file parses as plain XML.
    This is safe for our use case since we only care about tag names, not namespaces.
    """
    import re as _re
    # Remove namespace declarations to avoid conflicts
    data = _re.sub(rb'\s+xmlns:\w+="[^"]*"', b'', data)
    data = _re.sub(rb"\s+xmlns:\w+='[^']*'", b'', data)
    # Strip prefix from opening/closing tags: <kml:Foo → <Foo, </kml:Foo → </Foo
    data = _re.sub(rb'<(\w+):(\w)', lambda m: b'<' + m.group(2), data)
    data = _re.sub(rb'</(\w+):(\w)', lambda m: b'</' + m.group(2), data)
    # Strip prefix from attributes: kml:attr="..." → attr="..."
    data = _re.sub(rb'(?<= )(\w+):(\w+)=', lambda m: m.group(2) + b'=', data)
    return data


def _parse_kml_stream(file_obj, source_name: str = "") -> list[dict]:
    """
    Parse a KML file stream and extract Placemark records.
    Uses iterparse for memory efficiency on large files.
    Auto-detects the KML namespace so it works for both namespaced and plain KML.
    Falls back to prefix-stripping for ArcGIS-style KML with unbound namespace prefixes.
    """
    import io as _io

    # Read all bytes so we can retry with sanitization if needed
    raw = file_obj.read() if hasattr(file_obj, 'read') else file_obj
    if not isinstance(raw, bytes):
        raw = raw.encode('utf-8')

    # Auto-detect namespace before parsing
    ns = _detect_kml_ns(_io.BytesIO(raw))
    placemark_tag = f"{ns}Placemark"

    def _do_parse(data: bytes, ptag: str) -> list[dict]:
        records = []
        try:
            context = ET.iterparse(_io.BytesIO(data), events=("end",))
            for event, elem in context:
                if elem.tag == ptag:
                    record = _extract_placemark(elem, source_name, ns)
                    if record:
                        records.append(record)
                    elem.clear()
        except ET.ParseError as e:
            raise e
        return records

    try:
        return _do_parse(raw, placemark_tag)
    except ET.ParseError as e:
        print(f"[xml] ParseError in {source_name}: {e} — retrying with prefix sanitization")
        try:
            sanitized = _sanitize_kml_bytes(raw)
            # After sanitization tags have no namespace prefix → use empty ns
            result = _do_parse(sanitized, "Placemark")
            if result:
                print(f"[xml] Sanitized parse succeeded: {len(result)} records from {source_name}")
            else:
                # Try to detect new namespace after sanitization
                ns2 = _detect_kml_ns(_io.BytesIO(sanitized))
                result = _do_parse(sanitized, f"{ns2}Placemark")
            return result
        except Exception as e2:
            print(f"[xml] Sanitized parse also failed for {source_name}: {e2}")
            return []



def _extract_placemark(elem, source_name: str, ns: str = "{http://www.opengis.net/kml/2.2}") -> Optional[dict]:
    """Extract a single Placemark's data into a record dict."""
    # Owner name — try both namespaced and plain tags
    name_el = elem.find(f"{ns}name") if ns else elem.find("name")
    if name_el is None and ns:
        name_el = elem.find("name")  # fallback: no namespace
    owner = name_el.text.strip() if name_el is not None and name_el.text else ""

    # Extended data (UPC, PLAT, BOOK, PAGE)
    upc = ""
    plat = ""
    book = ""
    page = ""

    ext_data = elem.find(f"{ns}ExtendedData") if ns else elem.find("ExtendedData")
    if ext_data is None and ns:
        ext_data = elem.find("ExtendedData")  # fallback: no namespace
    if ext_data is not None:
        for sd in ext_data.iter():
            if sd.tag.endswith("SimpleData") or sd.tag == "SimpleData":
                attr_name = sd.get("name", "")
                val = (sd.text or "").strip()
                if attr_name == "UPC":
                    upc = val
                elif attr_name == "OWNER" and not owner:
                    # Some KMZ files store owner in ExtendedData
                    owner = val
                elif attr_name == "PLAT":
                    plat = val
                elif attr_name == "BOOK":
                    book = val
                elif attr_name == "PAGE":
                    page = val

    # If still no owner, use UPC as a fallback label (don't skip the record)
    if not owner:
        if upc:
            owner = f"UPC {upc}"
        else:
            owner = "Unknown Owner"  # include in index so geometry isn't lost

    # Coordinates — extract centroid and full polygon from first polygon
    # Always try BOTH the namespaced and plain "coordinates" tags.
    # KMZ-embedded KML often uses plain coords even when a namespace is declared.
    coords_raw = []
    seen_coord_texts = set()
    for tag in ([f"{ns}coordinates", "coordinates"] if ns else ["coordinates"]):
        for coord_el in elem.iter(tag):
            txt = (coord_el.text or "").strip()
            if txt and txt not in seen_coord_texts:
                coords_raw.append(txt)
                seen_coord_texts.add(txt)
    centroid = None
    polygon = None

    if coords_raw:
        centroid = _compute_centroid(coords_raw[0])
        polygon = _parse_polygon_coords(coords_raw[0])

    # Skip records with literally no geometry at all (they'd be invisible)
    if not centroid and not polygon:
        return None

    # Parse cabinet references from PLAT field
    cab_refs = _parse_cab_refs_from_plat(plat)

    return {
        "owner":     owner,
        "upc":       upc,
        "plat":      plat,
        "book":      book,
        "page":      page,
        "centroid":  centroid,     # [lng, lat] or None
        "polygon":   polygon,     # [[lng, lat], ...] ring for Leaflet
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


def _parse_polygon_coords(coords_text: str) -> Optional[list]:
    """Parse a KML coordinates string into a [[lng, lat], ...] list."""
    try:
        points = []
        for chunk in coords_text.split():
            parts = chunk.split(",")
            if len(parts) >= 2:
                points.append([round(float(parts[0]), 6), round(float(parts[1]), 6)])
        return points if len(points) >= 3 else None
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
            all_files = zf.namelist()
            kml_names = [n for n in all_files if n.lower().endswith('.kml')]
            print(f"[xml] KMZ {source}: found files: {all_files[:10]}")
            print(f"[xml] KMZ {source}: KML files: {kml_names}")
            for kml_name in kml_names:
                with zf.open(kml_name) as kml_file:
                    # Read into BytesIO so we can seek for namespace detection
                    import io
                    kml_bytes = io.BytesIO(kml_file.read())
                    parsed = _parse_kml_stream(kml_bytes, f"{source}/{kml_name}")
                    print(f"[xml] KMZ {source}/{kml_name}: {len(parsed)} parcels")
                    records.extend(parsed)
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

    # Sort files so KMZ sources are processed LAST — they win de-duplication
    # (Parcel_Maintenance.kmz is preferred over TC_Parcels_2024.kml)
    raw_files = [f for f in xml_dir.iterdir() if f.suffix.lower() in (".kml", ".kmz")]
    kml_files = sorted([f for f in raw_files if f.suffix.lower() == ".kml"])
    kmz_files = sorted([f for f in raw_files if f.suffix.lower() == ".kmz"])
    files = kml_files + kmz_files   # KML first, KMZ last → KMZ wins
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

    # De-duplicate by UPC.
    # Priority order: KMZ records always beat KML records for the same UPC;
    # within the same format, prefer whichever has more plat data.
    by_upc = {}
    no_upc = []
    for r in all_records:
        if r["upc"]:
            existing = by_upc.get(r["upc"])
            if not existing:
                by_upc[r["upc"]] = r
            else:
                # KMZ source always beats KML source
                r_is_kmz = r.get("source", "").lower().endswith(".kmz") or \
                            ".kmz/" in r.get("source", "").lower()
                ex_is_kmz = existing.get("source", "").lower().endswith(".kmz") or \
                            ".kmz/" in existing.get("source", "").lower()
                if r_is_kmz and not ex_is_kmz:
                    by_upc[r["upc"]] = r   # KMZ beats KML unconditionally
                elif r_is_kmz == ex_is_kmz:
                    # Same format — prefer richer plat data
                    if len(r.get("plat", "")) > len(existing.get("plat", "")):
                        by_upc[r["upc"]] = r
                # else existing is KMZ, incoming is KML → keep existing
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
    print(f"[xml] Index built: {len(deduped)} parcels in {elapsed}s -> {idx_path}")

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

def search_parcels_in_index(
    index: dict,
    owner: str = "",
    upc: str = "",
    book: str = "",
    page: str = "",
    cabinet_ref: str = "",
    operator: str = "contains",
    limit: int = 50,
) -> list[dict]:
    """
    Search an already-loaded parcel index dict — no disk I/O.

    Same parameters as search_parcels() but accepts the index dict directly,
    which avoids redundant file reads when the caller already has it in memory.
    """
    if not index:
        return []
    parcels = index.get("parcels", [])
    return _filter_parcels(parcels, owner=owner, upc=upc, book=book, page=page,
                           cabinet_ref=cabinet_ref, operator=operator, limit=limit)


def _filter_parcels(
    parcels: list,
    owner: str = "",
    upc: str = "",
    book: str = "",
    page: str = "",
    cabinet_ref: str = "",
    operator: str = "contains",
    limit: int = 50,
) -> list[dict]:
    """Core filtering logic shared by search_parcels and search_parcels_in_index."""
    results = []
    owner_q    = owner.strip().upper()
    upc_q      = upc.strip()
    book_q     = book.strip()
    page_q     = page.strip()
    cab_q      = cabinet_ref.strip().upper()

    for p in parcels:
        match = True

        if owner_q:
            p_owner = p.get("owner", "").upper()
            if operator == "exact":
                if p_owner != owner_q:
                    match = False
            elif operator == "begins":
                if not p_owner.startswith(owner_q):
                    match = False
            else:
                if owner_q not in p_owner:
                    match = False

        if match and upc_q:
            if p.get("upc", "") != upc_q:
                match = False

        if match and book_q:
            if p.get("book", "") != book_q:
                match = False
        if match and page_q:
            if p.get("page", "") != page_q:
                match = False

        if match and cab_q:
            p_refs = p.get("cab_refs", [])
            if cab_q not in p_refs:
                if cab_q not in p.get("plat", "").upper():
                    match = False

        if match:
            results.append(p)
            if len(results) >= limit:
                break

    return results


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
    Search the parcel index (loads from disk).

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
    return _filter_parcels(
        idx.get("parcels", []),
        owner=owner, upc=upc, book=book, page=page,
        cabinet_ref=cabinet_ref, operator=operator, limit=limit,
    )


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


# ══════════════════════════════════════════════════════════════════════════════
# GEOJSON MAP EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def _simplify_ring(ring: list, max_pts: int = 50) -> list:
    """
    Reduce a polygon ring to at most max_pts vertices using simple stride sampling.
    Always keeps first and last point (so ring stays closed).
    Coordinates are rounded to 4 decimal places (~11m accuracy — fine for parcel outlines).
    """
    # Round coords first
    ring = [[round(c[0], 4), round(c[1], 4)] for c in ring]
    if len(ring) <= max_pts:
        return ring
    # Keep first, last, and evenly-spaced intermediate points
    step = (len(ring) - 1) / (max_pts - 1)
    indices = set([0, len(ring) - 1])
    for i in range(1, max_pts - 1):
        indices.add(round(i * step))
    return [ring[i] for i in sorted(indices)]


def get_map_geojson(
    survey_data_path: str,
    highlight_upcs: list[str] | None = None,
    max_features: int = 100000,
    source_filter: str = "",
) -> dict:
    """
    Return a GeoJSON FeatureCollection of all parcels in the index.

    Each Feature has geometry (Polygon) and properties:
      - owner, upc, book, page, plat, cab_refs_str, source
      - highlight: True if the parcel UPC is in highlight_upcs

    Parcels without polygon data are emitted as Point (centroid).
    Coordinates are simplified to 4 decimal places and polygons are
    capped at 50 vertices to keep the response size manageable.

    If source_filter is non-empty, only parcels from that source file
    are included (e.g. "TC_Parcels_2024_og.kml").
    """
    idx = load_index(survey_data_path)
    if not idx:
        return {"type": "FeatureCollection", "features": [], "sources": []}

    highlight_set = set(highlight_upcs or [])
    features = []

    # Collect all unique source file names for the layer selector
    all_sources: list[str] = []
    seen_sources: set[str] = set()
    for p in idx.get("parcels", []):
        src = p.get("source", "")
        if src and src not in seen_sources:
            seen_sources.add(src)
            all_sources.append(src)

    source_filter_lower = source_filter.strip().lower() if source_filter else ""

    for p in idx.get("parcels", [])[:max_features]:
        # Apply source filter if specified
        if source_filter_lower:
            p_source = (p.get("source", "") or "").lower()
            if source_filter_lower not in p_source:
                continue

        upc      = p.get("upc", "")
        owner    = p.get("owner", "")
        polygon  = p.get("polygon")
        centroid = p.get("centroid")

        # Build lean properties — omit empty strings to reduce JSON size
        props: dict = {"owner": owner, "upc": upc}
        for key in ("book", "page", "plat"):
            val = p.get(key, "")
            if val:
                props[key] = val
        cab_refs_str = ", ".join(p.get("cab_refs", []))
        if cab_refs_str:
            props["cab_refs_str"] = cab_refs_str
        if upc and upc in highlight_set:
            props["highlight"] = True
        # Include source so the frontend knows which file this parcel came from
        p_source = p.get("source", "")
        if p_source:
            props["source"] = p_source

        if polygon and len(polygon) >= 3:
            ring = polygon if polygon[0] == polygon[-1] else polygon + [polygon[0]]
            ring = _simplify_ring(ring, max_pts=50)
            geometry = {"type": "Polygon", "coordinates": [ring]}
        elif centroid:
            geometry = {"type": "Point",
                        "coordinates": [round(centroid[0], 4), round(centroid[1], 4)]}
        else:
            continue  # skip parcels with no geometry

        features.append({
            "type":       "Feature",
            "geometry":   geometry,
            "properties": props,
        })

    return {
        "type":     "FeatureCollection",
        "features": features,
        "sources":  all_sources,
    }

