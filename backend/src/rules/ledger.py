"""Rules Ledger.

A persistent ledger of rules the agents should follow, plus auto-detected
candidates surfaced for human review.

Two clearly separated paths (this separation is deliberate):

- **Rules** (`source` = manual | seed | promoted) are injected into the
  Architect and Engineer prompts as normative "Rules & Lessons Learned". They
  exist at two scopes: **global** (all projects) and **project** (one project).

- **Candidates** are auto-detected from sandbox failure output. They are
  surfaced in the UI but **never injected** until a human promotes one into a
  rule. This avoids turning an infrastructure failure into an agent instruction
  (e.g. "add pytest to requirements.txt" when the Engineer already did and the
  real bug was in the sandbox).

Storage (JSON, filesystem-primary):
- Global rules:      <workspace>/.rules/global.json
- Project rules:     <project>/.tumbler/rules.json
- Project candidates:<project>/.tumbler/candidates.json
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

CATEGORIES = ["dependencies", "testing", "linting", "build", "environment", "general"]

# Bound how much of the ledger is injected into a prompt (it shares the same
# message that carries the spec suite and is subject to truncation).
MAX_INJECTED_RULES = 25
MAX_INJECTED_CHARS = 3000

# Modules the sandbox itself provides — never flag these as project-dependency
# problems (that was the exact false positive we want to avoid).
_INFRA_PROVIDED = {"pytest", "flake8", "_pytest", "pluggy", "iniconfig"}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class Rule:
    id: str
    scope: str            # "global" | "project"
    category: str
    text: str
    source: str = "manual"  # manual | seed | promoted
    enabled: bool = True
    created_at: str = field(default_factory=_now)

    @staticmethod
    def new(text: str, scope: str, category: str = "general", source: str = "manual") -> "Rule":
        return Rule(id=uuid.uuid4().hex[:12], scope=scope,
                    category=category if category in CATEGORIES else "general",
                    text=text.strip(), source=source)


@dataclass
class Candidate:
    id: str
    signature: str        # which detector matched
    category: str
    evidence: str         # short snippet of the matching output
    suggested_text: str   # proposed rule text
    count: int = 1
    first_seen: str = field(default_factory=_now)
    last_seen: str = field(default_factory=_now)


# ── Auto-detect signatures → candidates (NOT auto-injected) ────────────────────
# Each: name, category, compiled regex (group 1 = subject), suggestion template.
_SIGNATURES = [
    ("py-missing-module", "dependencies",
     re.compile(r"(?:ModuleNotFoundError:\s*)?No module named ['\"]?([A-Za-z0-9_.\-]+)"),
     "A Python import failed for '{0}'. Ensure it is declared in requirements.txt "
     "(or pyproject dependencies) so the sandbox installs it."),
    ("node-missing-module", "dependencies",
     re.compile(r"Cannot find module ['\"]([^'\"]+)['\"]"),
     "Node could not resolve module '{0}'. Add it to package.json dependencies "
     "and ensure the import path is correct."),
    ("pip-no-version", "dependencies",
     re.compile(r"(?:Could not find a version|No matching distribution).*?for ([A-Za-z0-9_.\-]+)"),
     "pip could not resolve '{0}'. Pin a valid, existing version in requirements.txt."),
    ("cmd-not-found", "environment",
     re.compile(r"([A-Za-z0-9_\-./]+):\s*(?:command )?not found"),
     "Command '{0}' is not available in the sandbox runtime image. Use a tool the "
     "runtime provides, or install it as a project dependency."),
    ("py-syntax-error", "build",
     re.compile(r"SyntaxError: (.+)"),
     "A Python SyntaxError was hit ({0}). Ensure generated code is valid and runs."),
]


class RulesLedger:
    """Read/write access to global + per-project rules and candidates."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root)
        self.global_dir = self.workspace_root / ".rules"
        self.global_file = self.global_dir / "global.json"

    # ── low-level json ────────────────────────────────────────────────────────
    @staticmethod
    def _read(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read ledger file %s: %s", path, e)
            return []

    @staticmethod
    def _write(path: Path, items: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    # ── global rules ──────────────────────────────────────────────────────────
    def get_global_rules(self) -> List[Rule]:
        self._maybe_seed()
        return [Rule(**r) for r in self._read(self.global_file)]

    def set_global_rules(self, rules: List[Rule]) -> None:
        self._write(self.global_file, [asdict(r) for r in rules])

    def add_global_rule(self, text: str, category: str = "general") -> Rule:
        rules = self.get_global_rules()
        rule = Rule.new(text, "global", category, source="manual")
        rules.append(rule)
        self.set_global_rules(rules)
        return rule

    def delete_global_rule(self, rule_id: str) -> bool:
        rules = self.get_global_rules()
        remaining = [r for r in rules if r.id != rule_id]
        if len(remaining) == len(rules):
            return False
        self.set_global_rules(remaining)
        return True

    # ── project rules ─────────────────────────────────────────────────────────
    @staticmethod
    def _project_rules_file(project_path: Path) -> Path:
        return project_path / ".tumbler" / "rules.json"

    @staticmethod
    def _project_candidates_file(project_path: Path) -> Path:
        return project_path / ".tumbler" / "candidates.json"

    def get_project_rules(self, project_path: Path) -> List[Rule]:
        return [Rule(**r) for r in self._read(self._project_rules_file(project_path))]

    def set_project_rules(self, project_path: Path, rules: List[Rule]) -> None:
        self._write(self._project_rules_file(project_path), [asdict(r) for r in rules])

    def add_project_rule(self, project_path: Path, text: str, category: str = "general",
                         source: str = "manual") -> Rule:
        rules = self.get_project_rules(project_path)
        rule = Rule.new(text, "project", category, source=source)
        rules.append(rule)
        self.set_project_rules(project_path, rules)
        return rule

    def delete_project_rule(self, project_path: Path, rule_id: str) -> bool:
        rules = self.get_project_rules(project_path)
        remaining = [r for r in rules if r.id != rule_id]
        if len(remaining) == len(rules):
            return False
        self.set_project_rules(project_path, remaining)
        return True

    # ── candidates (auto-detected, not injected) ──────────────────────────────
    def get_candidates(self, project_path: Path) -> List[Candidate]:
        return [Candidate(**c) for c in self._read(self._project_candidates_file(project_path))]

    def _set_candidates(self, project_path: Path, cands: List[Candidate]) -> None:
        self._write(self._project_candidates_file(project_path), [asdict(c) for c in cands])

    def detect_and_record(self, project_path: Path, output: str) -> List[Candidate]:
        """Scan sandbox output for known failure signatures and upsert candidates.

        De-duplicates by (signature, subject); repeat hits bump `count`.
        Returns the candidates touched this call.
        """
        if not output:
            return []
        cands = self.get_candidates(project_path)
        by_key = {(c.signature, c.suggested_text): c for c in cands}
        touched: List[Candidate] = []

        for name, category, regex, template in _SIGNATURES:
            for m in regex.finditer(output):
                subject = m.group(1).strip() if m.groups() else ""
                if name == "py-missing-module" and subject in _INFRA_PROVIDED:
                    continue  # sandbox provides these; not an agent-actionable rule
                suggested = template.format(subject) if "{0}" in template else template
                key = (name, suggested)
                snippet = output[max(0, m.start() - 20): m.end() + 40].replace("\n", " ").strip()
                existing = by_key.get(key)
                if existing:
                    existing.count += 1
                    existing.last_seen = _now()
                    existing.evidence = snippet[:200]
                    touched.append(existing)
                else:
                    c = Candidate(id=uuid.uuid4().hex[:12], signature=name,
                                  category=category, evidence=snippet[:200],
                                  suggested_text=suggested)
                    cands.append(c)
                    by_key[key] = c
                    touched.append(c)

        if touched:
            self._set_candidates(project_path, cands)
            logger.info("Rules ledger: recorded %d candidate signal(s) for %s",
                        len(touched), project_path.name)
        return touched

    def promote_candidate(self, project_path: Path, candidate_id: str,
                          scope: str = "project", text: Optional[str] = None) -> Optional[Rule]:
        """Turn a candidate into an (injected) rule, then drop it from candidates."""
        cands = self.get_candidates(project_path)
        cand = next((c for c in cands if c.id == candidate_id), None)
        if not cand:
            return None
        rule_text = (text or cand.suggested_text).strip()
        if scope == "global":
            rule = self.add_global_rule(rule_text, cand.category)
            rule.source = "promoted"
            rules = self.get_global_rules()
            for r in rules:
                if r.id == rule.id:
                    r.source = "promoted"
            self.set_global_rules(rules)
        else:
            rule = self.add_project_rule(project_path, rule_text, cand.category, source="promoted")
        # remove the promoted candidate
        self._set_candidates(project_path, [c for c in cands if c.id != candidate_id])
        return rule

    def dismiss_candidate(self, project_path: Path, candidate_id: str) -> bool:
        cands = self.get_candidates(project_path)
        remaining = [c for c in cands if c.id != candidate_id]
        if len(remaining) == len(cands):
            return False
        self._set_candidates(project_path, remaining)
        return True

    # ── injection ─────────────────────────────────────────────────────────────
    def get_effective_rules(self, project_path: Optional[Path]) -> List[Rule]:
        """Enabled global + project rules (global first)."""
        rules = [r for r in self.get_global_rules() if r.enabled]
        if project_path is not None:
            rules += [r for r in self.get_project_rules(project_path) if r.enabled]
        return rules

    def render_for_prompt(self, project_path: Optional[Path]) -> Optional[str]:
        """Format enabled rules for injection into an agent prompt.

        Capped at MAX_INJECTED_RULES / MAX_INJECTED_CHARS so the ledger can't
        grow unbounded into the (truncation-prone) context window.
        """
        rules = self.get_effective_rules(project_path)
        if not rules:
            return None
        lines: List[str] = []
        used = 0
        for r in rules[:MAX_INJECTED_RULES]:
            line = f"- [{r.category}] {r.text}"
            if used + len(line) > MAX_INJECTED_CHARS:
                lines.append(f"- (+{len(rules) - len(lines)} more rules omitted to fit context)")
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    # ── seed ──────────────────────────────────────────────────────────────────
    def _maybe_seed(self) -> None:
        """Seed a small set of sane global rules on first use."""
        if self.global_file.exists():
            return
        seed = [
            Rule.new("Every generated project must include a working test suite that "
                     "actually exercises the code, and the test runner's dependencies "
                     "must be declared (e.g. pytest in requirements.txt).",
                     "global", "testing", source="seed"),
            Rule.new("Pin dependency versions you rely on; do not import packages that "
                     "are not declared in the project's dependency manifest.",
                     "global", "dependencies", source="seed"),
            Rule.new("Never hardcode secrets. Reference them by name via environment "
                     "variables and document the required variables.",
                     "global", "general", source="seed"),
        ]
        self.set_global_rules(seed)
        logger.info("Rules ledger: seeded %d global rules", len(seed))
