from __future__ import annotations

import pytest

from desktop_agent.config import Settings
from desktop_agent.safety.paths import FileAccessConfig
from desktop_agent.tools.demo_data import ensure_demo_files


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    sandbox_dir = tmp_path / "sandbox"
    home = tmp_path / "userhome"
    documents = home / "Documents"
    downloads = home / "Downloads"
    desktop = home / "Desktop"
    sandbox_dir.mkdir()
    for folder in (documents, downloads, desktop):
        folder.mkdir(parents=True)

    class Override(Settings):
        @property
        def sandbox_root(self):
            return sandbox_dir

        @property
        def jobs_path(self):
            return sandbox_dir / "output" / ".agent_jobs.json"

    override = Override()
    monkeypatch.setattr("desktop_agent.config.get_settings", lambda: override)

    def fake_access() -> FileAccessConfig:
        return FileAccessConfig(
            allow_user_directories=True,
            roots={
                "sandbox": sandbox_dir,
                "documents": documents,
                "downloads": downloads,
                "desktop": desktop,
            },
            catalog={
                "documents": str(documents),
                "downloads": str(downloads),
                "desktop": str(desktop),
                "pictures": str(home / "Pictures"),
                "videos": str(home / "Videos"),
            },
            enabled_user_roots=["documents", "downloads", "desktop"],
            skip_dir_names={"node_modules", ".git", "__pycache__"},
        )

    monkeypatch.setattr("desktop_agent.safety.paths.get_file_access_config", fake_access)
    ensure_demo_files()
    return sandbox_dir, override
