"""In-process accuracy tracking for the learning-curve chart."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_rounds: list[dict] = []  # {"mode": "memory"|"baseline", "correct": bool, "incident_id": str}
_counters = {"memories_recalled": 0, "corrections_retained": 0}


def record(mode: str, correct: bool, incident_id: str) -> None:
    with _lock:
        _rounds.append({"mode": mode, "correct": correct, "incident_id": incident_id})


def add_recalled(n: int) -> None:
    with _lock:
        _counters["memories_recalled"] += n


def add_retained_correction() -> None:
    with _lock:
        _counters["corrections_retained"] += 1


def _cumulative(mode: str) -> list[float]:
    out, ok, total = [], 0, 0
    for r in _rounds:
        if r["mode"] != mode:
            continue
        total += 1
        ok += 1 if r["correct"] else 0
        out.append(round(ok / total, 3))
    return out


def snapshot() -> dict:
    with _lock:
        return {
            "cumulative_accuracy": {"memory": _cumulative("memory"), "baseline": _cumulative("baseline")},
            "rounds": {
                "memory": sum(1 for r in _rounds if r["mode"] == "memory"),
                "baseline": sum(1 for r in _rounds if r["mode"] == "baseline"),
            },
            **_counters,
        }


def reset() -> None:
    with _lock:
        _rounds.clear()
        _counters.update(memories_recalled=0, corrections_retained=0)
