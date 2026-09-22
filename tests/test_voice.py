from desktop_agent.interfaces.voice import extract_spoken_command, is_stop_phrase, speakable


def test_wake_phrase_extracts_command():
    assert extract_spoken_command("hey agent what time is it") == "what time is it"
    assert extract_spoken_command("OK Agent lock my PC") == "lock my pc"
    assert extract_spoken_command("hey agent") == ""
    assert extract_spoken_command("what time is it") is None


def test_stop_phrase_and_speakable():
    from desktop_agent.interfaces.voice import _speech_chunks, voice_task_hint

    assert is_stop_phrase("please stop listening now")
    assert "link" in speakable("See https://example.com/docs for more")
    assert "here" in speakable("You can check [here](https://example.com)")
    assert "http" not in speakable("You can check [here](https://example.com)")
    assert speakable("**Done.**") == "Done."
    assert voice_task_hint("what is the meaning of meeting").count("search_web") == 1
    assert "open_application" in voice_task_hint("open Chrome")
    assert "get_podman_usage" in voice_task_hint("how much podman space is used")
    assert "get_podman_usage" in voice_task_hint("storage occupied for the parliament")


def test_live_voice_trace_is_memory_only():
    from desktop_agent.session_log import append_turn, clear_turns, snapshot

    clear_turns()
    append_turn(source="voice", user="what's the time", assistant="It is noon.", trace={"steps": []})
    turns = snapshot()
    assert len(turns) == 1
    assert turns[0]["user"] == "what's the time"
    clear_turns()
    assert snapshot() == []



def test_voice_default_is_bubble():
    import inspect

    from desktop_agent.interfaces.voice import main

    source = inspect.getsource(main)
    assert "run_bubble" in source
    assert "--wake" in source
