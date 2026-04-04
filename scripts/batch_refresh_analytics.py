"""
scripts/batch_refresh_analytics.py — Scan all research sessions and generate analytics.

Usage:
    python scripts/batch_refresh_analytics.py <survey_data_path>
"""

import json
import os
import sys
import time

sys.path.insert(0, ".")
from helpers.research_analytics import scan_all_research, compute_aggregate_stats


def main():
    if len(sys.argv) < 2:
        print("Usage: batch_refresh_analytics.py <survey_data_path>")
        sys.exit(1)

    survey_path = sys.argv[1]
    t0 = time.time()

    print("[batch] Scanning research sessions...", flush=True)
    sessions = scan_all_research(survey_path)
    elapsed = round(time.time() - t0, 1)

    if not sessions:
        print(f"[batch] No research sessions found ({elapsed}s)")
        sys.exit(0)

    stats = compute_aggregate_stats(sessions)
    print(f"[batch] Research analytics complete in {elapsed}s")
    print(f"  Total jobs scanned:     {stats.get('total_jobs', 0)}")
    print(f"  Total subjects:         {stats.get('total_subjects', 0)}")
    print(f"  Total deeds saved:      {stats.get('total_deeds', 0)}")
    print(f"  Total plats saved:      {stats.get('total_plats', 0)}")
    print(f"  Avg adjoiners/job:      {stats.get('avg_adjoiners', 0)}")
    print(f"  Avg completion:         {stats.get('avg_completion_pct', 0)}%")

    date_range = stats.get("date_range", {})
    print(f"  Date range:             {date_range.get('oldest', '?')} to {date_range.get('newest', '?')}")
    print()

    print("  Jobs by type:")
    for jtype, count in stats.get("jobs_by_type", {}).items():
        print(f"    {jtype:8s}: {count}")
    print()

    print("  Completion tiers:")
    tiers = stats.get("completion_tiers", {})
    for tier, count in tiers.items():
        bar = "█" * min(count, 40)
        print(f"    {tier:12s}: {count:3d} {bar}")
    print()

    # Identify incomplete jobs for follow-up
    incomplete = [
        s for s in sessions
        if s["completion_pct"] < 50 and s["adjoiner_count"] > 0
    ]
    if incomplete:
        print(f"  ⚠ {len(incomplete)} jobs are <50% complete:")
        for s in incomplete[:10]:
            print(
                f"    Job #{s['job_number']} {s['client_name']:30s} "
                f"{s['completion_pct']:5.1f}% ({s['adjoiner_count']} adjoiners)"
            )
        if len(incomplete) > 10:
            print(f"    ... and {len(incomplete) - 10} more")

    # Save analytics snapshot
    snapshot = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stats": stats,
        "incomplete_jobs": [
            {
                "job": s["job_number"],
                "client": s["client_name"],
                "pct": s["completion_pct"],
                "adj": s["adjoiner_count"],
            }
            for s in incomplete[:50]
        ],
    }
    os.makedirs("data", exist_ok=True)
    snapshot_path = "data/analytics_snapshot.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    print(f"  Snapshot saved to: {snapshot_path}")


if __name__ == "__main__":
    main()
