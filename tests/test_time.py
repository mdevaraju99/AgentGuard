from datetime import datetime, timedelta

from desktop_agent.tools.time_tools import load_jobs, parse_when
from desktop_agent.tools.registry import build_default_registry


def test_parse_relative_and_absolute():
    soon = parse_when("3 minutes", relative=True)
    assert soon > datetime.now()
    assert soon < datetime.now() + timedelta(minutes=4)


def test_set_timer_persists(sandbox):
    registry = build_default_registry()
    tool = registry.get("set_timer")
    result = tool.run(tool.parse_args({"duration": "15 minutes", "message": "demo"}))
    assert result.ok
    jobs = load_jobs()
    assert any(j["id"] == result.data["id"] for j in jobs)
    verify = tool.verify(tool.parse_args({"duration": "15 minutes"}), result)
    assert verify.verified


def test_get_current_time(sandbox):
    registry = build_default_registry()
    tool = registry.get("get_current_time")
    result = tool.run(tool.parse_args({}))
    assert result.ok
    assert result.data["local_time"]
    assert result.data["date"]
    verify = tool.verify(tool.parse_args({}), result)
    assert verify.verified


def test_restore_overdue_timer_does_not_deadlock(sandbox):
    import threading

    from desktop_agent.tools.time_tools import load_jobs, restore_jobs, save_jobs

    save_jobs(
        [
            {
                "id": "overdue0",
                "kind": "timer",
                "message": "done",
                "source": "0 seconds",
                "fire_at": (datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds"),
                "status": "scheduled",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        ]
    )
    done = threading.Event()

    def _run() -> None:
        restore_jobs()
        done.set()

    threading.Thread(target=_run, daemon=True).start()
    assert done.wait(5), "restore_jobs deadlocked on overdue timer"
    jobs = load_jobs()
    assert jobs and jobs[0]["status"] == "fired"
