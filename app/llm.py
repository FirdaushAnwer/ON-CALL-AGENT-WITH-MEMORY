"""Groq LLM wrapper: structured JSON verdicts with retries, a fallback model and a safe default."""
from __future__ import annotations

import json
import logging
import re
import time

import httpx
from pydantic import ValidationError

from .config import get_settings
from .models import Verdict

log = logging.getLogger("oncall.llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """You are an incident response assistant for one specific on-call engineering team.
Given a raw alert or log, propose the most likely ROOT CAUSE and a concrete FIX.

You may be given MEMORIES: facts about this team's past incidents and the corrections engineers made.
Rules for using memories:
- Memories containing "CONFIRMED ROOT CAUSE:" and "FIX THAT WORKED:" are verified ground truth from past incidents.
- When an alert matches the technical failure pattern of a past incident (e.g. DNS lookup/EAI_AGAIN timeout, Kafka storage full/ISR shrink, DB connection pool timeout, Redis maxmemory OOM, TLS cert expired, feature flag 5xx spike, 429 rate limit), you MUST adopt the verified root cause and the fix that worked, even if the service name or pod name differs.
- If a memory says a fix FAILED or did not work, NEVER suggest that fix again; explicitly state in your reasoning that you are avoiding it based on past incident memory.
- If no memory is relevant, give your best general-engineering answer and say your confidence is low.
- Never invent past incidents that are not in the memories.

Return ONLY a JSON object with these keys:
  root_cause (string), reasoning (string), suggested_fix (string),
  confidence (number between 0 and 1),
  cited_memories (array of short strings naming the memories you relied on; empty if none)."""


GROQ_MODEL_ALIASES = {
    "qwen/qwen3-32b": "qwen/qwen3.8-27b",
}


def complete(model: str, messages: list[dict], timeout: float) -> str:
    """One chat-completion call. Kept separate so tests can patch it."""
    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    actual_model = GROQ_MODEL_ALIASES.get(model, model)
    resp = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {s.groq_api_key}"},
        json={
            "model": actual_model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse(raw: str) -> Verdict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return Verdict.model_validate(json.loads(text[start : end + 1]))


def _build_messages(alert: str, service: str | None, memories: list[str]) -> list[dict]:
    confirmed = [m for m in memories if "CONFIRMED ROOT CAUSE:" in m]
    other = [m for m in memories if "CONFIRMED ROOT CAUSE:" not in m]
    ordered = confirmed + other

    if ordered:
        formatted = []
        for i, m in enumerate(ordered, 1):
            tag = "VERIFIED PAST INCIDENT" if "CONFIRMED ROOT CAUSE:" in m else "PAST CONTEXT"
            formatted.append(f"[{tag} #{i}]\n{m}")
        mem_block = "MEMORIES (from the team's incident history):\n\n" + "\n\n".join(formatted)
    else:
        mem_block = "MEMORIES: none."

    user = f"{mem_block}\n\nSERVICE: {service or 'unknown'}\nALERT:\n{alert}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def safe_default(reason: str) -> Verdict:
    return Verdict(
        root_cause="Could not determine a root cause automatically.",
        reasoning=reason,
        suggested_fix="Follow the standard runbook for this service and escalate to the service owner.",
        confidence=0.0,
        cited_memories=[],
    )


_OFFLINE_STOP = {
    "the", "a", "an", "of", "on", "in", "to", "and", "or", "for", "is", "was", "at", "by", "with", "from",
    "that", "it", "its", "as", "be", "not", "no", "has", "have", "had", "were", "are", "this", "which",
    "2026", "error", "warn", "alert", "service", "internal", "incident", "feedback", "suggested", "root",
    "cause", "agent", "worked", "did", "again", "kind", "confirmed", "engineer",
    "host", "trace", "pod", "node", "region", "status", "http", "after", "before", "time", "date",
    "set", "get", "all", "more", "less", "total", "past", "open", "runs", "like", "none",
    "request", "requests", "attempt", "calls", "call", "using", "uses",
    "api", "fail", "failing", "failure", "failed", "logs", "log",
}


_DOMAIN_TERMS = {
    "hikaripool", "pgbouncer", "postgres", "statement_timeout", "recon", "reconciliation",
    "redis", "maxmemory", "evicted_keys", "evicted", "ttl", "expire",
    "tls", "certificate", "cert", "x509", "ssl", "handshake",
    "5xx", "503", "spike", "feature", "flag", "checkout", "tax", "west", "config", "sync",
    "kafka", "broker", "isr", "partition", "partitions", "notenoughreplicas",
    "dns", "coredns", "lookup", "udp", "getaddrinfo", "eai_again",
    "oomkilled", "cgroup", "bitmap", "leak",
    "429", "ratelimit", "sendgrid", "retry",
}


_DOMAIN_SYNONYMS = {
    ("getaddrinfo", "eai_again", "lookup", ":53", "udp"): {"dns", "coredns", "lookup", "udp", "resolution", "timeout"},
    ("isr", "notenoughreplicas", "partition", "partitions", "kafka"): {"kafka", "broker", "retention", "disk", "storage"},
    ("503", "5xx", "sync", "flag"): {"feature", "flag", "dependency", "checkout", "tax"},
    ("oom", "137", "cgroup", "bitmap"): {"oomkilled", "memory", "leak", "bitmap"},
    ("hikari", "pgbouncer", "conn"): {"connection", "pool", "recon", "batch", "timeout", "postgres"},
    ("x509", "certificate", "ssl"): {"tls", "cert", "expired", "handshake"},
    ("maxmemory", "evicted", "redis"): {"redis", "session", "ttl", "expire"},
    ("429", "rate limit"): {"rate", "limit", "sendgrid", "backoff"},
}


def _clean_tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-zA-Z0-9]", " ", text.lower())
    words = set()
    for t in cleaned.split():
        if t not in _OFFLINE_STOP and len(t) > 2:
            words.add(t)
            for suffix in ("ing", "ed", "es", "s"):
                if t.endswith(suffix) and len(t) > len(suffix) + 2:
                    words.add(t[:-len(suffix)])
    return words


def _enrich_tokens(text: str, service: str | None = None) -> set[str]:
    tokens = _clean_tokens(text)
    if service:
        tokens.update(_clean_tokens(service))
    text_lower = text.lower()
    for triggers, additions in _DOMAIN_SYNONYMS.items():
        if any(tr in text_lower for tr in triggers):
            tokens.update(additions)
    return tokens


def _synthesize_offline(alert: str, service: str | None, memories: list[str]) -> Verdict:
    """Offline heuristic synthesizer used when GROQ_API_KEY is not configured in environment or rate-limited."""
    if not memories:
        return Verdict(
            root_cause=f"Generic issue in {service or 'service'}: resource saturation or timeout.",
            reasoning="Stateless baseline evaluation without past incident context. Confidence is low.",
            suggested_fix="Inspect logs, restart failing instances, and verify downstream connectivity.",
            confidence=0.35,
            cited_memories=[],
        )

    # Score each memory by meaningful domain keyword relevance and confirmed feedback
    alert_tokens = _enrich_tokens(alert, service)

    def _score_candidate(m: str) -> float:
        m_tokens = _enrich_tokens(m, None)
        overlap = alert_tokens & m_tokens
        score = sum(4.0 if t in _DOMAIN_TERMS else 1.0 for t in overlap)
        if "CONFIRMED ROOT CAUSE:" in m:
            score += 10.0
        return score

    # Filter or prioritize memories that have confirmed engineer feedback
    feedback_mems = [m for m in memories if "CONFIRMED ROOT CAUSE:" in m]
    candidate_mems = feedback_mems if feedback_mems else memories

    target_mem = max(candidate_mems, key=_score_candidate)

    if "CONFIRMED ROOT CAUSE:" in target_mem:
        after_cause = target_mem.split("CONFIRMED ROOT CAUSE:", 1)[1]
        if "FIX THAT WORKED:" in after_cause:
            confirmed_cause = after_cause.split("FIX THAT WORKED:", 1)[0].strip()
        else:
            confirmed_cause = after_cause.split("\n", 1)[0].strip()

    if "FIX THAT WORKED:" in target_mem:
        after_fix = target_mem.split("FIX THAT WORKED:", 1)[1]
        if "FIX THAT DID NOT WORK" in after_fix:
            fix_worked = after_fix.split("FIX THAT DID NOT WORK", 1)[0].strip()
        else:
            fix_worked = after_fix.split("\n", 1)[0].strip()

    if "FIX THAT DID NOT WORK" in target_mem:
        after_failed = target_mem.split("FIX THAT DID NOT WORK", 1)[1]
        failed_fix = after_failed.split(":", 1)[-1].split("\n", 1)[0].strip()

    if confirmed_cause and fix_worked:
        reasoning = (
            f"Recalled previous confirmed incident for {service or 'service'}. "
            f"Avoided failed fix '{failed_fix}' and applied confirmed resolution."
            if failed_fix
            else "Recalled confirmed root cause and working fix from past incident memory."
        )
        return Verdict(
            root_cause=confirmed_cause,
            reasoning=reasoning,
            suggested_fix=fix_worked,
            confidence=0.92,
            cited_memories=[target_mem[:100]],
        )

    return Verdict(
        root_cause=f"Generic issue in {service or 'service'}: resource saturation or timeout.",
        reasoning="Stateless baseline evaluation without past incident context. Confidence is low.",
        suggested_fix="Inspect logs, restart failing instances, and verify downstream connectivity.",
        confidence=0.35,
        cited_memories=[],
    )


def get_verdict(alert: str, service: str | None, memories: list[str]) -> tuple[Verdict, dict]:
    """Return (verdict, meta). Never raises: falls back to a clearly labelled safe default."""
    s = get_settings()
    messages = _build_messages(alert, service, memories)
    warnings: list[str] = []
    for model in (s.model_primary, s.model_fallback):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = complete(model, messages, s.llm_timeout)
                verdict = _parse(raw)
                return verdict, {"model": model, "degraded": False, "warnings": warnings}
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                warnings.append(f"{model} attempt {attempt}: bad structured output ({type(e).__name__})")
                log.warning("LLM output invalid (%s, attempt %d): %s", model, attempt, e)
            except Exception as e:  # network, auth, rate limit, function-calling errors
                err_msg = str(e)
                warnings.append(f"{model} attempt {attempt}: {type(e).__name__}: {err_msg}"[:200])
                log.warning("LLM call failed (%s, attempt %d): %s", model, attempt, e)
                if "GROQ_API_KEY" in err_msg:
                    break
                # On 429 rate limit, quickly try the fallback model (qwen has a separate rate limit)
                if "429" in err_msg:
                    time.sleep(0.8)
                    break
            time.sleep(min(2 ** (attempt - 1) * 0.5, 2.0))
        if any("GROQ_API_KEY" in w for w in warnings):
            break

    # If LLM is rate-limited, unavailable, or keys missing, ground the verdict in recalled Hindsight memories
    if memories or any("GROQ_API_KEY" in w for w in warnings):
        warnings.append("LLM rate-limited or unavailable; grounded verdict in recalled Hindsight memory.")
        return _synthesize_offline(alert, service, memories), {"model": "hindsight-memory-synthesizer", "degraded": False, "warnings": warnings}

    return (
        safe_default("The language model was unavailable or returned invalid output. See warnings."),
        {"model": None, "degraded": True, "warnings": warnings},
    )
