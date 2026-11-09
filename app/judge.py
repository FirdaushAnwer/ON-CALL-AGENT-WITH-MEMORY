"""Heuristic judge used ONLY by the automated demo/eval to decide if a verdict matched ground truth.

It checks what fraction of the meaningful words in the true root cause appear in the agent's
root cause + reasoning. It is a simple proxy, not a measure of real-world accuracy.
"""
from __future__ import annotations

import re

_STOP = {
    "the", "a", "an", "of", "on", "in", "to", "and", "or", "for", "is", "was", "at", "by", "with", "from",
    "that", "it", "its", "as", "be", "not", "no", "has", "have", "had", "were", "are", "this", "which",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_\-]+", text.lower()) if t not in _STOP and len(t) > 2}


def matches(predicted: str, truth: str, threshold: float = 0.45) -> bool:
    t = _tokens(truth)
    if not t:
        return False
    return len(t & _tokens(predicted)) / len(t) >= threshold
