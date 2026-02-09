"""Prompt compression engine using LLMLingua-2.

Implements the "Zipper Architecture": store full text internally,
compress just-in-time before API transmission. Never compress active
instructions or code output.

Uses Microsoft's LLMLingua-2 (BERT-based token classifier) for
token-level keep/drop decisions — no hallucinations, only removal.
"""

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from utils.context_manager import TokenCounter
except ImportError:
    try:
        from ..utils.context_manager import TokenCounter
    except ImportError:
        TokenCounter = None  # Fallback: use word splitting

logger = logging.getLogger(__name__)

# Regex to find fenced code blocks (``` with optional language tag)
_CODE_BLOCK_RE = re.compile(
    r'(```[\w]*\n.*?\n```)',
    re.DOTALL,
)

# Regex to find <compress>...</compress> markers
_COMPRESS_MARKER_RE = re.compile(
    r'<compress>(.*?)</compress>',
    re.DOTALL,
)


@dataclass
class CompressedResult:
    """Result of a compression operation."""
    text: str
    original_tokens: int
    compressed_tokens: int
    ratio: float
    time_ms: float


class CompressionEngine:
    """Singleton prompt compression engine using LLMLingua-2.

    Lazy-loads the model on first use to avoid startup cost when
    compression is disabled. Thread-safe via lock on model loading.

    Falls back gracefully if llmlingua is not installed — returns
    text unchanged with a warning log.
    """

    _instance: Optional['CompressionEngine'] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'CompressionEngine':
        """Get or create the singleton instance (lazy model loading)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._model = None
        self._model_loaded = False
        self._model_lock = threading.Lock()
        self._available = True
        self._device = os.environ.get('COMPRESSION_DEVICE', 'cpu')
        self._token_counter = TokenCounter() if TokenCounter is not None else None

    def _count_tokens(self, text: str) -> int:
        """Count tokens using TokenCounter if available, else word splitting."""
        if self._token_counter is not None:
            return self._token_counter.estimate_tokens(text)
        return len(text.split()) if text else 0

    def _ensure_model(self) -> bool:
        """Load the LLMLingua-2 model if not already loaded.

        Returns:
            True if model is available, False if loading failed.
        """
        if self._model_loaded:
            return self._available

        with self._model_lock:
            if self._model_loaded:
                return self._available

            try:
                from llmlingua import PromptCompressor
                logger.info(
                    "Loading LLMLingua-2 model "
                    "(microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank) "
                    f"on device={self._device}..."
                )
                t0 = time.time()
                self._model = PromptCompressor(
                    model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
                    use_llmlingua2=True,
                    device_map=self._device,
                )
                elapsed = (time.time() - t0) * 1000
                logger.info(f"LLMLingua-2 model loaded in {elapsed:.0f}ms")
                self._available = True
            except ImportError:
                logger.warning(
                    "llmlingua package not installed — compression disabled. "
                    "Install with: pip install llmlingua"
                )
                self._available = False
            except Exception as e:
                logger.warning(
                    f"Failed to load LLMLingua-2 model — compression disabled: {e}"
                )
                self._available = False

            self._model_loaded = True
            return self._available

    def compress_context(
        self,
        text: str,
        rate: float = 0.5,
        target_token: int = -1,
        preserve_code_blocks: bool = True,
    ) -> CompressedResult:
        """Compress context text, optionally preserving fenced code blocks.

        Args:
            text: The context text to compress.
            rate: Token retention rate (0.5 = keep 50% of tokens).
            target_token: If >0, compress to this many tokens (overrides rate).
            preserve_code_blocks: If True, extract fenced code blocks before
                compression and reinsert them after.

        Returns:
            CompressedResult with compressed text and metrics.
        """
        if not text or self._count_tokens(text) < 50:
            # Too short to benefit from compression
            tokens = self._count_tokens(text)
            return CompressedResult(
                text=text,
                original_tokens=tokens,
                compressed_tokens=tokens,
                ratio=1.0,
                time_ms=0.0,
            )

        if not self._ensure_model():
            # Fallback: return unchanged
            tokens = self._count_tokens(text)
            return CompressedResult(
                text=text,
                original_tokens=tokens,
                compressed_tokens=tokens,
                ratio=1.0,
                time_ms=0.0,
            )

        t0 = time.time()

        if preserve_code_blocks:
            compressed_text = self._compress_preserving_code(text, rate, target_token)
        else:
            compressed_text = self._compress_raw(text, rate, target_token)

        elapsed_ms = (time.time() - t0) * 1000

        orig_tokens = self._count_tokens(text)
        comp_tokens = self._count_tokens(compressed_text)
        ratio = comp_tokens / orig_tokens if orig_tokens > 0 else 1.0

        return CompressedResult(
            text=compressed_text,
            original_tokens=orig_tokens,
            compressed_tokens=comp_tokens,
            ratio=ratio,
            time_ms=elapsed_ms,
        )

    def _compress_raw(self, text: str, rate: float, target_token: int) -> str:
        """Compress text directly without code block preservation."""
        kwargs = {
            "context": [text],
            "rate": rate,
            "force_tokens": ['\n', '.', '!', '?', ',', ':', ';', '#', '-', '*'],
        }
        if target_token > 0:
            kwargs["target_token"] = target_token

        result = self._model.compress_prompt(**kwargs)
        return result.get("compressed_prompt", text)

    def _compress_preserving_code(
        self, text: str, rate: float, target_token: int
    ) -> str:
        """Compress text while preserving fenced code blocks verbatim."""
        # Find all code blocks and replace with placeholders
        code_blocks: List[Tuple[str, str]] = []
        placeholder_template = "\n__CODE_BLOCK_{}_PRESERVED__\n"

        def replace_code_block(match):
            idx = len(code_blocks)
            code_blocks.append((placeholder_template.format(idx), match.group(0)))
            return placeholder_template.format(idx)

        text_with_placeholders = _CODE_BLOCK_RE.sub(replace_code_block, text)

        # Compress the text (with placeholders intact)
        compressed = self._compress_raw(text_with_placeholders, rate, target_token)

        # Reinsert code blocks
        for placeholder, original_code in code_blocks:
            compressed = compressed.replace(placeholder.strip(), original_code)

        return compressed

    def compress_messages(
        self,
        messages: List[Dict[str, str]],
        config: Dict,
    ) -> Tuple[List[Dict[str, str]], Dict]:
        """Compress <compress> blocks within messages.

        Finds <compress>...</compress> markers in message content,
        compresses the text within, and replaces the markers with
        compressed text. System prompts and unmarked content are
        left untouched.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            config: Compression config dict with keys:
                - rate (float): Token retention rate, default 0.5
                - preserve_code_blocks (bool): Preserve fenced code, default True

        Returns:
            Tuple of (compressed_messages, metrics_dict).
        """
        rate = config.get('rate', 0.5)
        preserve_code = config.get('preserve_code_blocks', True)

        total_original = 0
        total_compressed = 0
        total_time_ms = 0.0
        blocks_compressed = 0

        compressed_messages = []
        for msg in messages:
            content = msg.get('content', '')

            # Never compress system prompts
            if msg.get('role') == 'system':
                compressed_messages.append(dict(msg))
                continue

            # Find and compress <compress> blocks
            markers = list(_COMPRESS_MARKER_RE.finditer(content))
            if not markers:
                compressed_messages.append(dict(msg))
                continue

            new_content = content
            # Process in reverse order to maintain string positions
            for match in reversed(markers):
                block_text = match.group(1)
                result = self.compress_context(
                    block_text,
                    rate=rate,
                    preserve_code_blocks=preserve_code,
                )
                total_original += result.original_tokens
                total_compressed += result.compressed_tokens
                total_time_ms += result.time_ms
                blocks_compressed += 1

                # Replace the full <compress>...</compress> with compressed text
                new_content = (
                    new_content[:match.start()]
                    + result.text
                    + new_content[match.end():]
                )

            compressed_messages.append({**msg, 'content': new_content})

        metrics = {
            'original_tokens': total_original,
            'compressed_tokens': total_compressed,
            'compression_ratio': (
                total_compressed / total_original if total_original > 0 else 1.0
            ),
            'compression_time_ms': round(total_time_ms, 1),
            'blocks_compressed': blocks_compressed,
        }

        if blocks_compressed > 0:
            logger.info(
                f"Compressed {blocks_compressed} blocks: "
                f"{total_original} -> {total_compressed} tokens "
                f"(ratio={metrics['compression_ratio']:.2f}, "
                f"time={total_time_ms:.0f}ms)"
            )

        return compressed_messages, metrics
