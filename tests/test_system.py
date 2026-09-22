from pathlib import Path

from desktop_agent.tools.registry import build_default_registry
from desktop_agent.tools.system import battery_snapshot, device_snapshot, system_snapshot


def test_battery_status_tool(sandbox):
    snap = battery_snapshot()
    assert "has_battery" in snap
    registry = build_default_registry()
    tool = registry.get("get_battery_status")
    result = tool.run(tool.parse_args({}))
    assert result.ok
    assert "status" in result.data
    if result.data["has_battery"]:
        assert 0 <= result.data["percent"] <= 100


def test_system_and_device_status(sandbox):
    status = system_snapshot()
    assert "cpu_percent" in status
    assert status["memory_total_gb"] > 0
    device = device_snapshot()
    assert device["hostname"]
    registry = build_default_registry()
    sys_tool = registry.get("get_system_status")
    result = sys_tool.run(sys_tool.parse_args({}))
    assert result.ok
    assert "cpu_percent" in result.data
    net = registry.get("get_network_status")
    net_result = net.run(net.parse_args({}))
    assert net_result.ok
    assert "interfaces" in net_result.data


def test_screenshot_saves_in_sandbox(sandbox, monkeypatch):
    root, _ = sandbox
    fake = root / "output" / "screenshots" / "screenshot-test.png"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"png")
    monkeypatch.setattr("desktop_agent.tools.system.take_screenshot", lambda: fake)
    monkeypatch.setattr("desktop_agent.tools.system.os.startfile", lambda path: None)
    registry = build_default_registry()
    tool = registry.get("take_screenshot")
    result = tool.run(tool.parse_args({}))
    assert result.ok
    assert Path(result.data["path"]).exists()


def test_open_windows_settings_uses_protocol(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("desktop_agent.tools.system.os.startfile", lambda target: opened.append(target))
    registry = build_default_registry()
    tool = registry.get("open_windows_settings")
    result = tool.run(tool.parse_args({"page": "wifi"}))
    assert result.ok
    assert opened == ["ms-settings:network-wifi"]
