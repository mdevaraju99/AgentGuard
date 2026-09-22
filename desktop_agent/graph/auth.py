from __future__ import annotations

from desktop_agent.config import PROJECT_ROOT

CACHE_PATH = PROJECT_ROOT / ".runtime" / "msal_token_cache.bin"
SCOPES = [
    "User.Read",
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "People.Read",
    "offline_access",
]


class GraphAuthRequired(Exception):
    def __init__(self, question: str, flow: dict | None = None) -> None:
        super().__init__(question)
        self.question = question
        self.flow = flow or {}


def _settings():
    from desktop_agent.config import get_settings

    return get_settings()


def graph_client_id() -> str:
    import os

    settings = _settings()
    return (
        (getattr(settings, "graph_client_id", None) or "")
        or os.getenv("GRAPH_CLIENT_ID")
        or os.getenv("AZURE_CLIENT_ID")
        or ""
    ).strip()


def graph_tenant_id() -> str:
    import os

    settings = _settings()
    return (
        (getattr(settings, "graph_tenant_id", None) or "")
        or os.getenv("GRAPH_TENANT_ID")
        or os.getenv("AZURE_TENANT_ID")
        or "organizations"
    ).strip()


def _msal_app():
    import msal

    client_id = graph_client_id()
    if not client_id:
        return None
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = msal.SerializableTokenCache()
    if CACHE_PATH.exists():
        cache.deserialize(CACHE_PATH.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{graph_tenant_id()}",
        token_cache=cache,
    )
    app._da_cache = cache  # type: ignore[attr-defined]
    return app


def _persist(app) -> None:
    cache = getattr(app, "_da_cache", None)
    if cache is None or not cache.has_state_changed:
        return
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")


def acquire_token_silent() -> str | None:
    app = _msal_app()
    if app is None:
        return None
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _persist(app)
    if result and result.get("access_token"):
        return str(result["access_token"])
    return None


def acquire_token_interactive() -> str:
    app = _msal_app()
    if app is None:
        raise GraphAuthRequired(setup_help())
    result = app.acquire_token_interactive(scopes=SCOPES, prompt="select_account")
    _persist(app)
    if result and result.get("access_token"):
        return str(result["access_token"])
    raise RuntimeError((result or {}).get("error_description") or "Microsoft sign-in failed")


def start_device_login() -> dict:
    app = _msal_app()
    if app is None:
        raise GraphAuthRequired(setup_help())
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError("Could not start Microsoft device login")
    return flow


def finish_device_login(flow: dict) -> str:
    app = _msal_app()
    if app is None:
        raise GraphAuthRequired(setup_help())
    result = app.acquire_token_by_device_flow(flow)
    _persist(app)
    if result and result.get("access_token"):
        return str(result["access_token"])
    raise RuntimeError((result or {}).get("error_description") or "Device login did not complete")


def setup_help() -> str:
    return (
        "Teams send uses the Microsoft Graph API, so a one-time Microsoft 365 sign-in is required. "
        "In Entra ID create an App registration (public client / mobile and desktop), set Redirect URI "
        "http://localhost, enable Allow public client flows, and add delegated permissions "
        "Chat.ReadWrite, ChatMessage.Send, People.Read, User.Read, offline_access. "
        "Put the Application (client) ID in .env as GRAPH_CLIENT_ID= then Reload agent and try again. "
        "A Microsoft login window will open; use the same work account as Teams."
    )


def get_access_token(*, interactive: bool = True, device_flow: dict | None = None) -> str:
    token = acquire_token_silent()
    if token:
        return token
    if device_flow:
        return finish_device_login(device_flow)
    if not graph_client_id():
        raise GraphAuthRequired(setup_help())
    if interactive:
        return acquire_token_interactive()
    flow = start_device_login()
    raise GraphAuthRequired(
        f"Sign in to Microsoft 365 to send Teams messages.\n"
        f"Open {flow.get('verification_uri')} and enter code {flow.get('user_code')}.\n"
        f"Then send: done",
        flow=flow,
    )
