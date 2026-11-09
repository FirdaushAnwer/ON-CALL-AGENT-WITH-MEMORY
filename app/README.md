# Application Architecture & Backend Modules

This package implements the core FastAPI backend and memory integration for the On-Call Memory Agent.

---

## Module Overview

| File | Purpose |
|---|---|
| [`main.py`](file:///d:/repository/On-Call-Memory-Agents/app/main.py) | FastAPI application entrypoint, CORS configuration, API routes (`/triage`, `/feedback`, `/stats`, `/health`, `/samples`, `/demo/*`), and static UI dashboard mounting. |
| [`memory.py`](file:///d:/repository/On-Call-Memory-Agents/app/memory.py) | All Hindsight integration logic: Hindsight Inference API (`hs-decide-1-flash`), Hindsight Cloud Memory Banks (`hindsight-client`), and `LocalMemory` offline fallback. |
| [`triage.py`](file:///d:/repository/On-Call-Memory-Agents/app/triage.py) | Orchestrates the triage lifecycle: recall similar incidents → format prompt with past post-mortems and failed fix warnings → call LLM → retain unconfirmed verdict → handle feedback with confirmed root causes and working fixes. |
| [`llm.py`](file:///d:/repository/On-Call-Memory-Agents/app/llm.py) | Groq client wrapper with Pydantic JSON validation, exponential backoff (up to 3 retries), fallback model (`qwen/qwen3-32b`), safe default fallback, and memory-aware offline synthesizer. |
| [`models.py`](file:///d:/repository/On-Call-Memory-Agents/app/models.py) | Pydantic request and response schemas: `TriageRequest`, `Verdict`, `TriageResponse`, `FeedbackRequest`, `FeedbackResponse`, `StatsResponse`. |
| [`stats.py`](file:///d:/repository/On-Call-Memory-Agents/app/stats.py) | Thread-safe tracking of triage rounds, accuracy over time (Memory ON vs Memory OFF), corrections retained, and memories recalled. |
| [`judge.py`](file:///d:/repository/On-Call-Memory-Agents/app/judge.py) | Matcher that compares predicted verdicts against verified ground truth using domain keywords and semantic token overlap. |
| [`demo.py`](file:///d:/repository/On-Call-Memory-Agents/app/demo.py) | Background runner for the interactive web UI demo sequence that replays similar incidents across waves and demonstrates the divergence in accuracy. |
| [`config.py`](file:///d:/repository/On-Call-Memory-Agents/app/config.py) | Dynamic environment configuration loader with support for `.env`. |
| [`static/index.html`](file:///d:/repository/On-Call-Memory-Agents/app/static/index.html) | Single-page on-call dashboard with sample alerts, verdict card, "Memories used" panel, feedback controls, accuracy chart, and demo runner. |

---

## API Endpoints

- `POST /triage`: Analyzes an incident alert. Recalls similar past incidents and corrections when `use_memory=true`, invokes the LLM, returns structured verdict with confidence and cited memories, and retains the session.
- `POST /feedback`: Accepts engineer verification (`correct: true/false`, `actual_cause`, `actual_fix`, `suggested_fix_failed`). Retains confirmed knowledge into Hindsight.
- `GET /stats`: Returns round-by-round accuracy series, corrections retained, and memories recalled.
- `POST /reset-stats`: Clears in-memory statistics for clean demo runs.
- `GET /health`: Reports service status, memory backend mode, and model configurations.
- `GET /samples`: Provides 8 diverse sample alert templates for fast triage exploration.
- `POST /demo/start`: Starts the background scripted demo sequence.
- `GET /demo/status`: Polls progress of the active demo run.

