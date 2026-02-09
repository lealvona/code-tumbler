#!/usr/bin/env python3
"""One-time backfill: reads existing state.json and usage.json files and inserts into DB."""

import json
import sys
from pathlib import Path

# Add backend/src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from utils.config import load_config
from db.session import init_engines, get_sync_session
from db.repository import ProjectRepository


def backfill(workspace_path: str = None):
    """Walk workspace and insert all project data into DB."""
    backend_root = Path(__file__).parent.parent
    config = load_config(str(backend_root / "config.yaml"))

    if not init_engines(config.database):
        print("ERROR: Could not connect to database")
        sys.exit(1)

    session = get_sync_session()
    if session is None:
        print("ERROR: Could not create database session")
        sys.exit(1)

    # Resolve workspace
    if workspace_path:
        workspace = Path(workspace_path)
    else:
        base = Path(config.workspace.base_path)
        if not base.is_absolute():
            base = backend_root / base
        workspace = base.resolve()

    if not workspace.exists():
        print(f"Workspace not found: {workspace}")
        sys.exit(1)

    print(f"Scanning workspace: {workspace}")
    project_count = 0
    iteration_count = 0

    for project_dir in sorted(workspace.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        state_file = project_dir / ".tumbler" / "state.json"
        usage_file = project_dir / ".tumbler" / "usage.json"

        if not state_file.exists():
            print(f"  SKIP {project_dir.name} (no state.json)")
            continue

        # Load and upsert project state
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        ProjectRepository.sync_upsert_project(session, project_dir.name, state)
        project_count += 1
        print(f"  OK   {project_dir.name} (status={state.get('status', 'unknown')})")

        # Load and insert usage history
        if usage_file.exists():
            with open(usage_file, "r", encoding="utf-8") as f:
                usage = json.load(f)

            for entry in usage.get("history", []):
                try:
                    ProjectRepository.sync_log_iteration(
                        session,
                        project_name=project_dir.name,
                        iteration_number=state.get("iteration", 0),
                        agent=entry.get("agent", "unknown"),
                        input_tokens=entry.get("input_tokens", 0),
                        output_tokens=entry.get("output_tokens", 0),
                        cost=entry.get("cost", 0.0),
                    )
                    iteration_count += 1
                except Exception as e:
                    print(f"    WARN: Could not insert iteration: {e}")

    print(f"\nDone: {project_count} projects, {iteration_count} iterations backfilled.")


if __name__ == "__main__":
    workspace_arg = sys.argv[1] if len(sys.argv) > 1 else None
    backfill(workspace_arg)
