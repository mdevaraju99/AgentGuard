from __future__ import annotations

from desktop_agent.tools.base import RiskLevel, Tool, ToolResult


class PermissionDenied(PermissionError):
    pass


def check_permission(tool: Tool, confirmed: bool = False) -> ToolResult | None:
    """Return a failure result if the tool must not run; otherwise None."""
    if tool.risk_level == RiskLevel.BLOCKED:
        return ToolResult(
            ok=False,
            error=f"Tool '{tool.name}' is blocked by policy.",
            data={"policy": "blocked", "tool": tool.name},
            evidence={"policy_decision": "blocked"},
        )
    if tool.risk_level == RiskLevel.CONFIRM and not confirmed:
        return ToolResult(
            ok=False,
            error=f"Tool '{tool.name}' requires explicit user confirmation.",
            data={"policy": "confirmation_required", "tool": tool.name},
            evidence={"policy_decision": "confirmation_required"},
        )
    return None
