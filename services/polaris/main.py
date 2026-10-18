"""Polaris - public front desk and demo host: sessions, events, proofs, roster, traces, Store 6 demo."""
from __future__ import annotations
import base64, binascii, hmac, json, os, io, re, time, asyncio, warnings, secrets, hashlib
from contextlib import asynccontextmanager, suppress
from PIL import Image, UnidentifiedImageError
from pathlib import Path
from typing import Any, Literal
import yaml
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from zar_core.store import StoreBusy
from zar_core.supabase_store import SupabaseStoreError
from zar_core import config
from zar_core.bootstrap import ROOT, build
from zar_core.contracts import utcnow
from zar_core.workflow import InvalidTransition
from . import demo

APP = build("polaris")
STATIC = Path(__file__).parent / "static"
DEMO_MODE = os.getenv("ZAR_DEMO_MODE", "1") == "1"
ADMIN_TOKEN = os.getenv("ZAR_ADMIN_TOKEN", "")
MAX_LOGO_BYTES = 5 * 1024 * 1024
def process_retries():
    processed = 0
    for job in APP.runtime.store.list("retry_jobs", "status", "pending"):
        if job["due"] <= time.time():
            try:
                APP.runtime.handle_event(job["session_id"], job["event"], {})
                processed += 1
            except (StoreBusy, KeyError, InvalidTransition):
                continue
    return {"processed": processed}


@asynccontextmanager
async def lifespan(app):
    async def worker():
        while True:
            await asyncio.sleep(30)
            try:
                await asyncio.to_thread(process_retries)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry worker failed; pending jobs remain saved")
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


if not DEMO_MODE and not ADMIN_TOKEN:
    raise RuntimeError("Set ZAR_ADMIN_TOKEN when ZAR_DEMO_MODE=0")
app = FastAPI(title="Polaris - ZAR Intelligence front desk", version=APP.releases.items["zar-core"].version,
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.exception_handler(SupabaseStoreError)
async def supabase_unavailable(request: Request, exc: SupabaseStoreError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def tenant_profile(tenant_id: str) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tenant_id):
        raise HTTPException(404, "Unknown tenant")
    path = ROOT / "tenants" / f"{tenant_id}.yaml"
    if not path.exists():
        raise HTTPException(404, "Unknown tenant")
    return yaml.safe_load(path.read_text(encoding="utf-8"))["tenant"]


def admin(token: str | None) -> None:
    """Traces and ops data are internal. Open in demo when ZAR_ADMIN_TOKEN is unset."""
    if ADMIN_TOKEN and not (token and hmac.compare_digest(token, ADMIN_TOKEN)):
        raise HTTPException(401, "Admin token required")


class NewSession(BaseModel):
    tenant_id: str = Field("store6", max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    domain: str = "retail.custom_apparel"
    message: str = Field(..., min_length=3, max_length=4000)
    logo_b64: str = Field(..., max_length=7 * 1024 * 1024)
    logo_mime: Literal["image/png", "image/jpeg", "image/webp"] = "image/png"
    today: str | None = None
    scenario: str | None = Field(None, max_length=40)
    title: str | None = Field(None, max_length=80)


class EventIn(BaseModel):
    event: str = Field(..., max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(None, max_length=128)

    @model_validator(mode="after")
    def bounded_data(self):
        if len(json.dumps(self.data)) > 16000:
            raise ValueError("Event data is too large")
        if "text" in self.data and (not isinstance(self.data["text"], str) or not 1 <= len(self.data["text"].strip()) <= 4000):
            raise ValueError("Text must be 1-4000 characters")
        if self.event in {"customer_reply", "change_request"} and not self.data.get("text"):
            raise ValueError("Please provide text")
        return self


class RosterEntry(BaseModel):
    name: str = Field(..., max_length=40)
    size: str = Field(..., max_length=5)
    number: str = Field("", max_length=3)


def public(s: dict[str, Any]) -> dict[str, Any]:
    wf = APP.catalog.get(s["domain"]).workflow
    proofs = [{k: p[k] for k in ("proof_id", "body", "trim", "warnings")} |
              {"url": asset_url(f"/v1/sessions/{s['session_id']}/proofs/{p['proof_id']}", s['rev'])} for p in s.get("proofs", [])]
    keep = ("session_id", "tenant_id", "state", "spec", "assumptions", "questions", "messages", "selected_proof",
            "roster_status", "fulfillment", "quote", "production_spec", "handoff", "decisions", "release", "rev",
            "scenario", "title", "created_at", "updated_at", "dropped_fields", "last_error", "roster_submission", "source_archive", "synthetic")
    out = {k: s.get(k) for k in keep}
    out["proofs"] = proofs
    out["artwork"] = {k: s["artwork"][k] for k in ("print_method", "reasons", "file_problems", "measured",
                                                   "allowed_methods", "rule_id")} if s.get("artwork") else None
    out["roster_path"] = (s.get("roster") or {}).get("roster_path")
    out["allowed_events"] = wf.allowed_events(s["state"])
    if s.get("retry_step"):
        out["allowed_events"] = ["retry", "cancel"]
    if (s.get("handoff") or {}).get("delivered"):
        out["allowed_events"] = [e for e in out["allowed_events"] if e != "handoff_retry"]
    if out.get("production_spec"):
        out["production_spec"] = json.loads(json.dumps(out["production_spec"]))
        out["production_spec"]["artwork"].pop("logo_ref", None)
        out["production_spec"]["proof"].pop("ref", None)
        out["production_spec"]["artwork"]["url"] = asset_url(f"/v1/sessions/{s['session_id']}/logo")
        out["production_spec"]["proof"]["url"] = asset_url(f"/v1/sessions/{s['session_id']}/proofs/{s['selected_proof']}")
    return out

def asset_url(path: str, rev: int = 0) -> str:
    if not ADMIN_TOKEN:
        return f"{path}?rev={rev}"
    expires = int(time.time()) + 3600
    sig = hmac.new(ADMIN_TOKEN.encode(), f"{path}|{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{path}?rev={rev}&expires={expires}&signature={sig}"


def _handle(session_id: str, event: str, data: dict, key: str | None = None) -> dict:
    session = APP.runtime.store.get("sessions", session_id)
    if session and session.get("synthetic") and event in {"confirmed", "handoff_retry"} and config.ORDER_WEBHOOK_URL:
        raise HTTPException(409, "This database demo uses synthetic artwork. Start a real order with the customer's logo before live handoff.")
    try:
        return public(APP.runtime.handle_event(session_id, event, data, key))
    except KeyError:
        raise HTTPException(404, "Session not found")
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    except StoreBusy as exc:
        raise HTTPException(409, str(exc), headers={"Retry-After": "2"})


# ---------------- pages ----------------
@app.get("/")
def console_page():
    return FileResponse(STATIC / "website-v12.html", headers={"Cache-Control": "no-cache"})

@app.get("/legacy-v7")
def legacy_v7():
    return FileResponse(STATIC / "website-v7.html", headers={"Cache-Control": "no-cache"})

@app.get("/classic")
def classic_page():
    return FileResponse(STATIC / "reference.html", headers={"Cache-Control": "no-cache"})


@app.get("/operations")
def operations_page():
    return FileResponse(STATIC / "overview.html", headers={"Cache-Control": "no-cache"})


@app.get("/mission-control")
def mission_control_page():
    return FileResponse(STATIC / "console.html", headers={"Cache-Control": "no-cache"})


@app.get("/deployment-guide")
def deployment_guide():
    return FileResponse(STATIC / "deployment.html")


@app.get("/store6")
def store6_page():
    return FileResponse(STATIC / "store6.html")


@app.get("/embed")
def embed_page():
    return FileResponse(STATIC / "embed.html")


@app.get("/widget.js")
def widget():
    return FileResponse(STATIC / "widget.js", media_type="application/javascript")


@app.get("/roster/{token}")
def roster_page(token: str):
    return FileResponse(STATIC / "roster.html")


# ---------------- platform ----------------
@app.get("/healthz")
def health():
    return {"ok": True, "release": APP.releases.manifest(), "mode": config.LLM_MODE,
            "store": config.STORE_MODE, "assets": config.ASSET_MODE, "demo": DEMO_MODE,
            "cloud_runtime": bool(os.getenv("K_SERVICE"))}


@app.get("/v1/releases/current")
def release():
    return {"manifest": APP.releases.manifest(), "compatibility": APP.releases.compatibility_check(),
            "domains": {d: p.info.model_dump() for d, p in APP.catalog.packs.items()},
            "runtime": {"llm_mode": config.LLM_MODE, "store_mode": config.STORE_MODE, "asset_mode": config.ASSET_MODE,
                        "invoke_mode": config.INVOKE_MODE, "model_reasoning": config.MODEL_REASONING,
                        "model_fast": config.MODEL_FAST, "handoff": config.ORDER_WEBHOOK_URL or "sandbox"}}


@app.get("/v1/capabilities")
def capabilities():
    return [{"domain": d, "capability": c, "provider": prov}
            for d, p in APP.catalog.packs.items() for c, prov in p.info.capabilities.items()]


@app.get("/v1/tenants/{tenant_id}")
def tenant(tenant_id: str):
    return tenant_profile(tenant_id)


# ---------------- sessions ----------------
@app.post("/v1/sessions")
def create_session(body: NewSession):
    tenant = tenant_profile(body.tenant_id)
    if body.domain != tenant["domain_pack"] or body.domain not in APP.catalog.packs:
        raise HTTPException(422, "Domain is not enabled for this tenant")
    if body.today:
        from datetime import date
        try: date.fromisoformat(body.today)
        except ValueError: raise HTTPException(422, "today must be a valid ISO date")
    try:
        raw = base64.b64decode(body.logo_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "logo_b64 is not valid base64")
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(413, "Logo must be 5 MB or smaller")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as logo:
                if logo.format not in {"PNG", "JPEG", "WEBP"} or logo.width * logo.height > 20_000_000:
                    raise ValueError("Unsupported image or too many pixels")
                if Image.MIME[logo.format] != body.logo_mime:
                    raise ValueError("Image type does not match logo_mime")
                logo.verify()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, "Upload a valid PNG, JPEG or WebP logo under 20 megapixels")
    rt = APP.runtime
    fields: dict[str, Any] = {"transcript": [body.message], "logo_mime": body.logo_mime,
                              "messages": [{"role": "customer", "text": body.message, "at": utcnow()}],
                              "scenario": body.scenario, "title": body.title}
    if DEMO_MODE and body.today:
        fields["today_override"] = body.today
    session_id = rt.new_session_id()
    customer_token = secrets.token_urlsafe(32)
    fields["customer_token_hash"] = hashlib.sha256(customer_token.encode()).hexdigest()
    fields["logo_ref"] = rt.assets.put(raw, body.logo_mime, tenant_id=body.tenant_id,
                                       session_id=session_id, kind="logo")
    try:
        result = public(rt.start_session(tenant_id=body.tenant_id, domain=body.domain, fields=fields,
                                       session_id=session_id))
        result["customer_token"] = customer_token
        return result
    except KeyError as exc:
        raise HTTPException(400, str(exc))


@app.get("/v1/sessions")
def list_sessions(tenant_id: str = "store6", x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    return APP.runtime.sessions(tenant_id)


@app.post("/v1/sessions/{session_id}/events")
def post_event(session_id: str, body: EventIn):
    return _handle(session_id, body.event, body.data, body.idempotency_key)


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str):
    s = APP.runtime.store.get("sessions", session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return public(s)


@app.get("/v1/sessions/{session_id}/production-spec")
def download_spec(session_id: str):
    s = APP.runtime.store.get("sessions", session_id)
    if not s or not s.get("production_spec"):
        raise HTTPException(404, "Production specification not ready")
    return Response(json.dumps(public(s)["production_spec"], indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="ZAR-{session_id}.json"'})


@app.get("/v1/sessions/{session_id}/trace")
def get_trace(session_id: str, x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    if not APP.runtime.store.get("sessions", session_id):
        raise HTTPException(404, "Session not found")
    return APP.runtime.trace(session_id)


@app.get("/v1/sessions/{session_id}/proofs/{proof_id}")
def get_proof(session_id: str, proof_id: str):
    s = APP.runtime.store.get("sessions", session_id)
    proof = next((p for p in (s or {}).get("proofs", []) if p["proof_id"] == proof_id), None)
    if not proof:
        raise HTTPException(404, "Proof not found")
    raw, mime = APP.runtime.assets.get(proof["ref"])
    return Response(raw, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/v1/sessions/{session_id}/logo")
def get_logo(session_id: str):
    s = APP.runtime.store.get("sessions", session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    raw, mime = APP.runtime.assets.get(s["logo_ref"])
    return Response(raw, media_type=mime)


# ---------------- roster ----------------
@app.get("/v1/roster/{token}")
def roster_info(token: str):
    doc = APP.runtime.store.get("rosters", token)
    if not doc:
        raise HTTPException(404, "Roster not found")
    return {"expected": doc["expected"], "submitted": len(doc["entries"]), "needs": doc["needs"],
            "closed": doc["closed"], "sizes": ["XS", "S", "M", "L", "XL", "XXL", "3XL"],
            "entries": [{"name": e["name"], "size": e["size"], "number": e.get("number", "")} for e in doc["entries"]]}


def _submit(token: str, entry: dict) -> dict:
    doc = APP.runtime.store.get("rosters", token)
    if not doc:
        raise HTTPException(404, "Roster not found")
    session = APP.runtime.store.get("sessions", doc["session_id"])
    if not session or (session.get("roster") or {}).get("token") != token or session["state"] != "collecting":
        raise HTTPException(409, "This roster is no longer accepting entries")
    s = _handle(doc["session_id"], "roster_entry", {"entry": entry})
    accepted = bool(s.get("roster_submission", {}).get("accepted"))
    message = "Thanks - you're on the roster." if accepted else next(
        (m["text"] for m in reversed(s["messages"]) if m["role"] == "assistant"), "Entry not saved.")
    return {"accepted": accepted, "message": message, "roster_status": s["roster_status"], "state": s["state"]}


@app.post("/v1/roster/{token}/entries")
def roster_submit(token: str, entry: RosterEntry):
    return _submit(token, entry.model_dump())


@app.post("/v1/roster/{token}/entries:batch")
def roster_batch(token: str, entries: list[RosterEntry]):
    if len(entries) > 200:
        raise HTTPException(413, "At most 200 entries per batch")
    results = []
    for e in entries:
        try:
            results.append(_submit(token, e.model_dump()))
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            results.append({"accepted": False, "message": "Roster closed", "state": None})
            break
    return {"results": results, "accepted": sum(r["accepted"] for r in results)}


# ---------------- ops & demo ----------------
@app.post("/v1/ops/retries")
def retry_pending(x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    return process_retries()


@app.get("/v1/ops/metrics")
def ops_metrics(tenant_id: str = "store6", x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    return APP.runtime.metrics(tenant_id)


@app.get("/v1/ops/policies")
def ops_policies(x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    pack = APP.catalog.get("retail.custom_apparel")
    files = {"manifest.yaml": pack.root / "manifest.yaml", "workflow.yaml": pack.root / "workflow.yaml"}
    files |= {p.name: p for p in sorted((pack.root / "policy").glob("*.yaml"))}
    return {name: p.read_text(encoding="utf-8") for name, p in files.items()}


@app.get("/v1/handoffs")
def handoffs(tenant_id: str = "store6", x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    rows = APP.runtime.store.list("handoffs", "tenant_id", tenant_id)
    return sorted(rows, key=lambda r: r["sent_at"], reverse=True)


@app.post("/store6/api/zar-webhook")
async def store6_webhook_receiver(request: Request):
    """Reference receiver (what Store 6's backend implements). Point ZAR_ORDER_WEBHOOK_URL here to test
    the live connector path end to end, including signature verification."""
    from domains.retail.capabilities.handoff import signature
    body = await request.body()
    ts, sig = request.headers.get("X-ZAR-Timestamp", ""), request.headers.get("X-ZAR-Signature", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > 300:
        raise HTTPException(401, "Expired or invalid timestamp")
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(413, "Payload too large")
    if not hmac.compare_digest(signature(body, config.ORDER_WEBHOOK_SECRET, ts), sig):
        raise HTTPException(401, "Bad signature")
    try:
        env = json.loads(body)
        if env.get("type") != "zar.order.confirmed" or not all(isinstance(env.get(k), str) for k in ("session_id", "order_ref", "tenant_id")):
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(422, "Invalid handoff envelope")
    APP.runtime.store.set("handoffs", env["session_id"], {**env, "status": "received", "mode": "webhook",
                                                          "received_at": utcnow()})
    return {"ok": True, "store6_order_id": env["order_ref"]}


@app.get("/v1/demo/scenarios")
def demo_scenarios():
    return {"scenarios": demo.scenarios(), "logos": demo.LOGOS, "names": demo.NAMES, "sizes": demo.SIZES}


@app.get("/v1/demo/logos/{kind}.png")
def demo_logo(kind: str):
    try:
        return Response(demo.logo(kind), media_type="image/png")
    except KeyError:
        raise HTTPException(404, "Unknown demo logo")


@app.post("/v1/demo/reseed")
def demo_reseed(x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    if not (DEMO_MODE and config.STORE_MODE == "memory"):
        raise HTTPException(403, "Reseed is only available in demo mode with the memory store")
    APP.runtime.store.db.clear()
    return {"seeded": demo.seed(APP.runtime)}


if DEMO_MODE and config.STORE_MODE == "memory" and os.getenv("ZAR_DEMO_SEED", "1") == "1":
    demo.seed(APP.runtime)


# Website database snapshots stay separate from current workflow sessions.
from zar_core.database_kit import import_kit, summarize, replay_brief
KIT = ROOT / "fixtures" / "database-kit" / "zar_demo.sqlite"


@app.post("/v1/database/import-kit")
def database_import(x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    if not KIT.exists():
        raise HTTPException(404, "Historical database fixture is excluded from this public source release")
    return import_kit(APP.runtime.store, KIT)


@app.get("/v1/database/sessions")
def database_sessions(x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    return [summarize(r) for r in APP.runtime.store.list("imported_sessions", "tenant_id", "store6")]


@app.get("/v1/database/sessions/{archive_id}")
def database_session(archive_id: str, x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    row = APP.runtime.store.get("imported_sessions", archive_id)
    if not row or row["tenant_id"] != "store6":
        raise HTTPException(404, "Imported session not found")
    return row


class ReplayKit(BaseModel):
    request_id: str = Field(..., min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    prepare_quote: bool = False


@app.post("/v1/database/sessions/{archive_id}/replay")
def database_replay(archive_id: str, body: ReplayKit, x_zar_admin: str | None = Header(None)):
    row = database_session(archive_id, x_zar_admin)
    rt = APP.runtime
    key = "kit-replay-" + body.request_id
    with rt.store.lock(key):
        link = rt.store.get("kit_replays", body.request_id)
        if link and (link["archive_id"] != archive_id or link["prepare_quote"] != body.prepare_quote):
            raise HTTPException(409, "Use a new request ID for a different replay")
        if link:
            s = rt.store.get("sessions", link["session_id"])
        else:
            sid = rt.new_session_id()
            d = row["data"]
            brief = replay_brief(row)
            kind = "strikers" if (d.get("artwork") or {}).get("gradient") else "titans"
            ref = rt.assets.put(demo.logo(kind), "image/png", tenant_id="store6", session_id=sid, kind="logo")
            # Save the identity before work, allowing a retried request to find its session.
            rt.store.set("kit_replays", body.request_id, {"archive_id": archive_id, "session_id": sid,
                         "prepare_quote": body.prepare_quote})
            s = rt.start_session(tenant_id="store6", domain="retail.custom_apparel", session_id=sid,
                fields={"transcript": [brief], "logo_ref": ref, "logo_mime": "image/png",
                        "messages": [{"role": "customer", "text": brief, "at": utcnow()}],
                        "title": "Database demo: " + d.get("spec", {}).get("garment", "order"),
                        "scenario": "database-kit", "source_archive": archive_id, "synthetic": True})
        if not s:
            raise HTTPException(409, "Replay creation was interrupted. Start again with a new request ID.")
        if s.get("retry_step"):
            s = rt.handle_event(s["session_id"], "retry", {})
        if body.prepare_quote and s["state"] == "awaiting_approval":
            index = row["data"].get("proof") or 0
            proof = s["proofs"][min(int(index), len(s["proofs"]) - 1)]["proof_id"]
            s = rt.handle_event(s["session_id"], "proof_chosen", {"proof_id": proof}, "kit-proof")
        if body.prepare_quote and s["state"] == "collecting":
            for i, entry in enumerate(row["data"].get("roster", [])):
                if s["state"] != "collecting":
                    break
                # Source names like "Sample Player 01" exceed current print policy.
                # Preserve the source; give the fresh synthetic roster short labels.
                validated = RosterEntry(**{**entry, "name": f"Player {i+1:02d}"}).model_dump()
                s = rt.handle_event(s["session_id"], "roster_entry", {"entry": validated}, f"kit-player-{i}")
        # Intentionally never replay the old customer's confirmation.
        return {"session": public(s), "notice": "New synthetic demo, generated artwork, short Player labels and deadline shifted relative to today. Review the new quote before confirming."}


@app.get("/v1/ops/overview")
def overview(x_zar_admin: str | None = Header(None)):
    admin(x_zar_admin)
    sessions = APP.runtime.store.list("sessions", "tenant_id", "store6")
    archive = APP.runtime.store.list("imported_sessions", "tenant_id", "store6")
    attention = []
    for s in sessions:
        if s["state"] == "cancelled" or (s["state"] == "confirmed" and (s.get("handoff") or {}).get("delivered")):
            continue
        reasons = []
        q, spec = (s.get("quote") or {}).get("quote") or {}, s.get("spec") or {}
        budget = spec.get("budget_inr") or spec.get("budget")
        if budget and q.get("total", 0) > budget:
            reasons.append("Over budget")
        if (s.get("fulfillment") or {}).get("p_on_time", 1) < .8:
            reasons.append("Delivery risk")
        if s.get("retry_step"):
            reasons.append("Agent needs retry")
        if s["state"] == "confirmed" and not (s.get("handoff") or {}).get("delivered"):
            reasons.append("Store handoff pending")
        if s["state"] == "asking":
            reasons.append("Brief incomplete")
        if reasons:
            attention.append({"session_id": s["session_id"], "title": s.get("title") or s["session_id"],
                              "state": s["state"], "reasons": reasons})
    return {"counts": {"current": len(sessions), "confirmed": sum(s["state"] == "confirmed" for s in sessions),
                       "archive": len(archive), "attention": len(attention)},
            "attention": attention, "scope": "Up to 500 stored records per collection; confirmed includes sandbox demos.",
            "connections": {"reasoning": config.LLM_MODE, "database": config.STORE_MODE,
                            "artwork": config.ASSET_MODE, "workshop": config.INVOKE_MODE,
                            "store6": "webhook" if config.ORDER_WEBHOOK_URL else "sandbox"},
            "release": APP.releases.manifest(), "cloud_runtime": bool(os.getenv("K_SERVICE")),
            "retry_jobs": [{k: j.get(k) for k in ("session_id", "status", "attempts", "due")}
                           for j in APP.runtime.store.list("retry_jobs") if j.get("status") != "complete"]}


from .reference_ui import router as reference_router
app.include_router(reference_router)

from .identity import router as identity_router
from .merchant import router as merchant_router
app.include_router(identity_router)
app.include_router(merchant_router)
from .knowledge import router as knowledge_router
app.include_router(knowledge_router)
from .version10 import router as version10_router
app.include_router(version10_router)

from .merchant import Enquiry, save_enquiry
from .identity import writes
from fastapi import Depends

@app.post("/api/enquiries", dependencies=[Depends(writes)], status_code=201)
def website_enquiry(body: Enquiry):
    return save_enquiry(body)

@app.get("/signin")
@app.get("/pricing")
@app.get("/contact")
@app.get("/privacy")
@app.get("/experience")
@app.get("/try")
@app.get("/terms")
@app.get("/architecture")
@app.get("/hq")
@app.get("/hq/{page:path}")
@app.get("/products/{pack}")
@app.get("/app/{page:path}")
def website_route():
    return console_page()

@app.middleware("http")
async def protect_order_access(request: Request, call_next):
    """Staff roles cannot be bypassed via the customer-facing order API."""
    from .identity import viewer, require_role, scoped, writes
    from fastapi.responses import JSONResponse
    match = re.match(r"^/v1/sessions/([a-f0-9]{32})(?:/|$)", request.url.path)
    try:
        if match:
            s = await asyncio.to_thread(APP.runtime.store.get, "sessions", match[1])
            if s and (not DEMO_MODE or s.get("console_managed")):
                provided = request.headers.get("X-ZAR-Customer") or request.query_params.get("access", "")
                valid_customer = provided and s.get("customer_token_hash") and hmac.compare_digest(hashlib.sha256(provided.encode()).hexdigest(), s["customer_token_hash"])
                admin_key = request.headers.get("X-ZAR-Admin", "")
                valid_admin = ADMIN_TOKEN and admin_key and hmac.compare_digest(admin_key, ADMIN_TOKEN)
                valid_asset = False
                expires = request.query_params.get("expires", "")
                signature = request.query_params.get("signature", "")
                if ADMIN_TOKEN and request.method == "GET" and re.fullmatch(r"/v1/sessions/[a-f0-9]{32}/(?:logo|proofs/P[123])", request.url.path) and expires.isdigit() and time.time() < int(expires) <= time.time() + 3600:
                    expected = hmac.new(ADMIN_TOKEN.encode(), f"{request.url.path}|{expires}".encode(), hashlib.sha256).hexdigest()
                    valid_asset = hmac.compare_digest(signature, expected)
                if not valid_customer and not valid_admin and not valid_asset:
                    user = await asyncio.to_thread(viewer, request)
                    scoped(user, s["tenant_id"])
                    if request.method not in {"GET", "HEAD"}:
                        writes(request)
                        require_role(user, "administrator", "operations")
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith(("/api/console", "/v1/sessions", "/v1/roster")):
        response.headers["Cache-Control"] = "no-store"
    return response

from .storefront_connections import router as storefront_connections_router
app.include_router(storefront_connections_router)
