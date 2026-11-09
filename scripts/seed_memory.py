"""Seed Hindsight with confirmed past incidents so the demo can start from a warm memory.

Usage:
    python scripts/seed_memory.py                 # seed 1 incident per pattern (warm start)
    python scripts/seed_memory.py --per-pattern 3 # seed more history
    python scripts/seed_memory.py --bank my-bank  # seed a specific bank

Leave memory empty (skip this script) to demo the cold-start -> learning curve instead.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.demo import load_incidents  # noqa: E402
from app.memory import get_memory  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-pattern", type=int, default=1)
    ap.add_argument("--bank", default=None)
    args = ap.parse_args()

    bank = args.bank or get_settings().hindsight_bank_id
    mem = get_memory()
    taken: dict[str, int] = {}
    count = 0
    for inc in load_incidents():
        if taken.get(inc["pattern"], 0) >= args.per_pattern:
            continue
        taken[inc["pattern"]] = taken.get(inc["pattern"], 0) + 1
        content = (
            f"Past incident {inc['id']} on service {inc['service']} ({inc['severity']}, {inc['timestamp']}).\n"
            f"The alert was: {inc['raw_alert']}\n"
            f"CONFIRMED ROOT CAUSE: {inc['true_root_cause']}\n"
            f"FIX THAT WORKED: {inc['fix_that_worked']}\n"
            f"FIX THAT DID NOT WORK (do not suggest again for this kind of alert): {inc['failed_fix']}"
        )
        mem.retain(
            content,
            context="seeded post-mortem",
            document_id=f"seed-{inc['id']}",
            tags=["incident", "postmortem", f"service:{inc['service']}"],
            bank=bank,
        )
        count += 1
    print(f"Retained {count} past incidents into bank '{bank}'.")


if __name__ == "__main__":
    main()
