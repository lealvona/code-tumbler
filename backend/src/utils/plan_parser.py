"""Parse architect plan output for structured metadata.

Extracts the optional '## Resource Requirements' section and converts
it to a dict suitable for verification_overrides in project state.
"""

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Fields the architect is allowed to set, with their expected types.
_RESOURCE_FIELDS: Dict[str, type] = {
    "timeout_install": int,
    "timeout_build": int,
    "timeout_test": int,
    "timeout_lint": int,
    "timeout_e2e": int,
    "memory_limit": str,
    "memory_limit_e2e": str,
    "cpu_limit": float,
    "tmpfs_size": str,
}


def extract_resource_requirements(plan_text: str) -> Dict[str, Any]:
    """Extract resource requirements from a PLAN.md.

    Looks for a '## Resource Requirements' section and parses key: value lines.
    Returns a dict of validated overrides (empty dict if section is absent or
    contains no valid entries).
    """
    # Find the Resource Requirements section
    pattern = r"##\s*Resource\s+Requirements.*?\n(.*?)(?=\n##|\Z)"
    match = re.search(pattern, plan_text, re.DOTALL | re.IGNORECASE)
    if not match:
        return {}

    section = match.group(1)
    overrides: Dict[str, Any] = {}

    for field, expected_type in _RESOURCE_FIELDS.items():
        # Match lines like:  **timeout_build**: 300
        # or:                timeout_build: 300
        line_pattern = rf"(?:\*\*)?{re.escape(field)}(?:\*\*)?\s*:\s*(.+)"
        line_match = re.search(line_pattern, section, re.IGNORECASE)
        if not line_match:
            continue

        raw = line_match.group(1).strip().strip('"').strip("'")
        # Skip placeholder/template values
        if raw.startswith("[") or raw.startswith("default"):
            continue

        try:
            if expected_type is int:
                overrides[field] = int(raw)
            elif expected_type is float:
                overrides[field] = float(raw)
            else:
                overrides[field] = raw
        except (ValueError, TypeError):
            logger.debug("Could not parse resource field %s=%r", field, raw)

    if overrides:
        logger.info("Architect recommended resource overrides: %s", overrides)

    return overrides
