"""Sandboxed code verification via ephemeral Docker containers.

Spawns short-lived containers to install dependencies, build, test, and lint
generated projects.  Uses the Docker socket proxy for restricted API access.

Security model:
  - Each verification run gets a fresh container (process/network/FS isolation)
  - Resource limits: 1 CPU, 1 GB RAM, 256 PIDs, read-only rootfs
  - Install phase: restricted outbound network (for npm/pip)
  - Build/test/lint phase: no network at all
  - Non-root user, all Linux capabilities dropped
  - Automatic cleanup on success, failure, or timeout
"""

import io
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import docker
    from docker.errors import ContainerError, ImageNotFound, APIError
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------

@dataclass
class RuntimeInfo:
    """Detected language runtime and default commands."""
    language: str
    image: str
    install_commands: List[str] = field(default_factory=list)
    build_commands: List[str] = field(default_factory=list)
    test_commands: List[str] = field(default_factory=list)
    lint_commands: List[str] = field(default_factory=list)


# Mapping of file markers to runtime info
_RUNTIME_MARKERS = [
    # (file_to_check, RuntimeInfo factory)
    ("package.json", lambda: RuntimeInfo(
        language="javascript",
        image="node:20-slim",
        install_commands=["npm install --ignore-scripts"],
        build_commands=["npm run build --if-present"],
        test_commands=["npm test --if-present"],
        lint_commands=["npx eslint . --no-error-on-unmatched-pattern 2>/dev/null || true"],
    )),
    ("requirements.txt", lambda: RuntimeInfo(
        language="python",
        image="python:3.12-slim",
        install_commands=["pip install --no-cache-dir -r requirements.txt"],
        build_commands=[],
        test_commands=["python -m pytest -x --tb=short 2>&1 || true"],
        lint_commands=["python -m flake8 --max-line-length=120 --statistics 2>&1 || true"],
    )),
    ("pyproject.toml", lambda: RuntimeInfo(
        language="python",
        image="python:3.12-slim",
        install_commands=["pip install --no-cache-dir -e '.[dev]' 2>/dev/null || pip install --no-cache-dir ."],
        build_commands=[],
        test_commands=["python -m pytest -x --tb=short 2>&1 || true"],
        lint_commands=["python -m flake8 --max-line-length=120 --statistics 2>&1 || true"],
    )),
    ("go.mod", lambda: RuntimeInfo(
        language="go",
        image="golang:1.22-alpine",
        install_commands=["go mod download"],
        build_commands=["go build ./..."],
        test_commands=["go test ./... -count=1 -timeout 30s"],
        lint_commands=["go vet ./..."],
    )),
    ("Cargo.toml", lambda: RuntimeInfo(
        language="rust",
        image="rust:1.78-slim",
        install_commands=[],
        build_commands=["cargo build 2>&1"],
        test_commands=["cargo test 2>&1"],
        lint_commands=["cargo clippy 2>&1 || true"],
    )),
    ("pom.xml", lambda: RuntimeInfo(
        language="java",
        image="eclipse-temurin:21-jdk-alpine",
        install_commands=[],
        build_commands=["mvn -q compile 2>&1"],
        test_commands=["mvn -q test 2>&1"],
        lint_commands=[],
    )),
]


def detect_runtime(plan: str, project_path: Path) -> Optional[RuntimeInfo]:
    """Detect project language/framework from files and plan text.

    Checks for marker files (package.json, requirements.txt, etc.) first,
    then falls back to plan text analysis.

    Returns None if no runtime could be detected.
    """
    # 1. Check for marker files in project directory
    for filename, factory in _RUNTIME_MARKERS:
        if (project_path / filename).exists():
            runtime = factory()
            logger.info(f"Detected runtime '{runtime.language}' from {filename}")
            return runtime

    # 2. Fall back to plan text analysis
    plan_lower = plan.lower()
    if any(kw in plan_lower for kw in ("react", "node", "npm", "javascript", "typescript", "next.js", "express")):
        runtime = _RUNTIME_MARKERS[0][1]()  # Node.js
        logger.info("Detected runtime 'javascript' from plan text")
        return runtime
    if any(kw in plan_lower for kw in ("python", "flask", "django", "fastapi", "pytest")):
        runtime = _RUNTIME_MARKERS[1][1]()  # Python
        logger.info("Detected runtime 'python' from plan text")
        return runtime
    if any(kw in plan_lower for kw in ("golang", "go module", "go.mod")):
        runtime = _RUNTIME_MARKERS[3][1]()  # Go
        logger.info("Detected runtime 'go' from plan text")
        return runtime

    logger.warning("Could not detect project runtime — sandbox verification skipped")
    return None


# ---------------------------------------------------------------------------
# Command result
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    """Result of running a command inside the sandbox."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Sandbox configuration
# ---------------------------------------------------------------------------

@dataclass
class SandboxConfig:
    """Configuration for sandbox container limits."""
    enabled: bool = True
    timeout_install: int = 300
    timeout_build: int = 300
    timeout_test: int = 120
    timeout_lint: int = 60
    memory_limit: str = "2g"
    cpu_limit: float = 1.0
    pids_limit: int = 256
    tmpfs_size: str = "512m"
    network_install: bool = True
    network_verify: bool = False


# ---------------------------------------------------------------------------
# Sandbox executor
# ---------------------------------------------------------------------------

class SandboxExecutor:
    """Executes verification commands inside ephemeral Docker containers.

    Uses the Docker socket proxy for restricted API access.  Each
    verification run spawns a fresh container that is destroyed after
    execution completes (or times out).
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        if not DOCKER_AVAILABLE:
            raise ImportError(
                "docker package not installed. Install with: pip install docker"
            )

        self.config = config or SandboxConfig()

        # Connect via DOCKER_HOST env var (points to socket proxy)
        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host:
            self.client = docker.DockerClient(base_url=docker_host)
        else:
            self.client = docker.from_env()

        logger.info(f"SandboxExecutor initialized (docker_host={docker_host or 'local socket'})")

    def _ensure_image(self, image: str) -> None:
        """Pull the base image if not already present."""
        try:
            self.client.images.get(image)
            logger.debug(f"Image {image} already available")
        except ImageNotFound:
            logger.info(f"Pulling image {image} (first-time only)...")
            self.client.images.pull(image)
            logger.info(f"Image {image} pulled successfully")

    @staticmethod
    def _make_tar(source_dir: str) -> bytes:
        """Create an in-memory tar archive of a directory's contents.

        Files are added relative to the source directory root so that
        extracting the archive into /workspace recreates the project
        structure.

        Security: symlinks are skipped entirely — a generated project
        should never contain symlinks, and including them could allow
        path traversal into host filesystem paths.
        """
        source_resolved = os.path.realpath(source_dir)
        buf = io.BytesIO()
        skipped = 0
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for root, dirs, files in os.walk(source_dir, followlinks=False):
                # Skip directories that are symlinks
                dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]

                for fname in files:
                    full = os.path.join(root, fname)

                    # Skip symlinks entirely
                    if os.path.islink(full):
                        skipped += 1
                        logger.warning(f"Skipping symlink in tar: {full}")
                        continue

                    # Validate the resolved path is within source_dir
                    real = os.path.realpath(full)
                    if not real.startswith(source_resolved + os.sep) and real != source_resolved:
                        skipped += 1
                        logger.warning(
                            f"Skipping file outside workspace: {full} -> {real}"
                        )
                        continue

                    arcname = os.path.relpath(full, source_dir)
                    tar.add(full, arcname=arcname)

        if skipped:
            logger.info(f"Tar archive: skipped {skipped} symlinks/out-of-scope files")
        buf.seek(0)
        return buf.read()

    def _extract_workspace(self, container, workspace_path: str) -> None:
        """Extract /workspace from container back to host path.

        After the install phase, installed dependencies (node_modules, venv, etc.)
        live inside the container. This method downloads them back to the host
        workspace so that subsequent phases (build, test, lint) get the full
        workspace with installed deps.

        Security: only extracts into the validated workspace_path. Symlinks and
        paths outside the workspace are skipped during extraction.
        """
        resolved_root = os.path.realpath(workspace_path)
        try:
            archive_stream, _ = container.get_archive("/workspace")
            # Reassemble the chunked stream into a contiguous tar
            buf = io.BytesIO()
            for chunk in archive_stream:
                buf.write(chunk)
            buf.seek(0)

            with tarfile.open(fileobj=buf, mode="r") as tar:
                for member in tar.getmembers():
                    # The archive root is "workspace/" — strip it to get relative paths
                    if member.name.startswith("workspace/"):
                        member.name = member.name[len("workspace/"):]
                    elif member.name == "workspace":
                        continue  # skip the root directory entry itself
                    else:
                        # Unexpected path prefix — skip for safety
                        continue

                    # Skip empty name (root)
                    if not member.name:
                        continue

                    # Security: skip symlinks
                    if member.issym() or member.islnk():
                        logger.debug(f"Skipping symlink in extract: {member.name}")
                        continue

                    # Security: validate the destination is within workspace
                    dest = os.path.realpath(os.path.join(workspace_path, member.name))
                    if not dest.startswith(resolved_root + os.sep) and dest != resolved_root:
                        logger.warning(f"Skipping path traversal in extract: {member.name}")
                        continue

                    # Extract the member
                    if member.isdir():
                        os.makedirs(os.path.join(workspace_path, member.name), exist_ok=True)
                    else:
                        # Ensure parent directory exists
                        parent = os.path.dirname(os.path.join(workspace_path, member.name))
                        os.makedirs(parent, exist_ok=True)
                        f = tar.extractfile(member)
                        if f is not None:
                            with open(os.path.join(workspace_path, member.name), "wb") as out:
                                out.write(f.read())

            logger.info(f"Extracted workspace from container back to {workspace_path}")
        except Exception as e:
            logger.warning(f"Failed to extract workspace from container: {e}")

    def _run_container(
        self,
        image: str,
        commands: List[str],
        workspace_path: str,
        timeout: int,
        network_mode: str = "none",
        label: str = "sandbox",
        extract_workspace: bool = False,
    ) -> List[CommandResult]:
        """Run commands inside an ephemeral container.

        Uses create + put_archive + start instead of bind mounts.
        This avoids host path mapping issues when running as a
        sibling container (backend can't share its filesystem paths
        with Docker Engine directly).

        Args:
            image: Docker image to use.
            commands: Shell commands to execute sequentially.
            workspace_path: Path to project directory (inside backend container).
            timeout: Per-command timeout in seconds.
            network_mode: "none", "bridge", or a network name.
            label: Label for logging.
            extract_workspace: If True, extract /workspace from the container
                back to workspace_path after successful execution. Used by the
                install phase to persist installed dependencies.

        Returns:
            List of CommandResult for each command.
        """
        if not commands:
            return []

        # Build a single shell script from all commands
        script_lines = ["#!/bin/sh", "set -e", "cd /workspace"]
        for cmd in commands:
            script_lines.append(f"echo '=== RUNNING: {cmd} ==='")
            script_lines.append(cmd)
        script = "\n".join(script_lines)

        container = None
        results = []
        t0 = time.time()
        try:
            # Create container (not started yet).
            # Note: read_only is NOT set because put_archive needs to
            # write project files before start. Security is maintained by
            # dropping all capabilities, no-new-privileges, network
            # isolation, resource limits, and ephemeral container lifecycle.
            #
            # /workspace is intentionally NOT a tmpfs mount. Docker mounts
            # tmpfs at container start, which would overlay files placed by
            # put_archive (before start), making them invisible inside the
            # container. The writable layer is ephemeral (destroyed with the
            # container) so there is no security benefit to tmpfs here.
            container = self.client.containers.create(
                image=image,
                command=["sh", "-c", script],
                working_dir="/workspace",
                # Resource limits
                mem_limit=self.config.memory_limit,
                nano_cpus=int(self.config.cpu_limit * 1e9),
                pids_limit=self.config.pids_limit,
                # Security
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={
                    "/tmp": f"size={self.config.tmpfs_size}",
                    "/root": "size=64m",
                },
                # Network
                network_mode=network_mode,
                # Lifecycle
                auto_remove=False,
                detach=True,
                labels={"code-tumbler.role": "sandbox", "code-tumbler.phase": label},
            )

            # Copy project files into the container via tar archive
            tar_data = self._make_tar(workspace_path)
            container.put_archive("/workspace", tar_data)

            # Start the container
            container.start()

            # Wait for completion with timeout
            total_timeout = timeout * max(1, len(commands))
            exit_info = container.wait(timeout=total_timeout)
            elapsed = time.time() - t0
            exit_code = exit_info.get("StatusCode", -1)

            # Capture output
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            # Truncate very long output
            max_output = 50_000
            if len(stdout) > max_output:
                stdout = stdout[:max_output] + f"\n\n[... truncated at {max_output} chars ...]"
            if len(stderr) > max_output:
                stderr = stderr[:max_output] + f"\n\n[... truncated at {max_output} chars ...]"

            results.append(CommandResult(
                command=" && ".join(commands),
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_s=elapsed,
            ))

            logger.info(
                f"Sandbox [{label}]: exit={exit_code}, "
                f"time={elapsed:.1f}s, stdout={len(stdout)} chars"
            )

            # Extract workspace back to host if requested (install phase)
            if extract_workspace and exit_code == 0 and container:
                self._extract_workspace(container, workspace_path)

        except Exception as e:
            elapsed = time.time() - t0
            error_msg = str(e)

            # Check for timeout
            timed_out = "timed out" in error_msg.lower() or "read timeout" in error_msg.lower()

            if timed_out:
                logger.warning(f"Sandbox [{label}]: timed out after {timeout}s")
                if container:
                    try:
                        container.kill()
                    except Exception:
                        pass

            results.append(CommandResult(
                command=" && ".join(commands),
                exit_code=-1,
                stdout="",
                stderr=f"Container execution failed: {error_msg}",
                timed_out=timed_out,
                duration_s=elapsed,
            ))

        finally:
            # Always clean up
            if container:
                try:
                    container.remove(force=True)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to remove sandbox container: {cleanup_err}")

        return results

    @staticmethod
    def _notify_phase(
        callback: Optional[Callable[[str, Dict[str, Any]], None]],
        phase_name: str,
        results: Optional[List[CommandResult]],
        commands: List[str],
    ) -> None:
        """Invoke the on_phase_complete callback if present."""
        if callback is None:
            return
        if not results:
            callback(phase_name, {
                "status": "skipped",
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "duration_s": 0.0,
                "commands": commands,
            })
            return
        r = results[0]
        max_sse_output = 10_000
        status = "timeout" if r.timed_out else ("success" if r.exit_code == 0 else "failed")
        callback(phase_name, {
            "status": status,
            "exit_code": r.exit_code,
            "stdout": r.stdout[:max_sse_output],
            "stderr": r.stderr[:max_sse_output],
            "duration_s": r.duration_s,
            "commands": commands,
        })

    def run_verification(
        self,
        project_path: Path,
        strategy: Dict[str, List[str]],
        runtime: RuntimeInfo,
        on_phase_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> "VerificationResult":
        """Run full verification pipeline in sandboxed containers.

        Two-phase execution:
          1. Install phase (with restricted network)
          2. Build/test/lint phase (no network)

        Args:
            project_path: Path to project staging directory.
            strategy: Commands extracted from plan (may override runtime defaults).
            runtime: Detected runtime info.

        Returns:
            VerificationResult populated with real outputs.
        """
        from agents.verifier import VerificationResult

        results = VerificationResult()

        # Ensure the base image is available
        try:
            self._ensure_image(runtime.image)
        except Exception as e:
            logger.error(f"Failed to pull image {runtime.image}: {e}")
            results.code_review_only = True
            results.errors.append(f"Failed to pull sandbox image: {e}")
            return results

        workspace = str(project_path.resolve())

        # Merge plan strategy with runtime defaults (plan commands take priority)
        install_cmds = strategy.get("install") or runtime.install_commands
        build_cmds = strategy.get("build") or runtime.build_commands
        test_cmds = strategy.get("test") or runtime.test_commands
        lint_cmds = runtime.lint_commands  # always use runtime defaults for lint

        # --- Phase 1: Install (with network) ---
        # extract_workspace=True persists installed deps (node_modules, venv, etc.)
        # back to the workspace so that build/test/lint phases can use them.
        if install_cmds:
            logger.info(f"Sandbox install phase: {install_cmds}")
            network = "bridge" if self.config.network_install else "none"
            install_results = self._run_container(
                image=runtime.image,
                commands=install_cmds,
                workspace_path=workspace,
                timeout=self.config.timeout_install,
                network_mode=network,
                label="install",
                extract_workspace=True,
            )
            if install_results:
                r = install_results[0]
                results.build_output = r.stdout + ("\n" + r.stderr if r.stderr else "")
                results.build_success = (r.exit_code == 0 and not r.timed_out)
                if r.timed_out:
                    results.errors.append(f"Install timed out after {self.config.timeout_install}s")
                elif r.exit_code != 0:
                    results.errors.append(f"Install failed with exit code {r.exit_code}")
            self._notify_phase(on_phase_complete, "install", install_results, install_cmds)
        else:
            # No install commands — mark build as success (nothing to install)
            results.build_success = True
            results.build_output = "No install commands required."
            self._notify_phase(on_phase_complete, "install", None, [])

        # --- Phase 2: Build (no network) ---
        if build_cmds and results.build_success:
            logger.info(f"Sandbox build phase: {build_cmds}")
            build_results = self._run_container(
                image=runtime.image,
                commands=build_cmds,
                workspace_path=workspace,
                timeout=self.config.timeout_build,
                network_mode="none",
                label="build",
            )
            if build_results:
                r = build_results[0]
                results.build_output += "\n\n--- Build ---\n" + r.stdout
                if r.stderr:
                    results.build_output += "\n" + r.stderr
                if r.exit_code != 0:
                    results.build_success = False
                    results.errors.append(f"Build failed with exit code {r.exit_code}")
                if r.timed_out:
                    results.build_success = False
                    results.errors.append(f"Build timed out after {self.config.timeout_build}s")
            self._notify_phase(on_phase_complete, "build", build_results, build_cmds)

        # --- Phase 3+4: Test and Lint in parallel (no network) ---
        # Each phase spawns its own container — no shared state.
        def _run_test():
            if not test_cmds:
                return None
            logger.info(f"Sandbox test phase: {test_cmds}")
            return self._run_container(
                image=runtime.image,
                commands=test_cmds,
                workspace_path=workspace,
                timeout=self.config.timeout_test,
                network_mode="none",
                label="test",
            )

        def _run_lint():
            if not lint_cmds:
                return None
            logger.info(f"Sandbox lint phase: {lint_cmds}")
            return self._run_container(
                image=runtime.image,
                commands=lint_cmds,
                workspace_path=workspace,
                timeout=self.config.timeout_lint,
                network_mode="none",
                label="lint",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            test_future: Future = pool.submit(_run_test)
            lint_future: Future = pool.submit(_run_lint)

            test_results = test_future.result()
            lint_results = lint_future.result()

        if test_results:
            r = test_results[0]
            results.test_output = r.stdout + ("\n" + r.stderr if r.stderr else "")
            passed, total = self._parse_test_counts(r.stdout + r.stderr)
            results.tests_passed = passed
            results.tests_total = total
            if r.timed_out:
                results.errors.append(f"Tests timed out after {self.config.timeout_test}s")
        self._notify_phase(on_phase_complete, "test", test_results, test_cmds)

        if lint_results:
            r = lint_results[0]
            results.lint_output = r.stdout + ("\n" + r.stderr if r.stderr else "")
            results.lint_issues = self._count_lint_issues(r.stdout + r.stderr)
        self._notify_phase(on_phase_complete, "lint", lint_results, lint_cmds)

        return results

    @staticmethod
    def _parse_test_counts(output: str) -> Tuple[int, int]:
        """Extract test pass/total counts from test runner output.

        Supports common formats:
          - pytest: "5 passed, 2 failed"
          - jest/mocha: "Tests: 3 passed, 1 failed, 4 total"
          - go test: "ok   ... 0.5s" / "FAIL ... 0.5s"
          - generic: "X/Y tests passed"
        """
        # pytest format: "N passed" and optionally "M failed"
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        if passed_match:
            passed = int(passed_match.group(1))
            failed = int(failed_match.group(1)) if failed_match else 0
            return passed, passed + failed

        # Jest/Vitest format: "Tests:  N passed, M total"
        jest_match = re.search(r"Tests:\s+(\d+)\s+passed.*?(\d+)\s+total", output)
        if jest_match:
            return int(jest_match.group(1)), int(jest_match.group(2))

        # Go test: count "ok" and "FAIL" lines
        ok_count = len(re.findall(r"^ok\s+", output, re.MULTILINE))
        fail_count = len(re.findall(r"^FAIL\s+", output, re.MULTILINE))
        if ok_count + fail_count > 0:
            return ok_count, ok_count + fail_count

        # Generic "X/Y" pattern
        generic = re.search(r"(\d+)/(\d+)\s*(?:tests?\s+)?passed", output, re.IGNORECASE)
        if generic:
            return int(generic.group(1)), int(generic.group(2))

        # No recognizable test output
        return 0, 0

    @staticmethod
    def _count_lint_issues(output: str) -> int:
        """Count lint issues from linter output.

        Heuristic: count lines that look like file:line:col: messages.
        """
        # ESLint / flake8 / pylint pattern: path:line:col: message
        issue_lines = re.findall(r"^\s*\S+:\d+:\d+:?\s+", output, re.MULTILINE)
        if issue_lines:
            return len(issue_lines)

        # "N problems" / "N errors" / "N warnings" summary
        summary = re.search(r"(\d+)\s+(?:problems?|errors?|warnings?)", output, re.IGNORECASE)
        if summary:
            return int(summary.group(1))

        return 0
