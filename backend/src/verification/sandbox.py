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
    # Active Specification Alignment fields (set by web_detect)
    is_web_app: bool = False
    dev_server_command: Optional[str] = None
    dev_server_port: int = 3000


# Workspace-local dir where Python deps are installed so they persist across the
# per-phase containers (see the Python runtime note below).
_PY_DEPS = ".sandbox_deps"

# Manifest-aware install: tools first (always succeed), then whichever manifest
# exists. Guards against a generated project that references a manifest it didn't
# actually write (runtime detection can fall back to plan text), which previously
# hard-failed install on "Could not open requirements file".
_PY_INSTALL_CMD = (
    # 1. Test tools (always). 2. requirements.txt (strict — a broken manifest
    # should fail install visibly). 3. Dev-requirements variants (tolerant).
    # 4. The package itself with dev extras (src-layout projects put test deps
    # like moto in [dev]); fall back to bare package, then continue regardless.
    f"pip install --no-cache-dir --upgrade --target={_PY_DEPS} pytest pytest-cov flake8 && "
    # rm -f first: a stale round-tripped copy may be unwritable to the
    # capability-stripped root user, but unlink only needs directory write.
    f"rm -f .coveragerc-sandbox && "
    f"printf '[run]\\nomit =\\n    {_PY_DEPS}/*\\n    tests/*\\n    test/*\\n    setup.py\\n' > .coveragerc-sandbox && "
    f"if [ -f requirements.txt ]; then "
    f"pip install --no-cache-dir --upgrade --target={_PY_DEPS} -r requirements.txt; "
    f"fi && "
    f'for R in requirements-dev.txt dev-requirements.txt requirements_dev.txt; do '
    f'[ ! -f "$R" ] || pip install --no-cache-dir --upgrade --target={_PY_DEPS} -r "$R" || true; done && '
    f"if [ -f pyproject.toml ]; then "
    f"pip install --no-cache-dir --upgrade --target={_PY_DEPS} '.[dev]' 2>&1 || "
    f"pip install --no-cache-dir --upgrade --no-deps --target={_PY_DEPS} . 2>&1 || true; "
    f"fi"
)

# Prefer a tests/ dir (keeps pytest's rootdir/conftest scan away from .sandbox_deps);
# fall back to the workspace root. PYTHONPATH makes the installed deps importable.
_PY_TEST_CMD = (
    # PYTHONPATH comes from the container-level env exports (src, workspace,
    # .sandbox_deps) — no inline prefix here, it would shadow the exports.
    # No -x and --continue-on-collection-errors: one broken test module must
    # not zero out the whole run — partial pass/fail counts are exactly the
    # signal the feedback loop needs to converge.
    f'python -m pytest "$([ -d tests ] && echo tests || echo .)" '
    f'--tb=short --continue-on-collection-errors --ignore={_PY_DEPS} '
    # term-missing: the engineer needs the EXACT uncovered line numbers to
    # target new tests — a bare percentage leaves it guessing blind.
    f'--cov=. --cov-config=.coveragerc-sandbox --cov-report=term-missing '
    f'-p no:cacheprovider 2>&1 || true'
)
_PY_LINT_CMD = (
    # Full lint profile (the old --select=E9,F63,F7,F82 screened only critical
    # errors, making the lint score nearly free). Black-compatible ignores.
    f'python -m flake8 . --count --max-line-length=120 '
    f'--extend-ignore=E501,W503,W504,E203,E731 '
    f'--exclude .venv,venv,node_modules,dist,build,__pycache__,.git,{_PY_DEPS},.coveragerc-sandbox 2>&1 || true'
)

# Runtime smoke: import every top-level package. Programs that crash on import
# scored zero deductions before this — "does it even load" is now worth points.
_PY_SMOKE_CMD = (
    'python -c "'
    "import os, importlib; "
    "base = 'src' if os.path.isdir('src') else '.'; "
    "pkgs = [d for d in os.listdir(base) "
    "if os.path.isdir(os.path.join(base, d)) "
    "and os.path.exists(os.path.join(base, d, '__init__.py')) "
    "and d not in ('tests', 'test', '.sandbox_deps')]; "
    "mods = [f[:-3] for f in os.listdir(base) if f.endswith('.py') "
    "and not f.startswith('test') and f not in ('setup.py', 'conftest.py')] if not pkgs else []; "
    "targets = pkgs or mods; "
    "print('SMOKE SKIP: nothing importable') if not targets else "
    "([importlib.import_module(t) for t in targets], print('SMOKE OK:', ', '.join(targets)))"
    '"'
)

# Mapping of file markers to runtime info
_RUNTIME_MARKERS = [
    # (file_to_check, RuntimeInfo factory)
    ("package.json", lambda: RuntimeInfo(
        language="javascript",
        image="node:20-slim",
        install_commands=["npm install --legacy-peer-deps --ignore-scripts --no-audit"],
        build_commands=["npm run build --if-present"],
        test_commands=["npm test --if-present"],
        lint_commands=["npx eslint . --no-error-on-unmatched-pattern --ignore-pattern 'node_modules/' --ignore-pattern 'dist/' --ignore-pattern 'build/' --ignore-pattern 'coverage/' 2>/dev/null || true"],
    )),
    # Python: install deps into a workspace-local dir (.sandbox_deps) via
    # --target, NOT system site-packages. Each verification phase runs in a fresh
    # container and only the /workspace tree is carried between them, so anything
    # pip puts outside /workspace (the default site-packages) is lost — which is
    # why `python -m pytest` reported "No module named pytest" even though it
    # installed fine. Installing into .sandbox_deps + PYTHONPATH keeps the deps
    # available in the test/lint phases. pytest/flake8 are force-installed so the
    # test/lint tools always exist. --upgrade makes repeat iterations idempotent.
    # .sandbox_deps is excluded from lint and ignored by pytest collection.
    ("requirements.txt", lambda: RuntimeInfo(
        language="python",
        image="python:3.12-slim",
        install_commands=[_PY_INSTALL_CMD],
        build_commands=[],
        test_commands=[_PY_TEST_CMD],
        lint_commands=[_PY_LINT_CMD],
    )),
    ("pyproject.toml", lambda: RuntimeInfo(
        language="python",
        image="python:3.12-slim",
        install_commands=[_PY_INSTALL_CMD],
        build_commands=[],
        test_commands=[_PY_TEST_CMD],
        lint_commands=[_PY_LINT_CMD],
    )),
    ("go.mod", lambda: RuntimeInfo(
        language="go",
        image="golang:1.22-alpine",
        install_commands=["go mod download"],
        build_commands=["go build -v ./..."],
        test_commands=["go test ./... -count=1 -timeout 30s"],
        lint_commands=["go vet ./..."],
    )),
    ("Cargo.toml", lambda: RuntimeInfo(
        language="rust",
        image="rust:1.78-slim",
        install_commands=[],
        build_commands=["cargo build --quiet 2>&1"],
        test_commands=["cargo test --quiet 2>&1"],
        lint_commands=["cargo clippy --workspace -- -D warnings 2>&1 || true"],
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
            _augment_with_web_info(runtime, plan, project_path)
            return runtime

    # 2. Fall back to plan text analysis
    plan_lower = plan.lower()
    if any(kw in plan_lower for kw in ("react", "node", "npm", "javascript", "typescript", "next.js", "express")):
        runtime = _RUNTIME_MARKERS[0][1]()  # Node.js
        logger.info("Detected runtime 'javascript' from plan text")
        _augment_with_web_info(runtime, plan, project_path)
        return runtime
    if any(kw in plan_lower for kw in ("python", "flask", "django", "fastapi", "pytest")):
        runtime = _RUNTIME_MARKERS[1][1]()  # Python
        logger.info("Detected runtime 'python' from plan text")
        _augment_with_web_info(runtime, plan, project_path)
        return runtime
    if any(kw in plan_lower for kw in ("golang", "go module", "go.mod")):
        runtime = _RUNTIME_MARKERS[3][1]()  # Go
        logger.info("Detected runtime 'go' from plan text")
        return runtime

    logger.warning("Could not detect project runtime — sandbox verification skipped")
    return None


def _augment_with_web_info(runtime: RuntimeInfo, plan: str, project_path: Path) -> None:
    """Augment a detected runtime with web application info if applicable."""
    try:
        from verification.web_detect import detect_web_app
        web_info = detect_web_app(plan, project_path)
        if web_info.is_web_app:
            runtime.is_web_app = True
            runtime.dev_server_command = web_info.dev_server_command
            runtime.dev_server_port = web_info.dev_server_port
            logger.info("Web app detected: %s (port %d)",
                        web_info.framework, web_info.dev_server_port)
    except Exception as e:
        logger.debug("Web app detection failed: %s", e)


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
    timeout_install: int = 600  # Increased from 300
    timeout_build: int = 600    # Increased from 300
    timeout_test: int = 300     # Increased from 120
    timeout_lint: int = 120     # Increased from 60
    memory_limit: str = "4g"    # Increased from 2g
    cpu_limit: float = 2.0      # Increased from 1.0
    pids_limit: int = 1024      # Increased from 256
    tmpfs_size: str = "4g"      # Increased from 2g
    network_install: bool = True
    network_verify: bool = False
    # E2E verification (Active Specification Alignment)
    e2e_enabled: bool = True
    timeout_e2e: int = 300      # Increased from 180
    memory_limit_e2e: str = "4g" # Increased from 3g


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
        self.client = self._connect_with_recovery(docker_host)

        logger.info(f"SandboxExecutor initialized (docker_host={docker_host or 'local socket'})")

    @staticmethod
    def _connect_with_recovery(docker_host: Optional[str]):
        """Connect to Docker; if the daemon is down, optionally revive it.

        When DOCKER_RECOVERY_CMD is set (e.g. `systemctl --user start
        docker-desktop` for a host-run backend using Docker Desktop), a failed
        connection runs that command and polls up to 60s for the daemon to come
        back before giving up. Without it, behavior is unchanged: the caller
        falls back to code-review-only verification.
        """
        def _connect():
            client = (docker.DockerClient(base_url=docker_host)
                      if docker_host else docker.from_env())
            client.ping()
            return client

        try:
            return _connect()
        except Exception as first_err:
            recovery_cmd = os.environ.get("DOCKER_RECOVERY_CMD", "").strip()
            if not recovery_cmd:
                raise
            logger.warning(
                f"Docker daemon unreachable ({first_err}); attempting recovery "
                f"via DOCKER_RECOVERY_CMD: {recovery_cmd}"
            )
            import shlex
            import subprocess
            try:
                subprocess.run(
                    shlex.split(recovery_cmd), timeout=30, check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as rec_err:
                logger.warning(f"Docker recovery command failed to run: {rec_err}")
                raise first_err
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    client = _connect()
                    logger.info("Docker daemon recovered — sandbox available again")
                    return client
                except Exception:
                    time.sleep(2)
            logger.warning("Docker daemon did not come back within 60s")
            raise first_err

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
    def _tar_root_owner(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        """Normalize tar member ownership to root (uid/gid 0).

        The sandbox runs as root with ALL capabilities dropped — including
        CAP_DAC_OVERRIDE, so root there CANNOT bypass file permissions. Files
        archived with the host user's uid (e.g. a round-tripped
        .coveragerc-sandbox or .sandbox_deps) would be unwritable inside the
        container, silently killing the install command chain.
        """
        ti.uid = 0
        ti.gid = 0
        ti.uname = "root"
        ti.gname = "root"
        return ti

    @staticmethod
    def _make_tar(source_dir: str) -> bytes:
        """Create an in-memory tar archive of a directory's contents.

        Files are added relative to the source directory root so that
        extracting the archive into /workspace recreates the project
        structure. Ownership is normalized to root so the capability-stripped
        container user can modify every file it receives.

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
                    tar.add(full, arcname=arcname,
                            filter=SandboxExecutor._tar_root_owner)

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
                            out_path = os.path.join(workspace_path, member.name)
                            with open(out_path, "wb") as out:
                                out.write(f.read())
                            # Preserve the executable bit from the archive (owner-
                            # only class of chmod; masked to safe perm bits). Without
                            # this, console scripts like .sandbox_deps/bin/pytest
                            # lose +x on the round-trip and a plan's bare `pytest`
                            # fails with "Permission denied" in later phases.
                            if member.mode & 0o111:
                                try:
                                    os.chmod(out_path, member.mode & 0o755)
                                except OSError:
                                    pass

            logger.info(f"Extracted workspace from container back to {workspace_path}")
        except Exception as e:
            logger.warning(f"Failed to extract workspace from container: {e}")

    def run_python_test_probe(self, project_path: Path) -> Dict[str, Any]:
        """Fast pytest run reporting PER-TEST outcomes (staged verification).

        Runs the suite with -v and no coverage/lint so the caller can tell
        whether failures live in pre-existing or newly added test files.
        Network none; deps come from the persisted .sandbox_deps.
        """
        workspace = str(project_path.resolve())
        env_exports = [
            f"export PYTHONPATH=/workspace/src:/workspace:/workspace/{_PY_DEPS}",
            f"export PATH=/workspace/{_PY_DEPS}/bin:$PATH",
        ]

        # Post-revert staging is restored from a deps-free snapshot; without a
        # bootstrap the probe would see "No module named pytest" and misread a
        # healthy suite as wrecked. Run the standard install phase (round-trips
        # deps back to the host workspace) whenever .sandbox_deps is absent.
        if not (project_path / _PY_DEPS).is_dir():
            self._run_container(
                image="python:3.12-slim",
                commands=[_PY_INSTALL_CMD],
                workspace_path=workspace,
                timeout=self.config.timeout_install,
                network_mode="bridge" if self.config.network_install else "none",
                label="probe-install",
                extract_workspace=True,
                env_exports=env_exports,
            )

        cmd = (
            f'python -m pytest "$([ -d tests ] && echo tests || echo .)" '
            f'-v --tb=no -rEf --continue-on-collection-errors '
            f'--ignore={_PY_DEPS} -p no:cacheprovider 2>&1 || true'
        )
        results = self._run_container(
            image="python:3.12-slim",
            commands=[cmd],
            workspace_path=workspace,
            timeout=self.config.timeout_test,
            network_mode="none",
            label="probe",
            env_exports=env_exports,
        )
        out = (results[0].stdout or "") if results else ""
        # pytest must have actually executed — otherwise (missing deps, crash)
        # report ran=False so the caller skips staged decisions entirely.
        executed = bool(re.search(r"collected \d+ item|no tests ran|=+ .+ in [\d.]+s", out))
        passed: List[str] = []
        failed: List[str] = []
        collect_errors: List[str] = []
        for line in out.splitlines():
            line = line.strip()
            m = re.match(r'^(\S+::\S+)\s+(PASSED|FAILED|ERROR)\b', line)
            if m:
                (passed if m.group(2) == "PASSED" else failed).append(m.group(1))
                continue
            m = re.match(r'^(?:FAILED|ERROR)\s+(\S+?\.py)(?:::(\S+))?', line)
            if m:
                nid = m.group(1) + (f"::{m.group(2)}" if m.group(2) else "")
                if m.group(2):
                    if nid not in failed:
                        failed.append(nid)
                elif m.group(1) not in collect_errors:
                    collect_errors.append(m.group(1))
        m = re.search(r"while loading conftest '([^']+)'", out)
        if m:
            rel = m.group(1)
            rel = rel[len("/workspace/"):] if rel.startswith("/workspace/") else rel
            if rel not in collect_errors:
                collect_errors.append(rel)
        return {
            "ran": bool(results) and executed,
            "passed": passed,
            "failed": failed,
            "collect_errors": collect_errors,
            "output_tail": out[-2000:],
        }

    def _run_container(
        self,
        image: str,
        commands: List[str],
        workspace_path: str,
        timeout: int,
        network_mode: str = "none",
        label: str = "sandbox",
        extract_workspace: bool = False,
        env_exports: Optional[List[str]] = None,
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

        # Build a single shell script from all commands. env_exports (e.g. the
        # Python PYTHONPATH/PATH pointing at .sandbox_deps) are exported first so
        # they apply to EVERY command regardless of whether the command came from
        # the plan or the runtime defaults — this is what keeps a plan's bare
        # `pytest`/`flake8` resolving to the persisted deps.
        script_lines = ["#!/bin/sh", "set -e", "cd /workspace"]
        for exp in (env_exports or []):
            script_lines.append(exp)
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
                    "/root": f"size={self.config.tmpfs_size}",
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
        status_override: Optional[str] = None,
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
        status = "timeout" if r.timed_out else (
            status_override or ("success" if r.exit_code == 0 else "failed"))
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
        e2e_test_commands: Optional[List[str]] = None,
    ) -> "VerificationResult":
        """Run full verification pipeline in sandboxed containers.

        Multi-phase execution:
          1. Install phase (with restricted network)
          2. Build phase (no network)
          3+4. Test and Lint in parallel (no network)
          5. E2E phase for web apps (no network — dev server + browser in container)

        Args:
            project_path: Path to project staging directory.
            strategy: Commands extracted from plan (may override runtime defaults).
            runtime: Detected runtime info.
            on_phase_complete: Callback invoked after each sandbox phase.
            e2e_test_commands: Commands to run Playwright E2E tests (optional).

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

        # For Python, export PYTHONPATH + PATH pointing at the workspace-local
        # .sandbox_deps (where deps are installed via --target). This makes deps
        # and console scripts resolvable by ANY command form — `pytest`,
        # `python -m pytest`, `flake8` — whether the command came from the plan or
        # the runtime defaults, so a plan can't reintroduce "No module named X".
        env_exports = None
        if runtime.language == "python":
            # /workspace first so project modules import (bare `pytest` does not
            # add rootdir to sys.path the way `python -m pytest` does).
            env_exports = [
                f"export PYTHONPATH=/workspace/src:/workspace:/workspace/{_PY_DEPS}",
                f"export PATH=/workspace/{_PY_DEPS}/bin:$PATH",
            ]

        # Merge plan strategy with runtime defaults (plan commands take priority).
        # EXCEPTION: for Python, always use the runtime default install — it is the
        # only one that installs into .sandbox_deps (via --target) so deps survive
        # into the test/lint containers. A plan install like `pip install -r
        # requirements.txt` would drop deps into system site-packages and lose them,
        # reintroducing "No module named pytest". Test/build/lint can still override.
        if runtime.language == "python":
            install_cmds = runtime.install_commands
        else:
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
                env_exports=env_exports,
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
                env_exports=env_exports,
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
                env_exports=env_exports,
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
                env_exports=env_exports,
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
            results.coverage_percent = self._parse_coverage(r.stdout + r.stderr)
            if r.timed_out:
                results.errors.append(f"Tests timed out after {self.config.timeout_test}s")
        test_status = None
        if test_results and not test_results[0].timed_out:
            test_status = ("success"
                           if results.tests_total > 0
                           and results.tests_passed == results.tests_total
                           else "failed")
        self._notify_phase(on_phase_complete, "test", test_results, test_cmds,
                           status_override=test_status)

        if lint_results:
            r = lint_results[0]
            results.lint_output = r.stdout + ("\n" + r.stderr if r.stderr else "")
            results.lint_issues = self._count_lint_issues(r.stdout + r.stderr)
        self._notify_phase(on_phase_complete, "lint", lint_results, lint_cmds)

        # --- Phase 4b: Runtime smoke (import the package; no network) ---
        if runtime.language == "python":
            smoke_results = self._run_container(
                image=runtime.image,
                commands=[_PY_SMOKE_CMD],
                workspace_path=workspace,
                timeout=60,
                network_mode="none",
                label="smoke",
                env_exports=env_exports,
            )
            if smoke_results:
                r = smoke_results[0]
                results.smoke_success = (r.exit_code == 0 and not r.timed_out)
                results.runtime_output = (r.stdout + ("\n" + r.stderr if r.stderr else ""))[:5000]
                if not results.smoke_success:
                    results.errors.append("Runtime smoke failed: package does not import cleanly")
            self._notify_phase(on_phase_complete, "smoke", smoke_results, [_PY_SMOKE_CMD])
        else:
            results.smoke_success = None  # N/A — scoring falls back to build success

        # --- Phase 5: E2E Tests (web apps only) ---
        if (runtime.is_web_app
                and self.config.e2e_enabled
                and results.build_success
                and e2e_test_commands):
            logger.info("Sandbox E2E phase: running Playwright tests")
            e2e_image = self._resolve_e2e_image(runtime)
            e2e_results = self._run_e2e_container(
                image=e2e_image,
                dev_server_command=runtime.dev_server_command or "npm start",
                dev_server_port=runtime.dev_server_port,
                e2e_commands=e2e_test_commands,
                workspace_path=workspace,
                timeout=self.config.timeout_e2e,
            )
            if e2e_results:
                r = e2e_results[0]
                results.e2e_output = r.stdout + ("\n" + r.stderr if r.stderr else "")
                passed, total = self._parse_test_counts(r.stdout + r.stderr)
                results.e2e_tests_passed = passed
                results.e2e_tests_total = total
                if r.timed_out:
                    results.errors.append(f"E2E tests timed out after {self.config.timeout_e2e}s")
            self._notify_phase(on_phase_complete, "e2e", e2e_results, e2e_test_commands)

        return results

    @staticmethod
    def _parse_coverage(output: str) -> Optional[float]:
        """Extract total coverage %% from a pytest-cov term report (None if absent)."""
        m = re.search(r"^TOTAL\s+\d+\s+\d+(?:\s+\d+\s+\d+)?\s+(\d+(?:\.\d+)?)%", output, re.MULTILINE)
        if m:
            return float(m.group(1))
        return None

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

    # ------------------------------------------------------------------
    # E2E verification (Active Specification Alignment)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_e2e_image(runtime: RuntimeInfo) -> str:
        """Choose the Playwright Docker image based on runtime language."""
        if runtime.language == "python":
            return "mcr.microsoft.com/playwright/python:v1.49.1-noble"
        return "mcr.microsoft.com/playwright:v1.49.1-noble"

    def _run_e2e_container(
        self,
        image: str,
        dev_server_command: str,
        dev_server_port: int,
        e2e_commands: List[str],
        workspace_path: str,
        timeout: int,
    ) -> List[CommandResult]:
        """Run E2E tests in an ephemeral container with a dev server.

        Unlike _run_container, this method:
          1. Starts a dev server in the background
          2. Waits for the port to become available
          3. Runs E2E test commands (install Playwright, run tests)
          4. Kills the dev server

        The container runs with network_mode='none' because the dev
        server and Chromium browser are both inside the container and
        communicate over localhost.

        Security constraints match the standard sandbox (cap_drop=ALL,
        no-new-privileges, tmpfs) but memory limit is higher to
        accommodate the browser process.
        """
        if not e2e_commands:
            return []

        # Build a shell script that manages the dev server lifecycle
        # and then runs the E2E test commands
        script_lines = [
            "#!/bin/sh",
            "set -e",
            "cd /workspace",
            "",
            "# Start dev server in background",
            f"echo '=== STARTING DEV SERVER: {dev_server_command} ==='",
            f"({dev_server_command}) > /tmp/devserver.log 2>&1 &",
            "DEV_PID=$!",
            "",
            "# Wait for port to become available (up to 30s)",
            f"echo '=== WAITING FOR PORT {dev_server_port} ==='",
            "WAITED=0",
            "while [ $WAITED -lt 30 ]; do",
            f"  if sh -c \"echo > /dev/tcp/127.0.0.1/{dev_server_port}\" 2>/dev/null; then",
            "    echo 'Dev server is ready'",
            "    break",
            "  fi",
            "  sleep 1",
            "  WAITED=$((WAITED + 1))",
            "done",
            "",
            "if [ $WAITED -ge 30 ]; then",
            "  echo 'ERROR: Dev server failed to start within 30s'",
            "  echo '--- Dev server log ---'",
            "  cat /tmp/devserver.log 2>/dev/null || true",
            "  kill $DEV_PID 2>/dev/null || true",
            "  exit 1",
            "fi",
            "",
        ]

        # Add E2E test commands
        for cmd in e2e_commands:
            script_lines.append(f"echo '=== RUNNING: {cmd} ==='")
            script_lines.append(cmd)

        # Cleanup
        script_lines.extend([
            "",
            "# Cleanup",
            "E2E_EXIT=$?",
            "kill $DEV_PID 2>/dev/null || true",
            "exit $E2E_EXIT",
        ])

        script = "\n".join(script_lines)

        container = None
        results: List[CommandResult] = []
        t0 = time.time()
        try:
            # Ensure the Playwright image is available
            self._ensure_image(image)

            container = self.client.containers.create(
                image=image,
                command=["sh", "-c", script],
                working_dir="/workspace",
                # Resource limits (higher memory for browser)
                mem_limit=self.config.memory_limit_e2e,
                nano_cpus=int(self.config.cpu_limit * 1e9),
                pids_limit=self.config.pids_limit,
                # Security
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={
                    "/tmp": f"size={self.config.tmpfs_size}",
                    "/root": f"size={self.config.tmpfs_size}",
                },
                # Network: none — dev server and browser communicate via localhost
                network_mode="none",
                # Lifecycle
                auto_remove=False,
                detach=True,
                labels={"code-tumbler.role": "sandbox", "code-tumbler.phase": "e2e"},
                # Playwright needs /dev/shm for shared memory
                shm_size="256m",
            )

            # Copy project files (including generated E2E tests) into container
            tar_data = self._make_tar(workspace_path)
            container.put_archive("/workspace", tar_data)

            # Start and wait
            container.start()
            exit_info = container.wait(timeout=timeout)
            elapsed = time.time() - t0
            exit_code = exit_info.get("StatusCode", -1)

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            # Truncate
            max_output = 50_000
            if len(stdout) > max_output:
                stdout = stdout[:max_output] + f"\n\n[... truncated at {max_output} chars ...]"
            if len(stderr) > max_output:
                stderr = stderr[:max_output] + f"\n\n[... truncated at {max_output} chars ...]"

            results.append(CommandResult(
                command="e2e-verification",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_s=elapsed,
            ))

            logger.info(
                f"Sandbox [e2e]: exit={exit_code}, "
                f"time={elapsed:.1f}s, stdout={len(stdout)} chars"
            )

        except Exception as e:
            elapsed = time.time() - t0
            error_msg = str(e)
            timed_out = "timed out" in error_msg.lower() or "read timeout" in error_msg.lower()

            if timed_out:
                logger.warning(f"Sandbox [e2e]: timed out after {timeout}s")
                if container:
                    try:
                        container.kill()
                    except Exception:
                        pass

            results.append(CommandResult(
                command="e2e-verification",
                exit_code=-1,
                stdout="",
                stderr=f"E2E container execution failed: {error_msg}",
                timed_out=timed_out,
                duration_s=elapsed,
            ))

        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to remove E2E sandbox container: {cleanup_err}")

        return results
