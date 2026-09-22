from __future__ import annotations

import time
from pathlib import Path

import pytest

from desktop_agent.safety.paths import resolve_allowed_path
from desktop_agent.safety.sandbox import SandboxError, resolve_in_sandbox
from desktop_agent.tools.files import search_files
from desktop_agent.tools.registry import build_default_registry


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
