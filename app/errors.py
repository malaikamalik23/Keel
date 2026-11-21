"""Application-level exceptions. The API layer maps these to HTTP status codes."""


class AppError(Exception):
    pass


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass


class LLMError(AppError):
    """`retryable`: the provider is busy or unreachable (rate limit, overload, timeout), not a real error, so another
    model can answer instead (see app/llm/fallback.py)."""

    def __init__(self, message: str = "", *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
