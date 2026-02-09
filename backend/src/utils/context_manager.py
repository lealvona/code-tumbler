"""Context window management for Code Tumbler agents.

Provides token estimation, context budget calculation, and content
truncation/chunking to prevent context overflow before API calls.

Uses a character-based heuristic (~3.8 chars/token) by default, with
optional tiktoken for OpenAI models when the package is available.
"""

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token Counter
# ---------------------------------------------------------------------------

class TokenCounter:
    """Estimates token counts for text across different LLM providers.

    Default heuristic: ~3.8 characters per token for English/mixed text,
    ~3.3 for code-heavy text. Accurate within ~15% for most modern
    tokenizers (BPE, SentencePiece, Unigram).

    When tiktoken is installed and the provider is OpenAI-compatible,
    uses the real cl100k_base encoder for precise counts.
    """

    DEFAULT_CHARS_PER_TOKEN = 3.8
    CODE_CHARS_PER_TOKEN = 3.3
    MESSAGE_OVERHEAD_TOKENS = 4  # role + delimiters per message

    _tiktoken_encoder = None
    _tiktoken_checked = False

    @classmethod
    def _get_tiktoken(cls):
        """Lazy-load tiktoken encoder (singleton). Returns None if unavailable."""
        if not cls._tiktoken_checked:
            cls._tiktoken_checked = True
            try:
                import tiktoken
                cls._tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
                logger.debug("tiktoken cl100k_base encoder loaded")
            except (ImportError, Exception):
                logger.debug("tiktoken not available, using heuristic token counting")
                cls._tiktoken_encoder = None
        return cls._tiktoken_encoder

    @staticmethod
    def _looks_like_code(text: str) -> bool:
        """Heuristic: does this text contain mostly code?"""
        if len(text) < 100:
            return False
        code_indicators = 0
        for pattern in ('{', '}', 'def ', 'function ', 'import ', 'class ', '/**', '#include', '};'):
            code_indicators += text.count(pattern)
        # If there are more than 5 code indicators per 1000 chars, it's code-heavy
        return (code_indicators / max(1, len(text))) * 1000 > 5

    def estimate_tokens(self, text: str, provider_type: Optional[str] = None) -> int:
        """Estimate the token count for a piece of text.

        Args:
            text: The text to count tokens for.
            provider_type: Provider type string (e.g. "openai"). If openai and
                tiktoken is available, uses precise encoding.

        Returns:
            Estimated token count (always >= 1 for non-empty text).
        """
        if not text:
            return 0

        # Try tiktoken for OpenAI-compatible providers
        if provider_type in ("openai", "vllm"):
            enc = self._get_tiktoken()
            if enc is not None:
                try:
                    return len(enc.encode(text))
                except Exception:
                    pass  # fall through to heuristic

        # Heuristic estimation
        cpt = self.CODE_CHARS_PER_TOKEN if self._looks_like_code(text) else self.DEFAULT_CHARS_PER_TOKEN
        return max(1, math.ceil(len(text) / cpt))

    def estimate_messages_tokens(
        self, messages: List[Dict[str, str]], provider_type: Optional[str] = None
    ) -> int:
        """Estimate total tokens for a messages array.

        Includes per-message overhead for role/delimiters (~4 tokens each)
        plus a base overhead of ~3 tokens for the conversation wrapper.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            provider_type: Provider type string.

        Returns:
            Estimated total token count.
        """
        total = 3  # conversation priming overhead
        for msg in messages:
            total += self.MESSAGE_OVERHEAD_TOKENS
            total += self.estimate_tokens(msg.get("content", ""), provider_type)
        return total


# ---------------------------------------------------------------------------
# Context Budget
# ---------------------------------------------------------------------------

@dataclass
class ContextBudget:
    """Calculated token budget for a single LLM request."""

    context_length: int          # Total model context window (tokens)
    system_prompt_tokens: int    # Tokens consumed by system prompt
    max_output_tokens: int       # Reserved for model output
    safety_margin: int           # Buffer (5% of context_length)

    @property
    def available_input(self) -> int:
        """Tokens available for all input messages (system + user)."""
        return self.context_length - self.max_output_tokens - self.safety_margin

    @property
    def content_budget(self) -> int:
        """Tokens available for user message content (after system prompt)."""
        return max(0, self.available_input - self.system_prompt_tokens)

    def fits(self, user_content_tokens: int) -> bool:
        """Check if user content fits within the budget."""
        return user_content_tokens <= self.content_budget

    def clamped_max_tokens(self, input_tokens: int) -> int:
        """Clamp output tokens so input + output + safety fit the context."""
        headroom = self.context_length - input_tokens - self.safety_margin
        return max(256, min(self.max_output_tokens, headroom))


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------

# Known context lengths for common models (tokens).
# Keyed by model name prefix — longest prefix match wins.
MODEL_CONTEXT_DEFAULTS: Dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4-1": 1_000_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o3": 200_000,
    # Anthropic
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-4": 200_000,
    # Google Gemini
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-flash-lite": 1_000_000,
    "gemini-3": 1_000_000,
    "gemini-1.5-pro": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
    # Qwen
    "qwen": 32_768,
    # Llama
    "llama-3": 128_000,
    "llama-3.1": 128_000,
    "meta-llama/llama-3": 128_000,
    # DeepSeek
    "deepseek": 64_000,
}

DEFAULT_CONTEXT_LENGTH = 32_768


class ContextManager:
    """Manages context window budgets and content fitting for LLM requests.

    Computes available token budgets, determines whether truncation or
    chunking is needed, and provides strategies for both.
    """

    SAFETY_MARGIN_RATIO = 0.05  # 5% of context reserved as buffer

    def __init__(self):
        self._token_counter = TokenCounter()

    @property
    def token_counter(self) -> TokenCounter:
        return self._token_counter

    # -- Context length resolution ------------------------------------------

    @staticmethod
    def get_context_length(config) -> int:
        """Resolve the context window size for a provider config.

        Priority: config.context_length > model name lookup > default (32K).

        Args:
            config: ProviderConfig instance.

        Returns:
            Context window size in tokens.
        """
        # 1. Explicit config value
        if getattr(config, "context_length", None) is not None:
            return config.context_length

        # 2. Model name lookup (longest prefix match)
        model = getattr(config, "model", "") or ""
        model_lower = model.lower()
        best_match = ""
        best_length = DEFAULT_CONTEXT_LENGTH

        for prefix, length in MODEL_CONTEXT_DEFAULTS.items():
            if model_lower.startswith(prefix.lower()) and len(prefix) > len(best_match):
                best_match = prefix
                best_length = length

        if best_match:
            logger.debug(
                f"Context length for model '{model}' resolved via prefix '{best_match}': {best_length}"
            )
        else:
            logger.debug(
                f"No known context length for model '{model}', using default {DEFAULT_CONTEXT_LENGTH}. "
                f"Set 'context_length' in config.yaml for this provider to override."
            )

        return best_length

    # -- Budget calculation -------------------------------------------------

    def calculate_budget(
        self,
        config,
        system_prompt: str,
        max_output_tokens: int,
    ) -> ContextBudget:
        """Calculate the token budget for a request.

        Args:
            config: ProviderConfig instance.
            system_prompt: The system prompt text.
            max_output_tokens: Desired output token budget.

        Returns:
            ContextBudget with all values computed.
        """
        context_length = self.get_context_length(config)
        safety_margin = max(64, int(context_length * self.SAFETY_MARGIN_RATIO))

        provider_type = getattr(config, "type", None)
        ptype_str = provider_type.value if hasattr(provider_type, "value") else str(provider_type)
        system_tokens = self._token_counter.estimate_tokens(system_prompt, ptype_str)

        # Clamp output tokens if they'd leave too little room for input
        available_for_input = context_length - max_output_tokens - safety_margin
        if available_for_input < system_tokens + 500:
            # Not enough room — reduce output budget
            max_output_tokens = max(
                256,
                context_length - safety_margin - system_tokens - 500,
            )
            logger.warning(
                f"Output budget clamped to {max_output_tokens} tokens to leave room for input "
                f"(context={context_length}, system={system_tokens})"
            )

        return ContextBudget(
            context_length=context_length,
            system_prompt_tokens=system_tokens,
            max_output_tokens=max_output_tokens,
            safety_margin=safety_margin,
        )

    # -- Content truncation -------------------------------------------------

    def truncate_file_content(
        self,
        files: Dict[str, str],
        budget_tokens: int,
        priority_files: Optional[List[str]] = None,
        provider_type: Optional[str] = None,
    ) -> Tuple[Dict[str, str], int]:
        """Truncate a dict of file contents to fit within a token budget.

        Strategy:
        1. Always include priority files (mentioned in feedback / errors).
        2. Include remaining files in order until budget is exhausted.
        3. Files that don't fit get a "[content omitted — N lines]" stub.

        Args:
            files: Dict mapping file_path -> content.
            budget_tokens: Maximum tokens to use for all file content.
            priority_files: File paths to include first (e.g., files with errors).
            provider_type: For token counting accuracy.

        Returns:
            Tuple of (truncated_files dict, total_tokens_used).
        """
        if not files:
            return {}, 0

        priority_files = priority_files or []
        tc = self._token_counter

        # Score each file
        scored: List[Tuple[str, str, int, bool]] = []  # (path, content, tokens, is_priority)
        for path, content in files.items():
            is_priority = path in priority_files
            tokens = tc.estimate_tokens(content, provider_type) if content else 0
            scored.append((path, content, tokens, is_priority))

        # Sort: priority files first, then by token count ascending (include small files first)
        scored.sort(key=lambda x: (not x[3], x[2]))

        result: Dict[str, str] = {}
        used = 0

        for path, content, tokens, is_priority in scored:
            if not content or content.startswith("["):
                # Already a stub — include as-is (costs ~10 tokens)
                result[path] = content
                used += 10
                continue

            if used + tokens <= budget_tokens:
                result[path] = content
                used += tokens
            else:
                # Doesn't fit — add a stub
                lines = len(content.split("\n"))
                result[path] = f"[content omitted for context — {lines} lines]"
                used += 15  # stub costs ~15 tokens

        return result, used

    # -- Chunk planning for Engineer ----------------------------------------

    def plan_chunks(
        self,
        file_list: List[str],
        output_budget_tokens: int,
        tokens_per_file: int = 550,
        max_concurrent: int = 7,
    ) -> List[List[str]]:
        """Split a file list into chunks that fit the output budget.

        Each chunk should be small enough that the model can generate all
        files in that chunk within the output token limit.

        Args:
            file_list: List of file paths to generate.
            output_budget_tokens: Max output tokens per request.
            tokens_per_file: Estimated average tokens per file (including JSON wrapper).
            max_concurrent: Maximum number of chunks (concurrent requests).

        Returns:
            List of file-path lists, one per chunk.
        """
        if not file_list:
            return [file_list]

        files_per_chunk = max(1, output_budget_tokens // tokens_per_file)

        # Split into chunks
        chunks: List[List[str]] = []
        for i in range(0, len(file_list), files_per_chunk):
            chunks.append(file_list[i : i + files_per_chunk])

        # If we have more chunks than max_concurrent, merge the smallest tail chunks
        while len(chunks) > max_concurrent and len(chunks) > 1:
            # Merge the last two chunks
            last = chunks.pop()
            chunks[-1].extend(last)

        logger.info(
            f"Planned {len(chunks)} chunk(s) for {len(file_list)} files "
            f"(~{files_per_chunk} files/chunk, output budget={output_budget_tokens})"
        )

        return chunks
