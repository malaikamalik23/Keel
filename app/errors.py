"""Application-level exceptions. The API layer maps these to HTTP status codes."""


class AppError(Exception):
    pass


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass


class LLMError(AppError):
    pass
