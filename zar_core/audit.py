"""Deterministic SHA-256 record digests. Not a signature or external notarization."""
import hashlib
import json

def record_hash(record):
    return hashlib.sha256(json.dumps({k: v for k, v in record.items() if k != "record_hash"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def sealed(record):
    return {**record, "record_hash": record_hash(record)}
