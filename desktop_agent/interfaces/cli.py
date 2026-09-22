from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _print_state(state) -> None:
    print(f"\n[{state.task_outcome.value}] {state.final_response}\n")
    if state.steps_taken:
        print("Steps:")
        for step in state.steps_taken:
            flag = "ok" if step.ok and step.verified else ("unverified" if step.ok else "fail")
            print(f"  {step.step_index}. {step.tool_name} [{flag}] {step.arguments}")
            if step.error:
                print(f"      error: {step.error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Desktop Agent (Part 1)")
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt")
    parser.add_argument("--session", default="cli-default")
    parser.add_argument("--json", action="store_true", help="Print the AgentState as JSON")
    parser.add_argument("--confirm-close", action="store_true", help="Allow close_application this run")
    parser.add_argument("--voice", action="store_true", help="Hands-free microphone mode (hey agent)")
    args = parser.parse_args(argv)

    if args.voice:
        from desktop_agent.interfaces.voice import main as voice_main

        return voice_main([])

    from desktop_agent.factory import build_agent
    from desktop_agent.safety.paths import describe_allowed_roots

    agent = build_agent()
    if args.confirm_close:
        agent.confirmed_tools.add("close_application")

    def handle(text: str) -> None:
        state = agent.run(text, session_id=args.session)
        if args.json:
            print(state.model_dump_json(indent=2))
        else:
            _print_state(state)

    if args.prompt:
        handle(" ".join(args.prompt))
        return 0

    print("AI Desktop Agent  |  type 'exit' to quit")
    print("Allowed directories:")
    for row in describe_allowed_roots():
        mark = "on " if row["status"] == "enabled" else row["status"]
        print(f"  [{mark}] {row['id']}: {row['path']} ({row['access']})")
    print("Writes stay in AI-Agent-Demo. Delete and shell remain blocked.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            return 0
        handle(text)


if __name__ == "__main__":
    raise SystemExit(main())
