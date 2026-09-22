from desktop_agent.safety.permissions import check_permission
from desktop_agent.tools.files import search_files
from desktop_agent.tools.registry import build_default_registry


def test_search_aws_pdf(sandbox):
    payload = search_files("AWS PDF")
    hits = payload["files"]
    assert hits
    assert any("AWS_Observability.pdf" in h["path"] for h in hits)


def test_search_all_pdfs(sandbox):
    payload = search_files("all PDFs in documents", directory="documents")
    hits = payload["files"]
    assert hits
    assert all(h["suffix"] == ".pdf" for h in hits)


def test_create_and_read_file(sandbox):
    registry = build_default_registry()
    create = registry.get("create_file")
    args = create.parse_args({"path": "output/hello.txt", "content": "hello"})
    result = create.run(args)
    assert result.ok
    assert (sandbox[0] / "output" / "hello.txt").read_text(encoding="utf-8") == "hello"
    verify = create.verify(args, result)
    assert verify.verified


def test_delete_is_blocked(sandbox):
    registry = build_default_registry()
    tool = registry.get("delete_file")
    denied = check_permission(tool, confirmed=True)
    assert denied is not None
    assert denied.ok is False


def test_close_requires_confirmation(sandbox):
    registry = build_default_registry()
    tool = registry.get("close_application")
    denied = check_permission(tool, confirmed=False)
    assert denied is not None
    assert "confirmation" in (denied.error or "")
    assert check_permission(tool, confirmed=True) is None
