from app.config import Settings
from app.errors import LLMError
from app.llm.base import LLMClient, LLMResponse


class UnconfiguredLLM:
    """Lets the app start without an API key; chat fails with a clear message."""

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        raise LLMError("No LLM API key is set. Add ANTHROPIC_API_KEY or GROQ_API_KEY to your .env file.")


def build_llm(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider == "auto":
        if settings.anthropic_api_key:
            provider = "anthropic"
        elif settings.groq_api_key:
            provider = "groq"
        else:
            return UnconfiguredLLM()

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        from app.llm.anthropic_client import AnthropicClient

        return AnthropicClient(settings.anthropic_api_key, settings.agent_model, settings.max_output_tokens, settings.llm_temperature)

    if not settings.groq_api_key:
        raise LLMError("LLM_PROVIDER=groq but GROQ_API_KEY is not set.")
    from app.llm.groq_client import GroqClient

    return GroqClient(settings.groq_api_key, settings.agent_model, settings.max_output_tokens, settings.llm_temperature)
