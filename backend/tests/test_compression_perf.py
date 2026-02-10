"""Benchmark test for LLMLingua-2 compression performance."""

import os
import time
import pytest

try:
    from src.compression.engine import CompressionEngine
except ImportError:
    from compression.engine import CompressionEngine

# Sample text (~500 words)
SAMPLE_TEXT = """
The Zipper Architecture is a design pattern for optimizing prompt management in autonomous coding agents. 
It addresses the challenge of context window exhaustion and semantic drift by compressing long-term 
memory artifacts just-in-time before API transmission, while keeping the internal state in full fidelity.

Core Principles:
1. Storage vs. Transmission: Store everything in full resolution. Compress only what goes on the wire.
2. Sacred Sections: Never compress active task instructions or system prompts. These define the agent's 
   immediate behavior and must be precise.
3. Code Preservation: Fenced code blocks are often the most critical part of the context. Compression 
   models (like BERT-based token classifiers) might drop punctuation or structural tokens that are 
   essential for code validity. The Zipper extracts these blocks, compresses the surrounding natural 
   language, and then re-zips the code back in.

Implementation Details:
The system uses Microsoft's LLMLingua-2, a BERT-based token classification model trained on MeetingBank. 
Unlike generative compression (using an LLM to summarize), LLMLingua-2 only performs token removal. 
This eliminates hallucinations—it cannot invent new facts, only remove existing ones. It achieves 
2x-5x compression ratios with minimal information loss.

In the Code Tumbler architecture, this engine sits between the agent's context builder and the LLM 
provider adapter. Agents tag compressible sections (like previous plans, file dumps, or verbose logs) 
with XML-like markers. The engine processes these blocks, applies the compression model, and returns 
the optimized prompt.

Performance considerations include latency (CPU inference takes ~100-200ms per call) and memory 
overhead (~1.2GB for the model). However, the token savings (often 50%+) significantly reduce API 
costs and latency on the generation side, resulting in a net positive impact for most workflows.
""" * 5  # ~2500 words

def test_lingua2_performance():
    """Benchmark LLMLingua-2 compression time."""
    # Force backend to local
    os.environ['COMPRESSION_BACKEND'] = 'llmlingua2'
    
    # Reset singleton for test
    CompressionEngine._instance = None
    engine = CompressionEngine.get_instance()
    
    if not engine._ensure_model():
        print("\nSKIPPING: LLMLingua-2 model/dependencies not available.")
        print("To run this benchmark, install: pip install llmlingua torch")
        return

    print(f"\nCompressing sample text ({len(SAMPLE_TEXT)} chars)...")
    
    # Warmup
    engine.compress_context(SAMPLE_TEXT[:500], rate=0.5)
    
    # Benchmark
    start_time = time.time()
    result = engine.compress_context(SAMPLE_TEXT, rate=0.5)
    duration = time.time() - start_time
    
    print(f"\n--- Benchmark Results ---")
    print(f"Input length: {len(SAMPLE_TEXT)} chars")
    print(f"Original tokens: {result.original_tokens}")
    print(f"Compressed tokens: {result.compressed_tokens}")
    print(f"Ratio: {result.ratio:.2f}")
    print(f"Time taken: {duration:.4f}s ({duration*1000:.1f}ms)")
    print(f"Speed: {len(SAMPLE_TEXT)/duration:.0f} chars/sec")
    
    assert result.ratio <= 0.6, "Compression ratio should be effective"
    
if __name__ == "__main__":
    test_lingua2_performance()
