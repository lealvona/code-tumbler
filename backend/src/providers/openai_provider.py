"""OpenAI provider implementation.

Uses the official OpenAI Python SDK to interact with OpenAI's API.
Supports GPT-4, GPT-4o, GPT-3.5, and other OpenAI models.

Also supports OpenAI-compatible endpoints by specifying a custom base_url
in the configuration. This allows integration with VLLM servers, local
OpenAI-compatible APIs, or other third-party services.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Iterator, AsyncIterator

from .base import LLMProvider, ProviderConfig

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI, AsyncOpenAI, APIStatusError
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI's API and OpenAI-compatible endpoints.

    Uses the official OpenAI Python SDK for chat completions.

    For official OpenAI API:
    - Requires an API key set in the environment or config

    For OpenAI-compatible endpoints:
    - Set base_url in config to your custom endpoint
    - API key is optional (depends on endpoint requirements)
    """

    def __init__(self, config: ProviderConfig):
        """Initialize OpenAI provider.

        Args:
            config: Provider configuration with API key and model.

        Raises:
            ImportError: If openai package is not installed.
            ValueError: If API key is not provided for official OpenAI API.
        """
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI SDK not installed. Install with: pip install openai"
            )

        super().__init__(config)

        # For custom OpenAI-compatible endpoints, API key might not be required
        # For official OpenAI API, it's required
        client_kwargs = {}

        # Add custom base_url if provided (for OpenAI-compatible endpoints)
        if config.base_url:
            client_kwargs["base_url"] = config.base_url

        # Add API key if provided (some custom endpoints don't require it)
        if config.api_key:
            client_kwargs["api_key"] = config.api_key
        elif not config.base_url:
            # Only require API key if using official OpenAI API
            raise ValueError("OpenAI API key is required for official OpenAI API")
        else:
            # For custom endpoints without API key, use a dummy key
            client_kwargs["api_key"] = "not-needed"

        # Add timeout if specified (must be a float or httpx.Timeout)
        if config.timeout:
            client_kwargs["timeout"] = float(config.timeout)

        # Initialize sync + async OpenAI clients
        try:
            self.client = OpenAI(**client_kwargs)
            self.async_client = AsyncOpenAI(**client_kwargs)
        except TypeError as e:
            # If initialization fails due to unexpected parameters, try with minimal config
            if "unexpected keyword argument" in str(e):
                minimal_kwargs = {
                    "api_key": client_kwargs.get("api_key", "not-needed")
                }
                if "base_url" in client_kwargs:
                    minimal_kwargs["base_url"] = client_kwargs["base_url"]
                self.client = OpenAI(**minimal_kwargs)
                self.async_client = AsyncOpenAI(**minimal_kwargs)
            else:
                raise

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Send a chat completion request to OpenAI.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional parameters for OpenAI API.

        Returns:
            The assistant's response as a string.

        Raises:
            openai.OpenAIError: If the request fails.
        """
        # Build request parameters
        params = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }

        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            params["max_tokens"] = self.config.max_tokens

        # Pass extra_params from provider config as extra_body
        if self.config.extra_params:
            params["extra_body"] = self.config.extra_params

        # Add any extra parameters
        params.update(kwargs)

        # Make request
        response = self.client.chat.completions.create(**params)

        # Extract content
        content = response.choices[0].message.content

        # Track usage
        usage = response.usage
        if usage:
            self._track_usage(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens
            )

        return content

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Iterator[str]:
        """Stream a chat completion response from OpenAI.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional parameters for OpenAI API.

        Yields:
            Chunks of the assistant's response.

        Raises:
            openai.OpenAIError: If the request fails.
        """
        # Build request parameters
        params = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            params["max_tokens"] = self.config.max_tokens

        # Pass extra_params from provider config as extra_body
        if self.config.extra_params:
            params["extra_body"] = self.config.extra_params

        params.update(kwargs)

        # Stream response
        try:
            stream = self.client.chat.completions.create(**params)
        except TypeError:
            # Some OpenAI-compatible endpoints don't support stream_options
            params.pop("stream_options", None)
            stream = self.client.chat.completions.create(**params)

        input_tokens = 0
        output_tokens = 0

        for chunk in stream:
            if not chunk.choices:
                # Usage-only chunk (sent when stream_options.include_usage is true)
                if hasattr(chunk, 'usage') and chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens
                continue

            # Extract content delta
            delta = chunk.choices[0].delta
            content = delta.content

            if content:
                yield content

            # Check if we have usage info (last chunk)
            if hasattr(chunk, 'usage') and chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens

        # Track final usage
        if input_tokens or output_tokens:
            self._track_usage(input_tokens, output_tokens)

    def list_models(self) -> List[str]:
        """List available models from OpenAI.

        Returns:
            List of model IDs available.

        Raises:
            openai.OpenAIError: If the request fails.
        """
        try:
            response = self.client.models.list()
            return [model.id for model in response.data]
        except Exception:
            # For custom OpenAI-compatible endpoints, the /models endpoint
            # might not be implemented or might return a different format.
            # Fall back to returning the configured model.
            if self.config.base_url and self.config.model:
                # Custom endpoint - return configured model as a fallback
                return [self.config.model]
            else:
                # Official OpenAI API should always work
                raise

    def get_model_info(self, model_id: Optional[str] = None) -> Dict:
        """Get detailed information about a model.

        Args:
            model_id: ID of the model. If None, uses configured model.

        Returns:
            Dict with model information.

        Raises:
            openai.OpenAIError: If the request fails.
        """
        model = model_id or self.config.model
        response = self.client.models.retrieve(model)
        return response.model_dump()

    # --- Shared helpers ---

    def _build_params(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Build parameters dict for chat.completions.create.

        Centralises payload construction used by sync and async paths.
        """
        params: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if stream:
            params["stream"] = True
            params["stream_options"] = {"include_usage": True}
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            params["max_tokens"] = self.config.max_tokens
        if self.config.extra_params:
            params["extra_body"] = self.config.extra_params
        params.update(kwargs)
        return params

    # --- Async retry infrastructure ---

    async def _async_request_with_retry(self, params: Dict[str, Any]):
        """Execute async chat.completions.create with retry on 429/503.

        Respects Retry-After headers and uses exponential backoff.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.retry_max_attempts):
            try:
                return await self.async_client.chat.completions.create(**params)
            except APIStatusError as e:
                last_exc = e
                if e.status_code in (429, 503) and attempt < self.config.retry_max_attempts - 1:
                    delay = self.config.retry_base_delay * (2 ** attempt)
                    retry_after = e.response.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = max(delay, float(retry_after))
                        except ValueError:
                            pass
                    logger.warning(
                        f"OpenAI-compat returned {e.status_code}, "
                        f"retrying in {delay:.1f}s (attempt {attempt + 1}/{self.config.retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except Exception as e:
                last_exc = e
                if attempt < self.config.retry_max_attempts - 1:
                    delay = self.config.retry_base_delay * (2 ** attempt)
                    logger.warning(
                        f"OpenAI-compat request failed ({e}), "
                        f"retrying in {delay:.1f}s (attempt {attempt + 1}/{self.config.retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        raise last_exc  # type: ignore[misc]

    # --- Async chat methods ---

    async def async_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> str:
        """Async chat completion with retry on transient errors.

        Same interface as chat() but non-blocking.
        """
        params = self._build_params(messages, temperature, max_tokens, stream=False, **kwargs)
        response = await self._async_request_with_retry(params)
        content = response.choices[0].message.content

        if response.usage:
            self._track_usage(response.usage.prompt_tokens, response.usage.completion_tokens)

        return content

    async def async_stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Async streaming chat completion.

        Same interface as stream_chat() but yields chunks asynchronously.
        """
        params = self._build_params(messages, temperature, max_tokens, stream=True, **kwargs)
        try:
            stream = await self.async_client.chat.completions.create(**params)
        except TypeError:
            # Some OpenAI-compatible endpoints don't support stream_options
            params.pop("stream_options", None)
            stream = await self.async_client.chat.completions.create(**params)

        input_tokens = 0
        output_tokens = 0

        async for chunk in stream:
            if not chunk.choices:
                if hasattr(chunk, 'usage') and chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens
                continue

            delta = chunk.choices[0].delta
            content = delta.content
            if content:
                yield content

            if hasattr(chunk, 'usage') and chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens

        if input_tokens or output_tokens:
            self._track_usage(input_tokens, output_tokens)

    # --- Tool call support ---

    async def async_chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Async chat completion with structured tool call support.

        When tools are provided and the model/server supports tool calling
        (e.g. vLLM with --tool-call-parser behind Open WebUI), the response
        may include structured tool_calls.

        Returns:
            Dict with 'content' (str) and 'tool_calls' (list of dicts).
        """
        params = self._build_params(messages, temperature, max_tokens, stream=False, **kwargs)
        if tools:
            params["tools"] = tools

        response = await self._async_request_with_retry(params)
        message = response.choices[0].message
        content = message.content or ""

        tool_calls = []
        if hasattr(message, 'tool_calls') and message.tool_calls:
            import json as _json
            for tc in message.tool_calls:
                fn = tc.function
                try:
                    args = _json.loads(fn.arguments) if isinstance(fn.arguments, str) else fn.arguments
                except (_json.JSONDecodeError, TypeError):
                    args = {"raw": fn.arguments}
                tool_calls.append({
                    "id": tc.id,
                    "function_name": fn.name,
                    "arguments": args,
                })

        if response.usage:
            self._track_usage(response.usage.prompt_tokens, response.usage.completion_tokens)

        return {"content": content, "tool_calls": tool_calls}

    # --- Async health check ---

    async def async_health_check(self) -> Dict[str, Any]:
        """Check provider health asynchronously.

        Returns:
            Dict with 'healthy' bool, 'model', and optional 'warnings'.
        """
        result: Dict[str, Any] = {
            "healthy": False,
            "model": self.config.model,
            "warnings": [],
        }
        try:
            models_resp = await self.async_client.models.list()
            model_ids = [m.id for m in models_resp.data]
            result["healthy"] = True
            result["available_models"] = model_ids
            if self.config.model and self.config.model not in model_ids:
                result["warnings"].append(
                    f"Configured model '{self.config.model}' not in available models"
                )
        except Exception as e:
            result["warnings"].append(f"Could not reach provider: {e}")
            # For custom endpoints, still mark healthy if we can reach them
            if self.config.base_url:
                result["healthy"] = True
                result["warnings"][-1] += " (custom endpoint — may still work)"
        return result
