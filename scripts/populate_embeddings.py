"""
populate_embeddings.py
======================
Populate the ChromaDB legal embeddings index from three sources:
  1. KG nodes that have legal descriptions or meaningful text
  2. Cabinet PDF OCR cache (existing cached text)
  3. Access DB plat titles / memos

ChromaDB currently has ~20 docs. This script should push it to 5,000-50,000+.
Uses the existing ai.embeddings module (respects config.json embed_model).

Run:
    python scripts/populate_embeddings.py
"""

import json
import sys
import time
import re
from pathlib import Path
from datetime import datetime

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR  = ROOT / "data" / "ai" / "training_data"
OUT_FILE = OUT_DIR / "embeddings_populate_log.json"

BATCH_SIZE = 64    # ChromaDB upsert batch size


# ── Text cleaners ─────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Strip junk, collapse whitespace."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text[:2000]   # ChromaDB max reasonable doc size


def _is_useful(text: str) -> bool:
    """Skip very short or numeric-only strings."""
    text = text.strip()
    if len(text) < 20:
        return False
    # Must have at least a few real words
    words = [w for w in text.split() if re.search(r"[A-Za-z]{3}", w)]
    return len(words) >= 4


# ── Source 1: Knowledge Graph nodes ─────────────────────────────────────────

def _docs_from_kg(kg) -> list[tuple[str, str, dict]]:
    """
    Extract documents from KG nodes.
    Returns list of (doc_id, text, metadata).
    """
    docs = []
    G = kg.G

    for node_id, data in G.nodes(data=True):
        ntype = data.get("type", "")
        texts = []

        if ntype == "job":
            # Combine title + location + TRS into a searchable description
            parts = [
                data.get("title", ""),
                data.get("location", ""),
                f"Section {data.get('section','')} T{data.get('township','')} R{data.get('range','')}",
                f"Acreage: {data.get('acreage', '')}",
                data.get("subdivision", ""),
            ]
            texts.append(" ".join(p for p in parts if p.strip()))

        elif ntype in ("plat", "other_plat"):
            parts = [
                data.get("owner", data.get("name", "")),
                f"Section {data.get('section','')} T{data.get('township','')} R{data.get('range','')}",
                f"Acreage: {data.get('acreage', '')}",
                data.get("map_ref", ""),
                data.get("notes", ""),
            ]
            texts.append(" ".join(p for p in parts if p.strip()))

        elif ntype == "subdivision":
            parts = [
                data.get("name", ""),
                f"Section {data.get('section','')} T{data.get('township','')} R{data.get('range','')}",
                f"Cabinet {data.get('cabinet','')} Page {data.get('page','')}",
                f"Acreage: {data.get('acreage', '')}",
            ]
            texts.append(" ".join(p for p in parts if p.strip()))

        elif ntype == "parcel":
            parts = [
                data.get("address", ""),
                data.get("legal", ""),
                f"Parcel {data.get('parcel_id','')}",
                f"Acreage: {data.get('acreage', '')}",
            ]
            texts.append(" ".join(p for p in parts if p.strip()))

        for text in texts:
            text = _clean(text)
            if _is_useful(text):
                meta = {
                    "type":       ntype,
                    "node_id":    node_id,
                    "job_number": data.get("job_number", ""),
                    "source":     data.get("source", "kg"),
                }
                docs.append((f"kg_{node_id}", text, meta))

    logger.info(f"[embeddings] KG source: {len(docs):,} documents")
    return docs


# ── Source 2: Cabinet OCR cache ───────────────────────────────────────────────

def _docs_from_ocr_cache() -> list[tuple[str, str, dict]]:
    """Read OCR cache JSON files from data/ocr_cache/ and extract text."""
    docs = []
    cache_dir = ROOT / "data" / "ocr_cache"

    if not cache_dir.exists():
        logger.info("[embeddings] OCR cache dir not found — skipping")
        return docs

    cache_files = list(cache_dir.glob("*.json"))
    logger.info(f"[embeddings] OCR cache: {len(cache_files)} files")

    for cf in cache_files:
        try:
            data = json.loads(cf.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                text = data.get("text", "") or data.get("content", "")
                filename = data.get("filename", cf.stem)
            elif isinstance(data, str):
                text = data
                filename = cf.stem
            else:
                continue

            text = _clean(text)
            if _is_useful(text):
                docs.append((f"ocr_{cf.stem}", text, {
                    "type": "cabinet_pdf",
                    "filename": filename,
                    "source": "ocr_cache",
                }))
        except Exception:
            pass

    logger.info(f"[embeddings] OCR cache: {len(docs):,} usable documents")
    return docs


# ── Source 3: Access DB plat memos ───────────────────────────────────────────

def _docs_from_access_export() -> list[tuple[str, str, dict]]:
    """Extract plat memos and titles from the Access DB export JSON."""
    docs = []
    export_path = OUT_DIR / "access_db_export.json"

    if not export_path.exists():
        logger.info("[embeddings] Access DB export not found — skipping")
        return docs

    try:
        data = json.loads(export_path.read_text(encoding="utf-8"))
        records = data.get("records", [])
    except Exception as e:
        logger.warning(f"[embeddings] Access DB export read failed: {e}")
        return docs

    for rec in records:
        table = rec.get("_source_table", "")
        parts = []

        if "Plat" in table:
            parts = [
                rec.get("title_of_plat", rec.get("title_of__plat", "")),
                rec.get("memo", ""),
                f"Section {rec.get('section','')} T{rec.get('township', rec.get('township_1',''))} R{rec.get('range', rec.get('range_1',''))}",
                f"Acreage: {rec.get('acreage', '')}",
            ]
        elif table == "Subdivisions":
            parts = [
                rec.get("subdivision_title", ""),
                rec.get("memo", ""),
                f"Section {rec.get('section_1','')} T{rec.get('township_1','')} R{rec.get('range_1','')}",
            ]

        text = _clean(" ".join(p for p in parts if p))
        if _is_useful(text):
            doc_id = f"accdb_{table[:8]}_{hash(text) % 999999}"
            docs.append((doc_id, text, {
                "type":   "access_db_plat",
                "table":  table,
                "source": "access_db",
            }))

    logger.info(f"[embeddings] Access DB: {len(docs):,} usable documents")
    return docs


# ── Upsert into ChromaDB ──────────────────────────────────────────────────────

def _upsert_to_chromadb(
    emb,
    all_docs: list[tuple[str, str, dict]]
) -> dict:
    """Upsert all documents into ChromaDB using add_document()."""
    if not all_docs:
        return {"upserted": 0}

    # Deduplicate by doc_id
    seen: dict[str, tuple[str, dict]] = {}
    for doc_id, text, meta in all_docs:
        if doc_id not in seen:
            seen[doc_id] = (text, meta)
    unique_docs = list(seen.items())   # [(doc_id, (text, meta)), ...]

    logger.info(f"[embeddings] Upserting {len(unique_docs):,} unique documents ...")
    upserted = 0
    failed   = 0
    total    = len(unique_docs)

    for i, (doc_id, (text, meta)) in enumerate(unique_docs):
        try:
            emb.add_document(doc_id, text, meta)
            upserted += 1
        except Exception as e:
            failed += 1

        if (i + 1) % 500 == 0 or i + 1 == total:
            logger.info(f"[embeddings]   {upserted:,}/{total:,} upserted ...")

    return {"upserted": upserted, "failed": failed, "total": total}


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    t0 = time.time()

    # Load embeddings module
    try:
        from ai import get_embeddings
        emb = get_embeddings()
        if not emb:
            return {"success": False, "error": "Embeddings module unavailable"}
    except Exception as e:
        return {"success": False, "error": f"Could not load embeddings: {e}"}

    # Check API — use add_document which is the correct method
    if not hasattr(emb, "add_document"):
        return {"success": False,
                "error": f"Embeddings module missing add_document. Methods: {[m for m in dir(emb) if not m.startswith('_')]}"}

    # Load KG
    from ai import get_knowledge_graph
    kg = get_knowledge_graph()

    # Collect docs from all sources
    all_docs: list[tuple[str, str, dict]] = []

    if kg:
        all_docs.extend(_docs_from_kg(kg))
    else:
        logger.warning("[embeddings] KG not available — skipping KG source")

    all_docs.extend(_docs_from_access_export())
    all_docs.extend(_docs_from_ocr_cache())

    logger.info(f"[embeddings] Total documents collected: {len(all_docs):,}")

    if not all_docs:
        return {"success": False, "error": "No documents found from any source"}

    # Get current count before
    try:
        count_before = emb._collection.count() if hasattr(emb, "_collection") else "unknown"
    except Exception:
        count_before = "unknown"

    # Upsert
    result = _upsert_to_chromadb(emb, all_docs)

    # Get count after
    try:
        count_after = emb._collection.count() if hasattr(emb, "_collection") else "unknown"
    except Exception:
        count_after = "unknown"

    elapsed = round(time.time() - t0, 1)

    # Write log
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log = {
        "run_at":        datetime.now().isoformat(),
        "elapsed_s":     elapsed,
        "docs_collected": len(all_docs),
        "count_before":  count_before,
        "count_after":   count_after,
        **result,
    }
    OUT_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")

    logger.success(
        f"[embeddings] Done in {elapsed}s — "
        f"upserted {result['upserted']:,}, failed {result.get('failed',0)} | "
        f"ChromaDB: {count_before} → {count_after} docs"
    )

    return {"success": True, "elapsed_seconds": elapsed, **log}


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, default=str))
