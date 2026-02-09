"""Provider listing and model discovery endpoints."""

import asyncio
import logging
import time

from fastapi import APIRouter, Request, HTTPException

from utils.provider_factory import create_provider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["providers"])


@router.get("/providers")
async def list_providers(request: Request):
    """List all configured providers."""
    config = request.app.state.config
    result = []
    for name, pc in config.providers.items():
        # Check async capability by inspecting the provider class
        try:
            provider = create_provider(pc)
            supports_async = hasattr(provider, "async_chat")
        except Exception:
            supports_async = False
        result.append({
            "name": name,
            "type": pc.type.value,
            "model": pc.model,
            "base_url": pc.base_url,
            "is_active": name == config.active_provider,
            "cost_input": pc.cost_per_1k_input_tokens,
            "cost_output": pc.cost_per_1k_output_tokens,
            "supports_async": supports_async,
            "concurrency_limit": pc.concurrency_limit,
        })
    return result


@router.get("/providers/{name}/models")
async def list_provider_models(name: str, request: Request):
    """List available models from a provider."""
    config = request.app.state.config
    pc = config.providers.get(name)
    if not pc:
        raise HTTPException(404, f"Provider '{name}' not found")

    try:
        provider = create_provider(pc)
        models = provider.list_models()
        return {"provider": name, "models": models}
    except Exception as e:
        raise HTTPException(502, f"Failed to list models: {str(e)}")


@router.get("/providers/{name}/health")
async def provider_health(name: str, request: Request):
    """Check provider health asynchronously.

    Uses the provider's async_health_check if available, otherwise falls back
    to a sync list_models call in a thread.
    """
    config = request.app.state.config
    pc = config.providers.get(name)
    if not pc:
        raise HTTPException(404, f"Provider '{name}' not found")

    try:
        provider = create_provider(pc)
        t0 = time.monotonic()

        # Prefer async health check when available
        if hasattr(provider, "async_health_check"):
            result = await provider.async_health_check()
        elif hasattr(provider, "validate_server_config"):
            result = await provider.validate_server_config()
        else:
            # Fallback: run sync list_models in a thread
            models = await asyncio.to_thread(provider.list_models)
            result = {
                "healthy": True,
                "model": pc.model,
                "available_models": models,
                "warnings": [] if pc.model in models else [
                    f"Configured model '{pc.model}' not in available models"
                ],
            }

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Normalise result to a dict if it's a bool (vLLM/Ollama return bool)
        if isinstance(result, bool):
            result = {
                "healthy": result,
                "model": pc.model,
                "warnings": [] if result else [
                    getattr(provider, "_health_check_error", "Health check failed")
                ],
            }

        result["response_time_ms"] = round(elapsed_ms, 1)
        result["provider"] = name
        result["type"] = pc.type.value

        # Clean up async client if provider has one
        if hasattr(provider, "close"):
            try:
                await provider.close()
            except Exception:
                pass

        return result
    except Exception as e:
        logger.warning(f"Health check failed for provider '{name}': {e}")
        return {
            "provider": name,
            "type": pc.type.value,
            "healthy": False,
            "model": pc.model,
            "warnings": [str(e)],
        }
