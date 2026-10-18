"""Retail workflow steps. Orchestration only: every business answer comes from a capability."""
from __future__ import annotations
import base64, time
from datetime import date
from zar_core.workflow import StepContext, StepResult

DOMAIN_GOALS = {"print_method": "select_print_method", "quote": "select_quote_option"}


def _today(s: dict) -> str:
    return s.get("today_override") or date.today().isoformat()


def _money(n: int, cur: str) -> str:
    return f"{n:,} {cur}"


def _pct(p: float) -> str:
    return "more than 99%" if p >= 0.995 else "less than 1%" if p < 0.005 else f"{round(p * 100)}%"


def analyse_requirements(c: StepContext) -> StepResult:
    s, rt = c.session, c.runtime
    transcript = s["transcript"] + ([c.data["text"]] if c.data.get("text") else [])
    res = rt.require(rt.context_for(s), "requirements_analysis",
                     {"transcript": transcript, "today": _today(s), "already_asked": s.get("asked_fields", [])})
    d = res.data
    patch = {"transcript": transcript, "spec": d["spec"], "assumptions": d["assumptions"],
             "dropped_fields": d["dropped"], "locale": d["spec"].get("language") or "en",
             "asked_fields": sorted(set(s.get("asked_fields", [])) | {q["field"] for q in d["question_plan"]})}
    if res.status == "needs_input":
        qs = "\n".join(f"{i}. {q}" for i, q in enumerate(res.questions, 1))
        lead = "one quick question" if len(res.questions) == 1 else f"{len(res.questions)} quick questions"
        return StepResult("asking", {**patch, "questions": res.questions},
                          f"Thanks! Just {lead} before I prepare proofs:\n{qs}")
    spec = d["spec"]
    summary = f"{spec['quantity']} x {spec['garment_type']}, needed by {spec['deadline']}"
    return StepResult("artwork", {**patch, "questions": []},
                      f"Got it - {summary}. Checking your artwork and preparing proofs now.", emit="continue")


def analyse_artwork_and_proof(c: StepContext) -> StepResult:
    s, rt = c.session, c.runtime
    ctx, spec = rt.context_for(s), s["spec"]
    logo = rt.binary_payload("logo", s["logo_ref"])
    art_result = rt.require(ctx, "artwork_analysis", {**logo, "garment_type": spec["garment_type"],
                                               "quantity": spec["quantity"]})
    art = art_result.data
    d1 = rt.decide(ctx, goal="select_print_method", action=art["print_method"], engine="policy",
                   reasons=art["reasons"] + [f"Rule {art['rule_id']}"],
                   options=[{"method": m} for m in art["allowed_methods"]],
                   evidence=art_result.evidence, confidence=0.9)
    fulfil = rt.require(ctx, "fulfillment_confidence", {
        "deadline": spec["deadline"], "quantity": spec["quantity"], "print_method": art["print_method"],
        "roster_open": True, "proof_approved": False, "today": _today(s)}).data
    sleeve = spec.get("sleeve") or s.get("assumptions", {}).get("sleeve", "half")
    proofs_res = rt.require(ctx, "proof_generation", {
        **logo, "colours": spec.get("colours", []), "sleeve": sleeve,
        "personalisation": spec.get("personalisation", []), "print_method": art["print_method"], "count": 3}).data
    proofs = []
    for p in proofs_res["proofs"]:
        ref = rt.assets.put(base64.b64decode(p.pop("image_b64")), p["mime_type"], tenant_id=s["tenant_id"],
                            session_id=s["session_id"], kind="proof")
        proofs.append({**p, "ref": ref})
    lines = [f"Recommended print: {art['print_method']} - {art['reasons'][0]}"]
    lines += [f"Note: {x}" for x in art["file_problems"]]
    lines.append(f"Delivery outlook: {_pct(fulfil['p_on_time'])} chance of meeting "
                 f"{spec['deadline']} ({fulfil['status'].replace('_', ' ')}).")
    lines.append("Here are 3 proofs with your original logo. Which one should we go with?")
    return StepResult("awaiting_approval", {"artwork": art, "fulfillment": fulfil, "proofs": proofs,
                                            "logo_sha256": proofs_res["logo_sha256"],
                                            "decisions": s["decisions"] + [d1.decision_id]}, "\n".join(lines))


def open_roster(c: StepContext) -> StepResult:
    s, rt = c.session, c.runtime
    chosen = c.data.get("proof_id")
    if chosen not in {p["proof_id"] for p in s["proofs"]}:
        return StepResult(None, {}, "Please pick one of the proofs shown (P1, P2 or P3).")
    needs = s["spec"].get("personalisation", [])
    r = rt.require(rt.context_for(s), "roster_management",
                   {"action": "create", "expected": s["spec"]["quantity"], "needs": needs}).data
    return StepResult("collecting", {"selected_proof": chosen, "roster": r},
                      f"Great choice - {chosen} it is. Share this link with your team so each person can add "
                      f"their name{', number' if 'numbers' in needs else ''} and size: {r['roster_path']}")


def add_roster_entry(c: StepContext) -> StepResult:
    s, rt = c.session, c.runtime
    r = rt.require(rt.context_for(s), "roster_management",
                   {"action": "submit", "token": s["roster"]["token"], "entry": c.data.get("entry", {})})
    status = {k: r.data[k] for k in ("submitted", "expected", "missing", "ready", "problems", "size_breakdown")
              if k in r.data}
    submission = {"accepted": r.data.get("accepted", False), "updated": r.data.get("updated", False)}
    if r.status == "needs_input":
        return StepResult(None, {"roster_status": status or s.get("roster_status"), "roster_submission": submission},
                          "Entry not saved: " + " ".join(r.questions))
    if status["ready"]:
        return StepResult(None, {"roster_status": status, "roster_submission": submission}, "Roster complete - preparing your quote.",
                          emit="roster_complete")
    note = f" Issues: {'; '.join(status['problems'])}" if status["problems"] else ""
    return StepResult(None, {"roster_status": status, "roster_submission": submission}, None if not note else
                      f"{status['submitted']}/{status['expected']} entries received.{note}")


def quote(c: StepContext) -> StepResult:
    s, rt = c.session, c.runtime
    ctx = rt.context_for(s)
    chk = rt.require(ctx, "roster_management", {"action": "check", "token": s["roster"]["token"]}).data
    if not chk["ready"]:
        gaps = chk["problems"] + ([f"{chk['missing']} entries still missing"] if chk["missing"] else [])
        return StepResult(None, {"roster_status": chk}, "The roster isn't ready yet: " + "; ".join(gaps) + ".")
    art, spec = s["artwork"], s["spec"]
    k = rt.require(ctx, "pricing", {"spec": spec, "print_method": art["print_method"],
                                    "allowed_methods": art["allowed_methods"]}).data
    q = k["quote"]
    d2 = rt.decide(ctx, goal="select_quote_option", action=q["method"], engine="constraint_optimizer",
                   reasons=[k["reason"]], options=[{"method": o["method"], "total": o["total"]} for o in k["options"]],
                   risks=[] if k["within_budget"] else ["Quote exceeds stated budget"], confidence=1.0)
    fulfil = rt.require(ctx, "fulfillment_confidence", {
        "deadline": spec["deadline"], "quantity": spec["quantity"], "print_method": q["method"],
        "roster_open": False, "proof_approved": True, "today": _today(s)}).data
    proof_patch = {}
    if q["method"] != art["print_method"]:
        # The final specification must reference a proof labelled with the chosen
        # production method, preserving the customer's selected colours and logo.
        new_proofs = rt.require(ctx, "proof_generation", {
            **rt.binary_payload("logo", s["logo_ref"]), "colours": spec.get("colours", []),
            "sleeve": spec.get("sleeve") or s.get("assumptions", {}).get("sleeve", "half"),
            "personalisation": spec.get("personalisation", []), "print_method": q["method"], "count": 3}).data
        proofs = []
        for p in new_proofs["proofs"]:
            ref = rt.assets.put(base64.b64decode(p.pop("image_b64")), p["mime_type"],
                                tenant_id=s["tenant_id"], session_id=s["session_id"], kind="proof")
            proofs.append({**p, "ref": ref})
        proof_patch["proofs"] = proofs
    closed = rt.require(ctx, "roster_management", {"action": "close", "token": s["roster"]["token"]}).data
    msg = [f"Your quote: {_money(q['total'], q['currency'])} for {q['quantity']} pieces "
           f"({_money(q['unit_price'], q['currency'])} each"
           + (f", setup {_money(q['setup_fee'], q['currency'])}" if q["setup_fee"] else "") + f", {q['method']}).",
           k["reason"],
           *(["Your proof has been updated to the quoted print method. Confirming approves this method change."] if proof_patch else []),
           f"Delivery outlook for {spec['deadline']}: {_pct(fulfil['p_on_time'])} on-time probability.",
           "Confirm when you're ready and I'll place the order."]
    return StepResult("awaiting_confirmation", {**proof_patch, "quote": k, "fulfillment": fulfil, "roster_final": closed,
                                                "roster_status": chk, "decisions": s["decisions"] + [d2.decision_id]},
                      "\n".join(msg))


def request_changes(c: StepContext) -> StepResult:
    text = c.data.get("text", "")
    if not text.strip():
        return StepResult(message="Please describe what you want to change.")
    old = c.session.get("roster")
    if old:
        c.runtime.require(c.runtime.context_for(c.session), "roster_management", {"action": "archive", "token": old["token"]})
    return StepResult("asking", {"proofs": [], "quote": None, "selected_proof": None, "roster": None,
                                "roster_status": None, "roster_final": None, "artwork": None, "fulfillment": None}, "No problem - updating your requirements.",
                      emit="customer_reply", emit_data={"text": text} if text else {})


def finalise(c: StepContext) -> StepResult:
    s, rt = c.session, c.runtime
    proof = next(p for p in s["proofs"] if p["proof_id"] == s["selected_proof"])
    production_spec = {
        "session_id": s["session_id"], "tenant_id": s["tenant_id"], "spec": s["spec"],
        "assumptions": s.get("assumptions", {}), "print_method": s["quote"]["quote"]["method"],
        "artwork": {"logo_ref": s["logo_ref"], "measured": s["artwork"]["measured"],
                    "logo_sha256": s.get("logo_sha256"),
                    "file_problems": s["artwork"]["file_problems"]},
        "proof": {k: proof[k] for k in ("proof_id", "body", "trim", "ref")},
        "roster": s["roster_final"]["entries"], "size_breakdown": s["roster_final"]["size_breakdown"],
        "quote": s["quote"]["quote"], "decision_ids": s["decisions"], "release": rt.releases.manifest(),
        "synthetic": bool(s.get("synthetic")), "source_archive": s.get("source_archive")}
    rt.store.set("production_specs", s["session_id"], production_spec)
    h, handoff = _handoff(rt, s, production_spec)
    if h.status == "ok":
        msg = (f"Order confirmed! Reference {h.data['order_ref']}. " +
               ("The production specification is saved locally in the Store 6 sandbox. No live store order was sent."
                if h.data["mode"] == "sandbox" else "Your production specification has been sent to the store team."))
    else:
        msg = "Order confirmed! Your production specification is ready; the store handoff will be retried."
    return StepResult("confirmed", {"production_spec": production_spec, "handoff": handoff}, msg)


def _handoff(rt, s, spec):
    h = rt.act(rt.context_for(s), "order_handoff", {"production_spec": spec})
    attempt = (s.get("handoff") or {}).get("attempts", 0) + 1
    state = {**h.data, "attempts": attempt} if h.status == "ok" else {
        "delivered": False, "error": h.error, "attempts": attempt,
        "order_ref": "S6-" + s["session_id"].upper(), "mode": "webhook"}
    rt.store.set("retry_jobs", s["session_id"], {"session_id": s["session_id"], "event": "handoff_retry",
        "status": "complete" if h.status == "ok" else "pending" if attempt < 8 else "attention",
        "due": time.time() + min(3600, 30 * 2 ** (attempt - 1)), "attempts": attempt})
    return h, state


def retry_handoff(c: StepContext) -> StepResult:
    if (c.session.get("handoff") or {}).get("delivered"):
        return StepResult()
    h, state = _handoff(c.runtime, c.session, c.session["production_spec"])
    return StepResult(patch={"handoff": state}, message="Store handoff delivered." if h.status == "ok"
                      else "Store handoff is still pending. Your confirmed specification is saved.")


def cancel(c: StepContext) -> StepResult:
    if c.session.get("roster"):
        c.runtime.require(c.runtime.context_for(c.session), "roster_management",
                          {"action": "archive", "token": c.session["roster"]["token"]})
    return StepResult("cancelled", {}, "Your request has been cancelled.")


HANDLERS = {f.__name__: f for f in (analyse_requirements, analyse_artwork_and_proof, open_roster,
                                    add_roster_entry, quote, request_changes, finalise, retry_handoff, cancel)}
