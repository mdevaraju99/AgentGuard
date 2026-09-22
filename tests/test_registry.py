from desktop_agent.tools.applications import normalize_apps, resolve_app_id
from desktop_agent.tools.registry import build_default_registry


def test_app_aliases():
    apps = normalize_apps()
    assert resolve_app_id("Google Chrome", apps) == "chrome"
    assert resolve_app_id("VS Code", apps) == "vscode"
    assert resolve_app_id("calc", apps) == "calculator"
    assert resolve_app_id("Microsoft Teams", apps) == "teams"


def test_registry_has_v1_tools():
    names = set(build_default_registry().names())
    expected = {
        "open_application",
        "close_application",
        "is_application_running",
        "is_application_installed",
        "lookup_installed_application",
        "search_files",
        "read_file",
        "create_file",
        "create_folder",
        "open_file",
        "delete_file",
        "extract_document",
        "open_browser",
        "search_web",
        "open_url",
        "read_page",
        "set_timer",
        "set_alarm",
        "set_reminder",
        "list_timers",
        "get_current_time",
        "get_battery_status",
        "get_system_status",
        "get_device_info",
        "get_network_status",
        "list_top_processes",
        "take_screenshot",
        "lock_workstation",
        "open_windows_settings",
        "get_pc_configuration",
        "get_storage_overview",
        "get_file_inventory",
        "get_podman_usage",
        "get_brightness",
        "set_brightness",
        "get_volume",
        "set_volume",
        "set_wifi",
        "set_bluetooth",
        "list_bluetooth_devices",
        "connect_bluetooth_device",
    }
    assert expected <= names
    assert "send_teams_message" not in names
    assert "lookup_person" not in names
