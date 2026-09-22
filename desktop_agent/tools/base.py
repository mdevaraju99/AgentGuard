from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0

    def to_llm_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, "data": self.data, "evidence": self.evidence}
        if self.error:
            payload["error"] = self.error
        return payload


class VerificationResult(BaseModel):
    verified: bool
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class Tool(BaseModel):
    """Typed tool contract. The LLM never calls OS APIs directly."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    parameters: type[BaseModel]
    risk_level: RiskLevel = RiskLevel.SAFE
    timeout_s: float = 60
    handler: Callable[[BaseModel], ToolResult]
    verifier: Callable[[BaseModel, ToolResult], VerificationResult] | None = None

    def json_schema(self) -> dict[str, Any]:
        schema = self.parameters.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def parse_args(self, raw: dict[str, Any] | str) -> BaseModel:
        if isinstance(raw, str):
            return self.parameters.model_validate_json(raw)
        return self.parameters.model_validate(raw)

    def run(self, args: BaseModel) -> ToolResult:
        return self.handler(args)

    def verify(self, args: BaseModel, result: ToolResult) -> VerificationResult:
        if self.verifier is not None:
            return self.verifier(args, result)
        if not result.ok:
            return VerificationResult(verified=False, evidence=result.evidence, reason=result.error or "tool failed")
        return VerificationResult(verified=True, evidence=result.evidence or result.data, reason="ok")
