from zar_core.contracts import CapabilityRequest, CapabilityResult, DomainInfo, Evidence
from zar_core.decision.bayes import BayesianNetwork, BinaryNode

# Weather and carrier reliability both raise the chance of a capacity squeeze, which drives lateness.
NETWORK = BayesianNetwork([
    BinaryNode("bad_weather", [], {(): 0.15}),
    BinaryNode("weak_carrier", [], {(): 0.2}),
    BinaryNode("capacity_squeeze", ["bad_weather", "weak_carrier"],
               {(False, False): 0.05, (True, False): 0.4, (False, True): 0.35, (True, True): 0.75}),
    BinaryNode("late", ["capacity_squeeze"], {(False,): 0.08, (True,): 0.7}),
])


def shipment_risk(req: CapabilityRequest) -> CapabilityResult:
    p, evidence = req.payload, {}
    if "weather_risk" in p:
        evidence["bad_weather"] = bool(p["weather_risk"])
    if "carrier_on_time_rate" in p:
        evidence["weak_carrier"] = p["carrier_on_time_rate"] < 0.9
    risk = round(NETWORK.query("late", evidence), 3)
    return CapabilityResult(request_id=req.request_id, capability=req.capability, status="ok",
                            data={"late_probability": risk, "evidence_used": evidence}, confidence=0.8,
                            evidence=[Evidence(key="late_probability", value=risk,
                                               source="logistics.shipment_risk", confidence=0.8)])


def register(registry, info: DomainInfo) -> None:
    for cap, provider in info.capabilities.items():
        registry.register(info.id, cap, {"shipment_risk": shipment_risk}[cap], provider=provider)
