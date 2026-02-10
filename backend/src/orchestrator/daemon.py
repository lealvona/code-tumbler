"""Orchestrator Daemon - Coordinates the code tumbling cycle.

Watches the file system for trigger files and orchestrates the Architect, Engineer,
and Verifier agents through iterative refinement cycles.
"""

import asyncio
import json
import logging
import sys
import time
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
import threading

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

try:
    import psutil
except ImportError:
    psutil = None

try:
    from .state_manager import StateManager, ProjectPhase
    from ..agents import ArchitectAgent, EngineerAgent, VerifierAgent
    from ..utils.logger import get_logger
except ImportError:
    from orchestrator.state_manager import StateManager, ProjectPhase
    from agents import ArchitectAgent, EngineerAgent, VerifierAgent
    from utils.logger import get_logger


class ResourceAwareQueue:
    """Queue that schedules jobs based on system resource availability."""

    def __init__(self, orchestrator: 'Orchestrator', max_workers: int = 2, cpu_threshold: float = 85.0, memory_threshold: float = 90.0):
        self.orchestrator = orchestrator
        self.queue: queue.Queue = queue.Queue()
        self.max_workers = max_workers
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.running = False
        self.workers: List[threading.Thread] = []
        self.logger = get_logger("orchestrator.queue")

        # Initialize cpu_percent
        if psutil:
            psutil.cpu_percent(interval=None)
            self.logger.info("Resource monitoring enabled (psutil detected)")
        else:
            self.logger.warning("Resource monitoring disabled (psutil not found)")

    def start(self):
        """Start worker threads."""
        self.running = True
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            t.start()
            self.workers.append(t)
        self.logger.info(f"ResourceAwareQueue started with {self.max_workers} workers (thresholds: CPU>{self.cpu_threshold}%, MEM>{self.memory_threshold}%)")

    def stop(self):
        """Stop worker threads."""
        self.running = False
        # We don't join here because they are daemon threads, 
        # but we could signal them if needed.

    def put(self, file_path: Path):
        """Add a file path to the processing queue."""
        self.queue.put(file_path)

    def _worker_loop(self, worker_id: int):
        while self.running:
            try:
                # Get item with timeout to allow checking self.running
                file_path = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            # Check resources before starting
            wait_count = 0
            while self.running:
                if self._check_resources(worker_id, log_wait=(wait_count % 6 == 0)): # Log every ~30s
                    break
                wait_count += 1
                time.sleep(5)  # Wait for resources to free up

            if not self.running:
                break

            try:
                self.orchestrator.handle_trigger(file_path)
            except Exception as e:
                self.logger.error(f"Worker {worker_id}: Error processing {file_path}: {e}")
            finally:
                self.queue.task_done()

    def _check_resources(self, worker_id: int, log_wait: bool = True) -> bool:
        """Check if system resources are available."""
        if not psutil:
            return True

        # Check CPU (blocking for 0.1s to get accurate reading)
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent

        if cpu > self.cpu_threshold or mem > self.memory_threshold:
            if log_wait:
                self.logger.warning(
                    f"Worker {worker_id}: System busy (CPU: {cpu:.1f}%, Mem: {mem:.1f}%). "
                    f"Waiting for load to drop below {self.cpu_threshold}%/{self.memory_threshold}%..."
                )
            return False
        
        return True


class ProjectEventHandler(FileSystemEventHandler):
    """Handles file system events for project directories."""

    def __init__(self, orchestrator: 'Orchestrator'):
        """Initialize event handler.

        Args:
            orchestrator: Reference to the orchestrator instance
        """
        super().__init__()
        self.orchestrator = orchestrator
        self.logger = get_logger("orchestrator.events")

        # Debouncing: track last event time per file
        self._last_events: Dict[str, float] = {}
        self._debounce_seconds = 3.0  # Wait 3 seconds before processing

    def on_created(self, event: FileSystemEvent):
        """Handle file creation events.

        Args:
            event: File system event
        """
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Check if this is a trigger file
        if self._is_trigger_file(file_path) and self._should_process(file_path):
            self.logger.info(f"Trigger file detected: {file_path}")
            self._schedule_processing(file_path)

    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events.

        Args:
            event: File system event
        """
        # Treat modifications like creations for trigger files
        self.on_created(event)

    def _is_trigger_file(self, file_path: Path) -> bool:
        """Check if a file is a trigger file.

        Args:
            file_path: Path to check

        Returns:
            True if file triggers an agent
        """
        # Use Path.as_posix() for cross-platform normalized paths
        file_str = file_path.as_posix()

        # Trigger patterns (using forward slashes - normalized on all platforms)
        triggers = [
            '/01_input/requirements.txt',
            '/02_plan/PLAN.md',
            '/03_staging/.manifest.json',
        ]

        return any(trigger in file_str for trigger in triggers)

    def _should_process(self, file_path: Path) -> bool:
        """Check if enough time has passed since last event (debouncing).

        Args:
            file_path: Path that triggered event

        Returns:
            True if event should be processed
        """
        now = time.time()
        file_key = str(file_path)

        last_time = self._last_events.get(file_key, 0)
        if now - last_time < self._debounce_seconds:
            return False  # Too soon, ignore

        self._last_events[file_key] = now
        return True

    def _schedule_processing(self, file_path: Path):
        """Schedule processing of trigger file via the resource-aware queue.

        Args:
            file_path: Trigger file path
        """
        self.orchestrator.job_queue.put(file_path)



class Orchestrator:
    """Main orchestrator daemon that coordinates the tumbling cycle."""

    def __init__(
        self,
        workspace_root: Path,
        architect: ArchitectAgent,
        engineer: EngineerAgent,
        verifier: VerifierAgent,
        quality_threshold: float = 8.0,
        max_iterations: int = 10,
        max_cost_per_project: float = 0.0,
    ):
        """Initialize the orchestrator.

        Args:
            workspace_root: Root directory containing project folders
            architect: Architect agent instance
            engineer: Engineer agent instance
            verifier: Verifier agent instance
            quality_threshold: Minimum score to finalize project
            max_iterations: Maximum refinement iterations
            max_cost_per_project: Max cost in dollars (0 = unlimited)
        """
        self.workspace_root = Path(workspace_root)
        self.architect = architect
        self.engineer = engineer
        self.verifier = verifier
        self.quality_threshold = quality_threshold
        self.max_iterations = max_iterations
        self.max_cost_per_project = max_cost_per_project

        self.logger = get_logger("orchestrator")
        self.observer: Optional[Observer] = None

        # Currently processing projects (prevent concurrent runs)
        self._processing_lock = threading.Lock()
        self._processing_projects: set = set()
        
        # Initialize resource-aware job queue
        self.job_queue = ResourceAwareQueue(self)

    def start(self):
        """Start the orchestrator daemon."""
        self.logger.info("=" * 60)
        self.logger.info("Code Tumbler - Orchestrator Daemon")
        self.logger.info("=" * 60)
        self.logger.info(f"Workspace: {self.workspace_root}")
        self.logger.info(f"Quality threshold: {self.quality_threshold}/10")
        self.logger.info(f"Max iterations: {self.max_iterations}")
        self.logger.info("")

        # Start job queue workers
        self.job_queue.start()

        # Ensure workspace exists
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        # Set up file watcher
        event_handler = ProjectEventHandler(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.workspace_root), recursive=True)
        self.observer.start()

        self.logger.info("File watcher started. Monitoring for new projects...")
        self.logger.info("Press Ctrl+C to stop")
        self.logger.info("")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("\nShutting down...")
            self.stop()

    def stop(self):
        """Stop the orchestrator daemon."""
        if self.job_queue:
            self.job_queue.stop()
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self.logger.info("Orchestrator stopped")

    def handle_trigger(self, trigger_file: Path):
        """Handle a trigger file event.

        Args:
            trigger_file: Path to the trigger file
        """
        # Determine project root (go up to find project directory)
        project_path = self._find_project_root(trigger_file)
        if not project_path:
            self.logger.error(f"Could not determine project root for: {trigger_file}")
            return

        project_name = project_path.name

        # Prevent concurrent processing of same project
        with self._processing_lock:
            if project_name in self._processing_projects:
                self.logger.info(f"Project {project_name} already processing, skipping")
                return
            self._processing_projects.add(project_name)

        try:
            # Wait for file to stabilize (file may still be written)
            time.sleep(1)

            # Load state
            state_mgr = StateManager(project_path)
            state = state_mgr.load_state()

            self.logger.info(f"\n{'=' * 60}")
            self.logger.info(f"Project: {project_name}")
            self.logger.info(f"Trigger: {trigger_file.name}")
            self.logger.info(f"Current iteration: {state.get('iteration', 0)}")
            self.logger.info(f"{'=' * 60}\n")

            # Determine which phase to execute based on trigger file
            # Use as_posix() for cross-platform path normalization
            trigger_str = trigger_file.as_posix()

            if '/01_input/requirements.txt' in trigger_str:
                self._run_architect(project_path, state_mgr)
            elif '/02_plan/PLAN.md' in trigger_str:
                self._run_engineer(project_path, state_mgr)
            elif '/03_staging/.manifest.json' in trigger_str:
                self._run_verifier(project_path, state_mgr)
                self._evaluate_and_loop(project_path, state_mgr)

        except Exception as e:
            self.logger.error(f"Error processing {project_name}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

            # Mark as failed
            state_mgr = StateManager(project_path)
            state_mgr.mark_failed(str(e))

        finally:
            # Remove from processing set
            with self._processing_lock:
                self._processing_projects.discard(project_name)

    def _run_architect(self, project_path: Path, state_mgr: StateManager):
        """Run the Architect agent.

        Args:
            project_path: Project root directory
            state_mgr: State manager instance
        """
        self.logger.info("Phase: ARCHITECT - Creating plan")
        state_mgr.update_phase(ProjectPhase.PLANNING)

        # Read requirements
        requirements_file = project_path / "01_input" / "requirements.txt"
        if not requirements_file.exists():
            raise FileNotFoundError(f"Requirements file not found: {requirements_file}")

        requirements = requirements_file.read_text(encoding='utf-8')

        # Generate plan
        plan_file = project_path / "02_plan" / "PLAN.md"
        compression_config = state_mgr.get_compression_config()
        plan = self.architect.plan_project(
            requirements=requirements,
            project_name=project_path.name,
            output_path=plan_file,
            temperature=0.3,
            compression_config=compression_config,
        )

        # Log usage
        usage = self.architect.get_total_usage()
        state_mgr.log_usage(
            agent='architect',
            input_tokens=usage['total_input_tokens'],
            output_tokens=usage['total_output_tokens'],
            cost=usage['total_cost'],
            compression_metrics=self.architect.last_compression_metrics or None,
        )

        self.logger.info(f"Plan created: {len(plan)} characters")
        self.logger.info(f"Saved to: {plan_file}")
        self.logger.info("Waiting for Engineer to start...")

    def _run_engineer(self, project_path: Path, state_mgr: StateManager):
        """Run the Engineer agent.

        Args:
            project_path: Project root directory
            state_mgr: State manager instance
        """
        iteration = state_mgr.increment_iteration()

        self.logger.info(f"Phase: ENGINEER - Generating code (iteration {iteration})")
        state_mgr.update_phase(ProjectPhase.ENGINEERING)

        # Read plan
        plan_file = project_path / "02_plan" / "PLAN.md"
        if not plan_file.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_file}")

        plan = plan_file.read_text(encoding='utf-8')

        # Read feedback if this is a refinement iteration
        feedback = ""
        previous_code = None

        if iteration > 1:
            feedback_file = project_path / "04_feedback" / f"REPORT_iter{iteration - 1}.md"
            if feedback_file.exists():
                feedback = feedback_file.read_text(encoding='utf-8').strip()
                if feedback:
                    self.logger.info(f"Loaded feedback from iteration {iteration - 1}")
                else:
                    self.logger.warning(
                        f"Feedback file for iteration {iteration - 1} is empty — "
                        f"providing fallback guidance"
                    )

            if not feedback:
                # Empty or missing report — provide actionable fallback so
                # the engineer doesn't regenerate identical code.
                feedback = (
                    f"The verifier report for iteration {iteration - 1} was "
                    f"empty or unavailable. Improve the code by:\n"
                    f"1. Ensure all planned files are complete and functional\n"
                    f"2. Add error handling and input validation\n"
                    f"3. Include at least basic tests\n"
                    f"4. Fix any obvious bugs or missing imports"
                )

            # Load previous code content for refinement context
            staging_dir = project_path / "03_staging"
            if staging_dir.exists():
                previous_code = {}
                skip_ext = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.bin',
                            '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff',
                            '.woff2', '.ttf', '.eot', '.zip', '.tar', '.gz'}
                max_file_size = 50_000  # 50KB per file

                for file_path in staging_dir.rglob('*'):
                    if file_path.is_file() and file_path.name != '.manifest.json':
                        if file_path.suffix.lower() in skip_ext:
                            continue
                        rel_path = file_path.relative_to(staging_dir)
                        try:
                            if file_path.stat().st_size <= max_file_size:
                                previous_code[str(rel_path)] = file_path.read_text(encoding='utf-8')
                            else:
                                previous_code[str(rel_path)] = f"[File too large: {file_path.stat().st_size} bytes]"
                        except (UnicodeDecodeError, OSError):
                            previous_code[str(rel_path)] = "[Binary or unreadable file]"

        # Generate code
        staging_dir = project_path / "03_staging"
        compression_config = state_mgr.get_compression_config()
        files = self.engineer.generate_code(
            plan=plan,
            iteration=iteration,
            feedback=feedback,
            previous_code=previous_code,
            output_dir=staging_dir,
            temperature=0.3,
            compression_config=compression_config,
        )

        # Log usage
        usage = self.engineer.get_total_usage()
        state_mgr.log_usage(
            agent='engineer',
            input_tokens=usage['total_input_tokens'],
            output_tokens=usage['total_output_tokens'],
            cost=usage['total_cost'],
            compression_metrics=self.engineer.last_compression_metrics or None,
        )

        self.logger.info(f"Code generated: {len(files)} files")
        self.logger.info("Waiting for Verifier to start...")

    def _run_engineer_parallel(self, project_path: Path, state_mgr: StateManager):
        """Parallel map-reduce engineer execution.

        Extracts the file manifest from the plan, chunks files into groups,
        and fans out generation across concurrent async_chat calls using the
        provider's async infrastructure. Each chunk generates a subset of
        files; results are merged into the staging directory.

        Falls back to sequential _run_engineer if:
        - The provider doesn't support async_chat
        - The plan has too few files to benefit from parallelism
        - The async event loop cannot be created
        """
        # Check if provider supports async
        if not hasattr(self.engineer.provider, 'async_chat'):
            self.logger.info("Provider lacks async_chat — falling back to sequential engineer")
            return self._run_engineer(project_path, state_mgr)

        iteration = state_mgr.increment_iteration()
        self.logger.info(f"Phase: ENGINEER (parallel) - Generating code (iteration {iteration})")
        state_mgr.update_phase(ProjectPhase.ENGINEERING)

        # Read plan
        plan_file = project_path / "02_plan" / "PLAN.md"
        if not plan_file.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_file}")
        plan = plan_file.read_text(encoding='utf-8')

        # Extract file manifest from plan
        planned_files = self.engineer._extract_planned_files(plan)
        if len(planned_files) < 4:
            self.logger.info(
                f"Only {len(planned_files)} planned files — using sequential generation"
            )
            # Undo the iteration increment since _run_engineer will do it again
            # (we can't easily undo, so just proceed sequentially from here)
            return self._run_engineer_sequential_body(
                project_path, state_mgr, iteration, plan
            )

        # Read feedback and previous code (same logic as _run_engineer)
        feedback = ""
        previous_code = None
        if iteration > 1:
            feedback_file = project_path / "04_feedback" / f"REPORT_iter{iteration - 1}.md"
            if feedback_file.exists():
                feedback = feedback_file.read_text(encoding='utf-8').strip()
            if not feedback:
                feedback = (
                    f"The verifier report for iteration {iteration - 1} was "
                    f"empty or unavailable. Improve the code by:\n"
                    f"1. Ensure all planned files are complete and functional\n"
                    f"2. Add error handling and input validation\n"
                    f"3. Include at least basic tests\n"
                    f"4. Fix any obvious bugs or missing imports"
                )

            staging_dir = project_path / "03_staging"
            if staging_dir.exists():
                previous_code = {}
                skip_ext = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.bin',
                            '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff',
                            '.woff2', '.ttf', '.eot', '.zip', '.tar', '.gz'}
                max_file_size = 50_000
                for file_path in staging_dir.rglob('*'):
                    if file_path.is_file() and file_path.name != '.manifest.json':
                        if file_path.suffix.lower() in skip_ext:
                            continue
                        rel_path = file_path.relative_to(staging_dir)
                        try:
                            if file_path.stat().st_size <= max_file_size:
                                previous_code[str(rel_path)] = file_path.read_text(encoding='utf-8')
                            else:
                                previous_code[str(rel_path)] = f"[File too large: {file_path.stat().st_size} bytes]"
                        except (UnicodeDecodeError, OSError):
                            previous_code[str(rel_path)] = "[Binary or unreadable file]"

        # Chunk files into groups (~3-5 files per chunk)
        chunk_size = max(2, len(planned_files) // 5)
        chunks: List[List[str]] = []
        for i in range(0, len(planned_files), chunk_size):
            chunks.append(planned_files[i:i + chunk_size])

        self.logger.info(
            f"Parallel generation: {len(planned_files)} files in "
            f"{len(chunks)} chunks (async)"
        )

        # Build messages for each chunk
        async def _generate_chunk(chunk_files: List[str], chunk_num: int) -> Dict[str, str]:
            """Generate a subset of files using async_chat."""
            context = {
                'plan': plan,
                'iteration': iteration,
                'feedback': feedback,
                'previous_code': {
                    k: v for k, v in (previous_code or {}).items()
                    if k in chunk_files
                },
                'chunk_info': {
                    'chunk_num': chunk_num,
                    'total_chunks': len(chunks),
                    'target_files': chunk_files,
                },
            }
            messages = self.engineer._build_messages(context)

            # Apply compression if configured
            compression_config = state_mgr.get_compression_config()
            if compression_config and compression_config.get('enabled'):
                messages, _ = self.engineer._apply_compression(messages, compression_config)

            # Apply nothink if needed
            if self.engineer._should_nothink():
                self.engineer._inject_nothink(messages)

            max_tokens = self.engineer._resolve_max_tokens(None)
            response = await self.engineer.provider.async_chat(
                messages, temperature=0.3, max_tokens=max_tokens
            )

            # Parse the response
            try:
                return self.engineer._parse_files_json(response)
            except (json.JSONDecodeError, ValueError) as e:
                self.logger.error(
                    f"Chunk {chunk_num}/{len(chunks)} parse failed: {e}"
                )
                return {}

        async def _run_all_chunks():
            """Fan out all chunks concurrently."""
            tasks = [
                _generate_chunk(chunk_files, idx + 1)
                for idx, chunk_files in enumerate(chunks)
            ]
            return await asyncio.gather(*tasks, return_exceptions=True)

        # Run the async event loop
        try:
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(_run_all_chunks())
        finally:
            # Clean up the provider's async client if it has one
            if hasattr(self.engineer.provider, 'close'):
                try:
                    loop.run_until_complete(self.engineer.provider.close())
                except Exception:
                    pass
            loop.close()

        # Merge results from all chunks
        all_files: Dict[str, str] = {}
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Chunk {idx + 1} raised exception: {result}")
                continue
            if isinstance(result, dict):
                all_files.update(result)
                self.logger.info(f"Chunk {idx + 1}/{len(chunks)}: {len(result)} files")

        if not all_files:
            self.logger.warning(
                "Parallel generation produced no files — falling back to sequential"
            )
            return self._run_engineer_sequential_body(
                project_path, state_mgr, iteration, plan
            )

        # Write files to staging
        staging_dir = project_path / "03_staging"
        self.engineer._write_files(all_files, staging_dir)

        # Save debug output
        debug_dir = project_path / ".tumbler" / "logs"
        debug_dir.mkdir(parents=True, exist_ok=True)

        # Log usage
        usage = self.engineer.get_total_usage()
        state_mgr.log_usage(
            agent='engineer',
            input_tokens=usage['total_input_tokens'],
            output_tokens=usage['total_output_tokens'],
            cost=usage['total_cost'],
            compression_metrics=self.engineer.last_compression_metrics or None,
        )

        self.logger.info(f"Code generated (parallel): {len(all_files)} files")
        self.logger.info("Waiting for Verifier to start...")

    def _run_engineer_sequential_body(
        self, project_path: Path, state_mgr: StateManager,
        iteration: int, plan: str
    ):
        """Shared sequential generation body used by both _run_engineer and
        _run_engineer_parallel (when falling back).

        Unlike _run_engineer, this does NOT call increment_iteration() since
        the caller has already done so.
        """
        feedback = ""
        previous_code = None

        if iteration > 1:
            feedback_file = project_path / "04_feedback" / f"REPORT_iter{iteration - 1}.md"
            if feedback_file.exists():
                feedback = feedback_file.read_text(encoding='utf-8').strip()
            if not feedback:
                feedback = (
                    f"The verifier report for iteration {iteration - 1} was "
                    f"empty or unavailable. Improve the code by:\n"
                    f"1. Ensure all planned files are complete and functional\n"
                    f"2. Add error handling and input validation\n"
                    f"3. Include at least basic tests\n"
                    f"4. Fix any obvious bugs or missing imports"
                )

            staging_dir = project_path / "03_staging"
            if staging_dir.exists():
                previous_code = {}
                skip_ext = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.bin',
                            '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff',
                            '.woff2', '.ttf', '.eot', '.zip', '.tar', '.gz'}
                max_file_size = 50_000
                for file_path in staging_dir.rglob('*'):
                    if file_path.is_file() and file_path.name != '.manifest.json':
                        if file_path.suffix.lower() in skip_ext:
                            continue
                        rel_path = file_path.relative_to(staging_dir)
                        try:
                            if file_path.stat().st_size <= max_file_size:
                                previous_code[str(rel_path)] = file_path.read_text(encoding='utf-8')
                            else:
                                previous_code[str(rel_path)] = f"[File too large: {file_path.stat().st_size} bytes]"
                        except (UnicodeDecodeError, OSError):
                            previous_code[str(rel_path)] = "[Binary or unreadable file]"

        staging_dir = project_path / "03_staging"
        compression_config = state_mgr.get_compression_config()
        files = self.engineer.generate_code(
            plan=plan,
            iteration=iteration,
            feedback=feedback,
            previous_code=previous_code,
            output_dir=staging_dir,
            temperature=0.3,
            compression_config=compression_config,
        )

        usage = self.engineer.get_total_usage()
        state_mgr.log_usage(
            agent='engineer',
            input_tokens=usage['total_input_tokens'],
            output_tokens=usage['total_output_tokens'],
            cost=usage['total_cost'],
            compression_metrics=self.engineer.last_compression_metrics or None,
        )

        self.logger.info(f"Code generated: {len(files)} files")
        self.logger.info("Waiting for Verifier to start...")

    def _run_verifier(self, project_path: Path, state_mgr: StateManager):
        """Run the Verifier agent.

        Args:
            project_path: Project root directory
            state_mgr: State manager instance
        """
        iteration = state_mgr.get_iteration()

        self.logger.info(f"Phase: VERIFIER - Validating code (iteration {iteration})")
        state_mgr.update_phase(ProjectPhase.VERIFYING)

        # Read plan
        plan_file = project_path / "02_plan" / "PLAN.md"
        plan = plan_file.read_text(encoding='utf-8')

        # Verify code
        staging_dir = project_path / "03_staging"
        report_file = project_path / "04_feedback" / f"REPORT_iter{iteration}.md"

        compression_config = state_mgr.get_compression_config()
        report, score = self.verifier.verify(
            plan=plan,
            project_path=staging_dir,
            iteration=iteration,
            output_path=report_file,
            temperature=0.3,
            compression_config=compression_config,
        )

        # Save score
        state_mgr.set_score(score)

        # Log usage
        usage = self.verifier.get_total_usage()
        state_mgr.log_usage(
            agent='verifier',
            input_tokens=usage['total_input_tokens'],
            output_tokens=usage['total_output_tokens'],
            cost=usage['total_cost'],
            compression_metrics=self.verifier.last_compression_metrics or None,
        )

        self.logger.info(f"Verification complete - Score: {score}/10")
        self.logger.info("Waiting for evaluation...")

    def _check_cost_limit(self, project_path: Path, state_mgr: StateManager) -> bool:
        """Check if project has exceeded its cost budget.

        Returns:
            True if cost limit was exceeded and project was stopped.
        """
        if self.max_cost_per_project <= 0:
            return False

        total_cost = state_mgr.get_total_cost()
        if total_cost >= self.max_cost_per_project:
            self.logger.warning(
                f"Cost limit exceeded for {project_path.name}: "
                f"${total_cost:.4f} >= ${self.max_cost_per_project:.2f}"
            )
            state_mgr.mark_failed(
                f"Cost limit exceeded: ${total_cost:.4f} >= ${self.max_cost_per_project:.2f}"
            )
            return True
        return False

    def _evaluate_and_loop(self, project_path: Path, state_mgr: StateManager):
        """Evaluate quality score and decide whether to loop or finalize.

        Args:
            project_path: Project root directory
            state_mgr: State manager instance
        """
        iteration = state_mgr.get_iteration()
        score = state_mgr.get_score() or 0.0

        self.logger.info(f"\nEvaluation - Iteration {iteration}")
        self.logger.info(f"Score: {score}/10 (threshold: {self.quality_threshold}/10)")

        # Check cost budget before continuing
        if self._check_cost_limit(project_path, state_mgr):
            return

        if state_mgr.is_complete(self.quality_threshold, self.max_iterations):
            # Finalize project
            if score >= self.quality_threshold:
                self.logger.info(f"✓ Quality threshold met! Finalizing project...")
            else:
                self.logger.info(f"⚠ Max iterations ({self.max_iterations}) reached. Finalizing...")

            self._finalize_project(project_path, state_mgr)
        else:
            # Loop: Engineer will pick up the feedback automatically
            self.logger.info(f"⚠ Score below threshold. Triggering iteration {iteration + 1}...")
            self.logger.info("Engineer will receive feedback for refinement.\n")

            # Trigger Engineer by touching the PLAN.md file
            # (This simulates user editing, triggers file watcher)
            plan_file = project_path / "02_plan" / "PLAN.md"
            plan_file.touch()

    def _finalize_project(self, project_path: Path, state_mgr: StateManager):
        """Finalize a completed project.

        Args:
            project_path: Project root directory
            state_mgr: State manager instance
        """
        import shutil
        from datetime import datetime

        self.logger.info("Finalizing project...")

        # Update state
        state_mgr.update_phase(ProjectPhase.COMPLETED)

        # Archive to 05_final
        final_dir = project_path / "05_final"
        final_dir.mkdir(parents=True, exist_ok=True)

        staging_dir = project_path / "03_staging"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{project_path.name}_{timestamp}"

        # Create zip archive
        archive_path = final_dir / f"{archive_name}.zip"
        shutil.make_archive(
            str(final_dir / archive_name),
            'zip',
            staging_dir
        )

        self.logger.info(f"✓ Project archived to: {archive_path}")

        # Show summary
        state = state_mgr.load_state()

        self.logger.info("\n" + "=" * 60)
        self.logger.info("PROJECT COMPLETE")
        self.logger.info("=" * 60)
        self.logger.info(f"Project: {project_path.name}")
        self.logger.info(f"Iterations: {state.get('iteration', 0)}")
        self.logger.info(f"Final score: {state.get('last_score', 0)}/10")
        self.logger.info(f"Archive: {archive_path}")
        self.logger.info("=" * 60 + "\n")

    def _find_project_root(self, file_path: Path) -> Optional[Path]:
        """Find the project root directory from a file path.

        Args:
            file_path: Path to a file within the project

        Returns:
            Project root path, or None if not found
        """
        # Project root is the parent of 01_input, 02_plan, 03_staging, etc.
        current = file_path.parent

        while current != self.workspace_root and current.parent != current:
            # Check if this directory contains project structure
            if (current / "01_input").exists() or (current / ".tumbler").exists():
                return current
            current = current.parent

        return None
