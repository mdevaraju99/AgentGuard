from __future__ import annotations

import os
import subprocess
import time
import shutil
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel, Field

from desktop_agent.config import load_apps_config
from desktop_agent.tools.base import RiskLevel, Tool, ToolResult, VerificationResult


def _username() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def normalize_apps(raw: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    data = raw if raw is not None else load_apps_config()
    apps: dict[str, dict[str, Any]] = {}
    for app_id, spec in data.items():
        aliases = [a.lower() for a in spec.get("aliases", [])] + [app_id.lower()]
        executables = []
        for item in spec.get("executables", []):
            executables.append(item.replace("{username}", _username()))
        apps[app_id] = {
            "id": app_id,
            "display_name": spec.get("display_name", app_id),
            "aliases": aliases,
            "process_names": [p.lower() for p in spec.get("process_names", [])],
            "executables": executables,
            "path_commands": spec.get("path_commands", []),
        }
    return apps


def resolve_app_id(name: str, apps: dict[str, dict[str, Any]] | None = None) -> str:
    apps = apps or normalize_apps()
    key = name.strip().lower()
    if key in apps:
        return key
    for app_id, spec in apps.items():
        if key in spec["aliases"] or key == spec["display_name"].lower():
            return app_id
    known = ", ".join(sorted(apps))
    raise ValueError(f"Application '{name}' is not allowlisted. Known: {known}")


def running_processes(app: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    names = set(app["process_names"])
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pname = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pname in names:
            found.append({"pid": proc.info["pid"], "name": proc.info["name"]})
    return found


def is_installed(app: dict[str, Any]) -> tuple[bool, str | None]:
    for exe in app["executables"]:
        path = Path(exe)
        if path.exists():
            return True, str(path)
    for command in app["path_commands"]:
        found = shutil.which(command)
        if found:
            return True, found
    return False, None


def focus_app(app: dict[str, Any]) -> bool:
    """Bring an already-running app window to the front on Windows."""
    if os.name != "nt":
        return False
    display = str(app.get("display_name") or app.get("id") or "")
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"[void]$shell.AppActivate('{display.replace(chr(39), '')}')"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=False,
        )
        return True
    except Exception:
        return False


def launch_app(app: dict[str, Any], extra_args: list[str] | None = None) -> str:
    extra_args = extra_args or []
    for exe in app["executables"]:
        path = Path(exe)
        if path.exists():
            _popen([str(path), *extra_args])
            return str(path)
    for command in app["path_commands"]:
        _popen([command, *extra_args])
        return command
    raise FileNotFoundError(f"Could not find an executable for {app['id']}")


def _popen(argv: list[str]) -> None:
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


class AppNameArgs(BaseModel):
    app_name: str = Field(description="Application name, e.g. chrome, vscode, notepad, calculator, teams")


def build_application_tools() -> list[Tool]:
    apps = normalize_apps()

    def open_application(args: AppNameArgs) -> ToolResult:
        try:
            app = apps[resolve_app_id(args.app_name, apps)]
        except ValueError:
            from desktop_agent.tools.installed import find_installed

            hits = find_installed(args.app_name)
            exe = next((item.get("exe") for item in hits if item.get("exe")), None)
            if not exe:
                return ToolResult(
                    ok=False,
                    error=f"I could not find an installed app matching '{args.app_name}'.",
                    data={"query": args.app_name, "matches": hits},
                )
            _popen([exe])
            time.sleep(1.2)
            return ToolResult(
                ok=True,
                data={"app_id": args.app_name, "launched": exe, "from_inventory": True},
                evidence={"launched": exe},
            )
        already = running_processes(app)
        try:
            launched = launch_app(app)
        except FileNotFoundError:
            if app["id"] == "teams":
                try:
                    os.startfile("msteams:")  # type: ignore[attr-defined]
                    launched = "msteams:"
                except OSError as exc:
                    return ToolResult(ok=False, error=str(exc), data={"app_id": app["id"]})
            elif already:
                focus_app(app)
                return ToolResult(
                    ok=True,
                    data={"app_id": app["id"], "already_running": True, "focused": True, "processes": already},
                    evidence={"running": True, "process_count": len(already)},
                )
            else:
                return ToolResult(
                    ok=False,
                    error=f"Could not find {app['id']} on this PC.",
                    data={"app_id": app["id"]},
                )
        focus_app(app)
        time.sleep(1.0)
        found = running_processes(app) or already
        return ToolResult(
            ok=bool(found) or app["id"] == "teams",
            data={
                "app_id": app["id"],
                "launched": launched,
                "already_running": bool(already),
                "focused": True,
                "processes": found,
            },
            evidence={"running": bool(found), "process_count": len(found), "launched": launched},
            error=None if (found or app["id"] == "teams") else f"Launched {app['id']} but process not observed yet",
        )

    def close_application(args: AppNameArgs) -> ToolResult:
        app = apps[resolve_app_id(args.app_name, apps)]
        found = running_processes(app)
        terminated = []
        for item in found:
            try:
                psutil.Process(item["pid"]).terminate()
                terminated.append(item["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                return ToolResult(ok=False, error=str(exc), data={"app_id": app["id"]})
        remaining = []
        for pid in terminated:
            if not psutil.pid_exists(pid):
                continue
            try:
                remaining.append(psutil.Process(pid))
            except psutil.NoSuchProcess:
                continue
        if remaining:
            psutil.wait_procs(remaining, timeout=3)
        still = running_processes(app)
        return ToolResult(
            ok=not still,
            data={"app_id": app["id"], "terminated": terminated},
            evidence={"running": bool(still), "process_count": len(still)},
            error=None if not still else f"{app['id']} is still running",
        )

    def is_application_running(args: AppNameArgs) -> ToolResult:
        app = apps[resolve_app_id(args.app_name, apps)]
        found = running_processes(app)
        return ToolResult(
            ok=True,
            data={"app_id": app["id"], "running": bool(found), "processes": found},
            evidence={"running": bool(found), "process_count": len(found)},
        )

    def is_application_installed(args: AppNameArgs) -> ToolResult:
        from desktop_agent.tools.installed import find_installed

        inventory = find_installed(args.app_name)
        top = inventory[0] if inventory else None
        allowlisted = False
        location = None
        try:
            app = apps[resolve_app_id(args.app_name, apps)]
            allowlisted, location = is_installed(app)
        except ValueError:
            app = None
        installed = allowlisted or bool(top)
        return ToolResult(
            ok=True,
            data={
                "app_id": args.app_name,
                "installed": installed,
                "name": None if not top else top["name"],
                "version": None if not top else top["version"],
                "install_date": None if not top else top["install_date"],
                "location": location or (None if not top else top.get("install_location") or top.get("exe")),
                "running": bool(running_processes(app)) if app else False,
                "matches": inventory,
            },
            evidence={
                "installed": installed,
                "version": None if not top else top["version"],
                "install_date": None if not top else top["install_date"],
            },
        )

    def verify_installed(args: AppNameArgs, result: ToolResult) -> VerificationResult:
        installed = bool(result.data.get("installed"))
        return VerificationResult(
            verified="installed" in result.data,
            evidence={
                "installed": installed,
                "version": result.data.get("version"),
                "install_date": result.data.get("install_date"),
            },
            reason="inventory checked",
        )

    def verify_running(args: AppNameArgs, result: ToolResult) -> VerificationResult:
        if result.ok and str(result.data.get("launched") or "").startswith("msteams"):
            return VerificationResult(verified=True, evidence=result.evidence, reason="Teams protocol opened")
        app = apps[resolve_app_id(args.app_name, apps)]
        found = running_processes(app)
        running = bool(found)
        return VerificationResult(
            verified=running,
            evidence={"running": running, "processes": found},
            reason="process observed" if running else "process not observed",
        )

    def verify_closed(args: AppNameArgs, result: ToolResult) -> VerificationResult:
        app = apps[resolve_app_id(args.app_name, apps)]
        found = running_processes(app)
        return VerificationResult(
            verified=not found,
            evidence={"running": bool(found), "processes": found},
            reason="closed" if not found else "still running",
        )

    def verify_status(args: AppNameArgs, result: ToolResult) -> VerificationResult:
        app = apps[resolve_app_id(args.app_name, apps)]
        found = running_processes(app)
        reported = bool(result.data.get("running"))
        actual = bool(found)
        return VerificationResult(
            verified=reported == actual,
            evidence={"running": actual, "processes": found},
            reason="status matches" if reported == actual else "status mismatch",
        )

    return [
        Tool(
            name="open_application",
            description="Open a desktop application such as chrome, vscode, notepad, calculator, explorer, or teams.",
            parameters=AppNameArgs,
            risk_level=RiskLevel.SAFE,
            timeout_s=20,
            handler=open_application,
            verifier=verify_running,
        ),
        Tool(
            name="close_application",
            description="Close an allowlisted desktop application. Requires confirmation because it may discard unsaved work.",
            parameters=AppNameArgs,
            risk_level=RiskLevel.CONFIRM,
            timeout_s=15,
            handler=close_application,
            verifier=verify_closed,
        ),
        Tool(
            name="is_application_running",
            description="Check whether an allowlisted application currently has a running process.",
            parameters=AppNameArgs,
            risk_level=RiskLevel.SAFE,
            timeout_s=10,
            handler=is_application_running,
            verifier=verify_status,
        ),
        Tool(
            name="is_application_installed",
            description="Check whether an application is installed on this PC, including version and install date when Windows recorded them. Use this for 'is VS Code installed?', not is_application_running.",
            parameters=AppNameArgs,
            risk_level=RiskLevel.SAFE,
            timeout_s=10,
            handler=is_application_installed,
            verifier=verify_installed,
        ),
    ]
