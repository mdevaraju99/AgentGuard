from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from desktop_agent.config import PROJECT_ROOT
from desktop_agent.graph.auth import GraphAuthRequired, get_access_token
from desktop_agent.graph.client import send_chat_message
from desktop_agent.tools.base import Tool, ToolResult, VerificationResult

FLOW_PATH = PROJECT_ROOT / ".runtime" / "graph_device_flow.json"


class SendTeamsArgs(BaseModel):
    recipient: str = Field(
        default="",
        description="Person's name as the user said it, e.g. Janapati Thanusree or Megha S D",
    )
    message: str = Field(default="", description="The message text to send")


def _needs(slot: str, question: str, recipient: str, message: str, extra: dict | None = None) -> ToolResult:
    pending = {
        "tool": "send_teams_message",
        "recipient": recipient,
        "message": message,
        "missing": slot,
    }
    if extra:
        pending.update(extra)
    return ToolResult(
        ok=True,
        data={
            "needs_input": True,
            "slot": slot,
            "question": question,
            "pending": pending,
        },
        evidence={"needs_input": True, "slot": slot},
        error=None,
    )


def _choice_question(query: str, matches: list[dict]) -> str:
    lines = [f"I found more than one person matching '{query}'. Reply with the number:"]
    for index, item in enumerate(matches[:6], start=1):
        email = item.get("email") or ""
        extra = f" — {email}" if email else ""
        lines.append(f"{index}. {item.get('name')}{extra}")
    return "\n".join(lines)


def compose_and_send(recipient: str, message: str, email: str | None = None, device_flow: dict | None = None) -> dict:
    flow = device_flow
    if flow is None and FLOW_PATH.exists():
        try:
            flow = json.loads(FLOW_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            flow = None
    try:
        token = get_access_token(interactive=not bool(flow), device_flow=flow)
    except Exception:
        if flow and FLOW_PATH.exists():
            FLOW_PATH.unlink(missing_ok=True)
        token = get_access_token(interactive=True, device_flow=None)
    if FLOW_PATH.exists():
        FLOW_PATH.unlink(missing_ok=True)
    return send_chat_message(token, recipient, message)


def build_teams_tools() -> list[Tool]:
    def send_teams_message(args: SendTeamsArgs) -> ToolResult:
        recipient = (args.recipient or "").strip()
        message = (args.message or "").strip()
        if not recipient:
            return _needs(
                "recipient",
                "Who should I message in Teams? Say their name.",
                recipient,
                message,
            )
        if not message:
            return _needs(
                "message",
                f"What message should I send to {recipient} in Teams?",
                recipient,
                message,
            )
        try:
            sent = compose_and_send(recipient, message)
        except GraphAuthRequired as exc:
            if exc.flow:
                FLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
                FLOW_PATH.write_text(json.dumps(exc.flow), encoding="utf-8")
            return _needs(
                "graph_login",
                exc.question,
                recipient,
                message,
                extra={"flow": exc.flow} if exc.flow else None,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                error=str(exc),
                data={"recipient": recipient, "message": message, "delivered": False},
                evidence={"delivered": False},
            )
        if sent.get("needs_choice"):
            matches = sent.get("matches") or sent.get("chats") or []
            return _needs(
                "recipient_choice",
                _choice_question(recipient, matches),
                recipient,
                message,
                extra={"candidates": matches},
            )
        return ToolResult(
            ok=True,
            data={
                "recipient": sent.get("recipient") or recipient,
                "email": sent.get("email"),
                "message": message,
                "delivered": True,
                "method": sent.get("method"),
                "chat_id": sent.get("chat_id"),
                "message_id": sent.get("message_id"),
                "note": (
                    f"Sent via Microsoft Graph to {sent.get('recipient') or recipient}"
                    + (f" <{sent.get('email')}>" if sent.get("email") else "")
                    + "."
                ),
            },
            evidence={
                "delivered": True,
                "recipient": sent.get("recipient") or recipient,
                "method": sent.get("method"),
                "message_id": sent.get("message_id"),
            },
        )

    def verify(_args: SendTeamsArgs, result: ToolResult) -> VerificationResult:
        if result.data.get("needs_input"):
            return VerificationResult(verified=True, evidence=result.evidence, reason="waiting for user")
        delivered = bool(result.data.get("delivered"))
        return VerificationResult(
            verified=delivered,
            evidence=result.evidence,
            reason="Graph delivered Teams message" if delivered else result.error or "not sent",
        )

    return [
        Tool(
            name="send_teams_message",
            description=(
                "Send a Microsoft Teams chat using the Microsoft Graph API. "
                "Pass the person's name and the message. It finds them in your Teams chats/directory and posts the message. "
                "If Microsoft sign-in is needed, show the tool question to the user. "
                "Do NOT call lookup_person first. Never say sent unless delivered=true."
            ),
            parameters=SendTeamsArgs,
            timeout_s=90,
            handler=send_teams_message,
            verifier=verify,
        )
    ]
