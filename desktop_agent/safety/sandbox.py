from __future__ import annotations

from pathlib import Path

from desktop_agent import config


class SandboxError(PermissionError):
    pass


def sandbox_root() -> Path:
    root = config.get_settings().sandbox_root
    root.mkdir(parents=True, exist_ok=True)
    (root / "documents").mkdir(exist_ok=True)
    (root / "output").mkdir(exist_ok=True)
    (root / "test_data").mkdir(exist_ok=True)
    return root


def resolve_in_sandbox(user_path: str | Path | None, *, must_exist: bool = False) -> Path:
    """Resolve a user-supplied path and reject anything outside the demo sandbox."""
    root = sandbox_root()
    if user_path is None or str(user_path).strip() in {"", ".", "./"}:
        resolved = root
    else:
        raw = Path(str(user_path).strip().strip('"'))
        candidate = raw if raw.is_absolute() else root / raw
        resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SandboxError(f"Path is outside the allowed sandbox: {user_path}") from exc
    if must_exist and not resolved.exists():
        raise FileNotFoundError(str(resolved))
    return resolved


def relative_to_sandbox(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(sandbox_root())).replace("\\", "/")
    except ValueError:
        return str(path)
