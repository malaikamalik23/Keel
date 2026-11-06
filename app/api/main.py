from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import Settings
from app.container import build_container
from app.errors import ConflictError, LLMError, NotFoundError
from app.llm.base import LLMClient


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    app = FastAPI(title="Personal AI Agent", version="0.1.0")
    app.state.container = build_container(settings, llm)
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
    return app
