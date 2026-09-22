from __future__ import annotations

import json

from desktop_agent.agent.loop import DesktopAgent
from desktop_agent.agent.state import TaskOutcome
from desktop_agent.llm.provider import LLMResponse, ScriptedLLM, ToolCall
from desktop_agent.tools.registry import build_default_registry


def _call(oid: str, name: str, arguments: dict) -> ToolCall:
    return ToolCall(id=oid, name=name, arguments=json.dumps(arguments))


def test_m1_find_summarize_save_open(sandbox, monkeypatch):
    root, settings = sandbox
    monkeypatch.setattr("desktop_agent.tools.files._open_path", lambda path: None)

    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[_call("1", "search_files", {"query": "AWS PDF"})]),
            LLMResponse(
                tool_calls=[_call("2", "extract_document", {"path": "documents/AWS_Observability.pdf"})]
            ),
            LLMResponse(
                tool_calls=[
                    _call(
                        "3",
                        "create_file",
                        {
                            "path": "output/aws_summary.txt",
                            "content": "AWS observability uses CloudWatch, X-Ray, and CloudTrail.",
                        },
                    )
                ]
            ),
            LLMResponse(tool_calls=[_call("4", "open_file", {"path": "output/aws_summary.txt"})]),
            LLMResponse(
                content="I found documents/AWS_Observability.pdf, saved the summary, and opened it. The file exists."
            ),
        ]
    )
    agent = DesktopAgent(llm=llm, registry=build_default_registry(), settings=settings)
    state = agent.run(
        "Find the AWS PDF, summarize it, save the summary as aws_summary.txt, and open the file.",
        session_id="m1",
    )
    summary = root / "output" / "aws_summary.txt"
    assert summary.exists()
    assert "CloudWatch" in summary.read_text(encoding="utf-8")
    assert [s.tool_name for s in state.steps_taken] == [
        "search_files",
        "extract_document",
        "create_file",
        "open_file",
    ]
    assert all(s.ok and s.verified for s in state.steps_taken)
    assert state.task_outcome == TaskOutcome.ACHIEVED
    assert any(e.event_type == "tool_end" for e in state.trace_events)
    assert "AWS_Observability.pdf" in state.referenced_files[0]


def test_memory_it_refers_to_last_file(sandbox, monkeypatch):
    monkeypatch.setattr("desktop_agent.tools.files._open_path", lambda path: None)
    _, settings = sandbox
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[_call("1", "search_files", {"query": "AWS PDF"})]),
            LLMResponse(content="I found AWS_Observability.pdf."),
            LLMResponse(
                tool_calls=[_call("2", "extract_document", {"path": "documents/AWS_Observability.pdf"})]
            ),
            LLMResponse(content="It covers CloudWatch and X-Ray."),
        ]
    )
    agent = DesktopAgent(llm=llm, registry=build_default_registry(), settings=settings)
    first = agent.run("Find my AWS PDF.", session_id="mem")
    assert first.referenced_files
    second = agent.run("Summarize it.", session_id="mem")
    assert second.steps_taken[0].tool_name == "extract_document"
    assert second.last_entities.get("last_file")
    user_msgs = [m["content"] for m in second.conversation_history if m["role"] == "user"]
    assert any("AWS_Observability.pdf" in (m or "") for m in user_msgs)


def test_unrelated_turn_does_not_reuse_prior_file_in_trace(sandbox):
    _, settings = sandbox
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[_call("1", "search_files", {"query": "AWS PDF"})]),
            LLMResponse(content="Found it."),
            LLMResponse(tool_calls=[_call("2", "set_alarm", {"when": "in 1 minute", "message": "Alarm"})]),
            LLMResponse(content="Alarm set."),
        ]
    )
    agent = DesktopAgent(llm=llm, registry=build_default_registry(), settings=settings)
    first = agent.run("Find my AWS PDF.", session_id="sticky")
    assert first.referenced_files
    second = agent.run("Set an alarm for 1 minute.", session_id="sticky")
    assert second.referenced_files == []
    assert second.last_entities.get("last_file") == first.last_entities.get("last_file")


def test_blocked_delete_and_injection_not_executed(sandbox):
    _, settings = sandbox
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[_call("1", "extract_document", {"path": "documents/AWS_Observability.pdf"})]),
            LLMResponse(tool_calls=[_call("2", "delete_file", {"path": "documents/AWS_Observability.pdf"})]),
            LLMResponse(content="I will not delete files. The PDF contained an injection attempt."),
        ]
    )
    agent = DesktopAgent(llm=llm, registry=build_default_registry(), settings=settings)
    state = agent.run("Read the AWS PDF.", session_id="inj")
    delete = [s for s in state.steps_taken if s.tool_name == "delete_file"][0]
    assert delete.ok is False
    assert (sandbox[0] / "documents" / "AWS_Observability.pdf").exists()
    assert state.task_outcome in {TaskOutcome.BLOCKED, TaskOutcome.PARTIAL}


def test_timer_and_create_in_one_session(sandbox):
    _, settings = sandbox
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[_call("1", "set_timer", {"duration": "3 minutes", "message": "done"})]),
            LLMResponse(content="Timer set for 3 minutes."),
        ]
    )
    agent = DesktopAgent(llm=llm, registry=build_default_registry(), settings=settings)
    state = agent.run("Set a timer for 3 minutes.", session_id="timer")
    assert state.steps_taken[0].ok
    assert state.steps_taken[0].verified
    assert state.task_outcome == TaskOutcome.ACHIEVED
