from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from desktop_agent.tools.base import Tool, ToolResult, VerificationResult

_UNINSTALL_PATHS = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _parse_install_date(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return text
    return text


def _read_uninstall_hive(root, path: str) -> list[dict[str, Any]]:
    import winreg

    found: list[dict[str, Any]] = []
    try:
        hive = winreg.OpenKey(root, path)
    except OSError:
        return found
    index = 0
    while True:
        try:
            subname = winreg.EnumKey(hive, index)
        except OSError:
            break
        index += 1
        try:
            sub = winreg.OpenKey(hive, subname)
        except OSError:
            continue
        data: dict[str, Any] = {}
        for field in (
            "DisplayName",
            "DisplayVersion",
            "Publisher",
            "InstallDate",
            "InstallLocation",
            "DisplayIcon",
            "UninstallString",
        ):
            try:
                value, _ = winreg.QueryValueEx(sub, field)
                data[field] = value
            except OSError:
                continue
        name = str(data.get("DisplayName") or "").strip()
        if not name:
            continue
        icon = str(data.get("DisplayIcon") or "").split(",")[0].strip().strip('"')
        location = str(data.get("InstallLocation") or "").strip().strip('"')
        found.append(
            {
                "name": name,
                "version": str(data.get("DisplayVersion") or "").strip() or None,
                "publisher": str(data.get("Publisher") or "").strip() or None,
                "install_date": _parse_install_date(data.get("InstallDate")),
                "install_location": location or None,
                "exe": icon if icon.lower().endswith(".exe") and Path(icon).exists() else None,
            }
        )
    return found


@lru_cache(maxsize=1)
def list_installed_programs() -> tuple[tuple[str, ...], ...]:
    """Cached snapshot. Values are tuples so the cache stays hashable-friendly."""
    import winreg

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in _UNINSTALL_PATHS:
            if hive == winreg.HKEY_CURRENT_USER and "WOW6432Node" in path:
                continue
            for item in _read_uninstall_hive(hive, path):
                key = item["name"].lower()
                if key in seen:
                    continue
                seen.add(key)
                rows.append(item)
    rows.sort(key=lambda item: item["name"].lower())
    packed = []
    for item in rows:
        packed.append(
            (
                item["name"],
                item.get("version") or "",
                item.get("publisher") or "",
                item.get("install_date") or "",
                item.get("install_location") or "",
                item.get("exe") or "",
            )
        )
    return tuple(packed)


def installed_dicts() -> list[dict[str, Any]]:
    rows = []
    for name, version, publisher, install_date, location, exe in list_installed_programs():
        rows.append(
            {
                "name": name,
                "version": version or None,
                "publisher": publisher or None,
                "install_date": install_date or None,
                "install_location": location or None,
                "exe": exe or None,
            }
        )
    return rows


def score_app(name: str, query: str) -> int:
    q = query.lower().strip()
    n = name.lower()
    compact_q = _compact(q)
    compact_n = _compact(n)
    score = 0
    if q == n:
        score = 100
    elif q in n or compact_q and compact_q in compact_n:
        score = 80
    aliases = {
        "vscode": "visual studio code",
        "vs code": "visual studio code",
        "code": "visual studio code",
        "teams": "microsoft teams",
        "ms teams": "microsoft teams",
        "chrome": "google chrome",
    }
    mapped = aliases.get(q, q)
    if mapped in n or _compact(mapped) in compact_n:
        score = max(score, 90)
    if not score:
        return 0
    noise = ("add-in", "addin", "redistributable", "runtime", "helper", "sdk", "minimum")
    if any(word in n for word in noise) and not any(word in q for word in noise):
        score -= 45
    if n.startswith(mapped) or n == mapped:
        score += 5
    return score


def find_installed(query: str, limit: int = 8) -> list[dict[str, Any]]:
    scored = []
    for item in installed_dicts():
        value = score_app(item["name"], query)
        if value:
            scored.append((value, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
    hits = [item for _, item in scored[:limit]]
    return _merge_allowlisted(query, hits)


def _merge_allowlisted(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from desktop_agent.tools.applications import is_installed, normalize_apps, resolve_app_id

        app = normalize_apps()[resolve_app_id(query)]
        present, location = is_installed(app)
    except (ValueError, Exception):
        return hits
    if not present:
        return hits
    row = {
        "name": app["display_name"],
        "version": None,
        "publisher": None,
        "install_date": None,
        "install_location": location,
        "exe": location,
    }
    display = _compact(app["display_name"])
    for item in hits:
        noisy = any(word in item["name"].lower() for word in ("add-in", "addin", "redistributable"))
        if noisy:
            continue
        compact_hit = _compact(item["name"])
        if display in compact_hit or compact_hit in display:
            row["version"] = item.get("version") or row["version"]
            row["install_date"] = item.get("install_date") or row["install_date"]
            row["publisher"] = item.get("publisher") or row["publisher"]
            if item.get("install_location"):
                row["install_location"] = item["install_location"]
            break
    others = [item for item in hits if item["name"].lower() != row["name"].lower()]
    return [row, *others]


class AppQueryArgs(BaseModel):
    query: str = Field(description="Application name, e.g. VS Code, Teams, Chrome")


def build_installed_tools() -> list[Tool]:
    def lookup_installed_application(args: AppQueryArgs) -> ToolResult:
        hits = find_installed(args.query)
        if not hits:
            return ToolResult(
                ok=True,
                data={"query": args.query, "installed": False, "matches": []},
                evidence={"installed": False, "count": 0},
            )
        top = hits[0]
        return ToolResult(
            ok=True,
            data={
                "query": args.query,
                "installed": True,
                "name": top["name"],
                "version": top["version"],
                "install_date": top["install_date"],
                "install_location": top["install_location"],
                "matches": hits,
            },
            evidence={
                "installed": True,
                "name": top["name"],
                "version": top["version"],
                "install_date": top["install_date"],
            },
        )

    def verify(_args: AppQueryArgs, result: ToolResult) -> VerificationResult:
        return VerificationResult(
            verified=True,
            evidence=result.evidence,
            reason="inventory checked",
        )

    return [
        Tool(
            name="lookup_installed_application",
            description=(
                "Look up whether an application is installed on this Windows PC using the uninstall registry. "
                "Returns display name, version, and install date when Windows recorded them. "
                "Use this for questions like 'is VS Code installed?'"
            ),
            parameters=AppQueryArgs,
            timeout_s=20,
            handler=lookup_installed_application,
            verifier=verify,
        )
    ]
