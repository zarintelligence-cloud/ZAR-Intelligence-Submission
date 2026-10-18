"""Server-only Supabase document store using restricted Postgres RPCs.

No browser key or client-supplied identity grants access to these functions.
Apply deploy/supabase/001_runtime.sql once before selecting this backend.
"""
from contextlib import contextmanager
from urllib.parse import urlsplit
import uuid
import threading
import requests
from .store import StoreBusy


class SupabaseStoreError(RuntimeError):
    pass


class SupabaseStore:
    def __init__(self, url, secret, *, transport=None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError('ZAR_SUPABASE_URL must be an HTTPS project URL without a path or credentials')
        if not secret or secret.startswith('sb_publishable_'):
            raise ValueError('Set ZAR_SUPABASE_SECRET_KEY to a server secret key, not a publishable key')
        self.url = url.rstrip('/') + '/rest/v1/rpc/'
        self._headers = {'apikey': secret, 'Content-Type': 'application/json'}
        # Modern secret keys belong only in apikey; legacy service_role JWTs also
        # need Authorization. SQL denies anon/authenticated even if misconfigured.
        if not secret.startswith('sb_secret_'):
            self._headers['Authorization'] = 'Bearer ' + secret
        self._local = threading.local()
        self._post = transport or self._pooled_post

    def _pooled_post(self, url, **kwargs):
        # Reuse TLS connections within each worker, without sharing mutable
        # requests.Session objects between FastAPI worker threads.
        if not hasattr(self._local, 'http'):
            self._local.http = requests.Session()
        return self._local.http.post(url, **kwargs)

    def rpc(self, operation, **payload):
        try:
            response = self._post(self.url + 'zar_' + operation, headers=self._headers,
                                  json=payload, timeout=(5, 30), allow_redirects=False)
        except requests.RequestException:
            # Do not include exception messages/response bodies: they can echo
            # request headers or submitted private customer records.
            raise SupabaseStoreError('Supabase request failed. Check connectivity and retry.') from None
        if response.status_code not in (200, 204):
            raise SupabaseStoreError(f'Supabase returned HTTP {response.status_code}. Check the migration, server key and project status.')
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise SupabaseStoreError('Supabase returned an invalid response.') from None

    def get(self, collection, key):
        return self.rpc('doc_get', p_collection=collection, p_key=key)

    def set(self, collection, key, value):
        self.rpc('doc_set', p_collection=collection, p_key=key, p_body=value)

    def update(self, collection, key, patch):
        self.rpc('doc_update', p_collection=collection, p_key=key, p_patch=patch)

    def list(self, collection, field=None, value=None, limit=500):
        return self.rpc('doc_list', p_collection=collection, p_field=field,
                        p_value=value, p_limit=min(max(int(limit), 1), 5000))

    @contextmanager
    def lock(self, key):
        owner = uuid.uuid4().hex
        if self.rpc('lock_acquire', p_key=key, p_owner=owner) is not True:
            raise StoreBusy('This session is processing another request. Please retry shortly.')
        try:
            yield
        finally:
            # Release only our own lease. A lost response is not retried as an
            # acquisition: doing so could overlap a later workflow execution.
            self.rpc('lock_release', p_key=key, p_owner=owner)
