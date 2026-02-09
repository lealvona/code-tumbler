# Prompt Compression Engine ("Zipper Architecture")

**Date**: 2026-02-08
**Status**: Approved, ready for implementation
**Author**: Claude Opus 4.6 + lvona

---

## 1. Problem Statement

The Code Tumbler system sends large prompts to LLM APIs:
- Architectural plans: ~6K characters
- Full codebases: up to 200K character budget
- Verification feedback reports: ~4K characters
- System prompts: 1-2K characters each

With a 38K-token context model (`openwebui_chat` via a local vLLM-served model), this leads to:
- Context window exhaustion at later iterations (engineer receives plan + previous code + feedback)
- Token waste on boilerplate and redundant context
- API cost scaling with prompt size (relevant for paid providers like OpenAI/Anthropic)

## 2. Design Principle: "The Zipper"

> Store full text internally. Compress just-in-time before API transmission. Never compress active instructions or code output. Decompress only when needed for human-readable logs.

This prevents **semantic drift** — the "Chinese Whispers" effect where `function(a, b)` gets compressed to `fn(a,b)` then re-compressed to `f(a,b)` over 10 iterations, eventually producing broken code.

### What Gets Compressed (Context Dumps)
- **Architect**: project requirements, previous plan on revision, feedback
- **Engineer**: architectural plan, previous code files, verifier feedback
- **Verifier**: architectural plan, generated code content, build/test output

### What Stays Untouched (Sacred)
- All system prompts (agent behavior definitions)
- Active task instructions (the "Your Task" section in each agent)
- Code in LLM responses (never request compressed code output)
- Fenced code blocks within context (when `preserve_code_blocks: true`)

## 3. Technology Selection

### LLMLingua-2

**Model**: `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank`

| Property | Value |
|----------|-------|
| Parameters | 200M (BERT-based token classifier) |
| Mechanism | Token importance scoring → keep/drop decisions |
| Memory (CPU) | ~500MB RAM |
| Memory (GPU) | ~2.1GB peak |
| Latency | 100-200ms per compression on CPU |
| Compression ratio | 2x-5x with <2% quality loss |
| License | Apache 2.0 |
| Monthly downloads | 174K+ |

**Why LLMLingua-2 over alternatives:**

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **LLMLingua-2 (BERT)** | Model-quality compression, no hallucinations (only drops tokens), preserves structure | Adds ~1.2GB to Docker image, 100-200ms latency | **Selected** |
| Heuristic (whitespace/comments) | Zero overhead, instant | Too crude for plans and feedback text | Insufficient |
| tiktoken truncation | Instant, exact token count | Loses tail context entirely | Insufficient |
| Generative rewriting (7B+) | Best quality | 16GB+ VRAM, 5-30s latency, hallucination risk | Too heavy |
| Selective Context (perplexity) | Good quality | Requires LLM for perplexity scoring | Circular dependency |

### Key Technical Properties

**No hallucinations**: LLMLingua-2 uses token classification (keep/drop), not generative rewriting. It can only remove tokens, never invent new ones.

**Structural preservation**: Preserves newlines, punctuation, and code delimiters by default. The `force_tokens` parameter ensures critical formatting survives compression.

**Near-idempotent**: Compressing already-compressed text returns approximately the same text (tokens already deemed important won't be dropped on re-classification). Not perfectly idempotent, but stable enough for single-pass use.

## 4. Architecture

```
                    STORAGE (full text)          TRANSMISSION (compressed)
                    -----------------           --------------------------
state.json          <- full plans, feedback
03_staging/         <- full code files
conversation.json   <- full logs

BaseAgent._build_messages()
    |
    v
    [system_prompt, context_with_markers, instruction]
    |
    v
    _apply_compression()
    |   - Find <compress>...</compress> blocks
    |   - Extract fenced code blocks (preserve)
    |   - Run CompressionEngine on remaining text
    |   - Reinsert code blocks
    |   - Log compression metrics
    |
    v
    [system_prompt, COMPRESSED_context, instruction]    -> provider.stream_chat()
                                                                |
                                                                v
                                                        LLM response (full text)
```

### Inline Marker Format

Agent message builders tag compressible sections with XML markers:

```python
user_message = f"""<compress>
# Architectural Plan

{plan}

# Previous Implementation

{previous_code_blocks}
</compress>

# Your Task

Generate ALL files specified in the plan as a JSON array...
"""
```

BaseAgent strips `<compress>` markers before sending to the provider. If compression is disabled, markers are simply removed and full text is sent.

## 5. Implementation Plan

### 5.1 Compression Engine Module

**New file**: `backend/src/compression/__init__.py`
**New file**: `backend/src/compression/engine.py`

```python
class CompressedResult:
    text: str
    original_tokens: int
    compressed_tokens: int
    ratio: float
    time_ms: float

class CompressionEngine:
    """Singleton prompt compression engine using LLMLingua-2."""

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'CompressionEngine':
        """Lazy-load model on first use."""

    def compress_context(
        self,
        text: str,
        rate: float = 0.5,
        target_token: int = -1,
        preserve_code_blocks: bool = True
    ) -> CompressedResult:
        """Compress context text, optionally preserving fenced code blocks."""

    def compress_messages(
        self,
        messages: List[Dict[str, str]],
        config: Dict
    ) -> tuple[List[Dict[str, str]], Dict]:
        """Compress <compress> blocks in messages. Returns (compressed_messages, metrics)."""
```

**Graceful fallback**: If `llmlingua` import fails (e.g., torch not installed), the engine returns text unchanged with a warning log. This allows the system to function without compression dependencies.

### 5.2 Docker & Dependencies

**`backend/requirements.txt`** — add:
```
llmlingua>=0.2.2
```

**`backend/Dockerfile`** — add before `pip install -r requirements.txt`:
```dockerfile
# Install CPU-only PyTorch (no CUDA, saves ~2GB)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
```

**`docker-compose.yml`** — add model cache volume:
```yaml
backend:
  volumes:
    - model_cache:/app/models
  environment:
    HF_HOME: /app/models

volumes:
  pgdata:
  model_cache:
```

### 5.3 Configuration

**`backend/config.yaml`** — add under `tumbler:`:
```yaml
prompt_compression:
  enabled: true
  rate: 0.5                    # 50% token retention (2x compression)
  preserve_code_blocks: true   # never compress fenced code
```

**`backend/src/orchestrator/state_manager.py`** — add to `_default_state()`:
```python
'compression': {
    'enabled': True,
    'rate': 0.5,
    'preserve_code_blocks': True,
}
```

Per-project overrides via `state.json` take precedence over global config.

### 5.4 Agent Message Tagging

Each agent wraps compressible context with `<compress>` markers:

| Agent | Compressible Sections | Sacred Sections |
|-------|----------------------|-----------------|
| **Architect** | Requirements, previous plan, feedback | System prompt, task instructions |
| **Engineer** | Plan, previous code, feedback | System prompt, "Your Task" block |
| **Verifier** | Plan, generated code, build/test output | System prompt, "Your Task" block |

### 5.5 BaseAgent Integration

In `execute()`, after `_build_messages()` and before `provider.stream_chat()`:

```python
# Apply compression if enabled
compression_config = kwargs.pop('compression_config', None)
if compression_config and compression_config.get('enabled'):
    messages, compression_metrics = self._apply_compression(messages, compression_config)
    # Log metrics for observability
```

### 5.6 API Endpoint

```
PUT /api/projects/{name}/compression
Body: {"enabled": bool, "rate": float, "preserve_code_blocks": bool}
Response: 200 OK with updated config

GET /api/projects/{name}/status
Response: includes "compression" key with current config
```

### 5.7 Metrics

Compression stats logged alongside existing usage data:
```json
{
  "agent": "engineer",
  "iteration": 2,
  "original_tokens": 8500,
  "compressed_tokens": 3400,
  "compression_ratio": 0.60,
  "compression_time_ms": 145
}
```

## 6. Implementation Order

1. **Engine module** — standalone, testable in isolation
2. **Docker deps** — add torch CPU + llmlingua to requirements/Dockerfile
3. **Config & state** — add compression settings to state_manager and config.yaml
4. **Message tagging** — add `<compress>` markers in all three agent builders
5. **BaseAgent integration** — wire compression into `execute()` flow
6. **API endpoint** — toggle compression per project
7. **Metrics** — log compression stats alongside existing usage data

## 7. Verification Strategy

### Unit Tests
- Compress known plan text → verify output shorter, structurally intact
- Compress text with fenced code → verify code blocks preserved verbatim
- Compress already-compressed text → verify output stable (near-idempotent)
- Compress empty/very short text → verify graceful no-op

### Integration Tests
- Run full project cycle with compression ON vs OFF
- Compare token counts (expect 40-60% reduction on context sections)
- Compare final quality scores (should be comparable)
- Verify debug logs contain both original and compressed messages

### Edge Cases
- Empty context (should no-op)
- Very short context (<50 tokens, should skip compression)
- Context with only code blocks (should return unchanged)
- Model download failure (should fallback gracefully)

## 8. Risk Mitigations

| Risk | Mitigation |
|------|------------|
| Compression breaks code syntax | `preserve_code_blocks: true` extracts fenced code before compression |
| Semantic drift over iterations | Only compress context, never active instructions or code output |
| Model download fails in Docker | Graceful fallback: skip compression and log warning |
| Latency overhead (100-200ms) | Acceptable vs. API call time (5-30s); can be disabled per project |
| Docker image bloat (~1.2GB) | CPU-only torch; model cached in volume, not baked into image |
| Context window still exceeded | Compression is additive to existing char-budget truncation |

## 9. Expected Impact

With 2x compression on context sections:
- **Engineer iteration 2+**: Plan (~6K chars) + previous code (~50K chars) + feedback (~4K chars) = ~60K chars → ~30K chars
- **Verifier**: Plan (~6K chars) + generated code (~50K chars) + results (~2K chars) = ~58K chars → ~29K chars
- **Token savings**: ~40-60% reduction on input tokens per API call
- **Latency**: Net positive — 100-200ms compression overhead, but fewer tokens = faster LLM inference

---

## Research Sources

### Primary References

1. **LLMLingua-2 Paper**: Zhuoshi Pan et al. "LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression" (2024)
   - https://arxiv.org/abs/2403.12968
   - Core methodology: BERT-based token classification for keep/drop decisions

2. **LLMLingua Project (Microsoft Research)**: Official repository and documentation
   - https://github.com/microsoft/LLMLingua
   - https://llmlingua.com/llmlingua2.html

3. **Microsoft Research Blog**: "LLMLingua: Innovating LLM Efficiency with Prompt Compression"
   - https://www.microsoft.com/en-us/research/blog/llmlingua-innovating-llm-efficiency-with-prompt-compression/

### Model & Package

4. **Hugging Face Model Card**: `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank`
   - https://huggingface.co/microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank
   - 200M params, Apache 2.0 license, 174K+ monthly downloads

5. **PyPI Package**: `llmlingua` v0.2.2
   - https://pypi.org/project/llmlingua/
   - Python >=3.8, released April 2024

6. **Base Model**: `google-bert/bert-base-multilingual-cased`
   - https://huggingface.co/google-bert/bert-base-multilingual-cased

### Performance & Benchmarks

7. **LLMLingua-2 Benchmark Results** (from paper):
   - 2x-5x compression with <2% quality loss on MeetingBank, LongBench
   - GPU memory: 2.1GB peak (vs 16.6GB for LLMLingua-v1 with Llama-2-7B)
   - 3x-6x faster than original LLMLingua
   - End-to-end latency: 1.6x-2.9x speedup with 2x-5x compression

8. **BERT CPU Scaling**: Hugging Face blog on CPU inference optimization
   - https://huggingface.co/blog/bert-cpu-scaling-part-1
   - BERT-base: 40-76ms latency on CPU (batch=1)

### Docker & Deployment

9. **PyTorch CPU-Only Wheels**: Official index for lightweight Docker images
   - https://download.pytorch.org/whl/cpu
   - Eliminates ~2GB of CUDA libraries

10. **Optimizing PyTorch Docker Images**: Best practices for reducing image size
    - https://mveg.es/posts/optimizing-pytorch-docker-images-cut-size-by-60percent/

### Prompt Compression Surveys & Context

11. **Prompt Compression Survey**: "A Survey on Prompt Compression for Large Language Models" (2024)
    - https://arxiv.org/abs/2410.12388
    - Taxonomy of approaches: token pruning, soft prompts, generative compression

12. **DataCamp Tutorial**: "Prompt Compression: A Complete Guide"
    - https://www.datacamp.com/tutorial/prompt-compression
    - Practical overview of compression strategies and trade-offs

### Related Architecture Patterns

13. **Selective Context** (Li et al. 2023): Information-theoretic prompt compression
    - https://arxiv.org/abs/2304.12102
    - Alternative approach using self-information for token filtering

14. **LongLLMLingua**: Extension for long-context scenarios (RAG)
    - https://arxiv.org/abs/2310.06839
    - Question-aware compression for retrieval-augmented generation
