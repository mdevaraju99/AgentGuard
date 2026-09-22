from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from desktop_agent.tools.people import names_match


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


def graph_request(
    token: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    extra_headers: dict | None = None,
) -> Any:
    url = path if path.startswith("http") else f"{GRAPH_ROOT}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GraphError(f"Graph {method} {path} failed ({exc.code}): {detail[:800]}") from exc


def _pages(token: str, path: str, *, extra_headers: dict | None = None, limit: int = 5) -> list[dict]:
    items: list[dict] = []
    next_path = path
    for _ in range(limit):
        payload = graph_request(token, "GET", next_path, extra_headers=extra_headers)
        items.extend(payload.get("value") or [])
        nxt = payload.get("@odata.nextLink")
        if not nxt:
            break
        next_path = nxt
    return items


def other_member(chat: dict, my_id: str) -> dict:
    members = chat.get("members") or []
    for member in members:
        user_id = str(member.get("userId") or "")
        if user_id and user_id == my_id:
            continue
        display = str(member.get("displayName") or "").strip().lower()
        if display in {"me", "you"}:
            continue
        return member
    for member in members:
        if str(member.get("userId") or "") != my_id:
            return member
    return {}


def find_matching_chats(token: str, query: str, my_id: str) -> list[dict]:
    chats = _pages(token, "/me/chats?$expand=members&$top=50")
    scored: list[dict] = []
    for chat in chats:
        member = other_member(chat, my_id)
        name = str(member.get("displayName") or chat.get("topic") or "")
        email = str(member.get("email") or "")
        score = max(names_match(query, name), names_match(query, email.split("@")[0]))
        if query.lower() == email.lower() and email:
            score = 100
        if score < 84:
            continue
        scored.append(
            {
                "chat_id": chat.get("id"),
                "name": name,
                "email": email,
                "user_id": member.get("userId"),
                "score": score,
            }
        )
    scored.sort(key=lambda item: -int(item["score"]))
    return scored


def search_people(token: str, query: str) -> list[dict]:
    try:
        quoted = urllib.parse.quote(f'"{query}"')
        people = graph_request(
            token,
            "GET",
            f"/me/people?$search={quoted}&$top=10",
        ).get("value") or []
    except GraphError:
        people = []
    hits: list[dict] = []
    for person in people:
        name = str(person.get("displayName") or "")
        scored_emails = person.get("scoredEmailAddresses") or []
        email = ""
        if scored_emails:
            email = str(scored_emails[0].get("address") or "")
        user_id = str(person.get("id") or "")
        score = names_match(query, name)
        if score < 70:
            continue
        hits.append({"name": name, "email": email, "user_id": user_id, "score": score, "source": "people"})
    if hits:
        hits.sort(key=lambda item: -int(item["score"]))
        return hits
    users = graph_request(
        token,
        "GET",
        f"/users?$search={urllib.parse.quote(chr(34) + 'displayName:' + query + chr(34))}&$top=10",
        extra_headers={"ConsistencyLevel": "eventual"},
    ).get("value") or []
    for user in users:
        name = str(user.get("displayName") or "")
        email = str(user.get("mail") or user.get("userPrincipalName") or "")
        hits.append(
            {
                "name": name,
                "email": email,
                "user_id": user.get("id"),
                "score": names_match(query, name),
                "source": "users",
            }
        )
    hits.sort(key=lambda item: -int(item["score"]))
    return [item for item in hits if item["score"] >= 70]


def create_or_get_chat(token: str, my_id: str, other_id: str) -> str:
    body = {
        "chatType": "oneOnOne",
        "members": [
            {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": ["owner"],
                "kevin.m@example.com": f"{GRAPH_ROOT}/users('{my_id}')",
            },
            {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": ["owner"],
                "kevin.m@example.com": f"{GRAPH_ROOT}/users('{other_id}')",
            },
        ],
    }
    chat = graph_request(token, "POST", "/chats", body=body)
    chat_id = chat.get("id")
    if not chat_id:
        raise GraphError("Graph did not return a chat id")
    return str(chat_id)


def post_message(token: str, chat_id: str, message: str) -> dict:
    return graph_request(
        token,
        "POST",
        f"/chats/{chat_id}/messages",
        body={"body": {"contentType": "text", "content": message}},
    )


def send_chat_message(token: str, recipient: str, message: str) -> dict:
    me = graph_request(token, "GET", "/me")
    my_id = str(me.get("id") or "")
    chats = find_matching_chats(token, recipient, my_id)
    chosen = None
    if chats and (len(chats) == 1 or chats[0]["score"] - (chats[1]["score"] if len(chats) > 1 else 0) >= 8):
        chosen = chats[0]
    people = [] if chosen else search_people(token, recipient)
    if not chosen and people:
        top = people[0]
        second = people[1]["score"] if len(people) > 1 else 0
        if top["score"] >= 84 and top["score"] - second >= 8:
            chosen = top
        elif len(people) == 1:
            chosen = top
        else:
            return {"needs_choice": True, "matches": people[:6], "chats": chats[:6]}
    if not chosen:
        if chats:
            return {"needs_choice": True, "matches": chats[:6]}
        raise GraphError(f"Microsoft Graph could not find '{recipient}' in your Teams chats or directory.")
    chat_id = chosen.get("chat_id")
    if not chat_id:
        other_id = chosen.get("user_id")
        if not other_id:
            raise GraphError(f"Found {chosen.get('name')} but no user id to open a chat.")
        chat_id = create_or_get_chat(token, my_id, str(other_id))
    posted = post_message(token, str(chat_id), message)
    return {
        "delivered": True,
        "method": "microsoft_graph",
        "recipient": chosen.get("name") or recipient,
        "email": chosen.get("email"),
        "chat_id": chat_id,
        "message_id": posted.get("id"),
        "message": message,
    }
