"""Base agent class for Code Tumbler agents.

All agents (Architect, Engineer, Verifier) inherit from this base class
which provides common functionality for LLM interaction and usage tracking.
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
from pathlib import Path

try:
    from providers.base import LLMProvider
    from utils.context_manager import ContextManager, TokenCounter
except ImportError:
    from ..providers.base import LLMProvider
    from ..utils.context_manager import ContextManager, TokenCounter

logger = logging.getLogger(__name__)


class DegenerateOutputError(Exception):
    """Raised when the LLM output is detected as degenerate (repetitive garbage)."""
    pass


class BaseAgent(ABC):
    """Abstract base class for all Code Tumbler agents.

    Provides common functionality for:
    - LLM provider management
    - Message construction
    - Usage tracking
    - Context management
    """

    # Subclasses override to set per-agent max_tokens defaults.
    # This is only used when neither the caller nor the provider config
    # specifies a max_tokens value.
    default_max_tokens: Optional[int] = 8192

    # Regex to strip EOS tokens and spurious tool call hallucinations from output
    _eos_re = re.compile(
        r'<\|endoftext\|>.*|<\|im_end\|>.*|<\|eot_id\|>.*|</tool_call>.*',
        re.DOTALL,
    )

    def __init__(self, provider: LLMProvider, system_prompt: str, name: str,
                 nothink_override: Optional[bool] = None):
        """Initialize the agent.

        Args:
            provider: LLM provider instance to use for completions
            system_prompt: System prompt that defines agent behavior
            name: Agent name for logging and tracking
            nothink_override: Per-agent override for nothink (True/False/None=auto)
        """
        self.provider = provider
        self.system_prompt = system_prompt
        self.name = name
        self._nothink_override = nothink_override
        self.usage_history: List[Dict[str, Any]] = []
        self._on_chunk: Optional[Callable[[str], None]] = None
        self.last_compression_metrics: Dict[str, Any] = {}
        self._context_manager = ContextManager()
        self._token_counter = self._context_manager.token_counter

    def set_provider(self, provider: LLMProvider) -> None:
        """Replace the current LLM provider (hot-swap between iterations)."""
        self.provider = provider

    def _resolve_max_tokens(self, explicit: Optional[int]) -> Optional[int]:
        """Resolve max_tokens: explicit arg > provider config > agent default."""
        if explicit is not None:
            return explicit
        if self.provider.config.max_tokens is not None:
            return None  # let the provider apply its own config
        return self.default_max_tokens

    @staticmethod
    def _detect_degenerate(tail: str, min_pattern_len: int = 2, max_pattern_len: int = 20,
                           repeat_threshold: int = 10) -> bool:
        """Detect degenerate repetitive output (e.g. 'gYGBgYGB...').

        Checks if the tail of output consists of a short pattern repeated
        many times, which indicates the model is stuck in a loop.
        """
        if len(tail) < max_pattern_len * repeat_threshold:
            return False
        for plen in range(min_pattern_len, max_pattern_len + 1):
            pattern = tail[-plen:]
            # Check if the preceding text is just this pattern repeated
            check_len = plen * repeat_threshold
            segment = tail[-check_len:]
            if segment == pattern * repeat_threshold:
                return True
        return False

    # Provider types that default to /nothink when nothink is None (auto)
    _nothink_auto_types = frozenset(('openai', 'vllm', 'ollama'))

    def _should_nothink(self) -> bool:
        """Determine whether to append /nothink to the last user message.

        Resolution: per-agent override > provider config > auto-detect by provider type.
        """
        if self._nothink_override is not None:
            return self._nothink_override
        cfg = self.provider.config
        if cfg.nothink is not None:
            return cfg.nothink
        return cfg.type.value in self._nothink_auto_types

    @staticmethod
    def _inject_nothink(messages: List[Dict[str, str]]) -> None:
        """Append /nothink to the last user message in-place."""
        for msg in reversed(messages):
            if msg['role'] == 'user':
                msg['content'] += '\n/nothink'
                break

    def _apply_compression(
        self, messages: List[Dict[str, str]], compression_config: Dict
    ) -> tuple:
        """Apply prompt compression to <compress> blocks in messages.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            compression_config: Dict with 'enabled', 'rate', 'preserve_code_blocks'.

        Returns:
            Tuple of (compressed_messages, metrics_dict).
        """
        try:
            from compression.engine import CompressionEngine
            engine = CompressionEngine.get_instance()
            return engine.compress_messages(messages, compression_config)
        except ImportError:
            logger.debug("Compression module not available, stripping markers only")
            return self._strip_compress_markers(messages), {}
        except Exception as e:
            logger.warning(f"Compression failed, falling back to uncompressed: {e}")
            return self._strip_compress_markers(messages), {}

    @staticmethod
    def _strip_compress_markers(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Remove <compress>...</compress> markers without compressing."""
        import re
        marker_re = re.compile(r'</?compress>', re.IGNORECASE)
        result = []
        for msg in messages:
            content = msg.get('content', '')
            cleaned = marker_re.sub('', content)
            result.append({**msg, 'content': cleaned})
        return result

    def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """Execute the agent with given context using streaming.

        Uses stream_chat() internally so the LLM request is sent with
        stream=true, which improves connection reliability for long
        generations. Chunks are collected into the full response string.

        Includes safeguards:
        - max_tokens fallback (per-agent default) to prevent infinite generation
        - Degenerate output detection to abort early on repetitive garbage
        - Prompt compression via <compress> markers (when enabled)

        Args:
            context: Dictionary containing agent-specific context
            **kwargs: Additional parameters passed to LLM provider
                - compression_config: Dict with compression settings (popped from kwargs)

        Returns:
            Agent's response as a string

        Raises:
            DegenerateOutputError: If the output is detected as repetitive garbage
        """
        messages = self._build_messages(context)

        # Apply compression if enabled
        compression_config = kwargs.pop('compression_config', None)
        if compression_config and compression_config.get('enabled'):
            messages, compression_metrics = self._apply_compression(messages, compression_config)
            self.last_compression_metrics = compression_metrics
            if compression_metrics.get('blocks_compressed', 0) > 0:
                logger.info(
                    f"{self.name}: Compressed {compression_metrics['blocks_compressed']} blocks, "
                    f"{compression_metrics['original_tokens']} -> {compression_metrics['compressed_tokens']} tokens "
                    f"(ratio={compression_metrics['compression_ratio']:.2f})"
                )
        else:
            # Strip markers even when compression is disabled
            messages = self._strip_compress_markers(messages)
            self.last_compression_metrics = {}

        # Conditionally append /nothink to suppress thinking-mode output
        if self._should_nothink():
            self._inject_nothink(messages)

        # Get temperature and max_tokens from kwargs or use defaults
        temperature = kwargs.pop('temperature', None)
        max_tokens = self._resolve_max_tokens(kwargs.pop('max_tokens', None))

        # Pre-request context window validation
        output_budget = max_tokens or self.default_max_tokens or 8192
        budget = self._context_manager.calculate_budget(
            self.provider.config, self.system_prompt, output_budget
        )
        ptype = self.provider.config.type.value if hasattr(self.provider.config.type, 'value') else None
        input_tokens = self._token_counter.estimate_messages_tokens(messages, ptype)
        user_content_tokens = input_tokens - budget.system_prompt_tokens

        if not budget.fits(user_content_tokens):
            logger.warning(
                f"{self.name}: Input (~{input_tokens} tokens) exceeds context budget "
                f"({budget.context_length} context - {budget.max_output_tokens} output "
                f"- {budget.safety_margin} safety = {budget.available_input} available). "
                f"Truncating messages to fit."
            )
            messages = self._truncate_messages(messages, budget)

        # Clamp max_tokens so input + output + safety fit the context window
        if max_tokens is not None:
            clamped = budget.clamped_max_tokens(input_tokens)
            if clamped < max_tokens:
                logger.info(
                    f"{self.name}: Clamping max_tokens from {max_tokens} to {clamped} "
                    f"to fit context window ({budget.context_length})"
                )
                max_tokens = clamped

        # Use streaming to keep the connection alive during long generations
        chunks: List[str] = []
        total_chars = 0
        # Keep a rolling tail buffer for repetition detection
        tail_buf = ""
        tail_buf_size = 400  # chars to keep for pattern matching
        degenerate_check_interval = 200  # check every N chunks

        stream = self.provider.stream_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
        try:
            for chunk in stream:
                chunks.append(chunk)
                total_chars += len(chunk)
                tail_buf = (tail_buf + chunk)[-tail_buf_size:]
                if self._on_chunk:
                    self._on_chunk(chunk)

                # Periodically check for degenerate output
                if len(chunks) % degenerate_check_interval == 0 and total_chars > 500:
                    if self._detect_degenerate(tail_buf):
                        logger.warning(
                            f"{self.name}: Degenerate repetitive output detected "
                            f"after {total_chars} chars, aborting generation"
                        )
                        raise DegenerateOutputError(
                            f"Model output is degenerate (repetitive pattern detected "
                            f"after {total_chars} chars). The model may need a different "
                            f"prompt or temperature setting."
                        )
        except DegenerateOutputError:
            # Close the stream explicitly to avoid GeneratorExit warnings
            stream.close()
            raise

        response = "".join(chunks)

        # Strip EOS tokens and spurious suffixes that some models emit
        response = self._eos_re.sub('', response).rstrip()

        # Track usage (stream_chat tracks it internally in each provider)
        usage = self.provider.get_usage()
        self.usage_history.append({
            'agent': self.name,
            'input_tokens': usage.input_tokens,
            'output_tokens': usage.output_tokens,
            'cost': usage.cost,
        })

        return response

    def stream_execute(self, context: Dict[str, Any], **kwargs):
        """Execute the agent with streaming response.

        Args:
            context: Dictionary containing agent-specific context
            **kwargs: Additional parameters passed to LLM provider

        Yields:
            Chunks of the agent's response

        Raises:
            Exception: If the LLM request fails
        """
        messages = self._build_messages(context)

        # Apply compression if enabled
        compression_config = kwargs.pop('compression_config', None)
        if compression_config and compression_config.get('enabled'):
            messages, _ = self._apply_compression(messages, compression_config)
        else:
            messages = self._strip_compress_markers(messages)

        # Conditionally append /nothink to suppress thinking-mode output
        if self._should_nothink():
            self._inject_nothink(messages)

        # Get temperature and max_tokens from kwargs or use defaults
        temperature = kwargs.pop('temperature', None)
        max_tokens = kwargs.pop('max_tokens', None)

        # Stream the LLM response
        for chunk in self.provider.stream_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        ):
            yield chunk

        # Track usage after streaming completes
        usage = self.provider.get_usage()
        self.usage_history.append({
            'agent': self.name,
            'input_tokens': usage.input_tokens,
            'output_tokens': usage.output_tokens,
            'cost': usage.cost,
        })

    def _truncate_messages(
        self, messages: List[Dict[str, str]], budget
    ) -> List[Dict[str, str]]:
        """Truncate message content to fit within context budget.

        Default strategy: keep system prompt intact, truncate the longest
        user message from the tail until the total fits.

        Subclasses may override for agent-specific truncation (e.g.,
        dropping code files by priority rather than blind tail-truncation).

        Args:
            messages: The messages to truncate (modified copy returned).
            budget: ContextBudget with available_input and system_prompt_tokens.

        Returns:
            New messages list that fits within the budget.
        """
        ptype = self.provider.config.type.value if hasattr(self.provider.config.type, 'value') else None
        target_tokens = budget.content_budget

        result = [dict(msg) for msg in messages]

        # Find the longest non-system message
        longest_idx = -1
        longest_tokens = 0
        for i, msg in enumerate(result):
            if msg.get('role') == 'system':
                continue
            t = self._token_counter.estimate_tokens(msg.get('content', ''), ptype)
            if t > longest_tokens:
                longest_tokens = t
                longest_idx = i

        if longest_idx < 0:
            return result  # nothing to truncate

        # Estimate how many tokens to cut
        total_user_tokens = sum(
            self._token_counter.estimate_tokens(m.get('content', ''), ptype)
            for m in result if m.get('role') != 'system'
        )
        excess = total_user_tokens - target_tokens
        if excess <= 0:
            return result

        # Truncate the longest message by removing content from the middle
        # (preserve the beginning context and the end task instructions)
        content = result[longest_idx]['content']
        # Estimate chars to cut: excess tokens * chars_per_token
        chars_to_cut = int(excess * 3.8) + 200  # add margin
        if chars_to_cut >= len(content):
            # Extreme case — keep just the first and last 500 chars
            result[longest_idx]['content'] = (
                content[:500]
                + "\n\n[... content truncated to fit context window ...]\n\n"
                + content[-500:]
            )
        else:
            # Cut from the middle, preserving head and tail
            half_keep = (len(content) - chars_to_cut) // 2
            result[longest_idx]['content'] = (
                content[:half_keep]
                + "\n\n[... content truncated to fit context window ...]\n\n"
                + content[-half_keep:]
            )

        return result

    @abstractmethod
    def _build_messages(self, context: Dict[str, Any]) -> List[Dict[str, str]]:
        """Build the messages array for the LLM request.

        Each agent implements this method to construct messages
        based on its specific needs.

        Args:
            context: Dictionary containing agent-specific context

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        pass

    def get_total_usage(self) -> Dict[str, Any]:
        """Get cumulative usage statistics.

        Returns:
            Dictionary with total tokens and cost
        """
        total_input = sum(u['input_tokens'] for u in self.usage_history)
        total_output = sum(u['output_tokens'] for u in self.usage_history)
        total_cost = sum(u['cost'] for u in self.usage_history)

        return {
            'agent': self.name,
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'total_tokens': total_input + total_output,
            'total_cost': total_cost,
            'num_requests': len(self.usage_history),
        }

    def reset_usage(self) -> None:
        """Reset usage history."""
        self.usage_history = []

    def load_file(self, file_path: Path) -> str:
        """Load content from a file.

        Utility method for agents that need to read files.

        Args:
            file_path: Path to the file to read

        Returns:
            File content as string

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        return file_path.read_text(encoding='utf-8')

    def save_file(self, file_path: Path, content: str) -> None:
        """Save content to a file.

        Utility method for agents that need to write files.
        Creates parent directories if they don't exist.

        Args:
            file_path: Path where to save the file
            content: Content to write
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding='utf-8')
