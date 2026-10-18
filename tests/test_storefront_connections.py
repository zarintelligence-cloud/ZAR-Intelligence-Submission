from fastapi.testclient import TestClient
from tests.test_merchant_console import env, client_for

def test_connection_authorization_and_persistence(env):
    url='/api/console/storefront-connections'
    assert TestClient(env.app).get(url+'?tenant_id=store6').status_code==401
    c,_=client_for(env)
    b={'tenant_id':'store6','website':'https://store6.co.in/shop','platform':'custom'}
    r=c.post(url,json=b)
    assert r.status_code==200, r.text
    assert r.json()['connection']['status']=='ready_to_install'
    assert r.json()['connection']['verified'] is False
    assert c.get(url+'?tenant_id=store6').json()['connection']['website']=='https://store6.co.in'
    b['website']='https://another-shop.example'
    assert c.post(url,json=b).json()['connection']['status']=='onboarding_required'
    b['tenant_id']='other'
    assert c.post(url,json=b).status_code==404
    reader,_=client_for(env,'analyst')
    assert reader.get(url+'?tenant_id=store6').status_code==403
    assert reader.post(url,json=b).status_code==403

def test_connection_invalid_urls_and_csrf(env):
    c,_=client_for(env)
    for value in ['http://store6.co.in','https://localhost','https://127.0.0.1','https://user:pass@store6.co.in','javascript:alert(1)','https://store6.co.in:8000','https://bad.local','https://a..example']:
        assert c.post('/api/console/storefront-connections',json={'tenant_id':'store6','website':value}).status_code==422
    c.headers.pop('X-ZAR-Console',None)
    assert c.post('/api/console/storefront-connections',json={'tenant_id':'store6','website':'https://store6.co.in'}).status_code==403
