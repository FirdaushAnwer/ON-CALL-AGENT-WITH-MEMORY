"""FastAPI app: /triage, /feedback, /stats and a single-page UI."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import demo, stats
from .config import get_settings
from .models import FeedbackRequest, FeedbackResponse, TriageRequest, TriageResponse
from .triage import record_feedback, run_triage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="On-call Memory Agent", version="1.0.0")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "memory_backend": s.memory_backend,
        "hindsight_base_url": s.hindsight_base_url,
        "bank_id": s.hindsight_bank_id,
        "llm_configured": bool(s.groq_api_key),
        "models": [s.model_primary, s.model_fallback],
    }


# Sync handlers on purpose: the Hindsight client wrapper is synchronous and FastAPI runs these in a threadpool.
@app.post("/triage", response_model=TriageResponse)
def triage(req: TriageRequest, memory: str | None = Query(default=None, description="'off' disables memory for this call")) -> TriageResponse:
    if memory and memory.lower() == "off":
        req = req.model_copy(update={"use_memory": False})
    return run_triage(req)


@app.post("/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest) -> FeedbackResponse:
    try:
        return record_feedback(req)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown incident_id {req.incident_id}; triage it first.")


@app.get("/stats")
def get_stats() -> dict:
    return stats.snapshot()


@app.post("/reset-stats")
def reset_stats() -> dict:
    stats.reset()
    return {"ok": True}


@app.get("/samples")
def samples(limit: int = 8) -> list[dict]:
    """One sample alert per recurring pattern, for the UI's quick-load buttons."""
    seen, out = set(), []
    for inc in demo.load_incidents():
        if inc["pattern"] not in seen:
            seen.add(inc["pattern"])
            out.append({"pattern": inc["pattern"], "service": inc["service"], "alert_text": inc["raw_alert"]})
    return out[:limit]


@app.post("/demo/run")
def demo_run(waves: int = Query(default=3, ge=1, le=5), warm_start: bool = Query(default=False)) -> dict:
    started = demo.start_background(waves, warm_start=warm_start)
    return {"started": started, "status": demo.STATUS["state"], "warm_start": warm_start}


@app.get("/demo/status")
def demo_status() -> dict:
    return demo.STATUS


@app.post("/seed-memory")
def seed_memory(bank: str | None = None) -> dict:
    s = get_settings()
    target_bank = bank or s.hindsight_bank_id
    count = demo.seed_bank(target_bank, per_pattern=1)
    return {"seeded": True, "count": count, "bank": target_bank}


@app.post("/reset-memory")
def reset_memory(bank: str | None = None) -> dict:
    s = get_settings()
    target_bank = bank or s.hindsight_bank_id
    from .memory import get_memory
    get_memory().clear(target_bank)
    stats.reset()
    return {"reset": True, "bank": target_bank}
