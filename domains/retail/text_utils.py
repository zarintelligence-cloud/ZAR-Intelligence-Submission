"""Deterministic text helpers: grounding checks, date parsing and the offline baseline extractor."""
from __future__ import annotations
import re
from datetime import date
from . import policies

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(k|thousand|lakhs?|l)?(?![\w])", re.I)


def numbers_in(text: str) -> set[int]:
    """Every number a customer literally wrote, including 25k / 1.5 lakh forms."""
    out: set[int] = set()
    for value, unit in _NUM.findall(text.replace(",", "")):
        n = float(value)
        u = (unit or "").lower()
        n *= 1000 if u in {"k", "thousand"} else 100000 if u.startswith("l") and u else 1
        out.add(int(round(n)))
    return out


def parse_date(text: str, today: date) -> str | None:
    """Parse '15 Nov', 'Nov 15', '15/11', '15-11-2026' or ISO; infer the next occurrence."""
    t = text.lower()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try: return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError: return None
    day = month = year = None
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3})[a-z]*\.?(?:\s+(\d{4}))?", t):
        if m[2] in MONTHS:
            day, month, year = int(m[1]), MONTHS[m[2]], m[3]
            break
    if not month:
        for m in re.finditer(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", t):
            if m[1] in MONTHS:
                day, month, year = int(m[2]), MONTHS[m[1]], m[3]
                break
    if not month:
        m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
        if m:
            day, month, year = int(m[1]), int(m[2]), m[3]
    if not day or not month:
        return None
    try:
        y = int(year) if year else today.year
        y = y + 2000 if y < 100 else y
        d = date(y, month, day)
        if not year and d < today:
            d = date(y + 1, month, day)
        return d.isoformat()
    except ValueError:
        return None


def normalise_garment(text: str) -> str | None:
    t = text.lower()
    best: tuple[int, str] | None = None
    for garment, info in policies.catalog()["garments"].items():
        for alias in info["aliases"]:
            if re.search(rf"\b{re.escape(alias)}s?\b", t) and (best is None or len(alias) > best[0]):
                best = (len(alias), garment)
    return best[1] if best else None


def colours_in(text: str) -> list[str]:
    t, found = text.lower(), []
    for name in sorted(policies.catalog()["colours"], key=len, reverse=True):
        if re.search(rf"\b{name}\b", t) and not any(name in f for f in found):
            found.append(name)
    return sorted(found, key=lambda c: t.find(c))


def baseline_extract(transcript: list[str], today: date) -> dict:
    """Offline, rules-only extractor (mock mode + evaluation baseline). Each message is read on its
    own and later messages override earlier ones, so change requests win over the original brief."""
    merged: dict = {}
    for i, message in enumerate(transcript):
        part = _extract_one(message, today, follow_up=i > 0)
        for k, v in part.items():
            if v not in ("", 0, [], None) or k not in merged:
                merged[k] = v
    return merged


def _extract_one(text: str, today: date, follow_up: bool) -> dict:
    low = re.sub(r"(?<=\d),(?=\d)", "", text.lower())
    spec: dict = {"garment_type": normalise_garment(text) or "", "quantity": 0, "deadline": "",
                  "colours": colours_in(text), "budget": 0, "sleeve": "", "personalisation": [],
                  "language": "en"}
    m = re.search(r"(\d+)\s*(?:[\w+-]+\s+){0,4}?(people|players|members|kids|children|students|volunteers|staff|"
                  r"employees|pieces|pcs|nos|units|jerseys|t-shirts|tshirts|shirts|tees|polos|kits)\b", low)
    if m:
        spec["quantity"] = int(m[1])
    elif follow_up and text.strip().isdigit():
        spec["quantity"] = int(text.strip())
    spec["deadline"] = parse_date(text, today) or ""
    m = re.search(r"budget[^\d]{0,20}(\d+(?:\.\d+)?\s*(?:k|thousand|lakhs?)?)", low) or \
        re.search(r"(?:rs\.?|inr|\u20b9)\s*(\d+(?:\.\d+)?\s*(?:k|thousand|lakhs?)?)", low)
    if m:
        spec["budget"] = max(numbers_in(m[1]) or {0})
    if re.search(r"\b(half|short)[\s-]sleeves?\b", low):
        spec["sleeve"] = "half"
    elif re.search(r"\b(full|long)[\s-]sleeves?\b", low):
        spec["sleeve"] = "full"
    elif "sleeveless" in low:
        spec["sleeve"] = "sleeveless"
    if re.search(r"\bnames\b|\bname printing\b|\bwith names?\b", low):
        spec["personalisation"].append("names")
    if re.search(r"\bnumbers\b|\bjersey numbers?\b|\bwith numbers?\b", low):
        spec["personalisation"].append("numbers")
    if re.search(r"\bno (names|numbers|personali[sz]ation|printing on the back)\b|\bwithout names\b", low):
        spec["personalisation"] = ["none"]
    return spec
