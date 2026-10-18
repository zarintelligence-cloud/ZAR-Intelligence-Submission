from zar_core.contracts import CapabilityRequest, CapabilityResult, DomainInfo, Evidence


def incident_triage(req: CapabilityRequest) -> CapabilityResult:
    m, causes = req.payload.get("metrics", {}), []
    if m.get("db_latency_ms", 0) > 500:
        causes.append(("database_latency", 0.8))
    if m.get("queue_depth", 0) > 1000:
        causes.append(("queue_backlog", 0.7))
    if m.get("error_rate", 0) > 0.05:
        causes.append(("application_errors", 0.6))
    causes.sort(key=lambda x: x[1], reverse=True)
    return CapabilityResult(request_id=req.request_id, capability=req.capability, status="ok",
                            data={"hypotheses": [{"cause": c, "score": s} for c, s in causes]},
                            evidence=[Evidence(key="triage_complete", value=True, source="saas.incident_triage")],
                            confidence=causes[0][1] if causes else 0.3)


def plan_recommendation(req: CapabilityRequest) -> CapabilityResult:
    usage, current = req.payload.get("monthly_usage", 0), req.payload.get("current_plan", "starter")
    plan = "enterprise" if usage > 1_000_000 else "growth" if usage > 100_000 else current
    return CapabilityResult(request_id=req.request_id, capability=req.capability, status="ok",
                            data={"recommended_plan": plan}, confidence=0.9)


def register(registry, info: DomainInfo) -> None:
    fns = {"incident_triage": incident_triage, "plan_recommendation": plan_recommendation}
    for cap, provider in info.capabilities.items():
        registry.register(info.id, cap, fns[cap], provider=provider)
