"""Run the memory ON vs OFF demo from the command line and print the real numbers.

Needs GROQ_API_KEY and Hindsight configured in .env. Uses a fresh bank per run.
Results depend on the LLM and on the heuristic judge in app/judge.py, so report whatever you get.

    python scripts/eval_learning_curve.py --waves 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# If live keys are not configured, use local backend for offline evaluation
if not os.getenv("HINDSIGHT_API_KEY") and "MEMORY_BACKEND" not in os.environ:
    os.environ["MEMORY_BACKEND"] = "local"

from app.demo import run_demo  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--waves", type=int, default=3)
    ap.add_argument("--warm-start", action="store_true", help="Pre-seed historical post-mortems for 100% warm evaluation")
    args = ap.parse_args()

    rows = run_demo(args.waves, warm_start=args.warm_start, progress=lambda r: print(
        f"round {r['round']:>2} wave {r['wave']} {r['pattern']:<26} recalled={r['memories_recalled']} "
        f"baseline={'match' if r['baseline_correct'] else 'miss '} memory={'match' if r['memory_correct'] else 'miss '}"
        f"{'  [degraded: LLM/Hindsight problem]' if r['degraded'] else ''}"))

    print("\nPer-wave accuracy (matches / incidents)")
    for w in sorted({r["wave"] for r in rows}):
        wave = [r for r in rows if r["wave"] == w]
        b = sum(r["baseline_correct"] for r in wave)
        m = sum(r["memory_correct"] for r in wave)
        print(f"  wave {w}: memory OFF {b}/{len(wave)}   memory ON {m}/{len(wave)}")

    out = Path(__file__).resolve().parent.parent / "docs" / "learning_curve.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
