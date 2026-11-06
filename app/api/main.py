from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import Settings
from app.container import build_container
from app.db.session import PROJECT_ROOT
from app.errors import ConflictError, LLMError, NotFoundError
from app.llm.base import LLMClient


def create_app(settings: Settings | None = None, llm: LLMClient | None = None, notifier=None) -> FastAPI:
    container = build_container(settings, llm, notifier)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if container.settings.scheduler_enabled:
            container.scheduler.start()
        try:
            yield
        finally:
            container.scheduler.stop()

    app = FastAPI(title="Personal AI Agent", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def _handler(status_code: int):
        async def handle(_: Request, exc: Exception):
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})

        return handle

    app.add_exception_handler(NotFoundError, _handler(404))
    app.add_exception_handler(ConflictError, _handler(409))
    app.add_exception_handler(LLMError, _handler(502))

    # Serve the built dashboard last, so it can never shadow /api or /health.
    if app.state.container.settings.frontend_dist:
        dist = Path(app.state.container.settings.frontend_dist)
        dist = dist if dist.is_absolute() else PROJECT_ROOT / dist
        if (dist / "index.html").is_file():
            app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    return app
