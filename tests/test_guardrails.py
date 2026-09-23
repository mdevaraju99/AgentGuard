from desktop_agent.agent.loop import DesktopAgent
from desktop_agent.agent.state import TaskOutcome
from desktop_agent.llm.provider import LLMResponse, ScriptedLLM
from desktop_agent.safety.guardrails import looks_like_desktop_task, should_allow_tools
from desktop_agent.tools.registry import build_default_registry
from tests.test_agent_loop import _call


def test_small_talk_and_empty_are_not_tasks():
    assert looks_like_desktop_task("") is False
    assert looks_like_desktop_task("   ") is False
    assert looks_like_desktop_task("I'm feeling sad") is False
    assert looks_like_desktop_task("hello") is False
    assert should_allow_tools("I'm feeling sad") is False
    assert should_allow_tools("[voice] I'm feeling sad") is False
    assert should_allow_tools("when did i downloaded it", last_entities={"last_file": r"D:\Downloads\doc.docx"}) is True


def test_real_desktop_requests_are_allowed():
    assert looks_like_desktop_task("what's my battery")
    assert looks_like_desktop_task("Find my AWS PDF")
    assert looks_like_desktop_task("take a screenshot")
    assert looks_like_desktop_task("open Chrome")
    assert looks_like_desktop_task("[voice] meaning of language")
    assert should_allow_tools("open that", last_entities={"last_file": "notes.txt"})


def test_empty_and_feelings_do_not_search_files(sandbox):
    _, settings = sandbox
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[_call("1", "search_files", {"query": "uplifting"})]),
            LLMResponse(tool_calls=[_call("2", "search_files", {"query": "sad"})]),
            LLMResponse(content="should never run"),
        ]
    )
    agent = DesktopAgent(llm=llm, registry=build_default_registry(), settings=settings)
    empty = agent.run("   ", session_id="guard")
    assert empty.steps_taken == []
    assert empty.task_outcome == TaskOutcome.BLOCKED
    sad = agent.run("I'm feeling sad", session_id="guard")
    assert sad.steps_taken == []
    assert sad.task_outcome == TaskOutcome.BLOCKED
    assert "PC" in (sad.final_response or "")
