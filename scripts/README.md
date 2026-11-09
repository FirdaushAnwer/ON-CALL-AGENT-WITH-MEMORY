# Scripts Guide

This directory contains standalone CLI scripts for synthetic incident generation, memory seeding, and side-by-side verification of the On-Call Memory Agent.

---

## 1. `generate_incidents.py`

Generates realistic production incidents across recurring failure patterns.

```bash
# Offline deterministic generation (instant, no API key needed):
python scripts/generate_incidents.py

# LLM-assisted generation via Groq (requires GROQ_API_KEY in .env):
python scripts/generate_incidents.py --use-llm
```

- **Output File**: `data/incidents.json`
- **Output Corpus**: 40 incidents (8 patterns x 5 variations).
- **Incident Fields**: `id`, `timestamp`, `service`, `pattern`, `severity`, `raw_alert`, `true_root_cause`, `fix_that_worked`, `failed_fix`.

---

## 2. `seed_memory.py`

Pre-seeds Hindsight memory with confirmed historical post-mortems so you can start demos or testing from a "warm" memory state instead of cold start.

```bash
# Seed 1 past incident per pattern into the default memory bank:
python scripts/seed_memory.py

# Seed multiple past incidents per pattern:
python scripts/seed_memory.py --per-pattern 3

# Seed into a specific isolated team bank:
python scripts/seed_memory.py --bank payments-team-bank
```

- **Retained Document Format**: Includes the raw alert, verified root cause, working fix, and explicit warnings against failed remedies.
- **Tagged With**: `incident`, `postmortem`, and `service:<service-name>`.

---

## 3. `verify_10_incidents.py`

Executes the scripted 10-incident verification benchmark (Wave 1: 5 cold-start incidents; Wave 2: 5 repeat pattern variations). Measures accuracy with **Memory OFF** (stateless baseline) vs **Memory ON** (with Hindsight).

```bash
python scripts/verify_10_incidents.py
```

- **Expected Output**:
  - Wave 1 (Cold start): Memory OFF 0/5 vs Memory ON 0/5.
  - Wave 2 (Repeat variations): Memory OFF 0/5 vs Memory ON 5/5 (100% resolution match).
- **Output Artifact**: `docs/verification_10_results.json`

---

## 4. `eval_learning_curve.py`

Evaluates the multi-wave learning curve across all 8 failure patterns to demonstrate how the agent progressively improves with repeated engineer feedback.

```bash
# Run 3-wave evaluation (24 total incidents, cold start):
python scripts/eval_learning_curve.py --waves 3

# Run with warm-start (pre-seeded historical post-mortems for 100% green benchmark):
python scripts/eval_learning_curve.py --waves 3 --warm-start

# Run custom number of waves:
python scripts/eval_learning_curve.py --waves 2
```

- **Output Artifact**: `docs/learning_curve.json` with per-round logs, recalled memories count, baseline matches, and memory matches.

