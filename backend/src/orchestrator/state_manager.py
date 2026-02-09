"""State Manager - Persists project state to disk.

Manages the .tumbler/state.json file for each project, enabling crash recovery
and status tracking.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Subdirectories that full_reset() is allowed to clear.
# Any directory not in this set will be refused.
_CLEARABLE_PROJECT_SUBDIRS = frozenset({
    "02_plan",
    "03_staging",
    "04_feedback",
    "05_final",
})

_CLEARABLE_STATE_SUBDIRS = frozenset({
    "logs",
})


class ProjectPhase(Enum):
    """Phases in the project lifecycle."""
    IDLE = "idle"
    PLANNING = "planning"
    ENGINEERING = "engineering"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class StateManager:
    """Manages project state persistence (JSON files + optional PostgreSQL)."""

    def __init__(self, project_path: Path):
        """Initialize state manager for a project.

        Args:
            project_path: Path to the project root directory
        """
        self.project_path = project_path
        self.state_dir = project_path / ".tumbler"
        self.state_file = self.state_dir / "state.json"
        self.usage_file = self.state_dir / "usage.json"
        self.logs_dir = self.state_dir / "logs"
        self._db_session = None

        # Ensure directories exist
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _get_db_session(self):
        """Get or create a sync DB session. Returns None if DB unavailable."""
        if self._db_session is None:
            try:
                from db.session import get_sync_session
                self._db_session = get_sync_session()
            except Exception:
                pass
        return self._db_session

    def load_state(self) -> Dict[str, Any]:
        """Load project state from disk.

        Returns:
            Dictionary containing project state, or default state if not found
        """
        if not self.state_file.exists():
            return self._default_state()

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            # If state file is corrupted, return default
            print(f"Warning: Could not load state file: {e}")
            return self._default_state()

    def save_state(self, state: Dict[str, Any]) -> None:
        """Save project state to disk and optionally to database.

        Args:
            state: Dictionary containing project state
        """
        state['last_update'] = datetime.utcnow().isoformat() + 'Z'

        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, indent=2, fp=f)
        except IOError as e:
            print(f"Error: Could not save state file: {e}")

        # Dual-write to database (best-effort)
        session = self._get_db_session()
        if session:
            try:
                from db.repository import ProjectRepository
                ProjectRepository.sync_upsert_project(session, self.get_project_name(), state)
            except Exception as e:
                print(f"Warning: DB write failed for state (JSON is primary): {e}")

    def update_phase(self, phase: ProjectPhase) -> None:
        """Update the current phase.

        Args:
            phase: New project phase
        """
        state = self.load_state()
        state['status'] = phase.value
        state['current_phase'] = phase.value
        self.save_state(state)

    def increment_iteration(self) -> int:
        """Increment iteration counter and return new value.

        Returns:
            New iteration number
        """
        state = self.load_state()
        state['iteration'] = state.get('iteration', 0) + 1
        self.save_state(state)
        return state['iteration']

    def get_iteration(self) -> int:
        """Get current iteration number.

        Returns:
            Current iteration number
        """
        state = self.load_state()
        return state.get('iteration', 0)

    def set_score(self, score: float) -> None:
        """Set the latest quality score.

        Args:
            score: Quality score from verifier (0-10)
        """
        state = self.load_state()
        state['last_score'] = score
        self.save_state(state)

    def get_score(self) -> Optional[float]:
        """Get the latest quality score.

        Returns:
            Latest score, or None if not set
        """
        state = self.load_state()
        return state.get('last_score')

    def is_complete(self, quality_threshold: float = 8.0, max_iterations: int = 10) -> bool:
        """Check if project is complete (score threshold met or max iterations reached).

        Args:
            quality_threshold: Minimum score to consider complete
            max_iterations: Maximum iterations before stopping

        Returns:
            True if project should be finalized
        """
        state = self.load_state()
        iteration = state.get('iteration', 0)
        score = state.get('last_score', 0.0)

        return score >= quality_threshold or iteration >= max_iterations

    def get_total_cost(self) -> float:
        """Get the cumulative cost for this project from usage.json.

        Returns:
            Total cost in dollars, or 0.0 if no usage data exists.
        """
        if self.usage_file.exists():
            try:
                with open(self.usage_file, 'r', encoding='utf-8') as f:
                    usage = json.load(f)
                return float(usage.get('total_cost', 0.0))
            except (json.JSONDecodeError, ValueError):
                return 0.0
        return 0.0

    def reset_for_run(self) -> None:
        """Reset state for a fresh run, clearing any previous error."""
        state = self.load_state()
        state['status'] = ProjectPhase.IDLE.value
        state['current_phase'] = ProjectPhase.IDLE.value
        state['error'] = None
        state['iteration'] = 0
        state['last_score'] = None
        self.save_state(state)
        self.clear_conversation()

    def _assert_within_project(self, path: Path) -> Path:
        """Validate that a path is strictly contained within the project directory.

        Resolves symlinks and checks that the real path doesn't escape the
        project root. This prevents path traversal, symlink escapes, and
        accidental operations on host paths via Docker mounts.

        Args:
            path: Path to validate.

        Returns:
            The resolved (real) path.

        Raises:
            ValueError: If the path escapes the project root.
        """
        resolved = path.resolve()
        project_resolved = self.project_path.resolve()

        try:
            resolved.relative_to(project_resolved)
        except ValueError:
            raise ValueError(
                f"Path '{path}' resolves to '{resolved}' which is outside "
                f"project root '{project_resolved}'"
            )
        return resolved

    def _safe_clear_dir(self, target: Path, *, allowed_names: frozenset) -> Tuple[int, int]:
        """Safely clear contents of a directory within the project.

        Security policies enforced:
        - Target directory name must be in the allowed_names set.
        - Every file path is validated to be within the project before deletion.
        - Symlinks pointing outside the project are removed (the link itself,
          not its target), but only if the link is within the project.
        - Files that can't be deleted are logged and skipped — no chmod,
          no permission changes, no force operations.
        - Directories are removed bottom-up, only after they become empty.
        - Mount points are never removed.

        Args:
            target: Directory to clear.
            allowed_names: Set of permitted directory names.

        Returns:
            Tuple of (files_deleted, files_skipped).
        """
        if target.name not in allowed_names:
            logger.error(
                f"Refusing to clear '{target.name}': "
                f"not in allowlist {allowed_names}"
            )
            return 0, 0

        try:
            resolved_target = self._assert_within_project(target)
        except ValueError as e:
            logger.error(f"Refusing to clear directory: {e}")
            return 0, 0

        if not resolved_target.exists():
            return 0, 0

        if not resolved_target.is_dir():
            logger.error(f"'{resolved_target}' is not a directory")
            return 0, 0

        if os.path.ismount(str(resolved_target)):
            logger.error(f"Refusing to clear mount point: {resolved_target}")
            return 0, 0

        deleted = 0
        skipped = 0
        project_resolved = self.project_path.resolve()

        # Walk bottom-up: remove files first, then empty directories
        for dirpath, dirnames, filenames in os.walk(str(resolved_target), topdown=False):
            dirpath_p = Path(dirpath)

            # Delete files
            for fname in filenames:
                fpath = dirpath_p / fname

                # Validate containment (handles symlink resolution)
                try:
                    if fpath.is_symlink():
                        # For symlinks: validate the link itself is in project,
                        # then remove the link (not the target)
                        link_loc = fpath.parent.resolve() / fpath.name
                        link_loc.relative_to(project_resolved)
                    else:
                        self._assert_within_project(fpath)
                except ValueError as e:
                    logger.warning(f"Skipping out-of-scope file: {e}")
                    skipped += 1
                    continue

                try:
                    fpath.unlink()
                    deleted += 1
                except OSError as e:
                    logger.warning(f"Could not delete {fpath}: {e}")
                    skipped += 1

            # Remove subdirectories only if they're empty and safe
            for dname in dirnames:
                dpath = dirpath_p / dname

                if os.path.ismount(str(dpath)):
                    logger.warning(f"Skipping mount point: {dpath}")
                    continue

                try:
                    self._assert_within_project(dpath)
                    dpath.rmdir()  # Only succeeds if empty
                except ValueError:
                    pass  # Out of scope — leave it
                except OSError:
                    pass  # Not empty — leave it

        return deleted, skipped

    def safe_delete_project(self) -> Tuple[int, int]:
        """Safely delete the entire project directory.

        Unlike full_reset (which preserves the directory structure), this
        removes all project contents and the project directory itself.

        Enforces the same safety policies as _safe_clear_dir:
        - Path containment validation
        - No chmod / force operations
        - Symlink-safe
        - Mount point protection
        - Logs and skips undeletable files

        Returns:
            Tuple of (files_deleted, files_skipped).
        """
        project_resolved = self.project_path.resolve()

        if not project_resolved.exists():
            return 0, 0

        if os.path.ismount(str(project_resolved)):
            logger.error(f"Refusing to delete mount point: {project_resolved}")
            return 0, 0

        deleted = 0
        skipped = 0

        # Walk bottom-up
        for dirpath, dirnames, filenames in os.walk(str(project_resolved), topdown=False):
            dirpath_p = Path(dirpath)

            for fname in filenames:
                fpath = dirpath_p / fname
                try:
                    if fpath.is_symlink():
                        link_loc = fpath.parent.resolve() / fpath.name
                        link_loc.relative_to(project_resolved)
                    else:
                        fpath.resolve().relative_to(project_resolved)
                except ValueError:
                    logger.warning(f"Skipping out-of-scope file: {fpath}")
                    skipped += 1
                    continue

                try:
                    fpath.unlink()
                    deleted += 1
                except OSError as e:
                    logger.warning(f"Could not delete {fpath}: {e}")
                    skipped += 1

            for dname in dirnames:
                dpath = dirpath_p / dname
                if os.path.ismount(str(dpath)):
                    logger.warning(f"Skipping mount point: {dpath}")
                    continue
                try:
                    dpath.resolve().relative_to(project_resolved)
                    dpath.rmdir()
                except (ValueError, OSError):
                    pass

        # Remove the project directory itself if now empty
        try:
            project_resolved.rmdir()
        except OSError:
            logger.warning(
                f"Project directory not empty after cleanup "
                f"(skipped {skipped} files): {project_resolved}"
            )

        return deleted, skipped

    def full_reset(self) -> None:
        """Full project reset: state, usage, conversation, logs, staging, plan, feedback.

        Uses safe file-by-file deletion with path containment validation.
        Files that can't be deleted (e.g., permission issues from Docker volumes)
        are logged and skipped — no chmod, no force operations.
        """
        # Reset state (preserve provider_overrides and name)
        state = self.load_state()
        overrides = state.get('provider_overrides', {})
        new_state = self._default_state()
        new_state['provider_overrides'] = overrides
        self.save_state(new_state)

        # Clear usage
        if self.usage_file.exists():
            try:
                self._assert_within_project(self.usage_file)
                self.usage_file.unlink()
            except (ValueError, OSError) as e:
                logger.warning(f"Could not clear usage file: {e}")

        # Clear conversation
        self.clear_conversation()

        # Clear logs (.tumbler/logs)
        d, s = self._safe_clear_dir(self.logs_dir, allowed_names=_CLEARABLE_STATE_SUBDIRS)
        logger.info(f"Reset logs: {d} deleted, {s} skipped")
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Clear project subdirectories
        for subdir_name in _CLEARABLE_PROJECT_SUBDIRS:
            target = self.project_path / subdir_name
            if target.exists():
                d, s = self._safe_clear_dir(target, allowed_names=_CLEARABLE_PROJECT_SUBDIRS)
                logger.info(f"Reset {subdir_name}: {d} deleted, {s} skipped")
                target.mkdir(parents=True, exist_ok=True)

    def mark_failed(self, error_message: str) -> None:
        """Mark project as failed.

        Args:
            error_message: Description of the failure
        """
        state = self.load_state()
        state['status'] = ProjectPhase.FAILED.value
        state['current_phase'] = ProjectPhase.FAILED.value
        state['error'] = error_message
        self.save_state(state)

    def log_usage(self, agent: str, input_tokens: int, output_tokens: int, cost: float,
                  compression_metrics: Optional[Dict[str, Any]] = None) -> None:
        """Log token usage for an agent execution.

        Args:
            agent: Agent name ('architect', 'engineer', 'verifier')
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cost: Estimated cost in dollars
            compression_metrics: Optional dict with compression stats
        """
        # Load existing usage
        if self.usage_file.exists():
            with open(self.usage_file, 'r', encoding='utf-8') as f:
                usage = json.load(f)
        else:
            usage = {
                'total_tokens': 0,
                'total_cost': 0.0,
                'by_agent': {},
                'history': []
            }

        # Update totals
        total_tokens = input_tokens + output_tokens
        usage['total_tokens'] += total_tokens
        usage['total_cost'] += cost

        # Update by-agent stats
        if agent not in usage['by_agent']:
            usage['by_agent'][agent] = {
                'tokens': 0,
                'cost': 0.0,
                'calls': 0
            }

        usage['by_agent'][agent]['tokens'] += total_tokens
        usage['by_agent'][agent]['cost'] += cost
        usage['by_agent'][agent]['calls'] += 1

        # Add to history
        entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'agent': agent,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cost': cost,
        }
        if compression_metrics:
            entry['compression'] = compression_metrics
        usage['history'].append(entry)

        # Save to file
        with open(self.usage_file, 'w', encoding='utf-8') as f:
            json.dump(usage, indent=2, fp=f)

        # Dual-write to database (best-effort)
        session = self._get_db_session()
        if session:
            try:
                from db.repository import ProjectRepository
                state = self.load_state()
                ProjectRepository.sync_log_iteration(
                    session,
                    project_name=self.get_project_name(),
                    iteration_number=state.get("iteration", 0),
                    agent=agent,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost=cost,
                )
            except Exception as e:
                print(f"Warning: DB write failed for usage (JSON is primary): {e}")

    def log_conversation(self, agent: str, role: str, content: str,
                         iteration: int = 0, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Append a message to the project conversation log.

        Args:
            agent: Agent name ('architect', 'engineer', 'verifier', 'system')
            role: 'input' or 'output'
            content: Message content
            iteration: Current iteration number
            metadata: Optional extra data (score, file count, etc.)
        """
        conv_file = self.state_dir / "conversation.jsonl"
        entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'agent': agent,
            'role': role,
            'iteration': iteration,
            'content': content,
        }
        if metadata:
            entry['metadata'] = metadata
        try:
            with open(conv_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except IOError as e:
            print(f"Warning: Could not write conversation log: {e}")

    def load_conversation(self) -> list:
        """Load the full conversation log.

        Returns:
            List of conversation message dicts.
        """
        conv_file = self.state_dir / "conversation.jsonl"
        if not conv_file.exists():
            return []
        messages = []
        try:
            with open(conv_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        messages.append(json.loads(line))
        except (IOError, json.JSONDecodeError):
            pass
        return messages

    def clear_conversation(self) -> None:
        """Clear the conversation log (e.g. on restart)."""
        conv_file = self.state_dir / "conversation.jsonl"
        if conv_file.exists():
            conv_file.unlink()

    def get_provider_overrides(self) -> Dict[str, str]:
        """Get per-project provider overrides.

        Returns:
            Dict mapping agent name -> provider name. Empty dict means use global defaults.
        """
        state = self.load_state()
        return state.get('provider_overrides', {})

    def set_provider_overrides(self, overrides: Dict[str, str]) -> None:
        """Set per-project provider overrides.

        Args:
            overrides: Dict mapping agent name -> provider name. Pass {} to clear.
        """
        state = self.load_state()
        state['provider_overrides'] = overrides
        self.save_state(state)

    def _default_state(self) -> Dict[str, Any]:
        """Create default project state.

        Returns:
            Default state dictionary
        """
        return {
            'name': self.project_path.name,
            'status': ProjectPhase.IDLE.value,
            'current_phase': ProjectPhase.IDLE.value,
            'iteration': 0,
            'max_iterations': 10,
            'quality_threshold': 8.0,
            'start_time': datetime.utcnow().isoformat() + 'Z',
            'last_update': datetime.utcnow().isoformat() + 'Z',
            'last_score': None,
            'provider': None,
            'model': None,
            'provider_overrides': {},
            'verification': {},
            'compression': {
                'enabled': True,
                'rate': 0.5,
                'preserve_code_blocks': True,
            }
        }

    def get_compression_config(self) -> Dict[str, Any]:
        """Get compression configuration for this project.

        Returns:
            Compression config dict with 'enabled', 'rate', 'preserve_code_blocks'.
        """
        state = self.load_state()
        return state.get('compression', {
            'enabled': True,
            'rate': 0.5,
            'preserve_code_blocks': True,
        })

    def set_compression_config(self, config: Dict[str, Any]) -> None:
        """Set compression configuration for this project.

        Args:
            config: Dict with keys 'enabled', 'rate', 'preserve_code_blocks'.
        """
        state = self.load_state()
        defaults = {'enabled': True, 'rate': 0.5, 'preserve_code_blocks': True}
        current = state.get('compression', defaults)
        current.update(config)
        state['compression'] = current
        self.save_state(state)

    def get_verification_overrides(self) -> Dict[str, Any]:
        """Get per-project verification config overrides.

        Returns:
            Dict of override keys (timeout_build, memory_limit, etc.).
            Empty dict means use global defaults.
        """
        state = self.load_state()
        return state.get('verification', {})

    def get_project_name(self) -> str:
        """Get project name from path.

        Returns:
            Project directory name
        """
        return self.project_path.name
