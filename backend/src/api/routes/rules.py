"""Rules Ledger endpoints — global + per-project rules and auto-detected candidates."""

from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from rules import RulesLedger, Rule, CATEGORIES

router = APIRouter(tags=["rules"])


def _workspace(request: Request) -> Path:
    return Path(request.app.state.config.workspace.base_path).resolve()


def _ledger(request: Request) -> RulesLedger:
    return RulesLedger(_workspace(request))


def _project_dir(request: Request, name: str) -> Path:
    p = (_workspace(request) / name)
    if not p.exists() or not p.is_dir():
        raise HTTPException(404, f"Project '{name}' not found")
    return p


class RuleInput(BaseModel):
    text: str
    category: Optional[str] = "general"


class RuleItem(BaseModel):
    id: Optional[str] = None
    scope: Optional[str] = None
    category: str = "general"
    text: str
    source: Optional[str] = "manual"
    enabled: bool = True
    created_at: Optional[str] = None


class RulesReplace(BaseModel):
    rules: List[RuleItem]


class PromoteInput(BaseModel):
    candidate_id: str
    scope: str = "project"      # "project" | "global"
    text: Optional[str] = None


class DismissInput(BaseModel):
    candidate_id: str


def _to_rule(item: RuleItem, scope: str) -> Rule:
    r = Rule.new(item.text, scope, item.category or "general", source=item.source or "manual")
    if item.id:
        r.id = item.id
    if item.created_at:
        r.created_at = item.created_at
    r.enabled = item.enabled
    return r


# ── global rules ──────────────────────────────────────────────────────────────
@router.get("/rules")
async def get_global_rules(request: Request):
    led = _ledger(request)
    return {"categories": CATEGORIES, "rules": [asdict(r) for r in led.get_global_rules()]}


@router.post("/rules")
async def add_global_rule(body: RuleInput, request: Request):
    if not body.text.strip():
        raise HTTPException(400, "Rule text is required")
    rule = _ledger(request).add_global_rule(body.text, body.category or "general")
    return asdict(rule)


@router.put("/rules")
async def replace_global_rules(body: RulesReplace, request: Request):
    rules = [_to_rule(i, "global") for i in body.rules]
    _ledger(request).set_global_rules(rules)
    return {"status": "ok", "count": len(rules)}


@router.delete("/rules/{rule_id}")
async def delete_global_rule(rule_id: str, request: Request):
    if not _ledger(request).delete_global_rule(rule_id):
        raise HTTPException(404, "Rule not found")
    return {"status": "deleted", "id": rule_id}


# ── per-project rules + candidates ────────────────────────────────────────────
@router.get("/projects/{name}/rules")
async def get_project_rules(name: str, request: Request):
    led = _ledger(request)
    proj = _project_dir(request, name)
    return {
        "categories": CATEGORIES,
        "global_rules": [asdict(r) for r in led.get_global_rules()],
        "project_rules": [asdict(r) for r in led.get_project_rules(proj)],
        "effective": [asdict(r) for r in led.get_effective_rules(proj)],
        "candidates": [asdict(c) for c in led.get_candidates(proj)],
    }


@router.post("/projects/{name}/rules")
async def add_project_rule(name: str, body: RuleInput, request: Request):
    if not body.text.strip():
        raise HTTPException(400, "Rule text is required")
    proj = _project_dir(request, name)
    rule = _ledger(request).add_project_rule(proj, body.text, body.category or "general")
    return asdict(rule)


@router.put("/projects/{name}/rules")
async def replace_project_rules(name: str, body: RulesReplace, request: Request):
    proj = _project_dir(request, name)
    rules = [_to_rule(i, "project") for i in body.rules]
    _ledger(request).set_project_rules(proj, rules)
    return {"status": "ok", "count": len(rules)}


@router.delete("/projects/{name}/rules/{rule_id}")
async def delete_project_rule(name: str, rule_id: str, request: Request):
    proj = _project_dir(request, name)
    if not _ledger(request).delete_project_rule(proj, rule_id):
        raise HTTPException(404, "Rule not found")
    return {"status": "deleted", "id": rule_id}


@router.post("/projects/{name}/rules/promote")
async def promote_candidate(name: str, body: PromoteInput, request: Request):
    proj = _project_dir(request, name)
    if body.scope not in ("project", "global"):
        raise HTTPException(400, "scope must be 'project' or 'global'")
    rule = _ledger(request).promote_candidate(proj, body.candidate_id, body.scope, body.text)
    if rule is None:
        raise HTTPException(404, "Candidate not found")
    return {"status": "promoted", "rule": asdict(rule)}


@router.post("/projects/{name}/candidates/dismiss")
async def dismiss_candidate(name: str, body: DismissInput, request: Request):
    proj = _project_dir(request, name)
    if not _ledger(request).dismiss_candidate(proj, body.candidate_id):
        raise HTTPException(404, "Candidate not found")
    return {"status": "dismissed", "id": body.candidate_id}
