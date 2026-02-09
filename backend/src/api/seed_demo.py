"""Seed a demo project on first startup if the workspace is empty."""

import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

DEMO_PROJECT_NAME = "hello-tumbler"

DEMO_REQUIREMENTS = """\
Create a simple Python CLI application called "hello-tumbler" that:

1. Prints a colourful ASCII art banner on startup
2. Accepts a --name flag (default: "World")
3. Prints "Hello, {name}! Welcome to Code Tumbler."
4. Includes a pytest test suite that verifies the greeting logic
5. Has a pyproject.toml with project metadata

Keep it simple — one source file, one test file, and a pyproject.toml.
""".strip()


def seed_demo_project(workspace_root: Path) -> bool:
    """Create a demo project if the workspace is empty.

    Returns True if a demo was seeded, False if skipped.
    """
    # Skip if any project directories already exist
    if workspace_root.exists():
        existing = [
            d for d in workspace_root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        if existing:
            return False

    project_dir = workspace_root / DEMO_PROJECT_NAME
    input_dir = project_dir / "01_input"
    state_dir = project_dir / ".tumbler"

    input_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "logs").mkdir(exist_ok=True)

    # Write requirements
    (input_dir / "requirements.txt").write_text(DEMO_REQUIREMENTS, encoding="utf-8")

    # Write initial state
    now = datetime.utcnow().isoformat() + "Z"
    state = {
        "status": "idle",
        "current_phase": "idle",
        "iteration": 0,
        "max_iterations": 10,
        "quality_threshold": 8.0,
        "last_score": None,
        "start_time": now,
        "last_update": now,
        "error": None,
        "provider_overrides": {},
        "verification": {},
        "compression": {
            "enabled": True,
            "rate": 0.5,
            "preserve_code_blocks": True,
        },
    }
    (state_dir / "state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )

    logger.info("Seeded demo project: %s", DEMO_PROJECT_NAME)
    return True
