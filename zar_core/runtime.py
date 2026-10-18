"""ZAR Core runtime: sessions, capability calls, decisions, step traces, audit and release metadata."""
from __future__ import annotations
import base64, time, uuid, hashlib, json
from contextvars import ContextVar
from typing import Any
from . import config
from .contracts import CapabilityRequest, CapabilityResult, Context, Decision, Evidence, utcnow
from .domain_loader import DomainCatalog
from .release_registry import ReleaseRegistry
from .workflow import InvalidTransition
from .services import services

# The workflow step currently executing in this request (threads/async tasks each get their own copy).
_STEP: ContextVar[dict[str, Any] | None] = ContextVar("zar_step", default=None)


class CapabilityError(RuntimeError):
    def __init__(self, capability: str, error: str | None) -> None:
        super().__init__(f"{capability}: {error}")
        self.capability, self.error = capability, error


def redact(value: Any, limit: int = 4000) -> Any:
    """Audit/trace copies never hold binaries; long text is truncated."""
    if isinstance(value, dict):
        return {k: (f"<{len(v)} base64 chars>" if k.endswith("_b64") and isinstance(v, str) else redact(v, limit))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, limit) for v in value]
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    return value


class ZARRuntime:
    def __init__(self, *, invoker, store, assets, releases: ReleaseRegistry, catalog: DomainCatalog):
        self.invoker, self.store, self.assets = invoker, store, assets
        self.releases, self.catalog = releases, catalog

    # ---- context & capabilities ---------------------------------------------------------
    @staticmethod
    def context_for(session: dict[str, Any]) -> Context:
        return Context(tenant_id=session["tenant_id"], domain=session["domain"],
                       session_id=session["session_id"], locale=session.get("locale", "en"))

    def act(self, context: Context, capability: str, payload: dict[str, Any]) -> CapabilityResult:
        req = CapabilityRequest(request_id=uuid.uuid4().hex, context=context,
                                capability=capability, payload=payload)
        started = time.perf_counter()
        with services.use(self.store, self.assets):
            result = self.invoker.invoke(req)
        step = _STEP.get()
        pack = self.catalog.packs.get(context.domain)
        response = result.model_dump(exclude={"request_id"})
        if not config.TRACE_DEBUG:
            response.pop("debug", None)
        self.store.set("agent_runs", req.request_id, {
            "request_id": req.request_id, "tenant_id": context.tenant_id, "domain": context.domain,
            "session_id": context.session_id, "capability": capability,
            "provider": pack.info.capabilities.get(capability, "") if pack else "",
            "status": result.status, "error": result.error, "model_used": result.model_used,
            "confidence": result.confidence, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "step_id": step["step_id"] if step else None,
            "request": redact(req.model_dump(exclude={"request_id"})), "response": redact(response),
            "release": self.releases.manifest(), "at": utcnow()})
        if step is not None:
            step["run_ids"].append(req.request_id)
        return result

    def require(self, context: Context, capability: str, payload: dict[str, Any]) -> CapabilityResult:
        result = self.act(context, capability, payload)
        if result.status == "error":
            raise CapabilityError(capability, result.error)
        return result

    def binary_payload(self, prefix: str, ref: str) -> dict[str, str]:
        """Pass a reference when the capability host can read the asset store, else inline base64."""
        if self.assets.shared or not getattr(self.invoker, "remote", False):
            return {f"{prefix}_ref": ref}
        raw, mime = self.assets.get(ref)
        return {f"{prefix}_b64": base64.b64encode(raw).decode(), f"{prefix}_mime": mime}

    # ---- decisions ----------------------------------------------------------------------
    def decide(self, context: Context, *, goal: str, action: str, engine: str,
               reasons: list[str], confidence: float | None = None,
               evidence: list[Evidence] | None = None, options: list[dict] | None = None,
               risks: list[str] | None = None, missing: list[str] | None = None) -> Decision:
        d = Decision(decision_id=uuid.uuid4().hex, session_id=context.session_id, goal=goal,
                     action=action, engine=engine, confidence=confidence, reasons=reasons,
                     risks=risks or [], missing_evidence=missing or [], evidence=evidence or [],
                     options=options or [], release_manifest=self.releases.manifest())
        from .audit import sealed
        self.store.set("decisions", d.decision_id, sealed({**d.model_dump(), "tenant_id": context.tenant_id}))
        step = _STEP.get()
        if step is not None:
            step["decision_ids"].append(d.decision_id)
        return d

    # ---- sessions & workflow ------------------------------------------------------------
    @staticmethod
    def new_session_id() -> str:
        return uuid.uuid4().hex

    def start_session(self, *, tenant_id: str, domain: str, fields: dict[str, Any],
                      first_event: str = "created", session_id: str | None = None) -> dict[str, Any]:
        pack = self.catalog.get(domain)
        if pack.workflow is None:
            raise ValueError(f"Domain {domain} has no workflow")
        session_id = session_id or self.new_session_id()
        session = {"session_id": session_id, "tenant_id": tenant_id, "domain": domain,
                   "state": pack.workflow.spec.initial, "rev": 0, "step_seq": 0, "locale": "en",
                   "created_at": utcnow(), "updated_at": utcnow(), "history": [], "messages": [],
                   "decisions": [], "processed_events": [], "release": self.releases.manifest(), **fields}
        self.store.set("sessions", session_id, session)
        return self.handle_event(session_id, first_event, {})

    def handle_event(self, session_id: str, event: str, data: dict[str, Any] | None = None,
                     idempotency_key: str | None = None) -> dict[str, Any]:
        with self.store.lock(session_id):
            return self.execute_event_unlocked(session_id, event, data, idempotency_key)

    def execute_event_unlocked(self, session_id, event, data=None, idempotency_key=None):
        from .adk_agents import run_polaris
        return run_polaris(self, session_id, event, data, idempotency_key)

    def _handle_event(self, session_id, event, data=None, idempotency_key=None):
        session = self.store.get("sessions", session_id)
        if not session:
            raise KeyError("Session not found")
        if session.get("manual_hold") and event != "cancel":
            raise InvalidTransition("An operator has paused this order. Resume it in the merchant console first")
        if idempotency_key and idempotency_key in session["processed_events"]:
            return session                                   # replayed delivery: no-op
        workflow = self.catalog.get(session["domain"]).workflow
        data = data or {}
        fingerprint = hashlib.sha256(json.dumps([event, data], sort_keys=True).encode()).hexdigest()
        retry = session.get("retry_step")
        if event == "retry" and not retry:
            raise InvalidTransition("There is no failed step to retry")
        if retry and (event == "retry" or (idempotency_key and idempotency_key == retry.get("key"))):
            if event != "retry" and fingerprint != retry.get("fingerprint"):
                raise InvalidTransition("Retry key belongs to a different event")
            event, data = retry["event"], retry["data"]
            idempotency_key = retry.get("key") or idempotency_key
            fingerprint = retry["fingerprint"]
        elif retry and event != "cancel":
            raise InvalidTransition("Retry the failed step before submitting another event")
        if data.get("text"):
            session["messages"].append({"role": "customer", "text": data["text"], "at": utcnow()})
        pending, steps = [(event, data, "external")], 0
        while pending:
            ev, ev_data, origin = pending.pop(0)
            steps += 1
            if steps > config.MAX_AUTO_STEPS:
                raise RuntimeError("Workflow exceeded maximum automatic steps")
            transition = workflow.find(session["state"], ev)
            session["step_seq"] = session.get("step_seq", 0) + 1
            step = {"step_id": uuid.uuid4().hex, "session_id": session_id, "tenant_id": session["tenant_id"],
                    "seq": session["step_seq"], "event": ev, "origin": origin, "data": redact(ev_data),
                    "from": session["state"], "to": session["state"],
                    "handler": transition.handler if transition else None,
                    "run_ids": [], "decision_ids": [], "message": None, "emitted": None,
                    "error": None, "at": utcnow()}
            started, token = time.perf_counter(), _STEP.set(step)
            try:
                res = workflow.step(session, ev, ev_data, self)
            except CapabilityError as exc:
                session["failure_count"] = session.get("failure_count", 0) + 1
                if session["failure_count"] >= 2:
                    session["manual_hold"] = True
                step["error"] = f"{exc.capability}: {exc.error}"
                session["last_error"] = {"capability": exc.capability, "at": utcnow()}
                session["retry_step"] = {"event": ev, "data": ev_data, "key": idempotency_key,
                                         "fingerprint": fingerprint}
                msg = "A specialist could not finish this step after a retry. A person needs to review the order." if session.get("manual_hold") else "Sorry - one of our specialists could not finish that step. Please try again."
                session["messages"].append({"role": "assistant", "text": msg, "at": utcnow()})
                step["message"] = msg
                self._save_step(step, started)
                break
            except InvalidTransition:
                session["step_seq"] -= 1
                raise
            finally:
                _STEP.reset(token)
            session.update(res.patch)
            session["failure_count"] = 0
            session.pop("retry_step", None)
            session.pop("last_error", None)
            if res.state:
                session["state"] = res.state
            step["to"] = session["state"]
            session["history"] = (session["history"] + [{"event": ev, "from": step["from"],
                                                         "to": session["state"], "at": utcnow()}])[-100:]
            if res.message:
                text = self._voice(session, res.message)
                session["messages"].append({"role": "assistant", "state": session["state"], "text": text,
                                            "at": utcnow()})
                step["message"] = text
            if res.emit:
                step["emitted"] = res.emit
                pending.append((res.emit, res.emit_data, "auto"))
            self._save_step(step, started)
        if idempotency_key and not session.get("retry_step"):
            session["processed_events"] = (session["processed_events"] + [idempotency_key])[-200:]
        session["rev"] += 1
        session["updated_at"] = utcnow()
        self.store.set("sessions", session_id, session)
        return session

    def _save_step(self, step: dict[str, Any], started: float) -> None:
        step["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        self.store.set("steps", step["step_id"], step)

    def _voice(self, session: dict[str, Any], text: str) -> str:
        """Optional domain capability 'compose_reply' rewrites templated text in the brand
        voice/locale. The capability itself guarantees numbers are preserved."""
        pack = self.catalog.get(session["domain"])
        if "compose_reply" not in pack.info.capabilities or session.get("locale", "en") == "en":
            return text
        result = self.act(self.context_for(session), "compose_reply",
                          {"text": text, "locale": session.get("locale", "en")})
        return result.data.get("text", text) if result.status == "ok" else text

    # ---- read models for the console ----------------------------------------------------
    def trace(self, session_id: str) -> dict[str, Any]:
        steps = sorted(self.store.list("steps", "session_id", session_id), key=lambda s: s["seq"])
        runs = {r["request_id"]: r for r in self.store.list("agent_runs", "session_id", session_id)}
        decisions = {d["decision_id"]: d for d in self.store.list("decisions", "session_id", session_id)}
        for s in steps:
            s["runs"] = sorted((runs[r] for r in s["run_ids"] if r in runs), key=lambda r: r["at"])
            s["decisions"] = [decisions[d] for d in s["decision_ids"] if d in decisions]
        return {"session_id": session_id, "steps": steps}

    def sessions(self, tenant_id: str) -> list[dict[str, Any]]:
        out = []
        for s in self.store.list("sessions", "tenant_id", tenant_id):
            spec, quote = s.get("spec") or {}, ((s.get("quote") or {}).get("quote") or {})
            out.append({"session_id": s["session_id"], "state": s["state"], "created_at": s["created_at"],
                        "updated_at": s.get("updated_at", s["created_at"]), "scenario": s.get("scenario"),
                        "title": s.get("title") or (s.get("transcript") or [""])[0][:80],
                        "garment": spec.get("garment_type"), "quantity": spec.get("quantity"),
                        "total": quote.get("total"), "currency": quote.get("currency")})
        return sorted(out, key=lambda x: x["updated_at"], reverse=True)

    def metrics(self, tenant_id: str) -> dict[str, Any]:
        runs = self.store.list("agent_runs", "tenant_id", tenant_id, limit=5000)
        by: dict[str, dict[str, Any]] = {}
        for r in runs:
            m = by.setdefault(r["capability"], {"capability": r["capability"], "provider": r.get("provider", ""),
                                                "calls": 0, "errors": 0, "needs_input": 0, "latency": [], "models": {}})
            m["calls"] += 1
            m["errors"] += r["status"] == "error"
            m["needs_input"] += r["status"] == "needs_input"
            m["latency"].append(r["latency_ms"])
            m["models"][r.get("model_used") or "none"] = m["models"].get(r.get("model_used") or "none", 0) + 1
        for m in by.values():
            lat = sorted(m.pop("latency"))
            m["avg_ms"] = round(sum(lat) / len(lat), 1) if lat else 0
            m["p95_ms"] = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0
        sessions = self.store.list("sessions", "tenant_id", tenant_id)
        states: dict[str, int] = {}
        for s in sessions:
            states[s["state"]] = states.get(s["state"], 0) + 1
        return {"capabilities": sorted(by.values(), key=lambda m: m["capability"]), "runs": len(runs),
                "sessions": len(sessions), "states": states,
                "decisions": len(self.store.list("decisions", "tenant_id", tenant_id, limit=5000))}


__all__ = ["ZARRuntime", "CapabilityError", "InvalidTransition", "redact"]
