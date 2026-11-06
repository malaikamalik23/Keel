import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.ratelimit import RateLimiter
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
        if container.settings.notes_dir:
            try:
                container.index_notes()
            except Exception:  # noqa: BLE001 - a bad notes folder must not stop the app from starting
                logging.getLogger(__name__).exception("Could not index NOTES_DIR at startup")
        if container.settings.scheduler_enabled:
            container.scheduler.start()
        try:
            yield
        finally:
            container.scheduler.stop()

    app = FastAPI(title="Keel", version="1.0.0", lifespan=lifespan)
    app.state.container = container
    app.state.limiter = RateLimiter(container.settings.rate_limit_per_minute)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # The dashboard is same-origin only: no inline scripts, no third-party loads, no framing.
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store") if request.url.path.startswith("/api") else None
        return response
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
