from desktop_agent.tools.controls import _clamp_percent, _compact, _match_device
from desktop_agent.tools.registry import build_default_registry


def test_clamp_and_name_match():
    assert _clamp_percent(140) == 100
    assert _clamp_percent(-5) == 0
    assert _compact("SOUNDPEATS TrueDot") == "soundpeatstruedot"
    hits = _match_device(
        "soundpeats",
        [{"name": "SOUNDPEATS TrueDot"}, {"name": "Noise Buds VS102"}],
    )
    assert [item["name"] for item in hits] == ["SOUNDPEATS TrueDot"]


def test_control_tools_are_registered():
    names = set(build_default_registry().names())
    for name in (
        "get_brightness",
        "set_brightness",
        "get_volume",
        "set_volume",
        "set_wifi",
        "set_bluetooth",
        "list_bluetooth_devices",
        "connect_bluetooth_device",
    ):
        assert name in names


def test_brightness_and_volume_get_tools(monkeypatch):
    monkeypatch.setattr(
        "desktop_agent.tools.controls.brightness_snapshot",
        lambda: {"percent": 80, "monitors": [{"percent": 80, "active": True}], "summary": "Screen brightness is 80%."},
    )
    monkeypatch.setattr(
        "desktop_agent.tools.controls.volume_snapshot",
        lambda: {"percent": 40, "muted": False, "summary": "Speaker volume is 40%."},
    )
    registry = build_default_registry()
    bright = registry.get("get_brightness").run(registry.get("get_brightness").parse_args({}))
    assert bright.ok and bright.data["percent"] == 80
    vol = registry.get("get_volume").run(registry.get("get_volume").parse_args({}))
    assert vol.ok and vol.data["percent"] == 40


def test_set_wifi_and_bluetooth_status(monkeypatch):
    monkeypatch.setattr(
        "desktop_agent.tools.controls.set_wifi",
        lambda action: {"action": action, "state": "On", "on": True, "summary": "Wi-Fi radio is On.", "adapters": []},
    )
    monkeypatch.setattr(
        "desktop_agent.tools.controls.set_bluetooth_radio",
        lambda action: {"action": action, "state": "Off", "on": False, "summary": "Bluetooth radio is Off."},
    )
    registry = build_default_registry()
    wifi = registry.get("set_wifi").run(registry.get("set_wifi").parse_args({"action": "status"}))
    assert wifi.ok and wifi.data["on"] is True
    bt = registry.get("set_bluetooth").run(registry.get("set_bluetooth").parse_args({"action": "status"}))
    assert bt.ok and bt.data["on"] is False


def test_connect_bluetooth_reports_saved_not_nearby(monkeypatch):
    monkeypatch.setattr(
        "desktop_agent.tools.controls.saved_bluetooth_devices",
        lambda: [{"name": "SOUNDPEATS TrueDot", "address": "1C919D0C7B7E"}],
    )
    monkeypatch.setattr(
        "desktop_agent.tools.controls.radio_control",
        lambda kind, action: {"on": True, "state": "On", "summary": "Bluetooth radio is On."},
    )
    monkeypatch.setattr("desktop_agent.tools.controls.nearby_paired_bluetooth", lambda: [])
    monkeypatch.setattr("desktop_agent.tools.controls._connect_audio_ids", lambda ids: [])
    registry = build_default_registry()
    tool = registry.get("connect_bluetooth_device")
    result = tool.run(tool.parse_args({"name": "SOUNDPEATS"}))
    assert "not nearby" in result.data["summary"].lower()
    assert result.data["saved"][0]["name"] == "SOUNDPEATS TrueDot"
