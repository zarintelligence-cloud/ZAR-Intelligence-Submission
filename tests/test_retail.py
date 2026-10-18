import base64
import pytest
from datetime import date
from zar_core.contracts import CapabilityRequest, Context
from zar_core.llm import LLMClient, set_llm
from zar_core.services import services
from domains.retail.capabilities import hubble, horizon, kepler, pleiades, rosetta, spectra, voice
from domains.retail.pricing import calculate
from domains.retail.text_utils import numbers_in, parse_date
from tests.helpers import BRIEF, TODAY, flat_logo, gradient_logo

D = "retail.custom_apparel"


def req(cap, payload, session="s1"):
    return CapabilityRequest(request_id="r", capability=cap, payload=payload,
                             context=Context(tenant_id="t", domain=D, session_id=session))


# ---------- Kepler -------------------------------------------------------------------------
def test_price_golden_and_deterministic():
    spec = {"quantity": 40, "garment_type": "cricket jersey", "personalisation": ["names"]}
    a, b = calculate(spec, "sublimation"), calculate(spec, "sublimation")
    assert a == b and a["unit_price"] == 545 and a["total"] == 21800


def test_price_tiers_and_setup_fee():
    s = {"garment_type": "round neck tee", "personalisation": []}
    assert calculate({**s, "quantity": 10}, "dtf")["unit_price"] == round(355 * 1.15)
    assert calculate({**s, "quantity": 100}, "dtf")["unit_price"] == round(355 * 0.88)
    screen = calculate({**s, "quantity": 30}, "screen")
    assert screen["setup_fee"] == 1500 and screen["total"] == 330 * 30 + 1500


def test_kepler_offers_budget_alternative():
    spec = {"quantity": 40, "garment_type": "cricket jersey", "personalisation": ["names"], "budget": 21000}
    out = kepler.run(req("pricing", {"spec": spec, "print_method": "sublimation",
                                     "allowed_methods": ["sublimation", "dtf"]})).data
    assert out["recommended"]["total"] == 21800 and out["quote"]["method"] == "dtf"
    assert out["quote"]["total"] == 20800 and out["within_budget"]


# ---------- Rosetta ------------------------------------------------------------------------
def test_text_grounding_helpers():
    assert {40, 25000, 15}.issubset(numbers_in(BRIEF))
    assert 150000 in numbers_in("budget 1.5 lakh")
    assert parse_date("on 15 Nov", date(2026, 9, 26)) == "2026-11-15"
    assert parse_date("by Jan 10", date(2026, 9, 26)) == "2027-01-10"


def test_rosetta_baseline_extracts_and_asks_only_what_matters():
    out = rosetta.run(req("requirements_analysis", {"transcript": [BRIEF], "today": TODAY}))
    spec = out.data["spec"]
    assert (spec["garment_type"], spec["quantity"], spec["deadline"], spec["budget"]) == \
        ("cricket jersey", 40, "2026-11-15", 25000)
    assert spec["colours"] == ["navy", "gold"] and spec["personalisation"] == ["names"]
    assert out.status == "needs_input" and out.questions == ["Half sleeve or full sleeve?"]


def test_rosetta_answer_resolves_and_no_reask():
    out = rosetta.run(req("requirements_analysis", {"transcript": [BRIEF, "Half sleeve please"],
                                                    "today": TODAY, "already_asked": ["sleeve"]}))
    assert out.status == "ok" and out.data["spec"]["sleeve"] == "half"


class FabricatingLLM(LLMClient):
    is_mock = False
    def generate_json(self, model, prompt, schema, images=None):
        return {"garment_type": "cricket jerseys", "quantity": 50, "deadline": "2026-11-15", "colours": ["Navy"],
                "budget": 30000, "sleeve": "", "personalisation": ["names"], "language": "en", "notes": ""}


def test_rosetta_drops_fabricated_numbers():
    set_llm(FabricatingLLM())
    try:
        out = rosetta.run(req("requirements_analysis", {"transcript": [BRIEF], "today": TODAY}))
    finally:
        set_llm(None)
    spec, dropped = out.data["spec"], {d["field"] for d in out.data["dropped"]}
    assert spec["quantity"] == 0 and spec["budget"] == 0 and dropped == {"quantity", "budget"}
    assert "How many pieces do you need?" in out.questions


# ---------- Spectra ------------------------------------------------------------------------
def _art(raw, garment="cricket jersey", qty=40):
    return spectra.run(req("artwork_analysis", {"logo_b64": base64.b64encode(raw).decode(),
                                                "garment_type": garment, "quantity": qty})).data


def test_spectra_policy_selects_method():
    g = _art(gradient_logo())
    assert g["measured"]["gradient_suspected"] and g["print_method"] == "sublimation"
    assert "screen" not in g["allowed_methods"]
    f = _art(flat_logo())
    assert f["measured"]["colour_count"] <= 3 and f["print_method"] == "screen"
    assert _art(gradient_logo(), garment="polo")["print_method"] == "dtf"
    assert _art(flat_logo(), qty=10)["print_method"] == "dtf"


def test_spectra_flags_small_artwork():
    assert any("1000 px" in p for p in _art(flat_logo(400, 200))["file_problems"])


# ---------- Hubble -------------------------------------------------------------------------
def test_hubble_proofs_preserve_logo_and_follow_colours():
    out = hubble.run(req("proof_generation", {"logo_b64": base64.b64encode(flat_logo()).decode(),
                                              "colours": ["navy", "gold"], "personalisation": ["names"],
                                              "count": 3}))
    proofs = out.data["proofs"]
    assert out.status == "ok" and len(proofs) == 3
    assert (proofs[0]["body"], proofs[0]["trim"]) == ("navy", "gold")
    assert all(p["fidelity"]["aspect_ok"] for p in proofs) and len(out.data["logo_sha256"]) == 64


# ---------- Pleiades -----------------------------------------------------------------------
def test_pleiades_validation_duplicates_and_ready(app):
    c = pleiades.run(req("roster_management", {"action": "create", "expected": 2, "needs": ["names", "numbers"]}))
    tok = c.data["token"]
    bad = pleiades.run(req("roster_management", {"action": "submit", "token": tok,
                                                 "entry": {"name": "A", "size": "XXXXL", "number": "7"}}))
    assert bad.status == "needs_input" and "Size" in bad.questions[0]
    for name in ("Asha", "Ben"):
        pleiades.run(req("roster_management", {"action": "submit", "token": tok,
                                               "entry": {"name": name, "size": "m", "number": "7"}}))
    chk = pleiades.run(req("roster_management", {"action": "check", "token": tok})).data
    assert chk["complete"] and not chk["ready"] and "7" in chk["problems"][0]
    assert chk["size_breakdown"] == {"M": 2}
    with pytest.raises(ValueError):                                         # session isolation
        pleiades.run(req("roster_management", {"action": "check", "token": tok}, session="other"))


# ---------- Horizon & voice ----------------------------------------------------------------
def test_horizon_is_reproducible_and_sensible():
    p = {"deadline": "2026-11-15", "quantity": 40, "print_method": "sublimation", "today": TODAY}
    a = horizon.run(req("fulfillment_confidence", p)).data
    assert a == horizon.run(req("fulfillment_confidence", p)).data and a["status"] == "on_track"
    tight = horizon.run(req("fulfillment_confidence", {**p, "deadline": "2026-10-04"})).data
    assert tight["p_on_time"] < 0.2 and tight["status"] == "unlikely" and tight["advice"]


class Translator(LLMClient):
    is_mock = False
    def __init__(self, reply): self.reply = reply
    def generate_text(self, model, prompt): return self.reply


def test_voice_rejects_rewrites_that_change_numbers():
    src = "Your quote: 21,800 INR for 40 pieces."
    set_llm(Translator("Aapka quote: 21,000 INR, 40 pieces."))
    try:
        assert voice.run(req("compose_reply", {"text": src, "locale": "hi"})).data["text"] == src
        set_llm(Translator("Aapka quote: 21,800 INR, 40 pieces ke liye."))
        assert "Aapka" in voice.run(req("compose_reply", {"text": src, "locale": "hi"})).data["text"]
    finally:
        set_llm(None)


def test_first_light_dev_set_has_no_fabrication_and_few_questions():
    from evals.first_light import main
    report = main("evals/briefs.jsonl")
    assert report["fabricated_fields"] == 0 and report["max_questions"] <= 3
    assert report["overall_accuracy"] >= 0.9
