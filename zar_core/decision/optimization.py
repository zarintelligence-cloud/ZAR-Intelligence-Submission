from __future__ import annotations
from collections.abc import Callable
from typing import Any


class ConstraintOptimizer:
    """Feasible candidate with minimum score. Same adapter can wrap OR-Tools later."""

    def choose(self, candidates: list[dict[str, Any]], constraints: list[Callable[[dict], bool]],
               score: Callable[[dict], float]) -> dict[str, Any] | None:
        feasible = [c for c in candidates if all(rule(c) for rule in constraints)]
        return min(feasible, key=score) if feasible else None
