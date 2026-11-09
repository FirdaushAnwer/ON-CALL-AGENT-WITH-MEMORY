"""Tests for HindsightInferenceMemory (hs-decide-1-flash reranking engine)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.memory import HindsightInferenceMemory, reset_memory_instance


class FakeCohereResult:
    def __init__(self, index: int, relevance_score: float):
        self.index = index
        self.relevance_score = relevance_score


class FakeCohereResponse:
    def __init__(self, results: list[FakeCohereResult]):
        self.results = results


def test_hindsight_inference_retain_and_recall(monkeypatch, tmp_path):
    reset_memory_instance()
    mock_cohere = MagicMock()
    mock_client = MagicMock()
    mock_cohere.Client.return_value = mock_client

    # Mock rerank response
    mock_client.rerank.return_value = FakeCohereResponse([
        FakeCohereResult(index=1, relevance_score=0.98),
        FakeCohereResult(index=0, relevance_score=0.45),
    ])

    monkeypatch.setattr("cohere.Client", mock_cohere.Client)
    monkeypatch.setenv("HINDSIGHT_BASE_URL", "https://inference.hindsight.vectorize.io/v1")
    monkeypatch.setenv("HINDSIGHT_API_KEY", "test-hsk-key")

    mem = HindsightInferenceMemory()
    mem._store_file = tmp_path / "test_store.json"

    # Retain two documents
    mem.retain("Doc 0: Redis issue", context="context", document_id="doc-0", tags=["redis"], bank="test-bank")
    mem.retain("Doc 1: HikariPool issue", context="context", document_id="doc-1", tags=["db"], bank="test-bank")

    # Recall
    recalled = mem.recall("HikariPool connection timeout", bank="test-bank", limit=2)
    assert len(recalled) == 2
    # First item should be Doc 1 because index 1 scored 0.98
    assert "HikariPool issue" in recalled[0]
    assert "Redis issue" in recalled[1]

    # Verify rerank arguments
    mock_client.rerank.assert_called_once()
    kwargs = mock_client.rerank.call_args.kwargs
    assert kwargs["model"] == "hs-decide-1-flash"
    assert kwargs["query"] == "HikariPool connection timeout"
    assert len(kwargs["documents"]) == 2


def test_hindsight_inference_empty_bank(monkeypatch, tmp_path):
    reset_memory_instance()
    mock_cohere = MagicMock()
    monkeypatch.setattr("cohere.Client", mock_cohere.Client)

    mem = HindsightInferenceMemory()
    mem._store_file = tmp_path / "test_store_empty.json"

    recalled = mem.recall("Any query", bank="nonexistent-bank")
    assert recalled == []

