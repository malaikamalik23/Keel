from pathlib import Path

from scripts.gen_tools_doc import OUT, render

ROOT = Path(__file__).resolve().parents[1]


def test_tools_reference_is_in_sync_with_the_registry():
    assert OUT.read_text(encoding="utf-8") == render(), "docs/TOOLS.md is stale: run `python -m scripts.gen_tools_doc`"


def test_env_example_documents_every_setting():
    """Every Settings field must appear in .env.example, so a new option can't ship undocumented."""
    from app.config import Settings

    example = (ROOT / ".env.example").read_text(encoding="utf-8").upper()
    missing = [name for name in Settings.model_fields if name.upper() not in example]
    assert missing == [], f"add to .env.example: {missing}"


def test_required_project_files_exist():
    for name in ("README.md", "SECURITY.md", "CONTRIBUTING.md", "Dockerfile", "docker-compose.yml", ".github/workflows/ci.yml", "docs/ARCHITECTURE.md", "docs/DEMO.md"):
        assert (ROOT / name).is_file(), name


def test_dashboard_declares_a_favicon_so_browsers_do_not_log_a_404():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'rel="icon"' in html and "/favicon.svg" in html
    assert (ROOT / "frontend" / "public" / "favicon.svg").is_file()


def test_architecture_doc_agrees_with_security_doc():
    """ARCHITECTURE.md once kept listing a DNS-rebinding window after pinning had closed it. Keep the two in step."""
    import re

    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    arch = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    enforced = security.split("## What is enforced")[1].split("## How this is tested")[0]
    controls = len(re.findall(r"^\d+\. \*\*", enforced, flags=re.MULTILINE))
    assert controls >= 13
    assert f"the {controls} enforced controls" in arch
    assert "rebinding** window" not in arch and "DNS pinning" in security
