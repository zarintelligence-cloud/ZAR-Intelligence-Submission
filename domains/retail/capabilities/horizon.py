"""Horizon - fulfillment_confidence. Monte Carlo over production, waiting and shipping time."""
from __future__ import annotations
import hashlib
from datetime import date
from zar_core.contracts import CapabilityRequest, CapabilityResult, Evidence
from zar_core.decision.monte_carlo import MonteCarlo
from .. import policies


def run(req: CapabilityRequest) -> CapabilityResult:
    p, pol = req.payload, policies.fulfillment()
    today = date.fromisoformat(p.get("today") or date.today().isoformat())
    if not p.get("deadline"):
        return CapabilityResult(request_id=req.request_id, capability=req.capability, status="needs_input",
                                questions=["By which date do you need them delivered?"],
                                missing_evidence=["deadline"])
    available = (date.fromisoformat(p["deadline"]) - today).days
    qty, method = int(p["quantity"]), p["print_method"]
    extra = max(0, (qty - 1) // pol["extra_day_per_pieces"])
    prod = pol["production_days"][method]

    def simulate(rng) -> float:
        days = rng.triangular(prod[0], prod[2], prod[1]) + extra
        days += rng.triangular(*[pol["shipping_days"][i] for i in (0, 2, 1)])
        if p.get("roster_open", True):
            days += rng.triangular(*[pol["roster_wait_days"][i] for i in (0, 2, 1)])
        if not p.get("proof_approved", False):
            days += rng.triangular(*[pol["approval_wait_days"][i] for i in (0, 2, 1)])
        return days

    seed = int(hashlib.sha256(req.context.session_id.encode()).hexdigest()[:8], 16)
    sim = MonteCarlo().run(simulate, lambda d: d <= available, n=pol["simulations"], seed=seed)
    prob, th = round(sim["success_rate"], 3), pol["thresholds"]
    status = "on_track" if prob >= th["on_track"] else "at_risk" if prob >= th["at_risk"] else "unlikely"
    advice = []
    if status != "on_track":
        advice.append("Close the roster quickly - every waiting day comes out of the buffer.")
        if method != "dtf":
            advice.append("DTF has the shortest production time if the artwork allows it.")
        advice.append("Consider a later delivery date or split delivery.")
    return CapabilityResult(
        request_id=req.request_id, capability=req.capability, status="ok",
        data={"days_available": available, "p_on_time": prob, "status": status,
              "days_p50": round(sim["p50"], 1), "days_p80": round(sim["p80"], 1),
              "days_p90": round(sim["p90"], 1), "simulations": sim["runs"], "advice": advice},
        evidence=[Evidence(key="fulfillment.p_on_time", value=prob, source="horizon:monte_carlo",
                           confidence=0.7)],
        confidence=0.7, model_used="monte-carlo",
        debug={"seed": seed, "production_days": prod, "extra_days_for_quantity": extra,
               "roster_open": p.get("roster_open", True), "proof_approved": p.get("proof_approved", False),
               "mean_days": round(sim["mean"], 2), "policy_file": "fulfillment.yaml"})
