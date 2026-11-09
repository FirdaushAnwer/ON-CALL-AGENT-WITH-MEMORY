"""Triage + feedback flow. This is where Hindsight recall and retain are wired into the agent."""
from __future__ import annotations

import logging
import threading
import uuid

from . import llm, stats
from .config import get_settings
from .memory import get_memory
from .models import FeedbackRequest, FeedbackResponse, TriageRequest, TriageResponse

log = logging.getLogger("oncall.triage")

# incident_id -> context needed when feedback arrives
_pending: dict[str, dict] = {}
_lock = threading.Lock()


def build_recall_query(alert: str, service: str | None) -> str:
    """Enrich the recall query with domain concepts to maximize memory recall accuracy across alert variations."""
    q_parts = []
    if service:
        q_parts.append(service)
    q_parts.append(alert)

    alert_lower = alert.lower()
    if any(k in alert_lower for k in ("getaddrinfo", "eai_again", "read udp", ":53", "lookup", "dns", "coredns")):
        q_parts.append("dns name resolution coredns udp lookup timeout")
    if any(k in alert_lower for k in ("isr", "notenoughreplicas", "kafkastorageexception", "/var/lib/kafka", "broker", "partitions")):
        q_parts.append("kafka broker topic storage retention disk full partitions clickstream-raw")
    if any(k in alert_lower for k in ("oomkilled", "exit code 137", "cgroup", "used memory", "bitmap")):
        q_parts.append("oom killed memory leak buffer bitmap container")
    if any(k in alert_lower for k in ("hikaripool", "pgbouncer", "max_client_conn", "connection is not available")):
        q_parts.append("database connection pool postgres recon-batch statement_timeout")
    if any(k in alert_lower for k in ("x509", "certificate has expired", "tls handshake failure", "ssl alert")):
        q_parts.append("tls ssl certificate expired cert-manager mTLS auto-renewal")
    if any(k in alert_lower for k in ("redis", "maxmemory", "evicted_keys", "responseerror: oom")):
        q_parts.append("redis cache memory session ttl expire key")
    if any(k in alert_lower for k in ("5xx rate", "http 503", "spike", "feature flag")):
        q_parts.append("feature flag 5xx error rate deployment dependency")
    if any(k in alert_lower for k in ("http 429", "rate limit", "too many requests")):
        q_parts.append("third-party rate limit 429 retry storm queue worker")

    return " ".join(q_parts)


def run_triage(req: TriageRequest) -> TriageResponse:
    s = get_settings()
    incident_id = f"INC-{uuid.uuid4().hex[:8]}"
    warnings: list[str] = []
    memories: list[str] = []
    mem = get_memory()

    # 1) RECALL similar past incidents + corrections BEFORE the verdict (skipped when memory is off)
    if req.use_memory:
        try:
            query = build_recall_query(req.alert_text, req.service)
            memories = mem.recall(query, bank=req.bank_id, limit=s.recall_max_results)
            stats.add_recalled(len(memories))
        except Exception as e:  # Hindsight timeout/auth/etc: continue with empty memory
            warnings.append(f"Hindsight recall failed, continuing without memory: {type(e).__name__}: {e}"[:240])
            log.warning("recall failed: %s", e)

    # 2) Verdict from the LLM, grounded in the recalled memories
    verdict, meta = llm.get_verdict(req.alert_text, req.service, memories)
    warnings.extend(meta["warnings"][-3:])

    with _lock:
        _pending[incident_id] = {
            "alert": req.alert_text,
            "service": req.service,
            "verdict": verdict,
            "use_memory": req.use_memory,
            "bank_id": req.bank_id,
        }

    # 3) RETAIN the alert and the (still unconfirmed) verdict
    if req.use_memory:
        content = (
            f"On-call alert for service {req.service or 'unknown'} (incident {incident_id}):\n{req.alert_text}\n"
            f"Agent suggestion, NOT yet confirmed by an engineer: root cause = {verdict.root_cause}; "
            f"suggested fix = {verdict.suggested_fix}."
        )
        try:
            mem.retain(
                content,
                context="incident triage: alert and unconfirmed agent verdict",
                document_id=f"{incident_id}-triage",
                tags=["incident", "triage"] + ([f"service:{req.service}"] if req.service else []),
                bank=req.bank_id,
            )
        except Exception as e:
            warnings.append(f"Hindsight retain failed: {type(e).__name__}: {e}"[:240])
            log.warning("retain (triage) failed: %s", e)

    return TriageResponse(
        **verdict.model_dump(),
        incident_id=incident_id,
        memory_enabled=req.use_memory,
        memories_used=memories,
        warnings=warnings,
        degraded=meta["degraded"],
        model=meta["model"],
    )


def record_feedback(req: FeedbackRequest) -> FeedbackResponse:
    with _lock:
        ctx = _pending.get(req.incident_id)
    if ctx is None:
        raise KeyError(req.incident_id)

    verdict = ctx["verdict"]
    mode = "memory" if ctx["use_memory"] else "baseline"
    stats.record(mode, req.correct, req.incident_id)

    warnings: list[str] = []
    if not ctx["use_memory"]:
        # Memory-off runs do not learn: nothing is retained, which keeps the comparison honest.
        return FeedbackResponse(incident_id=req.incident_id, retained=False, warnings=warnings)

    actual_cause = req.actual_cause or (verdict.root_cause if req.correct else None)
    actual_fix = req.actual_fix or (verdict.suggested_fix if req.correct else None)
    parts = [
        f"Engineer feedback on incident {req.incident_id} (service {ctx['service'] or 'unknown'}).",
        f"The alert was: {ctx['alert'][:500]}",
        f"The agent's suggested root cause was {'CORRECT' if req.correct else 'WRONG'}.",
    ]
    if actual_cause:
        parts.append(f"CONFIRMED ROOT CAUSE: {actual_cause}")
    if actual_fix:
        parts.append(f"FIX THAT WORKED: {actual_fix}")
    failed = []
    if req.suggested_fix_failed or req.tried_fix_failed:
        failed.append(verdict.suggested_fix)
    if req.failed_fix:
        failed.append(req.failed_fix)
    for f in failed:
        parts.append(f"FIX THAT DID NOT WORK (do not suggest again for this kind of alert): {f}")

    try:
        get_memory().retain(
            "\n".join(parts),
            context="incident outcome: engineer correction",
            document_id=f"{req.incident_id}-feedback",
            tags=["incident", "correction"] + ([f"service:{ctx['service']}"] if ctx["service"] else []),
            bank=req.bank_id or ctx["bank_id"],
        )
        stats.add_retained_correction()
        retained = True
    except Exception as e:
        retained = False
        warnings.append(f"Hindsight retain failed: {type(e).__name__}: {e}"[:240])
        log.warning("retain (feedback) failed: %s", e)
    return FeedbackResponse(incident_id=req.incident_id, retained=retained, warnings=warnings)
