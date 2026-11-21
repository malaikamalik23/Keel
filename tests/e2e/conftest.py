"""Real-browser tests: the real server + the built dashboard + headless Chromium.

Skipped automatically when Playwright/Chromium or the built dashboard (frontend/dist) is missing, so a plain `pytest`
still works everywhere. CI installs both. Locally:  pip install playwright && playwright install chromium
and  (cd frontend && npm run build)
"""
import socket
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="pip install playwright && playwright install chromium")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        pytest.skip("build the dashboard first: cd frontend && npm run build")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - browser binary not installed
            pytest.skip(f"Chromium is not available: {str(exc)[:80]}")
        yield b
        b.close()


@pytest.fixture
def serve(tmp_path):
    """Start the real app (with a scripted LLM) on a free port; returns (base_url, app).

    It uses a database *file*, like the real app: the dashboard loads several endpoints at once, and an in-memory test
    database shares one connection between threads, which SQLite rejects ("bad parameter or other API misuse")."""
    import uvicorn

    from app.api.main import create_app
    from tests.conftest import make_settings
    from tests.fakes import ScriptedLLM

    started: list[uvicorn.Server] = []

    def _serve(script, **settings):
        settings.setdefault("database_url", f"sqlite:///{(tmp_path / f'e2e-{len(started)}.db').as_posix()}")
        app = create_app(make_settings().model_copy(update=settings), ScriptedLLM(script))
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        started.append(server)
        return f"http://127.0.0.1:{port}", app

    yield _serve
    for s in started:
        s.should_exit = True
