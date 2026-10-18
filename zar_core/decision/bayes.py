"""Exact inference for small binary Bayesian networks (interface proof; swap for a library later)."""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product


@dataclass
class BinaryNode:
    name: str
    parents: list[str]
    cpt: dict[tuple[bool, ...], float]   # parent truth tuple -> P(node=True); root uses ()


class BayesianNetwork:
    def __init__(self, nodes: list[BinaryNode]) -> None:
        self.nodes, self.by_name, known = nodes, {n.name: n for n in nodes}, set()
        for n in nodes:
            if any(p not in known for p in n.parents):
                raise ValueError("Nodes must be supplied in topological order")
            known.add(n.name)

    def _p(self, name: str, value: bool, a: dict[str, bool]) -> float:
        n = self.by_name[name]
        pt = n.cpt[tuple(a[p] for p in n.parents)]
        return pt if value else 1 - pt

    def _joint(self, a: dict[str, bool]) -> float:
        prob = 1.0
        for n in self.nodes:
            prob *= self._p(n.name, a[n.name], a)
        return prob

    def query(self, variable: str, evidence: dict[str, bool]) -> float:
        hidden = [n.name for n in self.nodes if n.name not in evidence and n.name != variable]
        totals = {True: 0.0, False: 0.0}
        for target in (True, False):
            for bits in product((False, True), repeat=len(hidden)):
                totals[target] += self._joint({**evidence, variable: target, **dict(zip(hidden, bits))})
        denom = totals[True] + totals[False]
        return totals[True] / denom if denom else 0.0
