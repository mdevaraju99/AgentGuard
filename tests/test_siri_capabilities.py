from __future__ import annotations

import json

from desktop_agent.agent.loop import DesktopAgent
from desktop_agent.agent.state import TaskOutcome
from desktop_agent.llm.provider import LLMResponse, ScriptedLLM, ToolCall
from desktop_agent.tools.files import _extract_folder_hint, search_files
from desktop_agent.tools.installed import find_installed
from desktop_agent.tools.registry import ToolRegistry, build_default_registry
from desktop_agent.tools.teams import build_teams_tools


def _registry_with_teams() -> ToolRegistry:
    registry = build_default_registry()
    for tool in build_teams_tools():
        registry.register(tool)
    return registry


def _call(oid: str, name: str, arguments: dict) -> ToolCall:
    return ToolCall(id=oid, name=name, arguments=json.dumps(arguments))


def test_extract_folder_hint_from_natural_language():
    rest, hint = _extract_folder_hint(
        "in a folder called desktopagent+langfuse, there's a file pytest.ini open it",
        "",
    )
    assert "desktopagent" in hint.lower().replace(" ", "")
    assert "pytest.ini" in rest.lower()


def test_search_file_inside_named_folder(sandbox):
    root, _ = sandbox
    docs = root.parent / "userhome" / "Documents"
    folder = docs / "Desktop Agent + Langfuse tools"
    folder.mkdir()
    target = folder / "pytest.ini"
    target.write_text("[pytest]\n", encoding="utf-8")
    (docs / "other.ini").write_text("nope", encoding="utf-8")
    payload = search_files(
        "in a folder called desktopagent+langfuse, there's a file pytest.ini open it"
    )
    assert payload["count"] >= 1
    assert payload["best_match"]["name"] == "pytest.ini"
    assert "Desktop Agent" in payload["best_match"]["absolute_path"]


def test_open_named_folder_file(sandbox, monkeypatch):
    root, _ = sandbox
    opened: list = []
    monkeypatch.setattr("desktop_agent.tools.files._open_path", lambda path: opened.append(path))
    folder = (root.parent / "userhome" / "Documents" / "Desktop Agent + Langfuse tools")
    folder.mkdir()
    target = folder / "pytest.ini"
    target.write_text("[pytest]\n", encoding="utf-8")
    registry = build_default_registry()
    search = registry.get("search_files")
    found = search.run(search.parse_args({"query": "pytest.ini", "folder": "desktopagent+langfuse"}))
    path = found.data["best_match"]["absolute_path"]
    opener = registry.get("open_file")
    result = opener.run(opener.parse_args({"path": path}))
    assert result.ok
    assert opened and opened[0].name == "pytest.ini"


def test_lookup_installed_reports_version_and_date(monkeypatch):
    monkeypatch.setattr(
        "desktop_agent.tools.installed.installed_dicts",
        lambda: [
            {
                "name": "Microsoft Visual Studio Code",
                "version": "1.93.1",
                "publisher": "Microsoft Corporation",
                "install_date": "2024-08-12",
                "install_location": r"C:\Users\me\AppData\Local\Programs\Microsoft VS Code",
                "exe": r"C:\Users\me\AppData\Local\Programs\Microsoft VS Code\Code.exe",
            }
        ],
    )
    hits = find_installed("vs code")
    assert hits
    assert hits[0]["version"] == "1.93.1"
    assert hits[0]["install_date"] == "2024-08-12"
    registry = build_default_registry()
    tool = registry.get("lookup_installed_application")
    result = tool.run(tool.parse_args({"query": "VS Code"}))
    assert result.ok
    assert result.data["installed"] is True
    assert result.data["version"] == "1.93.1"
    assert result.data["install_date"] == "2024-08-12"


def test_teams_asks_for_message_then_sends(sandbox, monkeypatch):
    sent: list[tuple[str, str, str | None]] = []

    def fake_send(recipient: str, message: str, email: str | None = None) -> dict:
        sent.append((recipient, message, email))
        return {"delivered": True, "method": "microsoft_graph", "email": email}

    monkeypatch.setattr("desktop_agent.tools.teams.compose_and_send", fake_send)
    _, settings = sandbox
    llm = ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[_call("1", "send_teams_message", {"recipient": "Janapati Thanusree"})]
            ),
            LLMResponse(content=""),
            LLMResponse(content="Sent Whatsup to Janapati Thanusree in Teams."),
        ]
    )
    agent = DesktopAgent(llm=llm, registry=_registry_with_teams(), settings=settings)
    first = agent.run("Send a Teams message to Janapati Thanusree", session_id="teams")
    assert first.task_outcome == TaskOutcome.AWAITING_INPUT
    assert "message" in first.final_response.lower()
    assert sent == []
    second = agent.run("Whatsup", session_id="teams")
    assert sent == [("Janapati Thanusree", "Whatsup", None)]
    assert second.task_outcome == TaskOutcome.ACHIEVED
    assert agent._pending.get("teams") is None


def test_send_teams_message_with_name_and_text_does_not_use_outlook(monkeypatch):
    sent: list[tuple[str, str]] = []

    def fake_send(recipient: str, message: str, email: str | None = None) -> dict:
        sent.append((recipient, message))
        return {"delivered": True, "method": "microsoft_graph"}

    monkeypatch.setattr("desktop_agent.tools.teams.compose_and_send", fake_send)

    def outlook_should_not_run(*_args, **_kwargs):
        raise AssertionError("Outlook must not block Teams send")

    monkeypatch.setattr("desktop_agent.tools.people.query_outlook", outlook_should_not_run)
    registry = _registry_with_teams()
    tool = registry.get("send_teams_message")
    result = tool.run(
        tool.parse_args({"recipient": "Janapati Thanusree", "message": "Whatsup"})
    )
    assert result.ok is True
    assert result.data["delivered"] is True
    assert sent == [("Janapati Thanusree", "Whatsup")]


def test_send_teams_message_reports_not_delivered_on_failure(monkeypatch):
    def boom(recipient: str, message: str, email: str | None = None) -> dict:
        raise RuntimeError("Could not activate the Microsoft Teams window.")

    monkeypatch.setattr("desktop_agent.tools.teams.compose_and_send", boom)
    registry = _registry_with_teams()
    tool = registry.get("send_teams_message")
    result = tool.run(tool.parse_args({"recipient": "Janapati Thanusree", "message": "Whatsup"}))
    assert result.ok is False
    assert result.data["delivered"] is False
    verify = tool.verify(tool.parse_args({"recipient": "Janapati Thanusree", "message": "Whatsup"}), result)
    assert verify.verified is False

