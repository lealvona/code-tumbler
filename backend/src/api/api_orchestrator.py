"""API-aware Orchestrator that emits SSE events during processing."""

import dataclasses
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from orchestrator.daemon import Orchestrator
from orchestrator.state_manager import StateManager
from api.event_bus import EventBus
from utils.config import Config, VerificationConfig, resolve_agent_provider
from utils.plan_parser import extract_resource_requirements
from utils.provider_factory import create_provider
from agents.base_agent import DegenerateOutputError


class APIOrchestrator(Orchestrator):
    """Orchestrator subclass that publishes SSE events during the tumbling cycle."""

    def __init__(self, event_bus: EventBus, config: Config = None, **kwargs):
        super().__init__(**kwargs)
        self.event_bus = event_bus
        self._config = config
        self._stopped = False

    def _refresh_providers(self, state_mgr: StateManager) -> None:
        """Re-resolve providers from global config + project overrides.

        Called at the top of each iteration so that mid-run provider
        changes take effect on the next phase.
        """
        if self._config is None:
            return
        overrides = state_mgr.get_provider_overrides()
        for agent_name, agent_obj in [
            ("architect", self.architect),
            ("engineer", self.engineer),
            ("verifier", self.verifier),
        ]:
            provider_config = resolve_agent_provider(self._config, agent_name, overrides)
            current_name = getattr(agent_obj.provider, '_resolved_name', None)
            if current_name != provider_config.name:
                new_provider = create_provider(provider_config)
                new_provider._resolved_name = provider_config.name
                agent_obj.set_provider(new_provider)

    def stop(self):
        """Stop the orchestrator."""
        self._stopped = True
        super().stop()

    def _publish_conversation_update(self, project_path: Path, agent: str):
        """Notify SSE subscribers that a new conversation message was added."""
        self.event_bus.publish("conversation_update", {
            "project": project_path.name,
            "agent": agent,
        })

    def _publish_thinking(self, project_path: Path, agent: str):
        """Notify SSE subscribers that an agent is thinking."""
        self.event_bus.publish("agent_thinking", {
            "project": project_path.name,
            "agent": agent,
        })

    def _make_chunk_callback(self, project_path: Path, agent: str):
        """Create a callback that batches streaming chunks before publishing via SSE.

        Accumulates tokens and publishes at most every 200ms or 200 chars,
        whichever comes first. This prevents flooding the SSE event bus
        with per-token events (which overwhelms the asyncio queue and
        the frontend event store).

        Also keeps a full transcript of all chunks so the complete LLM
        response can be persisted to conversation.jsonl after the agent
        finishes.
        """
        import time
        buf = []
        buf_chars = [0]
        last_flush = [time.monotonic()]
        full_content = []  # accumulate entire response for persistence

        def flush():
            if buf:
                combined = "".join(buf)
                buf.clear()
                buf_chars[0] = 0
                last_flush[0] = time.monotonic()
                self.event_bus.publish("conversation_chunk", {
                    "project": project_path.name,
                    "agent": agent,
                    "chunk": combined,
                })

        def on_chunk(chunk: str):
            buf.append(chunk)
            full_content.append(chunk)
            buf_chars[0] += len(chunk)
            now = time.monotonic()
            if buf_chars[0] >= 200 or (now - last_flush[0]) >= 0.2:
                flush()

        def get_full_content() -> str:
            return "".join(full_content)

        on_chunk._flush = flush
        on_chunk._get_full_content = get_full_content
        return on_chunk

    def _run_architect(self, project_path: Path, state_mgr: StateManager):
        self.event_bus.publish("phase_change", {
            "project": project_path.name,
            "phase": "planning",
        })
        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": "Architect agent started - creating plan...",
            "level": "info",
        })

        # Log the input (requirements)
        req_file = project_path / "01_input" / "requirements.txt"
        if req_file.exists():
            state_mgr.log_conversation(
                agent="system", role="input", iteration=0,
                content=req_file.read_text(encoding="utf-8"),
                metadata={"label": "Project Requirements"},
            )
            self._publish_conversation_update(project_path, "system")

        self._publish_thinking(project_path, "architect")

        chunk_cb = self._make_chunk_callback(project_path, "architect")
        self.architect._on_chunk = chunk_cb
        try:
            super()._run_architect(project_path, state_mgr)
        except Exception as e:
            state_mgr.log_conversation(
                agent="architect", role="error", iteration=0,
                content=f"Architect agent failed: {e}",
                metadata={"label": "Error"},
            )
            self._publish_conversation_update(project_path, "architect")
            raise
        finally:
            chunk_cb._flush()
            self.architect._on_chunk = None

        # Persist the full LLM response so it survives page refresh
        llm_response = chunk_cb._get_full_content()
        if not llm_response:
            plan_file = project_path / "02_plan" / "PLAN.md"
            if plan_file.exists():
                llm_response = plan_file.read_text(encoding="utf-8")
        if llm_response:
            state_mgr.log_conversation(
                agent="architect", role="output", iteration=0,
                content=llm_response,
                metadata={"label": "Architectural Plan"},
            )
            self._publish_conversation_update(project_path, "architect")

        # Extract resource recommendations from plan and store as overrides
        plan_file = project_path / "02_plan" / "PLAN.md"
        if plan_file.exists():
            plan_text = plan_file.read_text(encoding="utf-8")
            resource_recs = extract_resource_requirements(plan_text)
            if resource_recs:
                existing = state_mgr.get_verification_overrides()
                # Architect recommendations don't overwrite explicit user-set values
                merged = {**resource_recs, **existing}
                state = state_mgr.load_state()
                state["verification"] = merged
                state_mgr.save_state(state)
                self.event_bus.publish("log", {
                    "project": project_path.name,
                    "message": f"Architect recommended sandbox resources: {resource_recs}",
                    "level": "info",
                })

        state = state_mgr.load_state()
        self.event_bus.publish("phase_change", {
            "project": project_path.name,
            "phase": "planning_complete",
            "iteration": state.get("iteration", 0),
        })
        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": "Architect agent completed - plan created",
            "level": "info",
        })

    def _run_engineer(self, project_path: Path, state_mgr: StateManager):
        iteration = state_mgr.get_iteration() + 1  # Will be incremented inside
        self.event_bus.publish("phase_change", {
            "project": project_path.name,
            "phase": "engineering",
            "iteration": iteration,
        })
        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": f"Engineer agent started - iteration {iteration}",
            "level": "info",
        })

        # Log feedback input if this is a refinement iteration
        if iteration > 1:
            feedback_file = project_path / "04_feedback" / f"REPORT_iter{iteration - 1}.md"
            if feedback_file.exists():
                state_mgr.log_conversation(
                    agent="system", role="input", iteration=iteration,
                    content=feedback_file.read_text(encoding="utf-8"),
                    metadata={"label": f"Feedback from iteration {iteration - 1}"},
                )
                self._publish_conversation_update(project_path, "system")

        # Log start message so the user sees engineer activity immediately
        state_mgr.log_conversation(
            agent="engineer", role="status", iteration=iteration,
            content=f"Starting code generation for iteration {iteration}...",
            metadata={"label": "Engineer Started"},
        )
        self._publish_conversation_update(project_path, "engineer")
        self._publish_thinking(project_path, "engineer")

        chunk_cb = self._make_chunk_callback(project_path, "engineer")
        self.engineer._on_chunk = chunk_cb
        try:
            super()._run_engineer(project_path, state_mgr)
        except Exception as e:
            state_mgr.log_conversation(
                agent="engineer", role="error", iteration=iteration,
                content=f"Engineer agent failed: {e}",
                metadata={"label": "Error"},
            )
            self._publish_conversation_update(project_path, "engineer")
            raise
        finally:
            chunk_cb._flush()
            self.engineer._on_chunk = None

        # Persist the full LLM response so it survives page refresh
        llm_response = chunk_cb._get_full_content()
        staging_dir = project_path / "03_staging"
        file_count = 0
        if staging_dir.exists():
            file_count = sum(
                1 for f in staging_dir.rglob("*")
                if f.is_file() and f.name != ".manifest.json"
            )
        if llm_response:
            state_mgr.log_conversation(
                agent="engineer", role="output", iteration=iteration,
                content=llm_response,
                metadata={"label": "Code Generation", "file_count": file_count},
            )
            self._publish_conversation_update(project_path, "engineer")

        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": f"Engineer agent completed - code generated",
            "level": "info",
        })

    def _start_heartbeat(self, project_path: Path, interval: float = 5.0) -> threading.Event:
        """Start a daemon thread emitting SSE heartbeat events.

        Prevents SSE/proxy timeouts during long sandbox verification runs.
        Returns a threading.Event that, when set, stops the heartbeat thread.
        """
        stop_event = threading.Event()

        def _heartbeat_loop():
            while not stop_event.is_set():
                self.event_bus.publish("heartbeat", {
                    "project": project_path.name,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                stop_event.wait(interval)

        t = threading.Thread(target=_heartbeat_loop, daemon=True)
        t.start()
        return stop_event

    def _run_verifier(self, project_path: Path, state_mgr: StateManager):
        from orchestrator.daemon import ProjectPhase

        iteration = state_mgr.get_iteration()
        self.event_bus.publish("phase_change", {
            "project": project_path.name,
            "phase": "verifying",
            "iteration": iteration,
        })
        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": "Verifier agent started - validating code",
            "level": "info",
        })

        # Log start message so the user sees verifier activity immediately
        state_mgr.log_conversation(
            agent="verifier", role="status", iteration=iteration,
            content=f"Verifying code from iteration {iteration}...",
            metadata={"label": "Verifier Started"},
        )
        self._publish_conversation_update(project_path, "verifier")
        self._publish_thinking(project_path, "verifier")

        # Sandbox phase callback — publishes SSE events and persists to conversation
        def _on_sandbox_phase(phase_name: str, phase_data: dict):
            self.event_bus.publish("sandbox_phase", {
                "project": project_path.name,
                "phase": phase_name,
                "iteration": iteration,
                **phase_data,
            })
            state_mgr.log_conversation(
                agent="verifier", role="sandbox", iteration=iteration,
                content=phase_data.get("stdout", "")[:5000],
                metadata={
                    "label": f"Sandbox: {phase_name}",
                    "sandbox_phase": phase_name,
                    "sandbox_status": phase_data.get("status", "unknown"),
                    "exit_code": phase_data.get("exit_code", -1),
                    "duration_s": phase_data.get("duration_s", 0.0),
                    "commands": phase_data.get("commands", []),
                },
            )
            self._publish_conversation_update(project_path, "verifier")

        self.event_bus.publish("sandbox_start", {
            "project": project_path.name,
            "iteration": iteration,
        })

        chunk_cb = self._make_chunk_callback(project_path, "verifier")
        self.verifier._on_chunk = chunk_cb
        heartbeat_stop = self._start_heartbeat(project_path)
        try:
            # Inline the base daemon's _run_verifier logic so we can inject
            # the on_phase_complete callback for sandbox event publishing.
            self.logger.info(f"Phase: VERIFIER - Validating code (iteration {iteration})")
            state_mgr.update_phase(ProjectPhase.VERIFYING)

            plan_file = project_path / "02_plan" / "PLAN.md"
            plan = plan_file.read_text(encoding='utf-8')

            staging_dir = project_path / "03_staging"
            report_file = project_path / "04_feedback" / f"REPORT_iter{iteration}.md"

            # Merge per-project verification overrides with global config
            merged_vc = self._config.verification
            project_overrides = state_mgr.get_verification_overrides()
            if project_overrides:
                override_fields = {
                    k: v for k, v in project_overrides.items()
                    if k in {f.name for f in dataclasses.fields(VerificationConfig)}
                }
                if override_fields:
                    merged_vc = dataclasses.replace(merged_vc, **override_fields)

            compression_config = state_mgr.get_compression_config()
            report, score = self.verifier.verify(
                plan=plan,
                project_path=staging_dir,
                iteration=iteration,
                output_path=report_file,
                on_phase_complete=_on_sandbox_phase,
                verification_config=merged_vc,
                temperature=0.3,
                compression_config=compression_config,
            )

            state_mgr.set_score(score)

            usage = self.verifier.get_total_usage()
            state_mgr.log_usage(
                agent='verifier',
                input_tokens=usage['total_input_tokens'],
                output_tokens=usage['total_output_tokens'],
                cost=usage['total_cost'],
                compression_metrics=self.verifier.last_compression_metrics or None,
            )

            self.logger.info(f"Verification complete - Score: {score}/10")
        except Exception as e:
            state_mgr.log_conversation(
                agent="verifier", role="error", iteration=iteration,
                content=f"Verifier agent failed: {e}",
                metadata={"label": "Error"},
            )
            self._publish_conversation_update(project_path, "verifier")
            raise
        finally:
            heartbeat_stop.set()
            chunk_cb._flush()
            self.verifier._on_chunk = None

        score = state_mgr.get_score()

        # Persist the full LLM response so it survives page refresh
        llm_response = chunk_cb._get_full_content()
        if not llm_response:
            report_file = project_path / "04_feedback" / f"REPORT_iter{iteration}.md"
            if report_file.exists():
                llm_response = report_file.read_text(encoding="utf-8")
        if llm_response:
            state_mgr.log_conversation(
                agent="verifier", role="output", iteration=iteration,
                content=llm_response,
                metadata={"label": "Verification Report", "score": score},
            )
            self._publish_conversation_update(project_path, "verifier")

        self.event_bus.publish("score_update", {
            "project": project_path.name,
            "score": score,
            "iteration": iteration,
        })
        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": f"Verifier completed - score: {score}/10",
            "level": "info",
        })

    def _evaluate_and_loop(self, project_path: Path, state_mgr: StateManager):
        super()._evaluate_and_loop(project_path, state_mgr)
        state = state_mgr.load_state()
        status = state.get("status", "idle")
        if status == "completed":
            self.event_bus.publish("project_complete", {
                "project": project_path.name,
                "score": state.get("last_score"),
                "iteration": state.get("iteration"),
            })
        elif status == "failed":
            error = state.get("error", "")
            self.event_bus.publish("project_failed", {
                "project": project_path.name,
                "error": error,
            })
            if "Cost limit exceeded" in error:
                self.event_bus.publish("cost_limit_reached", {
                    "project": project_path.name,
                    "cost": state_mgr.get_total_cost(),
                    "limit": self.max_cost_per_project,
                })

    def _setup_project_logger(self, project_path: Path) -> logging.FileHandler:
        """Add a file handler that writes to the project's .tumbler/logs/ directory."""
        log_dir = project_path / ".tumbler" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "run.log"

        handler = logging.FileHandler(str(log_file), encoding="utf-8", mode="a")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

        # Attach to root logger so all structlog stdlib output is captured
        root = logging.getLogger()
        root.addHandler(handler)
        # Ensure root logger level allows DEBUG messages through
        if root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        return handler

    def run_cycle(self, project_path: Path):
        """Run the full tumbling cycle for a project (called from API).

        This runs architect -> (engineer -> verifier -> evaluate)* synchronously
        in a background thread.

        If a plan already exists from a previous run, the architect phase is
        skipped and the cycle resumes from the engineer/verifier loop, preserving
        existing iteration count, conversation log, and usage data.
        """
        state_mgr = StateManager(project_path)

        # Determine if we can resume from a previous run
        plan_file = project_path / "02_plan" / "PLAN.md"
        has_plan = plan_file.exists() and plan_file.stat().st_size > 0
        prev_iteration = state_mgr.get_iteration()
        resuming = has_plan and prev_iteration > 0

        if resuming:
            # Resume: just clear the error/status but keep iteration, conversation, usage
            state = state_mgr.load_state()
            state['status'] = 'idle'
            state['current_phase'] = 'idle'
            state['error'] = None
            state_mgr.save_state(state)
            state_mgr.log_conversation(
                agent="system", role="input", iteration=prev_iteration,
                content=f"Resuming project from iteration {prev_iteration}.",
                metadata={"label": "Resume"},
            )
            self._publish_conversation_update(project_path, "system")
        else:
            # Fresh start: full reset
            state_mgr.reset_for_run()

        # Set up per-project log file
        log_handler = self._setup_project_logger(project_path)

        self.logger.info(
            f"{'Resuming' if resuming else 'Starting'} project: {project_path.name}"
            f" (iteration {prev_iteration})"
        )

        # Read requirements
        req_file = project_path / "01_input" / "requirements.txt"
        if not req_file.exists():
            state_mgr.mark_failed("Requirements file not found")
            self.event_bus.publish("project_failed", {
                "project": project_path.name,
                "error": "Requirements file not found",
            })
            logging.getLogger().removeHandler(log_handler)
            return

        try:
            # Phase 1: Architect (skip if resuming with existing plan)
            if not resuming:
                self._refresh_providers(state_mgr)
                self._run_architect(project_path, state_mgr)

            # Phase 2-3: Engineer -> Verifier loop
            score_history: list[float] = []
            consecutive_failures = 0
            max_consecutive_failures = 3
            plateau_window = 3  # stop if score unchanged for this many iterations

            while not self._stopped:
                self._refresh_providers(state_mgr)

                try:
                    self._run_engineer(project_path, state_mgr)
                    consecutive_failures = 0
                except DegenerateOutputError as e:
                    consecutive_failures += 1
                    iteration = state_mgr.get_iteration()
                    self.logger.warning(f"Degenerate output from engineer (attempt {consecutive_failures})")
                    state_mgr.log_conversation(
                        agent="engineer", role="error", iteration=iteration,
                        content=f"Engineer produced degenerate output: {e}",
                        metadata={"label": "Degenerate Output"},
                    )
                    self._publish_conversation_update(project_path, "engineer")
                    if consecutive_failures >= max_consecutive_failures:
                        raise ValueError(
                            f"Engineer produced degenerate output {consecutive_failures} "
                            f"times in a row. The model may not be suitable for this task."
                        )
                    continue  # retry the engineer without incrementing verifier

                if self._stopped:
                    break

                try:
                    self._run_verifier(project_path, state_mgr)
                except DegenerateOutputError as e:
                    iteration = state_mgr.get_iteration()
                    self.logger.warning("Degenerate output from verifier, using preliminary score")
                    state_mgr.log_conversation(
                        agent="verifier", role="error", iteration=iteration,
                        content=f"Verifier produced degenerate output: {e}. Using preliminary score.",
                        metadata={"label": "Degenerate Output"},
                    )
                    self._publish_conversation_update(project_path, "verifier")
                    # Fall through to evaluation with whatever score was set

                if self._stopped:
                    break

                # Evaluate
                score = state_mgr.get_score() or 0.0
                iteration = state_mgr.get_iteration()
                score_history.append(score)

                # Check cost budget
                if self._check_cost_limit(project_path, state_mgr):
                    self.event_bus.publish("cost_limit_reached", {
                        "project": project_path.name,
                        "cost": state_mgr.get_total_cost(),
                        "limit": self.max_cost_per_project,
                    })
                    self.event_bus.publish("project_failed", {
                        "project": project_path.name,
                        "error": "Cost limit exceeded",
                    })
                    break

                # Check for score plateau (no improvement over N iterations)
                if len(score_history) >= plateau_window:
                    recent = score_history[-plateau_window:]
                    if max(recent) - min(recent) < 0.5:
                        msg = (
                            f"Score plateau detected: scores {recent} over last "
                            f"{plateau_window} iterations (no meaningful improvement). Stopping."
                        )
                        self.logger.warning(msg)
                        state_mgr.log_conversation(
                            agent="system", role="status", iteration=iteration,
                            content=msg,
                            metadata={"label": "Plateau"},
                        )
                        self._publish_conversation_update(project_path, "system")
                        state_mgr.mark_failed(f"Score plateau: {recent}")
                        self.event_bus.publish("project_failed", {
                            "project": project_path.name,
                            "error": msg,
                        })
                        break

                if state_mgr.is_complete(self.quality_threshold, self.max_iterations):
                    self._finalize_project(project_path, state_mgr)
                    state_mgr.log_conversation(
                        agent="system", role="status", iteration=iteration,
                        content=f"Project completed! Final score: {score}/10 after {iteration} iteration(s).",
                        metadata={"label": "Completed", "score": score},
                    )
                    self._publish_conversation_update(project_path, "system")
                    self.event_bus.publish("project_complete", {
                        "project": project_path.name,
                        "score": score,
                        "iteration": iteration,
                    })
                    break
                else:
                    state_mgr.log_conversation(
                        agent="system", role="status", iteration=iteration,
                        content=f"Score {score}/10 is below threshold ({self.quality_threshold}). Starting iteration {iteration + 1}...",
                        metadata={"label": "Continuing"},
                    )
                    self._publish_conversation_update(project_path, "system")
                    self.event_bus.publish("log", {
                        "project": project_path.name,
                        "message": f"Score {score}/10 below threshold, starting iteration {iteration + 1}",
                        "level": "warning",
                    })

        except Exception as e:
            state_mgr.mark_failed(str(e))
            state_mgr.log_conversation(
                agent="system", role="error", iteration=state_mgr.get_iteration(),
                content=f"Project failed: {e}",
                metadata={"label": "Failed"},
            )
            self._publish_conversation_update(project_path, "system")
            self.event_bus.publish("project_failed", {
                "project": project_path.name,
                "error": str(e),
            })
        finally:
            self.logger.info(f"Run finished for project: {project_path.name}")
            logging.getLogger().removeHandler(log_handler)
            log_handler.close()
