from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

TRACE_HOST = "127.0.0.1"
TRACE_PORT = 8765

_LOCK = threading.Lock()
_TURNS: list[dict[str, Any]] = []
_SERVER_STARTED = False
_IS_OWNER = False


def trace_from_state(state: Any) -> dict[str, Any]:
    if state is None:
        return {}
    return {
        "task_outcome": getattr(getattr(state, "task_outcome", None), "value", None),
        "steps": [
            {
                "tool": step.tool_name,
                "ok": step.ok,
                "verified": step.verified,
                "error": step.error,
            }
            for step in getattr(state, "steps_taken", []) or []
        ],
    }


def _store(row: dict[str, Any]) -> None:
    with _LOCK:
        _TURNS.append(row)
        del _TURNS[:-200]


def append_turn(
    *,
    source: str,
    user: str,
    assistant: str,
    trace: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid4().hex[:12],
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "user": user,
        "assistant": assistant,
        "trace": trace or {},
    }
    if extra:
        row["extra"] = extra
    _store(row)
    if not _IS_OWNER:
        _post_row(row)
    return row


def snapshot() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_TURNS)


def clear_turns() -> None:
    with _LOCK:
        _TURNS.clear()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/turns":
            self.send_error(404)
            return
        body = json.dumps(snapshot(), default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/turns":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            row = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400)
            return
        if isinstance(row, dict) and "user" in row:
            _store(row)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _TraceServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def _post_row(row: dict[str, Any], host: str = TRACE_HOST, port: int = TRACE_PORT) -> bool:
    import urllib.error
    import urllib.request

    try:
        request = urllib.request.Request(
            f"http://{host}:{port}/turns",
            data=json.dumps(row, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=0.8):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def start_server(host: str = TRACE_HOST, port: int = TRACE_PORT) -> None:
    global _SERVER_STARTED, _IS_OWNER
    if _SERVER_STARTED:
        return
    _SERVER_STARTED = True
    try:
        httpd = _TraceServer((host, port), _Handler)
    except OSError as exc:
        _IS_OWNER = False
        print(f"Voice trace using existing http://{host}:{port}/turns ({exc})", flush=True)
        return
    _IS_OWNER = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="voice-trace").start()
    print(f"Voice trace (RAM only) on http://{host}:{port}/turns", flush=True)


def fetch_turns(host: str = TRACE_HOST, port: int = TRACE_PORT) -> list[dict[str, Any]] | None:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/turns", timeout=1.2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        turns = payload.get("turns")
        return turns if isinstance(turns, list) else []
    return []
