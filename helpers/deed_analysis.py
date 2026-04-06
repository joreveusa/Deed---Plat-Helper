"""
helpers/deed_analysis.py
========================
Comprehensive deed health-check and legal description extraction.

Provides:
  - analyze_deed(detail, pdf_path, pdf_extractor) → dict
  - isolate_legal_description(text) → str

Extracted from app.py to enable unit testing and reduce monolith size.
All regex and helper references are resolved via imports from sibling modules.
"""

import os
import re
import math
from datetime import datetime

from helpers.metes_bounds import (
    parse_metes_bounds, calls_to_coords, extract_trs,
    detect_monuments, shoelace_area, has_pob,
    _LOT_BLOCK_RE, _TRACT_RE,
)


# ── Legal-description isolator ────────────────────────────────────────────────

def isolate_legal_description(text: str) -> str:
    """
    Attempt to isolate the legal description section from full deed text.
    Looks for common markers like 'Legal Description:', 'described as follows',
    'BEGINNING at', etc., and extracts the relevant block of text.
    """
    if not text or len(text.strip()) < 20:
        return text

    # Strategy 1: Find explicit section headers
    header_patterns = [
        re.compile(r'(?:LEGAL\s+DESCRIPTION|PROPERTY\s+DESCRIPTION|DESCRIPTION\s+OF\s+(?:THE\s+)?PROPERTY)[:\s]*\n?', re.I),
        re.compile(r'(?:described\s+as\s+follows|more\s+particularly\s+described\s+as)[:\s]*\n?', re.I),
        re.compile(r'(?:to\s+wit)[:\s]*\n?', re.I),
    ]

    # Strategy 2: Find POB to closing
    pob_pattern = re.compile(
        r'((?:BEGINNING|COMMENCING)\s+at\s+.+?)(?:containing|CONTAINING|subject\s+to|SUBJECT\s+TO|together\s+with|TOGETHER\s+WITH|\bEXCEPT\b|\bRESERVING\b|\bSAID\s+LAND\b|\bIN\s+WITNESS\b|\Z)',
        re.I | re.S
    )

    for pat in header_patterns:
        m = pat.search(text)
        if m:
            # Extract from the header to the next major section
            start = m.end()
            # Find end markers
            end_patterns = [
                re.compile(r'\n\s*(?:IN\s+WITNESS|WITNESS\s+WHEREOF|EXCEPTING|SUBJECT\s+TO|TOGETHER\s+WITH|This\s+conveyance|Grantor\s+reserves)', re.I),
                re.compile(r'\n\s*(?:COUNTY\s+OF|STATE\s+OF|NOTARY|ACKNOWLEDGMENT)', re.I),
            ]
            end_pos = len(text)
            for ep in end_patterns:
                em = ep.search(text, start)
                if em and em.start() < end_pos:
                    end_pos = em.start()

            extracted = text[start:end_pos].strip()
            if len(extracted) > 30:
                return extracted

    # Strategy 2: POB-based extraction
    pm = pob_pattern.search(text)
    if pm:
        return pm.group(1).strip()

    # Strategy 3: If the text is short enough, it might BE the description
    if len(text.strip()) < 2000:
        return text.strip()

    # Strategy 4: Return the first substantial paragraph
    paragraphs = text.split('\n\n')
    for p in paragraphs:
        p = p.strip()
        if len(p) > 100 and any(kw in p.lower() for kw in ['beginning', 'thence', 'lot', 'block', 'tract', 'section', 'township']):
            return p

    return text[:2000].strip()


# ── Deed health-check engine ─────────────────────────────────────────────────

def analyze_deed(detail: dict, pdf_path: str = "",
                 pdf_extractor=None) -> dict:
    """
    Perform a comprehensive health-check on a deed.

    Analyses:
      1. Closure       – metes-and-bounds closure error, perimeter, area
      2. Parties       – grantor/grantee presence, vesting, self-conveyance
      3. Legal Desc    – description type, POB, monuments, quality
      4. Completeness  – recording info, dates, consideration, TRS
      5. NM-Specific   – acequia, land grants, mineral rights flags

    Args:
        detail:        dict of deed fields from the online index.
        pdf_path:      optional path to a saved deed PDF.
        pdf_extractor: callable(pdf_path) -> (text, source) for PDF text
                       extraction.  Injected so this module stays Flask-free.

    Returns a dict with { score, grade, categories:[...], issues:[...] }.
    """
    issues: list[dict] = []
    categories: dict[str, dict] = {}

    # ── Gather all text ────────────────────────────────────────────────────
    legal_fields = [
        "Other_Legal", "Subdivision_Legal", "Comments", "Remarks",
        "Legal Description", "Legal", "Reference", "Description",
    ]
    legal_text = "\n".join(
        str(detail.get(f, "")) for f in legal_fields if detail.get(f)
    )
    all_text = legal_text + "\n" + " ".join(
        str(v) for v in detail.values() if isinstance(v, str)
    )

    # If a saved PDF exists, extract its full text for deeper analysis
    pdf_text = ""
    pdf_source = ""
    if pdf_path and os.path.isfile(pdf_path):
        if pdf_extractor:
            try:
                pdf_text, pdf_source = pdf_extractor(pdf_path)
            except Exception as e:
                print(f"[analyze] PDF text extraction failed: {e}", flush=True)

    combined_text = legal_text + "\n" + pdf_text

    # ── 1. CLOSURE ANALYSIS ────────────────────────────────────────────────
    calls = parse_metes_bounds(combined_text)
    pts   = calls_to_coords(calls) if calls else []
    closure_err = 0.0
    perimeter   = 0.0
    area_sqft   = 0.0
    closure_ratio = ""
    desc_type     = "unknown"

    if calls:
        desc_type = "metes_and_bounds"
        # Perimeter
        for c in calls:
            perimeter += c.get("distance", 0)
        perimeter = round(perimeter, 2)

        # Closure error
        if len(pts) >= 2:
            closure_err = round(
                math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]), 4
            )
            if closure_err > 0.001 and perimeter > 0:
                ratio = perimeter / closure_err
                closure_ratio = f"1:{int(ratio)}"

        # Area (Shoelace formula)
        area_sqft = shoelace_area(pts)

        # Closure issues
        if closure_err <= 0.5:
            issues.append({
                "category": "closure", "severity": "ok",
                "title": "Closure within tolerance",
                "detail": f"Closure error: {closure_err:.3f} ft ({closure_ratio or 'exact'})",
            })
        elif closure_err <= 1.0:
            issues.append({
                "category": "closure", "severity": "info",
                "title": "Closure is acceptable",
                "detail": f"Closure error: {closure_err:.3f} ft ({closure_ratio})",
            })
        elif closure_err <= 5.0:
            issues.append({
                "category": "closure", "severity": "warn",
                "title": "Closure error exceeds 1 ft",
                "detail": f"Closure error: {closure_err:.3f} ft ({closure_ratio}). May indicate transcription errors in bearings or distances.",
            })
        else:
            issues.append({
                "category": "closure", "severity": "critical",
                "title": "Large closure error",
                "detail": f"Closure error: {closure_err:.3f} ft ({closure_ratio}). This deed's metes and bounds may be unreliable or incomplete.",
            })

        issues.append({
            "category": "closure", "severity": "info",
            "title": f"{len(calls)} bearing/distance calls found",
            "detail": f"Perimeter: {perimeter:,.2f} ft  |  Area: {area_sqft:,.1f} sq ft ({area_sqft/43560:.3f} acres)" if area_sqft else f"Perimeter: {perimeter:,.2f} ft",
        })

    else:
        # No metes and bounds — check if it's lot/block or TRS only
        trs_refs     = extract_trs(all_text)

        if _LOT_BLOCK_RE.search(combined_text):
            desc_type = "lot_block"
            issues.append({
                "category": "closure", "severity": "info",
                "title": "Lot/Block subdivision description",
                "detail": "This deed uses a lot/block legal description — closure analysis not applicable.",
            })
        elif _TRACT_RE.search(combined_text):
            desc_type = "tract"
            issues.append({
                "category": "closure", "severity": "info",
                "title": "Tract reference description",
                "detail": "This deed references a tract — closure analysis not applicable.",
            })
        elif trs_refs:
            desc_type = "trs_only"
            issues.append({
                "category": "closure", "severity": "warn",
                "title": "TRS-only description (no metes & bounds)",
                "detail": "This deed contains only Township/Range/Section references. No bearing/distance calls were found.",
            })
        else:
            issues.append({
                "category": "closure", "severity": "warn",
                "title": "No legal description found",
                "detail": "No metes & bounds, lot/block, or TRS references detected. The deed may need manual review.",
            })

    # POB (Point of Beginning)
    if has_pob(combined_text):
        issues.append({
            "category": "closure", "severity": "ok",
            "title": "Point of Beginning found",
            "detail": "The deed references a point of beginning (POB).",
        })
    elif desc_type == "metes_and_bounds":
        issues.append({
            "category": "closure", "severity": "warn",
            "title": "No Point of Beginning mentioned",
            "detail": "Metes & bounds calls found but no POB reference detected.",
        })

    # Monument detection
    found_monuments = detect_monuments(combined_text)

    if found_monuments:
        issues.append({
            "category": "closure", "severity": "ok",
            "title": f"Monuments referenced: {', '.join(found_monuments)}",
            "detail": f"Found {len(found_monuments)} monument type(s) in the legal description.",
        })

    categories["closure"] = {
        "title":         "Boundary & Closure",
        "icon":          "📐",
        "calls_count":   len(calls),
        "closure_err":   closure_err,
        "closure_ratio": closure_ratio,
        "perimeter":     perimeter,
        "area_sqft":     area_sqft,
        "area_acres":    round(area_sqft / 43560, 3) if area_sqft else 0,
        "desc_type":     desc_type,
        "monuments":     found_monuments,
        "has_pob":       has_pob(combined_text),
    }

    # ── 2. GRANTOR / GRANTEE ANALYSIS ──────────────────────────────────────
    grantor = (detail.get("Grantor") or "").strip()
    grantee = (detail.get("Grantee") or "").strip()

    # Presence checks
    if grantor:
        issues.append({
            "category": "parties", "severity": "ok",
            "title": "Grantor present",
            "detail": grantor,
        })
    else:
        issues.append({
            "category": "parties", "severity": "critical",
            "title": "Grantor missing",
            "detail": "No grantor name found in the deed record.",
        })

    if grantee:
        issues.append({
            "category": "parties", "severity": "ok",
            "title": "Grantee present",
            "detail": grantee,
        })
    else:
        issues.append({
            "category": "parties", "severity": "critical",
            "title": "Grantee missing",
            "detail": "No grantee name found in the deed record.",
        })

    # Self-conveyance
    if grantor and grantee and grantor.upper() == grantee.upper():
        issues.append({
            "category": "parties", "severity": "warn",
            "title": "Self-conveyance detected",
            "detail": "Grantor and grantee are the same name. This may be a corrective deed or name change.",
        })

    # Multiple parties
    multi_sep = re.compile(r'\s*(?:\band\b|\b&\b|;)\s*', re.I)
    grantor_parts = [p.strip() for p in multi_sep.split(grantor) if p.strip()] if grantor else []
    grantee_parts = [p.strip() for p in multi_sep.split(grantee) if p.strip()] if grantee else []

    if len(grantor_parts) > 1:
        issues.append({
            "category": "parties", "severity": "info",
            "title": f"Multiple grantors ({len(grantor_parts)})",
            "detail": "; ".join(grantor_parts),
        })
    if len(grantee_parts) > 1:
        issues.append({
            "category": "parties", "severity": "info",
            "title": f"Multiple grantees ({len(grantee_parts)})",
            "detail": "; ".join(grantee_parts),
        })

    # Vesting language detection
    vesting_patterns = {
        "Joint Tenants":      re.compile(r'\bjoint\s+tenan', re.I),
        "Tenants in Common":  re.compile(r'\btenants?\s+in\s+common', re.I),
        "Community Property": re.compile(r'\bcommunity\s+property', re.I),
        "Husband & Wife":     re.compile(r'\bhusband\s+(?:and|&)\s+wife', re.I),
        "Trustee":            re.compile(r'\b(?:as\s+)?trustee', re.I),
        "LLC / Corp":         re.compile(r'\b(?:LLC|L\.L\.C\.|Inc\.|Corp\.|Corporation|Company)\b', re.I),
        "Estate / Heirs":     re.compile(r'\b(?:estate\s+of|heirs?\s+of|personal\s+representative)', re.I),
    }
    detected_vesting = []
    for label, pat in vesting_patterns.items():
        if pat.search(grantor + " " + grantee + " " + all_text[:2000]):
            detected_vesting.append(label)

    if detected_vesting:
        issues.append({
            "category": "parties", "severity": "info",
            "title": f"Vesting: {', '.join(detected_vesting)}",
            "detail": "Detected ownership/vesting language in the deed.",
        })

    # Name quality check
    for role, name in [("Grantor", grantor), ("Grantee", grantee)]:
        if name and "," not in name and len(name.split()) == 1:
            issues.append({
                "category": "parties", "severity": "warn",
                "title": f"{role} name may be incomplete",
                "detail": f"\"{name}\" — only a single word detected; may be missing first name.",
            })

    categories["parties"] = {
        "title":    "Grantor & Grantee",
        "icon":     "👤",
        "grantor":  grantor,
        "grantee":  grantee,
        "grantor_count": len(grantor_parts),
        "grantee_count": len(grantee_parts),
        "vesting":  detected_vesting,
    }

    # ── 2b. INSTRUMENT TYPE ANALYSIS ───────────────────────────────────────
    inst_type = (detail.get("Instrument Type") or detail.get("Document Type")
                 or detail.get("Type") or "").strip().upper()

    risky_types = {
        "QUIT":      ("warn",  "Quitclaim Deed", "Quitclaim deeds provide no warranty of title. The grantor only conveys whatever interest they hold, if any."),
        "TAX":       ("warn",  "Tax Deed / Tax Sale", "Tax deeds may carry title risks — prior owners may have redemption rights."),
        "PERSONAL":  ("info",  "Personal Representative Deed", "Deed executed by estate personal representative — check probate authority."),
        "SPECIAL":   ("info",  "Special Warranty Deed", "Special warranty only covers the period the grantor held title; prior defects are not warranted."),
        "CORRECTIVE": ("info", "Corrective Deed", "This is a corrective instrument — review the original deed it modifies."),
        "GIFT":      ("info",  "Gift Deed", "No consideration exchanged — may affect title insurance."),
    }
    for keyword, (sev, title, detail_text) in risky_types.items():
        if keyword in inst_type:
            issues.append({
                "category": "parties", "severity": sev,
                "title": title,
                "detail": detail_text,
            })
            break

    # ── 2c. DEED AGE CHECK ─────────────────────────────────────────────────
    rec_date_str = (detail.get("Recorded Date") or detail.get("Record Date")
                    or detail.get("Instrument Date") or "")
    deed_age_years = 0
    if rec_date_str:
        try:
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%b %d, %Y"):
                try:
                    rec_date = datetime.strptime(rec_date_str.strip(), fmt)
                    deed_age_years = (datetime.now() - rec_date).days / 365.25
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    if deed_age_years > 100:
        issues.append({
            "category": "parties", "severity": "warn",
            "title": f"Deed is over 100 years old ({int(deed_age_years)} years)",
            "detail": "Historical deed — legal descriptions, monuments, and boundaries may have changed significantly.",
        })
    elif deed_age_years > 50:
        issues.append({
            "category": "parties", "severity": "info",
            "title": f"Deed recorded {int(deed_age_years)} years ago",
            "detail": "Older deed — verify that legal description landmarks and monuments still exist.",
        })

    # ── 3. LEGAL DESCRIPTION QUALITY ───────────────────────────────────────
    has_legal = bool(legal_text.strip()) or bool(pdf_text.strip())

    if has_legal:
        issues.append({
            "category": "legal", "severity": "ok",
            "title": "Legal description present",
            "detail": f"Description type: {desc_type.replace('_', ' ').title()}",
        })
    else:
        issues.append({
            "category": "legal", "severity": "critical",
            "title": "No legal description text found",
            "detail": "The deed record has no legal description fields. Save the deed PDF for deeper OCR analysis.",
        })

    # Survey/plat references
    survey_ref = re.compile(r'\b(?:survey(?:ed)?|plat|subdivision|filed|recorded)\s+(?:by|of|in|under|at)\b', re.I)
    if survey_ref.search(combined_text):
        issues.append({
            "category": "legal", "severity": "ok",
            "title": "References existing survey/plat",
            "detail": "The legal description references a prior survey or recorded plat.",
        })

    # Exceptions / reservations
    except_re = re.compile(r'\b(?:except(?:ing)?|reserv(?:ing|ation)|subject\s+to|excluding|less\s+and\s+except)\b', re.I)
    if except_re.search(combined_text):
        except_matches = except_re.findall(combined_text)
        issues.append({
            "category": "legal", "severity": "warn",
            "title": f"Exceptions/Reservations found ({len(except_matches)})",
            "detail": "The deed contains exception or reservation language. Review carefully for excluded areas.",
        })

    # Easement references
    ease_re = re.compile(r'\b(?:easement|right[- ]of[- ]way|r\.?o\.?w\.?|utility\s+easement|access\s+easement)\b', re.I)
    if ease_re.search(combined_text):
        issues.append({
            "category": "legal", "severity": "info",
            "title": "Easement references found",
            "detail": "The deed references easements or rights-of-way.",
        })

    categories["legal"] = {
        "title":     "Legal Description",
        "icon":      "📜",
        "has_legal": has_legal,
        "desc_type": desc_type,
        "text_len":  len(combined_text.strip()),
    }

    # ── 4. DOCUMENT COMPLETENESS ───────────────────────────────────────────
    completeness_checks = {
        "Recording Location": bool(detail.get("Location")),
        "Document Number":    bool(detail.get("doc_no") or detail.get("Document Number")),
        "Recorded Date":      bool(detail.get("Recorded Date") or detail.get("Record Date")),
        "Instrument Date":    bool(detail.get("Instrument Date")),
        "Grantor":            bool(grantor),
        "Grantee":            bool(grantee),
        "Legal Description":  has_legal,
        "TRS References":     bool(extract_trs(all_text)),
        "Instrument Type":    bool(detail.get("Instrument Type") or detail.get("Document Type") or detail.get("Type")),
    }
    # Optional: consideration
    consideration = detail.get("Consideration") or detail.get("Amount") or detail.get("Sale Price") or ""
    if consideration and consideration not in ("$0", "0", "$0.00"):
        completeness_checks["Consideration"] = True
    else:
        completeness_checks["Consideration"] = False

    # Notary (only from PDF text)
    notary_re = re.compile(r'\b(?:notar(?:y|ial|ized)|acknowledged|sworn|commissioned)\b', re.I)
    completeness_checks["Notary/Acknowledgment"] = bool(notary_re.search(combined_text))

    passed = sum(1 for v in completeness_checks.values() if v)
    total  = len(completeness_checks)
    pct    = round((passed / total) * 100) if total else 0

    for field, present in completeness_checks.items():
        if present:
            issues.append({
                "category": "completeness", "severity": "ok",
                "title": f"{field} ✓",
                "detail": "",
            })
        else:
            # Skip fields already covered in their own categories to avoid noise
            if field in ("Grantor", "Grantee", "Legal Description"):
                continue
            sev = "warn" if field in ("Recording Location", "Document Number", "Recorded Date") else "info"
            issues.append({
                "category": "completeness", "severity": sev,
                "title": f"{field} — missing",
                "detail": f"No {field.lower()} found in the deed record.",
            })

    categories["completeness"] = {
        "title":   "Document Completeness",
        "icon":    "📋",
        "passed":  passed,
        "total":   total,
        "percent": pct,
        "checks":  completeness_checks,
    }

    # ── 5. NEW MEXICO SPECIFIC FLAGS ───────────────────────────────────────
    nm_flags = []

    acequia_re = re.compile(r'\b(?:acequia|ditch|irrigation|water\s+right)\b', re.I)
    if acequia_re.search(combined_text):
        nm_flags.append("Acequia / Water Rights")
        issues.append({
            "category": "nm_specific", "severity": "info",
            "title": "Acequia / Water Rights referenced",
            "detail": "The deed mentions acequia, ditch, or water rights — common in NM deeds.",
        })

    mineral_re = re.compile(r'\b(?:mineral\s+right|mineral\s+reservation|oil\s+(?:and|&)\s+gas|subsurface)\b', re.I)
    if mineral_re.search(combined_text):
        nm_flags.append("Mineral Rights")
        issues.append({
            "category": "nm_specific", "severity": "warn",
            "title": "Mineral rights language detected",
            "detail": "The deed contains mineral rights or subsurface references. Verify if minerals are reserved or conveyed.",
        })

    grant_re = re.compile(r'\b(?:land\s+grant|spanish\s+grant|mexican\s+grant|pueblo|merced|community\s+grant)\b', re.I)
    if grant_re.search(combined_text):
        nm_flags.append("Land Grant")
        issues.append({
            "category": "nm_specific", "severity": "info",
            "title": "Land grant reference found",
            "detail": "The deed references a Spanish/Mexican land grant, pueblo, or merced.",
        })

    # Mobile/manufactured home
    mobile_re = re.compile(r'\b(?:mobile\s+home|manufactured\s+home|modular|trailer)\b', re.I)
    if mobile_re.search(combined_text):
        nm_flags.append("Mobile/Manufactured Home")
        issues.append({
            "category": "nm_specific", "severity": "info",
            "title": "Mobile/manufactured home referenced",
            "detail": "The deed mentions a mobile or manufactured home on the property.",
        })

    if nm_flags:
        categories["nm_specific"] = {
            "title": "NM-Specific Flags",
            "icon":  "🏜️",
            "flags": nm_flags,
        }

    # ── COMPOSITE SCORE ────────────────────────────────────────────────────
    score = 100.0
    for iss in issues:
        sev = iss["severity"]
        if sev == "critical":
            score -= 15
        elif sev == "warn":
            score -= 5
        # ok and info don't reduce score

    # Bonus for well-documented deeds
    if completeness_checks.get("Notary/Acknowledgment"):
        score += 2
    if found_monuments:
        score += 3
    if len(calls) >= 4:
        score += 5

    score = max(0, min(100, round(score)))

    if score >= 80:
        grade = "good"
    elif score >= 50:
        grade = "fair"
    else:
        grade = "poor"

    return {
        "score":      score,
        "grade":      grade,
        "categories": categories,
        "issues":     issues,
        "pdf_used":   bool(pdf_text),
        "pdf_source": pdf_source,
        "desc_type":  desc_type,
    }
