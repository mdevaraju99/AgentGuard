from __future__ import annotations

import argparse
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WAKE_PHRASES = (
    "hey agent",
    "ok agent",
    "okay agent",
    "hello agent",
    "hi agent",
)
STOP_PHRASES = (
    "stop listening",
    "goodbye agent",
    "good bye agent",
    "cancel voice",
    "stop voice",
)


def _compact_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_spoken_command(transcript: str) -> str | None:
    """Return the command after a wake phrase, or None if this was not a wake."""
    text = _compact_space(transcript)
    if not text:
        return None
    for phrase in WAKE_PHRASES:
        if text == phrase:
            return ""
        prefix = phrase + " "
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return None


def is_stop_phrase(transcript: str) -> bool:
    text = _compact_space(transcript)
    return any(p in text for p in STOP_PHRASES)


def speakable(text: str) -> str:
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
    cleaned = re.sub(r"[#*_`]+", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "a link", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 420:
        cleaned = cleaned[:417] + "..."
    return cleaned or "Done."


def _beep() -> None:
    try:
        import winsound

        winsound.Beep(880, 120)
    except Exception:
        pass


def _mic_settings() -> tuple[int | None, int, str]:
    import sounddevice as sd

    try:
        info = sd.query_devices(kind="input")
        index = int(info["index"])
        rate = int(info.get("default_samplerate") or 44100)
        if rate < 8000:
            rate = 44100
        return index, rate, str(info.get("name") or "default")
    except Exception:
        return None, 44100, "default"


def _record_seconds(seconds: float, samplerate: int | None = None) -> "object":
    import numpy as np
    import sounddevice as sd
    import speech_recognition as sr

    device, native_rate, _name = _mic_settings()
    rate = samplerate or native_rate
    frames = int(seconds * rate)
    audio = sd.rec(frames, samplerate=rate, channels=1, dtype="float32", device=device)
    sd.wait()
    pcm = (np.clip(audio.reshape(-1), -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return sr.AudioData(pcm, rate, 2)


def _rms(data) -> float:
    import numpy as np

    return float(np.sqrt(np.mean(np.square(data)) + 1e-12))


def _to_audio_data(chunks, samplerate: int):
    import numpy as np
    import speech_recognition as sr

    if not chunks:
        empty = np.zeros(int(0.4 * samplerate), dtype=np.int16).tobytes()
        return sr.AudioData(empty, samplerate, 2)
    audio = np.concatenate([c.reshape(-1) for c in chunks]).astype(np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1e-4:
        audio = audio * min(0.85 / peak, 10.0)
        print(f"Mic gain applied (peak={peak:.4f})", flush=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return sr.AudioData(pcm, samplerate, 2)


def _record_until_silence(
    *,
    samplerate: int | None = None,
    max_seconds: float = 12.0,
    silence_seconds: float = 1.5,
    start_threshold: float = 0.012,
    stop_event=None,
    capture_immediately: bool = False,
    max_wait_for_speech: float = 12.0,
    max_utterance: float | None = None,
    on_status=None,
) -> "object":
    """Tap-to-talk: wait for real speech, then stop after a pause."""
    import queue
    import numpy as np
    import sounddevice as sd

    device, native_rate, mic_name = _mic_settings()
    rate = int(samplerate or native_rate)
    hop = 0.1
    hop_frames = max(1, int(hop * rate))
    start_voiced = 2
    needed_silence = max(10, int(silence_seconds / hop))
    wait_chunks = max(1, int((max_wait_for_speech or max_seconds) / hop))
    speak_chunks = max(1, int((max_utterance or max_seconds) / hop))
    preroll_max = 8
    preroll: list = []
    collected: list = []

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def _notify(message: str) -> None:
        if on_status is None:
            return
        try:
            on_status(message)
        except Exception:
            pass

    incoming: queue.Queue = queue.Queue()
    leftover = np.zeros((0, 1), dtype=np.float32)
    empty_reads = 0

    def _callback(indata, frames, _time, status) -> None:
        if status:
            print(f"Mic status: {status}", flush=True)
        incoming.put(indata.copy())

    def _next_from_queue(timeout: float = 0.4):
        nonlocal leftover, empty_reads
        while leftover.shape[0] < hop_frames:
            if _stopped():
                return None
            try:
                piece = incoming.get(timeout=timeout)
            except queue.Empty:
                empty_reads += 1
                if empty_reads >= 4:
                    raise RuntimeError("mic stream stalled")
                return None
            empty_reads = 0
            leftover = np.concatenate([leftover, np.asarray(piece, dtype=np.float32)], axis=0)
        chunk = leftover[:hop_frames].copy()
        leftover = leftover[hop_frames:]
        return chunk

    def _next_from_rec():
        data = sd.rec(
            hop_frames,
            samplerate=rate,
            channels=1,
            dtype="float32",
            device=device,
        )
        sd.wait()
        return data.copy()

    def _vad_loop(next_chunk):
        nonlocal preroll, collected
        # Drop the tap/click so it is not treated as speech.
        for _ in range(3):
            if _stopped():
                return
            next_chunk()
        noise: list[float] = []
        for _ in range(5):
            if _stopped():
                return
            sample = next_chunk()
            if sample is None:
                continue
            noise.append(_rms(sample))
            preroll.append(sample)
        if not noise:
            return
        floor = sorted(noise)[len(noise) // 2]
        threshold = max(0.006, min(start_threshold, floor * 2.5 + 0.004))
        print(
            f"Mic ready ({mic_name} @ {rate} Hz). noise={floor:.4f} "
            f"threshold={threshold:.4f} — speak now",
            flush=True,
        )
        _notify("Listening…")

        waiting = not capture_immediately
        voiced_run = start_voiced if capture_immediately else 0
        silent_run = 0
        speech_chunks = 0
        wait_used = 0
        speak_used = 0
        if capture_immediately:
            collected.extend(preroll)
            _notify("Listening…")

        while True:
            if _stopped():
                return
            if waiting and wait_used >= wait_chunks:
                print("No speech started in time", flush=True)
                collected = []
                return
            if (not waiting) and speak_used >= speak_chunks:
                return
            sample = next_chunk()
            if sample is None:
                if waiting:
                    wait_used += 1
                continue
            voiced = _rms(sample) >= threshold
            if waiting:
                wait_used += 1
                preroll.append(sample)
                if len(preroll) > preroll_max:
                    preroll.pop(0)
                voiced_run = voiced_run + 1 if voiced else 0
                if voiced_run >= start_voiced:
                    waiting = False
                    collected.extend(preroll)
                    silent_run = 0
                    speech_chunks = voiced_run
                    print("Heard speech — keep talking", flush=True)
                    _notify("Listening…")
                continue
            speak_used += 1
            collected.append(sample)
            if voiced:
                silent_run = 0
                speech_chunks += 1
                continue
            silent_run += 1
            if silent_run >= needed_silence and len(collected) >= 8:
                return

    used_fallback = False
    try:
        stream = sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            device=device,
            latency="high",
            callback=_callback,
        )
        with stream:
            _vad_loop(_next_from_queue)
    except Exception as exc:
        print(f"Live mic stream failed ({exc}); using short clips", flush=True)
        used_fallback = True
        preroll = []
        collected = []
        leftover = np.zeros((0, 1), dtype=np.float32)
        _vad_loop(_next_from_rec)

    duration = len(collected) * hop
    print(
        f"Captured {duration:.1f}s of audio{' (clip fallback)' if used_fallback else ''}",
        flush=True,
    )
    if duration < 0.5:
        return _to_audio_data([], rate)
    return _to_audio_data(collected, rate)


def _audio_seconds(audio, samplerate: int | None = None) -> float:
    try:
        rate = samplerate or getattr(audio, "sample_rate", 16000) or 16000
        return len(audio.get_raw_data()) / float(2 * rate)
    except Exception:
        return 0.0


def transcribe(audio) -> str:
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    try:
        return (recognizer.recognize_google(audio) or "").strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as exc:
        raise RuntimeError(f"Speech service failed: {exc}") from exc


def _speech_chunks(text: str, max_len: int = 160) -> list[str]:
    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+", speakable(text)) if p.strip()]
    if not pieces:
        return ["Done."]
    chunks: list[str] = []
    for piece in pieces:
        if len(piece) <= max_len:
            chunks.append(piece)
            continue
        words = piece.split()
        buf: list[str] = []
        for word in words:
            trial = (" ".join(buf + [word])).strip()
            if buf and len(trial) > max_len:
                chunks.append(" ".join(buf))
                buf = [word]
            else:
                buf.append(word)
        if buf:
            chunks.append(" ".join(buf))
    return chunks


def _speak_powershell(text: str, stop_event=None) -> None:
    import subprocess
    import tempfile
    from pathlib import Path

    spoken = speakable(text)
    tmp = Path(tempfile.gettempdir()) / "desktop_agent_tts.txt"
    tmp.write_text(spoken, encoding="utf-8")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SetOutputToDefaultAudioDevice(); "
        "$s.Volume = 100; "
        "$s.Rate = 0; "
        f"$t = Get-Content -Raw -Encoding UTF8 '{tmp}'; "
        "$s.Speak($t)"
    )
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while proc.poll() is None:
            if stop_event is not None and stop_event.is_set():
                proc.terminate()
                return
            time.sleep(0.12)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


class _SpeechWorker:
    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="desktop-agent-tts", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self) -> None:
        engine = None
        try:
            import pythoncom

            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 185)
            voices = engine.getProperty("voices") or []
            if voices:
                engine.setProperty("voice", voices[0].id)
        except Exception as exc:
            print(f"TTS engine init failed: {exc}", flush=True)
            engine = None
        self._ready.set()
        while True:
            job = self._q.get()
            if job is None:
                break
            text, stop_event, done = job
            try:
                self._say(engine, text, stop_event)
            except Exception as exc:
                print(f"TTS failed ({exc}); retrying Windows voice", flush=True)
                try:
                    _speak_powershell(text, stop_event=stop_event)
                except Exception as exc2:
                    print(f"Windows voice failed: {exc2}", flush=True)
            finally:
                done.set()

    def _say(self, engine, text: str, stop_event) -> None:
        # Windows SAPI on the default speakers/headset is more reliable than
        # pyttsx3 from a background thread (that path often stays silent).
        try:
            _speak_powershell(text, stop_event=stop_event)
            return
        except Exception as exc:
            print(f"Windows voice failed ({exc})", flush=True)
        if engine is None:
            raise RuntimeError("No TTS engine")
        for part in _speech_chunks(text):
            if stop_event is not None and stop_event.is_set():
                break
            engine.say(part)
            engine.runAndWait()

    def speak(self, text: str, stop_event=None) -> bool:
        done = threading.Event()
        self._q.put((text, stop_event, done))
        while not done.wait(timeout=0.25):
            if stop_event is not None and stop_event.is_set():
                done.wait(timeout=8)
                return False
        return not (stop_event is not None and stop_event.is_set())


_SPEECH: _SpeechWorker | None = None


def _speech_worker() -> _SpeechWorker:
    global _SPEECH
    if _SPEECH is None:
        _SPEECH = _SpeechWorker()
    return _SPEECH


def speak(text: str, stop_event=None) -> bool:
    """Speak text. Returns False if the user interrupted it."""
    spoken = speakable(text)
    print(f"Speaking: {spoken}", flush=True)
    try:
        return _speech_worker().speak(spoken, stop_event=stop_event)
    except Exception as exc:
        print(f"Speech worker failed ({exc}); using Windows voice", flush=True)
        try:
            _speak_powershell(spoken, stop_event=stop_event)
            return True
        except Exception as exc2:
            print(f"No spoken reply: {exc2}", flush=True)
            return False


def voice_task_hint(command: str) -> str:
    """Steer spoken requests toward the right tools."""
    text = _compact_space(command)
    notes: list[str] = []
    if re.search(r"\b(meaning|definition|google|look up|lookup)\b", text) or "search the web" in text:
        notes.append("Use search_web to open Chrome with a Google search. Do not use search_files.")
    if re.search(r"\bchrome\b", text) and re.search(r"\b(open|launch|start|bring)\b", text):
        notes.append("Call open_application with chrome so Chrome is launched or focused.")
    if re.search(r"\b(podman|pod man|portman|parliament|container|wsl)\b", text) or (
        "neo4j" in text and re.search(r"\b(space|storage|disk|usage)\b", text)
    ):
        notes.append("Call get_podman_usage for Podman/container disk usage, not get_storage_overview.")
    if not notes:
        return command
    return command + " " + " ".join(notes)


def _handle_command(agent, command: str) -> str:
    if is_stop_phrase(command):
        return "__stop__"
    if not command.strip():
        return "I'm listening."
    state = agent.run(f"[voice] {voice_task_hint(command)}", session_id="voice")
    reply = state.final_response or "Done."
    try:
        from desktop_agent.session_log import append_turn, trace_from_state

        append_turn(
            source="voice",
            user=command,
            assistant=reply,
            trace=trace_from_state(state),
        )
    except Exception as exc:
        print(f"Could not save voice turn: {exc}", flush=True)
    return reply


def run_voice_loop() -> int:
    from desktop_agent.factory import build_agent

    print("Loading desktop agent for voice...")
    agent = build_agent()
    print("Say 'hey agent' then your command. Example: hey agent, what's the time?")
    print("Say 'stop listening' to quit. Ctrl+C also quits.")
    speak("Voice mode on. Say hey agent.")
    while True:
        try:
            wake_audio = _record_seconds(2.2)
            heard = transcribe(wake_audio)
        except KeyboardInterrupt:
            print("\nVoice mode stopped.")
            return 0
        except Exception as exc:
            print(f"Listen error: {exc}")
            time.sleep(0.4)
            continue
        if not heard:
            continue
        print(f"Heard: {heard}")
        if is_stop_phrase(heard):
            speak("Voice mode off.")
            return 0
        command = extract_spoken_command(heard)
        if command is None:
            continue
        if not command:
            _beep()
            speak("Yes?")
            try:
                follow = transcribe(_record_until_silence())
            except Exception:
                follow = ""
            print(f"Command: {follow}")
            command = follow
        if is_stop_phrase(command or ""):
            speak("Voice mode off.")
            return 0
        if not (command or "").strip():
            speak("I didn't catch a command.")
            continue
        print(f"Running: {command}")
        try:
            reply = _handle_command(agent, command)
        except Exception as exc:
            reply = f"I hit an error: {exc}"
        if reply == "__stop__":
            speak("Voice mode off.")
            return 0
        print(f"Agent: {reply}\n")
        speak(reply)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hands-free voice mode for the desktop agent")
    parser.add_argument(
        "--wake",
        action="store_true",
        help="Always-listen for 'hey agent' instead of the click-to-talk bubble",
    )
    args = parser.parse_args(argv)
    try:
        import pyttsx3  # noqa: F401
        import sounddevice  # noqa: F401
        import speech_recognition  # noqa: F401
    except ImportError:
        print(
            "Voice packages are missing. From the project folder run:\n"
            r"  .\.venv\Scripts\python.exe -m pip install SpeechRecognition pyttsx3 sounddevice"
        )
        return 1
    if os.name != "nt":
        print("Voice mode is intended for this Windows laptop.")
    if args.wake:
        return run_voice_loop()
    from desktop_agent.interfaces.voice_bubble import run_bubble

    return run_bubble()


if __name__ == "__main__":
    raise SystemExit(main())
