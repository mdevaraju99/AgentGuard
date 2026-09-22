from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel, Field

from desktop_agent.safety.paths import get_file_access_config
from desktop_agent.tools.base import Tool, ToolResult, VerificationResult
from desktop_agent.tools.system import _bytes_gb, device_snapshot


class EmptyArgs(BaseModel):
    pass


class FileInventoryArgs(BaseModel):
    scope: str = Field(
        default="all",
        description="user = Documents/Downloads/Desktop/sandbox only; volumes = each local drive; all = both.",
    )


DRIVE_TYPES = {0: "unknown", 1: "no_root", 2: "removable", 3: "local", 4: "network", 5: "cdrom", 6: "ram"}

SYSTEM_SKIP_DIRS = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "$recycle.bin",
    "system volume information",
    "winsxs",
    "windows.old",
    "perflogs",
    "appdata",
    "$windows.~bt",
    "$windows.~ws",
    "documents and settings",
    "intel",
    "amd",
    "nvidia",
    "msocache",
}

POC_HINTS = ("neo4j", "langfuse", "qdrant", "poc", "mlflow", "ragas")

PODMAN_HOST_ROOT = Path.home() / ".local" / "share" / "containers"


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def run_command(argv: list[str], *, timeout: float = 20) -> tuple[int, str, str]:
    completed = subprocess.run(argv, capture_output=True, timeout=timeout)
    stdout = _decode(completed.stdout)
    stderr = _decode(completed.stderr)
    return completed.returncode, stdout, stderr


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe") or (len(raw) >= 4 and raw[1:2] == b"\x00"):
        return raw.decode("utf-16-le", errors="replace").replace("\x00", "")
    return raw.decode("utf-8", errors="replace")


def powershell_json(script: str, *, timeout: float = 25) -> Any:
    code, out, err = run_command(
        ["powershell", "-NoProfile", "-Command", script],
        timeout=timeout,
    )
    if code != 0:
        raise RuntimeError((err or out or f"powershell exit {code}").strip())
    text = out.strip()
    if not text or text == "null":
        return None
    return json.loads(text)


def _cim_snapshot() -> dict[str, Any]:
    script = r"""
$cs = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, TotalPhysicalMemory, NumberOfProcessors, NumberOfLogicalProcessors, UserName, SystemType
$cpu = Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture, BuildNumber
$gpu = Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion
$bios = Get-CimInstance Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, SerialNumber
$disks = Get-CimInstance Win32_DiskDrive | Select-Object Model, InterfaceType, MediaType, Size
$mem = Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer, Capacity, Speed, PartNumber
[pscustomobject]@{
  computer = $cs
  processor = $cpu
  os = $os
  gpu = $gpu
  bios = $bios
  disks = $disks
  memory_modules = $mem
} | ConvertTo-Json -Compress -Depth 5
"""
    return powershell_json(script, timeout=30) or {}


def pc_configuration_snapshot() -> dict[str, Any]:
    device = device_snapshot()
    vm = psutil.virtual_memory()
    hardware: dict[str, Any] = {
        "hostname": device["hostname"],
        "username": device["username"],
        "os": device["os"],
        "os_version": device["version"],
        "architecture": platform.machine(),
        "processor_reported": device["processor"],
        "logical_cpus": psutil.cpu_count(logical=True),
        "physical_cpus": psutil.cpu_count(logical=False),
        "ram_total_gb": _bytes_gb(vm.total),
        "ram_available_gb": _bytes_gb(vm.available),
    }
    notes: list[str] = []
    try:
        cim = _cim_snapshot()
    except Exception as exc:
        notes.append(f"WMI details unavailable: {exc}")
        cim = {}

    computer = (cim.get("computer") or {}) if isinstance(cim.get("computer"), dict) else {}
    processors = _as_list(cim.get("processor"))
    osinfo = cim.get("os") if isinstance(cim.get("os"), dict) else {}
    gpus = _as_list(cim.get("gpu"))
    bios = cim.get("bios") if isinstance(cim.get("bios"), dict) else {}
    disks = _as_list(cim.get("disks"))
    modules = _as_list(cim.get("memory_modules"))

    hardware.update(
        {
            "manufacturer": computer.get("Manufacturer"),
            "model": computer.get("Model"),
            "system_type": computer.get("SystemType"),
            "os_caption": osinfo.get("Caption") or hardware["os"],
            "os_architecture": osinfo.get("OSArchitecture"),
            "os_build": osinfo.get("BuildNumber"),
            "bios_vendor": bios.get("Manufacturer"),
            "bios_version": bios.get("SMBIOSBIOSVersion"),
            "bios_serial": bios.get("SerialNumber"),
            "cpu": [
                {
                    "name": item.get("Name"),
                    "cores": item.get("NumberOfCores"),
                    "threads": item.get("NumberOfLogicalProcessors"),
                    "max_clock_mhz": item.get("MaxClockSpeed"),
                }
                for item in processors
                if isinstance(item, dict)
            ],
            "gpu": [
                {
                    "name": item.get("Name"),
                    "adapter_ram_gb": _bytes_gb(int(item.get("AdapterRAM") or 0)),
                    "driver": item.get("DriverVersion"),
                }
                for item in gpus
                if isinstance(item, dict) and item.get("Name")
            ],
            "physical_disks": [
                {
                    "model": item.get("Model"),
                    "interface": item.get("InterfaceType"),
                    "media": item.get("MediaType"),
                    "size_gb": _bytes_gb(int(item.get("Size") or 0)),
                }
                for item in disks
                if isinstance(item, dict)
            ],
            "memory_modules": [
                {
                    "manufacturer": item.get("Manufacturer"),
                    "size_gb": _bytes_gb(int(item.get("Capacity") or 0)),
                    "speed_mhz": item.get("Speed"),
                    "part_number": item.get("PartNumber"),
                }
                for item in modules
                if isinstance(item, dict)
            ],
        }
    )
    cpu_name = hardware["cpu"][0]["name"] if hardware["cpu"] else hardware["processor_reported"]
    gpu_name = hardware["gpu"][0]["name"] if hardware["gpu"] else "unknown GPU"
    disk_bits = ", ".join(
        f"{d['model']} {d['size_gb']} GB" for d in hardware["physical_disks"] if d.get("model")
    ) or "unknown disk"
    model = str(hardware.get("model") or hardware["hostname"])
    manufacturer = str(hardware.get("manufacturer") or "").strip()
    title = model if manufacturer and model.lower().startswith(manufacturer.lower()) else f"{manufacturer} {model}".strip()
    summary = (
        title
        + f"; {hardware.get('os_caption') or hardware['os']}"
        + (f" {hardware['os_architecture']}" if hardware.get("os_architecture") else "")
        + f"; {cpu_name}; {hardware['ram_total_gb']} GB RAM; {gpu_name}; {disk_bits}."
    )
    hardware["summary"] = " ".join(summary.split())
    hardware["notes"] = notes
    return hardware


def _logical_disks_cim() -> list[dict[str, Any]]:
    script = (
        "Get-CimInstance Win32_LogicalDisk | "
        "Select-Object DeviceID, VolumeName, FileSystem, DriveType, Size, FreeSpace | "
        "ConvertTo-Json -Compress"
    )
    try:
        return [item for item in _as_list(powershell_json(script, timeout=20)) if isinstance(item, dict)]
    except Exception:
        return []


def storage_overview() -> dict[str, Any]:
    cim_rows = {str(row.get("DeviceID") or "").upper(): row for row in _logical_disks_cim()}
    volumes = []
    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        device = (part.device or mount).rstrip("\\/")
        key = (device[:2] if len(device) >= 2 and device[1] == ":" else device).upper()
        extra = cim_rows.get(key, {})
        drive_type = int(extra.get("DriveType") or (3 if "fixed" in (part.opts or "") else 0))
        if drive_type in (1, 5):
            continue
        try:
            usage = psutil.disk_usage(mount)
        except OSError:
            continue
        if usage.total <= 0:
            continue
        volumes.append(
            {
                "device": key or device,
                "label": extra.get("VolumeName") or "",
                "file_system": extra.get("FileSystem") or part.fstype,
                "kind": DRIVE_TYPES.get(drive_type, part.opts),
                "total_gb": _bytes_gb(usage.total),
                "used_gb": _bytes_gb(usage.used),
                "free_gb": _bytes_gb(usage.free),
                "percent_used": round(usage.percent, 1),
            }
        )
    local = [row for row in volumes if _is_local_volume(row)]
    if not local:
        local = volumes
    total_gb = round(sum(v["total_gb"] for v in local), 2)
    free_gb = round(sum(v["free_gb"] for v in local), 2)
    used_gb = round(sum(v["used_gb"] for v in local), 2)
    bits = [
        f"{v['device']} {('(' + v['label'] + ') ') if v['label'] else ''}"
        f"{v['free_gb']} GB free of {v['total_gb']} GB ({v['percent_used']}% used)"
        for v in volumes
    ]
    return {
        "volumes": volumes,
        "local_total_gb": total_gb,
        "local_used_gb": used_gb,
        "local_free_gb": free_gb,
        "volume_count": len(volumes),
        "summary": (
            ("Combined local storage: "
             f"{free_gb} GB free of {total_gb} GB ({used_gb} GB used). ")
            + " ".join(bits)
        ).strip(),
    }


def _is_local_volume(row: dict[str, Any]) -> bool:
    kind = str(row.get("kind") or "").lower()
    return kind == "local" or "fixed" in kind


def _skip_names() -> set[str]:
    names = {item.lower() for item in SYSTEM_SKIP_DIRS}
    names.update(get_file_access_config().skip_dir_names)
    return names


def _count_tree(root: Path, skip: set[str], remaining: list[int], deadline: float) -> dict[str, Any]:
    files = 0
    folders = 0
    truncated = False
    skipped = 0
    try:
        if not root.exists() or not root.is_dir():
            return {
                "path": str(root),
                "files": 0,
                "folders": 0,
                "truncated": False,
                "skipped_dirs": 0,
                "exists": False,
            }
    except OSError:
        return {
            "path": str(root),
            "files": 0,
            "folders": 0,
            "truncated": False,
            "skipped_dirs": 0,
            "exists": False,
        }

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        if time.monotonic() > deadline or remaining[0] <= 0:
            truncated = True
            dirnames.clear()
            break
        kept: list[str] = []
        for name in dirnames:
            if name.lower() in skip:
                skipped += 1
            else:
                kept.append(name)
        dirnames[:] = kept
        take_dirs = len(kept)
        take_files = len(filenames)
        if remaining[0] < take_dirs + take_files:
            truncated = True
            take_files = max(0, remaining[0] - take_dirs)
            take_dirs = min(take_dirs, remaining[0])
            dirnames.clear()
        folders += take_dirs
        files += take_files
        remaining[0] -= take_dirs + take_files

    return {
        "path": str(root),
        "files": files,
        "folders": folders,
        "truncated": truncated,
        "skipped_dirs": skipped,
        "exists": True,
    }


def file_inventory_snapshot(scope: str = "all", *, max_entries: int = 80000, max_seconds: float = 12.0) -> dict[str, Any]:
    wanted = (scope or "all").strip().lower()
    if wanted not in {"all", "user", "volumes"}:
        wanted = "all"
    skip = _skip_names()
    libraries: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    started = time.monotonic()

    if wanted in {"all", "user"}:
        lib_budget = [max_entries if wanted == "user" else max(5000, max_entries // 3)]
        lib_deadline = started + (max_seconds if wanted == "user" else max_seconds * 0.35)
        for name, path in get_file_access_config().roots.items():
            libraries.append({"id": name, **_count_tree(path, skip, lib_budget, lib_deadline)})

    if wanted in {"all", "volumes"}:
        vol_budget = [max_entries if wanted == "volumes" else max(10000, (max_entries * 2) // 3)]
        vol_deadline = time.monotonic() + (max_seconds if wanted == "volumes" else max_seconds * 0.65)
        for row in storage_overview()["volumes"]:
            if not _is_local_volume(row) and not (len(row["device"]) == 2 and row["device"][1] == ":"):
                continue
            mount = Path(row["device"] + "\\") if len(row["device"]) == 2 else Path(row["device"])
            counted = _count_tree(mount, skip, vol_budget, vol_deadline)
            counted["device"] = row["device"]
            counted["label"] = row["label"]
            volumes.append(counted)

    lib_files = sum(item["files"] for item in libraries)
    lib_folders = sum(item["folders"] for item in libraries)
    vol_files = sum(item["files"] for item in volumes)
    vol_folders = sum(item["folders"] for item in volumes)
    truncated = any(item.get("truncated") for item in libraries + volumes)
    note = (
        "Windows, Program Files, ProgramData, AppData, recycle bins, and similar system folders "
        "are skipped so this is not a full NTFS walk. User-library counts sit inside the volume counts."
    )
    if wanted == "user":
        summary = (
            f"User folders: {lib_files} files and {lib_folders} folders "
            f"across Documents/Downloads/Desktop/sandbox."
        )
        headline_files, headline_folders = lib_files, lib_folders
    elif wanted == "volumes":
        summary = f"Local volumes (system folders skipped): {vol_files} files and {vol_folders} folders."
        headline_files, headline_folders = vol_files, vol_folders
    else:
        summary = (
            f"User folders: {lib_files} files / {lib_folders} folders. "
            f"Local volumes excluding Windows system dirs: {vol_files} files / {vol_folders} folders."
        )
        headline_files, headline_folders = vol_files or lib_files, vol_folders or lib_folders
    if truncated:
        summary += " Count stopped at the scan budget, so totals are incomplete."
    return {
        "scope": wanted,
        "files": headline_files,
        "folders": headline_folders,
        "user_libraries": libraries,
        "volumes": volumes,
        "truncated": truncated,
        "skipped_system_dirs": sorted(SYSTEM_SKIP_DIRS),
        "note": note,
        "summary": summary,
    }


def directory_usage(path: Path, *, max_files: int = 20000) -> dict[str, Any]:
    total = 0
    files = 0
    truncated = False
    if not path.exists():
        return {"path": str(path), "exists": False, "bytes": 0, "gb": 0.0, "files": 0, "truncated": False}
    for dirpath, _dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        for name in filenames:
            if files >= max_files:
                truncated = True
                break
            try:
                total += (Path(dirpath) / name).stat().st_size
                files += 1
            except OSError:
                continue
        if truncated:
            break
    return {
        "path": str(path),
        "exists": True,
        "bytes": total,
        "gb": _bytes_gb(total),
        "files": files,
        "truncated": truncated,
    }


def find_vhdx(root: Path, *, max_depth: int = 6) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not root.exists():
        return found
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames.clear()
            continue
        for name in filenames:
            if not name.lower().endswith(".vhdx"):
                continue
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            found.append({"path": str(path), "gb": _bytes_gb(size), "bytes": size, "name": name})
    return found


def _is_poc(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in POC_HINTS)


def _parse_wsl_list(text: str) -> list[dict[str, Any]]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("*").strip()
        if not line or line.upper().startswith("NAME"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rows.append({"name": parts[0], "state": parts[1], "version": parts[2] if len(parts) > 2 else None})
    return rows


def _wsl_distro_for(machine: str, distros: list[dict[str, Any]]) -> str | None:
    names = [row["name"] for row in distros]
    if machine in names:
        return machine
    prefixed = f"podman-{machine}"
    if prefixed in names:
        return prefixed
    return None


def _podman_inside(distro: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"distro": distro, "reachable": False}
    try:
        code, out, err = run_command(["wsl", "-d", distro, "--", "df", "-k", "/"], timeout=20)
        if code == 0:
            payload["reachable"] = True
            payload["root_fs"] = _parse_df_bytes(out)
        else:
            payload["df_error"] = (err or out).strip()
            return payload
    except Exception as exc:
        payload["df_error"] = str(exc)
        return payload

    payload["images"] = _json_cmd(["wsl", "-d", distro, "--", "podman", "images", "--format", "json"], timeout=25)
    payload["containers"] = _json_cmd(["wsl", "-d", distro, "--", "podman", "ps", "-a", "--format", "json"], timeout=25)
    payload["volumes"] = _json_cmd(["wsl", "-d", distro, "--", "podman", "volume", "ls", "--format", "json"], timeout=20)
    payload["system_df"] = _text_cmd(["wsl", "-d", distro, "--", "podman", "system", "df"], timeout=25)
    payload["image_summaries"] = [
        {
            "names": item.get("Names") or item.get("RepoTags") or [],
            "size_gb": _bytes_gb(int(item.get("Size") or 0)),
            "containers": item.get("Containers"),
        }
        for item in _as_list(payload.get("images"))
        if isinstance(item, dict)
    ]
    payload["container_summaries"] = [
        {
            "id": (item.get("Id") or "")[:12],
            "names": item.get("Names"),
            "image": item.get("Image"),
            "status": item.get("Status") or item.get("State"),
        }
        for item in _as_list(payload.get("containers"))
        if isinstance(item, dict)
    ]
    payload["volume_summaries"] = [
        {"name": item.get("Name") or item.get("name"), "driver": item.get("Driver") or item.get("driver")}
        for item in _as_list(payload.get("volumes"))
        if isinstance(item, dict)
    ]
    return payload


def _text_cmd(argv: list[str], *, timeout: float) -> str:
    try:
        code, out, err = run_command(argv, timeout=timeout)
        return (out or err).strip() if code == 0 else (err or out).strip()
    except Exception as exc:
        return str(exc)


def _json_cmd(argv: list[str], *, timeout: float) -> Any:
    try:
        code, out, err = run_command(argv, timeout=timeout)
        if code != 0:
            return {"error": (err or out).strip()}
        text = out.strip()
        if not text:
            return []
        return json.loads(text)
    except Exception as exc:
        return {"error": str(exc)}


def _size_to_gb(value: int) -> float:
    if value >= 1_000_000:
        return _bytes_gb(value)
    return float(value)


def _parse_df_bytes(text: str) -> dict[str, Any] | None:
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[5] == "/":
            try:
                total = int(parts[1]) * 1024
                used = int(parts[2]) * 1024
                avail = int(parts[3]) * 1024
            except ValueError:
                return None
            return {
                "total_gb": _bytes_gb(total),
                "used_gb": _bytes_gb(used),
                "available_gb": _bytes_gb(avail),
            }
    return None


def _machine_row(raw: dict[str, Any], distros: list[dict[str, Any]], vhdx: list[dict[str, Any]]) -> dict[str, Any]:
    name = str(raw.get("Name") or "")
    disk_size = int(raw.get("DiskSize") or 0)
    memory = int(raw.get("Memory") or 0)
    distro = _wsl_distro_for(name, distros)
    related = [item for item in vhdx if name.lower() in item["path"].lower() or (distro or "").lower() in item["path"].lower()]
    host_gb = round(sum(item["gb"] for item in related), 2)
    row: dict[str, Any] = {
        "name": name,
        "role": "poc_storage" if _is_poc(name) else "podman_machine",
        "running": bool(raw.get("Running")),
        "vm_type": raw.get("VMType"),
        "cpus": raw.get("CPUs"),
        "memory_gb": _size_to_gb(memory),
        "allocated_disk_gb": _size_to_gb(disk_size),
        "wsl_distro": distro,
        "host_vhdx": related,
        "host_vhdx_gb": host_gb,
        "last_up": raw.get("LastUp"),
        "created": raw.get("Created"),
        "user_mode_networking": raw.get("UserModeNetworking"),
    }
    if distro:
        distro_state = next((item["state"] for item in distros if item["name"] == distro), None)
        row["wsl_state"] = distro_state
        if str(distro_state).lower() == "running":
            row["inside"] = _podman_inside(distro)
        else:
            row["inside"] = {
                "reachable": False,
                "note": "Machine is stopped; container/image listing needs it running. Host VHDX size is still shown.",
            }
    return row


def podman_usage_snapshot() -> dict[str, Any]:
    notes: list[str] = []
    distros: list[dict[str, Any]] = []
    if shutil.which("wsl"):
        try:
            _code, out, err = run_command(["wsl", "-l", "-v"], timeout=15)
            distros = _parse_wsl_list(out or err)
        except Exception as exc:
            notes.append(f"WSL list failed: {exc}")
    else:
        notes.append("wsl.exe not found.")

    machines_raw: list[dict[str, Any]] = []
    if shutil.which("podman"):
        parsed = _json_cmd(["podman", "machine", "list", "--format", "json"], timeout=20)
        if isinstance(parsed, dict) and parsed.get("error"):
            notes.append(f"podman machine list: {parsed['error']}")
        else:
            machines_raw = [item for item in _as_list(parsed) if isinstance(item, dict)]
    else:
        notes.append("Podman CLI not found on PATH.")

    vhdx = find_vhdx(PODMAN_HOST_ROOT)
    host = {
        "containers_root": directory_usage(PODMAN_HOST_ROOT),
        "podman": directory_usage(PODMAN_HOST_ROOT / "podman"),
        "podman_desktop": directory_usage(PODMAN_HOST_ROOT / "podman-desktop"),
        "storage": directory_usage(PODMAN_HOST_ROOT / "storage"),
        "config": directory_usage(Path.home() / ".config" / "containers"),
    }

    machines = [_machine_row(item, distros, vhdx) for item in machines_raw]
    known = {item["name"] for item in machines}
    extras = []
    for distro in distros:
        name = distro["name"]
        if name in known or name.removeprefix("podman-") in known:
            continue
        related = [item for item in vhdx if name.lower() in item["path"].lower()]
        extras.append(
            {
                "name": name,
                "role": "wsl_helper" if "net" in name.lower() else ("poc_storage" if _is_poc(name) else "wsl_distro"),
                "wsl_state": distro["state"],
                "host_vhdx": related,
                "host_vhdx_gb": round(sum(item["gb"] for item in related), 2),
            }
        )

    allocated = round(sum(float(item.get("allocated_disk_gb") or 0) for item in machines), 2)
    host_vhdx_gb = round(sum(item["gb"] for item in vhdx), 2)
    host_total_gb = host["containers_root"]["gb"]
    poc = [item for item in machines + extras if item.get("role") == "poc_storage"]
    default_running = next((item for item in machines if item.get("running")), None)

    summary_parts = [
        f"Podman host files under {PODMAN_HOST_ROOT} use about {host_total_gb} GB.",
        f"WSL virtual disks on this PC total {host_vhdx_gb} GB (sparse files; machines allocate {allocated} GB combined).",
    ]
    for item in machines:
        bit = (
            f"{item['name']} ({item['role']}, {'running' if item['running'] else 'stopped'}): "
            f"{item['allocated_disk_gb']} GB allocated, {item['host_vhdx_gb']} GB VHDX on disk"
        )
        inside = item.get("inside") or {}
        if inside.get("root_fs"):
            fs = inside["root_fs"]
            bit += f", {fs['used_gb']} GB used inside the VM"
        images = inside.get("image_summaries") or []
        containers = inside.get("container_summaries") or []
        volumes = inside.get("volume_summaries") or []
        if item["running"]:
            bit += f", {len(images)} images, {len(containers)} containers, {len(volumes)} named volumes"
        summary_parts.append(bit + ".")
    for item in extras:
        summary_parts.append(
            f"{item['name']} ({item['role']}, {item.get('wsl_state')}): {item['host_vhdx_gb']} GB VHDX on disk."
        )
    if poc:
        names = ", ".join(item["name"] for item in poc)
        summary_parts.append(f"Separate POC storage machines: {names}.")
    if default_running is None and machines:
        summary_parts.append("No Podman machine is running, so live container usage inside the VM was not queried.")

    return {
        "installed": bool(shutil.which("podman")),
        "host": host,
        "vhdx": vhdx,
        "wsl_distros": distros,
        "machines": machines,
        "extra_wsl": extras,
        "poc_storage": poc,
        "totals": {
            "host_containers_gb": host_total_gb,
            "host_vhdx_gb": host_vhdx_gb,
            "allocated_machine_disk_gb": allocated,
        },
        "notes": notes,
        "summary": " ".join(summary_parts),
    }


def build_inventory_tools() -> list[Tool]:
    def get_pc_configuration(_args: EmptyArgs) -> ToolResult:
        data = pc_configuration_snapshot()
        return ToolResult(
            ok=True,
            data=data,
            evidence={"model": data.get("model"), "ram_total_gb": data.get("ram_total_gb")},
        )

    def get_storage_overview(_args: EmptyArgs) -> ToolResult:
        data = storage_overview()
        return ToolResult(
            ok=True,
            data=data,
            evidence={"volume_count": data["volume_count"], "local_free_gb": data["local_free_gb"]},
        )

    def get_file_inventory(args: FileInventoryArgs) -> ToolResult:
        data = file_inventory_snapshot(args.scope)
        return ToolResult(
            ok=True,
            data=data,
            evidence={"files": data["files"], "folders": data["folders"], "truncated": data["truncated"]},
        )

    def get_podman_usage(_args: EmptyArgs) -> ToolResult:
        data = podman_usage_snapshot()
        return ToolResult(
            ok=True,
            data=data,
            evidence={
                "host_gb": data["totals"]["host_containers_gb"],
                "machines": len(data["machines"]),
            },
        )

    def verify_ok(_args: BaseModel, result: ToolResult) -> VerificationResult:
        return VerificationResult(
            verified=result.ok,
            evidence=result.evidence,
            reason="ok" if result.ok else result.error or "failed",
        )

    return [
        Tool(
            name="get_pc_configuration",
            description="Hardware/OS configuration: manufacturer, model, CPU, RAM, GPU, disks, BIOS. Use for 'what PC is this', 'specs', 'configuration'.",
            parameters=EmptyArgs,
            timeout_s=35,
            handler=get_pc_configuration,
            verifier=verify_ok,
        ),
        Tool(
            name="get_storage_overview",
            description="All volumes/drives with total, used, and free space, plus combined local storage. Use for 'how much storage is left', 'C: and D: space'.",
            parameters=EmptyArgs,
            timeout_s=20,
            handler=get_storage_overview,
            verifier=verify_ok,
        ),
        Tool(
            name="get_file_inventory",
            description="Count files and folders. scope=user (Documents/Downloads/Desktop/sandbox), volumes (each local drive, skipping Windows system dirs), or all. Use for 'how many files are on this PC'.",
            parameters=FileInventoryArgs,
            timeout_s=50,
            handler=get_file_inventory,
            verifier=verify_ok,
        ),
        Tool(
            name="get_podman_usage",
            description="Podman/WSL disk usage: host folders, each machine VHDX, allocated vs actual, containers/images/volumes, and separate POC machines such as neo4j. Use for 'how much space is Podman using'.",
            parameters=EmptyArgs,
            timeout_s=45,
            handler=get_podman_usage,
            verifier=verify_ok,
        ),
    ]
