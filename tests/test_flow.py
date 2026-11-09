"""Triage and feedback flow with Hindsight and the LLM mocked (no network, no keys)."""
from __future__ import annotations

import json
import os

os.environ["MEMORY_BACKEND"] = "local"
os.environ["GROQ_API_KEY"] = "test-key-not-real"
os.environ["HINDSIGHT_BASE_URL"] = "http://localhost:8888"

import pytest
from fastapi.testclient import TestClient

from app import judge, llm, memory, stats
from app.main import app
from app.memory import reset_memory_instance

reset_memory_instance()

client = TestClient(app)

ALERT = "ERROR [billing-api] HikariPool-1 - Connection is not available, request timed out after 30000ms (total=50, active=50)"


class FakeMemory:
    def __init__(self, recall_result=None, fail_recall=False, fail_retain=False):
        self.retained: list[dict] = []
        self.recall_calls = 0
        self._recall_result = recall_result or []
        self._fail_recall, self._fail_retain = fail_recall, fail_retain

    def retain(self, content, *, context, document_id, tags, bank=None):
        if self._fail_retain:
            raise TimeoutError("hindsight down")
        self.retained.append({"content": content, "doc": document_id, "tags": tags, "bank": bank})

    def recall(self, query, *, bank=None, limit=6):
        self.recall_calls += 1
        if self._fail_recall:
            raise TimeoutError("hindsight down")
        return self._recall_result


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    stats.reset()
    monkeypatch.setattr(
        llm,
        "complete",
        lambda model, messages, timeout: json.dumps(
            {"root_cause": "Pool exhausted by the recon-batch cron", "reasoning": "r", "suggested_fix": "Set statement_timeout", "confidence": 0.8, "cited_memories": ["m1"]}
        ),
    )
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def use(monkeypatch, fake):
    monkeypatch.setattr(memory, "_instance", fake)
    monkeypatch.setattr("app.triage.get_memory", lambda: fake)
    return fake


def test_triage_recalls_then_retains(monkeypatch):
    fake = use(monkeypatch, FakeMemory(recall_result=["Past incident: recon-batch cron held connections"]))
    r = client.post("/triage", json={"alert_text": ALERT, "service": "billing-api"}).json()
    assert r["root_cause"].startswith("Pool exhausted")
    assert r["memories_used"] == ["Past incident: recon-batch cron held connections"]
    assert fake.recall_calls == 1
    assert len(fake.retained) == 1 and "NOT yet confirmed" in fake.retained[0]["content"]
    assert r["degraded"] is False


def test_memory_off_skips_recall_and_retain(monkeypatch):
    fake = use(monkeypatch, FakeMemory(recall_result=["should never appear"]))
    r = client.post("/triage?memory=off", json={"alert_text": ALERT}).json()
    assert fake.recall_calls == 0 and fake.retained == []
    assert r["memories_used"] == [] and r["memory_enabled"] is False


def test_feedback_retains_correction_and_failed_fix(monkeypatch):
    fake = use(monkeypatch, FakeMemory())
    inc = client.post("/triage", json={"alert_text": ALERT, "service": "billing-api"}).json()["incident_id"]
    fb = client.post(
        "/feedback",
        json={"incident_id": inc, "correct": False, "actual_cause": "recon cron holds connections", "actual_fix": "kill sessions", "suggested_fix_failed": True},
    ).json()
    assert fb["retained"] is True
    text = fake.retained[-1]["content"]
    assert "WRONG" in text and "CONFIRMED ROOT CAUSE: recon cron" in text and "DID NOT WORK" in text
    assert fake.retained[-1]["doc"] == f"{inc}-feedback"  # separate doc id: does not overwrite the triage doc
    s = client.get("/stats").json()
    assert s["corrections_retained"] == 1 and s["cumulative_accuracy"]["memory"] == [0.0]


def test_feedback_unknown_incident_404(monkeypatch):
    use(monkeypatch, FakeMemory())
    assert client.post("/feedback", json={"incident_id": "INC-nope", "correct": True}).status_code == 404


def test_recall_failure_is_a_warning_not_a_crash(monkeypatch):
    use(monkeypatch, FakeMemory(fail_recall=True))
    r = client.post("/triage", json={"alert_text": ALERT}).json()
    assert r["memories_used"] == [] and any("recall failed" in w for w in r["warnings"])


def test_retain_failure_is_a_warning_not_a_crash(monkeypatch):
    use(monkeypatch, FakeMemory(fail_retain=True))
    r = client.post("/triage", json={"alert_text": ALERT}).json()
    assert any("retain failed" in w for w in r["warnings"])


def test_llm_failure_returns_labelled_safe_default(monkeypatch):
    use(monkeypatch, FakeMemory())
    def boom(model, messages, timeout):
        raise ConnectionError("groq down")
    monkeypatch.setattr(llm, "complete", boom)
    r = client.post("/triage", json={"alert_text": ALERT}).json()
    assert r["degraded"] is True and r["confidence"] == 0.0 and r["warnings"]


def test_bad_json_then_fallback_model(monkeypatch):
    use(monkeypatch, FakeMemory())
    calls = []
    def flaky(model, messages, timeout):
        calls.append(model)
        if model == "openai/gpt-oss-120b":
            return "not json at all"
        return json.dumps({"root_cause": "x", "suggested_fix": "y", "confidence": 55})
    monkeypatch.setattr(llm, "complete", flaky)
    r = client.post("/triage", json={"alert_text": ALERT}).json()
    assert r["degraded"] is False and r["model"] == "qwen/qwen3-32b" and r["confidence"] == 0.55
    assert calls.count("openai/gpt-oss-120b") == llm.MAX_ATTEMPTS


def test_prompt_contains_memories(monkeypatch):
    use(monkeypatch, FakeMemory(recall_result=["Fix X did NOT work"]))
    seen = {}
    def spy(model, messages, timeout):
        seen["user"] = messages[-1]["content"]
        return json.dumps({"root_cause": "x", "suggested_fix": "y"})
    monkeypatch.setattr(llm, "complete", spy)
    client.post("/triage", json={"alert_text": ALERT})
    assert "Fix X did NOT work" in seen["user"]


def test_judge():
    truth = "The nightly invoice-reconciliation cron (recon-batch) runs without a statement timeout and holds database connections open."
    assert judge.matches("recon-batch nightly cron runs without statement timeout holding connections open", truth)
    assert not judge.matches("The connection pool is too small for the traffic", truth)


def test_health_and_samples():
    assert client.get("/health").json()["status"] == "ok"
    assert len(client.get("/samples").json()) == 8
