from __future__ import annotations

from desktop_agent.agent.loop import DesktopAgent
from desktop_agent.config import get_settings
from desktop_agent.llm.provider import build_provider_from_settings
from desktop_agent.safety.paths import reset_file_access_cache
from desktop_agent.safety.sandbox import sandbox_root
from desktop_agent.tools.demo_data import ensure_demo_files
from desktop_agent.tools.registry import build_default_registry
from desktop_agent.tools.time_tools import restore_jobs


def build_agent() -> DesktopAgent:
    reset_file_access_cache()
    sandbox_root()
    ensure_demo_files()
    restore_jobs()
    return DesktopAgent(
        llm=build_provider_from_settings(),
        registry=build_default_registry(),
        settings=get_settings(),
    )
