"""Claude CLI provider — drives the local `claude` binary (Claude Code) as a
pure text-completion backend.

This adapter shells out to the `claude` command in headless mode (`--print`),
using the account already authenticated on the host. It is the template for
other CLI-based providers (e.g. a future `codex` adapter): subclass or copy
`_build_argv` / the streaming parse and swap the binary + event schema.

Key properties
--------------
- **Host execution only.** The `claude` binary and its credentials live on the
  host, not in the backend container. If the binary is missing we fail fast with
  an actionable message rather than a subprocess timeout.
- **Tools disabled.** `claude -p` defaults to all tools enabled with auto
  permissions; we pass `--disallowed-tools` for the full built-in set and a
  restrictive `--permission-mode` so the model behaves as a text LLM and never
  touches the filesystem or shell.
- **Streaming.** `--output-format stream-json --verbose --include-partial-messages`
  yields token deltas which we surface incrementally (matching every other
  provider's `stream_chat`). Usage is read from the terminal `result` event.
- **Reasoning effort** is controlled via `--effort` (default `high`), configured
  through `extra_params.effort` in config.yaml.
"""

import json
import logging
import shutil
import subprocess
import threading
from typing import List, Dict, Optional, Iterator

from .base import LLMProvider, ProviderConfig

logger = logging.getLogger(__name__)

# Built-in Claude Code tools we explicitly disallow so the provider is text-only.
_DISALLOWED_TOOLS = [
    "Task", "Bash", "Edit", "Write", "Read", "NotebookEdit", "Glob", "Grep",
    "WebFetch", "WebSearch", "TodoWrite", "MultiEdit",
]

# Static model list (the CLI has no models endpoint). The configured model is
# always included; aliases resolve to the latest of each family.
_KNOWN_MODELS = [
    "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5",
    "opus", "sonnet", "haiku",
]


class ClaudeCLIAuthError(RuntimeError):
    """Raised when the `claude` CLI is present but not authenticated."""


class ClaudeCLIProvider(LLMProvider):
    """LLM provider that proxies to the local `claude` CLI in headless mode."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        extra = config.extra_params or {}
        self.binary: str = extra.get("binary", "claude")
        self.effort: str = extra.get("effort", "high")
        # permission_mode is opt-in: by default we don't pass it (tools are already
        # disabled via --disallowed-tools, so the model just answers). "plan" mode in
        # particular withholds a direct answer, which is wrong for a text backend.
        self.permission_mode: Optional[str] = extra.get("permission_mode")
        self.disallowed_tools: List[str] = extra.get("disallowed_tools", _DISALLOWED_TOOLS)
        # Extra raw args passed straight through (advanced/escape hatch).
        self.extra_cli_args: List[str] = extra.get("cli_args", [])
        self.model: str = config.model or "claude-opus-4-8"

    # ── argv construction (shared by chat/stream) ───────────────────────────
    def _build_argv(self, system_prompt: Optional[str], stream: bool,
                    max_tokens: Optional[int]) -> List[str]:
        argv = [self.binary, "--print", "--model", self.model,
                "--effort", self.effort]
        if self.permission_mode:
            argv += ["--permission-mode", self.permission_mode]
        if self.disallowed_tools:
            argv += ["--disallowed-tools", *self.disallowed_tools]
        if system_prompt:
            argv += ["--system-prompt", system_prompt]
        if stream:
            argv += ["--output-format", "stream-json", "--verbose",
                     "--include-partial-messages"]
        else:
            argv += ["--output-format", "json"]
        argv += self.extra_cli_args
        return argv

    @staticmethod
    def _split_messages(messages: List[Dict[str, str]]) -> tuple:
        """Split into (system_prompt, prompt_text).

        The CLI takes one prompt string, so non-system turns are flattened. For
        the common [system, user] shape this is just the user content; multi-turn
        conversations are labelled by role.
        """
        system_parts: List[str] = []
        convo: List[str] = []
        non_system = [m for m in messages if m.get("role") != "system"]
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
        for m in non_system:
            role = m.get("role", "user")
            content = m.get("content", "")
            if len(non_system) == 1:
                convo.append(content)
            else:
                label = {"user": "User", "assistant": "Assistant", "tool": "Tool"}.get(role, role.title())
                convo.append(f"{label}: {content}")
        system_prompt = "\n\n".join(p for p in system_parts if p).strip() or None
        prompt = "\n\n".join(convo).strip()
        return system_prompt, prompt

    def _run(self, argv: List[str], prompt: str) -> subprocess.Popen:
        try:
            return subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"`{self.binary}` CLI not found on PATH. The claude_cli provider "
                f"requires host execution (the binary + credentials are not "
                f"available inside the backend container)."
            ) from exc

    @staticmethod
    def _is_auth_error(text: str) -> bool:
        t = (text or "").lower()
        return "oauth" in t or "authenticate" in t or "not logged in" in t or "log in" in t

    # ── streaming ────────────────────────────────────────────────────────────
    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Iterator[str]:
        """Stream a completion via `claude -p --output-format stream-json`.

        Yields text deltas. Unknown provider kwargs (e.g. ``response_format``)
        are accepted and ignored — the CLI has no equivalent knob.
        """
        system_prompt, prompt = self._split_messages(messages)
        argv = self._build_argv(system_prompt, stream=True, max_tokens=max_tokens)
        proc = self._run(argv, prompt)

        saw_delta = False
        final_text_parts: List[str] = []
        input_tokens = output_tokens = 0
        err_result: Optional[str] = None

        # Write the prompt from a separate thread so a large prompt (the Architect
        # sends the whole spec suite — tens of KB) can't deadlock against a full
        # stdout pipe that we haven't started draining yet.
        def _write_stdin():
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (BrokenPipeError, ValueError):
                pass

        writer = threading.Thread(target=_write_stdin, daemon=True)
        writer.start()

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")
                if etype == "stream_event":
                    inner = evt.get("event", {})
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                saw_delta = True
                                yield text
                elif etype == "assistant" and not saw_delta:
                    # Full assistant message (no partials available) — emit once.
                    for block in evt.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            txt = block.get("text", "")
                            if txt:
                                final_text_parts.append(txt)
                                yield txt
                elif etype == "result":
                    usage = evt.get("usage", {}) or {}
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    if evt.get("is_error"):
                        err_result = evt.get("result") or evt.get("error") or "unknown error"
            proc.wait(timeout=self.config.timeout)
        finally:
            if proc.poll() is None:
                proc.kill()
            writer.join(timeout=1)

        stderr = proc.stderr.read() if proc.stderr else ""
        if err_result is not None:
            if self._is_auth_error(err_result):
                raise ClaudeCLIAuthError(
                    f"claude CLI is not authenticated ({err_result}). Run `claude` "
                    f"in a terminal and log in, then retry."
                )
            raise RuntimeError(f"claude CLI error: {err_result}")
        if proc.returncode not in (0, None):
            if self._is_auth_error(stderr):
                raise ClaudeCLIAuthError(
                    "claude CLI is not authenticated. Run `claude` in a terminal "
                    "and log in, then retry."
                )
            raise RuntimeError(
                f"claude CLI exited {proc.returncode}: {stderr[:500]}"
            )

        self._track_usage(input_tokens, output_tokens)

    # ── buffered ─────────────────────────────────────────────────────────────
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> str:
        """Non-streaming completion (collects the stream)."""
        return "".join(self.stream_chat(messages, temperature, max_tokens, **kwargs))

    def list_models(self) -> List[str]:
        models = list(_KNOWN_MODELS)
        if self.model not in models:
            models.insert(0, self.model)
        return models

    def health_check(self) -> bool:
        """Healthy if the CLI binary is resolvable on PATH.

        Note: this does not verify authentication (that would cost a real call);
        auth failures surface with a clear message at generation time.
        """
        if shutil.which(self.binary) is None:
            self._health_check_error = (
                f"`{self.binary}` not found on PATH (claude_cli needs host execution)"
            )
            return False
        return True
