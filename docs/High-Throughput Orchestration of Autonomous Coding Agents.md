# **High-Throughput Orchestration of Autonomous Coding Agents**

## *Architectural Optimization of Qwen3-Coder-30B-A3B on vLLM*

# **1\. Introduction: The Paradigm Shift to Agentic Concurrency**

The evolution of Large Language Models (LLMs) has transitioned from ephemeral, single-turn chat interfaces to persistent, autonomous agents capable of executing complex, multi-step workflows. In the domain of software engineering, this shift is characterized by "Agentic Coding"—a methodology where models do not merely suggest code snippets but actively perceive file structures, reason about architectural dependencies, manipulate file systems via tools, and iterate on solutions through feedback loops.1 The user's requirement to establish an orchestration framework that autonomously iterates over coding tasks using the **Qwen3-Coder-30B-A3B-Instruct** model represents the bleeding edge of this domain. Specifically, the objective to maximize a **7x concurrency** configuration via the **vLLM** inference engine necessitates a rigorous interrogation of the intersection between Mixture-of-Experts (MoE) model architectures, GPU memory hierarchy, and asynchronous orchestration patterns.  
Traditional deployment strategies for dense models (e.g., Llama-3-70B) often struggle to support high concurrency on commodity or workstation-grade hardware due to the monolithic nature of their parameter activation. However, the Qwen3-Coder-30B-A3B, with its sparse MoE architecture, decouples memory capacity from computational latency, theoretically allowing a single inference engine to serve multiple distinct agentic threads simultaneously without linear degradation in token generation speed.1 Realizing this theoretical throughput in practice, particularly for coding agents that require massive context windows and reliable tool execution, requires a holistic optimization strategy. This report provides an exhaustive analysis of the methodologies required to architect such a system, covering the theoretical underpinnings of the MoE architecture, the low-level tuning of the vLLM engine, the reliability engineering of tool-use protocols, and the high-level orchestration logic required to saturate a 7-stream concurrent pipeline.

## **1.1 The Operational Context: Autonomous Iteration**

The user has established an orchestration layer designed to autonomously iterate over a coding task. "Autonomous iteration" implies a feedback loop—Plan, Act (Write Code), Observe (Run Tests), and Refine—that operates without human intervention. In a sequential model, this loop is slow; the latency of the model (Time Per Output Token, TPOT) becomes the bottleneck. By leveraging 7x concurrency, the system moves from a sequential execution model to a parallelized "Map-Reduce" architecture. Here, a single task (e.g., "Refactor the authentication module") is decomposed into independent sub-tasks (Unit Tests, Database Schema, API Endpoint, Documentation) that are executed simultaneously by seven specialized agent instances.2  
This architectural shift transforms the hardware problem. The bottleneck moves from pure compute (FLOPS) to memory bandwidth and Key-Value (KV) cache capacity. The vLLM engine must be tuned not just for raw speed, but for "throughput under pressure," ensuring that the prefill phase of one agent does not stall the decoding phase of the other six. Furthermore, the integration of tool capabilities—whether via an OpenAI-compatible backend like Open WebUI or direct API calls—introduces distinct failure modes regarding parser reliability and connection timeouts that must be mitigated at the protocol level.3

# ---

**2\. Architectural Analysis: Qwen3-Coder-30B-A3B-Instruct**

To optimize the serving infrastructure for 7x concurrency, one must first deeply understand the computational graph and memory characteristics of the model being served. The Qwen3-Coder-30B-A3B-Instruct is not a standard dense transformer; it is a sparse Mixture-of-Experts (MoE) model designed specifically for efficiency in code generation and reasoning tasks.1 This distinction is the primary enabler of the user's high-concurrency goal.

## **2.1 The Sparse Mixture-of-Experts (MoE) Advantage**

The defining characteristic of the Qwen3-Coder-30B-A3B is its sparsity. While the model contains **30.5 billion parameters** in total, only **3.3 billion parameters** are activated for any single token generation.1 This architecture utilizes a router mechanism to dynamically select a subset of "experts" (feed-forward networks) to process each token.

### **2.1.1 Decoupling Memory from Compute**

In a dense model (e.g., a hypothetical Qwen-30B-Dense), every parameter must be loaded into VRAM and involved in the matrix multiplication operations for every token. This binds memory bandwidth and compute capability tightly together. If one were to run 7 concurrent streams on a dense 30B model, the GPU would need to perform math on all 30B weights seven times per step (or batch them), likely saturating the CUDA cores and leading to high Inter-Token Latency (ITL).  
In contrast, the MoE architecture creates a divergence between static memory requirements and dynamic compute costs:

* **VRAM Usage (Static):** The system must hold all 30.5B parameters in VRAM. In BF16 precision (2 bytes per parameter), this requires approximately **61 GB** of VRAM for weights alone. In quantized formats like GPTQ-Int4 or AWQ, this drops to roughly **18-20 GB**.6 This sets a hard floor on the hardware requirements (e.g., requiring 2x RTX 3090/4090 or 1x A100 80GB).  
* **Compute Usage (Dynamic):** The matrix multiplications only occur on the \~3.3B active parameters.1 This implies that the *computational* latency of generating a token is roughly equivalent to that of a 3B-parameter model.

**Implication for 7x Concurrency:** This architecture is the "secret weapon" for the user's concurrency target. The compute load of 7 concurrent streams (7 \* 3.3B active params) is roughly equivalent to a single stream of a 23B dense model. Modern GPUs like the NVIDIA A100 or H100 (and even the RTX 4090\) have ample FP16/BF16 compute capability to handle this. The bottleneck, therefore, is not the speed of calculation, but the ability of the memory system to feed the relevant experts to the compute units and to store the context (KV cache) for 7 simultaneous conversations.8

### **2.1.2 Expert Locality and Batching**

A potential challenge with MoE concurrency is "Expert Thrashing." If the 7 agents are working on radically different tasks (e.g., Agent 1 is writing SQL, Agent 2 is writing Python, Agent 3 is summarizing text), they may activate different subsets of the 128 total experts. This forces the GPU to load a larger variety of weights from High Bandwidth Memory (HBM) into the L2 cache during each forward pass. However, in the user's specific use case—"autonomously iterating over a coding task"—the agents are likely working on semantically related sub-problems (e.g., all working on the same Python repository). This semantic locality suggests that the router will frequently select the same "coding experts" for all 7 streams, improving L2 cache hit rates and further enhancing throughput.8

## **2.2 Context Window Considerations**

Coding agents require massive context windows to ingest repository structures, API documentation, and existing code files. Qwen3-Coder supports a native context of **262,144 (256K) tokens**, extendable to **1M tokens** via YaRN (Yet another RoPE extension).1 While this capability is impressive, it presents a significant danger to high-concurrency deployments.

### **2.2.1 The KV Cache Constraints**

The Key-Value (KV) cache is the memory structure that stores the attention history for every token in the context. Its size grows linearly with context length and batch size. The formula for KV cache memory usage is:

$$\\text{Memory}\_{KV} \= 2 \\times n\_{layers} \\times d\_{head} \\times n\_{heads} \\times n\_{layers} \\times \\text{Precision} \\times \\text{Context} \\times \\text{Batch}$$  
For Qwen3-Coder-30B (48 layers, hidden size properties implied by architecture), a single stream with 128k context can consume tens of gigabytes of VRAM solely for the cache. If the user attempts to run 7 concurrent streams, each with a 32k or 64k context, the KV cache requirements can easily exceed the remaining VRAM after loading the model weights.  
**Constraint Identification:** To maintain 7 concurrent streams without Out-Of-Memory (OOM) errors, the user must rigidly define the max\_model\_len. If the full 256k context is allocated, vLLM's memory manager (PagedAttention) may not have enough logical blocks to reserve for 7 simultaneous requests, leading to request preemption or failure.10 A strategic limit of **32,768** or **65,536** tokens is recommended to guarantee stability for 7 slots on standard hardware.11

# ---

**3\. The vLLM Execution Engine: Methodologies for Concurrency**

The vLLM (Virtual Large Language Model) engine is the industry standard for high-throughput serving, but its default configuration is often tuned for latency (single user) rather than throughput (concurrent agents). To maximize the 7x concurrency, specific methodologies regarding parallelism, scheduling, and memory management must be applied.

## **3.1 Parallelism Strategies: Tensor vs. Expert Parallelism**

vLLM provides distinct strategies for splitting the model across multiple GPUs (if applicable). For the Qwen3 MoE model, the choice between Tensor Parallelism (TP) and Expert Parallelism (EP) is critical.10

### **3.1.1 Tensor Parallelism (TP)**

TP splits the individual weight matrices (both attention and feed-forward layers) across multiple GPUs.

* **Mechanism:** If tensor\_parallel\_size=2, each GPU holds 50% of the weights. Matrix multiplications are performed in parallel, and results are synchronized via All-Reduce operations after each layer.  
* **Suitability:** This is the standard and most stable approach for single-node deployments (e.g., a server with 2, 4, or 8 GPUs). It effectively aggregates the memory bandwidth of all GPUs, which is beneficial for the memory-bound nature of large context generation.13

### **3.1.2 Expert Parallelism (EP)**

EP is a specialized strategy for MoE models where the *experts* are distributed across GPUs, while the attention layers might be replicated or split differently.10

* **Mechanism:** With 128 experts and 4 GPUs, each GPU might host 32 experts. When a token requires Expert \#5, it must be routed to the GPU holding that expert.  
* **Trade-off:** EP reduces the memory footprint of the non-expert layers but introduces complex "All-to-All" communication overhead. For the Qwen3-30B model, which is relatively small compared to massive 671B MoEs (like DeepSeek-V3), EP is often *less* efficient than TP on small clusters because the communication overhead outweighs the compute benefits.  
* **Recommendation:** Unless the user is running on a massive multi-node cluster (which is unlikely for a "current configuration" implies a single workstation or node), **Tensor Parallelism (TP)** should be preferred. The flag \--enable-expert-parallel should generally be set to False (default) to avoid the overhead of routing tokens between GPUs, relying instead on the high-bandwidth NVLink/PCIe of TP.12

## **3.2 Throughput Optimization: Scheduling and Prefill**

The most significant bottleneck in agentic workflows is the "Prefill" phase. When an agent starts a task, it often ingests a massive amount of context (e.g., 20,000 tokens of code). In a naive scheduler, the GPU processes this prefill in one go, locking the compute resources for several seconds. During this time, the other 6 concurrent agents (who might be generating short lines of code) are frozen. This is known as **Head-of-Line Blocking**.

### **3.2.1 Chunked Prefill (vLLM V1 Feature)**

vLLM V1 introduces "Chunked Prefill" (enabled by default in recent versions) to solve this specific problem.10

* **Mechanism:** Instead of processing the full 20k token prompt of Agent A in one step, vLLM splits it into "chunks" (e.g., 2048 tokens).  
* **Interleaving:** The scheduler processes one chunk for Agent A, then performs a decoding step for Agents B, C, D, E, F, and G, then processes the next chunk for Agent A.  
* **Impact:** Agent A's initial time-to-first-token (TTFT) increases slightly, but the Inter-Token Latency (ITL) for the other 6 agents remains low and consistent. This provides the "fluidity" required for 7 concurrent agents to operate without perceptible stalls.  
* **Configuration:** The user should explicitly verify that \--enable-chunked-prefill is active. Tuning the \--max-num-batched-tokens parameter allows control over the chunk size. A value of **8192** or **16384** is generally optimal; setting it too low (e.g., 512\) increases the overhead of kernel launches, while setting it too high causes stuttering.16

### **3.2.2 The max-num-seqs Parameter**

This parameter defines the hard limit on the number of sequences vLLM will schedule in a single batch.16

* **Requirement:** To support 7 active agents, this value must be *at least* 7\.  
* **Optimization:** It is advisable to set this significantly higher (e.g., **32** or **64**). If set exactly to 7, the scheduler has no flexibility. For instance, if an agent uses "Beam Search" or "Parallel Tool Calling" (generating multiple tool calls in parallel branches), a single agent might temporarily consume multiple sequence slots. A buffer ensures that the 7 agents never block each other due to slot exhaustion.

## **3.3 Memory Hierarchy and Quantization**

To fit the heavy context of 7 agents into VRAM, quantization is often necessary. The snippets identify crucial distinctions between formats.

### **3.3.1 The GGUF Trap**

While GGUF is popular for local inference (llama.cpp), its implementation in vLLM is experimental and unoptimized for throughput.18 Snippets report critical bugs, such as the Qwen3 model outputting strings of exclamation marks (\!\!\!\!\!\!) when running GGUF on vLLM.20 Furthermore, GGUF often requires the \--enforce-eager flag, which disables CUDA Graphs (a massive performance optimization), reducing throughput by 20-50%.21

* **Directive:** The user should avoid GGUF for this high-performance orchestration.

### **3.3.2 FP8 and AWQ**

* **FP8 (Float8):** If the user's hardware supports it (NVIDIA Ada Lovelace or Hopper architectures, e.g., RTX 4090, H100), the **FP8** version of the model is ideal. It provides near-native accuracy with 50% memory reduction and utilizes the hardware's Transformer Engine for acceleration.22  
* **AWQ:** For Ampere hardware (RTX 3090, A100), **AWQ** (Activation-aware Weight Quantization) is the preferred format. It is fully supported by vLLM's optimized kernels and compatible with CUDA Graphs.  
* **KV Cache Quantization:** To further maximize the context window for the 7 agents, the user should enable **FP8 KV Cache** (--kv-cache-dtype fp8). This compresses the attention history stored in VRAM, effectively allowing for significantly longer contexts or higher batch sizes without performance penalty on supported hardware.22

# ---

**4\. Tool Use Protocols: Reliability at Scale**

A coding agent is only as good as its ability to execute tools (read files, run compilers). The interface between the orchestration layer and the model is the source of many reliability issues, particularly regarding the parsing of tool calls.

## **4.1 The Parser Conflict: Hermes vs. Qwen3\_XML**

Research indicates a critical fragmentation in the tool-calling ecosystem for Qwen models.

* **The Hermes Parser:** Many "OpenAI-compatible" clients default to the Hermes tool format (\<tool\_code\>). While Qwen2.5 was often compatible with this, **Qwen3-Coder** exhibits a severe bug when using the \--tool-call-parser hermes flag in streaming mode. The model's output—specifically the XML tags defining the tool—leaks into the content stream as raw text rather than being parsed into the structured tool\_calls API field.4 This breaks the agent loop, as the client never receives the signal to execute the tool.  
* **The Qwen3\_XML Parser:** vLLM has introduced a specific parser for Qwen3's native XML format. The user **must** configure vLLM with \--tool-call-parser qwen3\_xml (or qwen3\_coder in some versions).24 This ensures that the engine correctly interprets the model's \<tool\_call\> tokens, even when streaming, and presents them to the orchestration layer as standard OpenAI-compatible JSON objects.

## **4.2 System Prompt Engineering for Concurrency**

To ensure the 7 concurrent agents strictly adhere to the tool protocol, the orchestration layer should inject a robust system prompt. Relying on the model's training data alone is risky at high concurrency where stochasticity can lead to drift.

* **Explicit Schema Definition:** The system prompt should explicitly define the available tools using the XML format the model expects, even if the API handles the JSON schema. This reinforces the model's internal attention patterns.  
* **Parallel Tool Calling:** Qwen3-Coder supports generating multiple tool calls in a single turn (e.g., read\_file(a.py), read\_file(b.py), read\_file(c.py)). The orchestration layer should be designed to handle a *list* of tool calls. This is a massive efficiency gain: instead of 7 agents doing 3 turns each to read 3 files (21 total turns), they can each do 1 turn to read 3 files (7 total turns), drastically reducing the load on the scheduler.26

## **4.3 Streaming and Timeouts (Open WebUI)**

The user mentioned using **Open WebUI** as a potential backend. A common failure mode in agentic coding is the "Proxy Timeout."

* **The Issue:** Coding tools are slow. Running a test suite or searching a large repo might take 120 seconds. Most reverse proxies (Nginx, Cloudflare) and Open WebUI's internal timeouts default to 60 or 100 seconds.3 If the model (or the tool executor) is silent for this duration, the connection is severed.  
* **The Heartbeat Solution:** The orchestration layer must implement "Heartbeat" logic. When a tool is running, the backend should emit a "keep-alive" signal (e.g., an SSE comment or a whitespace character) to the client every 10-15 seconds. This resets the proxy's idle timer.  
* **Configuration:** If using Open WebUI, the user should adjust the AIOHTTP\_CLIENT\_TIMEOUT environment variables to accommodate the long operational horizons of coding agents.27

# ---

**5\. Orchestration Patterns: Leveraging 7x Concurrency**

Having a tuned engine and a reliable protocol is necessary but insufficient. The application logic—the "Orchestrator"—must be architected to utilize the available concurrency slots. A simple sequential loop ("Think, Act, Observe") uses only 1 slot. To use 7, the user must adopt parallel agent patterns.

## **5.1 The Map-Reduce-Produce Pattern**

The most effective pattern for autonomous coding is **Map-Reduce-Produce**.2 This transforms a coding task from a serial process into a parallel one.

| Phase | Description | Agentic Action | Concurrency |
| :---- | :---- | :---- | :---- |
| **1\. Map (Fan-Out)** | Decompose the high-level task into independent sub-tasks. | A "Planner" agent analyzes the request (e.g., "Add 2FA") and generates a list of files to modify and tests to write. | 1 Stream |
| **2\. Execute (Worker)** | Execute the sub-tasks in parallel. | The orchestrator spawns **7 Worker Agents**. Agent 1 writes the SQL migration. Agent 2 updates the Model. Agent 3 updates the Controller. Agent 4 writes the Frontend. Agents 5-7 write Unit Tests. | **7 Streams** (Saturated) |
| **3\. Reduce (Fan-In)** | Integrate and validate. | A "Reviewer" agent looks at all generated code, checks for interface consistency, and merges it into the codebase. | 1 Stream |
| **4\. Produce** | Final verification. | Run the full test suite and generate the final PR description. | 1 Stream |

**Implementation:** This pattern requires the orchestration client to support asynchronous execution. In Python, this is achieved via asyncio.

## **5.2 Client-Side Concurrency Control**

To ensure the orchestration strictly respects the 7x concurrency limit (preventing queue overload on vLLM), the client should implement a **Semaphore**.

Python

import asyncio  
from openai import AsyncOpenAI

\# Configuration  
VLLM\_API\_URL \= "http://localhost:8000/v1"  
API\_KEY \= "EMPTY"  
MAX\_CONCURRENCY \= 7  \# Matches the user's vLLM tuning

client \= AsyncOpenAI(base\_url=VLLM\_API\_URL, api\_key=API\_KEY)  
semaphore \= asyncio.Semaphore(MAX\_CONCURRENCY)

async def run\_agent\_worker(task\_id, task\_prompt):  
    """  
    An independent agent thread that performs a specific coding sub-task.  
    Protected by a semaphore to ensure we never exceed 7 active requests.  
    """  
    async with semaphore:  
        print(f"\[Agent {task\_id}\] Starting Task...")  
        \# This call blocks until a semaphore slot is open.  
        \# It allows vLLM to serve exactly 7 streams, maximizing throughput logic.  
        response \= await client.chat.completions.create(  
            model="Qwen/Qwen3-Coder-30B-A3B-Instruct",  
            messages=\[{"role": "user", "content": task\_prompt}\],  
            \# Use Qwen3 native tool definitions here  
            tool\_choice="auto"   
        )  
        return response

async def orchestrate\_coding\_task():  
    \# 1\. Plan Phase  
    plan \= await generate\_plan()   
      
    \# 2\. Fan-Out Phase (Launch 7+ tasks)  
    tasks \= \[run\_agent\_worker(i, subtask) for i, subtask in enumerate(plan)\]  
      
    \# 3\. Gather Results (Concurrency handled by Semaphore)  
    results \= await asyncio.gather(\*tasks)  
      
    \# 4\. Reduce Phase  
    final\_code \= await integrate\_code(results)

**Why this works:** The asyncio.Semaphore(7) acts as a client-side gatekeeper.28 Even if the planner generates 50 sub-tasks, only 7 requests are sent to vLLM at any millisecond. This keeps the vLLM request queue short, ensuring that the chunked\_prefill scheduler can optimally interleave the active requests without thrashing.

## **5.3 Managing "Thinking" Tokens**

The Qwen3 model supports "Thinking" (Chain of Thought) output. While powerful for reasoning, it consumes significant throughput.

* **Strategy:** Disable "Thinking" for the 7 parallel worker agents if their tasks are straightforward (e.g., "Write a function to validate email"). Enable "Thinking" only for the Planner and Reviewer agents who handle complex architectural logic. This optimizes the "useful code per second" metric of the system.30

# ---

**6\. Benchmarking and Tuning Guide**

To verify the system is making the "most" of the concurrency, the user should perform specific benchmarks.

# **6.1 Throughput vs. Latency Metrics**

* **Metric 1: Token Throughput (TPS):** The total number of tokens generated per second across all 7 streams. This should increase near-linearly with concurrency until memory bandwidth saturation.  
* **Metric 2: Inter-Token Latency (ITL):** The time between tokens for a single stream. Ideally, this should remain constant (or increase only slightly) as concurrency moves from 1 to 7\. If ITL spikes dramatically at 7, the system is compute-bound or memory-bound, and quantization (FP8) or reducing max-model-len is required.17

## **6.2 Hardware Utilization Monitoring**

* **nvtop / nvidia-smi:** Monitor GPU Compute utilization. For an MoE model, seeing 30-50% compute utilization with 100% memory controller utilization is normal. This indicates the system is memory-bound, confirming that further concurrency gains require KV cache optimization (FP8 Cache) rather than more compute.22

# ---

**7\. Strategic Recommendations**

To definitively satisfy the user's request for maximizing 7x concurrency with Qwen3-Coder-30B on vLLM, the following configuration is recommended:  
**1\. vLLM Engine Configuration:**

* **Model:** Qwen/Qwen3-Coder-30B-A3B-Instruct (Prefer FP8 or AWQ variants over GGUF).  
* **Parallelism:** \--tensor-parallel-size (Avoid Expert Parallelism on single nodes).  
* **Scheduling:** \--enable-chunked-prefill (Crucial for preventing agent stalls).  
* **Concurrency:** \--max-num-seqs 32 (Buffer for 7 active agents).  
* **Memory:** \--max-model-len 32768 (Capped to ensure stable KV cache for 7 streams).  
* **Protocol:** \--tool-call-parser qwen3\_xml (Crucial for fixing streaming bugs).

**2\. Orchestration Layer:**

* **Pattern:** Implement **Map-Reduce** to split coding tasks into 7 parallel streams.  
* **Control:** Use asyncio.Semaphore(7) to strictly align client load with server capacity.  
* **Reliability:** Implement **System Prompts** with explicit tool schemas and **Heartbeat** signals to prevent proxy timeouts.

By adhering to these methodologies, the user transforms the 7x concurrency from a theoretical hardware capability into a tangible acceleration of software development velocity. The MoE architecture, when properly served and orchestrated, allows for a "synthetic team" of 7 developers to work in parallel on a single codebase, realizing the true promise of agentic AI.

# **8\. Detailed Configuration Reference**

| Parameter | Value | Rationale |
| :---- | :---- | :---- |
| **Model Loader** | vLLM (Native) | Avoids GGUF/llama.cpp overhead and bugs. |
| **Quantization** | FP8 (if Ada/Hopper) or AWQ | Balances memory footprint with compute efficiency. |
| \--tensor-parallel-size | GPU Count | Maximizes memory bandwidth aggregation. |
| \--enable-chunked-prefill | True | Prevents "Head-of-Line Blocking" by large prompts. |
| \--max-num-batched-tokens | 8192 | Optimal chunk size for throughput vs. latency. |
| \--max-num-seqs | 32 | Provides scheduler headroom above the 7 active agents. |
| \--tool-call-parser | qwen3\_xml | Mitigates streaming parser failures specific to Qwen3. |
| \--gpu-memory-utilization | 0.90 \- 0.95 | Maximizes KV cache availability. |
| \--kv-cache-dtype | fp8 | Doubles effective context length/batch size. |
| **Orchestrator Semaphore** | 7 | Aligns client demand with hardware optimization target. |

# 

# **9\. Works cited**

1. Qwen/Qwen3-Coder-30B-A3B-Instruct \- Hugging Face, accessed February 8, 2026, [https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct)  
2. From MapReduce to MapReduceProduce: Enabling Automated ..., accessed February 8, 2026, [https://blog.forgen.ai/from-mapreduce-to-map-reduce-produce-a-new-paradigm-for-agentic-ai-668375de2329](https://blog.forgen.ai/from-mapreduce-to-map-reduce-produce-a-new-paradigm-for-agentic-ai-668375de2329)  
3. issue: API Timeout after 100 seconds with Long-Running Tools (e.g. ..., accessed February 8, 2026, [https://github.com/open-webui/open-webui/issues/16747](https://github.com/open-webui/open-webui/issues/16747)  
4. \[Bug\]: Streaming mode with \--tool-call-parser hermes returns raw text instead of parsed tool\_calls · Issue \#31871 · vllm-project/vllm \- GitHub, accessed February 8, 2026, [https://github.com/vllm-project/vllm/issues/31871](https://github.com/vllm-project/vllm/issues/31871)  
5. Qwen/Qwen3-30B-A3B-Instruct-2507 \- Hugging Face, accessed February 8, 2026, [https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507)  
6. Understanding Parallelisms in vLLM: Case Study with Qwen3–30B ..., accessed February 8, 2026, [https://medium.com/@justinduy/understanding-parallelisms-in-vllm-case-study-with-qwen3-30b-a3b-thinking-2507-on-a10g-gpus-59821cb20c6e](https://medium.com/@justinduy/understanding-parallelisms-in-vllm-case-study-with-qwen3-30b-a3b-thinking-2507-on-a10g-gpus-59821cb20c6e)  
7. Mungert/Qwen3-16B-A3B-GGUF \- Hugging Face, accessed February 8, 2026, [https://huggingface.co/Mungert/Qwen3-16B-A3B-GGUF](https://huggingface.co/Mungert/Qwen3-16B-A3B-GGUF)  
8. MoE-Gen: High-Throughput MoE Inference on a Single GPU with Module-Based Batching, accessed February 8, 2026, [https://arxiv.org/html/2503.09716v1](https://arxiv.org/html/2503.09716v1)  
9. Qwen1.5-MoE: Matching 7B Model Performance with 1/3 Activated Parameters | Qwen, accessed February 8, 2026, [https://qwenlm.github.io/blog/qwen-moe/](https://qwenlm.github.io/blog/qwen-moe/)  
10. Optimization and Tuning \- vLLM, accessed February 8, 2026, [https://docs.vllm.ai/en/stable/configuration/optimization/](https://docs.vllm.ai/en/stable/configuration/optimization/)  
11. Qwen3-Coder Usage Guide \- vLLM Recipes, accessed February 8, 2026, [https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3-Coder-480B-A35B.html](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3-Coder-480B-A35B.html)  
12. Expert Parallel Deployment \- vLLM, accessed February 8, 2026, [https://docs.vllm.ai/en/latest/serving/expert\_parallel\_deployment/](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/)  
13. Engine Arguments \- vLLM, accessed February 8, 2026, [https://docs.vllm.ai/en/v0.6.4.post1/models/engine\_args.html](https://docs.vllm.ai/en/v0.6.4.post1/models/engine_args.html)  
14. Engine Arguments \- vLLM, accessed February 8, 2026, [https://docs.vllm.ai/en/latest/configuration/engine\_args/](https://docs.vllm.ai/en/latest/configuration/engine_args/)  
15. HKUNLP/ChunkLlama: \[ICML'24\] Data and code for our paper "Training-Free Long-Context Scaling of Large Language Models" \- GitHub, accessed February 8, 2026, [https://github.com/HKUNLP/ChunkLlama](https://github.com/HKUNLP/ChunkLlama)  
16. How to Configure vLLM for LLM Serving \- OneUptime, accessed February 8, 2026, [https://oneuptime.com/blog/post/2026-01-25-vllm-llm-serving/view](https://oneuptime.com/blog/post/2026-01-25-vllm-llm-serving/view)  
17. \[Performance\]: TPOT and ITL increase as \`max-num-seqs\` increases? · Issue \#17598 · vllm-project/vllm \- GitHub, accessed February 8, 2026, [https://github.com/vllm-project/vllm/issues/17598](https://github.com/vllm-project/vllm/issues/17598)  
18. GGUF \- vLLM, accessed February 8, 2026, [https://docs.vllm.ai/en/v0.10.1/features/quantization/gguf.html](https://docs.vllm.ai/en/v0.10.1/features/quantization/gguf.html)  
19. GGUF \- vLLM, accessed February 8, 2026, [https://docs.vllm.ai/en/stable/features/quantization/gguf/](https://docs.vllm.ai/en/stable/features/quantization/gguf/)  
20. \[Usage\]: "When running the QWEN3 MoE GGUF quantized ... \- GitHub, accessed February 8, 2026, [https://github.com/vllm-project/vllm/issues/24025](https://github.com/vllm-project/vllm/issues/24025)  
21. vLLM Throughput Optimization-1: Basic of vLLM Parameters | by Kaige \- Medium, accessed February 8, 2026, [https://medium.com/@kaige.yang0110/vllm-throughput-optimization-1-basic-of-vllm-parameters-c39ace00a519](https://medium.com/@kaige.yang0110/vllm-throughput-optimization-1-basic-of-vllm-parameters-c39ace00a519)  
22. vLLM Optimization Techniques: 5 Practical Methods to Improve Performance, accessed February 8, 2026, [https://docs.jarvislabs.ai/blog/vllm-optimization-techniques](https://docs.jarvislabs.ai/blog/vllm-optimization-techniques)  
23. Enhancing vLLM Inference on AMD GPUs \- ROCm™ Blogs, accessed February 8, 2026, [https://rocm.blogs.amd.com/artificial-intelligence/vllm-optimize/README.html](https://rocm.blogs.amd.com/artificial-intelligence/vllm-optimize/README.html)  
24. Tool Calling Parsers Fail to Populate tool\_calls Array for Qwen2.5-Coder Models \#29192, accessed February 8, 2026, [https://github.com/vllm-project/vllm/issues/29192](https://github.com/vllm-project/vllm/issues/29192)  
25. \[Bug\]: Qwen3-Coder encountered a large number of errors when using the calling capabilities of vllm-0.11.0. · Issue \#26561 \- GitHub, accessed February 8, 2026, [https://github.com/vllm-project/vllm/issues/26561](https://github.com/vllm-project/vllm/issues/26561)  
26. Agent framework and applications built upon Qwen\>=3.0, featuring Function Calling, MCP, Code Interpreter, RAG, Chrome extension, etc. \- GitHub, accessed February 8, 2026, [https://github.com/QwenLM/Qwen-Agent](https://github.com/QwenLM/Qwen-Agent)  
27. Environment Variable Configuration \- Open WebUI, accessed February 8, 2026, [https://docs.openwebui.com/getting-started/env-configuration/](https://docs.openwebui.com/getting-started/env-configuration/)  
28. Mastering Asyncio Semaphores in Python: A Complete Guide to Concurrency Control, accessed February 8, 2026, [https://medium.com/@mr.sourav.raj/mastering-asyncio-semaphores-in-python-a-complete-guide-to-concurrency-control-6b4dd940e10e](https://medium.com/@mr.sourav.raj/mastering-asyncio-semaphores-in-python-a-complete-guide-to-concurrency-control-6b4dd940e10e)  
29. Controlling the concurrency of HTTP requests using Python's asyncio.Semaphore, accessed February 8, 2026, [https://stackoverflow.com/questions/67152371/controlling-the-concurrency-of-http-requests-using-pythons-asyncio-semaphore](https://stackoverflow.com/questions/67152371/controlling-the-concurrency-of-http-requests-using-pythons-asyncio-semaphore)  
30. vLLM \- Qwen docs, accessed February 8, 2026, [https://qwen.readthedocs.io/en/latest/deployment/vllm.html](https://qwen.readthedocs.io/en/latest/deployment/vllm.html)  
31. Deploy MOE and Call It. We use vLLM to serve… | by Kaige | Medium, accessed February 8, 2026, [https://medium.com/@kaige.yang0110/deploy-moe-and-call-it-b9acbfbeb0fe](https://medium.com/@kaige.yang0110/deploy-moe-and-call-it-b9acbfbeb0fe)