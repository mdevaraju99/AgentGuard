from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    llm_provider: str = Field(default="openai")
    llm_model: str = Field(default="gpt-4o-mini")
    llm_base_url: str | None = None
    openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2024-08-01-preview"

    max_steps: int = 12
    max_retries: int = 2
    tool_timeout_s: float = 90
    llm_temperature: float = 0.1
    extract_max_chars: int = 80_000
    playwright_headless: bool = True
    search_engine: str = "duckduckgo"
    sandbox_relpath: str = "AI-Agent-Demo"
    jobs_filename: str = ".agent_jobs.json"
    graph_client_id: str | None = None
    graph_tenant_id: str = "organizations"

    @property
    def sandbox_root(self) -> Path:
        return (PROJECT_ROOT / self.sandbox_relpath).resolve()

    @property
    def jobs_path(self) -> Path:
        return self.sandbox_root / "output" / self.jobs_filename

    def llm_api_key(self) -> str | None:
        if self.llm_provider == "azure":
            return self.azure_openai_api_key or os.getenv("AZURE_OPENAI_API_KEY")
        return self.openai_api_key or os.getenv("OPENAI_API_KEY")


def apply_yaml(settings: Settings) -> Settings:
    raw = _load_yaml(PROJECT_ROOT / "config" / "settings.yaml")
    updates = {k: v for k, v in raw.items() if hasattr(settings, k) and v is not None}
    if updates:
        return settings.model_copy(update=updates)
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return apply_yaml(Settings())


def load_apps_config() -> dict:
    return _load_yaml(PROJECT_ROOT / "config" / "apps.yaml")


def reset_settings_cache() -> None:
    cached = globals().get("get_settings")
    if hasattr(cached, "cache_clear"):
        cached.cache_clear()
