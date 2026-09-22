from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from desktop_agent.agent.state import AgentState, StepRecord, utcnow
from desktop_agent.safety.permissions import check_permission
from desktop_agent.safety.sandbox import SandboxError
from desktop_agent.tools.base import RiskLevel, ToolResult
from desktop_agent.tools.registry import ToolRegistry


def _signature(name: str, arguments: dict[str, Any]) -> str:
    blob = json.dumps({"tool": name, "args": arguments}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


FILE_TOOLS = {
    "search_files",
    "open_file",
    "read_file",
    "extract_document",
    "create_file",
    "create_folder",
}


def _remember(state: AgentState, tool_name: str, result: ToolResult) -> None:
    data = result.data or {}
    if tool_name == "search_files":
        if not data.get("needs_disambiguation"):
            if data.get("best_match") and data["best_match"].get("path"):
                state.remember_file(data["best_match"].get("absolute_path") or data["best_match"]["path"])
            else:
                files = data.get("files") or []
                if files:
                    state.remember_file(files[0].get("absolute_path") or files[0]["path"])
    elif tool_name in FILE_TOOLS:
        path = data.get("absolute_path") or data.get("path")
        if isinstance(path, str) and path.strip():
            state.remember_file(path)
    if data.get("url"):
        state.last_entities["last_url"] = data["url"]


def execute_tool(
    registry: ToolRegistry,
    state: AgentState,
    name: str,
    raw_args: dict[str, Any] | str,
    *,
    confirmed_tools: set[str] | None = None,
) -> StepRecord:
    started = utcnow()
    t0 = time.perf_counter()
    confirmed_tools = confirmed_tools or set()
    arguments: dict[str, Any]
    if isinstance(raw_args, str):
        try:
            arguments = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            arguments = {"_raw": raw_args}
    else:
        arguments = dict(raw_args)

    def fail(error: str, result: ToolResult | None = None, risk: str = "safe") -> StepRecord:
        payload = result.to_llm_payload() if result else {"ok": False, "error": error}
        record = StepRecord(
            step_index=state.current_step,
            tool_name=name,
            arguments=arguments,
            result=payload,
            ok=False,
            verified=False,
            error=error,
            latency_ms=(time.perf_counter() - t0) * 1000,
            started_at=started,
            ended_at=utcnow(),
            risk_level=risk,
            confirmation_required="confirmation_required" in error,
        )
        state.errors.append(error)
        return record

    try:
        tool = registry.get(name)
    except KeyError:
        return fail(f"Unknown tool: {name}")

    denied = check_permission(tool, confirmed=name in confirmed_tools)
    if denied is not None:
        record = fail(denied.error or "denied", denied, tool.risk_level.value)
        record.confirmation_required = tool.risk_level == RiskLevel.CONFIRM
        return record

    try:
        args = tool.parse_args(arguments)
        arguments = args.model_dump()
    except Exception as exc:
        return fail(f"Invalid arguments for {name}: {exc}", risk=tool.risk_level.value)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tool.run, args)
            result = future.result(timeout=tool.timeout_s)
    except FutureTimeout:
        result = ToolResult(ok=False, error=f"Tool {name} timed out after {tool.timeout_s}s")
    except SandboxError as exc:
        result = ToolResult(ok=False, error=str(exc), evidence={"policy_decision": "sandbox_denied"})
    except Exception as exc:
        result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    result.latency_ms = (time.perf_counter() - t0) * 1000
    verification = tool.verify(args, result)
    record = StepRecord(
        step_index=state.current_step,
        tool_name=name,
        arguments=arguments,
        result=result.to_llm_payload(),
        ok=result.ok,
        verified=verification.verified,
        verification=verification.model_dump(),
        error=result.error,
        latency_ms=result.latency_ms,
        started_at=started,
        ended_at=utcnow(),
        risk_level=tool.risk_level.value,
        confirmation_required=False,
    )
    if result.ok:
        _remember(state, name, result)
    return record


def loop_signature(name: str, arguments: dict[str, Any]) -> str:
    return _signature(name, arguments)
