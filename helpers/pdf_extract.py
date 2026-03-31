"""
helpers/pdf_extract.py — PDF text extraction with native + OCR fallback.

Extracted from app.py to improve testability and separation of concerns.
"""

import os
import re
import io
import json
from pathlib import Path


def _find_tesseract() -> str:
    """Locate the Tesseract binary. Checks standard install paths first, then PATH."""
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Try PATH (covers custom installs, Linux/Mac, conda environments)
    import shutil
    found = shutil.which("tesseract")
    return found or candidates[0]  # fall back to default path even if missing


def setup_tesseract():
    """Configure pytesseract with the detected binary path."""
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()


def extract_pdf_text(pdf_path: str) -> tuple[str, str]:
    """Extract text from a PDF, trying the native text layer first and
    falling back to Tesseract OCR if the layer is sparse (<30 chars).

    Returns (text, source) where source is 'text' or 'ocr'.
    """
    import fitz
    import pytesseract
    from PIL import Image

    text   = ""
    source = "text"
    try:
        doc = fitz.open(pdf_path)
        for i, page in enumerate(doc):
            page_text = page.get_text("text")
            print(f"[pdf] Page {i+1}: {len(page_text.strip())} chars extracted via text layer", flush=True)
            text += page_text + "\n"
        doc.close()
    except Exception as e:
        print(f"[pdf] Text extraction error for {pdf_path}: {e}", flush=True)

    native_text = text  # preserve whatever we got from the text layer

    # Only attempt OCR if native text is truly minimal (< 30 chars total)
    if len(text.strip()) < 30:
        source = "ocr"
        text   = ""
        try:
            from PIL import ImageEnhance
            doc = fitz.open(pdf_path)
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
                img = ImageEnhance.Contrast(img).enhance(1.8)
                img = img.point(lambda x: 255 if x > 128 else 0, "1")
                text += pytesseract.image_to_string(img, config="--oem 3 --psm 6") + "\n"
            doc.close()
        except Exception as e:
            print(f"[pdf] OCR failed for {pdf_path}: {e}", flush=True)
            # OCR failed — fall back to whatever native text we got
            if native_text.strip():
                print(f"[pdf] Falling back to native text ({len(native_text.strip())} chars)", flush=True)
                text = native_text
                source = "text"

    return text, source


def _ocr_cache_path(pdf_path: str) -> Path:
    return Path(pdf_path).with_suffix(".ocr.json")


def ocr_plat_file(pdf_path: str) -> list[str]:
    """
    OCR a plat PDF and return a de-duplicated list of adjoiner name strings.

    Pipeline:
      1. Check for a cached .ocr.json result alongside the PDF — return instantly if found.
      2. Render each page at 250 DPI with PyMuPDF.
      3. Pre-process with PIL: grayscale → contrast boost → Otsu binarization.
         This dramatically improves Tesseract accuracy on old/yellowed scans.
      4. Run Tesseract with --oem 3 --psm 6 (assume uniform block of text).
      5. Parse combined text for "Lands of / Property of / Adjoins [Name]" patterns.
      6. Write cache file so future calls are instant.
    """
    import fitz
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter

    cache = _ocr_cache_path(pdf_path)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            return cached.get("names", [])
        except Exception:
            pass

    try:
        doc       = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            pix = page.get_pixmap(dpi=250)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")  # grayscale

            # Boost contrast then binarize (helps with faded/yellowed scans)
            img = ImageEnhance.Contrast(img).enhance(2.0)
            # Simple threshold at 128 — Otsu-style via point()
            img = img.point(lambda x: 255 if x > 128 else 0, "1")

            text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
            full_text += text + "\n"
        doc.close()
    except Exception as e:
        print(f"[OCR] Failed to read {pdf_path}: {e}")
        return []

    found    = []
    seen     = set()
    patterns = [
        re.compile(
            r"\blands?\s+of\s+(?:the\s+(?:heirs?\s+of\s+)?)?([A-Z][A-Za-z'\-]+(?:[,\s]+[A-Za-z'\-]+){0,4})",
            re.I
        ),
        re.compile(r"\bproperty\s+of\s+([A-Z][A-Za-z'\-]+(?:[,\s]+[A-Za-z'\-]+){0,3})", re.I),
        re.compile(r"\badjoins?\s+([A-Z][A-Za-z'\-]+(?:[,\s]+[A-Za-z'\-]+){0,3})", re.I),
    ]
    noise = {
        "the", "said", "above", "grantor", "grantee", "new", "mexico",
        "county", "state", "united", "states", "government", "public",
        "road", "street", "acequia", "ditch", "river", "creek", "arroyo",
        "forest", "national", "carson", "section", "township", "range",
        "unknown", "parties", "record", "boundary", "corner", "tract",
        "survey", "plat", "map", "parcel", "lot", "block",
    }
    for pat in patterns:
        for m in pat.finditer(full_text):
            raw  = m.group(1).strip().rstrip(".,;:")
            raw  = re.sub(r"\s+", " ", raw)
            name = raw.title()
            key  = name.lower()
            first_word = key.split()[0] if key.split() else ""
            if first_word in noise or len(name) < 4 or key in seen:
                continue
            seen.add(key)
            found.append(name)

    # Write cache
    try:
        cache.write_text(
            json.dumps({"names": found, "source": str(pdf_path)}, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass

    return found
