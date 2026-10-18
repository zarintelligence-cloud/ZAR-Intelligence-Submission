"""Store 6 prototype: traces, read models, connector, roster corrections, demo scenarios."""
import base64, json, time
import pytest
from fastapi.testclient import TestClient
from tests.helpers import BRIEF, TODAY, gradient_logo


@pytest.fixture
def client():
    from services.polaris.main import app
    return TestClient(app)


@pytest.fixture
def runtime():
    from services.polaris.main import APP
    return APP.runtime


def test_trace_links_steps_runs_and_decisions(client):
    s = client.post("/v1/sessions", json={"message": BRIEF + " Half sleeve.", "today": TODAY, "tenant_id": "store6",
                                          "logo_b64": base64.b64encode(gradient_logo()).decode()}).json()
    tr = client.get(f"/v1/sessions/{s['session_id']}/trace").json()
    steps = tr["steps"]
    assert [x["event"] for x in steps] == ["created", "continue"] and steps[1]["origin"] == "auto"
    assert steps[1]["handler"] == "analyse_artwork_and_proof" and steps[1]["to"] == "awaiting_approval"
    caps = [r["capability"] for r in steps[1]["runs"]]
    assert caps == ["artwork_analysis", "fulfillment_confidence", "proof_generation"]
    art = steps[1]["runs"][0]
    assert art["provider"] == "Spectra" and art["request"]["context"]["tenant_id"] == "store6"
    assert "<" in art["request"]["payload"].get("logo_ref", "<") or art["request"]["payload"]["logo_ref"].startswith("mem://")
    assert art["response"]["debug"]["matched_rule"] == "P1-gradient-polyester" and "prompt" in art["response"]["debug"]
    hub = steps[1]["runs"][2]["response"]["data"]["proofs"][0]
    assert hub["image_b64"].startswith("<") and hub["image_b64"].endswith("base64 chars>")     # redacted
    assert steps[1]["decisions"][0]["goal"] == "select_print_method"
    rosetta = steps[0]["runs"][0]["response"]["debug"]
    assert rosetta["model_called"] is False and "untrusted data" in rosetta["prompt"]


def test_sessions_list_metrics_and_policies(client):
    client.post("/v1/sessions", json={"message": BRIEF, "today": TODAY, "tenant_id": "store6",
                                      "logo_b64": base64.b64encode(gradient_logo()).decode()})
    rows = client.get("/v1/sessions?tenant_id=store6").json()
    assert rows and {"session_id", "state", "title"} <= set(rows[0])
    m = client.get("/v1/ops/metrics?tenant_id=store6").json()
    assert m["runs"] >= 1 and any(c["capability"] == "requirements_analysis" for c in m["capabilities"])
    pol = client.get("/v1/ops/policies").json()
    assert "pricing.yaml" in pol and "workflow.yaml" in pol
    assert client.get("/v1/tenants/store6").json()["name"] == "Store 6"
    assert client.get("/v1/tenants/nobody").status_code == 404


def test_every_demo_scenario_runs_on_the_real_engine(runtime):
    from services.polaris import demo
    expected = {"happy": ("confirmed", 21800), "budget": ("confirmed", 20800), "deadline": ("collecting", None),
                "injection": ("awaiting_confirmation", 16350), "polo": ("confirmed", 12875), "roster": ("confirmed", 10488)}
    for sc in demo.scenarios():
        s = demo.run_scenario(runtime, sc, "store6")
        total = ((s.get("quote") or {}).get("quote") or {}).get("total")
        assert (s["state"], total) == expected[sc["id"]], sc["id"]
        if s["state"] == "confirmed":
            assert s["handoff"]["delivered"] and runtime.store.get("handoffs", s["session_id"])["order_ref"].startswith("S6-")


def test_deadline_scenario_flags_risk_then_recovers(runtime):
    from services.polaris import demo
    sc = next(x for x in demo.scenarios() if x["id"] == "deadline")
    s = demo.run_scenario(runtime, sc, "store6")
    horizons = [r["response"]["data"]["p_on_time"] for st in runtime.trace(s["session_id"])["steps"]
                for r in st["runs"] if r["capability"] == "fulfillment_confidence"]
    assert horizons[0] < 0.05 and horizons[-1] > 0.9


def test_roster_correction_by_name_and_batch_endpoint(client):
    from services.polaris import demo
    s = client.post("/v1/sessions", json={"message": "12 jersey + shorts combo kits, black and orange, names and numbers, "
                                                     "half sleeve, by 20 Dec.", "today": TODAY, "tenant_id": "store6",
                                          "logo_b64": base64.b64encode(demo.logo("titans")).decode()}).json()
    s = client.post(f"/v1/sessions/{s['session_id']}/events", json={"event": "proof_chosen", "data": {"proof_id": "P1"}}).json()
    tok = s["roster_path"].rsplit("/", 1)[1]
    entries = demo.roster_entries({"count": 12, "numbers": True, "duplicate": {"index": 11, "number": "7"}})
    out = client.post(f"/v1/roster/{tok}/entries:batch", json=entries).json()
    assert out["accepted"] == 12 and out["results"][-1]["state"] == "collecting"
    fix = client.post(f"/v1/roster/{tok}/entries", json={**entries[11], "number": "17"}).json()
    assert fix["accepted"] and fix["state"] == "awaiting_confirmation"
    assert client.get(f"/v1/roster/{tok}").json()["submitted"] == 12


def test_store6_webhook_receiver_verifies_signature(client):
    from domains.retail.capabilities.handoff import signature
    from zar_core import config
    env = {"type": "zar.order.confirmed", "order_ref": "S6-TEST01", "session_id": "test01", "tenant_id": "store6",
           "sent_at": "2026-09-26T00:00:00Z", "production_spec": {}}
    body, ts = json.dumps(env).encode(), str(int(time.time()))
    good = {"X-ZAR-Timestamp": ts, "X-ZAR-Signature": signature(body, config.ORDER_WEBHOOK_SECRET, ts),
            "Content-Type": "application/json"}
    assert client.post("/store6/api/zar-webhook", content=body, headers=good).json()["store6_order_id"] == "S6-TEST01"
    bad = {**good, "X-ZAR-Signature": "0" * 64}
    assert client.post("/store6/api/zar-webhook", content=body, headers=bad).status_code == 401


def test_pages_and_demo_assets_are_served(client):
    for path in ["/", "/store6", "/embed", "/widget.js", "/roster/x", "/static/orbit.js", "/v1/demo/logos/strikers.png"]:
        assert client.get(path).status_code == 200, path
    assert len(client.get("/v1/demo/scenarios").json()["scenarios"]) == 6
