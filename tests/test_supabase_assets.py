import pytest
import requests
from zar_core.supabase_assets import SupabaseAssets


def test_private_upload_download_and_key_headers():
    calls = []
    def transport(method, url, **kwargs):
        calls.append((method, url, kwargs))
        class Response:
            status_code = 200
            content = b'logo'
            headers = {'Content-Type': 'image/png'}
        return Response()
    store = SupabaseAssets('https://example.supabase.co', 'sb_secret_test', 'zar-assets', transport=transport)
    ref = store.put(b'logo', 'image/png', tenant_id='store6', session_id='abc', kind='logo')
    assert store.get(ref) == (b'logo', 'image/png')
    assert calls[0][0] == 'POST' and calls[1][0] == 'GET'
    assert calls[0][1].startswith('https://example.supabase.co/storage/v1/object/zar-assets/store6/abc/logo/')
    assert calls[0][2]['headers']['apikey'] == 'sb_secret_test'
    assert 'Authorization' not in calls[0][2]['headers']
    assert calls[0][2]['allow_redirects'] is False
    assert calls[0][2]['data'] == b'logo'


@pytest.mark.parametrize('ref', ['supabase://other/file','https://evil.test/file','supabase://zar-assets/../file','supabase://zar-assets/a/%2f'])
def test_reject_untrusted_references(ref):
    store = SupabaseAssets('https://example.supabase.co', 'sb_secret_test', 'zar-assets')
    with pytest.raises(ValueError):
        store.get(ref)


def test_failures_do_not_leak_key_or_response():
    def transport(*args, **kwargs):
        raise requests.ConnectionError('sensitive header contents')
    store = SupabaseAssets('https://example.supabase.co', 'sb_secret_test', 'zar-assets', transport=transport)
    with pytest.raises(RuntimeError, match='temporarily unavailable') as error:
        store.get('supabase://zar-assets/a')
    assert 'sensitive' not in str(error.value)
