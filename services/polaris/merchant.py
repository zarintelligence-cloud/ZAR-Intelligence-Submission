"""Live read models and authorized operations for the supplied V5 website."""
from __future__ import annotations
from collections import Counter
from datetime import datetime
import json
import secrets
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from zar_core.audit import record_hash, sealed
from zar_core.contracts import utcnow
from .identity import viewer, writes, require_role, scoped, backend, ROLES

router = APIRouter(prefix="/api/console", dependencies=[Depends(writes)])
AGENT = {"requirements_analysis": "rosetta", "artwork_analysis": "spectra", "proof_generation": "hubble", "roster_management": "pleiades", "pricing": "kepler", "compose_reply": "polaris", "order_handoff": "polaris", "fulfillment_confidence": "horizon"}

def millis(value):
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None

def stores_for(user):
    m = backend()
    out = []
    for tid in user.get("stores", []):
        t = m.tenant_profile(tid)
        out.append({"id": tid, "name": t["name"], "domain": t.get("domain", ""), "merch": t.get("segment", "Custom apparel"), "status": "connected", "garments": [r["garment"] for r in t.get("storefront", [])]})
    return out

def order_view(s):
    m = backend()
    sp, art, rs = s.get("spec") or {}, s.get("artwork") or {}, s.get("roster_status") or {}
    raw = ((s.get("quote") or {}).get("quote") or {})
    breakdown = raw.get("breakdown", {})
    quote = {"base": breakdown.get("garment", 0), "pr": breakdown.get("print", 0), "nn": breakdown.get("personalisation", 0), "mult": raw.get("tier_multiplier", 1), "unit": raw.get("unit_price", 0), "setup": raw.get("setup_fee", 0), "total": raw["total"]} if raw else None
    sid = s["session_id"]
    pub = m.public(s)
    return {"id": sid, "rev": s["rev"], "store": s["tenant_id"], "team": s.get("title") or (s.get("transcript") or ["Custom order"])[0][:100],
        "contact": "Customer", "channel": s.get("channel", "Web chat"), "language": s.get("locale", "en"),
        "createdAt": millis(s["created_at"]), "updatedAt": millis(s.get("updated_at", s["created_at"])),
        "state": "failed" if s.get("manual_hold") or s.get("retry_step") else s["state"], "coreState": s["state"],
        "garment": sp.get("garment_type", "Not specified"), "qty": sp.get("quantity") or 0, "colours": sp.get("colours", []),
        "deadline": millis(sp.get("deadline")), "budget": sp.get("budget"), "personalisation": sp.get("personalisation") or [],
        "firstReplyMs": next((max(0, millis(msg['at']) - millis(s['created_at'])) for msg in s.get('messages', []) if msg['role'] != 'customer'), None),
        "logo": art.get("measured") or {}, "print": art.get("print_method"), "printReason": "; ".join(art.get("reasons") or []),
        "proofs": [{**p, "url": f"/v1/sessions/{sid}/proofs/{p['proof_id']}", "ref": None} for p in s.get("proofs", [])],
        "chosen": s.get("selected_proof"), "quote": quote,
        "roster": {"submitted": rs.get("submitted", 0), "expected": rs.get("expected", sp.get("quantity", 0)), "dupes": rs.get("problems", []), "sizes": list((rs.get("size_breakdown") or {}).items())} if s.get("roster") else None,
        "rosterPath": pub.get("roster_path"), "delivery": millis(sp.get("deadline")), "fulfillment": s.get("fulfillment"),
        "history": [{"state": h.get("to", s["state"]), "at": millis(h["at"])} for h in s.get("history", [])],
        "messages": [{"who": "cust" if msg["role"] == "customer" else "agent", "text": msg["text"], "at": millis(msg["at"])} for msg in s.get("messages", [])],
        "questions": s.get("questions", []), "notes": [], "synthetic": bool(s.get("synthetic")), "manualHold": bool(s.get("manual_hold")),
        "allowed": pub["allowed_events"], "productionReady": bool(s.get("production_spec"))}

@router.get("/snapshot")
def snapshot(response: Response, user=Depends(viewer)):
    rt = backend().APP.runtime
    orders, runs, traces, audits, events, reviewed = [], [], [], [], [], {}
    truncated = False
    for tid in user["stores"]:
        sessions = rt.store.list("sessions", "tenant_id", tid, limit=1000)
        truncated |= len(sessions) >= 1000
        for s in sessions:
            item = order_view(s)
            if user["role"] not in {"administrator", "operations"}:
                item["rosterPath"] = None
                import re
                for msg in item["messages"]:
                    msg["text"] = re.sub(r"/roster/[A-Za-z0-9_-]+", "[restricted roster link]", msg["text"])
            orders.append(item)
        ids = {s["session_id"] for s in sessions}
        for r in rt.store.list("agent_runs", "tenant_id", tid, limit=5000):
            if r["session_id"] not in ids:
                continue
            run = {"id": r["request_id"], "traceId": r.get("step_id"), "orderId": r["session_id"], "store": tid, "agent": AGENT.get(r["capability"], "polaris"), "op": r["capability"].replace("_", " "), "ms": r["latency_ms"], "status": r["status"], "model": r.get("model_used") or "code", "at": millis(r["at"]), "tin": None, "tout": None}
            runs.append(run)
        run_by = {r["id"]: r for r in runs}
        for st in rt.store.list("steps", "tenant_id", tid, limit=5000):
            if st["session_id"] not in ids:
                continue
            at = millis(st["at"])
            spans = [{**run_by[k], "start": max(0, run_by[k]["at"] - at)} for k in st["run_ids"] if k in run_by]
            traces.append({"id": st["step_id"], "orderId": st["session_id"], "store": tid, "name": st["event"].replace("_", " "), "at": at, "spans": spans, "cursor": st["duration_ms"], "status": "error" if st.get("error") else "ok"})
            events.append({"id": st["step_id"], "orderId": st["session_id"], "store": tid, "agent": spans[-1]["agent"] if spans else "polaris", "at": at, "text": st.get("message") or st["event"].replace("_", " ")})
        if user["role"] in {"administrator", "auditor"}:
            for collection in ("decisions", "operator_actions"):
                for d in rt.store.list(collection, "tenant_id", tid, limit=5000):
                    if d["session_id"] not in ids:
                        continue
                    audits.append({"id": d["decision_id"], "orderId": d["session_id"], "store": tid, "agent": d.get("agent", "polaris"), "at": millis(d["created_at"]), "decision": d["action"], "basis": "; ".join(d.get("reasons", [])), "rule": d["engine"], "kind": "action" if collection == "operator_actions" else "decision", "hash": d.get("record_hash") or record_hash(d), "integrity": "verified" if d.get("record_hash") == record_hash(d) else "legacy" if not d.get("record_hash") else "MISMATCH"})
            for r in rt.store.list("audit_reviews", "tenant_id", tid, limit=5000):
                reviewed[r["decision_id"]] = {"by": r["name"], "at": millis(r["at"])}
    for arr, key in ((orders, "updatedAt"), (runs, "at"), (traces, "at"), (audits, "at"), (events, "at")):
        arr.sort(key=lambda x: x[key] or 0, reverse=True)
    if user['role'] not in {'administrator', 'operations'}:
        import re
        def scrub(value):
            if isinstance(value, str):
                return re.sub(r'/roster/[A-Za-z0-9_-]+', '[restricted roster link]', value)
            if isinstance(value, list): return [scrub(v) for v in value]
            if isinstance(value, dict): return {k: scrub(v) for k, v in value.items()}
            return value
        orders, events, audits = scrub(orders), scrub(events), scrub(audits)
    response.headers["Cache-Control"] = "no-store"
    return {"orders": orders, "runs": runs, "traces": traces, "audits": audits, "events": events[:200], "reviewed": reviewed, "stores": stores_for(user), "truncated": truncated, "scope": "Up to 1,000 orders and 5,000 activity records per store"}

class Create(BaseModel):
    tenant_id: str = Field("store6", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    message: str = Field(min_length=3, max_length=4000)
    logo_b64: str = Field(max_length=7 * 1024 * 1024)
    logo_mime: str = "image/png"
    title: str | None = Field(None, max_length=80)
    synthetic: bool = False

@router.post("/orders", status_code=201)
def create(body: Create, user=Depends(viewer)):
    require_role(user, "administrator", "operations")
    scoped(user, body.tenant_id)
    m = backend()
    result = m.create_session(m.NewSession(**body.model_dump()))
    sid = result["session_id"]
    m.APP.runtime.store.update("sessions", sid, {"console_managed": True, "created_by": user["id"], "synthetic": body.synthetic})
    return {"id": sid}

class Action(BaseModel):
    event: str = Field(max_length=64)
    data: dict = Field(default_factory=dict)
    rev: int = Field(ge=0)
    key: str = Field(min_length=8, max_length=80)

@router.post("/orders/{sid}/actions")
def action(sid: str, body: Action, user=Depends(viewer)):
    require_role(user, "administrator", "operations")
    rt = backend().APP.runtime
    if len(json.dumps(body.data)) > 16000:
        raise HTTPException(413, "Action is too large")
    from zar_core.workflow import InvalidTransition
    from zar_core.store import StoreBusy
    try:
        with rt.store.lock(sid):
            s = rt.store.get("sessions", sid)
            if not s:
                raise HTTPException(404, "Order not found")
            scoped(user, s["tenant_id"])
            fingerprint = record_hash({"event": body.event, "data": body.data})
            previous = (s.get("console_keys") or {}).get(body.key)
            if previous:
                if previous != fingerprint:
                    raise HTTPException(409, "This action key has already been used")
                return {"ok": True}
            if body.rev != s["rev"]:
                raise HTTPException(409, "The order changed. Refresh before continuing")
            if body.event in {"handoff", "resume"}:
                if s["state"] in {"confirmed", "cancelled"}:
                    raise HTTPException(409, "This order is closed")
                s["manual_hold"] = body.event == "handoff"
                s["rev"] += 1
                s["updated_at"] = utcnow()
                if body.event == "resume":
                    s["failure_count"] = 0
                rt.store.set("sessions", sid, s)
            elif body.event in {"remind", "resend"}:
                expected = "collecting" if body.event == "remind" else "awaiting_confirmation"
                if s["state"] != expected or s.get("manual_hold"):
                    raise HTTPException(409, "This action is not available in the current state")
                text = ("Please complete the team roster: " + s["roster"]["roster_path"]) if body.event == "remind" else next((msg["text"] for msg in reversed(s["messages"]) if msg.get("state") == "awaiting_confirmation"), "Your quote is ready. Please review it in this conversation.")
                s["messages"].append({"role": "assistant", "text": text, "at": utcnow()})
                s["rev"] += 1
                s["updated_at"] = utcnow()
                rt.store.set("sessions", sid, s)
            else:
                if body.event not in {"customer_reply", "proof_chosen", "roster_entry", "confirmed", "cancel", "retry", "change_request"}:
                    raise HTTPException(422, "Unsupported action")
                validated = backend().EventIn(event=body.event, data=body.data, idempotency_key=body.key)
                if s.get("synthetic") and body.event == "confirmed" and backend().config.ORDER_WEBHOOK_URL:
                    raise HTTPException(409, "Synthetic orders cannot enter a live production handoff")
                if body.event == "confirmed" and not (s.get("quote") or {}).get("within_budget", True) and not body.data.get("acceptOverBudget"):
                    raise HTTPException(422, "Acknowledge the budget overrun before confirming")
                s = rt.execute_event_unlocked(sid, validated.event, validated.data, body.key)
            keys = {**s.get("console_keys", {}), body.key: fingerprint}
            rt.store.update("sessions", sid, {"console_keys": dict(list(keys.items())[-200:])})
            audit = sealed({"decision_id": uuid.uuid4().hex, "session_id": sid, "tenant_id": s["tenant_id"], "created_at": utcnow(), "agent": "operator", "action": body.event, "engine": "operator-authorized", "reasons": [f"{user['name']} ({user['role']})"], "actor_id": user["id"]})
            rt.store.set("operator_actions", audit["decision_id"], audit)
            return {"ok": True, "message": "Message saved to the customer web conversation" if body.event in {"remind", "resend"} else "Order updated"}
    except (InvalidTransition, StoreBusy) as exc:
        raise HTTPException(409, str(exc)) from exc

@router.post("/reviews/{decision_id}")
def review(decision_id: str, user=Depends(viewer)):
    require_role(user, "administrator", "auditor")
    store = backend().APP.runtime.store
    d = store.get("decisions", decision_id) or store.get("operator_actions", decision_id)
    if not d:
        raise HTTPException(404, "Decision not found")
    scoped(user, d["tenant_id"])
    if d.get("record_hash") and d["record_hash"] != record_hash(d):
        raise HTTPException(409, "Record integrity check failed; investigate before reviewing")
    store.set("audit_reviews", decision_id, {"decision_id": decision_id, "tenant_id": d["tenant_id"], "user_id": user["id"], "name": user["name"], "at": utcnow(), "record_hash": record_hash(d)})
    return {"ok": True}

class Enquiry(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    message: str = Field(min_length=3, max_length=4000)

def save_enquiry(body: Enquiry):
    from .identity import digest
    store = backend().APP.runtime.store
    key = digest(body.email.lower())
    with store.lock("enquiry-" + key):
        previous = store.get("enquiry_limits", key) or {"at": 0}
        if time.time() - previous["at"] < 60:
            raise HTTPException(429, "Please wait a minute before sending another enquiry")
        store.set("enquiry_limits", key, {"at": time.time()})
        ref = uuid.uuid4().hex
        store.set("website_leads", ref, {**body.model_dump(), "id": ref, "at": utcnow(), "status": "new"})
    return {"id": ref}

@router.get("/administration")
def administration(user=Depends(viewer)):
    require_role(user, "administrator")
    store = backend().APP.runtime.store
    return {"members": [m for m in store.list("console_members", limit=1000) if set(m["stores"]).issubset(user["stores"])],
            "stores": stores_for(user), "connections": {"Database": backend().config.STORE_MODE, "Reasoning": backend().config.LLM_MODE,
            "Web chat": "Available", "WhatsApp": "Not connected", "Email": "Not connected", "Instagram": "Not connected", "Order handoff": "Webhook configured" if backend().config.ORDER_WEBHOOK_URL else "Sandbox"},
            "leads": store.list("website_leads", limit=200) if "store6" in user["stores"] else []}

class Member(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=80)
    role: str
    stores: list[str] = Field(min_length=1, max_length=20)
    active: bool = True

@router.post("/members")
def member(body: Member, user=Depends(viewer)):
    require_role(user, "administrator")
    if body.role not in ROLES:
        raise HTTPException(422, "Choose one of the four workspace roles")
    for tid in body.stores:
        scoped(user, tid)
        backend().tenant_profile(tid)
    existing = backend().APP.runtime.store.get("console_members", body.id)
    if existing and not set(existing["stores"]).issubset(user["stores"]):
        raise HTTPException(403, "Cannot change a member outside your stores")
    if body.id == user["id"] and (body.role != "administrator" or not body.active):
        raise HTTPException(409, "Keep your own administrator access; ask another administrator to change it")
    backend().APP.runtime.store.set("console_members", body.id, body.model_dump())
    return {"ok": True}
