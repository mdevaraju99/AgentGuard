from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENT_CODE_VERSION = "siri-ui-v26"

SIRI_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@500;700;800&display=swap');

html, body, [class*="css"]  {
  font-family: Nunito, "Segoe UI", sans-serif;
}
.stApp {
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(232,121,249,0.35), transparent 55%),
    radial-gradient(900px 500px at 100% 0%, rgba(56,189,248,0.30), transparent 50%),
    radial-gradient(800px 500px at 50% 110%, rgba(167,139,250,0.28), transparent 45%),
    linear-gradient(165deg, #1A1430 0%, #24164A 45%, #12304F 100%);
  color: #F8EFFF;
}
[data-testid="stHeader"] {
  background: transparent;
}
[data-testid="stToolbar"] {
  background: transparent;
}
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #3B1D63 0%, #1E2A5A 100%);
  border-right: 1px solid rgba(240,171,252,0.25);
}
[data-testid="stSidebar"] * {
  color: #F8EFFF !important;
}
.siri-hero {
  padding: 0.4rem 0 1.1rem 0;
}
.siri-orb {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: conic-gradient(from 200deg, #38BDF8, #818CF8, #E879F9, #FB7185, #38BDF8);
  box-shadow: 0 0 28px rgba(232,121,249,0.55), 0 0 48px rgba(56,189,248,0.35);
  margin-bottom: 0.75rem;
}
.siri-title {
  font-size: 2.15rem;
  font-weight: 800;
  margin: 0;
  background: linear-gradient(90deg, #7DD3FC 0%, #C4B5FD 45%, #F0ABFC 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.siri-sub {
  color: #E9D5FF;
  opacity: 0.88;
  margin-top: 0.25rem;
}
[data-testid="stChatMessage"] {
  background: linear-gradient(135deg, rgba(129,140,248,0.18), rgba(232,121,249,0.14));
  border: 1px solid rgba(240,171,252,0.22);
  border-radius: 18px;
  padding: 0.35rem 0.55rem;
}
[data-testid="stChatInput"] textarea,
[data-testid="stChatInput"] {
  border-radius: 22px !important;
}
.stButton > button {
  background: linear-gradient(90deg, #38BDF8, #A78BFA, #E879F9);
  color: white;
  border: 0;
  border-radius: 999px;
  font-weight: 700;
}
.stButton > button:hover {
  filter: brightness(1.08);
}
[data-testid="stExpander"] {
  background: rgba(26, 20, 48, 0.45);
  border-radius: 14px;
  border: 1px solid rgba(129,140,248,0.25);
}
.voice-term {
  background: rgba(10, 8, 24, 0.55);
  border: 1px solid rgba(240,171,252,0.35);
  border-radius: 16px;
  padding: 0.7rem 0.8rem;
  font-family: Consolas, "Cascadia Mono", "Segoe UI", monospace;
  color: #F8EFFF !important;
  max-height: 22rem;
  overflow-y: auto;
  line-height: 1.4;
  font-size: 0.85rem;
}
.voice-heard { color: #7DD3FC !important; font-weight: 700; }
.voice-agent { color: #F0ABFC !important; }
.voice-meta { color: #C4B5FD !important; opacity: 0.85; font-size: 0.85rem; }
</style>
"""


def _agent():
    from desktop_agent.factory import build_agent
    from desktop_agent.safety.paths import reset_file_access_cache

    if st.session_state.get("agent_version") != AGENT_CODE_VERSION:
        st.session_state.pop("agent", None)
        st.session_state.agent_version = AGENT_CODE_VERSION
    if "agent" not in st.session_state:
        reset_file_access_cache()
        st.session_state.agent = build_agent()
    if st.session_state.get("confirm_close"):
        st.session_state.agent.confirmed_tools.add("close_application")
    else:
        st.session_state.agent.confirmed_tools.discard("close_application")
    return st.session_state.agent


def _voice_turns() -> tuple[list, str]:
    from desktop_agent.session_log import fetch_turns

    remote = fetch_turns()
    if remote is None:
        return [], "Voice bubble is not connected. Start it, then speak."
    return remote, "Live from the bubble — not saved to disk."


def _tool_names(turn: dict) -> str:
    tools = [s.get("tool") for s in (turn.get("trace") or {}).get("steps") or [] if s.get("tool")]
    return " · ".join(tools)


def _terminal_html(turns: list, empty_hint: str) -> str:
    if not turns:
        return f'<div class="voice-term"><div class="voice-meta">{empty_hint}</div></div>'
    chunks = [
        f'<div class="voice-meta">{len(turns)} live turn{"s" if len(turns) != 1 else ""} — same stream as the voice terminal, not saved to disk.</div>'
    ]
    for turn in turns:
        user = (turn.get("user") or "").replace("<", "&lt;").replace(">", "&gt;")
        assistant = (turn.get("assistant") or "").replace("<", "&lt;").replace(">", "&gt;")
        tools = _tool_names(turn)
        chunks.append(f'<div class="voice-heard">Heard: {user}</div>')
        chunks.append(f'<div class="voice-agent">Agent: {assistant}</div>')
        if tools:
            chunks.append(f'<div class="voice-meta">Speaking / tools: {tools}</div>')
        chunks.append("<br/>")
    return '<div class="voice-term">' + "".join(chunks) + "</div>"


def _draw_voice_transcript(turns: list, empty_hint: str) -> None:
    st.markdown(_terminal_html(turns, empty_hint), unsafe_allow_html=True)


@st.fragment(run_every=2)
def live_voice_sidebar() -> None:
    turns, hint = _voice_turns()
    _draw_voice_transcript(turns, hint)


def main() -> None:
    st.set_page_config(page_title="AI Desktop Agent", layout="wide")
    st.markdown(SIRI_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="siri-hero">
          <div class="siri-orb"></div>
          <p class="siri-title">AI Desktop Agent</p>
          <p class="siri-sub">Tap the bubble to talk, or type a request below.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "turns" not in st.session_state:
        st.session_state.turns = []

    with st.sidebar:
        with st.expander("Voice trace", expanded=False):
            st.caption("Spoken I/O from the bubble. Tracing stays on even while this is closed.")
            live_voice_sidebar()
        st.divider()
        st.markdown("### Session")
        st.checkbox("Allow closing apps this session", key="confirm_close")
        if st.button("Reload agent"):
            st.session_state.pop("agent", None)
            st.session_state.agent_version = AGENT_CODE_VERSION
            st.rerun()

    with st.spinner("Starting desktop agent…"):
        agent = _agent()

    st.markdown("### Chat")
    for turn in st.session_state.get("turns", []):
        with st.chat_message("user"):
            st.write(turn["user"])
        with st.chat_message("assistant"):
            st.write(turn["assistant"])
            if turn.get("trace"):
                with st.expander("Trace"):
                    st.json(turn["trace"])

    prompt = (st.chat_input("Ask something…") or "").strip()
    if prompt:
        with st.spinner("Running tools…"):
            state = agent.run(prompt, session_id="streamlit")
        from desktop_agent.session_log import trace_from_state

        trace = trace_from_state(state)
        st.session_state.turns.append(
            {"user": prompt, "assistant": state.final_response, "trace": trace}
        )
        st.rerun()


if __name__ == "__main__":
    main()
