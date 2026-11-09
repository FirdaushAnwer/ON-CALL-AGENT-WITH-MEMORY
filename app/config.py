"""Runtime settings, read from environment variables (and a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    model_primary: str
    model_fallback: str
    llm_timeout: float
    hindsight_base_url: str
    hindsight_api_key: str
    hindsight_bank_id: str
    hindsight_timeout: float
    retain_async: bool
    memory_backend: str  # "hindsight" (default) or "local" (offline stub for tests/dev only)
    recall_max_results: int
    hindsight_rerank_model: str


def get_settings() -> Settings:
    """Read settings fresh each call so tests can override env vars."""
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        model_primary=os.getenv("MODEL_PRIMARY", "openai/gpt-oss-120b"),
        model_fallback=os.getenv("MODEL_FALLBACK", "qwen/qwen3-32b"),
        llm_timeout=float(os.getenv("LLM_TIMEOUT", "30")),
        hindsight_base_url=os.getenv("HINDSIGHT_BASE_URL", "https://api.hindsight.vectorize.io"),
        hindsight_api_key=os.getenv("HINDSIGHT_API_KEY", ""),
        hindsight_bank_id=os.getenv("HINDSIGHT_BANK_ID", "oncall-incidents"),
        hindsight_timeout=float(os.getenv("HINDSIGHT_TIMEOUT", "60")),
        retain_async=os.getenv("HINDSIGHT_RETAIN_ASYNC", "false").lower() == "true",
        memory_backend=os.getenv("MEMORY_BACKEND", "hindsight").lower(),
        recall_max_results=int(os.getenv("RECALL_MAX_RESULTS", "6")),
        hindsight_rerank_model=os.getenv("HINDSIGHT_RERANK_MODEL", "hs-decide-1-flash"),
    )
