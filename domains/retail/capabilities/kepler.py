"""Kepler - pricing. Versioned rules + budget-aware option selection. Numbers never come from a model."""
from __future__ import annotations
from zar_core.contracts import CapabilityRequest, CapabilityResult, Evidence
from zar_core.decision.optimization import ConstraintOptimizer
from ..pricing import calculate


def run(req: CapabilityRequest) -> CapabilityResult:
    spec, preferred = req.payload["spec"], req.payload["print_method"]
    allowed = req.payload.get("allowed_methods") or [preferred]
    budget = int(spec.get("budget") or 0)
    options = [calculate(spec, m) for m in dict.fromkeys([preferred, *allowed])]
    within = (lambda o: o["total"] <= budget) if budget else (lambda o: True)
    primary = options[0]
    if within(primary):
        selected, reason = primary, "Recommended print method is within budget." if budget else \
            "Recommended print method (no budget stated)."
    else:
        cheaper = ConstraintOptimizer().choose(options, [within], lambda o: o["total"])
        selected = cheaper or primary
        reason = (f"Recommended {preferred} quote exceeds the budget; {selected['method']} fits within it."
                  if cheaper else "No allowed print method fits the budget; showing the recommended option.")
    return CapabilityResult(
        request_id=req.request_id, capability=req.capability, status="ok",
        data={"quote": selected, "recommended": primary, "options": options, "budget": budget,
              "within_budget": within(selected), "reason": reason},
        evidence=[Evidence(key="quote.total", value=selected["total"], source="kepler:rules")],
        confidence=1.0, model_used="rules-only",
        debug={"formula": "unit = round((garment + print + personalisation) x tier); total = unit x qty + setup",
               "budget_constraint": f"total <= {budget}" if budget else "none",
               "options_considered": [{"method": o["method"], "unit": o["unit_price"], "total": o["total"],
                                       "within_budget": within(o)} for o in options],
               "policy_file": "pricing.yaml"})
