"""Ollama provider implementation.

Ollama is a local LLM runtime that provides an OpenAI-compatible API.
It's ideal for running models locally without API costs.
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


class OllamaProvider(LLMProvider):
    """Provider for Ollama local LLM runtime.

    Ollama provides a simple HTTP API for running LLMs locally.
    Default URL: http://localhost:11434
    """

    def __init__(self, config: ProviderConfig):
        """Initialize Ollama provider.

        Args:
            config: Provider configuration with base_url and model.
        """
        super().__init__(config)
        self.base_url = config.base_url or "http://localhost:11434"
        self.api_url = f"{self.base_url}/api"

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
        """Send a chat completion request to Ollama.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional parameters for Ollama.

        Returns:
            The assistant's response as a string.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/chat"

        # Build request payload
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature if temperature is not None else self.config.temperature,
            }
        }

        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        elif self.config.max_tokens is not None:
            payload["options"]["num_predict"] = self.config.max_tokens

        # Add any extra parameters
        payload["options"].update(kwargs)

        # Make request
        response = requests.post(
            url,
            json=payload,
            timeout=self.config.timeout
        )
        response.raise_for_status()

        # Parse response
        data = response.json()
        message = data.get("message", {})
        content = message.get("content", "")

        # Track usage (Ollama provides token counts)
        if "prompt_eval_count" in data and "eval_count" in data:
            self._track_usage(
                input_tokens=data["prompt_eval_count"],
                output_tokens=data["eval_count"]
            )

        return content

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Iterator[str]:
        """Stream a chat completion response from Ollama.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (default: 0.7).
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional parameters for Ollama.

        Yields:
            Chunks of the assistant's response.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/chat"

        # Build request payload
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature if temperature is not None else self.config.temperature,
            }
        }

        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        elif self.config.max_tokens is not None:
            payload["options"]["num_predict"] = self.config.max_tokens

        payload["options"].update(kwargs)

        # Stream response
        response = requests.post(
            url,
            json=payload,
            stream=True,
            timeout=self.config.timeout
        )
        response.raise_for_status()

        input_tokens = 0
        output_tokens = 0

        # Process streaming response
        for line in response.iter_lines():
            if line:
                import json
                data = json.loads(line)

                # Extract token counts if available
                if "prompt_eval_count" in data:
                    input_tokens = data["prompt_eval_count"]
                if "eval_count" in data:
                    output_tokens = data["eval_count"]

                # Yield content chunk
                message = data.get("message", {})
                content = message.get("content", "")
                if content:
                    yield content

                # Check if done
                if data.get("done", False):
                    # Track final usage
                    if input_tokens or output_tokens:
                        self._track_usage(input_tokens, output_tokens)
                    break

    def list_models(self) -> List[str]:
        """List available models from Ollama.

        Returns:
            List of model names installed in Ollama.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/tags"

        response = requests.get(url, timeout=self.config.timeout)
        response.raise_for_status()

        data = response.json()
        models = data.get("models", [])

        # Extract model names
        return [model.get("name", "") for model in models if model.get("name")]

    def pull_model(self, model_name: str) -> Iterator[Dict[str, any]]:
        """Pull (download) a model from Ollama's library.

        Args:
            model_name: Name of the model to pull (e.g., "llama3.1:70b").

        Yields:
            Progress updates as dicts with status and completion info.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/pull"

        payload = {
            "name": model_name,
            "stream": True
        }

        response = requests.post(
            url,
            json=payload,
            stream=True,
            timeout=None  # Pulling models can take a long time
        )
        response.raise_for_status()

        # Stream progress updates
        for line in response.iter_lines():
            if line:
                import json
                yield json.loads(line)

    def delete_model(self, model_name: str) -> None:
        """Delete a model from Ollama.

        Args:
            model_name: Name of the model to delete.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.api_url}/delete"

        payload = {"name": model_name}

        response = requests.delete(url, json=payload, timeout=self.config.timeout)
        response.raise_for_status()

    # --- Async infrastructure ---

    def _get_client(self) -> "httpx.AsyncClient":
        """Lazily create the async HTTP client."""
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for async Ollama operations. Install with: pip install httpx")
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                base_url=self.api_url,
                timeout=httpx.Timeout(connect=30.0, read=float(self.config.timeout), write=30.0, pool=10.0),
            )
        return self._async_client

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily create the concurrency semaphore."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.config.concurrency_limit)
        return self._semaphore

    async def _request_with_retry(self, method: str, url: str, **kwargs) -> "httpx.Response":
        """HTTP request with exponential backoff on 429/503, guarded by semaphore."""
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
                            f"Ollama returned {response.status_code}, "
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
                            f"Ollama request timed out, retrying in {delay:.1f}s "
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
        """Build Ollama request payload."""
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature if temperature is not None else self.config.temperature,
            },
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        elif self.config.max_tokens is not None:
            payload["options"]["num_predict"] = self.config.max_tokens
        payload["options"].update(kwargs)
        return payload

    async def async_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> str:
        """Async chat completion to Ollama with retry and semaphore."""
        payload = self._build_payload(messages, temperature, max_tokens, stream=False, **kwargs)
        response = await self._request_with_retry("POST", "/chat", json=payload)
        data = response.json()

        message = data.get("message", {})
        content = message.get("content", "")

        if "prompt_eval_count" in data and "eval_count" in data:
            self._track_usage(data["prompt_eval_count"], data["eval_count"])

        return content

    async def async_stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Async streaming chat completion from Ollama."""
        payload = self._build_payload(messages, temperature, max_tokens, stream=True, **kwargs)
        client = self._get_client()
        sem = self._get_semaphore()
        input_tokens = 0
        output_tokens = 0

        async with sem:
            async with client.stream("POST", "/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if "prompt_eval_count" in data:
                        input_tokens = data["prompt_eval_count"]
                    if "eval_count" in data:
                        output_tokens = data["eval_count"]

                    message = data.get("message", {})
                    content = message.get("content", "")
                    if content:
                        yield content

                    if data.get("done", False):
                        break

        if input_tokens or output_tokens:
            self._track_usage(input_tokens, output_tokens)

    # --- Health check ---

    async def async_health_check(self) -> bool:
        """Async health check with retry."""
        try:
            await self._request_with_retry("GET", "/tags")
            return True
        except Exception as e:
            self._health_check_error = str(e)
            return False
