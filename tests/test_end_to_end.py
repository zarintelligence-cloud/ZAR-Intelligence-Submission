import base64
import pytest
from fastapi.testclient import TestClient
from tests.helpers import BRIEF, TODAY, gradient_logo


@pytest.fixture
def client():
    from services.polaris.main import app
    return TestClient(app)


def test_full_retail_journey(client):
    r = client.post("/v1/sessions", json={"message": BRIEF, "today": TODAY, "tenant_id": "store6",
                                          "logo_b64": base64.b64encode(gradient_logo()).decode()})
    s = r.json()
    assert r.status_code == 200 and s["state"] == "asking" and s["questions"] == ["Half sleeve or full sleeve?"]
    sid = s["session_id"]

    s = client.post(f"/v1/sessions/{sid}/events", json={"event": "customer_reply",
                                                        "data": {"text": "Half sleeve"}}).json()
    assert s["state"] == "awaiting_approval" and len(s["proofs"]) == 3          # auto-chained
    assert s["artwork"]["print_method"] == "sublimation" and s["fulfillment"]["status"] == "on_track"
    img = client.get(s["proofs"][0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    assert client.post(f"/v1/sessions/{sid}/events", json={"event": "confirmed"}).status_code == 409

    s = client.post(f"/v1/sessions/{sid}/events", json={"event": "proof_chosen",
                                                        "data": {"proof_id": "P1"}}).json()
    assert s["state"] == "collecting" and s["roster_path"].startswith("/roster/")
    token = s["roster_path"].rsplit("/", 1)[1]
    assert client.get(f"/v1/roster/{token}").json()["expected"] == 40

    bad = client.post(f"/v1/roster/{token}/entries", json={"name": "Asha", "size": "HUGE"}).json()
    assert bad["accepted"] is False
    for i in range(40):
        out = client.post(f"/v1/roster/{token}/entries", json={"name": f"Player{i}", "size": "L"}).json()
        assert out["accepted"], out
    s = client.get(f"/v1/sessions/{sid}").json()
    assert s["state"] == "awaiting_confirmation"                                   # auto-quoted
    assert s["quote"]["quote"]["total"] == 21800 and s["quote"]["within_budget"]

    key = "confirm-once"
    s = client.post(f"/v1/sessions/{sid}/events", json={"event": "confirmed", "idempotency_key": key}).json()
    s2 = client.post(f"/v1/sessions/{sid}/events", json={"event": "confirmed", "idempotency_key": key}).json()
    assert s["state"] == "confirmed" and s2["rev"] == s["rev"]                      # replay is a no-op
    ps = s["production_spec"]
    assert ps["size_breakdown"] == {"L": 40} and len(ps["roster"]) == 40
    assert ps["release"]["zar-core"] == "3.2.0" and len(ps["decision_ids"]) == 2
    assert s["handoff"]["delivered"] if "handoff" in s else True


def test_change_request_returns_to_requirements(client):
    s = client.post("/v1/sessions", json={"message": BRIEF + " Full sleeve.", "today": TODAY,
                                          "logo_b64": base64.b64encode(gradient_logo()).decode()}).json()
    assert s["state"] == "awaiting_approval"
    s = client.post(f"/v1/sessions/{s['session_id']}/events",
                    json={"event": "change_request", "data": {"text": "Make it 60 people"}}).json()
    assert s["state"] == "awaiting_approval" and s["spec"]["quantity"] == 60


def test_input_limits(client):
    assert client.post("/v1/sessions", json={"message": BRIEF, "logo_b64": "%%%"}).status_code == 422
    assert client.get("/v1/sessions/nope").status_code == 404
