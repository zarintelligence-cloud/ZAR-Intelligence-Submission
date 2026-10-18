"""Kepler's arithmetic. Rules-only: no model can change a number."""
from __future__ import annotations
from . import policies


def tier_multiplier(qty: int) -> float:
    return float([t["multiplier"] for t in policies.pricing()["quantity_tiers"] if qty >= t["minimum"]][-1])


def calculate(spec: dict, method: str) -> dict:
    rules, garments = policies.pricing(), policies.catalog()["garments"]
    qty, garment = int(spec["quantity"]), spec["garment_type"]
    if qty <= 0:
        raise ValueError("Quantity must be positive")
    if garment not in garments:
        raise ValueError(f"Unsupported garment_type: {garment}")
    if method not in rules["print_methods"]:
        raise ValueError(f"Unsupported print_method: {method}")
    base = float(garments[garment]["base_price"])
    print_cost = float(rules["print_methods"][method]["per_piece"])
    personal = float(rules["name_number_charge"]) if set(spec.get("personalisation") or []) & {"names", "numbers"} else 0.0
    multiplier = tier_multiplier(qty)
    unit = round((base + print_cost + personal) * multiplier)
    setup = int(rules["print_methods"][method]["setup"])
    subtotal = unit * qty + setup
    tax = round(subtotal * float(rules.get("tax_rate", 0)))
    cur = rules["currency"]
    return {"currency": cur, "method": method, "quantity": qty, "unit_price": unit, "setup_fee": setup,
            "tier_multiplier": multiplier, "subtotal": subtotal, "tax": tax, "total": subtotal + tax,
            "breakdown": {"garment": base, "print": print_cost, "personalisation": personal},
            "lines": [f"{qty} x {garment} ({method}) @ {unit} {cur}"] +
                     ([f"Setup fee {setup} {cur}"] if setup else []) + ([f"Tax {tax} {cur}"] if tax else [])}
