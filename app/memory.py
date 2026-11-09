"""All Hindsight calls live in this file.

Hindsight Integration Summary & Verified Signatures:
===================================================
Vectorize provides two complementary deployment patterns for Hindsight:

A. Hindsight Cloud Memory Banks & Self-Hosted Instances:
--------------------------------------------------------
Package Installation:
    pip install hindsight-client

Docs: https://hindsight.vectorize.io/
Repo: https://github.com/vectorize-io/hindsight
Cloud Console: https://ui.hindsight.vectorize.io

Exact Client Methods & Signatures:
1. Client Initialization:
    from hindsight_client import Hindsight
    client = Hindsight(
        base_url="https://api.hindsight.vectorize.io",  # or "http://localhost:8888" for open-source
        api_key="your-api-key",                         # required for Hindsight Cloud, optional for self-hosted
        timeout=60.0                                    # request timeout in seconds
    )

2. Retain (Store memories into an isolated team bank):
    client.retain(
        bank_id="oncall-incidents",                     # isolated namespace per team/workspace
        content="Incident details or feedback text",    # unstructured text processed into structured facts
        context="incident outcome: engineer correction",# categorization context
        document_id="INC-12345-feedback",               # unique document ID
        tags=["incident", "correction", "service:billing-api"],
        retain_async=False                              # sync processing for immediate recall
    )

3. Recall (Retrieve relevant memories before triage):
    resp = client.recall(
        bank_id="oncall-incidents",                     # isolated memory bank
        query="HikariPool connection timeout billing-api", # search query (TEMPR multi-strategy search)
        max_tokens=2048,                                # token budget
        budget="mid"                                    # search budget: "low", "mid", "high"
    )
    # resp.results is a list of RecallResult items:
    memories = [r.text for r in resp.results]


B. Hindsight Inference API (Cohere-Compatible Decision & Reranking Engine):
--------------------------------------------------------------------------
Endpoint: https://inference.hindsight.vectorize.io/v1 (or https://inference.hindsight.vectorize.io)
Decision / Reranker Model: hs-decide-1-flash
Embedding Model: hs-embed-1
Package: cohere (pip install cohere) or direct HTTP POST to /v1/rerank

Exact Cohere Client Integration:
    import cohere
    co = cohere.Client(
        api_key="<your Hindsight API key>",
        base_url="https://inference.hindsight.vectorize.io",
    )
    response = co.rerank(
        model="hs-decide-1-flash",
        query="ERROR [billing-api] HikariPool-1 Connection timeout",
        documents=["Past incident: pool exhausted...", "Past incident: Redis OOM..."],
        top_n=6
    )
    # response.results contains list of items with .index and .relevance_score
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Protocol

from .config import get_settings

log = logging.getLogger("oncall.memory")


class Memory(Protocol):
    def retain(self, content: str, *, context: str, document_id: str, tags: list[str], bank: str | None = None) -> None: ...

    def recall(self, query: str, *, bank: str | None = None, limit: int = 6) -> list[str]: ...

    def clear(self, bank: str | None = None) -> None: ...


class HindsightInferenceMemory:
    """Uses Hindsight Inference API (hs-decide-1-flash reranker via Cohere client).

    Persists memories locally per bank_id and uses Vectorize's Hindsight Inference model
    (hs-decide-1-flash) to semantically score and rerank all candidate memories and engineer
    corrections on recall.
    """

    def __init__(self) -> None:
        import cohere

        s = get_settings()
        self._s = s
        raw_url = s.hindsight_base_url or "https://inference.hindsight.vectorize.io"
        base_url = raw_url.rstrip("/").removesuffix("/v1")
        if "inference.hindsight.vectorize.io" not in base_url and not base_url.startswith("http"):
            base_url = "https://inference.hindsight.vectorize.io"
        self._base_url = base_url
        api_key = s.hindsight_api_key or os.getenv("HINDSIGHT_API_KEY", "")
        self._client = cohere.Client(
            api_key=api_key or "anonymous",
            base_url=self._base_url,
            timeout=s.hindsight_timeout,
        )
        self._banks: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self._store_file = Path("data/hindsight_inference_store.json")
        self._load_store()

    def _load_store(self) -> None:
        if self._store_file.exists():
            try:
                data = json.loads(self._store_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._banks = data
            except Exception as e:
                log.warning("Could not load %s: %s", self._store_file, e)

    def _save_store(self) -> None:
        try:
            self._store_file.parent.mkdir(parents=True, exist_ok=True)
            self._store_file.write_text(json.dumps(self._banks, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Could not save %s: %s", self._store_file, e)

    def retain(self, content: str, *, context: str, document_id: str, tags: list[str], bank: str | None = None) -> None:
        bank_id = bank or self._s.hindsight_bank_id
        log.info(">>> [HINDSIGHT INFERENCE RETAIN] bank=%s doc=%s tags=%s | %s",
                 bank_id, document_id, tags, content[:140].replace("\n", " "))
        record = {
            "document_id": document_id,
            "content": content,
            "context": context,
            "tags": tags,
        }
        with self._lock:
            docs = self._banks.setdefault(bank_id, [])
            for i, d in enumerate(docs):
                if d.get("document_id") == document_id:
                    docs[i] = record
                    break
            else:
                docs.append(record)
            self._save_store()

    def recall(self, query: str, *, bank: str | None = None, limit: int = 6) -> list[str]:
        bank_id = bank or self._s.hindsight_bank_id
        with self._lock:
            docs = list(self._banks.get(bank_id, []))
        if not docs:
            log.info(">>> [HINDSIGHT INFERENCE RECALL] bank=%s (empty) -> 0 memories", bank_id)
            return []

        doc_contents = [d["content"] for d in docs]
        try:
            resp = self._client.rerank(
                model=self._s.hindsight_rerank_model,
                query=query,
                documents=doc_contents,
                top_n=min(limit, len(doc_contents)),
            )
            recalled: list[str] = []
            top_score = 0.0
            for r in resp.results:
                if r.index < len(doc_contents):
                    recalled.append(doc_contents[r.index])
                    if r.relevance_score > top_score:
                        top_score = r.relevance_score
            log.info(">>> [HINDSIGHT INFERENCE RECALL] bank=%s query=%r -> %d memories recalled (hs-decide-1-flash top score=%.4f)",
                     bank_id, query[:80], len(recalled), top_score)
            return recalled
        except Exception as e:
            log.warning("Hindsight Inference rerank call failed: %s; falling back to token matching", e)
            local = LocalMemory()
            with local._lock:
                local._banks[bank_id] = doc_contents
            return local.recall(query, bank=bank_id, limit=limit)

    def clear(self, bank: str | None = None) -> None:
        bank_id = bank or self._s.hindsight_bank_id
        with self._lock:
            if bank_id in self._banks:
                del self._banks[bank_id]
                self._save_store()
        log.info("Cleared Hindsight Inference memory bank '%s'", bank_id)


class HindsightMemory:
    """Uses Hindsight Cloud Memory Banks (or self-hosted) via hindsight-client SDK."""

    def __init__(self) -> None:
        s = get_settings()
        self._s = s
        # If the configured base URL points to Hindsight Inference, delegate to HindsightInferenceMemory
        if "inference.hindsight.vectorize.io" in s.hindsight_base_url.lower():
            self._delegate: Memory | None = HindsightInferenceMemory()
            return
        # If no API key is provided and not pointing to localhost, fallback to LocalMemory
        if not s.hindsight_api_key and "localhost" not in s.hindsight_base_url and "127.0.0.1" not in s.hindsight_base_url:
            log.info("No HINDSIGHT_API_KEY configured for %s; delegating to LocalMemory for offline run.", s.hindsight_base_url)
            self._delegate = LocalMemory()
            return
        self._delegate = None

        from hindsight_client import Hindsight  # imported lazily so tests don't need a server

        self._client = Hindsight(
            base_url=s.hindsight_base_url,
            api_key=s.hindsight_api_key or None,
            timeout=s.hindsight_timeout,
        )

    def retain(self, content: str, *, context: str, document_id: str, tags: list[str], bank: str | None = None) -> None:
        if self._delegate is not None:
            return self._delegate.retain(content, context=context, document_id=document_id, tags=tags, bank=bank)
        bank_id = bank or self._s.hindsight_bank_id
        log.info(">>> [HINDSIGHT RETAIN] bank=%s doc=%s tags=%s | %s", bank_id, document_id, tags, content[:140].replace("\n", " "))
        self._client.retain(
            bank_id=bank_id,
            content=content,
            context=context,
            document_id=document_id,
            tags=tags,
            retain_async=self._s.retain_async,
        )

    def recall(self, query: str, *, bank: str | None = None, limit: int = 6) -> list[str]:
        if self._delegate is not None:
            return self._delegate.recall(query, bank=bank, limit=limit)
        bank_id = bank or self._s.hindsight_bank_id
        try:
            resp = self._client.recall(bank_id=bank_id, query=query, max_tokens=2048, budget="mid")
            texts = [getattr(r, "text", str(r)) for r in (resp.results or [])][:limit]
            log.info(">>> [HINDSIGHT RECALL] bank=%s query=%r -> %d memories recalled", bank_id, query[:80], len(texts))
            return texts
        except Exception as e:
            # Handle cold-start on a fresh bank (404 / NotFoundException)
            err_str = str(e).lower()
            if "notfound" in err_str or "not found" in err_str or "404" in err_str:
                log.info(">>> [HINDSIGHT RECALL] bank=%s cold start (not found) -> 0 memories", bank_id)
                return []
            log.warning("HINDSIGHT RECALL error: %s", e)
            raise

    def clear(self, bank: str | None = None) -> None:
        if self._delegate is not None:
            return self._delegate.clear(bank=bank)
        bank_id = bank or self._s.hindsight_bank_id
        log.info("Hindsight Cloud bank '%s' reset requested", bank_id)


class LocalMemory:
    """Offline stub: keyword-overlap search over memories retained in this process."""

    _STOP = {
        "the", "a", "an", "of", "on", "in", "to", "and", "or", "for", "is", "was", "at", "by", "with", "from",
        "that", "it", "its", "as", "be", "not", "no", "has", "have", "had", "were", "are", "this", "which",
        "2026", "error", "warn", "alert", "service", "internal", "incident", "feedback", "suggested", "root",
        "cause", "agent", "worked", "did", "again", "kind", "confirmed", "engineer",
        "host", "trace", "pod", "node", "region", "status", "http", "after", "before", "time", "date",
        "set", "get", "all", "more", "less", "total", "past", "open", "runs", "like", "none",
        "request", "requests", "attempt", "calls", "call", "using", "uses",
        "api", "fail", "failing", "failure", "failed", "logs", "log",
    }

    DOMAIN_WEIGHTS = {
        "hikaripool", "pgbouncer", "postgres", "statement_timeout", "recon", "reconciliation",
        "redis", "maxmemory", "evicted_keys", "evicted", "ttl", "expire",
        "tls", "certificate", "cert", "x509", "ssl", "handshake",
        "5xx", "503", "spike", "feature", "flag", "checkout", "tax", "west", "config", "sync",
        "kafka", "broker", "isr", "partition", "partitions", "notenoughreplicas",
        "dns", "coredns", "lookup", "udp", "getaddrinfo", "eai_again",
        "oomkilled", "cgroup", "bitmap", "leak",
        "429", "ratelimit", "sendgrid", "retry",
    }

    def __init__(self) -> None:
        self._banks: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def _tokens(self, text: str) -> set[str]:
        cleaned = re.sub(r"[^a-zA-Z0-9]", " ", text.lower())
        words = set()
        for t in cleaned.split():
            if t not in self._STOP and len(t) > 2:
                words.add(t)
                for suffix in ("ing", "ed", "es", "s"):
                    if t.endswith(suffix) and len(t) > len(suffix) + 2:
                        words.add(t[:-len(suffix)])
        return words

    def _score(self, q_tokens: set[str], doc_tokens: set[str]) -> int:
        return sum(3 if w in self.DOMAIN_WEIGHTS else 1 for w in (q_tokens & doc_tokens))

    def retain(self, content: str, *, context: str, document_id: str, tags: list[str], bank: str | None = None) -> None:
        bank_id = bank or get_settings().hindsight_bank_id
        log.info(">>> [LOCAL RETAIN] bank=%s doc=%s tags=%s | %s", bank_id, document_id, tags, content[:140].replace("\n", " "))
        with self._lock:
            self._banks.setdefault(bank_id, []).append(content)

    def recall(self, query: str, *, bank: str | None = None, limit: int = 6) -> list[str]:
        bank_id = bank or get_settings().hindsight_bank_id
        q = self._tokens(query)
        with self._lock:
            docs = list(self._banks.get(bank_id, []))
        scored = sorted(((self._score(q, self._tokens(d)), d) for d in docs), key=lambda x: x[0], reverse=True)
        out = [d for score, d in scored if score >= 1][:limit]
        log.info(">>> [LOCAL RECALL] bank=%s query=%r -> %d memories recalled", bank_id, query[:80], len(out))
        return out

    def clear(self, bank: str | None = None) -> None:
        bank_id = bank or get_settings().hindsight_bank_id
        with self._lock:
            self._banks.pop(bank_id, None)
        log.info("Cleared local memory bank '%s'", bank_id)


_instance: Memory | None = None
_lock = threading.Lock()


def reset_memory_instance() -> None:
    global _instance
    with _lock:
        _instance = None


def get_memory() -> Memory:
    global _instance
    with _lock:
        if _instance is None:
            s = get_settings()
            if s.memory_backend == "local":
                _instance = LocalMemory()
            elif s.memory_backend in ("inference", "hindsight_inference") or "inference.hindsight.vectorize.io" in s.hindsight_base_url.lower():
                _instance = HindsightInferenceMemory()
            elif not s.hindsight_api_key and "localhost" not in s.hindsight_base_url and "127.0.0.1" not in s.hindsight_base_url:
                log.info("No HINDSIGHT_API_KEY configured for %s; using LocalMemory fallback for offline operation.", s.hindsight_base_url)
                _instance = LocalMemory()
            else:
                _instance = HindsightMemory()
        return _instance
