"""Preserve legacy SQL tables as an explicitly separate, verified Firestore archive."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from google.cloud import firestore


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('snapshot')
    parser.add_argument('--project', required=True)
    parser.add_argument('--expected-tables', type=int, required=True)
    parser.add_argument('--expected-rows', type=int, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = json.loads(Path(args.snapshot).read_text())
    tables = source['tables']
    assert len(tables) == args.expected_tables
    assert sum(len(rows) for rows in tables.values()) == args.expected_rows
    expected = {}
    for table, rows in sorted(tables.items()):
        occurrences = Counter()
        for body in sorted(canonical(row) for row in rows):
            assert len(body.encode()) < 900_000
            digest = hashlib.sha256(body.encode()).hexdigest()
            ordinal = occurrences[digest]
            occurrences[digest] += 1
            identity = hashlib.sha256(canonical([table, digest, ordinal]).encode()).hexdigest()
            expected[identity] = {'table': table, 'body_json': body, 'occurrence': ordinal, 'sha256': digest}
    db = firestore.Client(project=args.project, database='(default)')
    collection = db.collection('zar_legacy_sql_rows')
    existing = {doc.id: doc.to_dict() for doc in collection.stream()}
    assert not set(existing) - set(expected), 'Unexpected target records; no overwrites allowed'
    assert all(expected[k] == v for k, v in existing.items()), 'Target conflict; no overwrites allowed'
    missing = [(key, value) for key, value in expected.items() if key not in existing]
    if args.apply:
        for start in range(0, len(missing), 300):
            batch = db.batch()
            for key, value in missing[start:start + 300]:
                batch.create(collection.document(key), value)
            batch.commit()
        actual = {doc.id: doc.to_dict() for doc in collection.stream()}
        assert actual == expected, 'Archive verification failed'
        metadata = {'tables': {table: len(rows) for table, rows in tables.items()},
                    'schema_json': canonical(source['schema']),
                    'source_sha256': hashlib.sha256(canonical(source).encode()).hexdigest(),
                    'verified_rows': len(expected), 'purpose': 'Legacy archive; not the active workflow store'}
        assert len(canonical(metadata).encode()) < 900_000
        ref = db.collection('zar_migration_archives').document('supabase-public-20261018')
        old = ref.get()
        if old.exists:
            assert old.to_dict() == metadata, 'Archive metadata conflict'
        else:
            ref.create(metadata)
    print(json.dumps({'tables': len(tables), 'rows': len(expected), 'applied_and_verified': args.apply}))


if __name__ == '__main__':
    main()
