"""Rules Ledger — persistent global + per-project rules and auto-detected candidates."""

from .ledger import (
    RulesLedger,
    Rule,
    Candidate,
    CATEGORIES,
    MAX_INJECTED_RULES,
    MAX_INJECTED_CHARS,
)

__all__ = [
    "RulesLedger",
    "Rule",
    "Candidate",
    "CATEGORIES",
    "MAX_INJECTED_RULES",
    "MAX_INJECTED_CHARS",
]
