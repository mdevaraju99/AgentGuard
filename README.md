# AI Desktop Agent (Part 1)

Personal-laptop POC: a **tool-using desktop agent**, not a chatbot. Part 2 (Langfuse, evaluation, governance) is intentionally not included yet. `AgentState` and `trace_events` are shaped so Langfuse can be attached later.

## What V1 does

- Text CLI and Streamlit chat
- Allowlisted app control (Chrome, VS Code, Notepad, Calculator, Explorer)
- Sandboxed **writes** under `AI-Agent-Demo/`
- **Search / open / read** across approved user folders configured in `config/file_roots.yaml` (Documents, Downloads, Desktop by default)
- PDF / TXT / DOCX / CSV extraction and summarization via the LLM
- Playwright web search / URL read
- Timers, alarms, reminders
- Multi-step custom agent loop with verification and a permission layer
- Short-term conversation memory (`it` = last file)

## Setup

```powershell
cd "d:\Documents\Desktop Agent + Langfuse tools"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

Edit `.env`:

- `LLM_PROVIDER=openai` and `OPENAI_API_KEY=...`, or
- `LLM_PROVIDER=ollama`, `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=llama3.1`

## Run

```powershell
python -m desktop_agent
python -m desktop_agent "Find my AWS PDF."
streamlit run app.py
```

## Tests (no live LLM)

```powershell
python -m pytest -q
```

The M1 scenario is covered by `tests/test_agent_loop.py` with a scripted planner. Tools still run for real against a temp sandbox.

## Demo data

On first agent start, `AI-Agent-Demo/documents/AWS_Observability.pdf` is created (includes a prompt-injection sentence that must be treated as data).

## Safety

- **Writes** (create file/folder) cannot leave `AI-Agent-Demo/`
- **Search/open/read/extract** are limited to the sandbox plus enabled roots in `config/file_roots.yaml`
- Enable or disable Pictures/Videos there; the LLM cannot pick arbitrary folders
- `delete_file` is blocked
- No shell tool
- `close_application` needs `--confirm-close` (CLI) or the Streamlit checkbox
- Document/web text is wrapped as untrusted content
- If several files match equally, the agent lists them instead of opening one at random

## Part 2 (do not start yet)

After Part 1 is stable, traces already include user request, LLM calls, tool args/results, errors, retries, latency, verification, task outcome, and final response.
