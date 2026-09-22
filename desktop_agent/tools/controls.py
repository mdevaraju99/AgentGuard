from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any

from pydantic import BaseModel, Field

from desktop_agent.tools.base import Tool, ToolResult, VerificationResult
from desktop_agent.tools.inventory import _as_list, powershell_json, run_command


class EmptyArgs(BaseModel):
    pass


class PercentArgs(BaseModel):
    percent: int = Field(description="Level from 0 to 100")


class VolumeArgs(BaseModel):
    percent: int | None = Field(default=None, description="Optional volume 0-100. Omit to only change mute.")
    mute: bool | None = Field(default=None, description="Optional mute true/false. Omit to leave mute unchanged.")


class RadioActionArgs(BaseModel):
    action: str = Field(description="on, off, or status")


class BluetoothConnectArgs(BaseModel):
    name: str = Field(
        default="",
        description="Saved device name such as SOUNDPEATS or Noise Buds. Empty connects nearby saved devices.",
    )


_WINRT_HEADER = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await-WinRT($op, [type]$ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($op))
  if (-not $netTask.Wait(25000)) { throw 'WinRT timeout' }
  if ($netTask.IsFaulted) { throw $netTask.Exception.GetBaseException() }
  return $netTask.Result
}
function Await-Action($op) {
  $m = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
  } | Select-Object -First 1
  $netTask = $m.Invoke($null, @($op))
  if (-not $netTask.Wait(25000)) { throw 'WinRT timeout' }
  if ($netTask.IsFaulted) { throw $netTask.Exception.GetBaseException() }
}
"""

_AUDIO_TYPE = r"""
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int NotImpl1(); int NotImpl2();
  int GetChannelCount([Out] out int count);
  int SetMasterVolumeLevel(float level, System.Guid context);
  int SetMasterVolumeLevelScalar(float level, System.Guid context);
  int GetMasterVolumeLevel([Out] out float level);
  int GetMasterVolumeLevelScalar([Out] out float level);
  int SetChannelVolumeLevel(uint index, float level, System.Guid context);
  int SetChannelVolumeLevelScalar(uint index, float level, System.Guid context);
  int GetChannelVolumeLevel(uint index, [Out] out float level);
  int GetChannelVolumeLevelScalar(uint index, [Out] out float level);
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, System.Guid context);
  int GetMute([Out] [MarshalAs(UnmanagedType.Bool)] out bool mute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
  int Activate(ref System.Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume aev);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
  int NotImpl1();
  int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class Audio {
  static IAudioEndpointVolume Vol() {
    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;
    IMMDevice dev = null;
    Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(/*eRender*/ 0, /*eMultimedia*/ 1, out dev));
    IAudioEndpointVolume epv = null;
    var epvid = typeof(IAudioEndpointVolume).GUID;
    Marshal.ThrowExceptionForHR(dev.Activate(ref epvid, 23, 0, out epv));
    return epv;
  }
  public static float GetVolume() { float v = 0; Marshal.ThrowExceptionForHR(Vol().GetMasterVolumeLevelScalar(out v)); return v; }
  public static void SetVolume(float v) { Marshal.ThrowExceptionForHR(Vol().SetMasterVolumeLevelScalar(v, System.Guid.Empty)); }
  public static bool GetMute() { bool v; Marshal.ThrowExceptionForHR(Vol().GetMute(out v)); return v; }
  public static void SetMute(bool v) { Marshal.ThrowExceptionForHR(Vol().SetMute(v, System.Guid.Empty)); }
}
'@
"""


def _clamp_percent(value: int) -> int:
    return max(0, min(100, int(value)))


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def run_ps_script(body: str, *, timeout: float = 30) -> tuple[int, str, str]:
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8")
    handle.write(body)
    handle.close()
    try:
        return run_command(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", handle.name],
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _parse_json_blob(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for opener, closer in (("{", "}"), ("[", "]")):
            start = raw.rfind(opener)
            end = raw.rfind(closer)
            if start >= 0 and end > start:
                return json.loads(raw[start : end + 1])
        raise


def _require_ok(code: int, out: str, err: str) -> str:
    if code != 0:
        raise RuntimeError((err or out or f"powershell exit {code}").strip())
    return (out or "").strip()


def brightness_snapshot() -> dict[str, Any]:
    rows = _as_list(
        powershell_json(
            "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | "
            "Select-Object CurrentBrightness, Active | ConvertTo-Json -Compress",
            timeout=15,
        )
    )
    monitors = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        monitors.append(
            {
                "percent": int(row.get("CurrentBrightness") or 0),
                "active": bool(row.get("Active")),
            }
        )
    if not monitors:
        raise RuntimeError("No brightness sensor reported (external monitor or WMI unavailable).")
    percent = monitors[0]["percent"]
    return {
        "percent": percent,
        "monitors": monitors,
        "summary": f"Screen brightness is {percent}%.",
    }


def set_brightness(percent: int) -> dict[str, Any]:
    level = _clamp_percent(percent)
    script = (
        f"$level = {level}; "
        "$methods = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods; "
        "foreach ($m in @($methods)) { "
        "  Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{Timeout=1; Brightness=$level} | Out-Null "
        "}; "
        "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | "
        "Select-Object CurrentBrightness | ConvertTo-Json -Compress"
    )
    rows = _as_list(powershell_json(script, timeout=15))
    actual = int((rows[0] or {}).get("CurrentBrightness") or level) if rows else level
    return {
        "requested": level,
        "percent": actual,
        "summary": f"Screen brightness set to {actual}%.",
    }


def volume_snapshot() -> dict[str, Any]:
    code, out, err = run_ps_script(_AUDIO_TYPE + "\n[Audio]::GetVolume(); [Audio]::GetMute()\n", timeout=25)
    text = _require_ok(code, out, err)
    lines = [line.strip() for line in text.splitlines() if line.strip() and "Add-Type" not in line]
    if len(lines) < 2:
        raise RuntimeError(err or out or "Could not read volume.")
    raw = float(lines[-2])
    mute_token = lines[-1].lower()
    muted = mute_token in {"true", "1"}
    percent = int(round(raw * 100))
    status = "muted" if muted else f"{percent}%"
    return {
        "percent": percent,
        "muted": muted,
        "summary": f"Speaker volume is {status}.",
    }


def set_volume(*, percent: int | None = None, mute: bool | None = None) -> dict[str, Any]:
    if percent is None and mute is None:
        return volume_snapshot()
    parts = [_AUDIO_TYPE]
    if percent is not None:
        level = _clamp_percent(percent) / 100.0
        parts.append(f"[Audio]::SetVolume({level})")
    if mute is not None:
        parts.append(f"[Audio]::SetMute(${str(bool(mute)).lower()})")
    parts.append("[Audio]::GetVolume(); [Audio]::GetMute()")
    code, out, err = run_ps_script("\n".join(parts), timeout=25)
    text = _require_ok(code, out, err)
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("Add-Type")]
    raw = float(lines[-2])
    muted = lines[-1].lower() in {"true", "1"}
    actual = int(round(raw * 100))
    if muted:
        summary = f"Speakers muted (level {actual}%)."
    else:
        summary = f"Speaker volume set to {actual}%."
    return {"percent": actual, "muted": muted, "summary": summary}


def _radio_script(kind: str, action: str) -> str:
    wanted = kind.strip().lower()
    act = action.strip().lower()
    return _WINRT_HEADER + f"""
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$access = Await-WinRT ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$radios = Await-WinRT ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$target = $null
foreach ($r in $radios) {{
  $k = [string]$r.Kind
  if ($k -eq '{wanted}' -or $k -eq '{wanted.title()}' -or $k -eq '{kind}') {{ $target = $r; break }}
}}
if (-not $target) {{
  [pscustomobject]@{{ ok = $false; error = "No {wanted} radio found"; access = "$access" }} | ConvertTo-Json -Compress
  exit 0
}}
$action = '{act}'
if ($action -eq 'on' -or $action -eq 'off') {{
  $state = if ($action -eq 'on') {{ [Windows.Devices.Radios.RadioState]::On }} else {{ [Windows.Devices.Radios.RadioState]::Off }}
  Await-Action ($target.SetStateAsync($state))
  Start-Sleep -Milliseconds 400
}}
[pscustomobject]@{{
  ok = $true
  name = $target.Name
  kind = [string]$target.Kind
  state = [string]$target.State
  access = "$access"
  action = $action
}} | ConvertTo-Json -Compress
"""


def radio_control(kind: str, action: str) -> dict[str, Any]:
    act = (action or "status").strip().lower()
    if act not in {"on", "off", "status"}:
        raise ValueError("action must be on, off, or status")
    code, out, err = run_ps_script(_radio_script(kind, act), timeout=35)
    payload = _parse_json_blob(_require_ok(code, out, err) or "{}") or {}
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or err or "Radio control failed")
    state = str(payload.get("state") or "")
    label = "Wi-Fi" if kind.lower() == "wifi" else "Bluetooth"
    if act == "status":
        summary = f"{label} radio is {state}."
    else:
        summary = f"{label} radio turned {act}. Now {state}."
    payload["summary"] = summary
    payload["on"] = state.lower() == "on"
    return payload


def wifi_adapter_status() -> dict[str, Any]:
    rows = _as_list(
        powershell_json(
            "Get-NetAdapter | Where-Object { $_.Name -match 'Wi-?Fi|Wireless' -or $_.InterfaceDescription -match 'Wi-Fi|Wireless|802.11' } | "
            "Select-Object Name, Status, InterfaceDescription, MacAddress | ConvertTo-Json -Compress",
            timeout=15,
        )
    )
    adapters = []
    for row in rows:
        if isinstance(row, dict) and row.get("Name"):
            adapters.append(
                {
                    "name": row.get("Name"),
                    "status": str(row.get("Status")),
                    "description": row.get("InterfaceDescription"),
                }
            )
    return {"adapters": adapters}


def set_wifi(action: str) -> dict[str, Any]:
    radio = radio_control("WiFi", action)
    extra = wifi_adapter_status()
    radio["adapters"] = extra["adapters"]
    adapter_bits = ", ".join(f"{a['name']} {a['status']}" for a in extra["adapters"]) or "no Wi-Fi adapter listed"
    radio["summary"] = f"{radio['summary']} Adapter: {adapter_bits}."
    return radio


def set_bluetooth_radio(action: str) -> dict[str, Any]:
    return radio_control("Bluetooth", action)


def saved_bluetooth_devices() -> list[dict[str, Any]]:
    rows = _as_list(
        powershell_json(
            "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
            "Where-Object { $_.InstanceId -match 'BTHENUM\\\\DEV_' -and $_.FriendlyName -notmatch 'Avrcp|Transport|Enumerator' } | "
            "Select-Object Status, FriendlyName, InstanceId | ConvertTo-Json -Compress",
            timeout=20,
        )
    )
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("FriendlyName") or "").strip()
        instance = str(row.get("InstanceId") or "")
        if not name:
            continue
        match = re.search(r"DEV_([0-9A-F]+)", instance, re.I)
        addr = match.group(1).upper() if match else instance
        seen[addr] = {"name": name, "address": addr, "instance_id": instance, "status": row.get("Status")}
    return list(seen.values())


def nearby_paired_bluetooth() -> list[dict[str, Any]]:
    script = _WINRT_HEADER + r"""
[Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime] | Out-Null
$selector = 'System.Devices.Aep.ProtocolId:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}" AND System.Devices.Aep.IsPaired:=System.StructuredQueryType.Boolean#True'
$extra = [string[]]@(
  'System.Devices.Aep.IsConnected',
  'System.Devices.Aep.IsPresent',
  'System.Devices.Aep.DeviceAddress'
)
$devices = Await-WinRT ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector, $extra)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
$rows = @()
foreach ($d in $devices) {
  $rows += [pscustomobject]@{
    name = $d.Name
    id = $d.Id
    connected = [bool]$d.Properties['System.Devices.Aep.IsConnected']
    present = [bool]$d.Properties['System.Devices.Aep.IsPresent']
    address = [string]$d.Properties['System.Devices.Aep.DeviceAddress']
  }
}
$rows | ConvertTo-Json -Compress
"""
    code, out, err = run_ps_script(script, timeout=30)
    text = _require_ok(code, out, err)
    if not text:
        return []
    return [row for row in _as_list(_parse_json_blob(text)) if isinstance(row, dict)]


def _match_device(name: str, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    needle = _compact(name)
    if not needle:
        return list(devices)
    hits = [item for item in devices if needle in _compact(str(item.get("name") or ""))]
    return hits or [item for item in devices if _compact(str(item.get("name") or "")) in needle]


def _connect_audio_ids(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    quoted = ",".join(f"'{item.replace(chr(39), '')}'" for item in ids)
    script = _WINRT_HEADER + f"""
[Windows.Media.Audio.AudioPlaybackConnection,Windows.Media.Audio,ContentType=WindowsRuntime] | Out-Null
$ids = @({quoted})
$out = @()
foreach ($id in $ids) {{
  try {{
    $conn = [Windows.Media.Audio.AudioPlaybackConnection]::TryCreateFromId($id)
    if ($null -eq $conn) {{
      $out += [pscustomobject]@{{ id = $id; ok = $false; error = 'Not an audio playback device' }}
      continue
    }}
    Await-Action ($conn.StartAsync())
    $opened = Await-WinRT ($conn.OpenAsync()) ([Windows.Media.Audio.AudioPlaybackConnectionOpenResult])
    $out += [pscustomobject]@{{ id = $id; ok = $true; status = [string]$opened.Status; result = "$opened" }}
  }} catch {{
    $out += [pscustomobject]@{{ id = $id; ok = $false; error = $_.Exception.Message }}
  }}
}}
$out | ConvertTo-Json -Compress
"""
    code, out, err = run_ps_script(script, timeout=40)
    text = _require_ok(code, out, err)
    if not text:
        return []
    return [row for row in _as_list(_parse_json_blob(text)) if isinstance(row, dict)]


def connect_bluetooth_device(name: str = "") -> dict[str, Any]:
    saved = saved_bluetooth_devices()
    radio = radio_control("Bluetooth", "status")
    notes: list[str] = []
    if not radio.get("on"):
        notes.append("Bluetooth radio was off; turning it on to look for saved devices nearby.")
        radio = radio_control("Bluetooth", "on")
        time.sleep(3)

    nearby = nearby_paired_bluetooth()
    query = (name or "").strip()
    saved_hits = _match_device(query, saved) if query else saved
    nearby_hits = _match_device(query, nearby) if query else nearby
    present = [item for item in nearby_hits if item.get("present") or item.get("connected")]
    already = [item for item in present if item.get("connected")]
    to_connect = [item for item in present if not item.get("connected")]

    results = []
    if to_connect:
        results = _connect_audio_ids([str(item.get("id")) for item in to_connect if item.get("id")])
        id_map = {item.get("id"): item for item in to_connect}
        for row in results:
            src = id_map.get(row.get("id")) or {}
            row["name"] = src.get("name")

    connected_names = [str(item.get("name")) for item in already]
    connected_names += [str(item.get("name")) for item in results if item.get("ok") and item.get("name")]
    failed = [item for item in results if not item.get("ok")]

    if connected_names:
        summary = "Connected nearby saved Bluetooth: " + ", ".join(dict.fromkeys(connected_names)) + "."
    elif present:
        summary = "Saved device(s) are nearby but Windows could not complete the audio connection."
    elif saved_hits and query:
        names = ", ".join(item["name"] for item in saved_hits)
        summary = f"{names} is saved on this PC but not nearby right now."
    elif saved:
        names = ", ".join(item["name"] for item in saved)
        summary = f"No saved Bluetooth devices are nearby. Paired devices: {names}."
    else:
        summary = "No saved Bluetooth devices were found on this PC."

    return {
        "query": query,
        "radio": radio,
        "saved": saved,
        "nearby": nearby,
        "present": present,
        "connect_results": results,
        "notes": notes,
        "summary": summary,
        "failed": failed,
    }


def bluetooth_inventory() -> dict[str, Any]:
    radio = radio_control("Bluetooth", "status")
    saved = saved_bluetooth_devices()
    nearby: list[dict[str, Any]] = []
    if radio.get("on"):
        nearby = nearby_paired_bluetooth()
    present = [item for item in nearby if item.get("present") or item.get("connected")]
    saved_names = ", ".join(item["name"] for item in saved) or "none"
    if present:
        near_names = ", ".join(item.get("name") or "?" for item in present)
        extra = f" Nearby saved devices: {near_names}."
    else:
        extra = " None of the saved devices are nearby (Bluetooth radio off means nearby scan is skipped)." if not radio.get("on") else " None of the saved devices are nearby."
    return {
        "radio": radio,
        "saved": saved,
        "nearby": nearby,
        "present": present,
        "summary": f"{radio['summary']} Saved devices: {saved_names}.{extra}",
    }


def build_control_tools() -> list[Tool]:
    def get_brightness(_args: EmptyArgs) -> ToolResult:
        data = brightness_snapshot()
        return ToolResult(ok=True, data=data, evidence={"percent": data["percent"]})

    def tool_set_brightness(args: PercentArgs) -> ToolResult:
        data = set_brightness(args.percent)
        return ToolResult(ok=True, data=data, evidence={"percent": data["percent"]})

    def get_volume(_args: EmptyArgs) -> ToolResult:
        data = volume_snapshot()
        return ToolResult(ok=True, data=data, evidence={"percent": data["percent"], "muted": data["muted"]})

    def tool_set_volume(args: VolumeArgs) -> ToolResult:
        data = set_volume(percent=args.percent, mute=args.mute)
        return ToolResult(ok=True, data=data, evidence={"percent": data["percent"], "muted": data["muted"]})

    def tool_set_wifi(args: RadioActionArgs) -> ToolResult:
        data = set_wifi(args.action)
        return ToolResult(ok=True, data=data, evidence={"state": data.get("state"), "action": args.action})

    def tool_set_bluetooth(args: RadioActionArgs) -> ToolResult:
        data = set_bluetooth_radio(args.action)
        return ToolResult(ok=True, data=data, evidence={"state": data.get("state"), "action": args.action})

    def list_bluetooth(_args: EmptyArgs) -> ToolResult:
        data = bluetooth_inventory()
        return ToolResult(ok=True, data=data, evidence={"saved": len(data["saved"]), "present": len(data["present"])})

    def tool_connect_bt(args: BluetoothConnectArgs) -> ToolResult:
        data = connect_bluetooth_device(args.name)
        ok = not data.get("failed") or bool(data.get("present"))
        return ToolResult(ok=ok, data=data, evidence={"present": len(data["present"]), "summary": data["summary"]})

    def verify_ok(_args: BaseModel, result: ToolResult) -> VerificationResult:
        return VerificationResult(
            verified=result.ok,
            evidence=result.evidence,
            reason="ok" if result.ok else result.error or "failed",
        )

    return [
        Tool(
            name="get_brightness",
            description="Get current screen brightness percent. Use for 'how bright is the screen'.",
            parameters=EmptyArgs,
            timeout_s=20,
            handler=get_brightness,
            verifier=verify_ok,
        ),
        Tool(
            name="set_brightness",
            description="Set screen brightness 0-100. Use for 'set brightness to 50', 'make the screen brighter'.",
            parameters=PercentArgs,
            timeout_s=20,
            handler=tool_set_brightness,
            verifier=verify_ok,
        ),
        Tool(
            name="get_volume",
            description="Get speaker volume percent and mute state.",
            parameters=EmptyArgs,
            timeout_s=25,
            handler=get_volume,
            verifier=verify_ok,
        ),
        Tool(
            name="set_volume",
            description="Set speaker volume 0-100 and/or mute. Use for 'volume 30', 'mute', 'unmute'. Pass mute=true to mute.",
            parameters=VolumeArgs,
            timeout_s=25,
            handler=tool_set_volume,
            verifier=verify_ok,
        ),
        Tool(
            name="set_wifi",
            description="Turn Wi-Fi on or off, or return status. action=on|off|status. Only toggle when the user clearly asked.",
            parameters=RadioActionArgs,
            timeout_s=35,
            handler=tool_set_wifi,
            verifier=verify_ok,
        ),
        Tool(
            name="set_bluetooth",
            description="Turn the Bluetooth radio on or off, or return status. action=on|off|status. Only toggle when clearly asked.",
            parameters=RadioActionArgs,
            timeout_s=35,
            handler=tool_set_bluetooth,
            verifier=verify_ok,
        ),
        Tool(
            name="list_bluetooth_devices",
            description="List saved/paired Bluetooth devices and which of them are nearby right now.",
            parameters=EmptyArgs,
            timeout_s=40,
            handler=list_bluetooth,
            verifier=verify_ok,
        ),
        Tool(
            name="connect_bluetooth_device",
            description="Turn Bluetooth on if needed, then connect a saved device if it is nearby. name is optional; empty connects nearby saved devices (e.g. SOUNDPEATS, Noise Buds).",
            parameters=BluetoothConnectArgs,
            timeout_s=50,
            handler=tool_connect_bt,
            verifier=verify_ok,
        ),
    ]
