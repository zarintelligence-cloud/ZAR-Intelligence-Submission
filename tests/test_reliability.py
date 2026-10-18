import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient
from zar_core.contracts import CapabilityResult
from tests.helpers import BRIEF, TODAY, flat_logo


@pytest.fixture
def client():
    from services.polaris.main import app
    return TestClient(app)


def start(client, quantity=4):
    response = client.post('/v1/sessions', json={
        'message': f'{quantity} cricket jerseys, navy and gold, half sleeve, names and numbers, by 15 Nov 2026.',
        'today': TODAY, 'logo_b64': base64.b64encode(flat_logo()).decode()})
    assert response.status_code == 200, response.text
    return response.json()


def choose(client, session):
    r = client.post(f"/v1/sessions/{session['session_id']}/events", json={'event': 'proof_chosen', 'data': {'proof_id': 'P1'}})
    assert r.status_code == 200, r.text
    return r.json()


def test_local_storage_survives_runtime_recreation(tmp_path, monkeypatch):
    from zar_core import config
    from zar_core.bootstrap import build
    monkeypatch.setattr(config, 'STORE_MODE', 'sqlite')
    monkeypatch.setattr(config, 'ASSET_MODE', 'filesystem')
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    a = build(domains=['retail']).runtime
    sid = a.new_session_id()
    ref = a.assets.put(flat_logo(), 'image/png', tenant_id='store6', session_id=sid, kind='logo')
    s = a.start_session(tenant_id='store6', domain='retail.custom_apparel', session_id=sid,
                        fields={'transcript': [BRIEF], 'logo_ref': ref, 'today_override': TODAY})
    b = build(domains=['retail']).runtime
    assert b.store.get('sessions', sid)['state'] == 'asking'
    assert b.assets.get(ref)[0] == flat_logo()
    s = b.handle_event(sid, 'customer_reply', {'text': 'Half sleeve'})
    assert s['state'] == 'awaiting_approval'
    assert b.assets.get(s['proofs'][0]['ref'])[1] == 'image/jpeg'


def test_simultaneous_entries_and_confirmation_are_serialized(client):
    s = choose(client, start(client, 12))
    token = s['roster_path'].split('/')[-1]
    def submit(i):
        return client.post(f'/v1/roster/{token}/entries', json={'name': f'Player{i}', 'size': 'L', 'number': str(i)}).json()
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(x['accepted'] for x in pool.map(submit, range(12)))
    sid = s['session_id']
    s = client.get(f'/v1/sessions/{sid}').json()
    assert s['state'] == 'awaiting_confirmation' and s['roster_status']['submitted'] == 12
    def confirm(_):
        return client.post(f'/v1/sessions/{sid}/events', json={'event': 'confirmed', 'idempotency_key': 'once'}).json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(confirm, range(4)))
    assert len({s['rev'] for s in responses}) == 1
    assert len(responses[0]['production_spec']['roster']) == 12
    assert 'logo_ref' not in responses[0]['production_spec']['artwork']
    assert 'ref' not in responses[0]['production_spec']['proof']
    downloaded = client.get(f'/v1/sessions/{sid}/production-spec')
    assert downloaded.status_code == 200 and 'attachment' in downloaded.headers['content-disposition']


def test_failed_auto_step_retries_without_repeating_entry(client, monkeypatch):
    from services.polaris.main import APP
    s = choose(client, start(client, 1))
    invoke = APP.runtime.invoker.invoke
    failed = False
    def flaky(req):
        nonlocal failed
        if req.capability == 'pricing' and not failed:
            failed = True
            return CapabilityResult(request_id=req.request_id, capability=req.capability, status='error', error='temporary')
        return invoke(req)
    monkeypatch.setattr(APP.runtime.invoker, 'invoke', flaky)
    sid = s['session_id']
    payload = {'event': 'roster_entry', 'data': {'entry': {'name': 'Asha', 'size': 'L', 'number': '1'}}, 'idempotency_key': 'entry-one'}
    first = client.post(f'/v1/sessions/{sid}/events', json=payload).json()
    assert first['state'] == 'collecting' and 'retry' in first['allowed_events']
    retry = client.post(f'/v1/sessions/{sid}/events', json=payload).json()
    assert retry['state'] == 'awaiting_confirmation' and retry['roster_status']['submitted'] == 1
    assert retry['last_error'] is None


def test_failed_handoff_has_real_durable_retry(client, monkeypatch):
    from services.polaris.main import APP, process_retries
    s = choose(client, start(client, 1))
    sid, token = s['session_id'], s['roster_path'].split('/')[-1]
    client.post(f'/v1/roster/{token}/entries', json={'name': 'Asha', 'size': 'L', 'number': '1'})
    invoke = APP.runtime.invoker.invoke
    def failure(req):
        if req.capability == 'order_handoff':
            return CapabilityResult(request_id=req.request_id, capability=req.capability, status='error', error='temporary')
        return invoke(req)
    monkeypatch.setattr(APP.runtime.invoker, 'invoke', failure)
    s = client.post(f'/v1/sessions/{sid}/events', json={'event': 'confirmed'}).json()
    assert s['state'] == 'confirmed' and not s['handoff']['delivered']
    assert APP.runtime.store.get('retry_jobs', sid)['status'] == 'pending'
    monkeypatch.setattr(APP.runtime.invoker, 'invoke', invoke)
    APP.runtime.store.update('retry_jobs', sid, {'due': 0})
    assert process_retries()['processed'] >= 1
    s = client.get(f'/v1/sessions/{sid}').json()
    assert s['handoff']['delivered'] and s['handoff']['attempts'] == 2
    assert APP.runtime.store.get('retry_jobs', sid)['status'] == 'complete'


def test_stale_roster_link_cannot_edit_new_order_revision(client):
    s = choose(client, start(client, 1))
    sid, old = s['session_id'], s['roster_path'].split('/')[-1]
    client.post(f'/v1/roster/{old}/entries', json={'name': 'Asha', 'size': 'L', 'number': '1'})
    s = client.post(f'/v1/sessions/{sid}/events', json={'event': 'change_request', 'data': {'text': 'Make it 2 pieces'}}).json()
    assert s['state'] == 'awaiting_approval' and s['spec']['quantity'] == 2
    choose(client, s)
    assert client.post(f'/v1/roster/{old}/entries', json={'name': 'Bad', 'size': 'L', 'number': '2'}).status_code == 409


def test_leading_zero_numbers_cannot_bypass_duplicate_check(client):
    s = choose(client, start(client, 2))
    token = s['roster_path'].split('/')[-1]
    for name, number in [('Asha', '7'), ('Ben', '07')]:
        result = client.post(f'/v1/roster/{token}/entries', json={'name': name, 'size': 'L', 'number': number}).json()
    assert result['state'] == 'collecting' and not result['roster_status']['ready']
    assert result['roster_status']['problems']


@pytest.mark.parametrize('raw,mime', [(b'not an image', 'image/png'), (flat_logo(), 'image/jpeg')])
def test_reject_invalid_and_mislabeled_images(client, raw, mime):
    r = client.post('/v1/sessions', json={'message': BRIEF, 'logo_b64': base64.b64encode(raw).decode(), 'logo_mime': mime})
    assert r.status_code == 422


def test_input_and_admin_boundaries(client, monkeypatch):
    import services.polaris.main as api
    monkeypatch.setattr(api, 'ADMIN_TOKEN', 'test-private-token')
    assert client.get('/v1/sessions').status_code == 401
    assert client.get('/v1/sessions', headers={'X-ZAR-Admin': 'test-private-token'}).status_code == 200
    s = start(client)
    r = client.post(f"/v1/sessions/{s['session_id']}/events", json={'event': 'change_request', 'data': {'text': 'x' * 4001}})
    assert r.status_code == 422
    assert client.get('/v1/tenants/..%5Cdomains%5Cretail%5Cmanifest').status_code == 404


def test_expired_signed_webhook_rejected(client):
    from domains.retail.capabilities.handoff import signature
    from zar_core import config
    body, ts = b'{}', str(int(time.time()) - 1000)
    assert client.post('/store6/api/zar-webhook', content=body, headers={
        'X-ZAR-Timestamp': ts, 'X-ZAR-Signature': signature(body, config.ORDER_WEBHOOK_SECRET, ts)}).status_code == 401


def test_text_edge_cases_and_voice_guard():
    from domains.retail.text_utils import baseline_extract, parse_date
    from domains.retail.capabilities.voice import numbers_preserved
    assert parse_date('2026-02-30', date(2026, 1, 1)) is None
    assert baseline_extract(['40 jerseys, budget 25,000'], date(2026, 1, 1))['budget'] == 25000
    assert not numbers_preserved('40 pieces for 21,800 INR', '140 pieces for 21,800 INR')
    assert not numbers_preserved('40 pieces for 21,800 INR', '40 pieces for 21,800 INR plus 500')


@pytest.mark.parametrize('colours', [['white'], ['black'], ['white', 'black'], []])
def test_always_three_distinct_proofs(colours):
    from domains.retail.capabilities.hubble import colourways
    assert len(set(colourways(colours, 3))) == 3


def test_optional_questions_do_not_exceed_conversation_budget():
    from domains.retail.capabilities.rosetta import run
    from tests.test_retail import req
    first = run(req('requirements_analysis', {'transcript': ['I need team clothing'], 'today': TODAY}))
    asked = [q['field'] for q in first.data['question_plan']]
    assert len(asked) == 3
    second = run(req('requirements_analysis', {'transcript': ['I need team clothing', '40 cricket jerseys by 15 Nov'],
                                              'already_asked': asked, 'today': TODAY}))
    assert second.status == 'ok' and not second.questions
    assert second.data['assumptions']['sleeve'] == 'half'
