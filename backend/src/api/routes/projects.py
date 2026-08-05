"""Project CRUD, start/stop, artifacts, usage, and per-project provider endpoints."""

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.state_manager import StateManager
from agents import SpecifierAgent, ArchitectAgent, EngineerAgent, VerifierAgent
from agents.specifier import SPEC_ARCHIVE_FORMAT, REQUIRED_SPEC_FILES
from utils.provider_factory import create_provider
from utils.config import resolve_agent_provider, AGENT_ROLES
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
    "timeout_e2e", "memory_limit", "memory_limit_e2e", "cpu_limit",
    "tmpfs_size", "e2e_enabled",
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
    for subdir in ["01_input", "spec", "02_plan", "03_staging", "04_feedback", "05_final"]:
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
            if agent_name not in AGENT_ROLES:
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
    for agent_name in AGENT_ROLES:
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
    for agent_name in AGENT_ROLES:
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


def _spec_files(spec_dir: Path) -> List[Dict[str, Any]]:
    """Ordered flat list of spec YAML files under a project's spec/ dir."""
    if not spec_dir.exists():
        return []
    files = []
    for f in sorted(spec_dir.rglob("*.yaml")):
        rel = f.relative_to(spec_dir.parent).as_posix()  # e.g. "spec/00-base.yaml"
        files.append({"path": rel, "size": f.stat().st_size})
    return files


def _resolve_spec_path(project_dir: Path, file_path: str) -> Path:
    """Resolve a spec-relative path with traversal protection.

    Accepts either "spec/00-base.yaml" or "00-base.yaml"; always confined to the
    project's spec/ directory.
    """
    spec_dir = (project_dir / "spec").resolve()
    rel = file_path[5:] if file_path.startswith("spec/") else file_path
    target = (spec_dir / rel).resolve()
    if not str(target).startswith(str(spec_dir)):
        raise HTTPException(400, "Invalid spec file path")
    return target


@router.get("/projects/{name}/spec")
async def get_spec(name: str, request: Request):
    """List the Phase-1 spec suite files and completion status."""
    project_dir = _get_project_dir(request, name)
    sm = StateManager(project_dir)
    spec_cfg = sm.get_spec_config()
    return {
        "enabled": spec_cfg.get("enabled", True),
        "complete": spec_cfg.get("complete", False),
        "required": list(REQUIRED_SPEC_FILES),
        "files": _spec_files(project_dir / "spec"),
    }


@router.get("/projects/{name}/spec/export")
async def export_spec(name: str, request: Request):
    """Export the spec suite as a code-tumbler-spec-archive JSON archive."""
    project_dir = _get_project_dir(request, name)
    archive_file = project_dir / ".tumbler" / "spec_archive.json"
    if archive_file.exists():
        return json.loads(archive_file.read_text(encoding="utf-8"))
    # Rebuild a minimal archive from files on disk if no cached archive exists.
    spec_dir = project_dir / "spec"
    if not spec_dir.exists() or not any(spec_dir.rglob("*.yaml")):
        raise HTTPException(404, "No spec suite found for this project")
    files = []
    for f in sorted(spec_dir.rglob("*.yaml")):
        rel = f.relative_to(spec_dir.parent).as_posix()
        files.append({"path": rel, "content": f.read_text(encoding="utf-8"), "guide": {}})
    sm = StateManager(project_dir)
    idea = ""
    req = project_dir / "01_input" / "requirements.txt"
    if req.exists():
        idea = req.read_text(encoding="utf-8")
    return {
        "format": SPEC_ARCHIVE_FORMAT,
        "format_version": 1,
        "spec_version": "0.1.0",
        "session": {"name": name, "description": idea},
        "files": files,
    }


@router.post("/projects/{name}/spec/import")
async def import_spec(name: str, request: Request):
    """Import a spec archive, validating the format discriminator."""
    project_dir = _get_project_dir(request, name)
    if name in request.app.state.active_orchestrators:
        raise HTTPException(409, "Cannot import spec into a running project. Stop it first.")
    archive = await request.json()
    if not isinstance(archive, dict) or archive.get("format") != SPEC_ARCHIVE_FORMAT:
        raise HTTPException(400, f"Invalid archive: expected format '{SPEC_ARCHIVE_FORMAT}'")
    files = archive.get("files", [])
    if not isinstance(files, list) or not files:
        raise HTTPException(400, "Archive contains no files")

    written = 0
    for fo in files:
        path = (fo.get("path") or "").strip().lstrip("/")
        content = fo.get("content", "")
        if not path or not isinstance(content, str):
            continue
        if not path.startswith("spec/"):
            continue
        target = _resolve_spec_path(project_dir, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written += 1

    # Persist the archive and mark complete if the required set is present.
    (project_dir / ".tumbler").mkdir(parents=True, exist_ok=True)
    (project_dir / ".tumbler" / "spec_archive.json").write_text(
        json.dumps(archive, indent=2), encoding="utf-8"
    )
    sm = StateManager(project_dir)
    complete = all((project_dir / f).exists() for f in REQUIRED_SPEC_FILES)
    sm.set_spec_complete(complete)

    event_bus = request.app.state.event_bus
    event_bus.publish("spec_imported", {"project": name, "files": written})
    return {"status": "imported", "files": written, "complete": complete}


@router.get("/projects/{name}/spec/{file_path:path}")
async def get_spec_file(name: str, file_path: str, request: Request):
    """Get the content of a single spec file."""
    project_dir = _get_project_dir(request, name)
    target = _resolve_spec_path(project_dir, file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Spec file not found")
    return {
        "path": file_path if file_path.startswith("spec/") else f"spec/{file_path}",
        "content": target.read_text(encoding="utf-8"),
        "size": target.stat().st_size,
    }


@router.put("/projects/{name}/spec/{file_path:path}")
async def save_spec_file(name: str, file_path: str, request: Request):
    """Save (edit) a single spec file's content."""
    project_dir = _get_project_dir(request, name)
    if name in request.app.state.active_orchestrators:
        raise HTTPException(409, "Cannot edit spec of a running project. Stop it first.")
    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(400, "Body must include a string 'content'")
    target = _resolve_spec_path(project_dir, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "saved", "path": file_path, "size": target.stat().st_size}


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
            if agent_name not in AGENT_ROLES:
                raise HTTPException(400, f"Invalid agent name: {agent_name}")
            if provider_name not in config.providers:
                raise HTTPException(400, f"Provider '{provider_name}' not found")
        sm = StateManager(project_dir)
        sm.set_provider_overrides(body.provider_overrides)

    def run_tumble():
        orch = None
        try:
            sm = StateManager(project_dir)
            overrides = sm.get_provider_overrides()

            # Create providers using three-tier resolution
            specifier_config = resolve_agent_provider(config, "specifier", overrides)
            architect_config = resolve_agent_provider(config, "architect", overrides)
            engineer_config = resolve_agent_provider(config, "engineer", overrides)
            verifier_config = resolve_agent_provider(config, "verifier", overrides)

            specifier_provider = create_provider(specifier_config)
            specifier_provider._resolved_name = specifier_config.name
            architect_provider = create_provider(architect_config)
            architect_provider._resolved_name = architect_config.name
            engineer_provider = create_provider(engineer_config)
            engineer_provider._resolved_name = engineer_config.name
            verifier_provider = create_provider(verifier_config)
            verifier_provider._resolved_name = verifier_config.name

            specifier = SpecifierAgent(
                specifier_provider,
                nothink_override=config.agent_nothink.get("specifier"),
            )
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
                specifier=specifier,
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
            # Only deregister OUR orchestrator. A stop() -> start() sequence can
            # register a new run before the old thread's finally executes; an
            # unconditional pop would deregister the NEW run, making it invisible
            # to is_running/stop and allowing a concurrent duplicate start.
            if request.app.state.active_orchestrators.get(name) is orch:
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
    for agent_name in AGENT_ROLES:
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
        if agent_name not in AGENT_ROLES:
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