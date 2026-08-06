"""API-aware Orchestrator that emits SSE events during processing."""

import dataclasses
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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
        agents = [
            ("architect", self.architect),
            ("engineer", self.engineer),
            ("verifier", self.verifier),
        ]
        if self.specifier is not None:
            agents.insert(0, ("specifier", self.specifier))
        for agent_name, agent_obj in agents:
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
            # 1000 chars / 300ms: fast providers (Kimi) emit thousands of
            # chars/sec — the old 200-char flush produced an SSE event flood
            # that the frontend had to re-render for. ~3 events/sec is plenty
            # for a live preview.
            if buf_chars[0] >= 1000 or (now - last_flush[0]) >= 0.3:
                flush()

        def get_full_content() -> str:
            return "".join(full_content)

        on_chunk._flush = flush
        on_chunk._get_full_content = get_full_content
        return on_chunk

    def _make_spec_scanner(self, project_path: Path):
        """Create a streaming-JSON scanner that emits `spec_layer` SSE events.

        Drives the animated YAML-layer index in the UI from the ACTUAL bytes the
        model emits (no simulated progress): as each file's `"path"` value closes
        we emit status=start; while its `"content"` value streams we emit
        status=writing with a tail snippet; when it closes we emit status=done.

        The authoritative file set still comes from the Specifier's final parse —
        this scanner is presentation only, so it stays defensive and never raises.
        """
        state = {
            "in_string": False,
            "escape": False,
            "buf": [],
            "value_key": None,        # key this string is the value of (None = it's a key)
            "pending_key": None,      # last completed key awaiting its value
            "last_key_candidate": None,
            "current_path": None,
            "last_writing_emit": [0.0],
        }

        def emit(path, status, snippet=""):
            self.event_bus.publish("spec_layer", {
                "project": project_path.name,
                "path": path,
                "status": status,
                "snippet": snippet,
            })

        def feed(chunk: str):
            import time
            try:
                for c in chunk:
                    if state["in_string"]:
                        if state["escape"]:
                            state["buf"].append(c)
                            state["escape"] = False
                        elif c == "\\":
                            state["escape"] = True
                        elif c == '"':
                            # string closes
                            value = "".join(state["buf"])
                            state["in_string"] = False
                            if state["value_key"] == "path":
                                state["current_path"] = value
                                emit(value, "start")
                            elif state["value_key"] == "content":
                                emit(state["current_path"], "done")
                            elif state["value_key"] is None:
                                state["last_key_candidate"] = value
                            state["value_key"] = None
                        else:
                            state["buf"].append(c)
                            if state["value_key"] == "content" and state["current_path"]:
                                now = time.monotonic()
                                if now - state["last_writing_emit"][0] >= 0.15:
                                    state["last_writing_emit"][0] = now
                                    tail = "".join(state["buf"])[-60:]
                                    emit(state["current_path"], "writing", tail)
                    else:
                        if c == '"':
                            state["in_string"] = True
                            state["buf"] = []
                            state["value_key"] = state["pending_key"]
                            state["pending_key"] = None
                        elif c == ":":
                            state["pending_key"] = state["last_key_candidate"]
                            state["last_key_candidate"] = None
                        elif c in "{[":
                            state["pending_key"] = None
            except Exception:
                pass  # presentation-only; never break generation

        return feed

    def _run_specifier(self, project_path: Path, state_mgr: StateManager):
        """Phase 1: idea -> YAML spec suite, with live SSE (layer index + tokens)."""
        self.event_bus.publish("phase_change", {
            "project": project_path.name,
            "phase": "specifying",
        })
        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": "Specifier agent started - generating spec suite...",
            "level": "info",
        })

        req_file = project_path / "01_input" / "requirements.txt"
        if req_file.exists():
            state_mgr.log_conversation(
                agent="system", role="input", iteration=0,
                content=req_file.read_text(encoding="utf-8"),
                metadata={"label": "App Idea"},
            )
            self._publish_conversation_update(project_path, "system")

        self._publish_thinking(project_path, "specifier")

        chunk_cb = self._make_chunk_callback(project_path, "specifier")
        scanner = self._make_spec_scanner(project_path)

        def combined(chunk: str):
            chunk_cb(chunk)
            scanner(chunk)

        self.specifier._on_chunk = combined
        try:
            super()._run_specifier(project_path, state_mgr)
        except Exception as e:
            state_mgr.log_conversation(
                agent="specifier", role="error", iteration=0,
                content=f"Specifier agent failed: {e}",
                metadata={"label": "Error"},
            )
            self._publish_conversation_update(project_path, "specifier")
            raise
        finally:
            chunk_cb._flush()
            self.specifier._on_chunk = None

        llm_response = chunk_cb._get_full_content()
        if llm_response:
            state_mgr.log_conversation(
                agent="specifier", role="output", iteration=0,
                content=llm_response,
                metadata={"label": "Specification Suite"},
            )
            self._publish_conversation_update(project_path, "specifier")

        self.event_bus.publish("phase_change", {
            "project": project_path.name,
            "phase": "specifying_complete",
        })
        self.event_bus.publish("log", {
            "project": project_path.name,
            "message": "Specifier agent completed - spec suite created",
            "level": "info",
        })

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

        # Staged verification ratchet: on EVERY refinement iteration, snapshot
        # staging so changes that break previously-passing tests can be rolled
        # back file-by-file. Previously gated on a directive marker in the
        # prior report — but the marker chain broke across revert rewrites
        # (the embedded best report predated the directive) and protection
        # vanished exactly where rampages happen. "Never lose a passing test"
        # is now unconditional; the baseline-aware verdict keeps it correct on
        # imperfect baselines by whitelisting chronic failures.
        baseline = None
        if iteration > 1:
            baseline = self._snapshot_staging_baseline(project_path, state_mgr)

        chunk_cb = self._make_chunk_callback(project_path, "engineer")
        self.engineer._on_chunk = chunk_cb
        try:
            super()._run_engineer(project_path, state_mgr)
            if baseline is not None:
                self._staged_verify(project_path, state_mgr, baseline, iteration)
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
            # Sandbox outage: loud, dedicated signal — scores from this
            # iteration are LLM-only and not grounded in real build/test runs.
            if phase_name == "sandbox_unavailable":
                reason = phase_data.get("reason", "unknown")
                self.event_bus.publish("sandbox_unavailable", {
                    "project": project_path.name,
                    "iteration": iteration,
                    "reason": reason,
                })
                state_mgr.log_conversation(
                    agent="verifier", role="error", iteration=iteration,
                    content=(
                        "SANDBOX UNAVAILABLE — falling back to LLM-only code review. "
                        "Scores this iteration are NOT grounded in real build/test "
                        f"results. Reason: {reason}"
                    ),
                    metadata={"label": "Sandbox Unavailable"},
                )
                self._publish_conversation_update(project_path, "verifier")
                return

            # Auto-detect known failure signatures into the rules ledger as
            # candidates (surfaced for review, NOT injected into prompts).
            try:
                from rules import RulesLedger
                combined = (phase_data.get("stdout", "") or "") + "\n" + (phase_data.get("stderr", "") or "")
                RulesLedger(self.workspace_root).detect_and_record(project_path, combined)
            except Exception as e:
                self.logger.debug(f"Rules auto-detect skipped: {e}")

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

            self.logger.info(f"Verification complete - Score: {score}/100")
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
            "message": f"Verifier completed - score: {score}/100",
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

    # Gates that reflect test/hygiene shortfalls rather than broken runtime
    # code. When ONLY these fail while build + every test pass, the next
    # iteration must not touch working code — chasing coverage with runtime
    # edits is how two prior iterations broke a green suite.
    _PROTECTABLE_GATES = {"coverage", "quality", "test_meaning"}

    # Heading marker shared by the directive writer and the staged-verify
    # trigger check — they must never drift apart.
    _DIRECTIVE_MARKER = "REFINEMENT DIRECTIVE — PROTECTED MODE"

    # Dirs never snapshotted/diffed by staged verification (deps, caches).
    _STAGE_IGNORE = ('.sandbox_deps', 'node_modules', '__pycache__',
                     '.venv', 'venv', '.git', 'dist', 'build')

    # Max risky files worth probing one-by-one (~40s per probe). Beyond
    # this, fall back to wholesale rollback.
    _BISECT_MAX = 6

    def _append_gate_directive(self, project_path: Path, iteration: int, vres) -> None:
        """Append a protected-mode directive to the iteration report when the
        runtime code is fully healthy (build + all tests green) and only
        coverage/quality-style gates are below the bar.
        """
        try:
            gates = getattr(vres, "section_gates", {}) or {}
            failing = [k for k, v in gates.items() if not v.get("pass")]
            all_green = (
                getattr(vres, "build_success", False)
                and (getattr(vres, "tests_total", 0) or 0) > 0
                and getattr(vres, "tests_passed", 0) == getattr(vres, "tests_total", 0)
            )
            if not (failing and all_green and set(failing) <= self._PROTECTABLE_GATES):
                return
            report_file = project_path / "04_feedback" / f"REPORT_iter{iteration}.md"
            if not report_file.exists():
                return
            lines = [
                f"\n\n---\n\n# {self._DIRECTIVE_MARKER} (mandatory)\n\n",
                "Build succeeds and EVERY test passes. The only failing gates are: "
                + ", ".join(sorted(failing)) + ".\n\n",
                "The working runtime code is PROTECTED this iteration:\n",
                "- Do NOT modify, rename, move, or refactor any existing runtime module.\n",
                "- Do NOT change any `__init__.py` exports or the import structure.\n",
                "- Do NOT rewrite existing passing tests.\n\n",
                "Allowed changes ONLY:\n",
            ]
            test_output = getattr(vres, "test_output", "") or ""
            if "coverage" in failing:
                cov = getattr(vres, "coverage_percent", None)
                cov_s = f" (currently {cov:.0f}%)" if isinstance(cov, (int, float)) else ""
                lines.append(
                    f"- ADD new test files/functions exercising uncovered modules "
                    f"and branches{cov_s} — target ≥85% coverage.\n")
                misses = self._coverage_misses(test_output)
                if misses:
                    lines.append(
                        "\nUncovered code by file — write tests that execute "
                        "EXACTLY these lines:\n")
                    for name, miss, cover, missing in misses:
                        loc = f" (lines {missing})" if missing else ""
                        lines.append(
                            f"- `{name}` — {cover}% covered, {miss} statements "
                            f"missed{loc}\n")
            if "quality" in failing or "test_meaning" in failing:
                lines.append(
                    "- DELETE dead/unused code flagged in this report (use the "
                    "\"delete\" array) or add the missing docs — nothing else.\n")
            broken_tests = self._collection_error_files(test_output)
            if broken_tests:
                lines.append(
                    "\nThese test files FAIL TO IMPORT on every run — dead "
                    "weight dragging the quality score; DELETE them via the "
                    "\"delete\" array (or fix the import only if it is a "
                    "one-line wrong module name):\n")
                for f in broken_tests:
                    lines.append(f"- `{f}`\n")
            lines.append(
                "\nA regression is worse than a low score here: any change that "
                "breaks a passing test will be rejected and reverted.\n"
                "Runtime edits are STAGE-CHECKED before scoring — a modification "
                "that breaks a currently-passing test is dropped automatically, "
                "and new tests that FAIL against the current runtime are "
                "quarantined. Write tests that PASS against the code AS IT "
                "EXISTS — do not write tests for behavior you are changing in "
                "the same iteration.\n")
            with open(report_file, "a", encoding="utf-8") as fh:
                fh.writelines(lines)
            self.logger.info(
                f"Protected-mode directive appended (failing gates: {', '.join(sorted(failing))})")
        except OSError as e:
            self.logger.warning(f"Could not append refinement directive: {e}")

    # ── Staged verification ratchet (trust-but-verify) ────────────────────
    # Nothing is forbidden in a refinement iteration; instead "never lose a
    # passing test" is an enforced invariant. After the engineer writes,
    # changes are partitioned into safe (new test/doc files) and risky
    # (everything else); a per-test probe then checks that every
    # PRE-EXISTING passing test still passes (chronic failures are
    # whitelisted via a baseline probe). Risky changes that break the old
    # suite are rolled back file-by-file, and new tests that fail against
    # the surviving runtime are quarantined — a regression costs one probe
    # run, not an iteration.

    def _snapshot_staging_baseline(self, project_path: Path,
                                   state_mgr: StateManager) -> Optional[Path]:
        """Copy pre-iteration staging (sans deps) for rollback capability."""
        import shutil
        staging = project_path / "03_staging"
        if not staging.exists():
            return None
        dest = project_path / ".tumbler" / "staged_baseline"
        try:
            if dest.exists():
                from orchestrator.state_manager import _CLEARABLE_STATE_SUBDIRS
                state_mgr._safe_clear_dir(dest, allowed_names=_CLEARABLE_STATE_SUBDIRS)
            shutil.copytree(
                staging, dest, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    *self._STAGE_IGNORE, '.manifest.json', '.coveragerc-sandbox'))
            return dest
        except OSError as e:
            self.logger.warning(f"Staged-verify baseline snapshot failed: {e}")
            return None

    @staticmethod
    def _is_safe_new(rel: str) -> bool:
        """New files that cannot break runtime code: tests and docs."""
        name = rel.rsplit('/', 1)[-1]
        if name.endswith(('.md', '.rst')):
            return True
        in_tests = rel.startswith(('tests/', 'test/')) or '/tests/' in rel or '/test/' in rel
        looks_test = ((name.startswith('test_') or name.endswith('_test.py'))
                      and name.endswith('.py'))
        return (in_tests and name.endswith('.py')) or looks_test

    def _walk_stage_files(self, root: Path) -> Dict[str, Path]:
        out: Dict[str, Path] = {}
        for p in root.rglob('*'):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in self._STAGE_IGNORE for part in rel.parts[:-1]):
                continue
            if rel.name in ('.manifest.json', '.coveragerc-sandbox'):
                continue
            out[rel.as_posix()] = p
        return out

    def _partition_changes(self, baseline: Path, staging: Path):
        """Classify staging vs baseline: (safe_new, risky_new, risky_modified,
        deleted). Safe = new test/doc files; risky = everything else."""
        base = self._walk_stage_files(baseline)
        cur = self._walk_stage_files(staging)
        safe_new, risky_new, risky_mod = [], [], []
        for rel, p in cur.items():
            if rel not in base:
                (safe_new if self._is_safe_new(rel) else risky_new).append(rel)
            else:
                try:
                    if p.read_bytes() != base[rel].read_bytes():
                        risky_mod.append(rel)
                except OSError:
                    risky_mod.append(rel)
        deleted = [rel for rel in base if rel not in cur]
        return safe_new, risky_new, risky_mod, deleted

    def _rollback_files(self, baseline: Path, staging: Path,
                        rels: List[str]) -> Tuple[int, int]:
        """Restore listed paths from the baseline (modifications/deletions)
        or remove them (files new this iteration). Path-contained; no
        recursive deletion."""
        import shutil
        restored = removed = 0
        staging_resolved = staging.resolve()
        for rel in rels:
            dst = staging / rel
            try:
                if not dst.resolve().is_relative_to(staging_resolved):
                    continue
            except OSError:
                continue
            src = baseline / rel
            try:
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    restored += 1
                elif dst.is_file():
                    dst.unlink()
                    removed += 1
            except OSError as e:
                self.logger.warning(f"Staged-verify rollback skipped {rel}: {e}")
        return restored, removed

    def _apply_attempt_file(self, staging: Path, rel: str,
                            content: Optional[bytes], baseline: Path) -> None:
        """Re-apply one saved risky change during bisection: write the
        engineer's bytes, or — when content is None (a deletion attempt) —
        remove the file. Path-contained like rollback."""
        dst = staging / rel
        try:
            if not dst.resolve().is_relative_to(staging.resolve()):
                return
        except OSError:
            return
        try:
            if content is None:
                if dst.is_file():
                    dst.unlink()
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(content)
        except OSError as e:
            self.logger.warning(f"Bisection apply skipped {rel}: {e}")

    def _merged_verification_config(self, state_mgr: StateManager):
        merged = self._config.verification if self._config else None
        if merged is None:
            return None
        overrides = state_mgr.get_verification_overrides()
        if overrides:
            fields = {k: v for k, v in overrides.items()
                      if k in {f.name for f in dataclasses.fields(VerificationConfig)}}
            if fields:
                merged = dataclasses.replace(merged, **fields)
        return merged

    @staticmethod
    def _old_suite_broken(probe: Dict[str, Any], new_files: set,
                          baseline_probe: Optional[Dict[str, Any]] = None) -> bool:
        """True when any pre-existing test fails/errors — or nothing passes
        at all (the baseline was green, so zero passing means wreckage).

        baseline_probe, when available, whitelists failures that were ALREADY
        present before this iteration (e.g. a chronic collection error in a
        stale test file) so pre-existing rot can't masquerade as breakage.
        """
        base_failed = set((baseline_probe or {}).get("failed", []))
        base_errors = set((baseline_probe or {}).get("collect_errors", []))
        if not probe.get("passed"):
            return True
        file_of = lambda nid: nid.split("::", 1)[0]
        for nid in probe.get("failed", []):
            if file_of(nid) not in new_files and nid not in base_failed:
                return True
        for f in probe.get("collect_errors", []):
            if f not in new_files and f not in base_errors:
                return True
        return False

    @staticmethod
    def _coverage_misses(test_output: str, limit: int = 8):
        """Rows from a term-missing coverage table with misses, worst first:
        [(name, missed, cover_pct, missing_lines_str), ...]."""
        rows = []
        for line in test_output.splitlines():
            m = re.match(r'^(\S+\.py)\s+(\d+)\s+(\d+)\s+(\d+)%\s*(.*)$', line.strip())
            if m and m.group(1) != 'TOTAL':
                miss = int(m.group(3))
                if miss > 0:
                    missing = m.group(5).strip()[:120]
                    rows.append((m.group(1), miss, int(m.group(4)), missing))
        # A file can appear once per pytest-cov section; keep the first.
        seen, uniq = set(), []
        for r in rows:
            if r[0] not in seen:
                seen.add(r[0])
                uniq.append(r)
        uniq.sort(key=lambda r: -r[1])
        return uniq[:limit]

    @staticmethod
    def _collection_error_files(test_output: str) -> List[str]:
        """Test files that error at collection time (chronic dead weight)."""
        out: List[str] = []
        for line in test_output.splitlines():
            m = re.match(r'^ERROR (\S+\.py)', line.strip())
            if m and m.group(1) not in out:
                out.append(m.group(1))
        return out

    def _quarantine_failing_new(self, baseline: Path, staging: Path,
                                probe: Dict[str, Any],
                                new_files: set) -> List[str]:
        """Remove NEW test files that fail or error against the surviving
        runtime. Keeping them ('discovery') sounded principled but loses in
        practice: the failing tests drag the full-verify score down and the
        revert-to-best guard then erases the WHOLE iteration — including the
        passing tests. Keep-only-passing makes protected iterations
        monotonically non-decreasing. Returns the quarantined paths."""
        file_of = lambda nid: nid.split("::", 1)[0]
        failing = {file_of(nid) for nid in probe.get("failed", [])}
        failing |= set(probe.get("collect_errors", []))
        targets = sorted(f for f in failing if f in new_files)
        if targets:
            self._rollback_files(baseline, staging, targets)
        return targets

    @staticmethod
    def _quarantine_note(quarantined: List[str]) -> str:
        if not quarantined:
            return " Keeping everything."
        names = ", ".join(quarantined[:6])
        more = f" (+{len(quarantined) - 6} more)" if len(quarantined) > 6 else ""
        return (f" Quarantined {len(quarantined)} new test file(s) that fail "
                f"against the current runtime: {names}{more}. Passing "
                f"additions were kept.")

    def _log_staged(self, project_path: Path, state_mgr: StateManager,
                    iteration: int, msg: str) -> None:
        self.logger.info(msg)
        state_mgr.log_conversation(
            agent="system", role="status", iteration=iteration,
            content=msg, metadata={"label": "Staged Verify"})
        self._publish_conversation_update(project_path, "system")

    def _staged_verify(self, project_path: Path, state_mgr: StateManager,
                       baseline: Path, iteration: int) -> None:
        staging = project_path / "03_staging"
        try:
            safe_new, risky_new, risky_mod, deleted = self._partition_changes(
                baseline, staging)
            risky = sorted(set(risky_new) | set(risky_mod) | set(deleted))
            new_files = set(safe_new) | set(risky_new)
            if not risky and not new_files:
                return  # nothing changed — nothing to check
            # NOTE: safe-only iterations still get probed — a batch of new
            # failing test files can wreck the score just as thoroughly as a
            # runtime edit, and skipping the probe here let exactly that
            # happen (iteration 25 scored 31.2 with zero risky changes).
            vc = self._merged_verification_config(state_mgr)
            probe = self.verifier.run_test_probe(staging, vc)
            if not probe or not probe.get("ran"):
                return  # probe unavailable — the full verification pass decides
            broken = self._old_suite_broken(probe, new_files)
            base_probe = None
            if broken:
                # Second opinion: probe the untouched baseline so chronic
                # pre-existing failures don't get pinned on this iteration.
                base_probe = self.verifier.run_test_probe(baseline, vc)
                if base_probe and base_probe.get("ran"):
                    broken = self._old_suite_broken(probe, new_files, base_probe)
            if not broken:
                q = self._quarantine_failing_new(baseline, staging, probe, new_files)
                self._log_staged(
                    project_path, state_mgr, iteration,
                    f"Staged verify: the passing suite held with all "
                    f"{len(risky)} runtime change(s) applied "
                    f"({len(safe_new)} new test/doc file(s))."
                    + self._quarantine_note(q))
                return
            # Per-file bisection: the risky set usually mixes genuine fixes
            # with one poison file. All-or-nothing rollback discarded the
            # good with the bad (three plateau iterations banked nothing
            # despite correctly-targeted fixes). Save the engineer's risky
            # versions, roll everything back, then re-apply one file at a
            # time — keeping each file whose probe holds the suite green.
            attempt: Dict[str, Optional[bytes]] = {}
            for rel in risky:
                p = staging / rel
                try:
                    attempt[rel] = p.read_bytes() if p.is_file() else None
                except OSError:
                    attempt[rel] = None
            self._rollback_files(baseline, staging, risky)

            kept: List[str] = []
            dropped: List[str] = []
            final_probe = None
            if len(risky) <= self._BISECT_MAX:
                # Test-file edits first — most likely to be benign fixes.
                order = sorted(
                    risky,
                    key=lambda r: (not r.startswith(('tests/', 'test/')), r))
                for rel in order:
                    self._apply_attempt_file(staging, rel, attempt[rel],
                                             baseline)
                    p_i = self.verifier.run_test_probe(staging, vc)
                    if (p_i and p_i.get("ran")
                            and not self._old_suite_broken(p_i, new_files, base_probe)):
                        kept.append(rel)
                        final_probe = p_i
                    else:
                        self._rollback_files(baseline, staging, [rel])
                        dropped.append(rel)
            else:
                dropped = list(risky)

            if final_probe is None:
                final_probe = self.verifier.run_test_probe(staging, vc)
            if (final_probe and final_probe.get("ran")
                    and self._old_suite_broken(final_probe, new_files, base_probe)):
                # Even the "safe" additions (e.g. a new conftest) are toxic —
                # discard the whole iteration and restore the green baseline.
                self._rollback_files(baseline, staging, kept + safe_new)
                self._log_staged(
                    project_path, state_mgr, iteration,
                    f"Staged verify: iteration {iteration} broke the passing "
                    f"suite even after dropping its risky change(s) — all of "
                    f"its changes were discarded and the previous green code "
                    f"restored.")
                return
            q = []
            if final_probe and final_probe.get("ran"):
                q = self._quarantine_failing_new(
                    baseline, staging, final_probe, new_files)

            def _names(rels, cap=5):
                s = ", ".join(rels[:cap])
                return s + (f" (+{len(rels) - cap} more)" if len(rels) > cap else "")
            msg = (f"Staged verify (bisected): kept {len(kept)} of "
                   f"{len(risky)} risky change(s)"
                   + (f" [{_names(kept)}]" if kept else "")
                   + f"; dropped {len(dropped)} suite-breaking"
                   + (f" [{_names(dropped)}]" if dropped else "")
                   + f"; {len(safe_new) - len(q)} new test/doc file(s) kept."
                   + (self._quarantine_note(q) if q else ""))
            self._log_staged(project_path, state_mgr, iteration, msg)
        except Exception as e:
            self.logger.warning(f"Staged verify skipped on error: {e}")
        finally:
            try:
                from orchestrator.state_manager import _CLEARABLE_STATE_SUBDIRS
                state_mgr._safe_clear_dir(baseline, allowed_names=_CLEARABLE_STATE_SUBDIRS)
            except OSError:
                pass

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

        # Persist the effective threshold/ceiling so the UI matches the
        # policy this run actually enforces (state may hold stale
        # creation-time defaults).
        state = state_mgr.load_state()
        if (state.get('quality_threshold') != self.quality_threshold
                or state.get('max_iterations') != self.max_iterations):
            state['quality_threshold'] = self.quality_threshold
            state['max_iterations'] = self.max_iterations
            state_mgr.save_state(state)

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
            # Phase 1: Specifier (idea -> YAML spec suite), then Architect.
            # Both are skipped when resuming from an existing plan. The Specifier
            # is additionally skipped if disabled or already complete (idempotent
            # on restart after a mid-run crash).
            if not resuming:
                self._refresh_providers(state_mgr)
                if (self.specifier is not None
                        and state_mgr.is_spec_enabled()
                        and not state_mgr.is_spec_complete()):
                    self._run_specifier(project_path, state_mgr)
                self._run_architect(project_path, state_mgr)

            # Phase 2-3: Engineer -> Verifier loop
            progress_history: list[tuple] = []
            # Sustained-quality policy state: (iteration, score, recognized)
            quality_history: list[tuple] = []
            first_hit_iteration = None
            consecutive_reverts = 0
            consecutive_failures = 0
            max_consecutive_failures = 3
            plateau_window = 3  # stop only if NOTHING observable changed this many times

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

                # Evaluate — build a progress tuple: the plateau detector only
                # trips when score AND concrete metrics are all identical, so
                # real progress (more tests passing, more files) never stalls
                # the run even if the coarse score hasn't moved yet.
                score = state_mgr.get_score() or 0.0
                iteration = state_mgr.get_iteration()
                self._snapshot_best(project_path, state_mgr, score)
                reverted = self._revert_to_best(project_path, state_mgr, score,
                                                consecutive_reverts + 1)
                consecutive_reverts = consecutive_reverts + 1 if reverted else 0
                if reverted:
                    state_mgr.log_conversation(
                        agent="system", role="status", iteration=iteration,
                        content=(
                            f"Iteration {iteration} regressed ({score}/100) well below the "
                            f"best iteration — its changes were rejected and the code was "
                            f"restored to the best-scoring state. The next iteration will "
                            f"improve the best code instead."
                        ),
                        metadata={"label": "Reverted to Best"},
                    )
                    self._publish_conversation_update(project_path, "system")
                vres = getattr(self.verifier, "last_result", None)
                if not reverted and vres is not None:
                    self._append_gate_directive(project_path, iteration, vres)
                staging = project_path / "03_staging"
                skip_dirs = {'.sandbox_deps', 'node_modules', '__pycache__', '.venv', 'venv'}
                file_count = sum(
                    1 for f in staging.rglob('*')
                    if f.is_file() and not any(part in skip_dirs for part in f.relative_to(staging).parts[:-1])
                ) if staging.exists() else 0
                progress = (
                    round(score, 1),
                    getattr(vres, "tests_passed", None),
                    getattr(vres, "tests_total", None),
                    getattr(vres, "lint_issues", None),
                    bool(getattr(vres, "build_success", False)),
                    file_count,
                )
                progress_history.append(progress)

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

                # Plateau check: fail only when N consecutive iterations produced
                # IDENTICAL progress tuples (score, tests, lint, build, file count).
                # A moving score or improving metrics always continues the loop.
                if len(progress_history) >= plateau_window:
                    recent = progress_history[-plateau_window:]
                    if all(t == recent[0] for t in recent):
                        scores = [t[0] for t in recent]
                        msg = (
                            f"No observable progress over {plateau_window} iterations "
                            f"(score {scores[0]}/100, tests {recent[0][1]}/{recent[0][2]}, "
                            f"{recent[0][5]} files — all unchanged). Stopping."
                        )
                        self.logger.warning(msg)
                        state_mgr.log_conversation(
                            agent="system", role="status", iteration=iteration,
                            content=msg,
                            metadata={"label": "Plateau"},
                        )
                        self._publish_conversation_update(project_path, "system")
                        state_mgr.mark_failed(f"No observable progress: {scores}")
                        self.event_bus.publish("project_failed", {
                            "project": project_path.name,
                            "error": msg,
                        })
                        break

                # ── Sustained-quality completion policy ─────────────────────
                # RECOGNIZED = sandbox ran, build succeeded, and EVERY test
                # passed (tests_total > 0). Unrecognized scores can never
                # complete a run, no matter how high.
                #   1. Recognized 100/100 at any point      -> stop (perfect).
                #   2. Recognized ≥ threshold for 3 straight -> stop (sustained).
                #   3. Threshold first reached in iters 1-3  -> keep refining
                #      (minimum 5 iterations before a single-hit completion).
                #   4. Recognized ≥ threshold thereafter     -> stop.
                #   5. max_iterations                        -> finalize from best.
                recognized = bool(
                    vres is not None
                    and not getattr(vres, "code_review_only", False)
                    and getattr(vres, "build_success", False)
                    and (getattr(vres, "tests_total", 0) or 0) > 0
                    and getattr(vres, "tests_passed", 0) == getattr(vres, "tests_total", 0)
                    and getattr(vres, "gates_passed", False)
                )
                quality_history.append((iteration, score, recognized))
                hit = recognized and score >= self.quality_threshold
                if hit and first_hit_iteration is None:
                    first_hit_iteration = iteration
                consecutive_hits = 0
                for _, s_, r_ in reversed(quality_history):
                    if r_ and s_ >= self.quality_threshold:
                        consecutive_hits += 1
                    else:
                        break
                min_required = 5 if (first_hit_iteration is not None
                                     and first_hit_iteration <= 3) else 0

                done_reason = None
                if recognized and score >= 100.0:
                    done_reason = "perfect score (100/100)"
                elif consecutive_hits >= 3:
                    done_reason = (
                        f"score held ≥{self.quality_threshold} for "
                        f"{consecutive_hits} consecutive iterations"
                    )
                elif hit and (not min_required or iteration >= min_required):
                    done_reason = f"quality threshold met ({score} ≥ {self.quality_threshold})"
                elif consecutive_reverts >= 4:
                    done_reason = (
                        "converged at best iteration — 4 consecutive refinement "
                        "attempts were rejected as regressions"
                    )
                elif iteration >= self.max_iterations:
                    done_reason = f"maximum iterations ({self.max_iterations}) reached"

                if done_reason:
                    self._finalize_project(project_path, state_mgr)
                    final = state_mgr.get_score()
                    state_mgr.log_conversation(
                        agent="system", role="status", iteration=iteration,
                        content=f"Project completed ({done_reason}). Final score: {final}/100 after {iteration} iteration(s).",
                        metadata={"label": "Completed", "score": final},
                    )
                    self._publish_conversation_update(project_path, "system")
                    self.event_bus.publish("project_complete", {
                        "project": project_path.name,
                        "score": final,
                        "iteration": iteration,
                    })
                    break
                else:
                    if score >= self.quality_threshold and not recognized:
                        gates = getattr(vres, "section_gates", {}) or {}
                        failing = [k for k, v in gates.items() if not v.get("pass")]
                        if vres is None or getattr(vres, "code_review_only", False):
                            why = "sandbox did not run"
                        elif (getattr(vres, "tests_total", 0) or 0) == 0:
                            why = "no tests collected"
                        elif getattr(vres, "tests_passed", 0) != getattr(vres, "tests_total", 0):
                            why = (f"{getattr(vres, 'tests_total', 0) - getattr(vres, 'tests_passed', 0)} "
                                   f"of {getattr(vres, 'tests_total', 0)} tests failing")
                        elif failing:
                            why = "section gates below 85%: " + ", ".join(failing)
                        else:
                            why = "recognition conditions not met"
                        msg = (f"Score {score}/100 is above threshold but NOT recognized "
                               f"({why}) — all tests must pass. Continuing.")
                        label = "Not Recognized"
                    elif hit and min_required and iteration < min_required:
                        msg = (f"Score {score}/100 met the threshold early (iteration "
                               f"{iteration}) — refining further; a single-hit completion "
                               f"requires at least {min_required} iterations.")
                        label = "Early Success — Extending"
                    else:
                        msg = (f"Score {score}/100 below threshold "
                               f"({self.quality_threshold}/100). Starting iteration {iteration + 1}...")
                        label = "Continuing"
                    state_mgr.log_conversation(
                        agent="system", role="status", iteration=iteration,
                        content=msg,
                        metadata={"label": label},
                    )
                    self._publish_conversation_update(project_path, "system")
                    self.event_bus.publish("log", {
                        "project": project_path.name,
                        "message": msg,
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
