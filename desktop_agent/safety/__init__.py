from desktop_agent.safety.permissions import PermissionDenied, check_permission
from desktop_agent.safety.paths import describe_allowed_roots, resolve_allowed_path
from desktop_agent.safety.sandbox import SandboxError, resolve_in_sandbox, sandbox_root
from desktop_agent.safety.untrusted import wrap_untrusted

__all__ = [
    "PermissionDenied",
    "check_permission",
    "SandboxError",
    "resolve_in_sandbox",
    "resolve_allowed_path",
    "describe_allowed_roots",
    "sandbox_root",
    "wrap_untrusted",
]
