"""FastAPI application factory for Code Tumbler API."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.event_bus import EventBus
from api.routes import health, projects, providers, config_routes, events, analytics
from api.seed_demo import seed_demo_project
from db.session import init_engines
from utils.config import load_config
from utils.logger import setup_logger


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    setup_logger(level="INFO", log_format="text")

    app = FastAPI(
        title="Code Tumbler API",
        description="Code Tumbler Backend API",
        version="0.1.0",
    )

    # CORS - allow the configured frontend URL plus common local variants
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    cors_origins = [frontend_url]
    # Also allow 127.0.0.1 and localhost variants so either works in the browser
    for host in ("localhost", "127.0.0.1"):
        for scheme in ("http",):
            variant = f"{scheme}://{host}:3000"
            if variant not in cors_origins:
                cors_origins.append(variant)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize state eagerly (lifespan protocol unreliable on this stack)
    backend_root = Path(__file__).parent.parent.parent
    app.state.backend_root = str(backend_root)
    app.state.config = load_config(str(backend_root / "config.yaml"))
    app.state.event_bus = EventBus()
    app.state.active_orchestrators = {}

    # Initialize database engines (graceful if DB unavailable)
    app.state.db_available = init_engines(app.state.config.database)

    # Seed demo project on first startup (no-op if workspace is non-empty)
    workspace = Path(app.state.config.workspace.base_path)
    if not workspace.is_absolute():
        workspace = backend_root / workspace
    seed_demo_project(workspace.resolve())

    # Register route modules
    app.include_router(health.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(config_routes.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")

    return app
