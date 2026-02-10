"""Prompt compression engine.

Supports two modes:
1. Local: Uses Microsoft's LLMLingua-2 (BERT-based) for token-level compression.
   Requires 'llmlingua' and 'torch' packages (CPU/GPU inference).
2. LLM: Uses a fast/cheap LLM provider (e.g., Gemini Flash, GPT-4o-mini) to
   summarize/compress context. Zero local inference load.

Implements "Zipper Architecture": store full text, compress just-in-time.
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
    from utils.provider_factory import create_provider
    from providers.base import ProviderConfig, ProviderType
except ImportError:
    try:
        from ..utils.context_manager import TokenCounter
        from ..utils.provider_factory import create_provider
        from ..providers.base import ProviderConfig, ProviderType
    except ImportError:
        TokenCounter = None
        create_provider = None
        ProviderConfig = None
        ProviderType = None

logger = logging.getLogger(__name__)

_CODE_BLOCK_RE = re.compile(r'(```[\w]*\n.*?\n```)', re.DOTALL)
_COMPRESS_MARKER_RE = re.compile(r'<compress>(.*?)</compress>', re.DOTALL)


@dataclass
class CompressedResult:
    """Result of a compression operation."""
    text: str
    original_tokens: int
    compressed_tokens: int
    ratio: float
    time_ms: float


class CompressionEngine:
    """Singleton prompt compression engine.
    
    Can use either local LLMLingua-2 model (BERT) or an external LLM provider.
    Configured via environment variables:
      - COMPRESSION_BACKEND: 'llmlingua2' (BERT-based, default) or 'llm_provider' (summarization)
      - COMPRESSION_PROVIDER: 'gemini', 'openai', 'ollama', etc. (for 'llm_provider' backend)
      - COMPRESSION_MODEL: Model name (e.g., 'gemini-1.5-flash')
      - COMPRESSION_API_KEY: API key (if different from default)
    """

    _instance: Optional['CompressionEngine'] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'CompressionEngine':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        # Default to 'llmlingua2' if not specified, but check if user used old var
        self._backend = os.environ.get('COMPRESSION_BACKEND')
        if not self._backend:
            old_type = os.environ.get('COMPRESSION_TYPE', 'local').lower()
            self._backend = 'llm_provider' if old_type == 'llm' else 'llmlingua2'

        self._provider_instance = None
        self._local_model = None
        self._model_loaded = False
        self._model_lock = threading.Lock()
        self._available = True
        self._device = os.environ.get('COMPRESSION_DEVICE', 'cpu')
        self._token_counter = TokenCounter() if TokenCounter else None

    def _count_tokens(self, text: str) -> int:
        if self._token_counter:
            return self._token_counter.estimate_tokens(text)
        return len(text.split()) if text else 0

    def _ensure_model(self) -> bool:
        """Initialize the compression backend."""
        if self._model_loaded:
            return self._available

        with self._model_lock:
            if self._model_loaded:
                return self._available

            if self._backend == 'llm_provider':
                self._init_llm_provider()
            else:
                self._init_local_model()

            self._model_loaded = True
            return self._available

    def _init_llm_provider(self):
        """Initialize external LLM provider for compression."""
        if not create_provider:
            logger.error("Provider factory not available — LLM compression disabled")
            self._available = False
            return

        try:
            p_type_str = os.environ.get('COMPRESSION_PROVIDER', 'gemini').upper()
            try:
                p_type = ProviderType[p_type_str]
            except KeyError:
                logger.warning(f"Unknown provider {p_type_str}, defaulting to GEMINI")
                p_type = ProviderType.GEMINI

            api_key = os.environ.get('COMPRESSION_API_KEY') or os.environ.get(f"{p_type_str}_API_KEY")
            model = os.environ.get('COMPRESSION_MODEL', 'gemini-1.5-flash')

            config = ProviderConfig(
                type=p_type,
                api_key=api_key or "dummy", # Provider might load from env internally
                model=model,
                temperature=0.0,
            )
            
            logger.info(f"Initializing LLM compression with {p_type.name} ({model})...")
            self._provider_instance = create_provider(config)
            self._available = True
            logger.info("LLM compression provider ready")

        except Exception as e:
            logger.error(f"Failed to init LLM provider: {e}")
            self._available = False

    def _init_local_model(self):
        """Initialize local LLMLingua-2 model."""
        try:
            from llmlingua import PromptCompressor
            logger.info(f"Loading local LLMLingua-2 on {self._device}...")
            t0 = time.time()
            self._local_model = PromptCompressor(
                model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
                use_llmlingua2=True,
                device_map=self._device,
            )
            logger.info(f"LLMLingua-2 loaded in {(time.time()-t0)*1000:.0f}ms")
            self._available = True
        except ImportError:
            logger.warning("llmlingua not installed. Install with: pip install llmlingua")
            self._available = False
        except Exception as e:
            logger.warning(f"Failed to load local model: {e}")
            self._available = False

    def compress_context(
        self,
        text: str,
        rate: float = 0.5,
        target_token: int = -1,
        preserve_code_blocks: bool = True,
    ) -> CompressedResult:
        """Compress context using the configured backend."""
        orig_tokens = self._count_tokens(text)
        if not text or orig_tokens < 50:
            return CompressedResult(text, orig_tokens, orig_tokens, 1.0, 0.0)

        if not self._ensure_model():
            return CompressedResult(text, orig_tokens, orig_tokens, 1.0, 0.0)

        t0 = time.time()
        
        # Determine strict or loose compression based on preserve_code_blocks
        if preserve_code_blocks:
            compressed_text = self._compress_preserving_code(text, rate, target_token)
        else:
            compressed_text = self._compress_raw(text, rate, target_token)

        elapsed_ms = (time.time() - t0) * 1000
        comp_tokens = self._count_tokens(compressed_text)
        ratio = comp_tokens / orig_tokens if orig_tokens > 0 else 1.0

        return CompressedResult(compressed_text, orig_tokens, comp_tokens, ratio, elapsed_ms)

    def _compress_raw(self, text: str, rate: float, target_token: int) -> str:
        """Compress text using backend logic."""
        if self._backend == 'llm_provider' and self._provider_instance:
            return self._compress_with_llm(text, rate)
        elif self._local_model:
            return self._compress_with_local(text, rate, target_token)
        return text

    def _compress_with_local(self, text: str, rate: float, target_token: int) -> str:
        kwargs = {
            "context": [text],
            "rate": rate,
            "force_tokens": ['\n', '.', '!', '?', ',', ':', ';', '#', '-', '*'],
        }
        if target_token > 0:
            kwargs["target_token"] = target_token
        
        try:
            result = self._local_model.compress_prompt(**kwargs)
            return result.get("compressed_prompt", text)
        except Exception as e:
            logger.error(f"Local compression failed: {e}")
            return text

    def _compress_with_llm(self, text: str, rate: float) -> str:
        """Use LLM to summarize/compress text."""
        # Simple zero-shot summarization prompt
        prompt = (
            f"Compress the following text to approximately {int(rate*100)}% of its original length. "
            "Preserve all key technical details, variable names, and logic. "
            "Remove redundancy and verbosity.\n\n"
            f"TEXT:\n{text}"
        )
        
        # Run in a separate thread to avoid event loop conflicts with async callers
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        
        def run_in_new_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Assuming provider.chat is async. If it's sync (some might be?), this still works
                # but we need to check if chat is a coroutine.
                # Our providers return a coroutine or response. 
                # Based on provider_factory, they are async.
                coro = self._provider_instance.chat([{'role': 'user', 'content': prompt}])
                if asyncio.iscoroutine(coro):
                    return loop.run_until_complete(coro)
                return coro
            finally:
                loop.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_new_loop)
            try:
                return future.result(timeout=60) # 60s timeout
            except Exception as e:
                logger.error(f"LLM compression failed/timed out: {e}")
                return text

    def _compress_preserving_code(self, text: str, rate: float, target_token: int) -> str:
        """Compress text while preserving fenced code blocks verbatim."""
        code_blocks = []
        placeholder_tmpl = "\n__CODE_BLOCK_{}_PRESERVED__\n"

        def replace_cb(match):
            idx = len(code_blocks)
            code_blocks.append((placeholder_tmpl.format(idx), match.group(0)))
            return placeholder_tmpl.format(idx)

        text_with_placeholders = _CODE_BLOCK_RE.sub(replace_cb, text)
        compressed = self._compress_raw(text_with_placeholders, rate, target_token)

        for placeholder, original in code_blocks:
            compressed = compressed.replace(placeholder.strip(), original)
        
        return compressed

    def compress_messages(self, messages: List[Dict], config: Dict) -> Tuple[List[Dict], Dict]:
        """Compress <compress> blocks within messages."""
        rate = config.get('rate', 0.5)
        preserve_code = config.get('preserve_code_blocks', True)
        
        total_orig = 0
        total_comp = 0
        total_time = 0.0
        blocks = 0

        new_msgs = []
        for msg in messages:
            content = msg.get('content', '')
            if msg.get('role') == 'system' or not content:
                new_msgs.append(dict(msg))
                continue

            markers = list(_COMPRESS_MARKER_RE.finditer(content))
            if not markers:
                new_msgs.append(dict(msg))
                continue

            new_content = content
            # Reverse order to keep indices valid
            for match in reversed(markers):
                block_text = match.group(1)
                res = self.compress_context(block_text, rate, -1, preserve_code)
                
                total_orig += res.original_tokens
                total_comp += res.compressed_tokens
                total_time += res.time_ms
                blocks += 1

                new_content = (
                    new_content[:match.start()]
                    + res.text
                    + new_content[match.end():]
                )
            new_msgs.append({**msg, 'content': new_content})

        metrics = {
            'original_tokens': total_orig,
            'compressed_tokens': total_comp,
            'compression_ratio': (total_comp / total_orig if total_orig > 0 else 1.0),
            'compression_time_ms': round(total_time, 1),
            'blocks_compressed': blocks,
        }
        
        if blocks > 0:
            logger.info(
                f"Compressed {blocks} blocks: {total_orig}->{total_comp} "
                f"(ratio={metrics['compression_ratio']:.2f}, time={total_time:.0f}ms) "
                f"using {self._backend}"
            )

        return new_msgs, metrics