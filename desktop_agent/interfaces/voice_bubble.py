from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IDLE = "idle"
LISTENING = "listening"
WORKING = "working"
SPEAKING = "speaking"

BG = "#1A1430"
GRIP_BG = "#3B1D63"
GRIP_FG = "#F0ABFC"
TEXT = "#F8EFFF"

ORB = {
    IDLE: ("#F0ABFC", "#818CF8", "#38BDF8"),
    LISTENING: ("#FB7185", "#E879F9", "#C084FC"),
    WORKING: ("#FBBF24", "#A78BFA", "#60A5FA"),
    SPEAKING: ("#34D399", "#67E8F9", "#A78BFA"),
}

HINTS = {
    IDLE: "Tap to talk",
    LISTENING: "Listening…",
    WORKING: "On it…",
    SPEAKING: "Tap to stop",
}


class VoiceBubble:
    def __init__(self, agent) -> None:
        import tkinter as tk

        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.agent = agent
        self.state = IDLE
        self.stop_event = threading.Event()
        self.speak_stop = threading.Event()
        self._listen_started = 0.0
        self.busy = threading.Lock()
        self._ui_q: queue.Queue = queue.Queue()
        self._drag = {"ox": 0, "oy": 0, "moved": False, "from_grip": False}

        self.root = tk.Tk()
        self.root.title("AI Agent")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        width, height = 128, 176
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+{max(20, sw - width - 36)}+{max(40, sh - height - 110)}")

        self.grip = tk.Label(
            self.root,
            text="⣿  drag  ⣿",
            fg=GRIP_FG,
            bg=GRIP_BG,
            font=("Segoe UI", 8, "bold"),
            cursor="fleur",
        )
        self.grip.pack(fill="x")

        self.canvas = tk.Canvas(
            self.root, width=96, height=96, bg=BG, highlightthickness=0, bd=0, cursor="hand2"
        )
        self.canvas.pack(padx=8, pady=(10, 0))
        self.icon = None
        self._draw_orb(IDLE)

        self.status = tk.Label(
            self.root,
            text=HINTS[IDLE],
            fg=TEXT,
            bg=BG,
            font=("Segoe UI", 9, "bold"),
            wraplength=112,
            justify="center",
        )
        self.status.pack(pady=(6, 10), fill="x")
        self.root.after(80, self._round_window)

        self.grip.bind("<ButtonPress-1>", self._on_grip_press)
        self.grip.bind("<B1-Motion>", self._on_drag)
        self.grip.bind("<ButtonRelease-1>", lambda _e: None)
        self.canvas.bind("<ButtonPress-1>", self._on_talk_click)
        self.canvas.bind("<ButtonRelease-1>", lambda e: "break")
        self.status.bind("<ButtonPress-1>", self._on_talk_click)
        self.root.bind("<Button-3>", self._on_right_click)
        self.grip.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.status.bind("<Button-3>", self._on_right_click)

    def _round_window(self) -> None:
        try:
            from ctypes import windll

            self.root.update_idletasks()
            hwnd = windll.user32.GetParent(self.root.winfo_id())
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            region = windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, 28, 28)
            windll.user32.SetWindowRgn(hwnd, region, True)
        except Exception:
            pass

    def _draw_orb(self, state: str) -> None:
        outer, mid, inner = ORB.get(state, ORB[IDLE])
        self.canvas.delete("orb")
        self.canvas.create_oval(2, 2, 94, 94, fill=outer, outline="", tags="orb")
        self.canvas.create_oval(14, 14, 82, 82, fill=mid, outline="", tags="orb")
        self.canvas.create_oval(26, 26, 70, 70, fill=inner, outline="", tags="orb")
        if state == LISTENING:
            icon = "•"
        elif state == SPEAKING:
            icon = "■"
        else:
            icon = "AI"
        self.icon = self.canvas.create_text(
            48, 48, text=icon, fill="white", font=("Segoe UI", 15, "bold"), tags="orb"
        )

    def _paint(self, state: str, detail: str | None = None) -> None:
        try:
            if not self.root.winfo_exists():
                return
            self.state = state
            self._draw_orb(state)
            self.status.config(text=(detail or HINTS.get(state, ""))[:48])
        except Exception:
            pass

    def _set_state(self, state: str, detail: str | None = None) -> None:
        self._ui_q.put((state, detail))

    def _pump_ui(self) -> None:
        try:
            while True:
                state, detail = self._ui_q.get_nowait()
                if state == "__destroy__":
                    try:
                        self.root.destroy()
                    except Exception:
                        pass
                    return
                self._paint(state, detail)
        except queue.Empty:
            pass
        try:
            if self.root.winfo_exists():
                self.root.after(50, self._pump_ui)
        except Exception:
            pass

    def _on_grip_press(self, event) -> str:
        self._drag = {
            "ox": event.x_root - self.root.winfo_x(),
            "oy": event.y_root - self.root.winfo_y(),
            "sx": event.x_root,
            "sy": event.y_root,
        }
        return "break"

    def _on_drag(self, event) -> str:
        x = event.x_root - self._drag.get("ox", 0)
        y = event.y_root - self._drag.get("oy", 0)
        self.root.geometry(f"+{x}+{y}")
        return "break"

    def _on_talk_click(self, _event) -> str:
        if self.state == SPEAKING:
            self.speak_stop.set()
            return "break"
        if self.state == LISTENING:
            # Second tap means "I'm done speaking" — process audio, do not cancel.
            if time.monotonic() - self._listen_started < 0.8:
                return "break"
            self.stop_event.set()
            self._paint(WORKING, "On it…")
            return "break"
        if self.state != IDLE:
            return "break"
        self._start_turn()
        return "break"

    def _start_turn(self) -> None:
        if not self.busy.acquire(blocking=False):
            return
        self.stop_event.clear()
        self.speak_stop.clear()
        self._listen_started = time.monotonic()
        self._paint(LISTENING, "Listening…")
        threading.Thread(target=self._talk_once, daemon=True).start()

    def _on_right_click(self, _event) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass

    def _talk_once(self) -> None:
        from desktop_agent.interfaces.voice import (
            _audio_seconds,
            _handle_command,
            _record_until_silence,
            is_stop_phrase,
            speak,
            transcribe,
        )

        try:
            self._set_state(LISTENING, "Listening…")
            try:
                audio = _record_until_silence(
                    max_wait_for_speech=8.0,
                    max_utterance=12.0,
                    silence_seconds=1.3,
                    start_threshold=0.008,
                    stop_event=self.stop_event,
                    on_status=lambda msg: self._set_state(LISTENING, "Listening…"),
                )
            except Exception as exc:
                print(f"Record error: {exc}", flush=True)
                self._set_state(SPEAKING, "Mic failed")
                speak("The microphone failed.", stop_event=self.speak_stop)
                return
            if self.stop_event.is_set():
                print("Listen finished by tap — using captured audio", flush=True)
            if _audio_seconds(audio) < 0.5:
                print("Heard: (empty)", flush=True)
                self._set_state(SPEAKING, "Didn't catch that")
                speak("I didn't catch that.", stop_event=self.speak_stop)
                try:
                    from desktop_agent.session_log import append_turn

                    append_turn(
                        source="voice",
                        user="(didn't catch that)",
                        assistant="I didn't catch that.",
                        extra={"status": "empty"},
                    )
                except Exception:
                    pass
                return
            self._set_state(WORKING, "On it…")
            try:
                heard = (transcribe(audio) or "").strip()
            except Exception as exc:
                print(f"Transcribe error: {exc}", flush=True)
                self._set_state(SPEAKING, "Didn't catch that")
                speak("I didn't catch that.", stop_event=self.speak_stop)
                try:
                    from desktop_agent.session_log import append_turn

                    append_turn(
                        source="voice",
                        user="(didn't catch that)",
                        assistant="I didn't catch that.",
                        extra={"status": "empty"},
                    )
                except Exception:
                    pass
                return
            print(f"Heard: {heard or '(empty)'}", flush=True)
            if not heard:
                self._set_state(SPEAKING, "Didn't catch that")
                speak("I didn't catch that.", stop_event=self.speak_stop)
                try:
                    from desktop_agent.session_log import append_turn

                    append_turn(
                        source="voice",
                        user="(didn't catch that)",
                        assistant="I didn't catch that.",
                        extra={"status": "empty"},
                    )
                except Exception:
                    pass
                return
            if is_stop_phrase(heard):
                speak("Okay.", stop_event=self.speak_stop)
                self._ui_q.put(("__destroy__", None))
                return
            self._set_state(WORKING, heard)
            try:
                reply = _handle_command(self.agent, heard)
            except Exception as exc:
                reply = f"I hit an error: {exc}"
            if reply == "__stop__":
                speak("Okay.", stop_event=self.speak_stop)
                self._ui_q.put(("__destroy__", None))
                return
            print(f"Agent: {reply}\n", flush=True)
            self._set_state(SPEAKING, "Tap to stop")
            speak(reply, stop_event=self.speak_stop)
        except Exception as exc:
            print(f"Talk error: {exc}", flush=True)
        finally:
            self._set_state(IDLE)
            if self.busy.locked():
                try:
                    self.busy.release()
                except Exception:
                    pass

    def run(self) -> None:
        self.root.after(50, self._pump_ui)
        self.root.mainloop()


def run_bubble() -> int:
    from desktop_agent.factory import build_agent
    from desktop_agent.interfaces.voice import speak

    print("Loading desktop agent for the voice bubble...", flush=True)
    from desktop_agent.session_log import start_server

    start_server()
    agent = build_agent()
    try:
        from desktop_agent.interfaces.voice import _mic_settings

        _device, rate, name = _mic_settings()
        print(f"Using microphone: {name} @ {rate} Hz", flush=True)
    except Exception as exc:
        print(f"Microphone lookup failed: {exc}", flush=True)
    try:
        import sounddevice as sd

        out = sd.query_devices(kind="output")
        print(f"Speaking through: {out.get('name')} @ {int(out.get('default_samplerate') or 0)} Hz", flush=True)
    except Exception as exc:
        print(f"Speaker lookup failed: {exc}", flush=True)
    print("Tap to talk. After the answer it goes idle until you tap again.", flush=True)
    try:
        speak("Ready. I will speak the answer after each task.")
    except Exception as exc:
        print(f"Startup voice failed: {exc}", flush=True)
    VoiceBubble(agent).run()
    return 0
