# CLAUDE.md — Code Tumbler

## Project Overview

Code Tumbler is an autonomous code generation platform using a three-agent feedback loop
(Architect, Engineer, Verifier) to iteratively refine requirements into production-ready
software. It supports multiple LLM providers and multiple target languages.

## Development Environment

### Shell
Always use **Git Bash** for all terminal commands on Windows. Do not use PowerShell or cmd.exe.

### Python
Use the local virtual environment for all Python operations:
```bash
backend/venv/Scripts/python
backend/venv/Scripts/pip
```
Do **not** use system Python or create new virtual environments.

### Docker
The project runs as a multi-container Docker Compose stack:
```bash
docker compose up --build -d    # rebuild and start
docker logs code-tumbler-backend      # check backend logs
```

### Git
Always make a new branch for any changes:
```bash
git checkout -b my-feature-branch
```
Always figure out if there will be branch divergences before making commits
Choose the path of least complexity when managing git history (rebasing vs merging).

## Project Layout

```
code-tumbler/
  backend/               # Python 3.11 FastAPI backend
    src/
      agents/            # Architect, Engineer, Verifier agents
      api/routes/        # FastAPI REST endpoints
      orchestrator/      # Daemon, state manager, feedback loop
      providers/         # LLM provider adapters (OpenAI, Anthropic, Gemini, Ollama, vLLM)
      verification/      # Sandboxed Docker verification engine
      utils/             # Config loader, compression, provider factory
    prompts/             # System prompt templates
    config.yaml          # Provider and system configuration
    requirements.txt     # Python dependencies
    venv/                # Local virtual environment (use this)
  frontend/              # Next.js 14 / React 18 / TypeScript UI
  database/              # Database schemas
  docker-compose.yml     # Container orchestration
  projects/              # Generated project workspace (runtime data)
```

## Security Policies

These policies are **non-negotiable**. Every code change must comply.

### 1. No Force Deletion

Never use `os.chmod()` + retry, `shutil.rmtree()`, or `rm -rf`-style operations.
All file deletion must go through:

- `StateManager._safe_clear_dir()` for clearing project subdirectories
- `StateManager.safe_delete_project()` for deleting entire projects

These methods enforce:
- **Path containment**: every file path is resolved and validated against the project root before deletion
- **Allowlisted directories**: only directories in the explicit allowlist can be cleared
- **Symlink safety**: symlinks are removed (the link, not the target) only if the link itself is within the project
- **Mount point protection**: `os.path.ismount()` check before any directory removal
- **No chmod**: files that can't be deleted are logged and skipped, never force-removed
- **Bottom-up deletion**: files first, then empty directories via `rmdir()`

### 2. Sandbox Isolation

Generated code runs in ephemeral Docker containers, never on the host or in the backend container.

Container constraints:
- All Linux capabilities dropped (`cap_drop=["ALL"]`)
- `no-new-privileges:true`
- 1 CPU, 1 GB RAM, 256 PIDs max
- No network during build/test/lint phases
- Restricted outbound-only network during install phase
- tmpfs for `/tmp` and `/root`
- Automatic cleanup after execution

The Docker socket is accessed through a **socket proxy** (`tecnativa/docker-socket-proxy`)
that restricts the API to container and image operations only. No exec, no volumes,
no privileged operations.

### 3. Tar Archive Safety

`SandboxExecutor._make_tar()` enforces:
- Symlinks are **skipped entirely** (never archived)
- Every file's resolved path is validated to be within the workspace root
- `os.walk(followlinks=False)` prevents symlink directory traversal

### 4. API Key Handling

- API keys are loaded from environment variables, never hardcoded in config.yaml
- The `api_key_env` field in provider config names the env var to read
- YAML is parsed with `yaml.safe_load()` (prevents code injection)
- `.env` files are gitignored

### 5. General Rules

- Never `os.chmod()` files to work around permission errors
- Never mount host paths into sandbox containers (use `put_archive()`)
- Never run generated code in the backend container or on the host
- Never allow sandbox containers to access internal Docker networks
- Always validate paths before any filesystem operation that modifies state
- Log and skip undeletable files rather than escalating permissions

## Agentic Coding Guidelines

When making changes to this codebase:

### Architecture Awareness
- The system uses a **three-agent loop**: Architect (plans) -> Engineer (codes) -> Verifier (tests)
- Each agent has a system prompt in `backend/prompts/`
- Agents communicate through the filesystem: `01_input/` -> `02_plan/` -> `03_staging/` -> `04_feedback/` -> `05_final/`
- State is persisted in `.tumbler/state.json` per project

### Prompt Compression
- The system uses LLMLingua-2 for prompt compression
- Content inside `<compress>...</compress>` tags gets compressed
- Content outside these tags is **never compressed** (verification results, exact error messages)
- Never wrap sandbox output or error messages in compress tags

### Provider Abstraction
- All LLM calls go through the provider abstraction in `backend/src/providers/`
- Per-agent provider overrides are supported (config.yaml `agent_providers` + per-project overrides)
- Cost tracking is automatic per call

### State Management
- `StateManager` manages all project state (iterations, scores, phases)
- Dual-write: JSON files (primary) + PostgreSQL (best-effort)
- Always use `StateManager` methods rather than direct file I/O for project state

### Testing Changes
After modifying backend code:
```bash
# Syntax check
backend/venv/Scripts/python -c "import ast; ast.parse(open('backend/src/FILE.py').read())"

# Rebuild containers
docker compose up --build -d

# Check logs
docker logs code-tumbler-backend --tail 30

# Test an API endpoint
curl -s http://localhost:8000/api/projects
```
