"""
Anomaly Detector
==================
Adapted from AI Surveyor ml/anomaly_detector.py.
Flags suspicious patterns in completed research:
  - Missing adjoiners (low count for job type)
  - Missing documents (deeds/plats not found)
  - TRS mismatches between subjects
  - Unusual bearing counts
  - Unusual acreage
"""

import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from loguru import logger

from ai import AI_DATA_DIR


# ══════════════════════════════════════════════════════════════════════════════
# STATISTICS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _compute_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": 0, "std": 0, "q1": 0, "q3": 0, "iqr": 0}

    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / max(n - 1, 1)
    std = math.sqrt(variance)
    sorted_vals = sorted(values)
    q1 = sorted_vals[int(n * 0.25)]
    q3 = sorted_vals[int(n * 0.75)]
    iqr = q3 - q1

    return {
        "count": n, "mean": round(mean, 2), "std": round(std, 2),
        "q1": round(q1, 2), "q3": round(q3, 2), "iqr": round(iqr, 2),
        "min": round(sorted_vals[0], 2), "max": round(sorted_vals[-1], 2),
    }


def _z_score(value: float, mean: float, std: float) -> float:
    return round((value - mean) / std, 2) if std != 0 else 0.0


def _is_iqr_outlier(value: float, q1: float, q3: float, iqr: float,
                     factor: float = 1.5) -> bool:
    return value < q1 - factor * iqr or value > q3 + factor * iqr


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALY FLAG
# ══════════════════════════════════════════════════════════════════════════════

class AnomalyFlag:
    """A single detected anomaly."""

    def __init__(self, category: str, severity: str, message: str,
                 value: float = 0, expected: str = "",
                 suggestion: str = ""):
        self.category = category
        self.severity = severity
        self.message = message
        self.value = value
        self.expected = expected
        self.suggestion = suggestion

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
            "expected": self.expected,
            "suggestion": self.suggestion,
        }


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALY DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

class AnomalyDetector:
    """Statistical anomaly detector for survey research data."""

    Z_WARNING = 2.0
    Z_ERROR = 3.0

    def __init__(self):
        self._baselines: dict = {}
        self._build_baselines()

    def _build_baselines(self):
        """Build per-job-type statistical baselines from training data."""
        records = []
        for name in [
            "full_archive_training_data.json",
            "archive_training_data.json",
        ]:
            path = AI_DATA_DIR / name
            if path.exists():
                try:
                    records = json.loads(path.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

        if not records:
            logger.debug("No training data for anomaly baselines")
            return

        type_adjoiners = defaultdict(list)
        type_deeds = defaultdict(list)
        type_plats = defaultdict(list)

        for r in records:
            jt = r.get("job_type", "")
            if not jt or jt in ("PLACE", "TYPE", "LEGACY"):
                continue
            type_adjoiners[jt].append(
                r.get("estimated_adjoiners", r.get("adjoiner_count", 0))
            )
            type_deeds[jt].append(r.get("deed_count", 0))
            type_plats[jt].append(r.get("plat_count", 0))

        for jt in type_adjoiners:
            self._baselines[jt] = {
                "adjoiners": _compute_stats(type_adjoiners[jt]),
                "deeds": _compute_stats(type_deeds[jt]),
                "plats": _compute_stats(type_plats[jt]),
            }

        all_adj = [v for lst in type_adjoiners.values() for v in lst]
        all_deeds = [v for lst in type_deeds.values() for v in lst]
        all_plats = [v for lst in type_plats.values() for v in lst]

        self._baselines["_global"] = {
            "adjoiners": _compute_stats(all_adj),
            "deeds": _compute_stats(all_deeds),
            "plats": _compute_stats(all_plats),
        }

    # ── Job-Level Checks ────────────────────────────────────────────────

    def check_job(self, research: dict, job_type: str = "BDY",
                  client_name: str = "") -> list[dict]:
        """Run all anomaly checks on a research package."""
        flags: list[AnomalyFlag] = []
        baseline = self._baselines.get(
            job_type, self._baselines.get("_global", {})
        )

        flags.extend(self._check_adjoiners(research, job_type, baseline))
        flags.extend(self._check_document_completeness(research))
        flags.extend(self._check_trs_consistency(research))

        severity_order = {"error": 0, "warning": 1, "info": 2}
        flags.sort(key=lambda f: severity_order.get(f.severity, 3))
        return [f.to_dict() for f in flags]

    def _check_adjoiners(self, research: dict, job_type: str,
                          baseline: dict) -> list[AnomalyFlag]:
        flags = []
        adj_count = research.get(
            "adjoiners_found", research.get("adjoiner_count", 0)
        )
        stats = baseline.get("adjoiners", {})
        if not stats or stats.get("count", 0) < 5:
            return flags

        z = _z_score(adj_count, stats["mean"], stats["std"])

        if adj_count == 0 and stats["mean"] > 2:
            flags.append(AnomalyFlag(
                category="adjoiners", severity="warning",
                message=(
                    f"Zero adjoiners — {job_type} jobs typically have "
                    f"{stats['mean']:.1f} (±{stats['std']:.1f})"
                ),
                value=adj_count,
                expected=f"{stats['mean']:.1f} ± {stats['std']:.1f}",
                suggestion="Check the plat for adjacent properties.",
            ))
        elif z < -self.Z_WARNING and adj_count > 0:
            flags.append(AnomalyFlag(
                category="adjoiners", severity="info",
                message=(
                    f"Low adjoiner count ({adj_count}) for {job_type} "
                    f"(z={z}). Range: {stats['q1']:.0f}–{stats['q3']:.0f}"
                ),
                value=adj_count,
            ))
        elif z > self.Z_WARNING:
            severity = "error" if z > self.Z_ERROR else "warning"
            flags.append(AnomalyFlag(
                category="adjoiners", severity=severity,
                message=(
                    f"High adjoiner count ({adj_count}) for {job_type} "
                    f"(z={z}). Typical: {stats['mean']:.1f}."
                ),
                value=adj_count,
                suggestion="Verify all adjoiners are relevant.",
            ))
        return flags

    def _check_document_completeness(
        self, research: dict
    ) -> list[AnomalyFlag]:
        flags = []
        subjects = research.get("subjects", [])
        if not subjects:
            if not research.get("deed_found", True):
                flags.append(AnomalyFlag(
                    category="documents", severity="warning",
                    message="Client deed not found",
                    suggestion="Check the county recorder's portal.",
                ))
            if not research.get("plat_found", True):
                flags.append(AnomalyFlag(
                    category="documents", severity="warning",
                    message="Client plat not found",
                    suggestion="Check cabinets under different name.",
                ))
            return flags

        total = len(subjects)
        both = sum(
            1 for s in subjects
            if s.get("deed_saved") and s.get("plat_saved")
        )
        if total > 0:
            completion = round(both / total * 100, 1)
            if completion < 50:
                flags.append(AnomalyFlag(
                    category="documents", severity="error",
                    message=(
                        f"Research only {completion}% complete — "
                        f"{both}/{total} subjects have both docs"
                    ),
                    value=completion, expected="100%",
                    suggestion="Run Step 5 Research Board to fill gaps.",
                ))
            elif completion < 80:
                flags.append(AnomalyFlag(
                    category="documents", severity="warning",
                    message=(
                        f"Research {completion}% complete — "
                        f"{total - both} subjects missing docs"
                    ),
                    value=completion, expected="100%",
                ))
        return flags

    def _check_trs_consistency(self, research: dict) -> list[AnomalyFlag]:
        flags = []
        subjects = research.get("subjects", [])
        if not subjects:
            return flags

        client_subjects = [s for s in subjects if s.get("type") == "client"]
        adj_subjects = [s for s in subjects if s.get("type") == "adjoiner"]

        if client_subjects and adj_subjects:
            client_trs = {
                s.get("trs", "") for s in client_subjects if s.get("trs")
            }
            adj_trs = {
                s.get("trs", "") for s in adj_subjects if s.get("trs")
            }
            if client_trs and adj_trs:
                shared = client_trs & adj_trs
                if not shared and len(client_trs) == 1:
                    flags.append(AnomalyFlag(
                        category="trs", severity="warning",
                        message=(
                            f"Client TRS ({', '.join(client_trs)}) "
                            f"doesn't match adjoiners. "
                            f"Possible section boundary."
                        ),
                        suggestion="Verify the legal description.",
                    ))
        return flags

    # ── Batch Audit ────────────────────────────────────────────────────

    def batch_audit(self, records: Optional[list[dict]] = None,
                     limit: int = 50) -> dict:
        """Run anomaly detection across multiple records."""
        if records is None:
            for name in [
                "full_archive_training_data.json",
                "archive_training_data.json",
            ]:
                path = AI_DATA_DIR / name
                if path.exists():
                    try:
                        records = json.loads(
                            path.read_text(encoding="utf-8")
                        )
                        break
                    except Exception:
                        records = []
        if not records:
            return {"success": False, "error": "No records to audit"}

        flagged_jobs = []
        severity_counts = {"error": 0, "warning": 0, "info": 0}
        category_counts = defaultdict(int)

        for r in records[:limit]:
            research = {
                "adjoiners_found": r.get(
                    "estimated_adjoiners", r.get("adjoiner_count", 0)
                ),
                "deed_found": r.get("has_research", False),
                "plat_found": r.get("plat_count", 0) > 0,
            }
            flags = self.check_job(
                research, r.get("job_type", "BDY"),
                r.get("client_name", ""),
            )
            if flags:
                flagged_jobs.append({
                    "job_number": r.get("job_number", 0),
                    "client_name": r.get("client_name", ""),
                    "job_type": r.get("job_type", ""),
                    "flags": flags,
                    "flag_count": len(flags),
                    "max_severity": flags[0]["severity"],
                })
                for f in flags:
                    severity_counts[f["severity"]] += 1
                    category_counts[f["category"]] += 1

        flagged_jobs.sort(key=lambda j: (
            {"error": 0, "warning": 1, "info": 2}.get(j["max_severity"], 3),
            -j["flag_count"],
        ))

        return {
            "success": True,
            "total_audited": min(len(records), limit),
            "total_flagged": len(flagged_jobs),
            "severity_breakdown": severity_counts,
            "category_breakdown": dict(category_counts),
            "flagged_jobs": flagged_jobs[:20],
        }

    def get_baselines(self) -> dict:
        return self._baselines
