from zar_core.contracts import CapabilityRequest, Context
from zar_core.domain_loader import DomainCatalog
from zar_core.registry import CapabilityRegistry
from tests.helpers import ROOT


def req(domain, cap, payload):
    return CapabilityRequest(request_id="1", context=Context(tenant_id="t", domain=domain, session_id="s"),
                             capability=cap, payload=payload)


def test_new_domain_pack_loads_without_core_change():
    r, c = CapabilityRegistry(), DomainCatalog()
    c.load(ROOT / "domains" / "saas", r)
    out = r.invoke(req("saas.operations", "incident_triage", {"metrics": {"db_latency_ms": 900}}))
    assert out.data["hypotheses"][0]["cause"] == "database_latency"
    assert c.get("saas.operations").workflow is None


def test_logistics_bayesian_risk_updates_with_evidence():
    r, c = CapabilityRegistry(), DomainCatalog()
    c.load(ROOT / "domains" / "logistics", r)
    base = r.invoke(req("logistics.fulfillment", "shipment_risk", {})).data["late_probability"]
    bad = r.invoke(req("logistics.fulfillment", "shipment_risk",
                       {"weather_risk": True, "carrier_on_time_rate": 0.8})).data["late_probability"]
    assert bad > base


def test_same_capability_name_isolated_per_domain(app):
    names = {(x["domain"], x["capability"]) for x in app.registry.describe()}
    assert ("retail.custom_apparel", "pricing") in names and ("saas.operations", "pricing") not in names


def test_bootstrap_release_manifest_is_compatible(app):
    m = app.releases.manifest()
    assert m["zar-core"] == "3.2.0" and m["retail.custom_apparel"] == "3.2.0"
    assert app.releases.compatibility_check() == []
