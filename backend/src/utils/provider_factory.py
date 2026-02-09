"""Provider factory - creates LLM provider instances from configuration."""

from providers import OllamaProvider, OpenAIProvider, VLLMProvider, AnthropicProvider, GeminiProvider
from providers.base import ProviderConfig, ProviderType, LLMProvider


def create_provider(provider_config: ProviderConfig) -> LLMProvider:
    """Create provider instance from config.

    Args:
        provider_config: Provider configuration object

    Returns:
        Provider instance

    Raises:
        ValueError: If provider type is not supported
    """
    if provider_config.type == ProviderType.OLLAMA:
        return OllamaProvider(provider_config)
    elif provider_config.type == ProviderType.OPENAI:
        return OpenAIProvider(provider_config)
    elif provider_config.type == ProviderType.VLLM:
        return VLLMProvider(provider_config)
    elif provider_config.type == ProviderType.ANTHROPIC:
        return AnthropicProvider(provider_config)
    elif provider_config.type == ProviderType.GEMINI:
        return GeminiProvider(provider_config)
    else:
        raise ValueError(f"Unsupported provider type: {provider_config.type}")
