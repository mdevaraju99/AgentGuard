from __future__ import annotations

import json
from typing import Any

from desktop_agent.agent.executor import execute_tool, loop_signature
from desktop_agent.agent.prompts import SYSTEM_PROMPT, history_context
from desktop_agent.agent.state import AgentState, TaskOutcome, new_id, utcnow
from desktop_agent.config import Settings, get_settings
from desktop_agent.llm.provider import LLMProvider, LLMResponse, ToolCall
from desktop_agent.safety.guardrails import refuse_message, should_allow_tools
from desktop_agent.tools.registry import ToolRegistry, build_default_registry


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
    return message


def _is_cancel(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"cancel", "nevermind", "never mind", "stop", "forget it", "forget that"}


def _merge_pending(pending: dict[str, Any], args_obj: dict[str, Any], user_request: str) -> dict[str, Any]:
    merged = dict(args_obj)
    for key in ("recipient", "message"):
        if pending.get(key) and not str(merged.get(key) or "").strip():
            merged[key] = pending[key]
    candidates = pending.get("candidates") or []
    if candidates:
        pick = user_request.strip()
        chosen = None
        if pick.isdigit():
            index = int(pick)
            if 1 <= index <= len(candidates):
                chosen = candidates[index - 1]
        else:
            lowered = pick.lower()
            for item in candidates:
                email = str(item.get("email") or "").lower()
                name = str(item.get("name") or "").lower()
                if lowered in {email, name} or (len(lowered) > 3 and lowered in name):
                    chosen = item
                    break
        if chosen:
            merged["recipient"] = chosen.get("email") or chosen.get("name")
            if pending.get("message") and not str(merged.get("message") or "").strip():
                merged["message"] = pending["message"]
            return merged
    missing = pending.get("missing")
    if missing and missing not in {"recipient_choice"} and not str(merged.get(missing) or "").strip():
        merged[missing] = user_request.strip()
    elif missing == "recipient" and not str(merged.get("recipient") or "").strip():
        merged["recipient"] = user_request.strip()
    elif missing == "message" and not str(merged.get("message") or "").strip():
        merged["message"] = user_request.strip()
    return merged


def _decide_outcome(state: AgentState) -> TaskOutcome:
    waiting = [
        s
        for s in state.steps_taken
        if (s.result.get("data") or {}).get("needs_input")
    ]
    if waiting:
        return TaskOutcome.AWAITING_INPUT
    if not state.steps_taken:
        return TaskOutcome.ACHIEVED if state.final_response else TaskOutcome.FAILED
    blocked = [s for s in state.steps_taken if s.result.get("data", {}).get("policy") == "blocked" or (s.error or "").endswith("blocked by policy.")]
    confirm = [s for s in state.steps_taken if s.confirmation_required]
    failed = [s for s in state.steps_taken if not s.ok]
    verified = [s for s in state.steps_taken if s.ok and s.verified]
    if confirm and not verified:
        return TaskOutcome.BLOCKED
    if blocked and not verified:
        return TaskOutcome.BLOCKED
    if failed and verified:
        return TaskOutcome.PARTIAL
    if failed and not verified:
        return TaskOutcome.FAILED
    if verified and all(s.verified or not s.ok for s in state.steps_taken if s.ok):
        unverified_ok = [s for s in state.steps_taken if s.ok and not s.verified]
        if unverified_ok:
            return TaskOutcome.PARTIAL
        return TaskOutcome.ACHIEVED
    return TaskOutcome.ACHIEVED


class DesktopAgent:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry | None = None,
        settings: Settings | None = None,
        confirmed_tools: set[str] | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry or build_default_registry()
        self.settings = settings or get_settings()
        self.confirmed_tools = confirmed_tools or set()
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._states: dict[str, AgentState] = {}
        self._pending: dict[str, dict[str, Any]] = {}

    def session_state(self, session_id: str) -> AgentState | None:
        return self._states.get(session_id)

    def run(self, user_request: str, session_id: str | None = None) -> AgentState:
        session_id = session_id or new_id()
        prior = self._states.get(session_id)
        state = AgentState(
            session_id=session_id,
            user_request=user_request,
            current_task=user_request,
            referenced_files=[],
            last_entities=dict(prior.last_entities) if prior else {},
            conversation_history=list(self._histories.get(session_id, [])),
        )
        state.emit("user_request", {"text": user_request, "session_id": session_id})

        pending = self._pending.get(session_id)
        if not should_allow_tools(
            user_request,
            pending=pending,
            last_entities=state.last_entities,
        ):
            state.final_response = refuse_message(user_request)
            state.task_outcome = TaskOutcome.BLOCKED
            state.finished_at = utcnow()
            state.emit(
                "guardrail_blocked",
                {"reason": "empty_or_off_topic", "text": user_request[:200]},
            )
            self._states[session_id] = state
            return state

        messages = list(self._histories.get(session_id, []))
        if not messages:
            messages.append({"role": "system", "content": SYSTEM_PROMPT})
        extra = history_context(state.referenced_files, state.last_entities, user_request)
        pending = self._pending.get(session_id)
        user_content = user_request
        if pending:
            user_content = (
                f"{user_request}\n\n[pending action]\n"
                f"Continue this action unless the user clearly changed the subject or said cancel.\n"
                f"tool={pending.get('tool')}\n"
                f"already known: recipient={pending.get('recipient')!r} message={pending.get('message')!r}\n"
                f"still needed: {pending.get('missing')}\n"
                f"Treat the user's latest message as the missing {pending.get('missing')} unless they cancelled."
            )
        if extra:
            user_content = f"{user_content}\n\n[session context]\n{extra}"
        messages.append({"role": "user", "content": user_content})

        tools = self.registry.openai_tools()
        final_text = ""

        while state.current_step < self.settings.max_steps:
            llm_t0 = utcnow()
            try:
                response = self.llm.complete(messages, tools)
            except Exception as exc:
                message = str(exc)
                if "api_key" in message.lower() or "sk-" in message:
                    message = "LLM authentication failed. Check LLM_PROVIDER / LLM_BASE_URL / API key in .env."
                state.errors.append(message)
                state.emit("llm_error", {"error": message})
                state.final_response = f"I could not call the language model: {message}"
                state.task_outcome = TaskOutcome.FAILED
                state.finished_at = utcnow()
                self._states[session_id] = state
                return state
            latency = (utcnow() - llm_t0).total_seconds() * 1000
            state.emit(
                "llm_call",
                {
                    "model": response.model,
                    "usage": response.usage,
                    "has_tool_calls": bool(response.tool_calls),
                    "content_preview": (response.content or "")[:400],
                },
                latency_ms=latency,
            )
            if not response.tool_calls:
                pending_slots = self._pending.get(session_id)
                if pending_slots and _is_cancel(user_request):
                    self._pending.pop(session_id, None)
                    final_text = (response.content or "").strip() or "Cancelled."
                    messages.append(_assistant_message(response))
                    break
                if pending_slots:
                    filled = _merge_pending(pending_slots, {}, user_request)
                    response.tool_calls = [
                        ToolCall(
                            id="pending-fill",
                            name=str(pending_slots.get("tool") or "send_teams_message"),
                            arguments=json.dumps(filled),
                        )
                    ]
                else:
                    final_text = (response.content or "").strip()
                    messages.append(_assistant_message(response))
                    break

            messages.append(_assistant_message(response))

            waiting_for_user = False
            for call in response.tool_calls:
                state.current_step += 1
                try:
                    args_obj = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args_obj = {"_raw": call.arguments}
                if not isinstance(args_obj, dict):
                    args_obj = {"_raw": call.arguments}
                pending_slots = self._pending.get(session_id)
                if pending_slots and call.name == pending_slots.get("tool"):
                    args_obj = _merge_pending(pending_slots, args_obj, user_request)
                signature = loop_signature(call.name, args_obj)
                state.loop_signatures.append(signature)
                if state.signature_count(signature) > self.settings.max_retries + 1:
                    record_error = f"Loop detected for {call.name}; stopping this tool."
                    state.errors.append(record_error)
                    tool_payload = json.dumps({"ok": False, "error": record_error})
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": tool_payload})
                    state.emit("loop_detected", {"tool": call.name, "signature": signature})
                    continue

                state.emit("tool_start", {"tool": call.name, "arguments": args_obj, "step": state.current_step})
                record = execute_tool(
                    self.registry,
                    state,
                    call.name,
                    args_obj,
                    confirmed_tools=self.confirmed_tools,
                )
                record.retry_count = max(0, state.signature_count(signature) - 1)
                state.steps_taken.append(record)
                state.selected_tools.append(call.name)
                state.verification_results.append(record.verification)
                state.emit(
                    "tool_end",
                    {
                        "tool": call.name,
                        "ok": record.ok,
                        "verified": record.verified,
                        "error": record.error,
                        "result": record.result,
                    },
                    latency_ms=record.latency_ms,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(record.result, default=str)[:24000],
                    }
                )
                data = record.result.get("data") or {}
                if data.get("needs_input") and data.get("pending"):
                    self._pending[session_id] = data["pending"]
                elif record.ok and self._pending.get(session_id, {}).get("tool") == call.name:
                    self._pending.pop(session_id, None)
                elif record.ok and call.name in {
                    "open_application",
                    "search_web",
                    "set_alarm",
                    "set_timer",
                    "search_files",
                    "open_file",
                }:
                    self._pending.pop(session_id, None)
                if data.get("needs_input"):
                    waiting_for_user = True
                    break
                if state.current_step >= self.settings.max_steps:
                    break
            if waiting_for_user:
                break

        if not final_text:
            waiting = next(
                (
                    s
                    for s in reversed(state.steps_taken)
                    if (s.result.get("data") or {}).get("needs_input")
                ),
                None,
            )
            if waiting:
                final_text = (waiting.result.get("data") or {}).get("question") or "I need a bit more information."
            elif state.current_step >= self.settings.max_steps:
                final_text = self._summarize_limit(state)
            elif state.steps_taken:
                last = state.steps_taken[-1]
                final_text = f"Stopped after tool '{last.tool_name}'. Verified={last.verified}. {last.error or ''}".strip()
            else:
                final_text = "I could not complete the request."

        state.final_response = final_text
        state.task_outcome = _decide_outcome(state)
        state.finished_at = utcnow()
        state.conversation_history = messages
        state.emit(
            "final_response",
            {
                "text": final_text,
                "task_outcome": state.task_outcome.value,
                "steps": len(state.steps_taken),
            },
        )
        self._histories[session_id] = messages
        self._states[session_id] = state
        return state

    def _summarize_limit(self, state: AgentState) -> str:
        names = ", ".join(s.tool_name for s in state.steps_taken) or "none"
        return (
            f"I reached the maximum of {self.settings.max_steps} steps without a final answer. "
            f"Tools used: {names}. Last error: {state.errors[-1] if state.errors else 'none'}."
        )
