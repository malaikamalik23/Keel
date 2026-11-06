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
