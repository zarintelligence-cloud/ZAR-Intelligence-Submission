"""Firestore document envelope preserving the Postgres JSON document contract."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import time
import uuid

DOCUMENTS = 'zar_documents'
QUERY_FIELDS = ('tenant_id', 'session_id', 'status', 'parent', 'id')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def document_id(collection, key):
    return fingerprint([collection, key])


def envelope(collection, key, body, updated_at=None):
    if not isinstance(body, dict) or not isinstance(collection, str) or not isinstance(key, str):
        raise ValueError('Invalid document envelope')
    encoded = canonical(body)
    # Leave ample room for Firestore names, indexes and envelope overhead.
    if len(encoded.encode()) > 900_000:
        raise ValueError('Document exceeds migration size budget; no data was truncated')
    return {'collection': collection, 'key': key, 'body_json': encoded,
            'filters': {field: fingerprint(body[field]) for field in QUERY_FIELDS if field in body},
            'updated_at': updated_at or datetime.now(timezone.utc), 'format_version': 1}


class FirestoreDocumentStore:
    def __init__(self, client=None):
        from google.cloud import firestore
        self.client = client or firestore.Client(project=os.environ['GOOGLE_CLOUD_PROJECT'],
            database=os.getenv('ZAR_FIRESTORE_DATABASE', '(default)'))

    def ref(self, collection, key):
        return self.client.collection(DOCUMENTS).document(document_id(collection, key))

    def get(self, collection, key):
        snap = self.ref(collection, key).get()
        return json.loads(snap.to_dict()['body_json']) if snap.exists else None

    def set(self, collection, key, value):
        self.ref(collection, key).set(envelope(collection, key, value))

    def update(self, collection, key, patch):
        from google.cloud import firestore
        ref = self.ref(collection, key)

        @firestore.transactional
        def apply(tx):
            snap = ref.get(transaction=tx)
            body = json.loads(snap.to_dict()['body_json']) if snap.exists else {}
            body.update(patch)  # Postgres JSONB || is a shallow merge.
            tx.set(ref, envelope(collection, key, body))
        apply(self.client.transaction())

    def list(self, collection, field=None, value=None, limit=500):
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = self.client.collection(DOCUMENTS).where(filter=FieldFilter('collection', '==', collection))
        if field is not None:
            if field not in QUERY_FIELDS:
                raise ValueError('Query field needs an explicit Firestore index before release')
            q = q.where(filter=FieldFilter('filters.' + field, '==', fingerprint(value)))
        q = q.order_by('updated_at', direction='DESCENDING').order_by('key')
        return [json.loads(d.to_dict()['body_json']) for d in q.limit(min(max(int(limit), 1), 5000)).stream()]

    @contextmanager
    def lock(self, key):
        from google.cloud import firestore
        from .store import StoreBusy
        ref = self.client.collection('runtime_locks').document(fingerprint(key))
        owner = uuid.uuid4().hex

        @firestore.transactional
        def acquire(tx):
            snap = ref.get(transaction=tx)
            if snap.exists and snap.to_dict().get('expires', 0) > time.time():
                raise StoreBusy('This session is processing another request. Please retry shortly.')
            tx.set(ref, {'owner': owner, 'expires': time.time() + 600})
        acquire(self.client.transaction())
        try:
            yield
        finally:
            @firestore.transactional
            def release(tx):
                snap = ref.get(transaction=tx)
                if snap.exists and snap.to_dict().get('owner') == owner:
                    tx.delete(ref)
            release(self.client.transaction())
