"""
Legal Description Embeddings
===============================
Adapted from AI Surveyor ml/legal_embeddings.py.
Vector similarity search for legal descriptions.

Embedding backend priority:
  1. sentence-transformers (all-MiniLM-L6-v2) — local, fast, no API call
  2. Ollama nomic-embed-text — no extra pip install, uses running Ollama
  3. Unavailable — returns empty results, logs warning

ChromaDB is used for vector storage in both cases.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from ai import AI_DATA_DIR


# ── Paths ───────────────────────────────────────────────────────────────────

_CHROMA_DIR = AI_DATA_DIR / "chroma_db"

# ── Lazy imports ────────────────────────────────────────────────────────────

_ST_AVAILABLE = False
_CHROMA_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except ImportError:
    pass

try:
    import chromadb
    _CHROMA_AVAILABLE = True
except ImportError:
    pass


# ── Text preparation ──────────────────────────────────────────────────────

def _clean_legal_text(text: str) -> str:
    """Clean and normalize legal description text for embedding."""
    if not text:
        return ""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[|]{2,}', '', text)
    text = re.sub(r'[-]{5,}', '', text)
    text = re.sub(r'[_]{3,}', '', text)
    text = re.sub(r'\.{3,}', '', text)
    text = re.sub(
        r'(\d+)\s*[°d]\s*(\d+)\s*[\'m]\s*(\d+)\s*["s]?',
        r'\1°\2\'\3"', text,
    )
    if len(text) > 2048:
        text = text[:2048]
    return text


def _build_metadata(record: dict) -> dict:
    meta = {}
    for k in ("client_name", "source_file", "township", "range",
              "owner", "upc", "plat", "trs", "job_number", "desc_type",
              "doc_no"):
        if record.get(k):
            meta[k] = str(record[k])
    if record.get("sections"):
        meta["sections"] = ",".join(str(s) for s in record["sections"][:5])
    if record.get("max_acreage"):
        meta["acreage"] = float(record["max_acreage"])
    if record.get("land_grants"):
        meta["land_grants"] = ",".join(record["land_grants"][:3])
    return meta


# ── Ollama embedding helper ────────────────────────────────────────────────

def _ollama_embed(text: str, model: str = "nomic-embed-text",
                  url: str = "http://localhost:11434") -> list[float] | None:
    """Get a single embedding vector from Ollama. Returns None on failure."""
    try:
        r = httpx.post(
            f"{url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=20.0,
        )
        r.raise_for_status()
        return r.json().get("embedding")
    except Exception as e:
        logger.debug(f"[embeddings] Ollama embed failed: {e}")
        return None


def _ollama_embed_batch(texts: list[str], model: str, url: str) -> list[list[float]]:
    """Embed a list of texts via Ollama (sequential — Ollama has no batch endpoint)."""
    results = []
    for t in texts:
        vec = _ollama_embed(t, model=model, url=url)
        results.append(vec)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# EMBEDDING INDEX
# ══════════════════════════════════════════════════════════════════════════════

class LegalEmbeddingIndex:
    """
    Vector embedding index for legal description similarity search.

    Backend selection (automatic):
      - sentence-transformers if installed  → all-MiniLM-L6-v2 (384-dim)
      - else Ollama nomic-embed-text        → 768-dim
      - else unavailable
    """

    ST_MODEL_NAME   = "all-MiniLM-L6-v2"
    OLLAMA_MODEL    = "nomic-embed-text"
    COLLECTION_NAME = "legal_descriptions"

    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self._st_model   = None   # sentence-transformers model or None
        self._ollama_url = ollama_url
        self._use_ollama = False  # True when falling back to Ollama
        self._client     = None   # chromadb client
        self._collection = None   # chromadb collection
        self._init_backends()

    # ── Initialization ───────────────────────────────────────────────────────

    def _init_backends(self):
        # 1. Try sentence-transformers
        if _ST_AVAILABLE:
            try:
                self._st_model = SentenceTransformer(self.ST_MODEL_NAME)
                logger.info(f"[embeddings] Loaded sentence-transformers: {self.ST_MODEL_NAME}")
            except Exception as e:
                logger.warning(f"[embeddings] sentence-transformers load failed: {e}")

        # 2. If ST unavailable, probe Ollama
        if not self._st_model:
            probe = _ollama_embed("test", model=self.OLLAMA_MODEL, url=self._ollama_url)
            if probe:
                self._use_ollama = True
                logger.info(f"[embeddings] Using Ollama backend: {self.OLLAMA_MODEL}")
            else:
                logger.info("[embeddings] No embedding backend available (install sentence-transformers or start Ollama)")

        # 3. Init ChromaDB
        if _CHROMA_AVAILABLE:
            try:
                _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
                self._collection = self._client.get_or_create_collection(
                    name=self.COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(f"[embeddings] ChromaDB collection: {self._collection.count()} docs")
            except Exception as e:
                logger.warning(f"[embeddings] ChromaDB init failed: {e}")

    @property
    def _backend_available(self) -> bool:
        return bool(self._st_model or self._use_ollama)

    @property
    def backend_name(self) -> str:
        if self._st_model:
            return f"sentence-transformers ({self.ST_MODEL_NAME})"
        if self._use_ollama:
            return f"ollama ({self.OLLAMA_MODEL})"
        return "none"

    # ── Embed helper ─────────────────────────────────────────────────────────

    def _embed_one(self, text: str) -> list[float] | None:
        if self._st_model:
            return self._st_model.encode([text]).tolist()[0]
        if self._use_ollama:
            return _ollama_embed(text, model=self.OLLAMA_MODEL, url=self._ollama_url)
        return None

    def _embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        if self._st_model:
            return self._st_model.encode(texts, show_progress_bar=False).tolist()
        if self._use_ollama:
            return _ollama_embed_batch(texts, model=self.OLLAMA_MODEL, url=self._ollama_url)
        return [None] * len(texts)

    # ── Public API ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "available":        self._backend_available and self._collection is not None,
            "backend":          self.backend_name,
            "st_available":     _ST_AVAILABLE,
            "ollama_available": self._use_ollama,
            "chroma_available": _CHROMA_AVAILABLE,
            "model_loaded":     self._backend_available,
            "collection_exists": self._collection is not None,
            "document_count":   (self._collection.count() if self._collection else 0),
        }

    def find_similar(self, query: str, top_k: int = 10,
                     filters: Optional[dict] = None) -> list[dict]:
        """Find legal descriptions similar to the query text."""
        if not self._backend_available or not self._collection:
            return []
        if self._collection.count() == 0:
            return []

        cleaned = _clean_legal_text(query)
        if not cleaned:
            return []

        vec = self._embed_one(cleaned)
        if vec is None:
            return []

        kwargs: dict = {
            "query_embeddings": [vec],
            "n_results": min(top_k, self._collection.count()),
        }
        if filters:
            kwargs["where"] = filters

        try:
            results = self._collection.query(**kwargs)
        except Exception as e:
            logger.warning(f"[embeddings] Similarity search failed: {e}")
            return []

        formatted = []
        for i in range(len(results["ids"][0])):
            distance = results["distances"][0][i] if results.get("distances") else 0.0
            metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
            doc_text = results["documents"][0][i] if results.get("documents") else ""
            formatted.append({
                "id":          results["ids"][0][i],
                "distance":    round(float(distance), 4),
                "similarity":  round(1 - float(distance), 3),
                "text":        doc_text[:200],
                "metadata":    metadata,
                # Legacy flat fields kept for backwards compat
                "client_name": metadata.get("client_name", ""),
                "owner":       metadata.get("owner", ""),
                "upc":         metadata.get("upc", ""),
                "plat":        metadata.get("plat", ""),
                "source_file": metadata.get("source_file", ""),
                "township":    metadata.get("township", ""),
                "range":       metadata.get("range", ""),
                "acreage":     metadata.get("acreage", ""),
            })
        return formatted

    def add_document(self, doc_id: str, text: str, metadata: dict | None = None) -> bool:
        """
        Add (or update) a single document in the index.
        Called by /api/ai/embed for fire-and-forget background indexing.
        Returns True on success.
        """
        if not self._backend_available or not self._collection:
            return False

        cleaned = _clean_legal_text(text)
        if not cleaned:
            return False

        vec = self._embed_one(cleaned)
        if vec is None:
            return False

        try:
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[vec],
                documents=[cleaned],
                metadatas=[_build_metadata(metadata or {})],
            )
            return True
        except Exception as e:
            logger.warning(f"[embeddings] add_document failed for {doc_id}: {e}")
            return False

    def build_from_extractions(self, limit: int = 0) -> dict:
        """Build the embedding index from plat_text_extractions.json."""
        if not self._backend_available:
            return {"success": False,
                    "error": "No embedding backend (install sentence-transformers or start Ollama)"}
        if not self._collection:
            return {"success": False, "error": "ChromaDB not installed"}

        ocr_path = AI_DATA_DIR / "plat_text_extractions.json"
        if not ocr_path.exists():
            return {"success": False, "error": "No OCR results found"}

        data = json.loads(ocr_path.read_text(encoding="utf-8"))

        to_index = []
        for filename, record in data.items():
            if not isinstance(record, dict) or not record.get("has_text"):
                continue
            parts = []
            if record.get("client_name"):
                parts.append(f"Client: {record['client_name']}")
            if record.get("township"):
                parts.append(f"Township {record['township']}")
            if record.get("land_grants"):
                parts.append(f"Grant: {', '.join(record['land_grants'][:2])}")
            text = " | ".join(parts)
            if len(text) < 10:
                continue
            cleaned = _clean_legal_text(text)
            if cleaned:
                doc_id = filename.replace(" ", "_").replace(".", "_")
                to_index.append({
                    "id": doc_id, "text": cleaned,
                    "metadata": _build_metadata(record),
                })

        if limit:
            to_index = to_index[:limit]
        if not to_index:
            return {"success": True, "indexed": 0}

        t0 = time.time()
        total_indexed = 0

        if self._st_model:
            # Batch encode with sentence-transformers (fast)
            batch_size = 64
            for i in range(0, len(to_index), batch_size):
                batch   = to_index[i:i + batch_size]
                texts   = [r["text"] for r in batch]
                ids     = [r["id"] for r in batch]
                metas   = [r["metadata"] for r in batch]
                vecs    = self._st_model.encode(texts, show_progress_bar=False).tolist()
                self._collection.upsert(
                    ids=ids, embeddings=vecs,
                    documents=texts, metadatas=metas,
                )
                total_indexed += len(batch)
        else:
            # Ollama: sequential (no batch endpoint)
            for r in to_index:
                ok = self.add_document(r["id"], r["text"], r["metadata"])
                if ok:
                    total_indexed += 1

        elapsed = time.time() - t0
        return {
            "success": True,
            "indexed": total_indexed,
            "backend": self.backend_name,
            "total_in_collection": self._collection.count(),
            "elapsed_seconds": round(elapsed, 1),
        }

    def build_from_research_sessions(self, survey_data_path: str,
                                     limit: int = 0) -> dict:
        """Batch-index deed legal descriptions from live research.json sessions.

        Scans every completed research.json on the survey drive, extracts any
        saved legal description text, and upserts it into the ChromaDB
        collection.  Documents are keyed by deed doc-number so re-runs are
        idempotent (upsert, not insert).

        Args:
            survey_data_path: Root of the Survey Data folder (e.g. J:\\Survey Data)
            limit: If >0, stop after indexing this many documents (for testing).

        Returns:
            { success, indexed, skipped, backend, total_in_collection, elapsed_seconds }
        """
        if not self._backend_available:
            return {"success": False,
                    "error": "No embedding backend (install sentence-transformers or start Ollama)"}
        if not self._collection:
            return {"success": False, "error": "ChromaDB not installed"}

        survey = Path(survey_data_path)
        if not survey.exists():
            return {"success": False, "error": f"Survey path not found: {survey_data_path}"}

        to_index: list[dict] = []
        skipped = 0

        for range_dir in survey.iterdir():
            if not range_dir.is_dir() or range_dir.name.startswith("00"):
                continue
            for job_dir in range_dir.iterdir():
                if not job_dir.is_dir():
                    continue
                m = re.match(r'^(\d{4})\s+(.*)', job_dir.name)
                if not m:
                    continue
                job_number = int(m.group(1))
                client_name = m.group(2).strip()

                for sub_dir in job_dir.iterdir():
                    if not sub_dir.is_dir():
                        continue
                    mt = re.match(r'^\d+-01-([A-Z]+)\s', sub_dir.name)
                    if not mt:
                        continue
                    job_type = mt.group(1)

                    research_file = sub_dir / "E Research" / "research.json"
                    if not research_file.exists():
                        continue

                    try:
                        data = json.loads(
                            research_file.read_text(encoding="utf-8")
                        )
                    except Exception:
                        skipped += 1
                        continue

                    for subject in data.get("subjects", []):
                        # Try pre-extracted legal text first
                        legal_text = (
                            subject.get("legal_description")
                            or subject.get("description")
                            or subject.get("full_text", "")
                        )

                        # Fallback: read from the saved deed PDF directly
                        if (not legal_text or len(legal_text) < 30):
                            deed_path = subject.get("deed_path", "")
                            if deed_path and Path(deed_path).exists():
                                try:
                                    from helpers.pdf_extract import extract_pdf_text
                                    legal_text, _ = extract_pdf_text(str(deed_path))
                                except Exception as pdf_err:
                                    logger.debug(f"[embeddings] PDF read failed {deed_path}: {pdf_err}")
                                    legal_text = ""

                        if not legal_text or len(legal_text) < 30:
                            skipped += 1
                            continue

                        # Build index text — subject name + job context + deed text
                        index_text = f"{subject.get('name', client_name)} | Job {job_number} | {legal_text}"
                        cleaned = _clean_legal_text(index_text)
                        if not cleaned:
                            skipped += 1
                            continue

                        doc_no = (
                            subject.get("doc_no")
                            or subject.get("document_number")
                            or subject.get("name", "")
                        )
                        doc_id = f"deed_{job_number}_{re.sub(r'[^a-zA-Z0-9_]', '_', str(doc_no or hash(cleaned)))}"

                        meta = _build_metadata({
                            "owner":       subject.get("name", client_name),
                            "client_name": client_name,
                            "job_number":  str(job_number),
                            "job_type":    job_type,
                            "upc":         subject.get("upc", ""),
                            "trs":         subject.get("trs", ""),
                            "doc_no":      str(doc_no),
                            "source_file": str(research_file),
                        })

                        to_index.append({"id": doc_id, "text": cleaned,
                                         "metadata": meta})
                        if limit and len(to_index) >= limit:
                            break
                    if limit and len(to_index) >= limit:
                        break
                if limit and len(to_index) >= limit:
                    break
            if limit and len(to_index) >= limit:
                break

        if not to_index:
            return {
                "success": True, "indexed": 0, "skipped": skipped,
                "reason": "No deed descriptions found in research sessions",
            }

        logger.info(f"[embeddings] Indexing {len(to_index)} deed descriptions "
                    f"({skipped} skipped)…")
        t0 = time.time()
        total_indexed = 0

        if self._st_model:
            batch_size = 64
            for i in range(0, len(to_index), batch_size):
                batch = to_index[i:i + batch_size]
                texts = [r["text"] for r in batch]
                ids   = [r["id"]   for r in batch]
                metas = [r["metadata"] for r in batch]
                try:
                    vecs = self._st_model.encode(
                        texts, show_progress_bar=False
                    ).tolist()
                    self._collection.upsert(
                        ids=ids, embeddings=vecs,
                        documents=texts, metadatas=metas,
                    )
                    total_indexed += len(batch)
                except Exception as e:
                    logger.warning(f"[embeddings] Batch {i} failed: {e}")
        else:
            for r in to_index:
                if self.add_document(r["id"], r["text"], r["metadata"]):
                    total_indexed += 1

        elapsed = time.time() - t0
        logger.success(
            f"[embeddings] ✅ Indexed {total_indexed} deeds in {elapsed:.1f}s "
            f"— collection now {self._collection.count()} docs"
        )
        return {
            "success":             True,
            "indexed":             total_indexed,
            "skipped":             skipped,
            "backend":             self.backend_name,
            "total_in_collection": self._collection.count(),
            "elapsed_seconds":     round(elapsed, 1),
        }

