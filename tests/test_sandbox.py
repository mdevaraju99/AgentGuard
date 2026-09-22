from pathlib import Path

import pytest

from desktop_agent.safety.sandbox import SandboxError, relative_to_sandbox, resolve_in_sandbox, sandbox_root


def test_sandbox_creates_layout(sandbox):
    root, _ = sandbox
    assert sandbox_root() == root.resolve()
    assert (root / "documents").is_dir()


def test_relative_path_ok(sandbox):
    path = resolve_in_sandbox("documents/AWS_Observability.pdf")
    assert path.exists()
    assert relative_to_sandbox(path) == "documents/AWS_Observability.pdf"


def test_rejects_escape(sandbox):
    with pytest.raises(SandboxError):
        resolve_in_sandbox(r"..\..\Windows\System32")
    with pytest.raises(SandboxError):
        resolve_in_sandbox(Path("C:/Windows/System32/drivers"))
