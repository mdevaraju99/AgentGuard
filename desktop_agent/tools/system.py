from __future__ import annotations

import os
import platform
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from pydantic import BaseModel, Field

from desktop_agent.safety.sandbox import sandbox_root
from desktop_agent.tools.base import Tool, ToolResult, VerificationResult


class EmptyArgs(BaseModel):
    pass


class SettingsPageArgs(BaseModel):
    page: str = Field(
        default="",
        description="Optional Windows Settings page: wifi, bluetooth, display, battery, sound, about. Empty opens Settings home.",
    )


def _bytes_gb(value: int) -> float:
    return round(value / (1024**3), 2)


def battery_snapshot() -> dict:
    info = psutil.sensors_battery()
    if info is None:
        return {
            "has_battery": False,
            "percent": None,
            "plugged_in": True,
            "status": "No battery reported (desktop PC or battery sensor unavailable).",
        }
    remaining = None
    if info.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN, -1):
        remaining = str(timedelta(seconds=int(info.secsleft)))
    plugged = bool(info.power_plugged)
    if plugged:
        status = f"Charging / plugged in at {int(info.percent)}%."
    else:
        status = f"On battery at {int(info.percent)}%."
        if remaining:
            status += f" About {remaining} remaining."
    return {
        "has_battery": True,
        "percent": int(info.percent),
        "plugged_in": plugged,
        "time_remaining": remaining,
        "status": status,
    }


def system_snapshot() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    boot = datetime.fromtimestamp(psutil.boot_time())
    battery = battery_snapshot()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.4),
        "cpu_count": psutil.cpu_count(logical=True),
        "memory_percent": vm.percent,
        "memory_used_gb": _bytes_gb(vm.used),
        "memory_total_gb": _bytes_gb(vm.total),
        "disk_percent": disk.percent,
        "disk_free_gb": _bytes_gb(disk.free),
        "disk_total_gb": _bytes_gb(disk.total),
        "uptime": str(datetime.now() - boot).split(".")[0],
        "booted_at": boot.isoformat(timespec="seconds"),
        "battery": battery,
    }


def device_snapshot() -> dict:
    return {
        "hostname": socket.gethostname(),
        "username": os.environ.get("USERNAME") or os.environ.get("USER"),
        "os": f"{platform.system()} {platform.release()}",
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }


def network_snapshot() -> dict:
    addrs = []
    stats = psutil.net_if_stats()
    for name, items in psutil.net_if_addrs().items():
        if name.lower().startswith(("loopback", "isatap", "teredo")):
            continue
        st = stats.get(name)
        if st is not None and not st.isup:
            continue
        ipv4 = [a.address for a in items if a.family == socket.AF_INET]
        if not ipv4:
            continue
        addrs.append({"interface": name, "ipv4": ipv4, "up": True if st is None else bool(st.isup)})
    return {"hostname": socket.gethostname(), "interfaces": addrs[:8]}


def top_processes(limit: int = 8) -> list[dict]:
    tracked = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            proc.cpu_percent(None)
            tracked.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.15)
    rows = []
    for proc in tracked:
        try:
            mem = proc.memory_info()
            rows.append(
                {
                    "pid": proc.pid,
                    "name": proc.name(),
                    "cpu_percent": round(proc.cpu_percent() or 0.0, 1),
                    "memory_mb": round(mem.rss / (1024**2), 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    rows.sort(key=lambda item: (-item["cpu_percent"], -item["memory_mb"]))
    return rows[:limit]


SETTINGS_PAGES = {
    "wifi": "ms-settings:network-wifi",
    "network": "ms-settings:network",
    "bluetooth": "ms-settings:bluetooth",
    "display": "ms-settings:display",
    "battery": "ms-settings:batterysaver",
    "sound": "ms-settings:sound",
    "about": "ms-settings:about",
    "update": "ms-settings:windowsupdate",
    "apps": "ms-settings:appsfeatures",
}


def _screenshot_path() -> Path:
    folder = sandbox_root() / "output" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return folder / f"screenshot-{stamp}.png"


def take_screenshot() -> Path:
    dest = _screenshot_path()
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); "
        f"$bmp.Save('{str(dest).replace(chr(39), chr(39)+chr(39))}'); "
        "$g.Dispose(); $bmp.Dispose();"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0 or not dest.exists():
        err = (completed.stderr or completed.stdout or "screenshot failed").strip()
        raise RuntimeError(err)
    return dest


def lock_workstation() -> None:
    if os.name != "nt":
        raise RuntimeError("Lock is only supported on Windows.")
    import ctypes

    if not ctypes.windll.user32.LockWorkStation():
        raise RuntimeError("Windows refused to lock the workstation.")


def build_system_tools() -> list[Tool]:
    def get_battery_status(_args: EmptyArgs) -> ToolResult:
        data = battery_snapshot()
        return ToolResult(ok=True, data=data, evidence={"percent": data.get("percent"), "has_battery": data["has_battery"]})

    def get_system_status(_args: EmptyArgs) -> ToolResult:
        data = system_snapshot()
        return ToolResult(
            ok=True,
            data=data,
            evidence={"cpu_percent": data["cpu_percent"], "memory_percent": data["memory_percent"]},
        )

    def get_device_info(_args: EmptyArgs) -> ToolResult:
        data = device_snapshot()
        return ToolResult(ok=True, data=data, evidence={"hostname": data["hostname"]})

    def get_network_status(_args: EmptyArgs) -> ToolResult:
        data = network_snapshot()
        return ToolResult(ok=True, data=data, evidence={"count": len(data["interfaces"])})

    def list_top_processes(_args: EmptyArgs) -> ToolResult:
        rows = top_processes()
        return ToolResult(ok=True, data={"processes": rows, "count": len(rows)}, evidence={"count": len(rows)})

    def tool_take_screenshot(_args: EmptyArgs) -> ToolResult:
        path = take_screenshot()
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            opened = True
        except OSError:
            opened = False
        shown = str(path)
        return ToolResult(
            ok=True,
            data={"path": shown, "opened": opened, "bytes": path.stat().st_size},
            evidence={"path": shown, "exists": path.exists()},
        )

    def tool_lock(_args: EmptyArgs) -> ToolResult:
        lock_workstation()
        return ToolResult(ok=True, data={"locked": True}, evidence={"locked": True})

    def open_windows_settings(args: SettingsPageArgs) -> ToolResult:
        key = (args.page or "").strip().lower()
        target = SETTINGS_PAGES.get(key, "ms-settings:")
        os.startfile(target)  # type: ignore[attr-defined]
        return ToolResult(ok=True, data={"opened": target, "page": key or "home"}, evidence={"opened": target})

    def verify_ok(_args: BaseModel, result: ToolResult) -> VerificationResult:
        return VerificationResult(verified=result.ok, evidence=result.evidence, reason="ok" if result.ok else result.error or "failed")

    def verify_shot(_args: EmptyArgs, result: ToolResult) -> VerificationResult:
        path = Path(str(result.data.get("path") or ""))
        exists = path.exists()
        return VerificationResult(verified=exists, evidence={"exists": exists}, reason="saved" if exists else "missing")

    return [
        Tool(
            name="get_battery_status",
            description="Get this PC's battery percent, plugged-in state, and remaining time. Use for 'battery', 'how much charge'.",
            parameters=EmptyArgs,
            timeout_s=10,
            handler=get_battery_status,
            verifier=verify_ok,
        ),
        Tool(
            name="get_system_status",
            description="CPU, RAM, disk, uptime, and battery snapshot. Use for 'how is my PC', 'why is it slow'.",
            parameters=EmptyArgs,
            timeout_s=15,
            handler=get_system_status,
            verifier=verify_ok,
        ),
        Tool(
            name="get_device_info",
            description="Hostname, Windows version, username, machine. Use for 'what PC is this', 'who am I logged in as'.",
            parameters=EmptyArgs,
            timeout_s=10,
            handler=get_device_info,
            verifier=verify_ok,
        ),
        Tool(
            name="get_network_status",
            description="Active network interfaces and IPv4 addresses. Use for 'what's my IP', 'am I on wifi'.",
            parameters=EmptyArgs,
            timeout_s=10,
            handler=get_network_status,
            verifier=verify_ok,
        ),
        Tool(
            name="list_top_processes",
            description="List processes using the most CPU and memory right now.",
            parameters=EmptyArgs,
            timeout_s=15,
            handler=list_top_processes,
            verifier=verify_ok,
        ),
        Tool(
            name="take_screenshot",
            description="Capture the screen and save/open a PNG under AI-Agent-Demo/output/screenshots.",
            parameters=EmptyArgs,
            timeout_s=25,
            handler=tool_take_screenshot,
            verifier=verify_shot,
        ),
        Tool(
            name="lock_workstation",
            description="Lock this Windows PC (Win+L). Only when the user clearly asked to lock the computer.",
            parameters=EmptyArgs,
            timeout_s=10,
            handler=tool_lock,
            verifier=verify_ok,
        ),
        Tool(
            name="open_windows_settings",
            description="Open Windows Settings. Optional page=wifi|bluetooth|display|battery|sound|about|update|apps.",
            parameters=SettingsPageArgs,
            timeout_s=10,
            handler=open_windows_settings,
            verifier=verify_ok,
        ),
    ]
