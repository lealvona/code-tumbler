"""Google Gemini Provider - Uses Google GenAI SDK for Gemini models."""

import os
from typing import List, Dict, Any, Iterator

from google import genai
from google.genai import types

from .base import LLMProvider, ProviderConfig

# Avoid relative imports that can cause issues
try:
    from utils.logger import get_logger
except ImportError:
    # Fallback for when running from different contexts
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.logger import get_logger


class GeminiProvider(LLMProvider):
    """Provider for Google Gemini models via Google GenAI SDK.

    Supports models like:
    - gemini-2.0-flash-exp (latest)
    - gemini-2.5-flash (newest)
    - gemini-1.5-pro
    - gemini-1.5-flash
    """

    def __init__(self, config: ProviderConfig):
        """Initialize Gemini provider.

        Args:
            config: Provider configuration
        """
        super().__init__(config)
        self.logger = get_logger(f"provider.gemini.{config.name}")

        # Initialize usage tracking
        self.usage = {
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 0,
            'total_cost': 0.0
        }

        # Get API key from environment
        api_key_env = config.api_key_env or "GOOGLE_API_KEY"
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise ValueError(
                f"Gemini API key not found. Set {api_key_env} environment variable."
            )

        # Create client with explicit API key
        self.client = genai.Client(api_key=api_key)

        self.logger.info(f"Initialized Gemini provider: {config.model}")

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Response text from the model
        """
        # Convert messages to Gemini format
        contents, system_instruction = self._convert_messages(messages)

        # Get generation config
        generation_config = self._get_generation_config(kwargs, system_instruction)

        # Make request
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=generation_config
            )

            # Extract text
            response_text = response.text

            # Track usage
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                self._track_usage(
                    input_tokens=response.usage_metadata.prompt_token_count or 0,
                    output_tokens=response.usage_metadata.candidates_token_count or 0
                )

            return response_text

        except Exception as e:
            self.logger.error(f"Gemini API error: {e}")
            raise

    def stream_chat(self, messages: List[Dict[str, str]], **kwargs) -> Iterator[str]:
        """Stream a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional parameters

        Yields:
            Response text chunks
        """
        # Convert messages to Gemini format
        contents, system_instruction = self._convert_messages(messages)

        # Get generation config
        generation_config = self._get_generation_config(kwargs, system_instruction)

        # Make streaming request
        try:
            total_input_tokens = 0
            total_output_tokens = 0

            for chunk in self.client.models.generate_content_stream(
                model=self.config.model,
                contents=contents,
                config=generation_config
            ):
                if chunk.text:
                    yield chunk.text

                # Track usage from each chunk
                if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                    total_input_tokens = chunk.usage_metadata.prompt_token_count or 0
                    total_output_tokens = chunk.usage_metadata.candidates_token_count or 0

            # Track final usage
            if total_input_tokens > 0 or total_output_tokens > 0:
                self._track_usage(
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens
                )

        except Exception as e:
            self.logger.error(f"Gemini streaming error: {e}")
            raise

    def list_models(self) -> List[str]:
        """List available Gemini models.

        Returns:
            List of model names
        """
        try:
            model_names = []
            for model in self.client.models.list():
                # Check if model supports generateContent
                if hasattr(model, 'supported_actions'):
                    for action in model.supported_actions:
                        if action == "generateContent":
                            # Remove 'models/' prefix if present
                            name = model.name
                            if name.startswith('models/'):
                                name = name[7:]
                            model_names.append(name)
                            break

            return sorted(model_names)
        except Exception as e:
            self.logger.error(f"Error listing models: {e}")
            return []

    def health_check(self) -> bool:
        """Check if the Gemini API is accessible.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Try to list models as a health check
            models = list(self.client.models.list())
            return len(models) > 0
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return False

    def _convert_messages(self, messages: List[Dict[str, str]]) -> tuple[List[types.Content], str]:
        """Convert standard messages to Gemini format.

        Gemini uses a different message format:
        - 'user' role maps to user messages
        - 'assistant' role maps to model messages
        - 'system' role is handled via system_instruction (separate parameter)

        Args:
            messages: Standard message format

        Returns:
            Tuple of (contents list, system_instruction string)
        """
        contents = []
        system_instruction = None

        for msg in messages:
            role = msg['role']
            content = msg['content']

            if role == 'system':
                # Extract system instruction (use first one found)
                if system_instruction is None:
                    system_instruction = content
            elif role == 'user':
                contents.append(
                    types.Content(
                        role='user',
                        parts=[types.Part.from_text(text=content)]
                    )
                )
            elif role == 'assistant':
                contents.append(
                    types.Content(
                        role='model',  # Gemini uses 'model' not 'assistant'
                        parts=[types.Part.from_text(text=content)]
                    )
                )

        return contents, system_instruction or ""

    def _get_generation_config(
        self,
        kwargs: Dict[str, Any],
        system_instruction: str
    ) -> types.GenerateContentConfig:
        """Build generation config from kwargs.

        Args:
            kwargs: Additional parameters
            system_instruction: System instruction string

        Returns:
            Gemini generation config
        """
        config_dict = {}

        # System instruction
        if system_instruction:
            config_dict['system_instruction'] = system_instruction

        # Temperature
        if 'temperature' in kwargs:
            config_dict['temperature'] = kwargs['temperature']
        elif self.config.temperature is not None:
            config_dict['temperature'] = self.config.temperature

        # Max tokens
        if 'max_tokens' in kwargs:
            config_dict['max_output_tokens'] = kwargs['max_tokens']
        elif self.config.max_tokens is not None:
            config_dict['max_output_tokens'] = self.config.max_tokens

        # Top-p
        if 'top_p' in kwargs:
            config_dict['top_p'] = kwargs['top_p']

        # Top-k
        if 'top_k' in kwargs:
            config_dict['top_k'] = kwargs['top_k']

        return types.GenerateContentConfig(**config_dict)

    def _track_usage(self, input_tokens: int, output_tokens: int):
        """Track token usage using the base class mechanism.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        """
        # Use cost_input_1k / cost_output_1k aliases if the main fields are zero
        if self.config.cost_per_1k_input_tokens == 0 and self.config.cost_input_1k:
            self.config.cost_per_1k_input_tokens = self.config.cost_input_1k
        if self.config.cost_per_1k_output_tokens == 0 and self.config.cost_output_1k:
            self.config.cost_per_1k_output_tokens = self.config.cost_output_1k

        # Delegate to base class which appends to usage_history
        super()._track_usage(input_tokens, output_tokens)
