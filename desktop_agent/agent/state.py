from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid4().hex[:12]


class TaskOutcome(str, Enum):
    IN_PROGRESS = "in_progress"
    AWAITING_INPUT = "awaiting_input"
    ACHIEVED = "achieved"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class TraceEvent(BaseModel):
    """Local structured event. Part 2 can map these 1:1 onto Langfuse spans."""

    event_type: str
    timestamp: datetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None


class StepRecord(BaseModel):
    step_index: int
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    ok: bool = False
    verified: bool = False
    verification: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    retry_count: int = 0
    risk_level: str = "safe"
    confirmation_required: bool = False


class AgentState(BaseModel):
    session_id: str = Field(default_factory=new_id)
    turn_id: str = Field(default_factory=new_id)
    user_request: str = ""
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    current_task: str = ""
    current_step: int = 0
    planned_steps: list[str] = Field(default_factory=list)
    steps_taken: list[StepRecord] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    referenced_files: list[str] = Field(default_factory=list)
    last_entities: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    retries: int = 0
    loop_signatures: list[str] = Field(default_factory=list)
    verification_results: list[dict[str, Any]] = Field(default_factory=list)
    trace_events: list[TraceEvent] = Field(default_factory=list)
    task_outcome: TaskOutcome = TaskOutcome.IN_PROGRESS
    final_response: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None

    def emit(self, event_type: str, payload: dict[str, Any] | None = None, latency_ms: float | None = None) -> None:
        self.trace_events.append(
            TraceEvent(event_type=event_type, payload=payload or {}, latency_ms=latency_ms)
        )

    def remember_file(self, path: str) -> None:
        if path and path not in self.referenced_files:
            self.referenced_files.append(path)
        self.last_entities["last_file"] = path

    def signature_count(self, signature: str) -> int:
        return sum(1 for item in self.loop_signatures if item == signature)
