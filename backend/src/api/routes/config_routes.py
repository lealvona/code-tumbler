"""Configuration endpoints."""

import yaml
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException

router = APIRouter(tags=["config"])


@router.get("/config")
async def get_config(request: Request):
    """Get current system configuration (safe fields only)."""
    config = request.app.state.config
    return {
        "active_provider": config.active_provider,
        "agent_providers": config.agent_providers,
        "tumbler": {
            "max_iterations": config.tumbler.max_iterations,
            "quality_threshold": config.tumbler.quality_threshold,
            "project_timeout": config.tumbler.project_timeout,
            "debounce_time": config.tumbler.debounce_time,
            "max_cost_per_project": config.tumbler.max_cost_per_project,
            "prompt_compression": {
                "enabled": config.tumbler.prompt_compression.enabled,
                "rate": config.tumbler.prompt_compression.rate,
                "preserve_code_blocks": config.tumbler.prompt_compression.preserve_code_blocks,
            }
        },
    }


@router.put("/config")
async def update_config(body: dict, request: Request):
    """Update system configuration and reload."""
    config_path = Path(request.app.state.backend_root) / "config.yaml"

    if not config_path.exists():
        raise HTTPException(500, "Config file not found")

    # Load existing yaml
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Update safe fields only
    if "active_provider" in body:
        if body["active_provider"] not in data.get("providers", {}):
            raise HTTPException(400, f"Provider '{body['active_provider']}' not found")
        data["active_provider"] = body["active_provider"]

    if "agent_providers" in body:
        data["agent_providers"] = body["agent_providers"]

    if "tumbler" in body:
        if "tumbler" not in data:
            data["tumbler"] = {}
        for key in ["max_iterations", "quality_threshold", "project_timeout", "debounce_time", "max_cost_per_project"]:
            if key in body["tumbler"]:
                data["tumbler"][key] = body["tumbler"][key]
        
        if "prompt_compression" in body["tumbler"]:
            if "prompt_compression" not in data["tumbler"]:
                data["tumbler"]["prompt_compression"] = {}
            for key in ["enabled", "rate", "preserve_code_blocks"]:
                if key in body["tumbler"]["prompt_compression"]:
                    data["tumbler"]["prompt_compression"][key] = body["tumbler"]["prompt_compression"][key]

    # Write back
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    # Reload config
    from utils.config import load_config
    request.app.state.config = load_config(str(config_path))

    return {"status": "updated"}
