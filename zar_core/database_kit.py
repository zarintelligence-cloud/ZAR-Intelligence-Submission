"""Read-only migration of the website prototype's SQLite sessions into an archive.

Archive records deliberately do not become current runtime sessions: the kit has
no original artwork, and its historic quotes/confirmations are not new approvals.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from .contracts import utcnow


def read_kit(path: Path) -> tuple[str, list[dict]]:
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Expected a SQLite kit of at most 20 MB")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
        if not {"id", "owner", "state", "rev", "data", "created_at", "updated_at"} <= columns:
            raise ValueError("This database does not have the website sessions schema")
        rows = db.execute("SELECT id,owner,state,rev,data,created_at,updated_at FROM sessions ORDER BY id LIMIT 501").fetchall()
        if len(rows) > 500:
            raise ValueError("Import at most 500 sessions per kit")
        result = []
        for row in rows:
            if len(row["data"].encode()) > 500_000:
                raise ValueError("Session data exceeds 500 KB")
            data = json.loads(row["data"])
            if not isinstance(data, dict) or data.get("id") != row["id"] or data.get("state") != row["state"]:
                raise ValueError("Session JSON does not match its database row")
            if not isinstance(data.get("spec", {}), dict) or not isinstance(data.get("roster", []), list):
                raise ValueError("Invalid session specification or roster")
            result.append({"original_id": row["id"], "source_owner": row["owner"],
                           "state": row["state"], "source_rev": row["rev"],
                           "created_at": row["created_at"], "updated_at": row["updated_at"],
                           "data": data})
        return digest, result
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError("Cannot read a valid website database kit") from exc
    finally:
        db.close()


def import_kit(store, path: Path, tenant_id="store6") -> dict:
    digest, rows = read_kit(path)  # Validate the whole input before any write.
    added = 0
    with store.lock("kit-" + digest[:32]):
        for row in rows:
            key = hashlib.sha256((tenant_id + digest + row["original_id"]).encode()).hexdigest()[:32]
            if store.get("imported_sessions", key):
                continue
            store.set("imported_sessions", key, {**row, "archive_id": key, "tenant_id": tenant_id,
                      "source_sha256": digest, "source_file": path.name, "imported_at": utcnow(),
                      "source_kind": "website-prototype", "artwork_available": False})
            added += 1
    return {"added": added, "existing": len(rows) - added, "total": len(rows), "sha256": digest,
            "note": "Historical snapshots preserved. No current orders or approvals were overwritten."}


def summarize(row: dict) -> dict:
    d = row["data"]
    q, delivery = d.get("quote") or {}, d.get("delivery") or {}
    risk = []
    if q.get("overBudget"):
        risk.append("Over budget")
    if delivery.get("probability", 1) < .8:
        risk.append("Delivery risk")
    if row["state"] == "asking":
        risk.append("Brief incomplete")
    return {k: row[k] for k in ("archive_id", "original_id", "state", "source_owner", "source_rev", "updated_at")} | {
        "title": d.get("title", "Imported order"), "spec": d.get("spec", {}), "total": q.get("total"),
        "risk": risk, "roster_count": len(d.get("roster", [])), "historical": True, "artwork_available": False}


def replay_brief(row: dict, today: date | None = None) -> str:
    """Make the changed clock explicit; never copy a historical confirmation."""
    d = row["data"]
    spec = d.get("spec", {})
    if row["state"] == "asking":
        return "We need cricket jerseys for our team."
    today = today or date.today()
    old_start = date.fromisoformat(row["created_at"][:10])
    deadline = date.fromisoformat(spec["deadline"])
    remaining = max(1, min(365, (deadline - old_start).days))
    new_deadline = today + timedelta(days=remaining)
    garment = str(spec.get("garment", "cricket jersey"))
    if garment == "cricket jersey":
        garment = "cricket jerseys"
    if garment == "round neck tee":
        garment = "round neck t-shirts"
    return (f"{spec['quantity']} {garment}, {spec.get('sleeve', 'half')} sleeve, "
            f"{', '.join(spec.get('colors', []))}, "
            f"{'names and numbers' if spec.get('personalisation') else 'no names'}, "
            f"budget {spec.get('budget', 0)} INR, by {new_deadline.isoformat()}.")
