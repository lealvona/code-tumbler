"""Rubric data model and YAML parser for Agentic Specification Alignment.

The Architect generates a RUBRIC.yaml alongside PLAN.md containing structured,
verifiable checklist items.  The Verifier loads and grades these items against
verification results to measure specification completeness.

Categories:
  - static:     verifiable by reading code / build output
  - dynamic:    requires a running application (page renders, API responds)
  - behavioral: requires user interaction simulation (form submit, navigation)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


@dataclass
class RubricItem:
    """A single verifiable checklist item from the specification rubric."""

    id: str                          # e.g. "FUNC-001", "DYN-001", "BEH-001"
    category: str                    # "static" | "dynamic" | "behavioral"
    requirement: str                 # Human-readable requirement text
    check: str                       # How to verify this item
    priority: str                    # "critical" | "important" | "nice-to-have"
    verified: Optional[bool] = None  # Set after grading (True/False/None=ungraded)
    notes: Optional[str] = None      # Grading notes from the verifier


_VALID_CATEGORIES = {"static", "dynamic", "behavioral"}
_VALID_PRIORITIES = {"critical", "important", "nice-to-have"}


@dataclass
class Rubric:
    """Parsed specification rubric containing verifiable items."""

    items: List[RubricItem] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, yaml_text: str) -> "Rubric":
        """Parse RUBRIC.yaml content into a Rubric instance.

        Tolerant of minor formatting issues — skips malformed items rather
        than failing the entire parse.
        """
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            logger.warning("RUBRIC.yaml parse error: %s", exc)
            return cls(items=[])

        if not isinstance(data, dict):
            logger.warning("RUBRIC.yaml: expected top-level dict, got %s", type(data).__name__)
            return cls(items=[])

        raw_items = data.get("rubric", [])
        if not isinstance(raw_items, list):
            logger.warning("RUBRIC.yaml: 'rubric' key is not a list")
            return cls(items=[])

        items: List[RubricItem] = []
        for i, entry in enumerate(raw_items):
            if not isinstance(entry, dict):
                logger.debug("RUBRIC.yaml: skipping non-dict item at index %d", i)
                continue

            item_id = str(entry.get("id", f"ITEM-{i:03d}"))
            category = str(entry.get("category", "static")).lower().strip()
            requirement = str(entry.get("requirement", "")).strip()
            check = str(entry.get("check", "")).strip()
            priority = str(entry.get("priority", "important")).lower().strip()

            if not requirement:
                logger.debug("RUBRIC.yaml: skipping item %s with empty requirement", item_id)
                continue

            # Normalise to valid values
            if category not in _VALID_CATEGORIES:
                category = "static"
            if priority not in _VALID_PRIORITIES:
                priority = "important"

            items.append(RubricItem(
                id=item_id,
                category=category,
                requirement=requirement,
                check=check,
                priority=priority,
            ))

        logger.info("Parsed rubric with %d items (%d static, %d dynamic, %d behavioral)",
                     len(items),
                     sum(1 for it in items if it.category == "static"),
                     sum(1 for it in items if it.category == "dynamic"),
                     sum(1 for it in items if it.category == "behavioral"))
        return cls(items=items)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def dynamic_items(self) -> List[RubricItem]:
        """Return items that need a running application (dynamic + behavioral)."""
        return [it for it in self.items if it.category in ("dynamic", "behavioral")]

    def static_items(self) -> List[RubricItem]:
        """Return items that can be checked from code / build output."""
        return [it for it in self.items if it.category == "static"]

    def score_fraction(self) -> Tuple[int, int]:
        """Return (verified_count, total_count) for graded items."""
        graded = [it for it in self.items if it.verified is not None]
        verified = sum(1 for it in graded if it.verified)
        return verified, len(graded) if graded else len(self.items)

    def to_yaml(self) -> str:
        """Serialise the rubric back to YAML (for debugging / logging)."""
        entries = []
        for it in self.items:
            entry = {
                "id": it.id,
                "category": it.category,
                "requirement": it.requirement,
                "check": it.check,
                "priority": it.priority,
            }
            if it.verified is not None:
                entry["verified"] = it.verified
            if it.notes:
                entry["notes"] = it.notes
            entries.append(entry)
        return yaml.dump({"rubric": entries}, default_flow_style=False, sort_keys=False)
