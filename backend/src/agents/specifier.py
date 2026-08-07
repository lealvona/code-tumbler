"""Specifier Agent — Phase 1 of the Code Tumbler lifecycle.

Turns a one-line app idea into a structured, ordered multi-file YAML
specification suite (the Step-3 meta-spec registry) that the Architect then
consumes. Output is a single JSON envelope:

    {"files": [{"path": "spec/00-base.yaml", "content": "...", "guide": {...}}, ...]}

The suite is written under the project root (paths are literal, e.g.
`spec/00-base.yaml`), and a `code-tumbler-spec-archive` JSON is written to
`.tumbler/spec_archive.json` for export/import.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

# The interchange discriminator for exported/imported spec archives.
SPEC_ARCHIVE_FORMAT = "code-tumbler-spec-archive"

# Fixed registry files that must be present for the spec to count as "complete".
# (Per-source files under spec/sources/<id>.yaml are variable and not required
# for the completion gate beyond the template.)
REQUIRED_SPEC_FILES = (
    "spec/00-base.yaml",
    "spec/10-product.yaml",
    "spec/20-architecture.yaml",
    "spec/30-ui.yaml",
    "spec/40-interchange.yaml",
    "spec/50-agent-bootstrap.yaml",
    "spec/60-glitz.yaml",
    "spec/sources/_template.yaml",
)


class SpecifierAgent(BaseAgent):
    """Generates the YAML specification suite from a free-text idea."""

    # The suite is large; give it a generous output budget.
    default_max_tokens: Optional[int] = 16384

    def __init__(self, provider, system_prompt_path: Path = None,
                 nothink_override: Optional[bool] = None):
        if system_prompt_path is None:
            backend_dir = Path(__file__).parent.parent.parent
            system_prompt_path = backend_dir / "prompts" / "specifier_system.txt"
        system_prompt = self._load_prompt(system_prompt_path)
        super().__init__(provider, system_prompt, name="Specifier",
                         nothink_override=nothink_override)

    @staticmethod
    def _load_prompt(path: Path) -> str:
        if not path.exists():
            return (
                "You are the Specifier. Turn the app idea into a JSON envelope "
                '{"files":[{"path","content","guide"}]} of ordered spec/*.yaml files.'
            )
        return path.read_text(encoding="utf-8")

    def _build_messages(self, context: Dict[str, Any]) -> List[Dict[str, str]]:
        idea = context.get("idea", "")
        project_name = context.get("project_name", "")
        user = f"""# App idea

{idea}
"""
        if project_name:
            user += (
                f"\nA working directory name of `{project_name}` was suggested, but "
                f"you should invent a fitting project name and derive your own "
                f"kebab-case slug from the idea.\n"
            )
        user += (
            "\n# Your task\n\nProduce the complete specification suite as a single "
            "JSON envelope exactly as described in your system prompt. Return ONLY "
            "the JSON object — no prose, no markdown fences."
        )
        correction = context.get("format_correction")
        if correction:
            user += f"\n\n# FORMAT CORRECTION (mandatory)\n\n{correction}"
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user},
        ]

    # ── parsing ──────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_envelope(text: str) -> List[Dict[str, Any]]:
        """Parse the model output into a list of file objects.

        Tolerant of markdown fences and leading/trailing prose.
        """
        cleaned = text.strip()
        # Strip a leading ```json / ``` fence if present.
        fence = re.match(r"^```(?:json)?\s*\n(.*)\n```$", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1).strip()

        # Strategy 1: strict parse.
        obj = None
        try:
            obj = json.loads(cleaned)
        except json.JSONDecodeError:
            # Strategy 2: extract the outermost {...} containing "files".
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                try:
                    obj = json.loads(cleaned[start:end + 1])
                except json.JSONDecodeError:
                    obj = None

        if not isinstance(obj, dict):
            raise ValueError("Specifier output did not contain a valid {\"files\": [...]} envelope")

        files = obj.get("files")
        # Dialect: some models name the envelope key after the artifact.
        if files is None:
            for alt in ("spec", "specs", "specifications"):
                if isinstance(obj.get(alt), (list, dict)):
                    files = obj[alt]
                    break

        # Dialect: {"files": {path: content}} — files as a map.
        if isinstance(files, dict):
            files = [{"path": p, "content": c} for p, c in files.items()
                     if isinstance(p, str) and isinstance(c, str)]

        # Dialect: flat {path: content} map at the top level. Small local
        # models produce this valid-but-differently-shaped JSON reliably;
        # rejecting 25k chars of good spec content over envelope shape wastes
        # an entire (slow) generation.
        if files is None:
            flat = [(k, v) for k, v in obj.items()
                    if isinstance(k, str) and isinstance(v, str)
                    and ("/" in k or k.endswith((".yaml", ".yml", ".md")))]
            if flat and len(flat) >= max(1, int(0.5 * len(obj))):
                files = [{"path": p, "content": c} for p, c in flat]

        if not isinstance(files, list) or not files:
            raise ValueError("Specifier output did not contain a valid {\"files\": [...]} envelope")
        return files

    # ── generation ───────────────────────────────────────────────────────────
    def generate_spec(
        self,
        idea: str,
        project_name: str,
        project_root: Path,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate the spec suite, write files under `project_root`, and write
        the export archive to `.tumbler/spec_archive.json`.

        Returns a dict: {"files": {path: content}, "guides": {path: guide},
        "archive": <archive dict>, "project": <project name from meta>}.
        """
        context = {"idea": idea, "project_name": project_name}

        # Only OpenAI/vLLM support the JSON response_format hint; others (claude_cli,
        # anthropic, gemini, ollama) rely on the system prompt's instruction.
        if self.provider.config.type.value in ("openai", "vllm"):
            kwargs.setdefault("response_format", {"type": "json_object"})

        raw = self.execute(context, **kwargs)
        try:
            file_objs = self._parse_envelope(raw)
        except ValueError:
            # One corrective retry: tell the model exactly what shape came
            # back and what is required. Small models usually comply on the
            # second attempt; without this, a malformed envelope costs the
            # whole (slow, local) generation.
            logger.warning("Specifier envelope parse failed — retrying with "
                           "format correction (got %d chars)", len(raw))
            retry_ctx = dict(context)
            retry_ctx["format_correction"] = (
                "Your previous attempt was NOT the required shape. It began "
                f"with: {raw[:200]!r}\n"
                "You MUST return a single JSON object with EXACTLY one top-level "
                'key "files", whose value is an ARRAY of objects, each with '
                '"path" and "content" string fields:\n'
                '{"files": [{"path": "spec/00-base.yaml", "content": "..."}, ...]}\n'
                "Do NOT use file paths as top-level keys."
            )
            raw = self.execute(retry_ctx, **kwargs)
            file_objs = self._parse_envelope(raw)

        files: Dict[str, str] = {}
        guides: Dict[str, Any] = {}
        archive_files: List[Dict[str, Any]] = []
        for fo in file_objs:
            path = (fo.get("path") or "").strip().lstrip("/")
            content = fo.get("content", "")
            if not path or not isinstance(content, str):
                continue
            # Containment: only allow paths under spec/.
            if not (path == "spec" or path.startswith("spec/")):
                logger.warning("Specifier produced out-of-scope path %r, skipping", path)
                continue
            files[path] = content
            guides[path] = fo.get("guide", {})
            archive_files.append({"path": path, "content": content, "guide": fo.get("guide", {})})

        if not files:
            raise ValueError("Specifier produced no valid spec files")

        # Write spec files under the project root.
        for path, content in files.items():
            dest = (project_root / path).resolve()
            root_resolved = project_root.resolve()
            if not str(dest).startswith(str(root_resolved)):
                logger.warning("Refusing to write spec file outside project root: %s", dest)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        project_label = self._read_project_name(files) or project_name
        archive = {
            "format": SPEC_ARCHIVE_FORMAT,
            "format_version": 1,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "spec_version": "0.1.0",
            "session": {"name": project_label, "description": idea},
            "files": archive_files,
        }
        archive_path = project_root / ".tumbler" / "spec_archive.json"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(json.dumps(archive, indent=2), encoding="utf-8")

        logger.info("Specifier wrote %d spec files (project=%s)", len(files), project_label)
        return {"files": files, "guides": guides, "archive": archive, "project": project_label}

    @staticmethod
    def _read_project_name(files: Dict[str, str]) -> Optional[str]:
        """Best-effort read of meta.project from spec/00-base.yaml."""
        base = files.get("spec/00-base.yaml", "")
        m = re.search(r"^\s*project:\s*[\"']?([^\"'\n]+)", base, re.MULTILINE)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def is_complete(project_root: Path) -> bool:
        """True if all required registry files exist under the project root."""
        return all((project_root / f).exists() for f in REQUIRED_SPEC_FILES)
