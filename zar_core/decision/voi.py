"""Value-of-information helpers: decide which questions are worth asking."""
from __future__ import annotations
import math
from typing import Any


def entropy(p: float) -> float:
    if p <= 0 or p >= 1:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def rank_questions(current_probability: float,
                   expected_probabilities: dict[str, tuple[float, float]]) -> list[tuple[str, float]]:
    """Heuristic expected information gain for a binary belief (yes/no equally likely)."""
    before = entropy(current_probability)
    scored = [(q, before - (0.5 * entropy(py) + 0.5 * entropy(pn)))
              for q, (py, pn) in expected_probabilities.items()]
    return sorted(scored, key=lambda x: x[1], reverse=True)


def select_questions(missing: list[str], fields: dict[str, dict[str, Any]],
                     max_questions: int, threshold: float) -> list[dict[str, Any]]:
    """Essential gaps are always asked; optional gaps are asked only when the expected
    loss of silently assuming the default (p_default_wrong x cost_if_wrong) exceeds
    the threshold. Output is capped and ordered: essentials first, then by loss."""
    essential, optional = [], []
    for name in missing:
        f = fields.get(name)
        if not f:
            continue
        if f.get("essential"):
            essential.append({"field": name, "question": f["question"], "expected_loss": 1.0})
        else:
            loss = float(f.get("p_default_wrong", 0)) * float(f.get("cost_if_wrong", 0))
            if loss >= threshold:
                optional.append({"field": name, "question": f["question"], "expected_loss": round(loss, 3)})
    optional.sort(key=lambda x: x["expected_loss"], reverse=True)
    return (essential + optional)[:max_questions]
