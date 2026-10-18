import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from zar_core.adk_agents import ContractAgent, run_contract, mount_a2a
from zar_core.contracts import CapabilityRequest, CapabilityResult, Context
from zar_core.invoker import A2AInvoker, RemoteInvoker
from zar_core.registry import CapabilityRegistry


def request(capability='pricing'):
    return CapabilityRequest(request_id=uuid.uuid4().hex, capability=capability,
                             context=Context(tenant_id='store6', domain='retail', session_id='test'))


def test_adk_executes_contract():
    agent = ContractAgent('polaris', 'test', lambda p: {'answer': p['value']+1})
    assert run_contract(agent, {'value': 2}) == {'answer': 3}


def test_a2a_real_adk_server_roundtrip_and_scope(monkeypatch):
    registry = CapabilityRegistry()
    registry.register('retail', 'pricing', lambda r: CapabilityResult(request_id=r.request_id,
        capability=r.capability, status='ok', data={'total': 1550}))
    app = FastAPI()
    mount_a2a(app, registry, None, None, 'http://localhost:8081')
    with TestClient(app) as client:
        card = client.get('/a2a/kepler/.well-known/agent-card.json')
        assert card.status_code == 200, card.text
        assert card.json()['name'] == 'kepler'
        import requests
        monkeypatch.setattr(requests, 'post', lambda url, **kw: client.post(url.replace('http://localhost:8081',''),
                                                                          json=kw['json'], headers=kw['headers']))
        result = A2AInvoker('http://localhost:8081').invoke(request())
        assert result.status == 'ok', result
        assert result.data['total'] == 1550
        # Even private callers cannot ask Kepler to execute another capability.
        r = request('order_handoff')
        response = client.post('/a2a/kepler/', json={'jsonrpc':'2.0','id':'bad','method':'message/send',
            'params':{'message':{'role':'user','messageId':'bad','parts':[{'kind':'text','text':r.model_dump_json()}]},
                      'configuration':{'blocking':True}}})
        assert 'outside this agent scope' in response.text, response.text


def test_a2a_error_and_mismatched_response_never_succeed(monkeypatch):
    import requests
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'result': {'parts':[{'text':CapabilityResult(request_id='other', capability='pricing',status='ok').model_dump_json()}]}}
    monkeypatch.setattr(requests, 'post', lambda *a, **k: Response())
    assert A2AInvoker('http://localhost:8081').invoke(request()).status == 'error'


def test_oidc_loopback_matching_is_exact(monkeypatch):
    import google.oauth2.id_token
    monkeypatch.setattr(google.oauth2.id_token, 'fetch_id_token', lambda r, audience: 'signed-test-token')
    assert RemoteInvoker('http://127.0.0.1:8081')._headers() == {}
    assert RemoteInvoker('http://localhost.evil.example')._headers()['Authorization'] == 'Bearer signed-test-token'
