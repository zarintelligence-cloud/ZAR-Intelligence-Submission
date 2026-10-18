import pytest
import os
import subprocess
import sys
from fastapi.testclient import TestClient
from tests.test_merchant_console import env, client_for, start, action

def test_vercel_unconfigured_preview_serves_v10_and_blocks_writes(tmp_path):
    settings={k:v for k,v in os.environ.items() if not k.startswith('ZAR_')}
    settings.update(ZAR_DATA_DIR=str(tmp_path), ZAR_STORE_MODE='sqlite', ZAR_LLM_MODE='mock', ZAR_ASSET_MODE='filesystem', ZAR_DEMO_SEED='0')
    code="""
from fastapi.testclient import TestClient
from app import app
with TestClient(app) as c:
    r=c.get('/')
    assert r.status_code==200 and '/static/website-v12.js?v=' in r.text
    assert 'Website preview' in r.text
    assert c.get('/api/console/config').json()['local'] is False
    assert c.post('/api/console/local-login',json={}).status_code==503
    assert c.post('/api/v10/document',json={'path':'tenants/store6','data':{}}).status_code==503
"""
    result=subprocess.run([sys.executable,'-c',code],env=settings,capture_output=True,text=True,timeout=45)
    assert result.returncode==0, result.stderr

def test_v10_pages_and_legacy_are_available(env):
    c=TestClient(env.app)
    for path in ('/', '/try', '/terms', '/architecture', '/hq/companies', '/app/retail/orders'):
        r=c.get(path)
        assert r.status_code==200
        assert '/static/website-v12.js' in r.text
    assert '/static/website-v7.js' in c.get('/legacy-v7').text
    assert len(c.get('/api/v10/catalog').json()['plans'])==4

def test_no_v10_auth_bypass_or_cross_tenant_writes(env):
    public=TestClient(env.app,headers={'X-ZAR-Console':'1'})
    assert public.get('/api/v10/documents?path=companies').status_code==401
    c,_=client_for(env)
    assert c.get('/api/v10/profile').json()['hq'] is False
    assert c.post('/api/v10/document',json={'path':'companies/acme','data':{}}).status_code==403
    assert c.post('/api/v10/document',json={'path':'tenants/other','data':{}}).status_code==404
    assert c.post('/api/v10/document',json={'path':'tenants/store6/orders/fake','data':{'state':'confirmed'}}).status_code==403
    assert c.post('/api/v10/document',json={'path':'tenants/store6/members/admin','data':{}}).status_code==403
    assert c.post('/api/v10/document',json={'path':'console_members/admin','data':{}}).status_code==404

def test_v10_settings_persist_without_changing_order_engine(env):
    c,_=client_for(env)
    sid=start(c)
    before=c.get('/api/console/snapshot').json()['orders'][0]
    value={'subscription':{'plan':'basic','flags':{'proofs':{'staff':False}}}}
    assert c.post('/api/v10/document',json={'path':'tenants/store6','data':value}).status_code==200
    assert c.get('/api/v10/document?path=tenants/store6').json()['subscription']==value['subscription']
    after=c.get('/api/console/snapshot').json()['orders'][0]
    assert before==after
    assert action(c,sid,'proof_chosen',{'proof_id':before['proofs'][0]['proof_id']}).status_code==200
    reader,_=client_for(env,'analyst')
    assert reader.post('/api/v10/document',json={'path':'tenants/store6','data':value}).status_code==403

def test_hq_scope_is_separate_and_records_are_empty_by_default(env,monkeypatch):
    c,u=client_for(env)
    monkeypatch.setenv('ZAR_HQ_EMAILS',u['email'])
    assert c.get('/api/v10/profile').json()['hq'] is True
    assert c.get('/api/v10/documents?path=companies').json()==[]
    for path,data in [('companies/acme',{'name':'Acme'}),('companies/acme/invoices/inv1',{'status':'due','total':100})]:
        assert c.post('/api/v10/document',json={'path':path,'data':data}).status_code==200
    assert c.get('/api/v10/documents?path=companies').json()[0]['name']=='Acme'
    assert c.get('/api/v10/documents?path=companies/acme/invoices').json()[0]['total']==100
    other,_=client_for(env,'operations')
    assert other.get('/api/v10/documents?path=companies').status_code==403
    assert c.post('/api/v10/hq/reminder',json={}).status_code==400
    assert env.APP.runtime.store.list('sessions')==[]

def test_v10_csrf_and_assistant_limits(env):
    c,_=client_for(env)
    assert c.post('/api/v10/document',headers={'X-ZAR-Console':'0'},json={'path':'tenants/store6','data':{}}).status_code==403
    assert c.post('/api/v10/document',headers={'Origin':'https://evil.example'},json={'path':'tenants/store6','data':{}}).status_code==403
    for _ in range(20):
        r=c.post('/api/v10/zuzu',json={'message':'Help me find an order'})
        assert r.status_code==200
        assert r.json()['mode']=='rules'
    assert c.post('/api/v10/zuzu',json={'message':'Help'}).status_code==429

def test_v10_ai_failure_does_not_claim_success(env,monkeypatch):
    from services.polaris import version10
    class Broken:
        is_mock=False
        def generate_text(self,*a):raise RuntimeError('private-provider-error')
    monkeypatch.setattr(version10,'get_llm',lambda:Broken())
    c,_=client_for(env)
    r=c.post('/api/v10/zuzu',json={'message':'Help'})
    assert r.status_code==503
    assert 'private-provider-error' not in r.text

def test_invalid_plan_cannot_replace_saved_preferences(env):
    c,_=client_for(env)
    path='tenants/store6'
    valid={'subscription':{'plan':'basic','autoRenew':False}}
    assert c.post('/api/v10/document',json={'path':path,'data':valid}).status_code==200
    for invalid in (None, {'plan':'unknown'}, {'plan':'basic','autoRenew':True}, {'plan':'basic','flags':{'zuzu':True}}):
        assert c.post('/api/v10/document',json={'path':path,'data':{'subscription':invalid}}).status_code==422
    assert c.get('/api/v10/document',params={'path':path}).json()['subscription']==valid['subscription']

def test_generated_v10_contains_no_demo_auth_or_seed_stream():
    from pathlib import Path
    text=(Path(__file__).parents[1]/'services/polaris/static/website-v12.js').read_text(encoding='utf-8')
    assert 'ceo@123' not in text
    assert 'const HQ_SEED = []' in text
    assert 'const Engine = (() =>' not in text
    assert 'u.password !== password' not in text
    assert 'await Cloud.start()' not in text
    assert "iframe src=\"/embed?tenant=" in text
