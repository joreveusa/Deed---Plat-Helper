"""
Legal Description Embeddings
===============================
Adapted from AI Surveyor ml/legal_embeddings.py.
Vector similarity search for legal descriptions using
sentence-transformers + ChromaDB.

Both dependencies are OPTIONAL — class returns empty results if missing.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ai import AI_DATA_DIR


# ── Paths ───────────────────────────────────────────────────────────────────

_CHROMA_DIR = AI_DATA_DIR / "chroma_db"

# ── Lazy imports ────────────────────────────────────────────────────────────

_EMBEDDINGS_AVAILABLE = False
_CHROMA_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    _EMBEDDINGS_AVAILABLE = True
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
    if record.get("client_name"):
        meta["client_name"] = str(record["client_name"])
    if record.get("source_file"):
        meta["source_file"] = str(record["source_file"])
    if record.get("township"):
        meta["township"] = str(record["township"])
    if record.get("range"):
        meta["range"] = str(record["range"])
    if record.get("sections"):
        meta["sections"] = ",".join(str(s) for s in record["sections"][:5])
    if record.get("max_acreage"):
        meta["acreage"] = float(record["max_acreage"])
    if record.get("land_grants"):
        meta["land_grants"] = ",".join(record["land_grants"][:3])
    return meta


# ══════════════════════════════════════════════════════════════════════════════
# EMBEDDING INDEX
# ══════════════════════════════════════════════════════════════════════════════

class LegalEmbeddingIndex:
    """Vector embedding index for legal description similarity search."""

    MODEL_NAME = "all-MiniLM-L6-v2"
    COLLECTION_NAME = "legal_descriptions"

    def __init__(self):
        self._model = None
        self._client = None
        self._collection = None
        self._init_backends()

    def _init_backends(self):
        if _EMBEDDINGS_AVAILABLE:
            try:
                self._model = SentenceTransformer(self.MODEL_NAME)
                logger.debug(f"Loaded embedding model: {self.MODEL_NAME}")
            except Exception as e:
                logger.debug(f"Embedding model not available: {e}")

        if _CHROMA_AVAILABLE:
            try:
                _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(_CHROMA_DIR)
                )
                self._collection = self._client.get_or_create_collection(
                    name=self.COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as e:
                logger.debug(f"ChromaDB not available: {e}")

    def status(self) -> dict:
        return {
            "embeddings_available": _EMBEDDINGS_AVAILABLE,
            "chroma_available": _CHROMA_AVAILABLE,
            "model_loaded": self._model is not None,
            "collection_exists": self._collection is not None,
            "document_count": (
                self._collection.count() if self._collection else 0
            ),
            "model_name": self.MODEL_NAME,
        }

    def find_similar(self, query: str, top_k: int = 10,
                      filters: Optional[dict] = None) -> list[dict]:
        """Find legal descriptions similar to the query text."""
        if not self._model or not self._collection:
            return []
        if self._collection.count() == 0:
            return []

        cleaned = _clean_legal_text(query)
        if not cleaned:
            return []

        query_embedding = self._model.encode([cleaned]).tolist()
        kwargs = {
            "query_embeddings": query_embedding,
            "n_results": min(top_k, self._collection.count()),
        }
        if filters:
            kwargs["where"] = filters

        try:
            results = self._collection.query(**kwargs)
        except Exception as e:
            logger.warning(f"Similarity search failed: {e}")
            return []

        formatted = []
        for i in range(len(results["ids"][0])):
            distance = (
                results["distances"][0][i] if results["distances"] else 0
            )
            similarity = round(1 - distance, 3)
            metadata = (
                results["metadatas"][0][i] if results["metadatas"] else {}
            )
            doc_text = (
                results["documents"][0][i] if results["documents"] else ""
            )
            formatted.append({
                "id": results["ids"][0][i],
                "similarity": similarity,
                "text": doc_text[:200],
                "client_name": metadata.get("client_name", ""),
                "source_file": metadata.get("source_file", ""),
                "township": metadata.get("township", ""),
                "range": metadata.get("range", ""),
                "acreage": metadata.get("acreage", ""),
                "land_grants": metadata.get("land_grants", ""),
            })
        return formatted

    def build_from_extractions(self, limit: int = 0) -> dict:
        """Build the embedding index from plat_text_extractions.json."""
        if not self._model:
            return {"success": False,
                     "error": "sentence-transformers not installed"}
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
            text_parts = []
            if record.get("client_name"):
                text_parts.append(f"Client: {record['client_name']}")
            if record.get("township"):
                text_parts.append(f"Township {record['township']}")
            if record.get("land_grants"):
                text_parts.append(
                    f"Grant: {', '.join(record['land_grants'][:2])}"
                )
            text = " | ".join(text_parts)
            if len(text) < 10:
                continue
            cleaned = _clean_legal_text(text)
            if cleaned:
                doc_id = filename.replace(" ", "_").replace(".", "_")
                to_index.append({
                    "id": doc_id,
                    "text": cleaned,
                    "metadata": _build_metadata(record),
                })

        if limit:
            to_index = to_index[:limit]
        if not to_index:
            return {"success": True, "indexed": 0}

        t0 = time.time()
        batch_size = 64
        total_indexed = 0

        for i in range(0, len(to_index), batch_size):
            batch = to_index[i:i + batch_size]
            texts = [r["text"] for r in batch]
            ids = [r["id"] for r in batch]
            metadatas = [r["metadata"] for r in batch]
            embeddings = self._model.encode(
                texts, show_progress_bar=False
            ).tolist()
            self._collection.upsert(
                ids=ids, embeddings=embeddings,
                documents=texts, metadatas=metadatas,
            )
            total_indexed += len(batch)

        elapsed = time.time() - t0
        return {
            "success": True,
            "indexed": total_indexed,
            "total_in_collection": self._collection.count(),
            "elapsed_seconds": round(elapsed, 1),
        }
