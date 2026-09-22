from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from desktop_agent.config import PROJECT_ROOT, _load_yaml
from desktop_agent.safety.sandbox import SandboxError, sandbox_root

ROOT_ALIASES = {
    "sandbox": "sandbox",
    "demo": "sandbox",
    "ai-agent-demo": "sandbox",
    "documents": "documents",
    "docs": "documents",
    "downloads": "downloads",
    "desktop": "desktop",
    "pictures": "pictures",
    "photos": "pictures",
    "videos": "videos",
}


@dataclass
class FileAccessConfig:
    allow_user_directories: bool
    roots: dict[str, Path]
    catalog: dict[str, str]
    enabled_user_roots: list[str]
    max_search_results: int = 25
    max_files_scanned: int = 8000
    content_scan_limit: int = 40
    skip_dir_names: set[str] = field(default_factory=set)


KNOWN_FOLDER_IDS = {
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "desktop": "B4BFCC3B-46DE-11D4-BE5B-00E02B687562",
    "pictures": "33E28130-4E1E-4678-9B27-F3E8C1D2F8AE",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


def _windows_known_folder(root_id: str) -> Path | None:
    if os.name != "nt":
        return None
    folder_id = KNOWN_FOLDER_IDS.get(root_id)
    if not folder_id:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", wintypes.BYTE * 8),
            ]

        uuid = UUID(folder_id)
        guid = GUID(uuid.time_low, uuid.time_mid, uuid.time_hi_version, (wintypes.BYTE * 8).from_buffer_copy(uuid.bytes[8:]))
        path_ptr = ctypes.c_wchar_p()
        windll = ctypes.windll.shell32
        result = windll.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path_ptr))
        if result != 0 or not path_ptr.value:
            return None
        path = Path(path_ptr.value)
        ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        return path if path.exists() else None
    except Exception:
        return None


def _expand_path(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def _resolve_configured_root(root_id: str, template: str) -> Path | None:
    candidates: list[Path] = []
    known = _windows_known_folder(root_id)
    if known:
        candidates.append(known)
    expanded = _expand_path(template)
    if expanded not in candidates:
        candidates.append(expanded)
    for path in candidates:
        try:
            if path.exists() and path.is_dir():
                return path.resolve()
        except OSError:
            continue
    return None


@lru_cache(maxsize=1)
def get_file_access_config() -> FileAccessConfig:
    raw = _load_yaml(PROJECT_ROOT / "config" / "file_roots.yaml")
    allow = bool(raw.get("allow_user_directories", True))
    catalog = dict(raw.get("user_root_paths") or {})
    enabled = [str(item).lower() for item in (raw.get("enabled_user_roots") or [])]
    skip = {str(name).lower() for name in (raw.get("skip_dir_names") or [])}
    roots: dict[str, Path] = {"sandbox": sandbox_root()}
    if allow:
        for key in enabled:
            template = catalog.get(key)
            if not template:
                continue
            path = _resolve_configured_root(key, template)
            if path is not None:
                roots[key] = path
    return FileAccessConfig(
        allow_user_directories=allow,
        roots=roots,
        catalog=catalog,
        enabled_user_roots=enabled,
        max_search_results=int(raw.get("max_search_results") or 25),
        max_files_scanned=int(raw.get("max_files_scanned") or 8000),
        content_scan_limit=int(raw.get("content_scan_limit") or 40),
        skip_dir_names=skip,
    )


def reset_file_access_cache() -> None:
    get_file_access_config.cache_clear()


def allowed_roots() -> dict[str, Path]:
    return dict(get_file_access_config().roots)


def describe_allowed_roots() -> list[dict[str, str]]:
    """UI-facing list of configured roots and whether they are currently usable."""
    cfg = get_file_access_config()
    rows: list[dict[str, str]] = [
        {
            "id": "sandbox",
            "label": "AI-Agent-Demo (sandbox, writes allowed)",
            "path": str(cfg.roots["sandbox"]),
            "status": "enabled",
            "access": "search / read / open / create",
        }
    ]
    for key, template in cfg.catalog.items():
        resolved = cfg.roots.get(key) or _windows_known_folder(key) or _expand_path(template)
        if key not in cfg.enabled_user_roots or not cfg.allow_user_directories:
            status = "disabled"
        elif key in cfg.roots:
            status = "enabled"
        else:
            status = "missing"
        rows.append(
            {
                "id": key,
                "label": key.title(),
                "path": str(resolved),
                "status": status,
                "access": "search / read / open" if status == "enabled" else "not searched",
            }
        )
    return rows


def normalize_root_id(name: str | None) -> str | None:
    if not name:
        return None
    key = re.sub(r"[^a-z0-9]+", "", name.strip().lower())
    return ROOT_ALIASES.get(name.strip().lower()) or ROOT_ALIASES.get(key)


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def which_root(path: Path) -> str | None:
    resolved = path.resolve()
    for name, root in allowed_roots().items():
        if is_under(resolved, root):
            return name
    return None


def display_path(path: Path) -> str:
    resolved = path.resolve()
    sandbox = allowed_roots().get("sandbox")
    if sandbox and is_under(resolved, sandbox):
        return str(resolved.relative_to(sandbox)).replace("\\", "/")
    return str(resolved)


def resolve_allowed_path(user_path: str | Path | None, *, must_exist: bool = False) -> Path:
    """Resolve a path that must sit inside the sandbox or an approved user root."""
    roots = allowed_roots()
    sandbox = roots["sandbox"]
    if user_path is None or str(user_path).strip() in {"", ".", "./"}:
        resolved = sandbox
    else:
        raw = Path(os.path.expandvars(str(user_path).strip().strip('"')))
        if not raw.is_absolute():
            # Prefer an exact relative match under sandbox, else under each user root.
            candidates = [sandbox / raw]
            for name, root in roots.items():
                if name == "sandbox":
                    continue
                candidates.append(root / raw)
            existing = [c.resolve() for c in candidates if c.exists()]
            resolved = existing[0] if existing else (sandbox / raw).resolve()
        else:
            resolved = raw.resolve()
    if which_root(resolved) is None:
        raise SandboxError(f"Path is outside approved directories: {user_path}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(str(resolved))
    return resolved


def resolve_write_path(user_path: str | Path | None, *, must_exist: bool = False) -> Path:
    """Writes are still confined to the demo sandbox."""
    from desktop_agent.safety.sandbox import resolve_in_sandbox

    return resolve_in_sandbox(user_path, must_exist=must_exist)
