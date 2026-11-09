"""Scripted learning-curve demo: replays recurring incidents with memory ON and OFF side by side.

Each wave contains one incident from each recurring pattern. Wave 1 is cold (nothing to recall).
From wave 2 on, the memory-on agent can recall the confirmed root causes from earlier waves.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import judge
from .models import FeedbackRequest, TriageRequest
from .triage import record_feedback, run_triage

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "incidents.json"

STATUS: dict = {"state": "idle", "rounds": [], "total": 0, "error": None, "bank": None}
_lock = threading.Lock()


def load_incidents() -> list[dict]:
    return json.loads(DATA_FILE.read_text())


def ordered_for_waves(incidents: list[dict], waves: int) -> list[dict]:
    """Interleave patterns: wave k holds the k-th variation of every pattern."""
    by_pattern: dict[str, list[dict]] = {}
    for inc in incidents:
        by_pattern.setdefault(inc["pattern"], []).append(inc)
    out: list[dict] = []
    for k in range(waves):
        for pattern in sorted(by_pattern):
            if k < len(by_pattern[pattern]):
                out.append(by_pattern[pattern][k])
    return out


def seed_bank(bank_id: str, per_pattern: int = 1) -> int:
    """Pre-seed confirmed past incident post-mortems into an isolated bank."""
    from .memory import get_memory

    mem = get_memory()
    taken: dict[str, int] = {}
    count = 0
    for inc in load_incidents():
        p = inc["pattern"]
        if taken.get(p, 0) >= per_pattern:
            continue
        taken[p] = taken.get(p, 0) + 1
        content = (
            f"Past incident {inc['id']} on service {inc['service']} ({inc['severity']}).\n"
            f"The alert was: {inc['raw_alert']}\n"
            f"CONFIRMED ROOT CAUSE: {inc['true_root_cause']}\n"
            f"FIX THAT WORKED: {inc['fix_that_worked']}\n"
            f"FIX THAT DID NOT WORK (do not suggest again for this kind of alert): {inc['failed_fix']}"
        )
        mem.retain(
            content,
            context="seeded historical post-mortem",
            document_id=f"seed-{inc['id']}",
            tags=["incident", "postmortem", f"service:{inc['service']}"],
            bank=bank_id,
        )
        count += 1
    return count


def run_demo(waves: int = 3, bank_prefix: str = "oncall-demo", progress=None, warm_start: bool = False) -> list[dict]:
    """Run the demo synchronously and return per-round results."""
    from .config import get_settings

    bank = f"{bank_prefix}-{int(time.time())}"
    if warm_start:
        seed_bank(bank, per_pattern=1)

    results: list[dict] = []
    seq = ordered_for_waves(load_incidents(), waves)
    for i, inc in enumerate(seq, 1):
        base = run_triage(
            TriageRequest(alert_text=inc["raw_alert"], service=inc["service"], use_memory=False, bank_id=bank)
        )
        base_ok = judge.matches(base.root_cause + " " + base.reasoning, inc["true_root_cause"])
        record_feedback(FeedbackRequest(incident_id=base.incident_id, correct=base_ok))

        mem = run_triage(
            TriageRequest(alert_text=inc["raw_alert"], service=inc["service"], use_memory=True, bank_id=bank)
        )
        mem_ok = judge.matches(mem.root_cause + " " + mem.reasoning, inc["true_root_cause"])
        # The "engineer" confirms the truth, so Hindsight retains the correction either way.
        record_feedback(
            FeedbackRequest(
                incident_id=mem.incident_id,
                correct=mem_ok,
                actual_cause=inc["true_root_cause"],
                actual_fix=inc["fix_that_worked"],
                failed_fix=inc["failed_fix"],
                bank_id=bank,
            )
        )
        row = {
            "round": i,
            "wave": (i - 1) // max(1, len({x["pattern"] for x in seq})) + 1,
            "pattern": inc["pattern"],
            "service": inc["service"],
            "baseline_correct": base_ok,
            "memory_correct": mem_ok,
            "memories_recalled": len(mem.memories_used),
            "degraded": base.degraded or mem.degraded,
        }
        results.append(row)
        if progress:
            progress(row)
        time.sleep(0.2)  # Gentle pacing to avoid bursting API rate limits
    _ = get_settings  # keep import local; settings are read inside triage
    return results


def start_background(waves: int = 3, warm_start: bool = False) -> bool:
    with _lock:
        if STATUS["state"] == "running":
            return False
        STATUS.update(state="running", rounds=[], total=0, error=None, warm_start=warm_start)

    def _target() -> None:
        try:
            seq_len = len(ordered_for_waves(load_incidents(), waves))
            STATUS["total"] = seq_len
            run_demo(waves, progress=lambda row: STATUS["rounds"].append(row), warm_start=warm_start)
            STATUS["state"] = "done"
        except Exception as e:  # surface to the UI instead of dying silently
            STATUS.update(state="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=_target, daemon=True).start()
    return True
    return True
