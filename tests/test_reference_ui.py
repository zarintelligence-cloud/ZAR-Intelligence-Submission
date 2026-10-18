import base64
import re
from datetime import date, timedelta
import uuid
import pytest
from fastapi.testclient import TestClient
from tests.helpers import flat_logo


@pytest.fixture
def client(monkeypatch):
    from services.polaris import main
    from zar_core.bootstrap import build
    monkeypatch.setattr(main, 'APP', build('monolith', domains=['retail'], invoke_mode='local'))
    monkeypatch.setattr(main, 'ADMIN_TOKEN', 'ui-test')
    return TestClient(main.app, headers={'X-ZAR-Admin':'ui-test'})


def create(client, n=4, artwork=None):
    deadline=(date.today()+timedelta(days=40)).isoformat()
    r=client.post('/api/sessions',json={'message':f'{n} cricket jerseys, half sleeve, navy and gold, names and numbers, budget 25000, by {deadline}.', 'artwork':artwork or {'gradient':True}})
    assert r.status_code == 201, r.text
    return r.json()


def event(client,s,kind,payload=None,key=None):
    return client.patch('/api/sessions',json={'id':s['id'],'rev':s['rev'],'event':kind,'payload':payload or {},'key':key or uuid.uuid4().hex})


def test_reference_design_and_operations_both_served(client):
    script = re.search(r'/static/website-v12\.js\?v=[0-9a-f]{12}', client.get('/').text)
    assert script is not None
    assert client.get(script.group()).status_code == 200
    assert '/static/website-v7.js?v=r9' in client.get('/legacy-v7').text
    assert '/static/reference.css?v=r5' in client.get('/classic').text
    assert 'From history to a live demonstration' in client.get('/operations').text
    assert client.get('/static/reference-app.js').status_code == 200
    assert client.get('/api/sessions',headers={'X-ZAR-Admin':''}).status_code == 401


def test_reference_journey_uses_real_proofs_quote_and_persistent_session(client):
    s=create(client)
    assert s['state']=='awaiting_approval' and len(s['proofs'])==3 and s['synthetic']
    assert client.get(s['proofs'][0]['url']).headers['content-type']=='image/jpeg'
    s=event(client,s,'proof_chosen',{'proof':0}).json()
    s=event(client,s,'roster_entry',{'entries':[{'name':f'Player {i}','size':'M','number':str(i)} for i in range(4)]}).json()
    assert s['state']=='awaiting_confirmation' and len(s['roster'])==4
    assert s['quote']['unit']>0 and s['quote']['base']==380
    key=uuid.uuid4().hex
    response=event(client,s,'confirmed',key=key)
    assert response.status_code==200,response.text
    confirmed=response.json()
    assert confirmed['production_spec']['synthetic']
    assert len(confirmed['production_spec']['roster'])==4
    assert event(client,s,'confirmed',key=key).json()['rev']==confirmed['rev']
    reloaded=client.get('/api/sessions',params={'id':s['id']}).json()
    assert reloaded['state']=='confirmed' and reloaded['quote']==confirmed['quote']
    core=client.get('/v1/sessions/'+s['id']).json()
    assert core['handoff']['mode']=='sandbox' and core['quote']['quote']['total']==confirmed['quote']['total']


def test_stale_revision_and_conflicting_keys_are_rejected(client):
    s=create(client)
    key=uuid.uuid4().hex
    assert event(client,s,'proof_chosen',{'proof':0},key).status_code==200
    assert event(client,s,'proof_chosen',{'proof':1},key).status_code==409
    assert event(client,s,'cancel').status_code==409


def test_roster_batch_invalid_entry_does_not_partially_write(client):
    s=event(client,create(client),'proof_chosen',{'proof':0}).json()
    bad=event(client,s,'roster_entry',{'entries':[{'name':'Asha','number':'1','size':'M'},{'name':'Name far too long to print','number':'2','size':'M'}]})
    assert bad.status_code==422
    assert client.get('/api/sessions',params={'id':s['id']}).json()['roster']==[]
    duplicate=event(client,s,'roster_entry',{'entries':[{'name':'Asha','number':'01','size':'M'},{'name':'Ravi','number':'1','size':'L'}]})
    assert duplicate.status_code==422


def test_upload_is_validated_and_original_logo_used(client):
    raw=flat_logo()
    invalid=client.post('/api/assets',content=b'not an image',headers={'Content-Type':'image/png'})
    assert invalid.status_code==422
    upload=client.post('/api/assets',content=raw,headers={'Content-Type':'image/png'})
    assert upload.status_code==200,upload.text
    asset=upload.json()
    assert client.get('/api/assets',params={'key':asset['asset']}).content==raw
    s=create(client,artwork=asset)
    assert not s['synthetic']
    assert client.get(s['artwork']['asset']).content==raw


def test_budget_acknowledgement_is_enforced(client):
    from services.polaris import main
    s=event(client,create(client,1),'proof_chosen',{'proof':0}).json()
    s=event(client,s,'roster_entry',{'entries':[{'name':'Asha','size':'M','number':'1'}]}).json()
    record=main.APP.runtime.store.get('sessions',s['id'])
    record['quote']['within_budget']=False
    main.APP.runtime.store.set('sessions',s['id'],record)
    assert event(client,s,'confirmed').status_code==422
    assert event(client,s,'confirmed',{'acceptOverBudget':True}).status_code==200
