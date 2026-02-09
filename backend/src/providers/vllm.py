"""VLLM provider implementation.

VLLM is a high-throughput LLM serving engine that provides an OpenAI-compatible API.
It's optimized for performance and can serve large models efficiently.
"""

import asyncio
import json
import logging
from typing import List, Dict, Any, Optional, Iterator, AsyncIterator

import requests

from .base import LLMProvider, ProviderConfig

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

logger = logging.getLogger(__name__)


class VLLMProvider(LLMProvider):
    """Provider for VLLM local LLM serving engine.

    VLLM provides an OpenAI-compatible HTTP API for high-performance inference.
    Default URL: http://localhost:8000
    """

    def __init__(self, config: ProviderConfig):
        """Initialize VLLM provider.

        Args:
            config: Provider configuration with base_url and model.
        """
        super().__init__(config)
        self.base_url = config.base_url or "http://localhost:8000"
        self.api_url = f"{self.base_url}/v1"

        # Async infrastructure (lazy-initialized)
        self._async_client: Optional[Any] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._retry_max = config.retry_max_attempts
        self._retry_base_delay = config.retry_base_delay

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Send a chat completion request to VLLM.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional parameters for VLLM.

        Returns:
            The assistant's response as a string.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/chat/completions"

        # Build request payload (OpenAI-compatible format)
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "stream": False,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens

        # Add any extra parameters
        payload.update(kwargs)

        # Make request
        response = requests.post(
            url,
            json=payload,
            timeout=self.config.timeout
        )
        response.raise_for_status()

        # Parse response (OpenAI format)
        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # Track usage
        usage = data.get("usage", {})
        if usage:
            self._track_usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0)
            )

        return content

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Iterator[str]:
        """Stream a chat completion response from VLLM.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional parameters for VLLM.

        Yields:
            Chunks of the assistant's response.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/chat/completions"

        # Build request payload
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens

        payload.update(kwargs)

        # Stream response
        response = requests.post(
            url,
            json=payload,
            stream=True,
            timeout=self.config.timeout
        )
        response.raise_for_status()

        import json

        input_tokens = 0
        output_tokens = 0

        # Process streaming response (Server-Sent Events format)
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')

                # Skip SSE comments and empty lines
                if not line.startswith('data: '):
                    continue

                # Extract JSON data
                data_str = line[6:]  # Remove 'data: ' prefix

                # Check for stream end
                if data_str == '[DONE]':
                    break

                try:
                    data = json.loads(data_str)

                    # Extract content delta
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content

                    # Track usage if available (usually in the last chunk)
                    if "usage" in data and data["usage"]:
                        usage = data["usage"]
                        input_tokens = usage.get("prompt_tokens", 0)
                        output_tokens = usage.get("completion_tokens", 0)

                except json.JSONDecodeError:
                    continue

        # Track final usage
        if input_tokens or output_tokens:
            self._track_usage(input_tokens, output_tokens)

    def list_models(self) -> List[str]:
        """List available models from VLLM.

        Returns:
            List of model names available in VLLM.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/models"

        response = requests.get(url, timeout=self.config.timeout)
        response.raise_for_status()

        data = response.json()
        models = data.get("data", [])

        # Extract model IDs
        return [model.get("id", "") for model in models if model.get("id")]

    def get_model_info(self, model_name: Optional[str] = None) -> Dict:
        """Get detailed information about a model.

        Args:
            model_name: Name of the model. If None, uses configured model.

        Returns:
            Dict with model information.

        Raises:
            requests.RequestException: If the request fails.
        """
        model = model_name or self.config.model
        url = f"{self.api_url}/models/{model}"

        response = requests.get(url, timeout=self.config.timeout)
        response.raise_for_status()

        return response.json()

    # --- Async infrastructure ---

    def _get_client(self) -> "httpx.AsyncClient":
        """Lazily create the async HTTP client."""
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for async vLLM operations. Install with: pip install httpx[http2]")
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                base_url=self.api_url,
                timeout=httpx.Timeout(connect=30.0, read=float(self.config.timeout), write=30.0, pool=10.0),
                http2=True,
            )
        return self._async_client

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily create the concurrency semaphore."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.config.concurrency_limit)
        return self._semaphore

    async def _request_with_retry(self, method: str, url: str, **kwargs) -> "httpx.Response":
        """HTTP request with exponential backoff on 429/503, guarded by semaphore.

        Args:
            method: HTTP method ("GET" or "POST").
            url: Request URL path (relative to base_url).

        Returns:
            httpx.Response on success.
        """
        client = self._get_client()
        sem = self._get_semaphore()
        last_exc: Optional[Exception] = None

        async with sem:
            for attempt in range(self._retry_max):
                try:
                    response = await client.request(method, url, **kwargs)
                    if response.status_code in (429, 503) and attempt < self._retry_max - 1:
                        delay = self._retry_base_delay * (2 ** attempt)
                        retry_after = response.headers.get("retry-after")
                        if retry_after:
                            try:
                                delay = max(delay, float(retry_after))
                            except ValueError:
                                pass
                        logger.warning(
                            f"vLLM returned {response.status_code}, "
                            f"retrying in {delay:.1f}s (attempt {attempt + 1}/{self._retry_max})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    response.raise_for_status()
                    return response
                except httpx.TimeoutException as e:
                    last_exc = e
                    if attempt < self._retry_max - 1:
                        delay = self._retry_base_delay * (2 ** attempt)
                        logger.warning(
                            f"vLLM request timed out, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{self._retry_max})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise
                except httpx.HTTPStatusError:
                    raise
        raise last_exc or RuntimeError("All retry attempts exhausted")

    async def close(self):
        """Close the underlying async HTTP client."""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

    # --- Async chat methods ---

    def _build_payload(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Build vLLM request payload."""
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        payload.update(kwargs)
        return payload

    async def async_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> str:
        """Async chat completion to vLLM with retry and semaphore."""
        payload = self._build_payload(messages, temperature, max_tokens, stream=False, **kwargs)
        response = await self._request_with_retry("POST", "/chat/completions", json=payload)
        data = response.json()
        content = data["choices"][0]["message"]["content"]

        usage = data.get("usage", {})
        if usage:
            self._track_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

        return content

    async def async_stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Async streaming chat completion from vLLM."""
        payload = self._build_payload(messages, temperature, max_tokens, stream=True, **kwargs)
        client = self._get_client()
        sem = self._get_semaphore()
        input_tokens = 0
        output_tokens = 0

        async with sem:
            async with client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        if "usage" in data and data["usage"]:
                            u = data["usage"]
                            input_tokens = u.get("prompt_tokens", 0)
                            output_tokens = u.get("completion_tokens", 0)
                    except json.JSONDecodeError:
                        continue

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
        """Async chat with tool call support (vLLM --tool-call-parser qwen3_xml).

        Returns:
            Dict with 'content' (str) and 'tool_calls' (list of dicts).
        """
        payload = self._build_payload(messages, temperature, max_tokens, stream=False, **kwargs)
        if tools:
            payload["tools"] = tools

        response = await self._request_with_retry("POST", "/chat/completions", json=payload)
        data = response.json()
        message = data["choices"][0]["message"]
        content = message.get("content", "") or ""

        tool_calls = []
        for tc in message.get("tool_calls", []):
            fn = tc.get("function", {})
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except (json.JSONDecodeError, TypeError):
                args = {"raw": args_str}
            tool_calls.append({
                "id": tc.get("id", ""),
                "function_name": fn.get("name", ""),
                "arguments": args,
            })

        usage = data.get("usage", {})
        if usage:
            self._track_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

        return {"content": content, "tool_calls": tool_calls}

    # --- Health and validation ---

    async def validate_server_config(self) -> Dict[str, Any]:
        """Check vLLM server configuration for recommended settings.

        Queries /v1/models and checks model availability, max_model_len, etc.
        """
        result: Dict[str, Any] = {
            "healthy": False,
            "model": self.config.model,
            "warnings": [],
            "info": {},
        }
        try:
            response = await self._request_with_retry("GET", "/models")
            data = response.json()
            models = data.get("data", [])

            if not models:
                result["warnings"].append("No models loaded on vLLM server")
                return result

            model_ids = [m.get("id", "") for m in models]
            result["info"]["available_models"] = model_ids

            if self.config.model not in model_ids:
                result["warnings"].append(
                    f"Configured model '{self.config.model}' not found. Available: {model_ids}"
                )
                return result

            result["healthy"] = True

            for m in models:
                if m.get("id") == self.config.model:
                    max_model_len = m.get("max_model_len")
                    if max_model_len:
                        result["info"]["max_model_len"] = max_model_len
                        if self.config.context_length and self.config.context_length > max_model_len:
                            result["warnings"].append(
                                f"context_length ({self.config.context_length}) > "
                                f"server max_model_len ({max_model_len})"
                            )

        except Exception as e:
            result["warnings"].append(f"Could not reach vLLM server: {e}")

        return result

    async def async_health_check(self) -> bool:
        """Async health check with retry."""
        try:
            await self._request_with_retry("GET", "/models")
            return True
        except Exception as e:
            self._health_check_error = str(e)
            return False
