"""Pydantic mirror of ``ExecutionRun.run_summary`` JSONB payload (PR7).

The aggregator in ``core/llm/run_summary.py`` (TASK-004) emits this
shape; the ``/api/v1/run-summaries`` endpoint (TASK-005) returns lists
of these. The frontend RunQualityCard (TASK-006) renders one per run.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.src.core.llm.reply_quality import ReplyQualityKind


class RoundSummary(BaseModel):
    """One LLM round's classified outcome."""

    round_idx: int = Field(ge=0)
    content_chars: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    reply_quality: ReplyQualityKind
    finish_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RunSummary(BaseModel):
    """Per-run failure-mode aggregate stamped at terminal transition."""

    total_rounds: int = Field(ge=0)
    total_tool_calls: int = Field(ge=0)
    per_round: list[RoundSummary] = Field(default_factory=list)
    dominant_reply_quality: ReplyQualityKind
    did_emit_fenced_block: bool = False
    files_actually_written: list[str] = Field(default_factory=list)
    failure_signals: list[str] = Field(default_factory=list)

    # Free-form extension slot for forward-compatibility (e.g. token
    # counts, cost, model identifier when we add it). Keeps the
    # schema additive without minor migrations.
    extra: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)
