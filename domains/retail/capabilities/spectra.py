"""Spectra - artwork_analysis. Code measures, Gemini looks, policy decides the print method."""
from __future__ import annotations
import io
from collections import Counter
from PIL import Image
from zar_core.config import MODEL_REASONING
from zar_core.contracts import CapabilityRequest, CapabilityResult, Evidence
from zar_core.llm import get_llm
from zar_core.policy import DecisionTable, check
from zar_core.services import load_binary
from .. import policies

SCHEMA = {"type": "object", "properties": {
    "has_gradient": {"type": "boolean"}, "has_fine_text": {"type": "boolean"},
    "is_photographic": {"type": "boolean"}, "background_needs_removal": {"type": "boolean"},
    "description": {"type": "string"},
    "file_problems": {"type": "array", "items": {"type": "string"}}},
    "required": ["has_gradient", "has_fine_text", "is_photographic", "background_needs_removal",
                 "description", "file_problems"]}

PROMPT = """You are Spectra, a garment print-production expert. Inspect the attached logo.
Measured facts are authoritative and must not be contradicted: {facts}.
Report only what you can see: gradients or soft shading, text thinner than about 1 mm at 10 cm width,
photographic content, and whether a solid background box would need removal. List concrete file problems."""


def measure(raw: bytes) -> dict:
    img = Image.open(io.BytesIO(raw))
    w, h = img.size
    rgba = img.convert("RGBA")
    sample = rgba.copy()
    sample.thumbnail((160, 160))
    px = [p for p in sample.getdata() if p[3] > 20]
    buckets = Counter((r >> 4, g >> 4, b >> 4) for r, g, b, _ in px)
    total = max(1, len(px))
    covered, colour_count = 0, 0                 # colours needed to cover 95% of visible pixels
    for _, n in buckets.most_common():
        covered += n
        colour_count += 1
        if covered / total >= 0.95 or colour_count == 64:
            break
    top3 = sum(n for _, n in buckets.most_common(3)) / total
    alpha_min = rgba.getchannel("A").getextrema()[0]
    dominant = ["#%02x%02x%02x" % (r * 16 + 8, g * 16 + 8, b * 16 + 8) for (r, g, b), _ in buckets.most_common(5)]
    return {"width_px": w, "height_px": h, "aspect_ratio": round(w / h, 4),
            "colour_count": colour_count, "raw_colour_buckets": len(buckets),
            "top3_share": round(top3, 3), "gradient_suspected": len(buckets) >= 48 and top3 < 0.85,
            "has_transparency": alpha_min < 255, "dominant_colours": dominant,
            "dpi_at_10cm": round(w / (10 / 2.54)), "too_small_to_print": w < 1000}


def choose_method(facts: dict) -> dict:
    pol = policies.print_policy()
    table = DecisionTable(pol["rules"], pol["default"], pol.get("default_reason", ""))
    decision = table.decide(facts)
    allowed = [m for m in pol["fabric_methods"][facts["fabric"]]
               if not any(check(facts, c) for c in pol.get("exclusions", {}).get(m, []))]
    if decision.action not in allowed:                 # policy safety net
        allowed.append(decision.action)
    return {"print_method": decision.action, "rule_id": decision.rule_id,
            "reasons": decision.reasons, "allowed_methods": allowed}


def run(req: CapabilityRequest) -> CapabilityResult:
    raw, mime = load_binary(req.payload, "logo")
    facts = measure(raw)
    garment = req.payload["garment_type"]
    fabric = policies.catalog()["garments"][garment]["fabric"]
    llm, model = get_llm(), "measurement-only"
    visual = {"has_gradient": facts["gradient_suspected"], "has_fine_text": False,
              "is_photographic": False, "background_needs_removal": False,
              "description": "", "file_problems": []}
    if not llm.is_mock:
        visual = llm.generate_json(MODEL_REASONING, PROMPT.format(facts=facts), SCHEMA, [(raw, mime)])
        model = MODEL_REASONING
    gradient = bool(visual["has_gradient"] or visual["is_photographic"] or facts["gradient_suspected"])
    policy_facts = {"fabric": fabric, "gradient": gradient, "colour_count": facts["colour_count"],
                    "fine_text": bool(visual["has_fine_text"]), "quantity": int(req.payload["quantity"])}
    choice = choose_method(policy_facts)
    problems = list(visual.get("file_problems") or [])
    if facts["too_small_to_print"]:
        problems.append("Artwork is under 1000 px wide; please send a higher-resolution or vector file.")
    if visual.get("background_needs_removal"):
        problems.append("Logo sits on a solid background box; a transparent PNG or vector is preferred.")
    evidence = [Evidence(key="artwork.measured", value=facts, source="spectra:measure"),
                Evidence(key="artwork.gradient", value=gradient, source=f"spectra:{model}",
                         confidence=0.9 if model != "measurement-only" else 0.7),
                Evidence(key="artwork.fine_text", value=policy_facts["fine_text"], source=f"spectra:{model}",
                         confidence=0.85 if model != "measurement-only" else 0.5)]
    return CapabilityResult(request_id=req.request_id, capability=req.capability, status="ok",
                            data={"measured": facts, "visual": visual, "policy_facts": policy_facts,
                                  **choice, "file_problems": problems},
                            evidence=evidence, confidence=0.9 if model != "measurement-only" else 0.75,
                            model_used=model,
                            debug={"prompt": PROMPT.format(facts=facts), "model_called": model != "measurement-only",
                                   "visual_raw": visual, "policy_facts": policy_facts,
                                   "matched_rule": choice["rule_id"], "policy_file": "print_policy.yaml"})
