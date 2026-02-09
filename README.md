# Code Tumbler

**Turn a sentence into a working codebase.** Code Tumbler is an autonomous software factory that takes raw requirements and iteratively refines them into production-ready, tested code — powered by your own local LLMs. No cloud API keys required, no token bills, no data leaving your machine. Point it at an Ollama or vLLM instance and watch three AI agents collaborate to architect, write, and verify complete projects in any programming language, automatically re-tumbling until the code passes its own test suite.

Cloud providers (OpenAI, Anthropic, Google Gemini) are fully supported when you want maximum quality or need to mix-and-match — assign different models to different agents with a single line of config.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Code Tumbler UI                           │
│                  Next.js 14 · React 18 · SSE                    │
│     ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│     │Dashboard │  │Projects  │  │ Models   │  │Settings  │     │
│     └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST + SSE
┌────────────────────────────▼────────────────────────────────────┐
│                      FastAPI Backend                             │
│  ┌───────────┐  ┌──────────────────┐  ┌─────────────────────┐   │
│  │ Routes    │  │  Orchestrator    │  │  Event Bus (SSE)    │   │
│  │ REST API  │  │  Daemon Loop     │  │  Real-time updates  │   │
│  └───────────┘  └──────┬───────────┘  └─────────────────────┘   │
│                        │                                         │
│         ┌──────────────┼──────────────┐                         │
│         ▼              ▼              ▼                          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐                  │
│  │ Architect  │ │ Engineer   │ │ Verifier   │   Three-Agent    │
│  │ Plans the  │ │ Writes the │ │ Tests the  │   Feedback Loop  │
│  │ solution   │ │ code       │ │ output     │                  │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘                  │
│        │              │              │                          │
│        ▼              ▼              ▼                          │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │          LLM Provider Abstraction Layer                 │    │
│  │  Ollama · vLLM · OpenAI · Anthropic · Gemini            │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ PostgreSQL   │  │ State Mgr    │  │ Sandbox Executor      │  │
│  │ Cost/history │  │ JSON + DB    │  │ Ephemeral Docker      │  │
│  │              │  │ dual-write   │  │ containers for        │  │
│  │              │  │              │  │ build / test / lint    │  │
│  └──────────────┘  └──────────────┘  └───────────┬───────────┘  │
└──────────────────────────────────────────────────┼──────────────┘
                                                   │
                              ┌─────────────────────▼──────────┐
                              │  Docker Socket Proxy           │
                              │  Restricted API access         │
                              │  (no exec, no volumes,         │
                              │   no privileged ops)           │
                              └────────────────────────────────┘
```

### The Tumbling Cycle

```
Requirements ──▶ Architect ──▶ Engineer ──▶ Verifier ──▶ Score ≥ threshold? ──▶ Done
                    ▲                          │
                    └──── Feedback ◀────────────┘   (repeat up to N iterations)
```

Each iteration:
1. **Architect** reads the requirements (and any prior feedback) and outputs a `PLAN.md` — including recommended sandbox resource levels for the project
2. **Engineer** reads the plan and generates a complete multi-file codebase
3. **Verifier** builds, tests, and lints the code inside a sandboxed Docker container, then scores it 0-10
4. If the score is below the quality threshold, the feedback loops back and the agents try again

---

## Quickstart

> **Prerequisites:** Docker, Docker Compose, and at least one LLM provider (Ollama recommended).

```bash
# 1. Clone
git clone https://github.com/lealvona/code-tumbler.git
cd code-tumbler

# 2. Configure
cp .env.example .env
# Edit .env — at minimum set OLLAMA_BASE_URL if Ollama isn't on localhost

# 3. Launch
docker compose up --build -d

# 4. Open the UI
#    http://localhost:3000
```

That's it. The stack starts PostgreSQL, the FastAPI backend, the Next.js frontend, and a sandboxed Docker socket proxy. A demo project ("hello-tumbler") is seeded automatically on first startup.

### Local development (without Docker)

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp ../.env.example .env
python server.py

# Frontend (separate terminal)
cd frontend
npm ci && npm run dev
```

---

## Configuration

All configuration lives in `backend/config.yaml`. Key sections:

```yaml
# Which provider to use by default
active_provider: ollama_local

# Per-agent overrides — use a fast model for engineering, a smart one for planning
agent_providers:
  architect: anthropic_sonnet
  engineer: ollama_local
  verifier: ollama_local

# Tumbling parameters
tumbler:
  max_iterations: 10
  quality_threshold: 8.0
```

Environment variables (`.env`) supply API keys and override base URLs. See [.env.example](.env.example) for the full list.

---

## Project Structure

```
code-tumbler/
├── backend/                 # Python 3.11 FastAPI
│   ├── src/
│   │   ├── agents/          # Architect, Engineer, Verifier
│   │   ├── api/             # REST endpoints + event bus
│   │   ├── orchestrator/    # Daemon loop + state management
│   │   ├── providers/       # LLM adapters (Ollama, vLLM, OpenAI, Anthropic, Gemini)
│   │   ├── verification/    # Sandboxed Docker executor
│   │   ├── compression/     # LLMLingua-2 prompt compression
│   │   ├── db/              # SQLAlchemy models + repository
│   │   └── utils/           # Config, logging, provider factory
│   ├── prompts/             # Agent system prompt templates
│   ├── tests/               # pytest suite
│   └── config.yaml          # Provider and system configuration
├── frontend/                # Next.js 14 / React 18 / Tailwind CSS
│   └── src/
│       ├── app/             # Pages: dashboard, projects, models, settings
│       ├── components/      # UI components (shadcn/ui)
│       ├── hooks/           # SSE, toast hooks
│       └── lib/             # API client, types, state
├── database/                # Alembic migrations
├── scripts/                 # Deployment, logging, health check
├── docs/                    # Architecture and reference docs
├── docker-compose.yml       # Full-stack orchestration
└── .env.example             # Environment variable template
```

---

## Features

- **Local-first LLMs** — Ollama and vLLM for zero-cost, fully private code generation
- **Cloud providers** — OpenAI, Anthropic, and Google Gemini when you need them
- **Per-agent model assignment** — different models for Architect, Engineer, and Verifier
- **Polyglot** — generates Python, Node.js, Go, Rust, or any language the LLM supports
- **Sandboxed verification** — generated code runs in ephemeral Docker containers, never on the host
- **Architect-driven resource tuning** — the Architect can recommend sandbox resources (timeouts, memory, CPU) based on project complexity, and they can be overridden per-project
- **Prompt compression** — LLMLingua-2 reduces token usage while preserving meaning
- **Real-time UI** — Server-Sent Events stream agent output, sandbox phases, and cost data live
- **Cost tracking** — automatic per-call token and cost accounting across all providers
- **Async concurrency** — parallel LLM calls where the provider supports it

---

## Ops Scripts

```bash
./scripts/deploy.sh          # Build and start the full stack
./scripts/deploy.sh --pull   # Pull latest base images first
./scripts/logs.sh            # Tail all service logs
./scripts/logs.sh backend    # Tail backend logs only
./scripts/healthcheck.sh     # Quick status check of all services
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [CLAUDE.md](CLAUDE.md) | Development instructions and security policies |
| [docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md) | Orchestrator daemon reference |
| [docs/PLATFORM_GUIDE.md](docs/PLATFORM_GUIDE.md) | Cross-platform setup notes |
| [docs/GEMINI_SETUP.md](docs/GEMINI_SETUP.md) | Google Gemini provider setup |
| [docs/TESTING.md](docs/TESTING.md) | Test suite and provider testing guide |

---

## License

MIT — see [LICENSE](LICENSE) for details.
