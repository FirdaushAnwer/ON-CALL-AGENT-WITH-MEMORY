"""Scripted 10-incident verification check:
Runs 10 incidents across recurring patterns (Wave 1: 5 cold incidents, Wave 2: 5 repeat incidents)
with Memory ON and Memory OFF side by side, and prints the exact measured results.

Usage:
    python scripts/verify_10_incidents.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# If live keys are not configured, use local backend for offline test verification
if not os.getenv("HINDSIGHT_API_KEY") and "MEMORY_BACKEND" not in os.environ:
    os.environ["MEMORY_BACKEND"] = "local"

from app import judge, stats
from app.config import get_settings
from app.demo import load_incidents
from app.models import FeedbackRequest, TriageRequest
from app.triage import record_feedback, run_triage


def run_10_incident_check() -> dict:
    stats.reset()
    all_incidents = load_incidents()

    # Pick 5 recurring patterns, 2 variations each = 10 incidents
    patterns = [
        "db_pool_exhausted",
        "redis_out_of_memory",
        "tls_cert_expired",
        "feature_flag_5xx_spike",
        "kafka_disk_full",
    ]

    wave1 = []
    wave2 = []
    for p in patterns:
        matching = [inc for inc in all_incidents if inc["pattern"] == p]
        if len(matching) >= 2:
            wave1.append(matching[0])
            wave2.append(matching[1])

    incidents_seq = wave1 + wave2
    test_bank = f"verify-10-{int(time.time())}"

    print("=" * 80)
    print("RUNNING 10-INCIDENT SCRIPTED VERIFICATION")
    print(f"Memory Bank: {test_bank}")
    print(f"Memory Backend: {get_settings().memory_backend}")
    print(f"Patterns: {', '.join(patterns)}")
    print("=" * 80)

    results = []

    for idx, inc in enumerate(incidents_seq, 1):
        wave = 1 if idx <= 5 else 2
        pattern = inc["pattern"]
        service = inc["service"]
        alert = inc["raw_alert"]
        true_cause = inc["true_root_cause"]
        true_fix = inc["fix_that_worked"]
        failed_fix = inc["failed_fix"]

        # 1. Baseline Run: Memory OFF
        triage_base = run_triage(
            TriageRequest(alert_text=alert, service=service, use_memory=False, bank_id=test_bank)
        )
        base_match = judge.matches(f"{triage_base.root_cause} {triage_base.reasoning}", true_cause)
        record_feedback(FeedbackRequest(incident_id=triage_base.incident_id, correct=base_match, bank_id=test_bank))

        # 2. Memory Run: Memory ON
        triage_mem = run_triage(
            TriageRequest(alert_text=alert, service=service, use_memory=True, bank_id=test_bank)
        )
        mem_match = judge.matches(f"{triage_mem.root_cause} {triage_mem.reasoning}", true_cause)
        # Engineer provides feedback with confirmed cause, working fix, and failed fix
        record_feedback(
            FeedbackRequest(
                incident_id=triage_mem.incident_id,
                correct=mem_match,
                actual_cause=true_cause,
                actual_fix=true_fix,
                tried_fix_failed=True,
                failed_fix=failed_fix,
                bank_id=test_bank,
            )
        )

        recalled_count = len(triage_mem.memories_used)

        row = {
            "round": idx,
            "wave": wave,
            "pattern": pattern,
            "service": service,
            "baseline_correct": base_match,
            "memory_correct": mem_match,
            "memories_recalled": recalled_count,
            "baseline_cause": triage_base.root_cause,
            "memory_cause": triage_mem.root_cause,
            "memories_used": triage_mem.memories_used,
        }
        results.append(row)

        print(
            f"Round {idx:>2} | Wave {wave} | {pattern:<25} | "
            f"Recalled: {recalled_count:>1} | "
            f"Memory OFF: {'MATCH' if base_match else 'MISS ':<5} | "
            f"Memory ON: {'MATCH' if mem_match else 'MISS ':<5}"
        )

    # Compute summaries
    w1_base = sum(1 for r in results if r["wave"] == 1 and r["baseline_correct"])
    w1_mem = sum(1 for r in results if r["wave"] == 1 and r["memory_correct"])
    w2_base = sum(1 for r in results if r["wave"] == 2 and r["baseline_correct"])
    w2_mem = sum(1 for r in results if r["wave"] == 2 and r["memory_correct"])

    total_base = sum(1 for r in results if r["baseline_correct"])
    total_mem = sum(1 for r in results if r["memory_correct"])

    st = stats.snapshot()

    print("\n" + "=" * 80)
    print("VERIFICATION RESULTS SUMMARY")
    print("=" * 80)
    print(f"Wave 1 (Cold start, 5 incidents):")
    print(f"  - Memory OFF: {w1_base}/5 ({w1_base / 5 * 100:.0f}%)")
    print(f"  - Memory ON:  {w1_mem}/5 ({w1_mem / 5 * 100:.0f}%)")
    print(f"\nWave 2 (Repeat patterns after engineer feedback, 5 incidents):")
    print(f"  - Memory OFF: {w2_base}/5 ({w2_base / 5 * 100:.0f}%)")
    print(f"  - Memory ON:  {w2_mem}/5 ({w2_mem / 5 * 100:.0f}%)")
    print(f"\nOverall (10 incidents):")
    print(f"  - Memory OFF (Baseline): {total_base}/10 ({total_base / 10 * 100:.0f}%)")
    print(f"  - Memory ON  (Hindsight): {total_mem}/10 ({total_mem / 10 * 100:.0f}%)")
    print(f"\nCounters:")
    print(f"  - Corrections retained: {st['corrections_retained']}")
    print(f"  - Memories recalled:    {st['memories_recalled']}")
    print("=" * 80)

    # Save to docs for reporting
    out_path = Path(__file__).resolve().parent.parent / "docs" / "verification_10_results.json"
    out_path.parent.mkdir(exist_ok=True)
    summary_data = {
        "results": results,
        "wave1": {"baseline": w1_base, "memory": w1_mem, "total": 5},
        "wave2": {"baseline": w2_base, "memory": w2_mem, "total": 5},
        "overall": {"baseline": total_base, "memory": total_mem, "total": 10},
        "stats": st,
    }
    out_path.write_text(json.dumps(summary_data, indent=2) + "\n")
    print(f"Wrote verification details to {out_path}\n")

    return summary_data


if __name__ == "__main__":
    run_10_incident_check()
