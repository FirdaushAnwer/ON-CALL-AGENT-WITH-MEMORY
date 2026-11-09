"""Request/response schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TriageRequest(BaseModel):
    alert_text: str = Field(min_length=3, description="Raw alert or error log text")
    service: str | None = None
    use_memory: bool = True
    bank_id: str | None = Field(default=None, description="Override the Hindsight memory bank (used by the demo run)")


class Verdict(BaseModel):
    """What the LLM must return."""

    root_cause: str
    reasoning: str = ""
    suggested_fix: str
    confidence: float = 0.5
    cited_memories: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.5
        if v > 1:  # model answered 0-100
            v = v / 100
        return max(0.0, min(1.0, v))

    @field_validator("cited_memories", mode="before")
    @classmethod
    def _coerce_cited(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]


class TriageResponse(Verdict):
    incident_id: str
    memory_enabled: bool
    memories_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    degraded: bool = False
    model: str | None = None


class FeedbackRequest(BaseModel):
    incident_id: str
    correct: bool
    actual_cause: str | None = None
    actual_fix: str | None = None
    suggested_fix_failed: bool = Field(default=False, description="The suggested fix was tried and did not work")
    tried_fix_failed: bool | None = Field(default=None, description="The suggested fix was tried and did not work (alias)")
    failed_fix: str | None = Field(default=None, description="Another fix that was tried and failed")
    bank_id: str | None = None


class FeedbackResponse(BaseModel):
    incident_id: str
    retained: bool
    warnings: list[str] = Field(default_factory=list)
