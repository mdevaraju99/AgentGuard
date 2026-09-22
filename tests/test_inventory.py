from __future__ import annotations

import json
from pathlib import Path

from desktop_agent.tools.inventory import (
    _count_tree,
    _parse_df_bytes,
    _parse_wsl_list,
    directory_usage,
    file_inventory_snapshot,
    pc_configuration_snapshot,
    storage_overview,
)
from desktop_agent.tools.registry import build_default_registry


def test_storage_overview_lists_local_volumes():
    data = storage_overview()
    assert data["volume_count"] >= 1
    assert data["local_total_gb"] > 0
    assert data["local_free_gb"] >= 0
    assert all("free_gb" in row and "total_gb" in row for row in data["volumes"])
    assert "Combined local storage" in data["summary"]


def test_pc_configuration_reports_hardware():
    data = pc_configuration_snapshot()
    assert data["ram_total_gb"] > 0
    assert data["hostname"]
    assert data["summary"]
    registry = build_default_registry()
    tool = registry.get("get_pc_configuration")
    result = tool.run(tool.parse_args({}))
    assert result.ok
    assert result.data["logical_cpus"] >= 1


def test_file_inventory_counts_user_roots(sandbox, monkeypatch):
    root, _ = sandbox
    (root / "hello.txt").write_text("x", encoding="utf-8")
    nested = root / "reports"
    nested.mkdir()
    (nested / "note.txt").write_text("y", encoding="utf-8")
    monkeypatch.setattr(
        "desktop_agent.tools.inventory.storage_overview",
        lambda: {"volumes": []},
    )
    payload = file_inventory_snapshot("user")
    sandbox_row = next(item for item in payload["user_libraries"] if item["id"] == "sandbox")
    assert sandbox_row["files"] >= 2
    assert sandbox_row["folders"] >= 1
    registry = build_default_registry()
    tool = registry.get("get_file_inventory")
    result = tool.run(tool.parse_args({"scope": "user"}))
    assert result.ok
    assert result.data["files"] >= 2


def test_count_tree_skips_named_dirs_and_respects_budget(tmp_path):
    keep = tmp_path / "keep"
    skip = tmp_path / "node_modules"
    keep.mkdir()
    skip.mkdir()
    (keep / "a.txt").write_text("a", encoding="utf-8")
    (skip / "b.txt").write_text("b", encoding="utf-8")
    counted = _count_tree(tmp_path, {"node_modules"}, [1000], deadline=10**12)
    assert counted["files"] == 1
    assert counted["skipped_dirs"] == 1
    tiny = _count_tree(tmp_path, set(), [1], deadline=10**12)
    assert tiny["truncated"] is True


def test_directory_usage_sums_files(tmp_path):
    (tmp_path / "one.bin").write_bytes(b"abcd")
    usage = directory_usage(tmp_path)
    assert usage["exists"] is True
    assert usage["bytes"] == 4
    assert usage["files"] == 1


def test_parse_wsl_and_df_helpers():
    distros = _parse_wsl_list(
        "  NAME                      STATE           VERSION\n"
        "* podman-machine-default    Running         2\n"
        "  podman-neo4j-machine      Stopped         2\n"
    )
    assert distros[0]["name"] == "podman-machine-default"
    assert distros[0]["state"] == "Running"
    assert distros[1]["name"] == "podman-neo4j-machine"
    parsed = _parse_df_bytes(
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        "/dev/sdd 104857600 41943040 62914560 40% /\n"
    )
    assert parsed is not None
    assert parsed["total_gb"] == 100.0
    assert parsed["used_gb"] == 40.0


def test_podman_usage_splits_default_and_poc(monkeypatch):
    machines = [
        {
            "Name": "podman-machine-default",
            "Running": True,
            "VMType": "wsl",
            "CPUs": 4,
            "Memory": "2147483648",
            "DiskSize": "107374182400",
            "LastUp": "now",
            "Created": "then",
            "UserModeNetworking": False,
        },
        {
            "Name": "neo4j-machine",
            "Running": False,
            "VMType": "wsl",
            "CPUs": 2,
            "Memory": "4294967296",
            "DiskSize": "32212254720",
            "LastUp": "ago",
            "Created": "then",
            "UserModeNetworking": True,
        },
    ]

    def fake_run(argv, *, timeout=20):
        if argv[:3] == ["podman", "machine", "list"]:
            return 0, json.dumps(machines), ""
        if argv[:2] == ["wsl", "-l"]:
            return (
                0,
                "NAME STATE VERSION\n"
                "podman-machine-default Running 2\n"
                "podman-neo4j-machine Stopped 2\n"
                "podman-net-usermode Stopped 2\n",
                "",
            )
        if "df" in argv:
            return 0, "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/sdd 104857600 41943040 62914560 40% /\n", ""
        if "images" in argv:
            return 0, json.dumps([{"Names": ["python:3.11-slim"], "Size": 129000000, "Containers": 0}]), ""
        if "ps" in argv:
            return 0, "[]", ""
        if "volume" in argv:
            return 0, "[]", ""
        if "system" in argv:
            return 0, "TYPE TOTAL ACTIVE SIZE\nImages 2 0 129MB", ""
        return 1, "", "unexpected: " + " ".join(argv)

    monkeypatch.setattr("desktop_agent.tools.inventory.run_command", fake_run)
    monkeypatch.setattr("desktop_agent.tools.inventory.shutil.which", lambda name: f"C:\\bin\\{name}")
    monkeypatch.setattr(
        "desktop_agent.tools.inventory.directory_usage",
        lambda path, max_files=20000: {
            "path": str(path),
            "exists": True,
            "bytes": 80 * 1024**3,
            "gb": 80.0,
            "files": 3,
            "truncated": False,
        },
    )
    monkeypatch.setattr(
        "desktop_agent.tools.inventory.find_vhdx",
        lambda root, max_depth=6: [
            {
                "path": str(Path(root) / "wsldist" / "podman-machine-default" / "ext4.vhdx"),
                "gb": 54.0,
                "bytes": 54 * 1024**3,
                "name": "ext4.vhdx",
            },
            {
                "path": str(Path(root) / "wsldist" / "neo4j-machine" / "ext4.vhdx"),
                "gb": 22.0,
                "bytes": 22 * 1024**3,
                "name": "ext4.vhdx",
            },
            {
                "path": str(Path(root) / "wsldist" / "podman-net-usermode" / "ext4.vhdx"),
                "gb": 1.17,
                "bytes": int(1.17 * 1024**3),
                "name": "ext4.vhdx",
            },
        ],
    )
    registry = build_default_registry()
    tool = registry.get("get_podman_usage")
    result = tool.run(tool.parse_args({}))
    assert result.ok
    names = {item["name"] for item in result.data["machines"]}
    assert names == {"podman-machine-default", "neo4j-machine"}
    poc = result.data["poc_storage"]
    assert any(item["name"] == "neo4j-machine" for item in poc)
    default = next(item for item in result.data["machines"] if item["name"] == "podman-machine-default")
    assert default["running"] is True
    assert default["allocated_disk_gb"] == 100.0
    assert default["host_vhdx_gb"] == 54.0
    assert default["inside"]["root_fs"]["used_gb"] == 40.0
    neo = next(item for item in result.data["machines"] if item["name"] == "neo4j-machine")
    assert neo["role"] == "poc_storage"
    assert neo["inside"]["reachable"] is False
    extras = {item["name"] for item in result.data["extra_wsl"]}
    assert "podman-net-usermode" in extras
    assert "80" in result.data["summary"] or "54" in result.data["summary"]
