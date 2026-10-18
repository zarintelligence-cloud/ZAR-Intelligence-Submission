import base64
from datetime import date, timedelta
import time
import uuid
import pytest
from fastapi.testclient import TestClient
from tests.helpers import flat_logo
from zar_core.audit import record_hash

@pytest.fixture
def env(monkeypatch):
    from services.polaris import main
    from zar_core.bootstrap import build
    monkeypatch.setattr(main, "APP", build("monolith", domains=["retail"], invoke_mode="local"))
    monkeypatch.setattr(main, "DEMO_MODE", False)
    monkeypatch.setattr(main, "ADMIN_TOKEN", "private-test-token")
    return main

def client_for(env, role="administrator", stores=None):
    from services.polaris.identity import digest
    member = {"id": str(uuid.uuid4()), "name": role, "email": role+"@example.test", "role": role, "active": True, "stores": stores if stores is not None else ["store6"]}
    env.APP.runtime.store.set("console_members", member["id"], member)
    token = uuid.uuid4().hex
    env.APP.runtime.store.set("console_sessions", digest(token), {"user_id": member["id"], "expires": time.time()+3600})
    c = TestClient(env.app, headers={"X-ZAR-Console": "1"})
    c.cookies.set("zar_console", token)
    return c, member

def payload():
    return {"message": f"2 cricket jerseys half sleeve navy and gold names and numbers budget 25000 by {(date.today()+timedelta(days=40)).isoformat()}", "logo_b64": base64.b64encode(flat_logo()).decode(), "logo_mime": "image/png", "title": "Console test"}

def start(c):
    r = c.post("/api/console/orders", json=payload())
    assert r.status_code == 201, r.text
    return r.json()["id"]

def action(c, sid, event, data=None, key=None):
    snap = c.get("/api/console/snapshot").json()
    o = next(o for o in snap["orders"] if o["id"] == sid)
    return c.post(f"/api/console/orders/{sid}/actions", json={"event": event, "data": data or {}, "rev": o["rev"], "key": key or uuid.uuid4().hex})

def test_full_merchant_journey_and_real_audit(env):
    c, _ = client_for(env)
    sid = start(c)
    snap = c.get("/api/console/snapshot").json()
    assert len(snap["orders"]) == 1 and snap["runs"]
    assert snap['orders'][0]['budget'] == 25000
    assert snap['orders'][0]['firstReplyMs'] >= 0
    p = snap["orders"][0]["proofs"][0]
    assert c.get(p["url"]).headers["content-type"] == "image/jpeg"
    assert p["fidelity"]["encoding_mae"] <= 12
    assert action(c, sid, "proof_chosen", {"proof_id": p["proof_id"]}).status_code == 200
    for i in range(2):
        r = action(c, sid, "roster_entry", {"entry": {"name": "Player"+str(i), "size": "M", "number": str(i)}})
        assert r.status_code == 200, r.text
    snap = c.get("/api/console/snapshot").json()
    assert snap["orders"][0]["quote"]["total"] > 0
    assert action(c, sid, "confirmed").status_code == 200
    assert c.get(f"/v1/sessions/{sid}/production-spec").status_code == 200
    d = env.APP.runtime.store.list("decisions")[0]
    assert d["record_hash"] == record_hash(d)
    assert c.post('/api/console/reviews/'+d['decision_id'], json={}).status_code == 200
    assert c.get('/api/console/snapshot').json()['reviewed'][d['decision_id']]

@pytest.mark.parametrize("role", ["analyst", "auditor"])
def test_readonly_roles_cannot_mutate_or_bypass_through_customer_api(env, role):
    admin, _ = client_for(env)
    sid = start(admin)
    c, _ = client_for(env, role)
    assert c.get('/api/console/snapshot').json()['orders']
    assert c.post('/api/console/orders', json=payload()).status_code == 403
    assert action(c, sid, "cancel").status_code == 403
    assert c.post(f'/v1/sessions/{sid}/events', json={"event": "cancel"}).status_code == 403
    assert TestClient(env.app).post(f'/v1/sessions/{sid}/events', json={"event": "cancel"}).status_code == 401
    assert c.get('/api/console/administration').status_code == 403

def test_store_scope_applies_to_data_assets_actions_reviews(env):
    c, _ = client_for(env)
    sid = start(c)
    other, _ = client_for(env, stores=[])
    assert not other.get('/api/console/snapshot').json()['orders']
    assert other.get(f'/v1/sessions/{sid}/proofs/P1').status_code == 404
    assert other.post(f'/api/console/orders/{sid}/actions', json={"event":"cancel","rev":1,"key":"not-this-store"}).status_code == 404
    d = env.APP.runtime.store.list('decisions')[0]
    assert other.post('/api/console/reviews/'+d['decision_id'], json={}).status_code == 404

def test_operator_pause_resume_and_stale_revision(env):
    c, _ = client_for(env, "operations")
    sid = start(c)
    assert action(c, sid, 'handoff').status_code == 200
    assert action(c, sid, 'proof_chosen', {'proof_id':'P1'}).status_code == 409
    assert action(c, sid, 'resume').status_code == 200
    assert c.post(f'/api/console/orders/{sid}/actions', json={"event":"cancel","rev":0,"key":"stale-action"}).status_code == 409
    assert action(c, sid, 'proof_chosen', {'proof_id':'P1'}).status_code == 200
    assert not c.get('/api/console/snapshot').json()['audits']

def test_customer_credential_survives_but_id_alone_is_insufficient(env):
    c = TestClient(env.app)
    r = c.post('/v1/sessions', json=payload()).json()
    sid = r['session_id']
    assert c.get('/v1/sessions/'+sid).status_code == 401
    assert c.get('/v1/sessions/'+sid, headers={'X-ZAR-Customer':r['customer_token']}).status_code == 200
    assert c.get('/v1/sessions/'+sid+'/proofs/P1', params={'access':r['customer_token']}).status_code == 200
    assert c.get('/v1/sessions/'+sid+'/trace', headers={'X-ZAR-Customer':r['customer_token']}).status_code == 401

def test_csrf_logout_revocation_and_local_login_disabled(env):
    c, m = client_for(env)
    assert c.post('/api/console/orders', json=payload(), headers={'X-ZAR-Console':''}).status_code == 403
    assert c.post('/api/console/orders', json=payload(), headers={'Origin':'https://evil.example'}).status_code == 403
    assert c.post('/api/console/local-login', json={}).status_code == 404
    assert c.post('/api/console/logout', json={}).status_code == 200
    assert c.get('/api/console/snapshot').status_code == 401
    c, m = client_for(env)
    env.APP.runtime.store.update('console_members',m['id'],{'active':False})
    assert c.get('/api/console/snapshot').status_code == 403

def test_integrity_failure_cannot_be_marked_reviewed(env):
    c, _ = client_for(env)
    start(c)
    d = env.APP.runtime.store.list('decisions')[0]
    env.APP.runtime.store.update('decisions',d['decision_id'],{'action':'tampered'})
    assert c.post('/api/console/reviews/'+d['decision_id'],json={}).status_code == 409
    assert 'MISMATCH' in [d['integrity'] for d in c.get('/api/console/snapshot').json()['audits']]

def test_enquiry_persists_and_is_rate_limited(env):
    c, _ = client_for(env)
    p={'name':'Test','email':'test@example.test','message':'Please contact me about retail'}
    assert c.post('/api/enquiries',json=p).status_code == 201
    assert c.post('/api/enquiries',json=p).status_code == 429
    assert c.get('/api/console/administration').json()['leads'][0]['email']==p['email']

def test_supabase_password_login_verifies_user_ignores_claimed_role(env, monkeypatch):
    from services.polaris import identity
    uid=str(uuid.uuid4())
    monkeypatch.setattr(identity.config,'SUPABASE_URL','https://example.supabase.co')
    monkeypatch.setenv('ZAR_SUPABASE_PUBLISHABLE_KEY','sb_publishable_test')
    monkeypatch.setenv('ZAR_BOOTSTRAP_ADMIN_EMAIL','owner@example.test')
    class R:
        status_code=200
        def __init__(self,data): self.data=data
        def json(self): return self.data
    monkeypatch.setattr(identity.requests,'post',lambda *a,**kw:R({'access_token':'verified-on-server','expires_in':3600}))
    monkeypatch.setattr(identity.requests,'get',lambda *a,**kw:R({'id':uid,'email':'owner@example.test','email_confirmed_at':'2026-01-01','user_metadata':{'role':'administrator'}}))
    c=TestClient(env.app,headers={'X-ZAR-Console':'1'})
    r=c.post('/api/console/login',json={'email':'owner@example.test','password':'do-not-store'})
    assert r.status_code==200,r.text
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert 'do-not-store' not in str(env.APP.runtime.store.list('console_sessions'))
    assert c.get('/api/console/me').json()['user']['role']=='administrator'
    env.APP.runtime.store.update('console_members',uid,{'role':'analyst'})
    assert c.get('/api/console/me').json()['user']['role']=='analyst'
    monkeypatch.setenv('ZAR_BOOTSTRAP_ADMIN_EMAIL','different@example.test')
    env.APP.runtime.store.update('console_members',uid,{'active':False})
    assert c.post('/api/console/login',json={'email':'owner@example.test','password':'do-not-store'}).status_code==403
