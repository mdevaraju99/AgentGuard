"""One-time Microsoft 365 login for Teams Graph send: python -m desktop_agent.graph_login"""

from desktop_agent.graph.auth import acquire_token_interactive, graph_client_id, setup_help


def main() -> None:
    if not graph_client_id():
        print(setup_help())
        raise SystemExit(2)
    token = acquire_token_interactive()
    print("Signed in. Teams messages can now be sent through Microsoft Graph.")
    print(f"Token acquired ({len(token)} chars).")


if __name__ == "__main__":
    main()
