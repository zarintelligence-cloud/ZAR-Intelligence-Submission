"""Private Supabase Storage assets; credentials and downloads stay server-side."""
import re
import uuid
from urllib.parse import quote, urlsplit

import requests


class SupabaseAssets:
    shared = True

    def __init__(self, url, secret, bucket, *, transport=None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError('Supabase Storage requires an HTTPS project URL')
        if not secret or secret.startswith('sb_publishable_'):
            raise ValueError('Supabase Storage requires a server secret')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', bucket):
            raise ValueError('Set a valid private ZAR_ASSET_BUCKET')
        self.bucket = bucket
        self.url = url.rstrip('/') + '/storage/v1/object/'
        self.headers = {'apikey': secret}
        if not secret.startswith('sb_secret_'):
            self.headers['Authorization'] = 'Bearer ' + secret
        self.request = transport or requests.request

    def _send(self, method, name, **kwargs):
        try:
            response = self.request(method, self.url + quote(self.bucket, safe='') + '/' + quote(name, safe='/'),
                                    timeout=(5, 30), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise RuntimeError('Private asset storage is temporarily unavailable') from None
        if response.status_code not in (200, 201):
            raise RuntimeError(f'Private asset storage returned HTTP {response.status_code}')
        return response

    def put(self, raw, mime, *, tenant_id, session_id, kind):
        parts = [tenant_id, session_id, kind]
        if any(not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in parts):
            raise ValueError('Invalid asset location')
        name = '/'.join(parts + [uuid.uuid4().hex])
        self._send('POST', name, data=raw,
                   headers={**self.headers, 'Content-Type': mime, 'x-upsert': 'false'})
        return f'supabase://{self.bucket}/{name}'

    def get(self, ref):
        prefix = f'supabase://{self.bucket}/'
        if not ref.startswith(prefix):
            raise ValueError('Asset belongs to a different storage bucket')
        name = ref[len(prefix):]
        if not name or any(not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in name.split('/')):
            raise ValueError('Invalid asset reference')
        response = self._send('GET', name, headers=self.headers)
        return response.content, response.headers.get('Content-Type', 'application/octet-stream')
