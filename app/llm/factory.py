from app.config import Settings
from app.errors import LLMError
from app.llm.base import LLMClient, LLMResponse


class UnconfiguredLLM:
    """Lets the app start without an API key; chat fails with a clear message."""

    provider = None

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        raise LLMError("No LLM API key is set. Add ANTHROPIC_API_KEY, GROQ_API_KEY or GEMINI_API_KEY to your .env file, or use LLM_PROVIDER=ollama.")


def resolve_provider(settings: Settings) -> str | None:
    if settings.llm_provider != "auto":
        return settings.llm_provider
    return "anthropic" if settings.anthropic_api_key else "groq" if settings.groq_api_key else "gemini" if settings.gemini_api_key else None


def build_llm(settings: Settings) -> LLMClient:
    main = _build(settings, resolve_provider(settings))
    fallback = settings.llm_fallback
    if fallback == "none" or fallback == resolve_provider(settings) or isinstance(main, UnconfiguredLLM):
        return main
    from app.llm.fallback import FallbackLLM

    return FallbackLLM(main, _build(settings, fallback), resolve_provider(settings) or "?", fallback)


def _build(settings: Settings, provider: str | None) -> LLMClient:
    if provider is None:
        return UnconfiguredLLM()
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise LLMError("Gemini is selected but GEMINI_API_KEY is not set.")
        from app.llm.gemini_client import GeminiClient

        return GeminiClient(settings.gemini_api_key, settings.gemini_model, settings.max_output_tokens, settings.llm_temperature)
    if provider == "ollama":
        from app.llm.ollama_client import OllamaClient

        return OllamaClient(settings.ollama_base_url, settings.ollama_model, settings.max_output_tokens, settings.llm_temperature,
                            num_ctx=settings.ollama_num_ctx, timeout=settings.ollama_timeout_seconds)
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        from app.llm.anthropic_client import AnthropicClient

        return AnthropicClient(settings.anthropic_api_key, settings.agent_model, settings.max_output_tokens, settings.llm_temperature)

    if not settings.groq_api_key:
        raise LLMError("LLM_PROVIDER=groq but GROQ_API_KEY is not set.")
    from app.llm.groq_client import GroqClient

    return GroqClient(settings.groq_api_key, settings.agent_model, settings.max_output_tokens, settings.llm_temperature)


def model_name(settings: Settings) -> str:
    """The model that will actually answer, for logs and eval history (an Ollama run is labelled with Ollama's model)."""
    provider = resolve_provider(settings)
    if provider == "ollama":
        return f"ollama/{settings.ollama_model}"
    if provider == "gemini":
        return f"gemini/{settings.gemini_model}"
    return settings.agent_model
