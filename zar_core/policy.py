"""Deterministic policy layer: constraint checks and first-match decision tables."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "exists": lambda a, b: (a is not None) == bool(b),
}


def check(facts: dict[str, Any], cond: dict[str, Any]) -> bool:
    op = _OPS.get(cond["op"])
    if op is None:
        raise ValueError(f"Unknown policy operator: {cond['op']}")
    return bool(op(facts.get(cond["field"]), cond.get("value")))


@dataclass
class PolicyResult:
    allowed: bool
    reasons: list[str]


class PolicyEngine:
    """All rules must pass. Replaceable later by OPA/Cedar behind the same interface."""

    def evaluate(self, facts: dict[str, Any], rules: list[dict[str, Any]]) -> PolicyResult:
        reasons = [r.get("message", f"Policy failed: {r['field']} {r['op']} {r.get('value')}")
                   for r in rules if not check(facts, r)]
        return PolicyResult(allowed=not reasons, reasons=reasons)


@dataclass
class TableResult:
    action: str
    rule_id: str
    reasons: list[str] = field(default_factory=list)


class DecisionTable:
    """Ordered rules; first rule whose conditions all hold wins; otherwise default."""

    def __init__(self, rules: list[dict[str, Any]], default: str, default_reason: str = "") -> None:
        self.rules, self.default, self.default_reason = rules, default, default_reason

    def decide(self, facts: dict[str, Any]) -> TableResult:
        for rule in self.rules:
            if all(check(facts, c) for c in rule.get("when", [])):
                return TableResult(rule["then"], rule["id"], [rule.get("reason", rule["id"])])
        return TableResult(self.default, "default", [self.default_reason or "No rule matched; default applied."])
