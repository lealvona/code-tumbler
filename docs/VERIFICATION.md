# Code Verification & Quality Assessment

How Code Tumbler validates generated code and decides when it's ready.

---

## Overview

Every iteration of the tumbling cycle ends with a verification phase that answers two questions: *does this code work?* and *is it good enough?* The system combines automated sandbox testing with LLM-driven code review, feeding concrete results back to the Engineer for targeted fixes.

```
Engineer writes code
       |
       v
  +-----------+     install, build,     +-----------+
  |  Sandbox  | --> test, lint (Docker) |  Metrics  |
  +-----------+                         +-----------+
       |                                      |
       v                                      v
  +-----------+     code + metrics +     +-----------+
  |  Verifier | --> plan context   -->  |  Report   |
  |   (LLM)   |                         |  + Score  |
  +-----------+                         +-----------+
       |
       v
  Score >= threshold?  --yes-->  Finalize
       |no
       v
  Feed report back to Engineer, loop
```

---

## 1. Sandbox Verification

Generated code never runs on the host or inside the backend container. Every verification step runs in an ephemeral Docker container that is destroyed after execution.

### 1.1 Runtime Detection

[`sandbox.py:107-138`](../backend/src/verification/sandbox.py#L107-L138) detects the project language by checking for marker files, then falling back to plan-text keyword matching.

| Marker file | Runtime | Image |
|---|---|---|
| `package.json` | Node.js | `node:20-slim` |
| `requirements.txt` | Python | `python:3.12-slim` |
| `pyproject.toml` | Python | `python:3.12-slim` |
| `go.mod` | Go | `golang:1.22-alpine` |
| `Cargo.toml` | Rust | `rust:1.78-slim` |
| `pom.xml` | Java | `eclipse-temurin:21-jdk-alpine` |

Each runtime carries default install, build, test, and lint commands ([`sandbox.py:54-104`](../backend/src/verification/sandbox.py#L54-L104)). These defaults are overridden when the Architect's plan specifies explicit commands (see [Strategy Extraction](#14-strategy-extraction)).

### 1.2 Execution Phases

[`sandbox.py:509-656`](../backend/src/verification/sandbox.py#L509-L656) runs four phases, each in its own container:

| Phase | Network | Timeout | Purpose |
|---|---|---|---|
| **Install** | bridge (outbound only) | 300s | `npm install`, `pip install`, etc. Installed deps are extracted back to host for subsequent phases. |
| **Build** | none | 300s | `npm run build`, `cargo build`, etc. Only runs if install succeeded. |
| **Test** | none | 120s | `pytest`, `npm test`, `go test`, etc. Runs in parallel with lint. |
| **Lint** | none | 60s | `eslint`, `flake8`, `go vet`, etc. Runs in parallel with test. |

Test and lint run concurrently via `ThreadPoolExecutor` ([`sandbox.py:633-638`](../backend/src/verification/sandbox.py#L633-L638)) since they are independent.

### 1.3 Container Security

Each container is created with ([`sandbox.py:379-400`](../backend/src/verification/sandbox.py#L379-L400)):

- **All Linux capabilities dropped** (`cap_drop=["ALL"]`)
- **No privilege escalation** (`no-new-privileges:true`)
- **Resource limits**: 1 CPU, 2 GB RAM, 256 PIDs max
- **tmpfs** at `/tmp` and `/root` (workspace uses the ephemeral writable layer)
- **No network** during build, test, and lint phases

Project files enter the container via `put_archive` — no bind mounts, no host paths exposed. Tar creation ([`sandbox.py:216-259`](../backend/src/verification/sandbox.py#L216-L259)) skips symlinks entirely and validates every file's resolved path stays within the workspace root.

### 1.4 Strategy Extraction

The Verifier parses the Architect's plan for explicit command blocks ([`verifier.py:325-359`](../backend/src/agents/verifier.py#L325-L359)):

```markdown
Install Commands:
```bash
pip install -r requirements.txt
```

Test Commands:
```bash
pytest -x --tb=short

```

Plan commands take priority over runtime defaults. Lint commands always use runtime defaults ([`sandbox.py:548-549`](../backend/src/verification/sandbox.py#L548-L549)).

### 1.5 Result Parsing

Test counts are extracted from runner output supporting pytest, Jest/Vitest, Go test, and generic `X/Y passed` formats ([`sandbox.py:658-693`](../backend/src/verification/sandbox.py#L658-L693)).

Lint issues are counted via `file:line:col:` pattern matching or summary lines like "N problems" ([`sandbox.py:695-711`](../backend/src/verification/sandbox.py#L695-L711)).

---

## 2. Quality Scoring

Scoring is two-tiered: automated metrics provide a baseline, and the Verifier LLM produces the final score.

### 2.1 Automated Score

Calculated from sandbox results by [`verifier.py:451-487`](../backend/src/agents/verifier.py#L451-L487):

| Component | Points | Criteria |
|---|---|---|
| Build success | 3 | Installation and build exit 0 |
| Test pass rate | 4 | `(tests_passed / tests_total) * 4` |
| Linting | 2 | 0 issues = 2 pts, <5 issues = 1 pt, 5+ = 0 |
| No critical errors | 1 | No errors in the error list |
| **Total** | **10** | |

### 2.2 LLM Score

The Verifier LLM receives the plan, generated code, and all sandbox output, then writes a quality report with a score. The report follows the scoring rubric in [`verifier_system.txt`](../backend/prompts/verifier_system.txt):

| Score | Meaning |
|---|---|
| 9-10 | Production-ready. All tests pass, clean lint, complete implementation. |
| 7-8 | Solid. Minor issues, 1 refinement iteration likely sufficient. |
| 4-6 | Needs work. Multiple failures, 2-3 iterations needed. |
| 1-3 | Poor. Core functionality broken, major rework required. |
| 0 | Build doesn't compile or run. |

The recommended breakdown ([`verifier_system.txt:113-121`](../backend/prompts/verifier_system.txt#L113-L121)):
- Correctness (0-3)
- Completeness (0-2)
- Testing (0-2)
- Code Quality (0-2)
- Best Practices (0-1)

### 2.3 Score Resolution

[`verifier.py:305-314`](../backend/src/agents/verifier.py#L305-L314) resolves the final score:

1. If the LLM report contains a score (`"Overall Score: X/10"`) — use it
2. Else if automated metrics produced a score — use that
3. Else default to **5.0** (signals "needs human review")

### 2.4 Code-Review-Only Mode

When the sandbox can't run (image pull failure, Docker unavailable, unrecognized runtime), the Verifier falls back to pure LLM code review ([`verifier.py:400-401`](../backend/src/agents/verifier.py#L400-L401)). The LLM reviews code against the plan without any automated test results. Scores in this mode tend to be less reliable but still drive the feedback loop.

---

## 3. Feedback Loop

### 3.1 Loop Logic

After each verification, the orchestrator evaluates whether to loop or finalize ([`daemon.py:730-763`](../backend/src/orchestrator/daemon.py#L730-L763)):

```
score >= quality_threshold (default 8.0)  -->  Finalize
iteration >= max_iterations (default 10)  -->  Finalize (forced)
cost >= max_cost_per_project              -->  Stop (mark failed)
otherwise                                 -->  Trigger next Engineer iteration
```

### 3.2 How Feedback Reaches the Engineer

The verification report is saved to `04_feedback/REPORT_iter{N}.md`. On the next iteration, the daemon reads this report and passes it to the Engineer along with the current staging code ([`daemon.py:330-374`](../backend/src/orchestrator/daemon.py#L330-L374)).

Verification output (exact error messages, test failures, lint warnings) is **never compressed** — it stays outside `<compress>` tags so the Engineer sees precise error context. The plan and previous code listings are subject to compression to fit within context windows.

If the report is empty or missing, the daemon provides a generic fallback prompt ([`daemon.py:342-352`](../backend/src/orchestrator/daemon.py#L342-L352)) so the Engineer doesn't regenerate identical code.

### 3.3 Cost Budget

Token usage is logged after every agent call ([`state_manager.py:450-522`](../backend/src/orchestrator/state_manager.py#L450-L522)), tracked per-agent in `.tumbler/usage.json`. If `max_cost_per_project` is set (default 0 = unlimited), the orchestrator checks cost before each loop iteration ([`daemon.py:709-728`](../backend/src/orchestrator/daemon.py#L709-L728)) and stops the project if the budget is exceeded.

---

## 4. Path Normalization

Two mechanisms prevent path mismatches from wasting iteration loops:

**Sandbox workspace**: Project files from `03_staging/` are tar-archived with relative paths and extracted into `/workspace` inside the container. All commands run with `cd /workspace`. The container's writable layer (not tmpfs) holds the workspace so `put_archive` content is visible at runtime ([`sandbox.py:374-378`](../backend/src/verification/sandbox.py#L374-L378)).

**Engineer output normalization**: LLMs sometimes prefix all paths with a project directory name (e.g. `my-app/package.json` instead of `package.json`). [`engineer.py:_normalize_file_paths()`](../backend/src/agents/engineer.py) detects when all paths share a single common first directory and no root-level marker files exist, then strips the prefix so files land at the workspace root where the sandbox expects them.

---

## 5. Further Considerations

### 5.1 Integration Testing

The current pipeline tests individual phases (install, build, test, lint) but doesn't run the built artifact. Adding a **run phase** that executes the project's entry point with sample input and checks for crashes or expected output would catch runtime errors that unit tests miss. The strategy extraction already supports a `run` key ([`verifier.py:340`](../backend/src/agents/verifier.py#L340)) — it just isn't wired into the sandbox executor yet.

### 5.2 Security Scanning

Static analysis tools like `bandit` (Python), `npm audit` (Node.js), or `cargo audit` (Rust) could run as an additional sandbox phase. Generated code is especially prone to issues like hardcoded secrets, insecure defaults, and unvalidated input. A security score component (e.g. 0-1 points) would incentivize the Engineer to address findings.

### 5.3 Deterministic Scoring

The LLM score can vary between runs for the same code. Relying more heavily on the automated metrics (which are deterministic) and using the LLM score only as a tiebreaker or adjustment could reduce score variance. Alternatively, running the LLM scorer multiple times and averaging could improve consistency at the cost of additional tokens.

### 5.4 Partial Re-verification

Currently, every iteration re-runs the full pipeline from install through lint. If only one file changed between iterations, re-running the entire install phase wastes time. A diff-aware verifier could skip install when `package.json`/`requirements.txt` hasn't changed and only re-run test and lint phases.

### 5.5 Coverage Metrics

Test pass/fail counts don't measure *what* is tested. Adding coverage tools (`pytest-cov`, `c8`/`istanbul` for Node.js) and feeding the coverage percentage into the scoring formula would push the Engineer toward meaningful test suites rather than trivially passing ones.

### 5.6 Multi-Language Projects

Runtime detection picks the first matching marker file. A project with both `package.json` and `requirements.txt` (e.g. a Python backend + React frontend) would only verify the Node.js side. Supporting composite runtimes — detecting multiple markers and running verification phases for each — would handle full-stack projects correctly.

### 5.7 Caching Base Images and Dependencies

Each verification phase pulls a fresh container and re-installs dependencies. Pre-building runtime images with common dependencies (e.g. `node:20-slim` with a pre-warmed npm cache) or caching `node_modules`/`.venv` across iterations via named volumes could significantly reduce install phase latency.

### 5.8 Structured Error Feedback

The current feedback to the Engineer is the full markdown report. Parsing the report into structured data — a list of `{file, line, error_type, message}` objects — would let the Engineer target fixes more precisely and reduce the risk of LLM hallucination when interpreting free-text feedback.
