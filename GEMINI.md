# GEMINI.md — Code Tumbler

## Project Overview

Code Tumbler is an autonomous code generation platform using a three-agent feedback loop
(Architect, Engineer, Verifier) to iteratively refine requirements into production-ready
software. It supports multiple LLM providers and multiple target languages.

## Development Environment

### Shell
Use the system's default shell for all terminal commands. For Windows, this typically means PowerShell.

### Python
It is recommended to use a virtual environment for Python development. Create and activate a virtual environment (e.g., using `python -m venv venv`) and install dependencies from `backend/requirements.txt`.

```bash
# Example: Create and activate a venv (if not already present)
python -m venv backend/venv
backend/venv/Scripts/Activate.ps1 # On Windows PowerShell
# or
source backend/venv/bin/activate # On Linux/macOS or Git Bash

# Install dependencies
backend/venv/Scripts/pip install -r backend/requirements.txt
```

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

#### Git Integrity - NON-NEGOTIABLE
**Violation of these rules will corrupt the repository. Exercise extreme caution.**

1.  **NEVER Force Push to a Shared Branch:** Under no circumstances should `git push --force` be used on `main`, `develop`, or any branch that has an open pull request. Force pushing overwrites history and can permanently delete the work of others.
2.  **Check Branch Status Before Pushing:** Before pushing any changes, always verify the state of your branch and its corresponding pull request.
    ```bash
    # Check for new changes on the remote main branch
    git fetch lealvona
    git status

    # Check the status of your pull request (e.g., for PR #9)
    gh pr status 9
    ```
3.  **Keep Your Branches Updated:** To avoid merge conflicts and outdated work, regularly rebase your feature branches onto the latest `main` branch.
    ```bash
    # Switch to main and pull the latest changes
    git checkout main
    git pull lealvona main

    # Switch back to your feature branch and rebase
    git checkout my-feature-branch
    git rebase main
    ```
4.  **Resolve Conflicts Locally:** If a rebase or merge results in conflicts, resolve them on your local machine. Do not push code with conflict markers.
5.  **Create New Pull Requests After a Bad Merge/Rebase:** If you have irreparably damaged a branch with a bad rebase or by force-pushing, DO NOT continue working on it. Close the associated pull request, create a *new* branch from a clean `main`, and carefully `cherry-pick` your commits. This isolates the damage and prevents it from spreading.


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
After modifying backend code, ensure all tests pass. This typically involves:
```bash
# Run unit tests using pytest (after activating your Python virtual environment)
backend/venv/Scripts/python -m pytest backend/tests/

# Rebuild and restart Docker containers to apply changes
docker compose up --build -d

# Check backend logs for any runtime errors
docker logs code-tumbler-backend --tail 30

# Test an API endpoint (example)
curl -s http://localhost:8000/api/projects
```