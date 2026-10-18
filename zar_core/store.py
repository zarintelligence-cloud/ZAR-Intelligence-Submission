"""Document store abstraction. Memory for dev/tests, Firestore in production."""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import json, sqlite3, threading, time, uuid
from typing import Any, Protocol


class Store(Protocol):
    def lock(self, key: str): ...
    def get(self, collection: str, key: str) -> dict[str, Any] | None: ...
    def set(self, collection: str, key: str, value: dict[str, Any]) -> None: ...
    def update(self, collection: str, key: str, patch: dict[str, Any]) -> None: ...
    def list(self, collection: str, field: str | None = None, value: Any = None,
             limit: int = 500) -> list[dict[str, Any]]: ...


class MemoryStore:
    def __init__(self) -> None:
        self.db: dict[str, dict[str, dict[str, Any]]] = {}
        self._guard = threading.RLock()

    @contextmanager
    def lock(self, key):
        with self._guard:
            yield

    def get(self, collection, key):
        value = self.db.get(collection, {}).get(key)
        return deepcopy(value) if value is not None else None

    def set(self, collection, key, value):
        self.db.setdefault(collection, {})[key] = deepcopy(value)

    def update(self, collection, key, patch):
        self.db.setdefault(collection, {}).setdefault(key, {}).update(deepcopy(patch))

    def list(self, collection, field=None, value=None, limit=500):
        docs = [d for d in self.db.get(collection, {}).values() if field is None or d.get(field) == value]
        return deepcopy(docs[-limit:])


class FirestoreStore:
    def __init__(self) -> None:
        from google.cloud import firestore
        self.client = firestore.Client()

    @contextmanager
    def lock(self, key):
        """Serialize a session across Cloud Run instances without retrying external effects.

        Lease exceeds the 300-second front-desk request limit. A killed request's lease
        expires after 10 minutes. Competing requests return a retryable conflict.
        """
        from google.cloud import firestore
        ref = self.client.collection("runtime_locks").document(key)
        owner = uuid.uuid4().hex
        @firestore.transactional
        def acquire(tx):
            snap = ref.get(transaction=tx)
            if snap.exists and snap.to_dict().get("expires", 0) > time.time():
                raise StoreBusy("This session is processing another request. Please retry shortly.")
            tx.set(ref, {"owner": owner, "expires": time.time() + 600})
        acquire(self.client.transaction())
        try:
            yield
        finally:
            @firestore.transactional
            def release(tx):
                snap = ref.get(transaction=tx)
                if snap.exists and snap.to_dict().get("owner") == owner:
                    tx.delete(ref)
            release(self.client.transaction())

    def get(self, collection, key):
        snap = self.client.collection(collection).document(key).get()
        return snap.to_dict() if snap.exists else None

    def set(self, collection, key, value):
        self.client.collection(collection).document(key).set(value)

    def update(self, collection, key, patch):
        self.client.collection(collection).document(key).set(patch, merge=True)

    def list(self, collection, field=None, value=None, limit=500):
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = self.client.collection(collection)
        if field is not None:
            q = q.where(filter=FieldFilter(field, "==", value))
        return [d.to_dict() for d in q.limit(limit).stream()]


class StoreBusy(RuntimeError):
    pass


class SQLiteStore:
    """Durable local documents with cross-process session leases; no extra server."""
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS documents (collection TEXT, key TEXT, body TEXT NOT NULL, PRIMARY KEY(collection,key))")
            db.execute("CREATE TABLE IF NOT EXISTS locks (key TEXT PRIMARY KEY, owner TEXT, expires REAL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, collection, key):
        with self.connect() as db:
            row = db.execute("SELECT body FROM documents WHERE collection=? AND key=?", (collection, key)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, collection, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO documents VALUES (?,?,?) ON CONFLICT(collection,key) DO UPDATE SET body=excluded.body",
                       (collection, key, json.dumps(value, ensure_ascii=False)))

    def update(self, collection, key, patch):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM documents WHERE collection=? AND key=?", (collection, key)).fetchone()
            value = json.loads(row[0]) if row else {}
            value.update(patch)
            db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?)", (collection, key, json.dumps(value)))

    def list(self, collection, field=None, value=None, limit=500):
        with self.connect() as db:
            rows = db.execute("SELECT body FROM documents WHERE collection=? ORDER BY rowid", (collection,)).fetchall()
        docs = [json.loads(r[0]) for r in rows]
        return [d for d in docs if field is None or d.get(field) == value][-limit:]

    @contextmanager
    def lock(self, key):
        owner, deadline = uuid.uuid4().hex, time.monotonic() + 30
        while True:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM locks WHERE expires < ?", (time.time(),))
                inserted = db.execute("INSERT OR IGNORE INTO locks VALUES (?,?,?)", (key, owner, time.time() + 600)).rowcount
            if inserted:
                break
            if time.monotonic() > deadline:
                raise StoreBusy("This session is processing another request. Please retry shortly.")
            time.sleep(0.05)
        try:
            yield
        finally:
            with self.connect() as db:
                db.execute("DELETE FROM locks WHERE key=? AND owner=?", (key, owner))


def make_store(mode: str) -> Store:
    from . import config
    if mode == "supabase":
        from .supabase_store import SupabaseStore
        return SupabaseStore(config.SUPABASE_URL, config.SUPABASE_SECRET_KEY)
    if mode == "firestore":
        from .firestore_store import FirestoreDocumentStore
        return FirestoreDocumentStore()
    if mode == "sqlite": return SQLiteStore(Path(config.DATA_DIR) / "zar.sqlite3")
    if mode == "memory": return MemoryStore()
    raise ValueError(f"Unknown store mode: {mode}")
