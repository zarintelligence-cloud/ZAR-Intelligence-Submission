"""Store 6 connector - order_handoff. Sends the confirmed production spec to the tenant's order system.
Sandbox mode (no ZAR_ORDER_WEBHOOK_URL) records the handoff in the 'handoffs' collection, which the
Store 6 admin view in the demo reads. Live mode POSTs JSON signed with HMAC-SHA256."""
from __future__ import annotations
import hashlib, hmac, json, time
from zar_core import config
from zar_core.contracts import CapabilityRequest, CapabilityResult, utcnow
from zar_core.services import services


def signature(body: bytes, secret: str, ts: str) -> str:
    return hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


def run(req: CapabilityRequest) -> CapabilityResult:
    spec = req.payload["production_spec"]
    order_ref = "S6-" + req.context.session_id.upper()
    envelope = {"type": "zar.order.confirmed", "order_ref": order_ref, "tenant_id": req.context.tenant_id,
                "session_id": req.context.session_id, "sent_at": utcnow(), "production_spec": spec}
    body = json.dumps(envelope, sort_keys=True, default=str).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "Idempotency-Key": req.context.session_id,
               "X-ZAR-Timestamp": ts, "X-ZAR-Signature": signature(body, config.ORDER_WEBHOOK_SECRET, ts)}
    if not config.ORDER_WEBHOOK_URL or spec.get("synthetic"):
        services.store.set("handoffs", req.context.session_id, {
            **envelope, "status": "received", "mode": "sandbox",
            "next_actions": ["Create Razorpay payment link", "Raise purchase order with print partner",
                             "Book Delhivery pickup after production"]})
        mode, remote = "sandbox", {"status": 202}
    else:
        import requests
        if not config.ORDER_WEBHOOK_SECRET or config.ORDER_WEBHOOK_SECRET == "dev-secret":
            raise ValueError("Configure a private webhook secret before enabling live handoff")
        r = requests.post(config.ORDER_WEBHOOK_URL, data=body, headers=headers, timeout=15, allow_redirects=False)
        if r.status_code >= 300:
            return CapabilityResult(request_id=req.request_id, capability=req.capability, status="error",
                                    error=f"Store 6 returned HTTP {r.status_code}",
                                    debug={"url": config.ORDER_WEBHOOK_URL, "headers": {k: v for k, v in headers.items() if k != "X-ZAR-Signature"}})
        mode, remote = "webhook", {"status": r.status_code, "body": r.text[:500]}
        services.store.set("handoffs", req.context.session_id, {**envelope, "status": "received", "mode": mode})
    return CapabilityResult(request_id=req.request_id, capability=req.capability, status="ok",
                            data={"delivered": True, "order_ref": order_ref, "mode": mode, "remote": remote},
                            confidence=1.0, model_used="none",
                            debug={"target": config.ORDER_WEBHOOK_URL or "sandbox://handoffs",
                                   "headers": {k: v for k, v in headers.items() if k != "X-ZAR-Signature"},
                                   "signed": True, "body_bytes": len(body)})
