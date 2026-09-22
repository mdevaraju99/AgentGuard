from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import dateparser
from pydantic import BaseModel, Field

from desktop_agent import config
from desktop_agent.tools.base import Tool, ToolResult, VerificationResult

_LOCK = threading.RLock()
_TIMERS: dict[str, threading.Timer] = {}


class TimerArgs(BaseModel):
    duration: str = Field(description="Relative duration such as '3 minutes' or '90 seconds'")
    message: str = Field(default="Timer finished")


class AlarmArgs(BaseModel):
    when: str = Field(description="Absolute time such as '7 PM', 'tomorrow 9 AM'")
    message: str = Field(default="Alarm")


class ReminderArgs(BaseModel):
    when: str = Field(description="When to remind the user")
    message: str = Field(description="Reminder text")


class EmptyArgs(BaseModel):
    pass


def _jobs_path() -> Path:
    path = config.get_settings().jobs_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_jobs() -> list[dict[str, Any]]:
    path = _jobs_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_jobs(jobs: list[dict[str, Any]]) -> None:
    _jobs_path().write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def _notify(title: str, message: str) -> None:
    print(f"\n[{title}] {message}\n")
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=12)
    except Exception:
        pass
    try:
        import winsound

        winsound.MessageBeep()
    except Exception:
        pass


def _fire(job_id: str) -> None:
    with _LOCK:
        jobs = load_jobs()
        job = next((j for j in jobs if j["id"] == job_id), None)
        if job:
            job["status"] = "fired"
            job["fired_at"] = datetime.now().isoformat(timespec="seconds")
            save_jobs(jobs)
        _TIMERS.pop(job_id, None)
    if job:
        _notify(job.get("kind", "timer").title(), job.get("message") or "Time is up")


def _arm(job: dict[str, Any]) -> None:
    fire_at = datetime.fromisoformat(job["fire_at"])
    delay = (fire_at - datetime.now()).total_seconds()
    if delay <= 0:
        _fire(job["id"])
        return
    timer = threading.Timer(delay, _fire, args=(job["id"],))
    timer.daemon = True
    timer.start()
    _TIMERS[job["id"]] = timer


def restore_jobs() -> None:
    to_arm: list[dict[str, Any]] = []
    with _LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job.get("status") == "scheduled" and job["id"] not in _TIMERS:
                to_arm.append(job)
    for job in to_arm:
        _arm(job)


def parse_when(text: str, *, relative: bool = False) -> datetime:
    settings = {"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()}
    parsed = dateparser.parse(text, settings=settings)
    if parsed is None:
        raise ValueError(f"Could not parse time: {text}")
    if parsed <= datetime.now() and not relative:
        # e.g. "7 PM" after 7 PM → tomorrow
        parsed = dateparser.parse(
            text,
            settings={**settings, "PREFER_DATES_FROM": "future"},
        ) or parsed
        if parsed <= datetime.now():
            from datetime import timedelta

            parsed = parsed + timedelta(days=1)
    return parsed


def _schedule(kind: str, fire_at: datetime, message: str, source: str) -> dict[str, Any]:
    job = {
        "id": uuid4().hex[:10],
        "kind": kind,
        "message": message,
        "source": source,
        "fire_at": fire_at.isoformat(timespec="seconds"),
        "status": "scheduled",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        jobs = load_jobs()
        jobs.append(job)
        save_jobs(jobs)
    _arm(job)
    return job


def build_time_tools() -> list[Tool]:
    def set_timer(args: TimerArgs) -> ToolResult:
        fire_at = parse_when(args.duration, relative=True)
        job = _schedule("timer", fire_at, args.message, args.duration)
        return ToolResult(
            ok=True,
            data=job,
            evidence={"job_id": job["id"], "fire_at": job["fire_at"], "status": job["status"]},
        )

    def set_alarm(args: AlarmArgs) -> ToolResult:
        fire_at = parse_when(args.when)
        job = _schedule("alarm", fire_at, args.message, args.when)
        return ToolResult(ok=True, data=job, evidence={"job_id": job["id"], "fire_at": job["fire_at"]})

    def set_reminder(args: ReminderArgs) -> ToolResult:
        fire_at = parse_when(args.when)
        job = _schedule("reminder", fire_at, args.message, args.when)
        return ToolResult(ok=True, data=job, evidence={"job_id": job["id"], "fire_at": job["fire_at"]})

    def list_timers(_args: EmptyArgs) -> ToolResult:
        jobs = load_jobs()
        return ToolResult(
            ok=True,
            data={"jobs": jobs, "count": len(jobs)},
            evidence={"count": len(jobs), "ids": [j["id"] for j in jobs]},
        )

    def get_current_time(_args: EmptyArgs) -> ToolResult:
        now = datetime.now().astimezone()
        payload = {
            "local_time": now.strftime("%I:%M:%S %p").lstrip("0"),
            "iso": now.isoformat(timespec="seconds"),
            "date": now.strftime("%A, %d %B %Y"),
            "timezone": now.tzname() or now.strftime("%z"),
            "unix": int(now.timestamp()),
        }
        return ToolResult(
            ok=True,
            data=payload,
            evidence={"local_time": payload["local_time"], "date": payload["date"]},
        )

    def verify(_args: BaseModel, result: ToolResult) -> VerificationResult:
        if result.data.get("local_time"):
            return VerificationResult(
                verified=True,
                evidence=result.evidence,
                reason="clock read",
            )
        job_id = result.data.get("id") or result.evidence.get("job_id")
        if result.data.get("jobs") is not None:
            return VerificationResult(verified=True, evidence={"count": result.data.get("count")}, reason="listed")
        jobs = load_jobs()
        found = next((j for j in jobs if j["id"] == job_id), None)
        return VerificationResult(
            verified=found is not None,
            evidence={"job": found},
            reason="job registered" if found else "job missing",
        )

    specs = [
        ("set_timer", "Set a relative timer, e.g. duration='3 minutes'.", TimerArgs, set_timer),
        ("set_alarm", "Set an alarm at an absolute time, e.g. when='7 PM'.", AlarmArgs, set_alarm),
        ("set_reminder", "Remind the user at a time with a message.", ReminderArgs, set_reminder),
        ("list_timers", "List scheduled timers, alarms, and reminders.", EmptyArgs, list_timers),
        (
            "get_current_time",
            "Get this PC's current local date and time. Use for 'what time is it', 'time now', 'what's the date'.",
            EmptyArgs,
            get_current_time,
        ),
    ]
    return [
        Tool(name=name, description=desc, parameters=params, timeout_s=10, handler=fn, verifier=verify)
        for name, desc, params, fn in specs
    ]
