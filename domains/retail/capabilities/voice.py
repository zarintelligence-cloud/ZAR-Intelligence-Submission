"""Polaris voice - compose_reply. Optional Gemini rewrite into the customer's language.
Guard: every number in the template must survive verbatim, otherwise the template is used."""
from __future__ import annotations
import re
from collections import Counter
from zar_core.config import MODEL_FAST
from zar_core.contracts import CapabilityRequest, CapabilityResult
from zar_core.llm import get_llm

PROMPT = """Rewrite the message below for a customer in language '{locale}'. Keep it warm and brief.
Keep every number, date, currency amount and URL exactly as written. Do not add facts.
Message:
{text}"""


def numbers_preserved(src: str, out: str) -> bool:
    pattern = r"\d+(?:[,./:-]\d+)*"
    return Counter(re.findall(pattern, src)) == Counter(re.findall(pattern, out)) and all(
        url in out for url in re.findall(r"(?:https?://\S+|/roster/[\w-]+)", src))


def run(req: CapabilityRequest) -> CapabilityResult:
    text, locale, llm = req.payload["text"], req.payload.get("locale", "en"), get_llm()
    reply, model = text, "template"
    if not llm.is_mock and locale != "en":
        candidate = llm.generate_text(MODEL_FAST, PROMPT.format(locale=locale, text=text)).strip()
        if candidate and numbers_preserved(text, candidate):
            reply, model = candidate, MODEL_FAST
    return CapabilityResult(request_id=req.request_id, capability=req.capability, status="ok",
                            data={"text": reply}, model_used=model,
                            debug={"template": text, "locale": locale})
