"""Pleiades - roster_management. Plain software: collect, validate, summarise."""
from __future__ import annotations
import secrets
from collections import Counter
from zar_core.contracts import CapabilityRequest, CapabilityResult, utcnow
from zar_core.services import services
from .. import policies


def _validate(entry: dict, needs: list[str]) -> tuple[dict, list[str]]:
    cat = policies.catalog()
    limits, errors = cat["personalisation_limits"], []
    size = str(entry.get("size", "")).strip().upper()
    if size not in cat["sizes"]:
        errors.append(f"Size must be one of {', '.join(cat['sizes'])}.")
    name = str(entry.get("name", "")).strip()
    if not name:
        errors.append("Name is required.")
    elif "names" in needs and len(name) > limits["max_name_chars"]:
        errors.append(f"Printed name must be {limits['max_name_chars']} characters or fewer.")
    number = str(entry.get("number", "")).strip()
    if "numbers" in needs:
        if not number.isdigit() or not limits["number_min"] <= int(number) <= limits["number_max"]:
            errors.append(f"Number must be between {limits['number_min']} and {limits['number_max']}.")
    clean = {"name": name, "size": size}
    if "numbers" in needs:
        clean["number"] = str(int(number)) if number.isdigit() else number
    return clean, errors


def _status(doc: dict) -> dict:
    entries, expected = doc["entries"], doc["expected"]
    nums = [e["number"] for e in entries if e.get("number")]
    dupes = sorted({n for n in nums if nums.count(n) > 1}, key=lambda x: int(x))
    names = [e["name"].lower() for e in entries]
    problems = [f"Duplicate jersey number(s): {', '.join(dupes)}"] if dupes else []
    if len(entries) > expected:
        problems.append(f"{len(entries)} entries received but only {expected} pieces ordered.")
    warnings = ["Same name submitted more than once."] if len(set(names)) < len(names) else []
    complete = len(entries) >= expected
    return {"submitted": len(entries), "expected": expected, "missing": max(0, expected - len(entries)),
            "complete": complete, "ready": complete and not problems, "problems": problems,
            "warnings": warnings, "size_breakdown": dict(sorted(Counter(e["size"] for e in entries).items()))}


def run(req: CapabilityRequest) -> CapabilityResult:
    p, store = req.payload, services.store
    ok = lambda data, status="ok", q=None: CapabilityResult(
        request_id=req.request_id, capability=req.capability, status=status, data=data,
        questions=q or [], confidence=1.0, model_used="none")
    action = p["action"]
    if action == "create":
        token = secrets.token_urlsafe(24)
        store.set("rosters", token, {"token": token, "session_id": req.context.session_id,
                                     "tenant_id": req.context.tenant_id, "expected": int(p["expected"]),
                                     "needs": p.get("needs", []), "entries": [], "closed": False,
                                     "created_at": utcnow()})
        return ok({"token": token, "roster_path": f"/roster/{token}"})
    doc = store.get("rosters", p["token"])
    if not doc or doc["session_id"] != req.context.session_id or doc["tenant_id"] != req.context.tenant_id:
        raise ValueError("Roster not found for this session")
    if action == "submit":
        if doc["closed"]:
            return ok({"accepted": False}, "needs_input", ["This roster is closed."])
        clean, errors = _validate(p["entry"], doc["needs"])
        if errors:
            return ok({"accepted": False, **_status(doc)}, "needs_input", errors)
        same = [i for i, e in enumerate(doc["entries"]) if e["name"].lower() == clean["name"].lower()]
        if same:                                    # same name = correction of an earlier entry
            doc["entries"][same[0]] = clean
        else:
            doc["entries"].append(clean)
        store.set("rosters", doc["token"], doc)
        return ok({"accepted": True, "updated": bool(same), **_status(doc)})
    if action == "check":
        return ok(_status(doc))
    if action in {"close", "archive"}:
        if action == "close" and not _status(doc)["ready"]:
            return ok(_status(doc), "needs_input", ["Complete the roster and resolve its problems first."])
        doc["closed"] = True
        store.set("rosters", doc["token"], doc)
        return ok({**_status(doc), "entries": doc["entries"]})
    raise ValueError(f"Unknown roster action: {action}")
