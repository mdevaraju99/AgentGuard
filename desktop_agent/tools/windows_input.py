from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Iterable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
SW_RESTORE = 9
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
VK_DOWN = 0x28
VK_N = 0x4E
VK_E = 0x45
VK_A = 0x41
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

MAPVK_VK_TO_VSC = 0

ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.EnumWindows.argtypes = [ENUM_WINDOWS_PROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ULONG_PTR,
]

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def iter_top_windows() -> list[tuple[int, str, int]]:
    found: list[tuple[int, str, int]] = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if title:
            found.append((int(hwnd), title, _window_pid(hwnd)))
        return True

    cb = ENUM_WINDOWS_PROC(callback)
    user32.EnumWindows(cb, 0)
    return found


def find_teams_window(pids: Iterable[int] | None = None) -> tuple[int, str] | None:
    pid_set = {int(p) for p in (pids or [])}
    ranked: list[tuple[int, int, str]] = []
    for hwnd, title, pid in iter_top_windows():
        lower = title.lower()
        if "teams" not in lower:
            continue
        if any(skip in lower for skip in ("notification", "overlay", "meeting compact")):
            continue
        score = 0
        if pid_set and pid in pid_set:
            score += 20
        if "microsoft teams" in lower:
            score += 10
        if lower.startswith("chat"):
            score += 3
        ranked.append((score, hwnd, title))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    _score, hwnd, title = ranked[0]
    return hwnd, title


def foreground_title() -> str:
    hwnd = user32.GetForegroundWindow()
    return _window_title(int(hwnd)) if hwnd else ""


def focus_window(hwnd: int) -> bool:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.25)
    fg = user32.GetForegroundWindow()
    current = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached_fg = False
    attached_target = False
    if fg_thread and fg_thread != current:
        attached_fg = bool(user32.AttachThreadInput(current, fg_thread, True))
    if target_thread and target_thread != current:
        attached_target = bool(user32.AttachThreadInput(current, target_thread, True))
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.BringWindowToTop(hwnd)
    user32.ShowWindow(hwnd, SW_RESTORE)
    ok = bool(user32.SetForegroundWindow(hwnd))
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    if attached_target:
        user32.AttachThreadInput(current, target_thread, False)
    if attached_fg:
        user32.AttachThreadInput(current, fg_thread, False)
    time.sleep(0.35)
    return ok or user32.GetForegroundWindow() == hwnd


def _send_input(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput sent {sent} of {len(inputs)} events")


def _vk_event(vk: int, up: bool = False) -> INPUT:
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_KEYUP if up else 0
    event = INPUT(type=INPUT_KEYBOARD)
    event.union.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return event


def _unicode_event(char: str, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    event = INPUT(type=INPUT_KEYBOARD)
    event.union.ki = KEYBDINPUT(wVk=0, wScan=ord(char), dwFlags=flags, time=0, dwExtraInfo=0)
    return event


def tap_vk(vk: int) -> None:
    _send_input([_vk_event(vk, False), _vk_event(vk, True)])
    time.sleep(0.05)


def chord(modifier_vk: int, key_vk: int) -> None:
    _send_input(
        [
            _vk_event(modifier_vk, False),
            _vk_event(key_vk, False),
            _vk_event(key_vk, True),
            _vk_event(modifier_vk, True),
        ]
    )
    time.sleep(0.08)


def type_text(text: str, pause: float = 0.035) -> None:
    for char in text:
        if char == "\n":
            chord(VK_SHIFT, VK_RETURN)
            continue
        if char == "\r":
            continue
        _send_input([_unicode_event(char, False), _unicode_event(char, True)])
        time.sleep(pause)


def press_enter() -> None:
    tap_vk(VK_RETURN)


def press_escape() -> None:
    tap_vk(VK_ESCAPE)


def press_down() -> None:
    tap_vk(VK_DOWN)


def ctrl_enter() -> None:
    chord(VK_CONTROL, VK_RETURN)


def ctrl_n() -> None:
    chord(VK_CONTROL, VK_N)


def ctrl_e() -> None:
    chord(VK_CONTROL, VK_E)


def ctrl_a() -> None:
    chord(VK_CONTROL, VK_A)


def click_at(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.04)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.08)
