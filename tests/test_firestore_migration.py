import importlib.util
import json
from pathlib import Path
import pytest
from zar_core.firestore_store import envelope, document_id

spec = importlib.util.spec_from_file_location('migration', Path(__file__).parents[1] / 'scripts/migrate_firestore.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_nested_arrays_and_reserved_names_roundtrip():
    body = {'collection': 'user data', 'nested': [[1, 2], [3]], 'updated_at': None}
    assert json.loads(envelope('sessions', 'a/b', body)['body_json']) == body
    assert document_id('a/b', 'c') != document_id('a', 'b/c')


def test_large_documents_fail_without_truncation():
    with pytest.raises(ValueError):
        envelope('sessions', 'one', {'data': 'x' * 900_001})


def test_export_count_and_duplicates(tmp_path):
    path = tmp_path / 'export.csv'
    path.write_text('collection,key,body,updated_at\nsessions,one,{},2026-10-18T00:00:00+00:00\n')
    assert len(migration.load(path, 1)) == 1
    with pytest.raises(ValueError):
        migration.load(path, 2)
    with path.open('a') as stream:
        stream.write('sessions,one,{},2026-10-18T00:00:00+00:00\n')
    with pytest.raises(ValueError):
        migration.load(path, 2)


class Target:
    def __init__(self, values):
        self.values = values
    def stream(self):
        from types import SimpleNamespace
        return [SimpleNamespace(id=k, to_dict=lambda v=v: v) for k, v in self.values.items()]
    def document(self, key):
        from types import SimpleNamespace
        return SimpleNamespace(create=lambda value: self.values.setdefault(key, value))


class Client:
    def __init__(self, values):
        self.target = Target(values)
    def collection(self, name):
        assert name == 'zar_documents'
        return self.target


def test_dry_run_conflict_and_resumable_import():
    row = envelope('sessions', 'one', {'status': 'pending'})
    client = Client({})
    migration.migrate(client, [row])
    assert not client.target.values
    migration.migrate(client, [row], True)
    migration.migrate(client, [row], True)
    assert len(client.target.values) == 1
    changed = dict(row, body_json='{}')
    with pytest.raises(ValueError):
        migration.migrate(client, [changed], True)
    assert list(client.target.values.values()) == [row]
