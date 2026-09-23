from __future__ import annotations

import re

EMPTY_REPLY = (
    "I need a request before I can do anything on this PC. "
    "For example: open Chrome, find a file, take a screenshot, or check the battery."
)

OFF_TOPIC_REPLY = (
    "I only act when you ask for something on this PC — open an app, find a file, "
    "take a screenshot, or check battery, storage, or the time. What should I do?"
)

_VOICE_PREFIX = re.compile(r"^\[voice\]\s*", re.IGNORECASE)
_CONTEXT_SPLIT = re.compile(r"\n\n\[(?:session context|pending action)\]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_TASK = re.compile(
    r"\b("
    r"open|close|launch|start|find|search|look(?:\s+up)?|google|meaning|definition|"
    r"screenshot|lock|unlock|battery|charge|time|date|wifi|wi-fi|bluetooth|"
    r"volume|mute|brightness|chrome|browser|notepad|teams|outlook|vscode|"
    r"calculator|explorer|podman|parliament|storage|disk|folder|file|files|"
    r"document|documents|downloads|desktop|resume|pdf|docx|installed|running|"
    r"create|make|write|save|summarize|extract|read|check|get|timer|alarm|settings|network|"
    r"ip|hostname|cpu|ram|gpu|pc|computer|laptop|windows|screenshot|"
    r"how many|what's on|what is on|list|show me|take a|"
    r"download|downloaded|summarize|summarise|named|modified"
    r")\b",
    re.IGNORECASE,
)

_FOLLOW_UP = re.compile(
    r"("
    r"\b(it|that|this|that file|this file|the file)\b|"
    r"\b(yes|yeah|yep|ok|okay|sure)\b|"
    r"\b(open|summarize|summarise|name|named|when|download|downloaded|modified)\b|"
    r"\bthe (first|second|third)\b|#?\d+"
    r")",
    re.IGNORECASE,
)

_SMALL_TALK = re.compile(
    r"^\s*("
    r"hi|hello|hey|thanks|thank you|ok|okay|cool|nice|"
    r"i('m| am) (feeling )?(sad|happy|tired|bored|lonely|upset|down)|"
    r"feeling (sad|happy|tired|bored)|"
    r"how are you|what('s| is) up"
    r")\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def visible_request(text: str) -> str:
    raw = (text or "").strip()
    raw = _VOICE_PREFIX.sub("", raw)
    raw = _CONTEXT_SPLIT.split(raw, maxsplit=1)[0]
    return raw.strip()


def is_empty_request(text: str) -> bool:
    cleaned = _NON_ALNUM.sub("", visible_request(text).lower())
    return not cleaned


def looks_like_desktop_task(text: str) -> bool:
    visible = visible_request(text)
    if not visible or is_empty_request(visible):
        return False
    if _SMALL_TALK.match(visible):
        return False
    return bool(_TASK.search(visible))


def is_follow_up_action(text: str) -> bool:
    return bool(_FOLLOW_UP.match(visible_request(text)))


def should_allow_tools(
    text: str,
    *,
    pending: dict | None = None,
    last_entities: dict | None = None,
) -> bool:
    if is_empty_request(text):
        return False
    if pending:
        return True
    if _SMALL_TALK.match(visible_request(text)):
        return False
    if looks_like_desktop_task(text):
        return True
    last_file = (last_entities or {}).get("last_file")
    if last_file and (
        is_follow_up_action(text)
        or re.search(r"\b(when|name|named|summarize|summarise|download|downloaded|modified|date)\b", visible_request(text), re.I)
    ):
        return True
    return False


def refuse_message(text: str) -> str:
    if is_empty_request(text):
        return EMPTY_REPLY
    return OFF_TOPIC_REPLY
