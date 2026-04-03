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

try:
    import requests as _requests
except ImportError:
    _requests = None  # enrichment disabled if requests not installed

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
                raw_name = sd.get("name", "")
                # Strip namespace prefixes like "Taos_County:" to normalize field names
                attr_name = raw_name.split(":")[-1].strip().upper() if ":" in raw_name else raw_name.upper()
                val = (sd.text or "").strip()
                if attr_name == "UPC":
                    upc = val
                elif attr_name in ("OWNER", "OWNERALL", "OWNER_ALL"):
                    # Override if we have no owner OR current owner is just a numeric ID
                    if not owner or owner.isdigit():
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
    """Stream through a KML looking for a specific UPC's polygon.
    Auto-detects the KML namespace per file."""
    import io as _io
    try:
        with open(kml_path, 'rb') as f:
            raw = f.read()
        ns = _detect_kml_ns(_io.BytesIO(raw))
        placemark_tag = f"{ns}Placemark"
        ext_tag = f"{ns}ExtendedData"

        context = ET.iterparse(_io.BytesIO(raw), events=("end",))
        for event, elem in context:
            if elem.tag == placemark_tag:
                # Check if this Placemark has the target UPC
                ext = elem.find(ext_tag)
                if ext is None and ns:
                    ext = elem.find("ExtendedData")
                if ext is not None:
                    for sd in ext.iter():
                        if (sd.tag.endswith("SimpleData") or sd.tag == "SimpleData"):
                            sd_name = sd.get("name", "")
                            sd_key = sd_name.split(":")[-1].strip().upper() if ":" in sd_name else sd_name.upper()
                            if sd_key == "UPC" and (sd.text or "").strip() == target_upc:
                                return _extract_all_coords(elem, ns)
                elem.clear()
    except Exception:
        pass
    return None


def _find_polygon_in_kmz(kmz_path: str, target_upc: str) -> Optional[list]:
    """Search inside a KMZ for a specific UPC's polygon.
    Auto-detects the KML namespace per embedded file."""
    import io as _io
    try:
        with zipfile.ZipFile(kmz_path, 'r') as zf:
            for name in zf.namelist():
                if name.lower().endswith('.kml'):
                    with zf.open(name) as kml_file:
                        raw = kml_file.read()
                    ns = _detect_kml_ns(_io.BytesIO(raw))
                    placemark_tag = f"{ns}Placemark"
                    ext_tag = f"{ns}ExtendedData"

                    context = ET.iterparse(_io.BytesIO(raw), events=("end",))
                    for event, elem in context:
                        if elem.tag == placemark_tag:
                            ext = elem.find(ext_tag)
                            if ext is None and ns:
                                ext = elem.find("ExtendedData")
                            if ext is not None:
                                for sd in ext.iter():
                                    if (sd.tag.endswith("SimpleData") or sd.tag == "SimpleData"):
                                        sd_name = sd.get("name", "")
                                        sd_key = sd_name.split(":")[-1].strip().upper() if ":" in sd_name else sd_name.upper()
                                        if sd_key == "UPC" and (sd.text or "").strip() == target_upc:
                                            return _extract_all_coords(elem, ns)
                            elem.clear()
    except Exception:
        pass
    return None


def _extract_all_coords(placemark_elem, ns: str = "") -> list:
    """Extract all coordinate points from a Placemark's geometry.
    Searches both namespaced and plain 'coordinates' tags."""
    points = []
    tags_to_try = [f"{ns}coordinates", "coordinates"] if ns else ["coordinates"]
    seen = set()
    for tag in tags_to_try:
        for coord_el in placemark_elem.iter(tag):
            txt = (coord_el.text or "").strip()
            if txt and txt not in seen:
                seen.add(txt)
                for chunk in txt.split():
                    parts = chunk.split(",")
                    if len(parts) >= 2:
                        try:
                            points.append([float(parts[0]), float(parts[1])])
                        except ValueError:
                            pass
    return points if points else None


# ══════════════════════════════════════════════════════════════════════════════
# ARCGIS ENRICHMENT
# ══════════════════════════════════════════════════════════════════════════════

# NM OSE ArcGIS Parcel Service — Taos County is layer 29
_ARCGIS_TAOS_QUERY_URL = (
    "https://gis.ose.nm.gov/server_s/rest/services/"
    "Parcels/County_Parcels_2025/MapServer/29/query"
)

# Fields to pull for enrichment (lean — skip geometry and address)
_ARCGIS_ENRICH_FIELDS = (
    "UPC,Township,TownshipDirection,Range,RangeDirection,Section,"
    "PLSSID,LegalDescription,Subdivision,ZoningCode,ZoningDescription,"
    "LandUseCode,LandUseDescription,NeighborhoodCode,NeighborhoodDescription,"
    "OwnerAll,SitusAddressAll,LandArea"
)

_ARCGIS_BATCH_SIZE = 40      # UPCs per query (keep URL length manageable)
_ARCGIS_BATCH_DELAY = 0.25   # seconds between batches


def _build_trs_string(attrs: dict) -> str:
    """Assemble a human-readable TRS string from ArcGIS attribute fields."""
    twp = (attrs.get("Township") or "").strip()
    twp_dir = (attrs.get("TownshipDirection") or "").strip()
    rng = (attrs.get("Range") or "").strip()
    rng_dir = (attrs.get("RangeDirection") or "").strip()
    sec = (attrs.get("Section") or "").strip()
    if not twp or not rng:
        return ""
    trs = f"T{twp}{twp_dir} R{rng}{rng_dir}"
    if sec:
        trs += f" Sec {sec}"
    return trs


def enrich_index_with_arcgis(
    index: dict,
    progress_callback=None,
) -> dict:
    """Batch-query ArcGIS for parcels with UPCs and merge enrichment data.

    Modifies the index dict in-place and returns enrichment stats.
    """
    if _requests is None:
        return {"enriched": 0, "error": "requests library not available"}

    parcels = index.get("parcels", [])
    # Collect parcels with UPCs that haven't been enriched yet
    to_enrich = []
    for p in parcels:
        if p.get("upc") and not p.get("arcgis"):
            to_enrich.append(p)

    if not to_enrich:
        return {"enriched": 0, "skipped": "all parcels already enriched or no UPCs"}

    total = len(to_enrich)
    enriched_count = 0
    error_count = 0
    t0 = time.time()

    # Process in batches
    for batch_start in range(0, total, _ARCGIS_BATCH_SIZE):
        batch = to_enrich[batch_start:batch_start + _ARCGIS_BATCH_SIZE]
        upcs = [p["upc"] for p in batch]

        if progress_callback:
            pct = round(batch_start / total * 100)
            progress_callback(
                batch_start, total,
                f"Enriching parcels from ArcGIS… {pct}% ({batch_start}/{total})"
            )

        # Build OR query: UPC IN ('xxx','yyy',...)
        upc_list = ",".join(f"'{u}'" for u in upcs)
        where_clause = f"UPC IN ({upc_list})"

        try:
            resp = _requests.get(
                _ARCGIS_TAOS_QUERY_URL,
                params={
                    "where":          where_clause,
                    "outFields":      _ARCGIS_ENRICH_FIELDS,
                    "returnGeometry": "false",
                    "f":              "json",
                },
                headers={"User-Agent": "DeedPlatHelper/1.0"},
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"[arcgis-enrich] Batch HTTP {resp.status_code} at offset {batch_start}")
                error_count += len(batch)
                continue

            data = resp.json()
            features = data.get("features", [])

            # Build lookup by UPC
            by_upc = {}
            for feat in features:
                a = feat.get("attributes", {})
                fupc = (a.get("UPC") or "").strip()
                if fupc:
                    by_upc[fupc] = a

            # Merge into parcel records
            for p in batch:
                a = by_upc.get(p["upc"])
                if not a:
                    continue
                trs = _build_trs_string(a)
                p["arcgis"] = {
                    "trs":              trs,
                    "plssid":           (a.get("PLSSID") or "").strip(),
                    "legal_desc":       (a.get("LegalDescription") or "").strip()[:500],
                    "subdivision":      (a.get("Subdivision") or "").strip(),
                    "zoning":           (a.get("ZoningDescription") or "").strip(),
                    "zoning_code":      (a.get("ZoningCode") or "").strip(),
                    "land_use":         (a.get("LandUseDescription") or "").strip(),
                    "land_use_code":    (a.get("LandUseCode") or "").strip(),
                    "neighborhood":     (a.get("NeighborhoodDescription") or "").strip(),
                    "neighborhood_code":(a.get("NeighborhoodCode") or "").strip(),
                    "owner_official":   (a.get("OwnerAll") or "").strip(),
                    "situs":            (a.get("SitusAddressAll") or "").strip(),
                    "land_area":        a.get("LandArea") or 0,
                }
                # Promote TRS to top-level for fast search filtering
                if trs:
                    p["trs"] = trs
                enriched_count += 1

        except Exception as e:
            print(f"[arcgis-enrich] Batch error at offset {batch_start}: {e}")
            error_count += len(batch)

        # Rate-limit between batches
        if batch_start + _ARCGIS_BATCH_SIZE < total:
            time.sleep(_ARCGIS_BATCH_DELAY)

    elapsed = round(time.time() - t0, 1)
    index["arcgis_enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    index["arcgis_enriched_count"] = enriched_count

    print(f"[arcgis-enrich] Done: {enriched_count}/{total} enriched, "
          f"{error_count} errors, {elapsed}s")

    return {
        "enriched":   enriched_count,
        "total":      total,
        "errors":     error_count,
        "elapsed_sec": elapsed,
    }


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
        "version":    3,
        "built_at":   time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total":      len(deduped),
        "sources":    sources,
        "parcels":    deduped,
    }

    # ── ArcGIS enrichment (adds TRS, legal desc, zoning, etc.) ────────────
    if _requests is not None:
        print(f"[xml] Starting ArcGIS enrichment for {len(deduped)} parcels...",
              flush=True)
        enrich_stats = enrich_index_with_arcgis(index, progress_callback)
        print(f"[xml] ArcGIS enrichment: {enrich_stats}", flush=True)
    else:
        print("[xml] Skipping ArcGIS enrichment (requests library not available)",
              flush=True)

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
        "arcgis_enriched": index.get("arcgis_enriched_count", 0),
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
        "version":   idx.get("version", 1) if idx else 1,
        "arcgis_enriched_at":    idx.get("arcgis_enriched_at") if idx else None,
        "arcgis_enriched_count": idx.get("arcgis_enriched_count", 0) if idx else 0,
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
    trs: str = "",
    subdivision: str = "",
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
                           cabinet_ref=cabinet_ref, trs=trs, subdivision=subdivision,
                           operator=operator, limit=limit)


def _filter_parcels(
    parcels: list,
    owner: str = "",
    upc: str = "",
    book: str = "",
    page: str = "",
    cabinet_ref: str = "",
    trs: str = "",
    subdivision: str = "",
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
    trs_q      = trs.strip().upper()
    subdiv_q   = subdivision.strip().upper()

    for p in parcels:
        match = True

        if owner_q:
            p_owner = p.get("owner", "").upper()
            # Also search against ArcGIS official owner
            p_owner_arc = (p.get("arcgis", {}).get("owner_official", "") or "").upper()
            combined_owner = p_owner + " " + p_owner_arc
            if operator == "exact":
                if p_owner != owner_q and p_owner_arc != owner_q:
                    match = False
            elif operator == "begins":
                if not p_owner.startswith(owner_q) and not p_owner_arc.startswith(owner_q):
                    match = False
            else:
                if owner_q not in combined_owner:
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

        if match and trs_q:
            p_trs = (p.get("trs", "") or "").upper()
            arc_trs = (p.get("arcgis", {}).get("trs", "") or "").upper()
            if trs_q not in p_trs and trs_q not in arc_trs:
                match = False

        if match and subdiv_q:
            p_subdiv = (p.get("arcgis", {}).get("subdivision", "") or "").upper()
            if subdiv_q not in p_subdiv:
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
    trs: str = "",
    subdivision: str = "",
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
        trs:         Township/Range/Section (partial match)
        subdivision: Subdivision name (partial match)
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
        cabinet_ref=cabinet_ref, trs=trs, subdivision=subdivision,
        operator=operator, limit=limit,
    )


def cross_reference_deed(survey_data_path: str, deed_detail: dict,
                         client_upc: str = "") -> list[dict]:
    """
    Given a deed detail dict (from 1stNMTitle), find matching parcels.

    Cross-references via:
      0. client_upc — the parcel selected on the map picker (highest priority)
      1. Grantor/Grantee name match
      2. Book/page (Location field like "M568-482")
      3. Cabinet references in the deed

    Results are sorted by relevance: parcels matching on multiple criteria
    rank highest (e.g. name + book/page = very likely the client's parcel).
    """
    idx = load_index(survey_data_path)
    if not idx:
        return []

    # Track per-UPC: which strategies matched and the first match reason
    # score_map[upc] = {"score": int, "reasons": [str], "parcel": dict}
    score_map: dict[str, dict] = {}

    def _record_hit(parcel: dict, reason: str, score: int):
        """Record a parcel hit, accumulating score for multi-match parcels."""
        upc = parcel.get("upc", "")
        if not upc:
            return
        if upc in score_map:
            # Already seen — boost score and append reason
            entry = score_map[upc]
            entry["score"] += score
            entry["reasons"].append(reason)
        else:
            parcel["_match_reason"] = reason
            score_map[upc] = {
                "score": score,
                "reasons": [reason],
                "parcel": parcel,
            }

    # Strategy 0: Map-picked parcel (score 100 — user explicitly selected it)
    client_upc_clean = (client_upc or "").strip()
    if client_upc_clean:
        hits = search_parcels(survey_data_path, upc=client_upc_clean, limit=1)
        for h in hits:
            _record_hit(h, "Map selection", 100)
        print(f"[xref] Strategy 0: client_upc={client_upc_clean!r} -> {len(hits)} hit(s)", flush=True)

    # Strategy 1: Name match (score 10 — common, lower confidence alone)
    for name_field in ["Grantor", "Grantee"]:
        name = deed_detail.get(name_field, "")
        if name:
            last_name = name.split(",")[0].strip().upper()
            if last_name and len(last_name) >= 3:
                hits = search_parcels(survey_data_path, owner=last_name, operator="contains", limit=10)
                for h in hits:
                    _record_hit(h, f"Name match: {name_field}", 10)
                print(f"[xref] Strategy 1: {name_field}={last_name!r} -> {len(hits)} hit(s)", flush=True)

    # Strategy 2: Book/page from Location field (score 50 — very precise)
    location = deed_detail.get("Location", "")
    book_num = ""
    page_num = ""
    if location:
        m = re.match(r'^[A-Za-z]?(\d+)-(\d+)', location.strip())
        if m:
            book_num = m.group(1)
            page_num = m.group(2)
            hits = search_parcels(survey_data_path, book=book_num, page=page_num, limit=5)
            for h in hits:
                _record_hit(h, f"Book/Page: {book_num}-{page_num}", 50)
            print(f"[xref] Strategy 2: Location={location!r} book={book_num} page={page_num} -> {len(hits)} hit(s)", flush=True)
        else:
            print(f"[xref] Strategy 2: Location={location!r} — regex didn't match", flush=True)
    else:
        print(f"[xref] Strategy 2: No Location field in deed detail", flush=True)

    # Strategy 3: Cabinet references (score 30 — good precision)
    cab_pat = re.compile(r'\bCAB(?:INET)?\.?\s*([A-Fa-f])\s*[-–]\s*(\d+[A-Za-z]?)\b', re.I)
    for val in deed_detail.values():
        if not isinstance(val, str):
            continue
        for cm in cab_pat.finditer(val):
            cab_key = f"{cm.group(1).upper()}-{cm.group(2).upper()}"
            hits = search_parcels(survey_data_path, cabinet_ref=cab_key, limit=5)
            for h in hits:
                _record_hit(h, f"Cabinet: {cab_key}", 30)

    # Build results sorted by score (highest first = most criteria matched)
    results = []
    for entry in sorted(score_map.values(), key=lambda e: -e["score"]):
        p = entry["parcel"]
        # Update match_reason to show all matched criteria for multi-match parcels
        if len(entry["reasons"]) > 1:
            p["_match_reason"] = " + ".join(entry["reasons"])
        results.append(p)

    # Log top-5 scores for debugging
    for i, entry in enumerate(sorted(score_map.values(), key=lambda e: -e["score"])[:5]):
        p = entry["parcel"]
        print(f"[xref] #{i+1}: score={entry['score']} upc={p.get('upc','')} owner={p.get('owner','')} reasons={entry['reasons']}", flush=True)

    return results[:30]  # cap total results


# ══════════════════════════════════════════════════════════════════════════════
# GEOMETRY-BASED ADJACENCY
# ══════════════════════════════════════════════════════════════════════════════

def _bounding_box(polygon: list) -> tuple:
    """Compute (min_lng, min_lat, max_lng, max_lat) for a polygon ring."""
    lngs = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    return (min(lngs), min(lats), max(lngs), max(lats))


def _boxes_overlap(a: tuple, b: tuple, margin: float = 0.001) -> bool:
    """Check if two bounding boxes overlap (with optional margin in degrees)."""
    return not (
        a[2] + margin < b[0] or   # a.max_lng < b.min_lng
        b[2] + margin < a[0] or   # b.max_lng < a.min_lng
        a[3] + margin < b[1] or   # a.max_lat < b.min_lat
        b[3] + margin < a[1]      # b.max_lat < a.min_lat
    )


def _min_edge_distance_sq(poly_a: list, poly_b: list) -> float:
    """Estimate minimum squared distance between two polygon edges.

    Uses point-to-edge distance for a fast approximation.
    Returns distance in degrees^2 (caller converts as needed).
    """
    min_dist_sq = float('inf')

    # Sample: check every vertex of A against every edge of B, and vice versa
    def _point_to_segment_dist_sq(px, py, ax, ay, bx, by):
        """Squared distance from point (px,py) to segment (ax,ay)-(bx,by)."""
        dx, dy = bx - ax, by - ay
        len_sq = dx * dx + dy * dy
        if len_sq == 0:
            return (px - ax) ** 2 + (py - ay) ** 2
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / len_sq))
        proj_x = ax + t * dx
        proj_y = ay + t * dy
        return (px - proj_x) ** 2 + (py - proj_y) ** 2

    # For performance, stride through vertices (check every 3rd for large polys)
    stride_a = max(1, len(poly_a) // 30)
    stride_b = max(1, len(poly_b) // 30)

    for i in range(0, len(poly_a), stride_a):
        px, py = poly_a[i]
        for j in range(0, len(poly_b) - 1, stride_b):
            d = _point_to_segment_dist_sq(px, py, poly_b[j][0], poly_b[j][1],
                                           poly_b[j + 1][0], poly_b[j + 1][1])
            if d < min_dist_sq:
                min_dist_sq = d
                if d < 1e-10:  # effectively touching
                    return d

    for i in range(0, len(poly_b), stride_b):
        px, py = poly_b[i]
        for j in range(0, len(poly_a) - 1, stride_a):
            d = _point_to_segment_dist_sq(px, py, poly_a[j][0], poly_a[j][1],
                                           poly_a[j + 1][0], poly_a[j + 1][1])
            if d < min_dist_sq:
                min_dist_sq = d
                if d < 1e-10:
                    return d

    return min_dist_sq


def find_adjacent_parcels(
    survey_data_path: str,
    client_upc: str,
    max_results: int = 20,
    edge_threshold_deg: float = 0.0003,   # ~33m — parcels within this edge distance
) -> list[dict]:
    """Find parcels geometrically adjacent to a given parcel.

    Uses polygon edge proximity rather than centroid distance, which is far
    more accurate for irregularly shaped parcels.

    Returns list of parcel dicts with an added '_adjacency_dist' field.
    """
    idx = load_index(survey_data_path)
    if not idx:
        return []

    parcels = idx.get("parcels", [])
    if not parcels:
        return []

    # Find the client parcel
    client = None
    for p in parcels:
        if p.get("upc") == client_upc:
            client = p
            break
    if not client:
        return []

    client_poly = client.get("polygon")
    client_centroid = client.get("centroid")
    if not client_poly or len(client_poly) < 3:
        # Fall back to centroid proximity if no polygon
        if not client_centroid:
            return []
        # Simple centroid-based fallback with wider radius
        results = []
        clng, clat = client_centroid
        RADIUS = 0.002  # ~222m
        for p in parcels:
            if p.get("upc") == client_upc:
                continue
            pc = p.get("centroid")
            if not pc:
                continue
            if abs(pc[0] - clng) < RADIUS and abs(pc[1] - clat) < RADIUS:
                p_copy = dict(p)
                p_copy["_adjacency_dist"] = ((pc[0] - clng) ** 2 + (pc[1] - clat) ** 2) ** 0.5
                p_copy["_adjacency_type"] = "centroid"
                results.append(p_copy)
                if len(results) >= max_results:
                    break
        results.sort(key=lambda r: r["_adjacency_dist"])
        return results[:max_results]

    # Bounding box of client parcel (with margin for edge threshold)
    client_bbox = _bounding_box(client_poly)
    threshold_sq = edge_threshold_deg ** 2

    results = []
    for p in parcels:
        if p.get("upc") == client_upc:
            continue
        p_poly = p.get("polygon")
        if not p_poly or len(p_poly) < 3:
            continue

        # Fast reject: bounding boxes don't overlap
        p_bbox = _bounding_box(p_poly)
        if not _boxes_overlap(client_bbox, p_bbox, margin=edge_threshold_deg * 2):
            continue

        # Detailed check: minimum edge distance
        dist_sq = _min_edge_distance_sq(client_poly, p_poly)
        if dist_sq <= threshold_sq:
            p_copy = dict(p)
            p_copy["_adjacency_dist"] = dist_sq ** 0.5
            p_copy["_adjacency_type"] = "edge"
            results.append(p_copy)

    # Sort by distance (closest first)
    results.sort(key=lambda r: r["_adjacency_dist"])
    return results[:max_results]


def _perpendicular_distance(point, line_start, line_end):
    """Perpendicular distance from a point to a line segment (for RDP)."""
    dx = line_end[0] - line_start[0]
    dy = line_end[1] - line_start[1]
    mag_sq = dx * dx + dy * dy
    if mag_sq < 1e-20:
        return ((point[0] - line_start[0]) ** 2 + (point[1] - line_start[1]) ** 2) ** 0.5
    u = ((point[0] - line_start[0]) * dx + (point[1] - line_start[1]) * dy) / mag_sq
    u = max(0, min(1, u))
    proj_x = line_start[0] + u * dx
    proj_y = line_start[1] + u * dy
    return ((point[0] - proj_x) ** 2 + (point[1] - proj_y) ** 2) ** 0.5


def _rdp_simplify(points: list, epsilon: float) -> list:
    """Ramer-Douglas-Peucker line simplification (iterative to avoid stack overflow)."""
    if len(points) < 3:
        return points
    # Iterative RDP using an explicit stack
    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        max_dist = 0.0
        max_idx = start
        for i in range(start + 1, end):
            d = _perpendicular_distance(points[i], points[start], points[end])
            if d > max_dist:
                max_dist = d
                max_idx = i
        if max_dist > epsilon:
            keep[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))
    return [points[i] for i in range(len(points)) if keep[i]]


def _simplify_ring(ring: list, max_pts: int = 500) -> list:
    """
    Reduce a polygon ring to at most max_pts vertices using Ramer-Douglas-Peucker.
    Always keeps first and last point (so ring stays closed).
    Coordinates are rounded to 7 decimal places (~1cm accuracy — matches Google Earth quality).
    """
    # Round coords to 7 dp (~1cm) — preserves smooth lines
    ring = [[round(c[0], 7), round(c[1], 7)] for c in ring]
    if len(ring) <= max_pts:
        return ring
    # Use RDP with progressively increasing epsilon until under max_pts
    epsilon = 1e-7  # ~1cm in degrees
    for _ in range(20):
        simplified = _rdp_simplify(ring, epsilon)
        if len(simplified) <= max_pts:
            return simplified
        epsilon *= 2.0
    # Fallback: stride sampling if RDP doesn't converge
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
    Coordinates are simplified to 7 decimal places (~1cm accuracy) and polygons are
    simplified using Ramer-Douglas-Peucker with up to 500 vertices for accurate rendering.

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
            ring = _simplify_ring(ring, max_pts=500)
            geometry = {"type": "Polygon", "coordinates": [ring]}
        elif centroid:
            geometry = {"type": "Point",
                        "coordinates": [round(centroid[0], 7), round(centroid[1], 7)]}
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


# ══════════════════════════════════════════════════════════════════════════════
# DATA QUALITY: INDEX HEALTH & CROSS-SOURCE ANOMALY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def compute_index_health(survey_data_path: str) -> dict:
    """Compute comprehensive health metrics about the parcel index.

    Returns a dict with:
      - total_parcels, pct_with_upc, pct_with_polygon, pct_with_arcgis
      - pct_with_owner, pct_with_plat_ref, pct_with_cab_refs
      - placeholder_count (Unknown Owner / UPC-only labels)
      - index_age_days, stale_warning
      - source_files (list of {name, record_count})
      - xml_file_dates vs index build date
    """
    idx = load_index(survey_data_path)
    if not idx:
        return {"exists": False, "total_parcels": 0}

    parcels = idx.get("parcels", [])
    total = len(parcels)
    if total == 0:
        return {"exists": True, "total_parcels": 0}

    # Count metrics
    has_upc = 0
    has_polygon = 0
    has_arcgis = 0
    has_real_owner = 0
    has_plat = 0
    has_cab_refs = 0
    placeholder_names = 0

    for p in parcels:
        if p.get("upc"):
            has_upc += 1
        if p.get("polygon") and len(p["polygon"]) >= 3:
            has_polygon += 1
        if p.get("arcgis"):
            has_arcgis += 1
        owner = p.get("owner", "")
        if owner and not owner.startswith("UPC ") and owner != "Unknown Owner":
            has_real_owner += 1
        else:
            placeholder_names += 1
        if p.get("plat"):
            has_plat += 1
        if p.get("cab_refs"):
            has_cab_refs += 1

    # Index age
    built_at = idx.get("built_at", "")
    index_age_days = 0
    stale_warning = False
    if built_at:
        try:
            from datetime import datetime
            built_dt = datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%S")
            index_age_days = (datetime.now() - built_dt).days
            stale_warning = index_age_days > 30
        except Exception:
            pass

    # Check if XML/KML files are newer than the index
    idx_path = _index_path(survey_data_path)
    idx_mtime = idx_path.stat().st_mtime if idx_path.exists() else 0
    xml_files = discover_xml_files(survey_data_path)
    newer_xml_files = []
    for xf in xml_files:
        try:
            xf_mtime = Path(xf["path"]).stat().st_mtime
            if xf_mtime > idx_mtime:
                newer_xml_files.append(xf["name"])
        except Exception:
            pass

    # ArcGIS enrichment stats
    enriched_at = idx.get("arcgis_enriched_at", "")
    enriched_count = idx.get("arcgis_enriched_count", 0)

    # Source breakdown
    source_counts: dict[str, int] = {}
    for p in parcels:
        src = p.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
    sources = [{"name": k, "record_count": v} for k, v in
               sorted(source_counts.items(), key=lambda x: -x[1])]

    pct = lambda n: round((n / total) * 100, 1) if total else 0

    return {
        "exists":              True,
        "total_parcels":       total,
        "has_upc":             has_upc,
        "pct_with_upc":        pct(has_upc),
        "has_polygon":         has_polygon,
        "pct_with_polygon":    pct(has_polygon),
        "has_arcgis":          has_arcgis,
        "pct_with_arcgis":     pct(has_arcgis),
        "has_real_owner":      has_real_owner,
        "pct_with_owner":      pct(has_real_owner),
        "has_plat":            has_plat,
        "pct_with_plat_ref":   pct(has_plat),
        "has_cab_refs":        has_cab_refs,
        "pct_with_cab_refs":   pct(has_cab_refs),
        "placeholder_count":   placeholder_names,
        "built_at":            built_at,
        "index_age_days":      index_age_days,
        "stale_warning":       stale_warning,
        "newer_xml_files":     newer_xml_files,
        "arcgis_enriched_at":  enriched_at,
        "arcgis_enriched_count": enriched_count,
        "sources":             sources,
        "xml_files":           xml_files,
        "index_version":       idx.get("version", 1),
    }


def detect_data_conflicts(survey_data_path: str, max_conflicts: int = 200) -> dict:
    """Compare KML parcel data against ArcGIS enrichment and flag mismatches.

    Detects:
      - Owner mismatch: KML owner != ArcGIS OwnerAll (normalized)
      - Area mismatch: polygon area vs ArcGIS LandArea differ >50%
      - Missing enrichment: parcels with UPC but no ArcGIS data
      - TRS conflict: KML-derived TRS != ArcGIS TRS (when both present)

    Returns:
      {
        "total_checked": int,
        "conflicts": [ { upc, type, kml_value, arcgis_value, severity } ],
        "summary": { owner_mismatches, area_mismatches, missing_enrichment, trs_mismatches }
      }
    """
    import math as _math

    idx = load_index(survey_data_path)
    if not idx:
        return {"total_checked": 0, "conflicts": [], "summary": {}}

    parcels = idx.get("parcels", [])
    conflicts = []
    summary = {
        "owner_mismatches": 0,
        "area_mismatches": 0,
        "missing_enrichment": 0,
        "trs_mismatches": 0,
    }

    def _normalize_name(name: str) -> str:
        """Normalize an owner name for comparison."""
        if not name:
            return ""
        name = re.sub(r'[,.\-\'\"]+', ' ', name.upper())
        name = re.sub(r'\s+', ' ', name).strip()
        name = re.sub(r'\s+(JR|SR|II|III|IV|EST|ESTATE|TRUST|LLC|INC|ETAL|ET\s*AL)\.?\s*$', '', name)
        return name

    def _polygon_area_sqm(polygon: list) -> float:
        """Estimate polygon area in sq meters using Shoelace at Taos County latitude."""
        if not polygon or len(polygon) < 3:
            return 0.0
        M_PER_DEG_LAT = 111_000
        M_PER_DEG_LNG = 111_000 * _math.cos(_math.radians(36.4))
        pts = [(p[0] * M_PER_DEG_LNG, p[1] * M_PER_DEG_LAT) for p in polygon]
        n = len(pts)
        a = 0.0
        for i in range(n):
            j = (i + 1) % n
            a += pts[i][0] * pts[j][1]
            a -= pts[j][0] * pts[i][1]
        return abs(a) / 2.0

    total_checked = 0

    for p in parcels:
        upc = p.get("upc", "")
        arc = p.get("arcgis")
        if not upc:
            continue

        total_checked += 1

        if not arc:
            if len(conflicts) < max_conflicts:
                conflicts.append({
                    "upc": upc, "type": "missing_enrichment",
                    "kml_value": p.get("owner", ""), "arcgis_value": "",
                    "severity": "info",
                })
            summary["missing_enrichment"] += 1
            continue

        # Owner mismatch
        kml_owner = _normalize_name(p.get("owner", ""))
        arc_owner = _normalize_name(arc.get("owner_official", ""))
        if kml_owner and arc_owner and kml_owner != arc_owner:
            if kml_owner not in arc_owner and arc_owner not in kml_owner:
                kml_last = kml_owner.split()[0] if kml_owner.split() else ""
                arc_last = arc_owner.split()[0] if arc_owner.split() else ""
                if kml_last != arc_last:
                    if len(conflicts) < max_conflicts:
                        conflicts.append({
                            "upc": upc, "type": "owner_mismatch",
                            "kml_value": p.get("owner", ""),
                            "arcgis_value": arc.get("owner_official", ""),
                            "severity": "warn",
                        })
                    summary["owner_mismatches"] += 1

        # Area mismatch
        arc_area = arc.get("land_area")
        kml_poly = p.get("polygon")
        if arc_area and kml_poly and len(kml_poly) >= 3:
            try:
                arc_area_sqm = float(arc_area) * 0.092903
                kml_area_sqm = _polygon_area_sqm(kml_poly)
                if kml_area_sqm > 0 and arc_area_sqm > 0:
                    ratio = max(kml_area_sqm, arc_area_sqm) / min(kml_area_sqm, arc_area_sqm)
                    if ratio > 1.5:
                        severity = "critical" if ratio > 3.0 else "warn"
                        if len(conflicts) < max_conflicts:
                            conflicts.append({
                                "upc": upc, "type": "area_mismatch",
                                "kml_value": f"{kml_area_sqm:.0f} sq m (polygon)",
                                "arcgis_value": f"{arc_area_sqm:.0f} sq m (ArcGIS)",
                                "severity": severity, "ratio": round(ratio, 2),
                            })
                        summary["area_mismatches"] += 1
            except (ValueError, TypeError):
                pass

        # TRS mismatch
        kml_trs = (p.get("trs", "") or "").strip().upper()
        arc_trs = (arc.get("trs", "") or "").strip().upper()
        if kml_trs and arc_trs and kml_trs != arc_trs:
            kml_norm = re.sub(r'SEC\s*', 'S', kml_trs)
            arc_norm = re.sub(r'SEC\s*', 'S', arc_trs)
            if kml_norm != arc_norm:
                if len(conflicts) < max_conflicts:
                    conflicts.append({
                        "upc": upc, "type": "trs_mismatch",
                        "kml_value": kml_trs, "arcgis_value": arc_trs,
                        "severity": "warn",
                    })
                summary["trs_mismatches"] += 1

    return {
        "total_checked": total_checked,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "summary": summary,
    }

