"""Import a complete SQL CSV export; dry-run by default. Never print record bodies.

Export columns: collection,key,body,updated_at. Keep the original CSV as backup.
Freeze source writes and verify expected count before final import/cutover.
"""
import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zar_core.firestore_store import envelope, document_id, canonical


def load(path, expected):
    csv.field_size_limit(2_000_000)
    records = []
    seen = set()
    with open(path, encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            identity = (row['collection'], row['key'])
            if identity in seen:
                raise ValueError('Duplicate source key; import refused')
            seen.add(identity)
            timestamp = datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError('Source timestamps must include timezone')
            records.append(envelope(*identity, json.loads(row['body']), timestamp))
    if len(records) != expected:
        raise ValueError('Source count does not match expected count; possible truncated export')
    return records


def migrate(client, records, apply=False):
    target = client.collection('zar_documents')
    expected_ids = {document_id(r['collection'], r['key']) for r in records}
    existing = {s.id: s.to_dict() for s in target.stream()}
    if set(existing) - expected_ids:
        raise ValueError('Destination has extra records; use an isolated migration database')
    for record in records:
        old = existing.get(document_id(record['collection'], record['key']))
        if old is not None and old != record:
            raise ValueError('Destination conflict; no existing records were overwritten')
    if apply:
        for record in records:
            identity = document_id(record['collection'], record['key'])
            if identity not in existing:
                target.document(identity).create(record)
        actual = {s.id: s.to_dict() for s in target.stream()}
        wanted = {document_id(r['collection'], r['key']): r for r in records}
        if actual != wanted:
            raise ValueError('Destination verification failed; do not cut over')
    return len(records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('export_csv', type=Path)
    parser.add_argument('--expected-count', type=int, required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        records = load(args.export_csv, args.expected_count)
        from google.cloud import firestore
        client = firestore.Client(project=args.project, database=args.database)
        count = migrate(client, records, args.apply)
        print(json.dumps({'records': count, 'applied_and_verified': args.apply,
              'source_sha256': hashlib.sha256(args.export_csv.read_bytes()).hexdigest()}))
    except Exception:
        # SDK failures may include private document contents; do not echo them.
        print('Migration stopped. Check source format/count, destination conflicts and cloud access. No source data was modified.', file=sys.stderr)
        sys.exit(1)
