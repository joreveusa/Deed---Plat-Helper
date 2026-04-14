"""
ML Prediction Engine
======================
Adapted from AI Surveyor ml/engine.py.
Predicts adjoiner count, cabinet letter, and research duration
using scikit-learn Random Forest / Gradient Boosting.

Models are persisted to data/ai/models/ via joblib.
"""

import json
import re
import time
from pathlib import Path
from collections import Counter
from datetime import datetime

import numpy as np
from loguru import logger

from ai import AI_DATA_DIR, load_ai_config

# Lazy imports for sklearn
_sklearn_available = False
try:
    from sklearn.ensemble import (
        RandomForestRegressor, RandomForestClassifier,
        GradientBoostingRegressor, GradientBoostingClassifier,
    )
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error, accuracy_score
    import joblib
    _sklearn_available = True
except ImportError:
    pass


# ── Config ──────────────────────────────────────────────────────────────────

_AI_CFG = load_ai_config()
_MIN_TRAINING_JOBS = _AI_CFG.get("min_training_jobs", 30)

# ── Model directory: prefer AI Surveyor's models (single source of truth) ──
# AI Surveyor's nightly retrain updates data/models/ — we use those.
# Falls back to our own data/ai/models/ if AI Surveyor isn't present.
def _resolve_models_dir() -> Path:
    ai_surveyor_models = Path("J:/Under Development/AI Surveyor/data/models")
    if ai_surveyor_models.exists():
        return ai_surveyor_models
    return AI_DATA_DIR / "models"

_MODELS_DIR = _resolve_models_dir()
_MODELS_DIR.mkdir(parents=True, exist_ok=True)


# ── Job type encoding ──────────────────────────────────────────────────────

_JOB_TYPE_MAP = {
    "BDY": 1, "ILR": 2, "SE": 3, "SUB": 4, "TIE": 5,
    "TOPO": 6, "ELEV": 7, "ALTA": 8, "CONS": 9, "CNS": 9,
    "BL": 10, "FNF": 11, "POL": 12, "LS": 13, "TPG": 14,
    "LT": 15, "MP": 16, "PLAT": 17, "OTHER": 20,
}
_CABINET_MAP = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}

_SUBDIVISION_KEYWORDS = {
    "subdivision", "sub", "addition", "estates", "ranch",
    "village", "country club", "condos", "condo", "townhomes",
    "plaza", "park", "heights", "acres", "lots",
}


def _is_subdivision(client_name: str) -> bool:
    lower = client_name.lower()
    return any(kw in lower for kw in _SUBDIVISION_KEYWORDS)


# ── Feature encoding ──────────────────────────────────────────────────────

def _encode_features(record: dict, *, for_adjoiners: bool = False) -> list[float]:
    """Encode a record into a feature vector.

    When for_adjoiners=True, omits deed/plat counts to prevent leakage.
    """
    deed_count = record.get("deed_count", record.get("deeds_saved", 0))
    plat_count = record.get("plat_count", record.get("plats_saved", 0))
    total = deed_count + plat_count
    ratio = deed_count / plat_count if plat_count > 0 else (
        2.0 if deed_count > 0 else 0.0
    )

    kg_prior = record.get("kg_prior_jobs", 0)
    kg_adj = record.get("kg_known_adjoiners", 0)
    kg_area = record.get("kg_area_avg_adjoiners", 0.0)

    base = [
        _JOB_TYPE_MAP.get(record.get("job_type", ""), 20),
        record.get("range_num", 0),
        record.get("total_subjects", 1),
        1.0 if record.get("has_research", total > 0) else 0.0,
        1.0 if record.get("has_drafting", False) else 0.0,
        1.0 if record.get("has_fieldwork", False) else 0.0,
    ]

    if for_adjoiners:
        base.extend([
            1.0 if _is_subdivision(record.get("client_name", "")) else 0.0,
            min(kg_prior, 10),
            min(kg_adj, 20),
            round(min(kg_area, 15.0), 1),
        ])
    else:
        base.extend([
            min(deed_count, 30),
            min(plat_count, 30),
            1.0 if _is_subdivision(record.get("client_name", "")) else 0.0,
            min(ratio, 5.0),
            min(kg_prior, 10),
            min(kg_adj, 20),
            round(min(kg_area, 15.0), 1),
        ])

    return base


# ── Training data scanner ──────────────────────────────────────────────────

def scan_training_data(survey_data_path: str) -> list[dict]:
    """Scan completed research.json files for ML training data."""
    if not survey_data_path or not Path(survey_data_path).exists():
        return []

    records = []
    survey = Path(survey_data_path)

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
                    subjects = data.get("subjects", [])
                    total = len(subjects)
                    adj_count = sum(
                        1 for s in subjects if s.get("type") == "adjoiner"
                    )
                    deeds = sum(1 for s in subjects if s.get("deed_saved"))
                    plats = sum(1 for s in subjects if s.get("plat_saved"))
                    both = sum(
                        1 for s in subjects
                        if s.get("deed_saved") and s.get("plat_saved")
                    )
                    completion = round(both / total * 100, 1) if total else 0

                    cabinets = []
                    for s in subjects:
                        pp = s.get("plat_path", "")
                        if pp:
                            cm = re.search(r'Cab(?:inet)?\s*([A-Fa-f])', pp, re.I)
                            if cm:
                                cabinets.append(cm.group(1).upper())

                    mtime = research_file.stat().st_mtime
                    ctime = research_file.stat().st_ctime
                    range_num = 0
                    rm = re.match(r'^(\d+)', range_dir.name)
                    if rm:
                        range_num = int(rm.group(1))

                    records.append({
                        "job_number": job_number,
                        "client_name": client_name,
                        "job_type": job_type,
                        "range_num": range_num,
                        "total_subjects": total,
                        "adjoiner_count": adj_count,
                        "deeds_saved": deeds,
                        "plats_saved": plats,
                        "both_saved": both,
                        "completion_pct": completion,
                        "cabinets": cabinets,
                        "primary_cabinet": cabinets[0] if cabinets else "",
                        "duration_days": max(
                            0, round((mtime - ctime) / 86400, 1)
                        ),
                    })
                except Exception:
                    continue

    records.sort(key=lambda r: r["job_number"], reverse=True)
    logger.info(f"📊 Scanned {len(records)} completed research sessions")
    return records


# ── KG enrichment ──────────────────────────────────────────────────────────

def _enrich_with_kg(records: list[dict]) -> list[dict]:
    """Enrich training records with knowledge graph features."""
    try:
        from ai.knowledge_graph import SurveyKnowledgeGraph
        kg = SurveyKnowledgeGraph()
    except Exception:
        return records

    if kg.G.number_of_nodes() == 0:
        return records

    type_adj_sums = {}
    type_adj_counts = {}
    for r in records:
        jt = r.get("job_type", "BDY")
        adj = r.get("adjoiner_count", r.get("estimated_adjoiners", 0))
        if adj > 0:
            type_adj_sums[jt] = type_adj_sums.get(jt, 0) + adj
            type_adj_counts[jt] = type_adj_counts.get(jt, 0) + 1

    type_adj_avgs = {
        jt: round(type_adj_sums[jt] / type_adj_counts[jt], 1)
        for jt in type_adj_sums
    }

    for r in records:
        client = r.get("client_name", "")
        jt = r.get("job_type", "BDY")
        if client:
            r["kg_prior_jobs"] = len(kg.get_person_jobs(client))
            r["kg_known_adjoiners"] = len(kg.get_adjoiners(client))
        else:
            r["kg_prior_jobs"] = 0
            r["kg_known_adjoiners"] = 0
        r["kg_area_avg_adjoiners"] = type_adj_avgs.get(jt, 0.0)

    return records


# ══════════════════════════════════════════════════════════════════════════════
# PREDICTOR CLASS
# ══════════════════════════════════════════════════════════════════════════════

class SurveyPredictor:
    """ML prediction engine for survey research."""

    def __init__(self):
        self.adj_model = None
        self.cab_model = None
        self.time_model = None
        self.training_stats: dict = {}
        self._load_models()

    # ── Model Persistence ───────────────────────────────────────────────

    def _load_models(self):
        if not _sklearn_available:
            return

        _EXPECTED_FEATURES = {
            "adj_predictor.pkl": 10,
            "cab_predictor.pkl": 13,
            "time_predictor.pkl": 13,
        }

        for name, attr in [
            ("adj_predictor.pkl", "adj_model"),
            ("cab_predictor.pkl", "cab_model"),
            ("time_predictor.pkl", "time_model"),
        ]:
            path = _MODELS_DIR / name
            if path.exists():
                try:
                    model = joblib.load(path)
                    expected = _EXPECTED_FEATURES.get(name, 0)
                    n_features = getattr(model, 'n_features_in_', 0)
                    if expected and n_features and n_features != expected:
                        logger.warning(
                            f"  ⚠️ {name}: stale model (expected "
                            f"{expected} features, got {n_features}). "
                            f"Discarding — will retrain."
                        )
                        path.unlink()
                        continue
                    setattr(self, attr, model)
                    logger.debug(f"  Loaded model: {name}")
                except Exception as e:
                    logger.debug(f"  Failed to load {name}: {e}")

        stats_path = _MODELS_DIR / "training_stats.json"
        if stats_path.exists():
            try:
                self.training_stats = json.loads(
                    stats_path.read_text(encoding="utf-8")
                )
            except Exception:
                pass

    def _save_models(self):
        if not _sklearn_available:
            return
        for name, model in [
            ("adj_predictor.pkl", self.adj_model),
            ("cab_predictor.pkl", self.cab_model),
            ("time_predictor.pkl", self.time_model),
        ]:
            if model is not None:
                joblib.dump(model, _MODELS_DIR / name)

        stats_path = _MODELS_DIR / "training_stats.json"
        stats_path.write_text(
            json.dumps(self.training_stats, indent=2), encoding="utf-8"
        )

    # ── Training ────────────────────────────────────────────────────────

    def train(self, survey_data_path: str = "") -> dict:
        """Train all models on historical research data."""
        if not _sklearn_available:
            return {"success": False, "error": "scikit-learn not installed"}

        # Find survey data path from Deed Helper
        if not survey_data_path:
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from app import get_survey_data_path
                survey_data_path = get_survey_data_path()
            except Exception:
                pass

        records = []
        if survey_data_path:
            records = scan_training_data(survey_data_path)

        # Load archived training data
        for name in [
            "full_archive_training_data.json",
            "archive_training_data.json",
        ]:
            archive_json = AI_DATA_DIR / name
            if archive_json.exists():
                try:
                    archive_records = json.loads(
                        archive_json.read_text(encoding="utf-8")
                    )
                    for ar in archive_records:
                        if ar.get("job_type") in ("PLACE", "TYPE", "LEGACY"):
                            continue
                        records.append({
                            "job_number": ar["job_number"],
                            "client_name": ar["client_name"],
                            "job_type": ar["job_type"],
                            "range_num": (ar["job_number"] // 100) * 100,
                            "total_subjects": 1 + ar.get(
                                "estimated_adjoiners", 0
                            ),
                            "adjoiner_count": ar.get(
                                "estimated_adjoiners", 0
                            ),
                            "deeds_saved": ar.get("deed_count", 0),
                            "plats_saved": ar.get("plat_count", 0),
                            "deed_count": ar.get("deed_count", 0),
                            "plat_count": ar.get("plat_count", 0),
                            "both_saved": min(
                                ar.get("deed_count", 0),
                                ar.get("plat_count", 0),
                            ),
                            "completion_pct": (
                                100 if ar.get("has_research") else 0
                            ),
                            "cabinets": [],
                            "primary_cabinet": "",
                            "duration_days": 0,
                            "has_research": ar.get("has_research", False),
                            "has_drafting": ar.get("has_drafting", False),
                            "has_fieldwork": ar.get("has_fieldwork", False),
                        })
                    logger.info(
                        f"📦 Loaded {len(archive_records)} archive records"
                    )
                except Exception as e:
                    logger.debug(f"Archive data load failed: {e}")
                break  # only load first found

        records = _enrich_with_kg(records)

        if len(records) < 10:
            return {
                "success": False,
                "error": (
                    f"Need at least 10 training records, "
                    f"found {len(records)}"
                ),
                "jobs_found": len(records),
            }

        logger.info(f"🎓 Training ML models on {len(records)} records")
        start = time.time()

        metrics = {}
        metrics["adjoiner"] = self._train_adjoiner_model(records)
        metrics["cabinet"] = self._train_cabinet_model(records)
        metrics["duration"] = self._train_duration_model(records)

        elapsed = time.time() - start

        self.training_stats = {
            "trained_at": datetime.now().isoformat(),
            "training_jobs": len(records),
            "elapsed_seconds": round(elapsed, 1),
            "metrics": metrics,
        }
        self._save_models()

        logger.success(
            f"✅ Training complete in {elapsed:.1f}s — "
            f"{len(records)} jobs, "
            f"adj MAE={metrics.get('adjoiner', {}).get('mae', '?')}"
        )
        return {
            "success": True,
            "jobs_trained": len(records),
            "elapsed_seconds": round(elapsed, 1),
            "metrics": metrics,
        }

    def _train_adjoiner_model(self, records: list[dict]) -> dict:
        adj_records = [
            r for r in records
            if r.get("job_number", 0) > 0
            or r.get("adjoiner_count", 0) > 0
        ]
        if len(adj_records) < 10:
            return {"error": "Not enough adj data", "records": len(adj_records)}

        X = np.array([
            _encode_features(r, for_adjoiners=True) for r in adj_records
        ])
        y = np.array([r["adjoiner_count"] for r in adj_records])

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        rf = RandomForestRegressor(
            n_estimators=100, max_depth=8, random_state=42
        )
        rf.fit(X_train, y_train)
        rf_mae = mean_absolute_error(y_test, rf.predict(X_test))

        gb = GradientBoostingRegressor(
            n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42
        )
        gb.fit(X_train, y_train)
        gb_mae = mean_absolute_error(y_test, gb.predict(X_test))

        if gb_mae < rf_mae:
            self.adj_model = gb
            best = "gradient_boosting"
            mae = round(gb_mae, 2)
        else:
            self.adj_model = rf
            best = "random_forest"
            mae = round(rf_mae, 2)

        return {
            "mae": mae, "train_size": len(X_train),
            "test_size": len(X_test),
            "mean_actual": round(float(y.mean()), 1),
            "model_selected": best,
        }

    def _train_cabinet_model(self, records: list[dict]) -> dict:
        cab_records = [r for r in records if r.get("primary_cabinet")]
        if len(cab_records) < 10:
            return {"error": "Not enough cabinet data"}

        X = np.array([_encode_features(r) for r in cab_records])
        y = np.array([
            _CABINET_MAP.get(r["primary_cabinet"], 2) for r in cab_records
        ])

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        rf = RandomForestClassifier(
            n_estimators=100, max_depth=6, random_state=42,
            class_weight='balanced',
        )
        rf.fit(X_train, y_train)
        rf_acc = accuracy_score(y_test, rf.predict(X_test))

        gb = GradientBoostingClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42
        )
        gb.fit(X_train, y_train)
        gb_acc = accuracy_score(y_test, gb.predict(X_test))

        if gb_acc > rf_acc:
            self.cab_model = gb
            best = "gradient_boosting"
            acc = round(gb_acc, 3)
        else:
            self.cab_model = rf
            best = "random_forest"
            acc = round(rf_acc, 3)

        return {
            "accuracy": acc, "model_selected": best,
            "cabinet_distribution": dict(
                Counter(r["primary_cabinet"] for r in cab_records)
            ),
        }

    def _train_duration_model(self, records: list[dict]) -> dict:
        dur_records = [
            r for r in records if 0 < r.get("duration_days", 0) < 365
        ]
        if len(dur_records) < 10:
            return {"error": "Not enough duration data"}

        X = np.array([_encode_features(r) for r in dur_records])
        y = np.array([r["duration_days"] for r in dur_records])

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        model = RandomForestRegressor(
            n_estimators=50, max_depth=6, random_state=42
        )
        model.fit(X_train, y_train)
        mae = round(mean_absolute_error(y_test, model.predict(X_test)), 2)
        self.time_model = model

        return {
            "mae_days": mae,
            "mean_duration_days": round(float(y.mean()), 1),
        }

    # ── Prediction ──────────────────────────────────────────────────────

    def predict_adjoiners(self, job_type: str = "BDY",
                          range_num: int = 0,
                          client_name: str = "") -> dict:
        """Predict adjoiner count for a job."""
        if self.adj_model is None:
            return self._fallback_prediction(job_type)

        record = {
            "job_type": job_type, "range_num": range_num,
            "total_subjects": 1, "client_name": client_name,
        }
        features = [_encode_features(record, for_adjoiners=True)]
        predicted = round(float(self.adj_model.predict(features)[0]), 1)

        return {
            "predicted_adjoiners": predicted,
            "model": self.training_stats.get("metrics", {}).get(
                "adjoiner", {}
            ).get("model_selected", "ensemble"),
        }

    def predict_cabinet(self, job_type: str = "BDY",
                        client_name: str = "") -> dict:
        """Predict the most likely cabinet letter."""
        inv = {v: k for k, v in _CABINET_MAP.items()}

        if self.cab_model is None:
            return {"predicted_cabinet": "C", "model": "fallback"}

        record = {
            "job_type": job_type, "total_subjects": 1,
            "client_name": client_name,
        }
        features = [_encode_features(record)]
        predicted_idx = int(self.cab_model.predict(features)[0])
        probs = self.cab_model.predict_proba(features)[0]

        top = sorted(
            zip(self.cab_model.classes_, probs),
            key=lambda x: x[1], reverse=True,
        )[:3]

        return {
            "predicted_cabinet": inv.get(predicted_idx, "C"),
            "top_predictions": [
                {"cabinet": inv.get(int(cls), "?"),
                 "probability": round(float(p), 3)}
                for cls, p in top
            ],
        }

    def predict_complexity(self, job_type: str = "BDY",
                           client_name: str = "") -> dict:
        """Predict overall job complexity (combines adj + cabinet + duration)."""
        adj = self.predict_adjoiners(job_type, client_name=client_name)
        cab = self.predict_cabinet(job_type, client_name=client_name)

        adj_count = adj.get("predicted_adjoiners", 5)
        if adj_count > 10:
            complexity = "high"
        elif adj_count > 5:
            complexity = "moderate"
        else:
            complexity = "low"

        return {
            "complexity": complexity,
            "predicted_adjoiners": adj_count,
            "predicted_cabinet": cab.get("predicted_cabinet", "C"),
            "confidence": adj.get("model", "fallback"),
        }

    def _fallback_prediction(self, job_type: str) -> dict:
        """Statistical fallback when no model is available."""
        defaults = {
            "BDY": 6.0, "ILR": 3.0, "SE": 4.0, "SUB": 8.0,
            "TOPO": 2.0, "ALTA": 5.0,
        }
        return {
            "predicted_adjoiners": defaults.get(job_type, 5.0),
            "model": "fallback_statistical",
        }

    def get_training_stats(self) -> dict:
        """Return info about the last training run."""
        return self.training_stats
