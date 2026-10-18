"""Compatibility view for the user's supplied interface, backed by the real Core.

The interface speaks the website prototype's shape. This adapter only translates
that shape; pricing, proofs, roster validation and decisions stay in Retail Pack.
"""
from __future__ import annotations
import base64
import hashlib
import json
import uuid
from typing import Any, Literal
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from zar_core.workflow import InvalidTransition
from zar_core.store import StoreBusy
from domains.retail.capabilities.pleiades import _validate

router = APIRouter()
AGENTS = dict(requirements_analysis="Rosetta", artwork_analysis="Spectra", proof_generation="Hubble",
              roster_management="Pleiades", pricing="Kepler", fulfillment_confidence="Horizon",
              compose_reply="Voice", order_handoff="Polaris")


def backend():
    from . import main
    return main


def quote_view(q):
    b = q.get("breakdown", {})
    return {**q, "base": b.get("garment", 0), "print": b.get("print", 0),
            "personalisation": b.get("personalisation", 0), "tier": q.get("tier_multiplier", 1),
            "unit": q.get("unit_price", 0), "setup": q.get("setup_fee", 0)}


def session_view(s):
    m = backend()
    rt = m.APP.runtime
    public = m.public(s)
    spec, art, fulfillment = s.get("spec") or {}, s.get("artwork") or {}, s.get("fulfillment") or {}
    pricing = s.get("quote") or {}
    quote = None
    if pricing.get("quote"):
        quote = {**quote_view(pricing["quote"]), "options": [quote_view(q) for q in pricing.get("options", [])],
                 "overBudget": not pricing.get("within_budget", True),
                 "taxNote": "Tax follows the configured pricing policy. Delivery charges are not included."}
    roster = rt.store.get("rosters", (s.get("roster") or {}).get("token", "")) if s.get("roster") else None
    trace = []
    for step in rt.trace(s["session_id"])["steps"]:
        for run in step["runs"]:
            trace.append({"id": run["request_id"], "agent": AGENTS.get(run["capability"], "Polaris"),
                          "capability": run["capability"], "status": "completed" if run["status"] == "ok" else run["status"],
                          "summary": step.get("message") or run["capability"].replace("_", " "),
                          "output": run["response"], "at": run["at"], "release": run["release"].get("release_id", "")})
    decisions = rt.store.list("decisions", "session_id", s["session_id"])
    return {"id": s["session_id"], "title": s.get("title") or (s.get("transcript") or ["Order"])[0][:80],
            "state": s["state"], "rev": s["rev"], "createdAt": s["created_at"], "updatedAt": s.get("updated_at", s["created_at"]),
            "spec": {**spec, "garment": spec.get("garment_type", ""), "colors": spec.get("colours", []),
                     "personalisation": bool(spec.get("personalisation")), "method": (quote or {}).get("method") or art.get("print_method", "")},
            "artwork": {"asset": f"/v1/sessions/{s['session_id']}/logo", "hash": s.get("logo_sha256"),
                        "gradient": art.get("measured", {}).get("gradient", False)},
            "proof": next((i for i,p in enumerate(s.get("proofs", [])) if p["proof_id"] == s.get("selected_proof")), None),
            "proofs": public["proofs"], "roster": (roster or {}).get("entries", []), "quote": quote,
            "delivery": {"probability": fulfillment["p_on_time"], "p50": fulfillment["days_p50"],
                         "p90": fulfillment["days_p90"], "simulations": fulfillment["simulations"]} if fulfillment else None,
            "trace": trace, "messages": [{**x, "role": "user" if x["role"] == "customer" else x["role"]} for x in s.get("messages", [])],
            "questions": s.get("questions", []), "keys": [], "release": s["release"],
            "decisions": [{**d, "id": d["decision_id"], "selected": d["action"], "reason": "; ".join(d["reasons"])} for d in decisions],
            "synthetic": s.get("synthetic", False), "retryable": bool(s.get("retry_step")),
            "production_spec": public.get("production_spec"), "roster_path": public.get("roster_path")}


class Start(BaseModel):
    message: str = Field(min_length=3, max_length=4000)
    artwork: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    rev: int = Field(ge=0)
    event: Literal["customer_reply", "proof_chosen", "roster_entry", "roster_complete", "confirmed", "cancel", "retry"]
    payload: dict[str, Any] = Field(default_factory=dict)
    key: str = Field(min_length=8, max_length=80)


@router.get("/api/sessions")
def sessions(id: str | None = None, x_zar_admin: str | None = Header(None)):
    m = backend(); m.admin(x_zar_admin)
    if id:
        s = m.APP.runtime.store.get("sessions", id)
        if not s or s["tenant_id"] != "store6":
            raise HTTPException(404, "Order not found")
        return session_view(s)
    rows = m.APP.runtime.store.list("sessions", "tenant_id", "store6")
    return [session_view(s) for s in sorted(rows, key=lambda s: s.get("updated_at", s["created_at"]), reverse=True)]


@router.post("/api/sessions", status_code=201)
def start(body: Start, x_zar_admin: str | None = Header(None)):
    m = backend(); m.admin(x_zar_admin)
    asset = body.artwork.get("asset")
    if asset:
        uploaded = m.APP.runtime.store.get("ui_uploads", str(asset))
        if not uploaded:
            raise HTTPException(422, "Upload your logo again")
        raw, mime = m.APP.runtime.assets.get(uploaded["ref"])
    else:
        raw, mime = m.demo.logo("strikers" if body.artwork.get("gradient") else "titans"), "image/png"
    result = m.create_session(m.NewSession(message=body.message, logo_b64=base64.b64encode(raw).decode(), logo_mime=mime))
    sid = result["session_id"]
    with m.APP.runtime.store.lock(sid):
        m.APP.runtime.store.update("sessions", sid, {"synthetic": not bool(asset), "scenario": "reference-ui" if not asset else None})
    return session_view(m.APP.runtime.store.get("sessions", sid))


@router.patch("/api/sessions")
def event(body: Event, x_zar_admin: str | None = Header(None)):
    m = backend(); m.admin(x_zar_admin)
    rt = m.APP.runtime
    if len(json.dumps(body.payload)) > 100_000:
        raise HTTPException(413, "Event is too large")
    fingerprint = hashlib.sha256(json.dumps([body.event, body.payload], sort_keys=True).encode()).hexdigest()
    try:
        with rt.store.lock(body.id):
            s = rt.store.get("sessions", body.id)
            if not s or s["tenant_id"] != "store6":
                raise HTTPException(404, "Order not found")
            prior = (s.get("reference_keys") or {}).get(body.key)
            if prior:
                if prior != fingerprint:
                    raise HTTPException(409, "Request key was already used for another action")
                return session_view(s)
            if s["rev"] != body.rev:
                raise HTTPException(409, "This order changed. Reopen it from Orders before continuing.")
            data = {}
            if body.event == "customer_reply":
                text = body.payload.get("message")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
                    raise HTTPException(422, "Enter a reply of 1–4000 characters")
                data = {"text": text}
            elif body.event == "proof_chosen":
                index = body.payload.get("proof")
                if type(index) is not int or not 0 <= index < len(s.get("proofs", [])):
                    raise HTTPException(422, "Choose a valid proof")
                data = {"proof_id": s["proofs"][index]["proof_id"]}
            elif body.event == "confirmed":
                if s.get("synthetic") and m.config.ORDER_WEBHOOK_URL:
                    raise HTTPException(409, "Sample artwork is for sandbox demonstrations. Start a real order with an uploaded logo.")
                if s.get("quote") and not s["quote"].get("within_budget", True) and not body.payload.get("acceptOverBudget"):
                    raise HTTPException(422, "Acknowledge the budget overrun before confirming")
            if body.event == "roster_entry":
                if s["state"] != "collecting":
                    raise HTTPException(409, "This roster is not accepting entries")
                entries = body.payload.get("entries")
                if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
                    raise HTTPException(422, "Submit 1–200 roster entries")
                doc = rt.store.get("rosters", s["roster"]["token"])
                prepared = []
                proposed = {e["name"].lower(): e for e in doc["entries"]}
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise HTTPException(422, "Invalid roster entry")
                    clean, errors = _validate(entry, doc["needs"])
                    if errors:
                        raise HTTPException(422, " ".join(errors))
                    proposed[clean["name"].lower()] = clean
                    prepared.append(clean)
                nums = [e["number"] for e in proposed.values() if e.get("number")]
                if len(nums) != len(set(nums)) or len(proposed) > doc["expected"]:
                    raise HTTPException(422, "Use unique jersey numbers and do not exceed the order quantity")
                for i, entry in enumerate(prepared):
                    s = rt.execute_event_unlocked(body.id, "roster_entry", {"entry": entry}, f"{body.key}-{i}")
                    if s.get("retry_step"):
                        break
            elif body.event == "roster_complete" and s["state"] == "awaiting_confirmation":
                pass  # Core automatically quotes once its validated roster is complete.
            else:
                s = rt.execute_event_unlocked(body.id, body.event, data, body.key)
            if not s.get("retry_step"):
                keys = {**s.get("reference_keys", {}), body.key: fingerprint}
                rt.store.update("sessions", body.id, {"reference_keys": dict(list(keys.items())[-200:])})
            return session_view(s)
    except (InvalidTransition, StoreBusy) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/assets")
async def upload(request: Request, x_zar_admin: str | None = Header(None)):
    m = backend(); m.admin(x_zar_admin)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > m.MAX_LOGO_BYTES:
            raise HTTPException(413, "Logo must be 5 MB or smaller")
    mime = request.headers.get("content-type", "")
    # Match the image checks on the normal public intake before saving an upload.
    import io, warnings
    from PIL import Image
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 20_000_000 or Image.MIME[image.format] != mime:
                    raise ValueError("Unsupported image")
                image.verify()
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise HTTPException(422, "Use a valid PNG, JPEG or WebP under 20 megapixels")
    key = uuid.uuid4().hex
    ref = m.APP.runtime.assets.put(bytes(raw), mime, tenant_id="store6", session_id=key, kind="upload")
    m.APP.runtime.store.set("ui_uploads", key, {"ref": ref, "tenant_id": "store6"})
    return {"asset": key, "hash": hashlib.sha256(raw).hexdigest()}


@router.get("/api/assets")
def asset(key: str):
    m = backend()
    item = m.APP.runtime.store.get("ui_uploads", key)
    if not item:
        raise HTTPException(404, "Artwork not found")
    raw, mime = m.APP.runtime.assets.get(item["ref"])
    return Response(raw, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})
