import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from zar_core.database_kit import import_kit, read_kit, replay_brief
from zar_core.store import SQLiteStore

KIT = Path(__file__).resolve().parents[1] / 'fixtures/database-kit/zar_demo.sqlite'


def test_import_preserves_source_and_existing_orders_and_is_repeatable(tmp_path):
    before = hashlib.sha256(KIT.read_bytes()).hexdigest()
    store = SQLiteStore(tmp_path / 'runtime.sqlite')
    store.set('sessions', 'existing', {'state': 'confirmed'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_kit(store, KIT), range(2)))
    assert sum(r['added'] for r in results) == 3
    assert store.get('sessions','existing') == {'state':'confirmed'}
    assert hashlib.sha256(KIT.read_bytes()).hexdigest() == before
    saved = SQLiteStore(tmp_path / 'runtime.sqlite').list('imported_sessions')
    assert len(saved) == 3
    assert all(s['source_owner'] == 'demo-user' and not s['artwork_available'] for s in saved)
    assert sum(len(s['data']['roster']) for s in saved) == 70


def test_bad_database_does_not_write_partial_import(tmp_path):
    invalid = tmp_path / 'invalid.sqlite'
    with sqlite3.connect(invalid) as db:
        db.execute('CREATE TABLE something_else (id TEXT)')
    store = SQLiteStore(tmp_path / 'runtime.sqlite')
    with pytest.raises(ValueError, match='schema'):
        import_kit(store,invalid)
    assert store.list('imported_sessions') == []


def test_replay_changes_clock_without_mutating_historical_deadline():
    _, rows = read_kit(KIT)
    row = next(r for r in rows if r['state']=='confirmed')
    old = row['data']['spec']['deadline']
    assert '2027-01-31' in replay_brief(row,date(2027,1,1))
    assert row['data']['spec']['deadline'] == old


@pytest.fixture
def client(monkeypatch):
    from services.polaris import main
    from zar_core.bootstrap import build
    monkeypatch.setattr(main,'APP',build('monolith',domains=['retail'],invoke_mode='local'))
    monkeypatch.setattr(main,'ADMIN_TOKEN','test-admin')
    return TestClient(main.app)


def test_database_requires_admin(client):
    assert client.post('/v1/database/import-kit').status_code == 401
    assert client.get('/v1/database/sessions').status_code == 401
    assert client.get('/v1/ops/overview').status_code == 401


def test_real_workflow_replay_stops_before_confirmation_and_is_idempotent(client):
    headers = {'X-ZAR-Admin':'test-admin'}
    assert client.post('/v1/database/import-kit',headers=headers).json()['added'] == 3
    records = client.get('/v1/database/sessions',headers=headers).json()
    row = next(r for r in records if r['state']=='confirmed')
    path = f"/v1/database/sessions/{row['archive_id']}/replay"
    payload = {'request_id':'test-replay-001','prepare_quote':True}
    result = client.post(path,headers=headers,json=payload)
    assert result.status_code == 200, result.text
    session = result.json()['session']
    assert session['state'] == 'awaiting_confirmation', session
    assert session['quote']['quote']['total'] == 21800
    assert session['synthetic'] and session['source_archive'] == row['archive_id']
    assert session['roster_status']['submitted'] == 40
    again = client.post(path,headers=headers,json=payload).json()['session']
    assert again['session_id'] == session['session_id'] and again['rev'] == session['rev']
    assert client.get('/v1/handoffs',headers=headers).json() == []
    historic = client.get(f"/v1/database/sessions/{row['archive_id']}",headers=headers).json()
    assert historic['state'] == 'confirmed' and historic['data']['quote']['total'] == 21800
    assert client.get(session['proofs'][0]['url']).status_code == 200


def test_risk_and_incomplete_examples_have_real_current_outcomes(client):
    headers = {'X-ZAR-Admin':'test-admin'}
    client.post('/v1/database/import-kit',headers=headers)
    records = client.get('/v1/database/sessions',headers=headers).json()
    for row in records:
        if row['state']=='confirmed':
            continue
        result = client.post(f"/v1/database/sessions/{row['archive_id']}/replay",headers=headers,
                            json={'request_id':'example-'+row['archive_id'],'prepare_quote':True})
        assert result.status_code == 200, result.text
        s = result.json()['session']
        if row['state'] == 'asking':
            assert s['state'] == 'asking' and 1 <= len(s['questions']) <= 3
        else:
            assert s['state'] == 'awaiting_confirmation'
            assert s['quote']['quote']['total'] > 9000
            assert s['fulfillment']['p_on_time'] < .8
    assert client.get('/v1/ops/overview',headers=headers).json()['counts']['attention'] == 2


def test_synthetic_demo_cannot_send_to_real_store(client,monkeypatch):
    from services.polaris import main
    headers={'X-ZAR-Admin':'test-admin'}
    client.post('/v1/database/import-kit',headers=headers)
    row=next(r for r in client.get('/v1/database/sessions',headers=headers).json() if r['state']=='confirmed')
    s=client.post(f"/v1/database/sessions/{row['archive_id']}/replay",headers=headers,
                  json={'request_id':'protected-demo-001','prepare_quote':True}).json()['session']
    monkeypatch.setattr(main.config,'ORDER_WEBHOOK_URL','https://example.invalid/order')
    assert client.post(f"/v1/sessions/{s['session_id']}/events",json={'event':'confirmed'}).status_code == 409
