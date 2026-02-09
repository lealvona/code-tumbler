"""Base LLM provider interface for Code Tumbler.

This module defines the abstract base class that all LLM providers must implement.
It provides a unified interface for interacting with different LLM backends
(local or cloud-based).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Iterator
from enum import Enum


class ProviderType(str, Enum):
    """Supported LLM provider types."""

    VLLM = "vllm"
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider."""

    name: str
    type: ProviderType
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None  # Environment variable name for API key
    model: str = ""
    cost_per_1k_input_tokens: float = 0.0
    cost_per_1k_output_tokens: float = 0.0
    cost_input_1k: Optional[float] = None  # Alias for cost_per_1k_input_tokens
    cost_output_1k: Optional[float] = None  # Alias for cost_per_1k_output_tokens
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: int = 300  # seconds
    context_length: Optional[int] = None  # Total context window in tokens; None = auto-detect from model name
    nothink: Optional[bool] = None  # Append /nothink to user messages; None = auto (on for openai/vllm/ollama)
    concurrency_limit: int = 4  # Max concurrent requests to this provider
    retry_max_attempts: int = 3  # Max retries on transient errors (429/503)
    retry_base_delay: float = 1.0  # Base delay in seconds for exponential backoff
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageStats:
    """Token usage statistics for a completion."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0

    def calculate_cost(self, config: ProviderConfig) -> None:
        """Calculate cost based on provider configuration."""
        self.cost = (
            (self.input_tokens / 1000) * config.cost_per_1k_input_tokens +
            (self.output_tokens / 1000) * config.cost_per_1k_output_tokens
        )


@dataclass
class ToolCall:
    """A structured tool call returned by the LLM."""

    id: str
    function_name: str
    arguments: Dict[str, Any]


@dataclass
class ChatResult:
    """Result of a chat completion that may include tool calls."""

    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All provider implementations must inherit from this class and implement
    the required methods. This ensures a consistent interface across different
    LLM backends.
    """

    def __init__(self, config: ProviderConfig):
        """Initialize the provider with configuration.

        Args:
            config: Provider configuration including API keys, base URLs, etc.
        """
        self.config = config
        self.usage_history: List[UsageStats] = []

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Send a chat completion request and return the response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
                     Example: [{"role": "user", "content": "Hello"}]
            temperature: Sampling temperature (0.0 to 2.0). If None, uses config default.
            max_tokens: Maximum tokens to generate. If None, uses config default.
            **kwargs: Additional provider-specific parameters.

        Returns:
            The assistant's response as a string.

        Raises:
            Exception: If the request fails.
        """
        pass

    @abstractmethod
    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Iterator[str]:
        """Stream a chat completion response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (0.0 to 2.0). If None, uses config default.
            max_tokens: Maximum tokens to generate. If None, uses config default.
            **kwargs: Additional provider-specific parameters.

        Yields:
            Chunks of the assistant's response as they are generated.

        Raises:
            Exception: If the request fails.
        """
        pass

    @abstractmethod
    def list_models(self) -> List[str]:
        """List available models from this provider.

        Returns:
            List of model names/identifiers available from this provider.

        Raises:
            Exception: If the request fails.
        """
        pass

    def get_usage(self) -> UsageStats:
        """Get the most recent usage statistics.

        Returns:
            UsageStats object with token counts and cost.
        """
        return self.usage_history[-1] if self.usage_history else UsageStats()

    def get_total_usage(self) -> UsageStats:
        """Get cumulative usage statistics across all requests.

        Returns:
            Aggregated UsageStats object.
        """
        total = UsageStats()
        for usage in self.usage_history:
            total.input_tokens += usage.input_tokens
            total.output_tokens += usage.output_tokens
            total.total_tokens += usage.total_tokens
            total.cost += usage.cost
        return total

    def _track_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Track token usage for a completion.

        Args:
            input_tokens: Number of input tokens used.
            output_tokens: Number of output tokens generated.
        """
        usage = UsageStats(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens
        )
        usage.calculate_cost(self.config)
        self.usage_history.append(usage)

    def reset_usage(self) -> None:
        """Reset usage history."""
        self.usage_history = []

    def health_check(self) -> bool:
        """Check if the provider is accessible and responding.

        Returns:
            True if the provider is healthy, False otherwise.
        """
        try:
            self.list_models()
            return True
        except Exception as e:
            # Store the exception for debugging
            self._health_check_error = str(e)
            return False
