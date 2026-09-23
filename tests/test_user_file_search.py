from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from desktop_agent.safety.paths import resolve_allowed_path
from desktop_agent.safety.sandbox import SandboxError, resolve_in_sandbox
from desktop_agent.tools.files import parse_time_window, search_files
from desktop_agent.tools.registry import build_default_registry


def _stamp(path: Path, when: datetime) -> None:
    path.write_bytes(b"x")
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def _user_dirs(sandbox_root: Path) -> dict[str, Path]:
    home = sandbox_root.parent / "userhome"
    return {
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "desktop": home / "Desktop",
    }


def test_search_resume_in_documents(sandbox):
    root, _ = sandbox
    docs = _user_dirs(root)["documents"]
    resume = docs / "Megha SD Resume.docx"
    resume.write_bytes(b"resume")
    payload = search_files("megha s d resume")
    paths = [item["absolute_path"] for item in payload["files"]]
    assert str(resume.resolve()) in paths
    assert payload["best_match"] is not None
    assert "Resume" in payload["best_match"]["name"]


def test_search_downloads_and_desktop(sandbox):
    root, _ = sandbox
    dirs = _user_dirs(root)
    invoice = dirs["downloads"] / "invoice_march.pdf"
    notes = dirs["desktop"] / "todo_notes.txt"
    invoice.write_bytes(b"%PDF")
    notes.write_text("buy milk", encoding="utf-8")
    down = search_files("invoice", directory="downloads")
    assert any(item["name"] == "invoice_march.pdf" for item in down["files"])
    desk = search_files("todo notes", directory="desktop")
    assert any(item["name"] == "todo_notes.txt" for item in desk["files"])


def test_latest_document_ignores_word_lock_file(sandbox):
    root, _ = sandbox
    down = _user_dirs(root)["downloads"]
    real = down / "AgentGuard_Part1_Manager_Demo_Walkthrough.docx"
    lock = down / "~$entGuard_Part1_Manager_Demo_Walkthrough.docx"
    clock = datetime(2026, 9, 23, 16, 0, 0)
    _stamp(real, datetime(2026, 9, 23, 12, 26))
    _stamp(lock, datetime(2026, 9, 23, 15, 58))
    payload = search_files("see the latest document", now=clock)
    assert payload["best_match"]["name"] == real.name


def test_latest_document_skips_newer_sandbox_demo(sandbox):
    root, _ = sandbox
    down = _user_dirs(root)["downloads"] / "AgentGuard_Part1_Manager_Demo_Walkthrough.docx"
    demo = root / "documents" / "readme_demo.docx"
    demo.parent.mkdir(exist_ok=True)
    clock = datetime(2026, 9, 23, 16, 0, 0)
    _stamp(down, datetime(2026, 9, 23, 12, 26))
    _stamp(demo, datetime(2026, 9, 23, 15, 50))
    payload = search_files("see the latest document and summarize it", now=clock)
    assert payload["best_match"] is not None
    assert payload["best_match"]["name"] == down.name
    assert "readme_demo" not in payload["best_match"]["name"].lower()


def test_latest_doc_in_downloads_is_newest_not_named_latest(sandbox):
    root, _ = sandbox
    down = _user_dirs(root)["downloads"]
    named_latest = down / "Resume of Megha S D (1) latest.pdf"
    newest_doc = down / "AgentGuard_Part1_Manager_Demo_Walkthrough.docx"
    named_latest.write_bytes(b"old resume")
    newest_doc.write_bytes(b"new walkthrough")
    now = time.time()
    import os

    os.utime(named_latest, (now - 86_400, now - 86_400))
    os.utime(newest_doc, (now, now))
    payload = search_files("my latest doc in downloads")
    assert payload["prefer_latest"] is True
    assert payload["best_match"]["name"] == newest_doc.name
    again = search_files("no the document i have downloaded recently")
    assert again["best_match"]["name"] == newest_doc.name


def test_parse_week_month_windows():
    clock = datetime(2026, 9, 23, 15, 0, 0)
    last_week = parse_time_window("last week", clock)
    assert last_week["start"].date().isoformat() == "2026-09-14"
    assert last_week["end"].date().isoformat() == "2026-09-20"
    this_week = parse_time_window("this week", clock)
    assert this_week["start"].date().isoformat() == "2026-09-21"
    last_month = parse_time_window("last month", clock)
    assert last_month["start"].date().isoformat() == "2026-08-01"
    assert last_month["end"].date().isoformat() == "2026-08-31"


def test_last_week_excludes_this_week_and_older_month(sandbox):
    root, _ = sandbox
    down = _user_dirs(root)["downloads"]
    clock = datetime(2026, 9, 23, 15, 0, 0)
    this_week = down / "AgentGuard_Part1_Manager_Demo_Walkthrough.docx"
    last_week = down / "Desktop Agent and LLM BRD.docx"
    older = down / "Recording 2026-09-09 141116.mp4"
    last_month = down / "August notes.docx"
    _stamp(this_week, datetime(2026, 9, 23, 12, 26))
    _stamp(last_week, datetime(2026, 9, 17, 10, 11))
    _stamp(older, datetime(2026, 9, 9, 14, 11))
    _stamp(last_month, datetime(2026, 8, 25, 13, 0))
    payload = search_files(
        "list the last week documents that i have downloaded",
        now=clock,
    )
    names = [item["name"] for item in payload["files"]]
    assert payload["time_window"]["label"] == "last_week"
    assert last_week.name in names
    assert this_week.name not in names
    assert older.name not in names
    assert last_month.name not in names


def test_last_month_and_specific_date(sandbox):
    root, _ = sandbox
    down = _user_dirs(root)["downloads"]
    clock = datetime(2026, 9, 23, 15, 0, 0)
    august = down / "August notes.docx"
    sept = down / "Desktop Agent and LLM BRD.docx"
    _stamp(august, datetime(2026, 8, 25, 13, 0))
    _stamp(sept, datetime(2026, 9, 17, 10, 11))
    month = search_files("documents I downloaded last month", now=clock)
    month_names = [item["name"] for item in month["files"]]
    assert month["time_window"]["label"] == "last_month"
    assert august.name in month_names
    assert sept.name not in month_names
    dated = search_files("documents downloaded on 17 September 2026", now=clock)
    assert dated["time_window"]["label"] == "on_date"
    assert [item["name"] for item in dated["files"]] == [sept.name]


def test_latest_resume_picks_newest(sandbox):
    root, _ = sandbox
    dirs = _user_dirs(root)
    older = dirs["documents"] / "Megha_SD_Resume_2024.docx"
    newer = dirs["downloads"] / "Resume of Megha S D (1)lastest.pdf"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    now = time.time()
    import os

    os.utime(older, (now - 86_400, now - 86_400))
    os.utime(newer, (now, now))
    payload = search_files("open my latest resume")
    assert payload["prefer_latest"] is True
    assert payload["needs_disambiguation"] is False
    assert "Megha" in payload["best_match"]["name"]


def test_ambiguous_matches_are_not_auto_chosen(sandbox):
    root, _ = sandbox
    dirs = _user_dirs(root)
    (dirs["documents"] / "quarterly_report.txt").write_text("a", encoding="utf-8")
    (dirs["downloads"] / "quarterly_report.txt").write_text("b", encoding="utf-8")
    payload = search_files("quarterly_report")
    assert payload["count"] >= 2
    assert payload["needs_disambiguation"] is True
    assert payload["best_match"] is None


def test_open_file_from_approved_user_root(sandbox, monkeypatch):
    root, _ = sandbox
    opened: list[Path] = []
    monkeypatch.setattr("desktop_agent.tools.files._open_path", lambda path: opened.append(path))
    target = _user_dirs(root)["documents"] / "cv.txt"
    target.write_text("cv", encoding="utf-8")
    registry = build_default_registry()
    tool = registry.get("open_file")
    result = tool.run(tool.parse_args({"path": str(target)}))
    assert result.ok
    assert opened and opened[0].resolve() == target.resolve()
    verify = tool.verify(tool.parse_args({"path": str(target)}), result)
    assert verify.verified


def test_create_file_cannot_write_to_documents(sandbox):
    root, _ = sandbox
    outside = _user_dirs(root)["documents"] / "hack.txt"
    with pytest.raises(SandboxError):
        resolve_in_sandbox(str(outside))
    registry = build_default_registry()
    create = registry.get("create_file")
    result = create.run(create.parse_args({"path": str(outside), "content": "nope"}))
    assert result.ok is False
    assert not outside.exists()


def test_system_paths_remain_denied(sandbox):
    with pytest.raises(SandboxError):
        resolve_allowed_path(r"C:\Windows\System32")


def test_pictures_not_enabled_by_default(sandbox):
    payload = search_files("vacation", directory="pictures")
    assert payload.get("error")
    assert payload["files"] == []
