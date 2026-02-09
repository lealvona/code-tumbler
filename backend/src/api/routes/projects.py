"""Project CRUD, start/stop, artifacts, usage, and per-project provider endpoints."""

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.state_manager import StateManager
from agents import ArchitectAgent, EngineerAgent, VerifierAgent
from utils.provider_factory import create_provider
from utils.config import resolve_agent_provider
from api.api_orchestrator import APIOrchestrator
from db.session import async_session_dep
from db.repository import ProjectRepository

router = APIRouter(tags=["projects"])


class CompressionSettings(BaseModel):
    enabled: Optional[bool] = None
    rate: Optional[float] = None
    preserve_code_blocks: Optional[bool] = None


_ALLOWED_VERIFICATION_KEYS = {
    "timeout_install", "timeout_build", "timeout_test", "timeout_lint",
    "memory_limit", "cpu_limit", "tmpfs_size",
}


class ProjectCreate(BaseModel):
    name: str
    requirements: str
    max_iterations: Optional[int] = None
    quality_threshold: Optional[float] = None
    provider_overrides: Optional[Dict[str, str]] = None
    verification_overrides: Optional[Dict[str, Any]] = None
    compression: Optional[CompressionSettings] = None


class StartProjectBody(BaseModel):
    provider_overrides: Optional[Dict[str, str]] = None


class UpdateProjectProviders(BaseModel):
    provider_overrides: Dict[str, str]


class UpdateCompression(BaseModel):
    enabled: Optional[bool] = None
    rate: Optional[float] = None
    preserve_code_blocks: Optional[bool] = None


@router.get("/projects")
async def list_projects(request: Request, session: AsyncSession = Depends(async_session_dep)):
    """List all projects with their status."""
    active = request.app.state.active_orchestrators

    # Try DB first
    if session is not None:
        try:
            db_projects = await ProjectRepository.async_list_projects(session)
            return [
                {
                    "name": p.name,
                    "status": p.status,
                    "iteration": p.current_iteration,
                    "last_score": p.last_score,
                    "last_update": p.last_update.isoformat() + "Z" if p.last_update else None,
                    "is_running": p.name in active,
                }
                for p in db_projects
            ]
        except Exception:
            pass  # Fall through to filesystem

    # Filesystem fallback
    workspace = _get_workspace(request)
    if not workspace.exists():
        return []

    projects = []
    for project_dir in sorted(workspace.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        sm = StateManager(project_dir)
        state = sm.load_state()
        projects.append({
            "name": project_dir.name,
            "status": state.get("status", "idle"),
            "iteration": state.get("iteration", 0),
            "last_score": state.get("last_score"),
            "last_update": state.get("last_update"),
            "is_running": project_dir.name in active,
        })
    return projects


@router.post("/projects")
async def create_project(body: ProjectCreate, request: Request):
    """Create a new project with requirements."""
    workspace = _get_workspace(request)
    workspace.mkdir(parents=True, exist_ok=True)

    project_dir = workspace / body.name
    if project_dir.exists():
        raise HTTPException(400, f"Project '{body.name}' already exists")

    # Create directory structure
    for subdir in ["01_input", "02_plan", "03_staging", "04_feedback", "05_final"]:
        (project_dir / subdir).mkdir(parents=True)

    # Write requirements
    (project_dir / "01_input" / "requirements.txt").write_text(
        body.requirements, encoding="utf-8"
    )

    # Initialize state with optional overrides
    sm = StateManager(project_dir)
    state = sm._default_state()

    if body.max_iterations is not None:
        state['max_iterations'] = body.max_iterations
    if body.quality_threshold is not None:
        state['quality_threshold'] = body.quality_threshold
    if body.provider_overrides:
        config = request.app.state.config
        for agent_name, provider_name in body.provider_overrides.items():
            if agent_name not in ("architect", "engineer", "verifier"):
                raise HTTPException(400, f"Invalid agent name: {agent_name}")
            if provider_name not in config.providers:
                raise HTTPException(400, f"Provider '{provider_name}' not found")
        state['provider_overrides'] = body.provider_overrides
    if body.compression:
        comp = body.compression
        if comp.enabled is not None:
            state['compression']['enabled'] = comp.enabled
        if comp.rate is not None:
            if not 0.1 <= comp.rate <= 1.0:
                raise HTTPException(400, "Compression rate must be between 0.1 and 1.0")
            state['compression']['rate'] = comp.rate
        if comp.preserve_code_blocks is not None:
            state['compression']['preserve_code_blocks'] = comp.preserve_code_blocks
    if body.verification_overrides:
        bad_keys = set(body.verification_overrides) - _ALLOWED_VERIFICATION_KEYS
        if bad_keys:
            raise HTTPException(400, f"Invalid verification override keys: {bad_keys}")
        state['verification'] = body.verification_overrides

    sm.save_state(state)

    return {"name": body.name, "status": "created"}


@router.get("/projects/{name}/status")
async def get_project_status(name: str, request: Request):
    """Get detailed project status."""
    project_dir = _get_project_dir(request, name)
    config = request.app.state.config
    sm = StateManager(project_dir)
    state = sm.load_state()
    state["is_running"] = name in request.app.state.active_orchestrators

    # Include effective provider info
    overrides = state.get('provider_overrides', {})
    providers = {}
    for agent_name in ("architect", "engineer", "verifier"):
        try:
            pc = resolve_agent_provider(config, agent_name, overrides)
            providers[agent_name] = {
                "provider": pc.name,
                "model": pc.model,
                "is_override": agent_name in overrides,
            }
        except KeyError:
            providers[agent_name] = {
                "provider": "unknown",
                "model": "unknown",
                "is_override": agent_name in overrides,
            }
    state["providers"] = providers
    state["compression"] = sm.get_compression_config()

    # Async concurrency capabilities per agent
    async_capabilities = {}
    for agent_name in ("architect", "engineer", "verifier"):
        try:
            pc = resolve_agent_provider(config, agent_name, overrides)
            provider = create_provider(pc)
            has_async = hasattr(provider, "async_chat")
            async_capabilities[agent_name] = {
                "supports_async": has_async,
                "concurrency_limit": pc.concurrency_limit,
                "parallel_generation": has_async and agent_name == "engineer",
            }
        except Exception:
            async_capabilities[agent_name] = {
                "supports_async": False,
                "concurrency_limit": 1,
                "parallel_generation": False,
            }
    state["async_capabilities"] = async_capabilities

    # Effective verification config (global merged with per-project overrides)
    import dataclasses
    from utils.config import VerificationConfig
    vc = config.verification
    project_vc_overrides = sm.get_verification_overrides()
    if project_vc_overrides:
        valid = {
            k: v for k, v in project_vc_overrides.items()
            if k in {f.name for f in dataclasses.fields(VerificationConfig)}
        }
        if valid:
            vc = dataclasses.replace(vc, **valid)
    state["verification_config"] = dataclasses.asdict(vc)

    return state


@router.get("/projects/{name}/conversation")
async def get_conversation(name: str, request: Request):
    """Get the agent conversation log for a project."""
    project_dir = _get_project_dir(request, name)
    sm = StateManager(project_dir)
    return sm.load_conversation()


@router.get("/projects/{name}/artifacts")
async def get_artifacts(name: str, request: Request):
    """Get file tree of the project's staging directory."""
    project_dir = _get_project_dir(request, name)
    staging_dir = project_dir / "03_staging"

    if not staging_dir.exists():
        return {"name": "03_staging", "path": "", "type": "directory", "children": []}

    return _build_file_tree(staging_dir, staging_dir)


@router.get("/projects/{name}/artifacts/{file_path:path}")
async def get_artifact_content(name: str, file_path: str, request: Request):
    """Get content of a specific file from staging."""
    project_dir = _get_project_dir(request, name)
    staging_dir = project_dir / "03_staging"

    # Sanitize path to prevent directory traversal
    target = (staging_dir / file_path).resolve()
    if not str(target).startswith(str(staging_dir.resolve())):
        raise HTTPException(400, "Invalid file path")

    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = "(binary file)"

    return {"path": file_path, "content": content, "size": target.stat().st_size}


@router.get("/projects/{name}/usage")
async def get_project_usage(name: str, request: Request, session: AsyncSession = Depends(async_session_dep)):
    """Get token usage data for a project."""
    # Try DB first
    if session is not None:
        try:
            usage = await ProjectRepository.async_get_project_usage(session, name)
            if usage["total_tokens"] > 0 or usage["history"]:
                return usage
        except Exception:
            pass  # Fall through to filesystem

    # Filesystem fallback
    project_dir = _get_project_dir(request, name)
    usage_file = project_dir / ".tumbler" / "usage.json"

    if not usage_file.exists():
        return {"total_tokens": 0, "total_cost": 0.0, "by_agent": {}, "history": []}

    return json.loads(usage_file.read_text(encoding="utf-8"))


@router.post("/projects/{name}/start")
async def start_project(name: str, request: Request, body: Optional[StartProjectBody] = None):
    """Start the tumbling cycle for a project."""
    project_dir = _get_project_dir(request, name)
    config = request.app.state.config
    event_bus = request.app.state.event_bus

    if name in request.app.state.active_orchestrators:
        raise HTTPException(409, "Project already running")

    # Verify requirements exist
    req_file = project_dir / "01_input" / "requirements.txt"
    if not req_file.exists():
        raise HTTPException(400, "No requirements.txt found in project")

    # Save provider overrides if provided
    if body and body.provider_overrides:
        for agent_name, provider_name in body.provider_overrides.items():
            if agent_name not in ("architect", "engineer", "verifier"):
                raise HTTPException(400, f"Invalid agent name: {agent_name}")
            if provider_name not in config.providers:
                raise HTTPException(400, f"Provider '{provider_name}' not found")
        sm = StateManager(project_dir)
        sm.set_provider_overrides(body.provider_overrides)

    def run_tumble():
        try:
            sm = StateManager(project_dir)
            overrides = sm.get_provider_overrides()

            # Create providers using three-tier resolution
            architect_config = resolve_agent_provider(config, "architect", overrides)
            engineer_config = resolve_agent_provider(config, "engineer", overrides)
            verifier_config = resolve_agent_provider(config, "verifier", overrides)

            architect_provider = create_provider(architect_config)
            architect_provider._resolved_name = architect_config.name
            engineer_provider = create_provider(engineer_config)
            engineer_provider._resolved_name = engineer_config.name
            verifier_provider = create_provider(verifier_config)
            verifier_provider._resolved_name = verifier_config.name

            architect = ArchitectAgent(
                architect_provider,
                nothink_override=config.agent_nothink.get("architect"),
            )
            engineer = EngineerAgent(
                engineer_provider,
                nothink_override=config.agent_nothink.get("engineer"),
            )
            verifier = VerifierAgent(
                verifier_provider,
                verification_config=config.verification,
                nothink_override=config.agent_nothink.get("verifier"),
            )

            orch = APIOrchestrator(
                event_bus=event_bus,
                config=config,
                workspace_root=_get_workspace(request),
                architect=architect,
                engineer=engineer,
                verifier=verifier,
                quality_threshold=config.tumbler.quality_threshold,
                max_iterations=config.tumbler.max_iterations,
                max_cost_per_project=config.tumbler.max_cost_per_project,
            )
            request.app.state.active_orchestrators[name] = orch
            orch.run_cycle(project_dir)
        except Exception as e:
            event_bus.publish("project_failed", {
                "project": name,
                "error": str(e),
            })
        finally:
            request.app.state.active_orchestrators.pop(name, None)

    thread = threading.Thread(target=run_tumble, daemon=True)
    thread.start()

    return {"status": "started", "project": name}


@router.post("/projects/{name}/stop")
async def stop_project(name: str, request: Request):
    """Stop a running tumbling cycle."""
    orch = request.app.state.active_orchestrators.get(name)
    if not orch:
        raise HTTPException(404, "Project is not running")

    orch.stop()
    request.app.state.active_orchestrators.pop(name, None)
    return {"status": "stopped", "project": name}


@router.post("/projects/{name}/reset")
async def reset_project(name: str, request: Request):
    """Reset a project to its initial state, clearing all generated artifacts."""
    project_dir = _get_project_dir(request, name)

    if name in request.app.state.active_orchestrators:
        raise HTTPException(409, "Cannot reset a running project. Stop it first.")

    sm = StateManager(project_dir)
    sm.full_reset()

    return {"status": "reset", "project": name}


@router.delete("/projects/{name}")
async def delete_project(name: str, request: Request, session: AsyncSession = Depends(async_session_dep)):
    """Permanently delete a project, removing all files and database records."""
    project_dir = _get_project_dir(request, name)

    if name in request.app.state.active_orchestrators:
        raise HTTPException(409, "Cannot delete a running project. Stop it first.")

    # Delete from database
    if session is not None:
        try:
            await ProjectRepository.async_delete_project(session, name)
        except Exception:
            pass  # Proceed with filesystem cleanup even if DB fails

    # Delete project directory (safe file-by-file deletion)
    sm = StateManager(project_dir)
    deleted, skipped = sm.safe_delete_project()

    # Publish event
    event_bus = request.app.state.event_bus
    event_bus.publish("project_deleted", {"project": name})

    return {"status": "deleted", "project": name, "files_deleted": deleted, "files_skipped": skipped}


@router.get("/projects/{name}/providers")
async def get_project_providers(name: str, request: Request):
    """Get effective provider configuration for a project."""
    project_dir = _get_project_dir(request, name)
    config = request.app.state.config
    sm = StateManager(project_dir)
    overrides = sm.get_provider_overrides()

    effective = {}
    for agent_name in ("architect", "engineer", "verifier"):
        try:
            pc = resolve_agent_provider(config, agent_name, overrides)
            effective[agent_name] = {
                "provider": pc.name,
                "model": pc.model,
                "type": pc.type.value,
                "is_override": agent_name in overrides,
            }
        except KeyError:
            effective[agent_name] = {
                "provider": "unknown",
                "model": "unknown",
                "type": "unknown",
                "is_override": agent_name in overrides,
            }

    return {"overrides": overrides, "effective": effective}


@router.put("/projects/{name}/providers")
async def update_project_providers(name: str, body: UpdateProjectProviders, request: Request):
    """Update per-project provider overrides. Changes take effect on next iteration."""
    project_dir = _get_project_dir(request, name)
    config = request.app.state.config

    for agent_name, provider_name in body.provider_overrides.items():
        if agent_name not in ("architect", "engineer", "verifier"):
            raise HTTPException(400, f"Invalid agent name: {agent_name}")
        if provider_name not in config.providers:
            raise HTTPException(400, f"Provider '{provider_name}' not found")

    sm = StateManager(project_dir)
    sm.set_provider_overrides(body.provider_overrides)

    event_bus = request.app.state.event_bus
    event_bus.publish("providers_changed", {
        "project": name,
        "overrides": body.provider_overrides,
    })

    return {"status": "updated", "overrides": body.provider_overrides}


@router.get("/projects/{name}/compression")
async def get_compression(name: str, request: Request):
    """Get compression configuration for a project."""
    project_dir = _get_project_dir(request, name)
    sm = StateManager(project_dir)
    return sm.get_compression_config()


@router.put("/projects/{name}/compression")
async def update_compression(name: str, body: UpdateCompression, request: Request):
    """Update compression settings for a project. Changes take effect on next agent call."""
    project_dir = _get_project_dir(request, name)
    sm = StateManager(project_dir)

    update = {}
    if body.enabled is not None:
        update['enabled'] = body.enabled
    if body.rate is not None:
        if not 0.1 <= body.rate <= 1.0:
            raise HTTPException(400, "rate must be between 0.1 and 1.0")
        update['rate'] = body.rate
    if body.preserve_code_blocks is not None:
        update['preserve_code_blocks'] = body.preserve_code_blocks

    if update:
        sm.set_compression_config(update)

    return sm.get_compression_config()


# --- Helpers ---

def _get_workspace(request: Request) -> Path:
    config = request.app.state.config
    base = Path(config.workspace.base_path)
    if not base.is_absolute():
        base = Path(request.app.state.backend_root) / base
    return base.resolve()


def _get_project_dir(request: Request, name: str) -> Path:
    workspace = _get_workspace(request)
    project_dir = workspace / name
    if not project_dir.exists() or not project_dir.is_dir():
        raise HTTPException(404, f"Project '{name}' not found")
    return project_dir


def _build_file_tree(path: Path, root: Path) -> dict:
    """Build a file tree dict recursively."""
    rel = str(path.relative_to(root)).replace("\\", "/")
    if rel == ".":
        rel = ""

    node = {
        "name": path.name,
        "path": rel,
        "type": "directory" if path.is_dir() else "file",
    }

    if path.is_dir():
        children = []
        for child in sorted(path.iterdir()):
            if child.name.startswith("."):
                continue
            children.append(_build_file_tree(child, root))
        node["children"] = children
    else:
        node["size"] = path.stat().st_size

    return node
