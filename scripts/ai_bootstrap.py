"""
AI Bootstrap — Scan archive, populate knowledge graph, train ML models.
Run this once to seed the AI with historical data.

Usage:
    python scripts/ai_bootstrap.py
"""

import sys
import json
import re
import time
from pathlib import Path
from collections import Counter

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Scan the archive for training data
# ══════════════════════════════════════════════════════════════════════════════

def scan_archive(archive_path: str) -> list[dict]:
    """Scan Z:\\0 ARCHIVE for completed survey jobs.

    Extracts job metadata from folder names and research.json files.
    Returns a list of training records.
    """
    archive = Path(archive_path)
    if not archive.exists():
        logger.error(f"Archive path not found: {archive_path}")
        return []

    records = []
    skipped = 0

    for range_dir in sorted(archive.iterdir()):
        if not range_dir.is_dir():
            continue
        # Skip non-range folders
        if not re.match(r'^_?\d+', range_dir.name):
            continue

        for job_dir in range_dir.iterdir():
            if not job_dir.is_dir():
                continue

            # Parse: "2938 GARZA, VERONICA" or "0145 SMITH JOHN"
            m = re.match(r'^(\d{3,4})\s+(.*)', job_dir.name)
            if not m:
                continue

            job_number = int(m.group(1))
            client_name = m.group(2).strip()

            # Find job sub-folders (e.g., "2938-01-BDY Garza")
            for sub_dir in job_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                mt = re.match(r'^\d+-\d+-([A-Z]+)', sub_dir.name)
                if not mt:
                    continue

                job_type = mt.group(1)
                if job_type in ("PLACE", "TYPE", "LEGACY", "XXXX"):
                    continue

                # Check for research folder
                research_dir = sub_dir / "E Research"
                has_research = research_dir.exists()

                # Check for drafting
                drafting_dir = sub_dir / "B Drafting"
                has_drafting = drafting_dir.exists() and any(
                    f.suffix.lower() in ('.dwg', '.dxf')
                    for f in drafting_dir.iterdir()
                ) if drafting_dir.exists() else False

                # Check for fieldwork
                survey_dir = sub_dir / "C Survey"
                has_fieldwork = survey_dir.exists() and any(
                    survey_dir.iterdir()
                ) if survey_dir.exists() else False

                # Count deeds and plats
                deed_count = 0
                plat_count = 0
                adjoiner_names = []

                if has_research:
                    deeds_dir = research_dir / "A Deeds"
                    plats_dir = research_dir / "B Plats"

                    if deeds_dir.exists():
                        try:
                            deed_count = sum(
                                1 for f in deeds_dir.iterdir()
                                if f.is_file() and f.suffix.lower() == '.pdf'
                            )
                        except (PermissionError, OSError):
                            deed_count = 0
                    if plats_dir.exists():
                        try:
                            plat_count = sum(
                                1 for f in plats_dir.iterdir()
                                if f.is_file() and f.suffix.lower() == '.pdf'
                            )
                        except (PermissionError, OSError):
                            plat_count = 0


                    # Try to read research.json for adjoiners
                    rj = research_dir / "research.json"
                    if rj.exists():
                        try:
                            data = json.loads(rj.read_text(encoding="utf-8"))
                            for s in data.get("subjects", []):
                                if s.get("type") == "adjoiner":
                                    name = s.get("name", "")
                                    if name:
                                        adjoiner_names.append(name)
                        except Exception:
                            pass

                    # Estimate adjoiners from Adjoiners subfolder
                    adj_deeds = deeds_dir / "Adjoiners" if deeds_dir.exists() else None
                    if adj_deeds and adj_deeds.exists() and not adjoiner_names:
                        try:
                            for f in adj_deeds.iterdir():
                                if f.is_file() and f.suffix.lower() == '.pdf':
                                    fname = f.stem
                                    clean = re.sub(r'^\d+[-_\s]*', '', fname)
                                    if clean and len(clean) > 3:
                                        adjoiner_names.append(clean)
                        except (PermissionError, OSError):
                            pass

                estimated_adjoiners = len(adjoiner_names)
                if estimated_adjoiners == 0 and deed_count > 1:
                    estimated_adjoiners = deed_count - 1  # rough estimate

                records.append({
                    "job_number": job_number,
                    "client_name": client_name,
                    "job_type": job_type,
                    "has_research": has_research,
                    "has_drafting": has_drafting,
                    "has_fieldwork": has_fieldwork,
                    "deed_count": deed_count,
                    "plat_count": plat_count,
                    "estimated_adjoiners": estimated_adjoiners,
                    "adjoiner_names": adjoiner_names,
                })

    records.sort(key=lambda r: r["job_number"], reverse=True)
    logger.info(f"📊 Scanned {len(records)} jobs from archive")
    return records


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    from ai import AI_DATA_DIR

    print("\n╔══════════════════════════════════════════════╗")
    print("║   AI Bootstrap — Seed Knowledge & Models     ║")
    print("╚══════════════════════════════════════════════╝\n")

    # ── Step 1: Scan Archive ──────────────────────────────────────────────
    archive_path = "Z:\\0 ARCHIVE"
    print(f"📦 Step 1: Scanning archive at {archive_path}...")
    start = time.time()

    records = scan_archive(archive_path)
    elapsed = time.time() - start

    if not records:
        print("  ❌ No records found. Is Z: drive connected?")
        return

    types = Counter(r["job_type"] for r in records)
    total_adj = sum(r["estimated_adjoiners"] for r in records)
    print(f"  ✅ Found {len(records)} jobs in {elapsed:.1f}s")
    print(f"  Job types: {dict(types)}")
    print(f"  Total estimated adjoiners: {total_adj}")

    # Save training data
    out_path = AI_DATA_DIR / "full_archive_training_data.json"
    out_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  💾 Saved to {out_path}")

    # Also scan active research.json files from Survey Data
    from app import get_survey_data_path
    sd_path = get_survey_data_path()
    active_count = 0
    if sd_path:
        from ai.predictions import scan_training_data
        active = scan_training_data(sd_path)
        active_count = len(active)
        if active:
            print(f"  📋 Also found {active_count} active research sessions")

    # ── Step 2: Populate Knowledge Graph ──────────────────────────────────
    print(f"\n🧠 Step 2: Building knowledge graph...")
    start = time.time()

    from ai import get_knowledge_graph
    kg = get_knowledge_graph()
    if kg:
        # Populate from archive data
        result = kg.populate_from_archive(str(out_path))
        elapsed = time.time() - start
        print(f"  ✅ Graph built in {elapsed:.1f}s")
        print(f"  Nodes: {result.get('total_nodes', 0)}")
        print(f"  Edges: {result.get('total_edges', 0)}")
        print(f"  Persons: {result.get('persons_added', 0)}")
        print(f"  Adjacencies: {result.get('adjacencies_added', 0)}")

        # Also populate from active research sessions
        if sd_path:
            print(f"  📋 Enriching with active research sessions...")
            r2 = kg.populate_from_research_sessions(sd_path)
            print(f"  + {r2.get('persons_added', 0)} persons, "
                  f"+ {r2.get('adjacencies_added', 0)} adjacencies from active jobs")
    else:
        print("  ❌ Knowledge graph unavailable (networkx installed?)")

    # ── Step 3: Train ML Models ───────────────────────────────────────────
    print(f"\n🎓 Step 3: Training ML models...")
    start = time.time()

    from ai import get_predictor
    predictor = get_predictor()
    if predictor:
        result = predictor.train(sd_path or "")
        elapsed = time.time() - start
        if result.get("success"):
            print(f"  ✅ Training complete in {elapsed:.1f}s")
            print(f"  Jobs trained on: {result.get('jobs_trained', 0)}")
            metrics = result.get("metrics", {})
            adj = metrics.get("adjoiner", {})
            cab = metrics.get("cabinet", {})
            dur = metrics.get("duration", {})
            if adj.get("mae"):
                print(f"  Adjoiner MAE: {adj['mae']} "
                      f"(model: {adj.get('model_selected', '?')})")
            if cab.get("accuracy"):
                print(f"  Cabinet accuracy: {cab['accuracy']} "
                      f"(model: {cab.get('model_selected', '?')})")
            if dur.get("mae_days"):
                print(f"  Duration MAE: {dur['mae_days']} days")
        else:
            print(f"  ⚠️ Training incomplete: {result.get('error', '?')}")
    else:
        print("  ❌ Predictor unavailable (scikit-learn installed?)")

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'═'*50}")
    print(f"  AI Bootstrap Complete!")
    print(f"  Archive jobs:    {len(records)}")
    print(f"  Active sessions: {active_count}")
    if kg:
        stats = kg.graph_stats()
        print(f"  KG nodes:        {stats.get('total_nodes', 0)}")
        print(f"  KG edges:        {stats.get('total_edges', 0)}")
    if predictor:
        status = predictor.get_training_stats()
        print(f"  Models trained:  {status.get('trained_at', 'N/A')}")
    print(f"{'═'*50}\n")


if __name__ == "__main__":
    main()
