import copy
import pytest
import requests
from zar_core.supabase_store import SupabaseStore, SupabaseStoreError
from zar_core.store import StoreBusy


class Response:
    status_code=200
    content=b'json'
    def __init__(self, value): self.value=value
    def json(self): return self.value


class RPCServer:
    """Contract fake, not a substitute for the real-project SQL acceptance check."""
    def __init__(self): self.docs={}; self.locks={}; self.calls=[]
    def __call__(self,url,**kwargs):
        self.calls.append((url,kwargs))
        p=kwargs['json'];op=url.rsplit('zar_',1)[1]
        key=(p.get('p_collection'),p.get('p_key'))
        result=None
        if op=='doc_get':result=self.docs.get(key)
        elif op=='doc_set':self.docs[key]=copy.deepcopy(p['p_body'])
        elif op=='doc_update':self.docs.setdefault(key,{}).update(copy.deepcopy(p['p_patch']))
        elif op=='doc_list':result=[v for (c,k),v in self.docs.items() if c==p['p_collection'] and (p['p_field'] is None or v.get(p['p_field'])==p['p_value'])][:p['p_limit']]
        elif op=='lock_acquire':
            result=p['p_key'] not in self.locks
            if result:self.locks[p['p_key']]=p['p_owner']
        elif op=='lock_release':
            if self.locks.get(p['p_key'])==p['p_owner']:del self.locks[p['p_key']]
        else:raise AssertionError(op)
        return Response(copy.deepcopy(result))


def test_contract_check_and_diagnostics_only():
    from scripts.check_supabase import check
    server=RPCServer();store=SupabaseStore('https://example.supabase.co','sb_secret_test',transport=server)
    assert check(store)['read_write_update_filter_lock']=='passed'
    assert {k[0] for k in server.docs}=={'system_checks'}
    assert not server.locks
    assert all(c[1]['allow_redirects'] is False for c in server.calls)
    assert 'Authorization' not in server.calls[0][1]['headers']


def test_independent_workers_compete_and_release_after_failure():
    server=RPCServer()
    a=SupabaseStore('https://example.supabase.co','sb_secret_test',transport=server)
    b=SupabaseStore('https://example.supabase.co','sb_secret_test',transport=server)
    with pytest.raises(ValueError):
        with a.lock('session'):
            with pytest.raises(StoreBusy):
                with b.lock('session'):pass
            raise ValueError('Workflow failed')
    with b.lock('session'):pass
    assert not server.locks


def test_merge_filter_and_absent_documents():
    server=RPCServer();store=SupabaseStore('https://example.supabase.co','sb_secret_test',transport=server)
    assert store.get('sessions','absent') is None
    store.set('sessions','a',{'tenant_id':'store6','rev':0,'nested':{'a':1}})
    store.update('sessions','a',{'rev':1})
    store.set('sessions','b',{'tenant_id':'another'})
    assert store.list('sessions','tenant_id','store6')==[{'tenant_id':'store6','rev':1,'nested':{'a':1}}]
    assert store.list('sessions','tenant_id','unknown')==[]


@pytest.mark.parametrize('url,key', [('http://example.supabase.co','sb_secret_x'),('https://example.supabase.co/path','sb_secret_x'),('https://example.supabase.co','sb_publishable_x'),('https://example.supabase.co','')])
def test_bad_settings_fail_closed(url,key):
    with pytest.raises(ValueError):SupabaseStore(url,key)


def test_legacy_header_and_redacted_errors():
    captured={}
    def fail(url,**kw):
        captured.update(kw)
        raise requests.ConnectionError('private-key private-customer')
    store=SupabaseStore('https://example.supabase.co','legacy-service-jwt',transport=fail)
    with pytest.raises(SupabaseStoreError) as err:store.get('sessions','a')
    assert 'private' not in str(err.value)
    assert captured['headers']['Authorization']=='Bearer legacy-service-jwt'
    response=Response({'secret':'must not be echoed'});response.status_code=401
    store._post=lambda *a,**k:response
    with pytest.raises(SupabaseStoreError,match='HTTP 401') as err:store.get('sessions','a')
    assert 'must not' not in str(err.value)


def test_factory_uses_supabase_without_requiring_google(monkeypatch):
    from zar_core import config
    from zar_core.store import make_store
    monkeypatch.setattr(config,'SUPABASE_URL','https://example.supabase.co')
    monkeypatch.setattr(config,'SUPABASE_SECRET_KEY','sb_secret_test')
    assert isinstance(make_store('supabase'),SupabaseStore)
