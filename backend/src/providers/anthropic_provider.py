"""Anthropic provider implementation.

Uses the official Anthropic Python SDK to interact with Claude models.
Supports Claude 3 Opus, Sonnet, and Haiku.
"""

from typing import List, Dict, Optional, Iterator
from .base import LLMProvider, ProviderConfig

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class AnthropicProvider(LLMProvider):
    """Provider for Anthropic's Claude API.

    Uses the official Anthropic Python SDK for chat completions.
    Requires an API key set in the environment or config.
    """

    def __init__(self, config: ProviderConfig):
        """Initialize Anthropic provider.

        Args:
            config: Provider configuration with API key and model.

        Raises:
            ImportError: If anthropic package is not installed.
            ValueError: If API key is not provided.
        """
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "Anthropic SDK not installed. Install with: pip install anthropic"
            )

        super().__init__(config)

        if not config.api_key:
            raise ValueError("Anthropic API key is required")

        # Initialize Anthropic client
        self.client = Anthropic(
            api_key=config.api_key,
            timeout=config.timeout
        )

    def _convert_messages(self, messages: List[Dict[str, str]]) -> tuple[Optional[str], List[Dict[str, str]]]:
        """Convert messages to Anthropic format.

        Anthropic requires the system message to be separate from other messages.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.

        Returns:
            Tuple of (system_prompt, converted_messages)
        """
        system_prompt = None
        converted = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                # Extract system message
                system_prompt = content
            else:
                # Keep user/assistant messages
                converted.append({"role": role, "content": content})

        return system_prompt, converted

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Send a chat completion request to Anthropic.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate (default: 4096).
            **kwargs: Additional parameters for Anthropic API.

        Returns:
            The assistant's response as a string.

        Raises:
            anthropic.AnthropicError: If the request fails.
        """
        # Convert messages to Anthropic format
        system_prompt, converted_messages = self._convert_messages(messages)

        # Build request parameters
        params = {
            "model": self.config.model,
            "messages": converted_messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens or 4096,  # Anthropic requires max_tokens
        }

        if system_prompt:
            params["system"] = system_prompt

        # Add any extra parameters
        params.update(kwargs)

        # Make request
        response = self.client.messages.create(**params)

        # Extract content
        content = response.content[0].text

        # Track usage
        usage = response.usage
        if usage:
            self._track_usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens
            )

        return content

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Iterator[str]:
        """Stream a chat completion response from Anthropic.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate (default: 4096).
            **kwargs: Additional parameters for Anthropic API.

        Yields:
            Chunks of the assistant's response.

        Raises:
            anthropic.AnthropicError: If the request fails.
        """
        # Convert messages to Anthropic format
        system_prompt, converted_messages = self._convert_messages(messages)

        # Build request parameters
        params = {
            "model": self.config.model,
            "messages": converted_messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens or 4096,
        }

        if system_prompt:
            params["system"] = system_prompt

        params.update(kwargs)

        # Stream response
        input_tokens = 0
        output_tokens = 0

        with self.client.messages.stream(**params) as stream:
            for text in stream.text_stream:
                yield text

            # Get final message for usage stats
            final_message = stream.get_final_message()
            if final_message and final_message.usage:
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens

        # Track final usage
        if input_tokens or output_tokens:
            self._track_usage(input_tokens, output_tokens)

    def list_models(self) -> List[str]:
        """List available models from Anthropic.

        Note: Anthropic doesn't provide a models endpoint, so we return
        a hardcoded list of known models.

        Returns:
            List of known Claude model IDs.
        """
        return [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-sonnet-20240620",
        ]
