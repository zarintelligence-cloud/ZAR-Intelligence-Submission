"""Hubble - proof_generation. Deterministic compositing: the customer's logo is placed, never redrawn."""
from __future__ import annotations
import base64, io
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageStat
from zar_core.contracts import CapabilityRequest, CapabilityResult, Evidence
from zar_core.services import load_binary
from zar_core.assets import sha256
from .. import policies

W, H, TOP = 1600, 900, 150


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _shirt(cx: int, sleeve: str, front: bool) -> tuple[list[tuple[int, int]], list, list]:
    t = TOP
    neck = (cx, t + (60 if front else 22))
    if sleeve == "full":
        l_sleeve, r_sleeve = [(cx - 330, t + 470), (cx - 262, t + 492)], [(cx + 262, t + 492), (cx + 330, t + 470)]
    elif sleeve == "sleeveless":
        l_sleeve, r_sleeve = [(cx - 205, t + 60), (cx - 200, t + 150)], [(cx + 200, t + 150), (cx + 205, t + 60)]
    else:
        l_sleeve, r_sleeve = [(cx - 300, t + 175), (cx - 232, t + 228)], [(cx + 232, t + 228), (cx + 300, t + 175)]
    pts = [(cx - 72, t), (cx - 200, t + 32), *l_sleeve, (cx - 200, t + 170), (cx - 200, t + 600),
           (cx + 200, t + 600), (cx + 200, t + 170), *r_sleeve, (cx + 200, t + 32), (cx + 72, t), neck]
    return pts, l_sleeve, r_sleeve


def _luma(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def colourways(requested: list[str], count: int) -> list[tuple[str, str]]:
    cat = policies.catalog()
    names = [c for c in requested if c in cat["colours"]]
    if len(names) >= 2:
        a, b = names[0], names[1]
        options = [(a, b), (b, a), ("white" if "white" not in (a, b) else "black", a)]
    elif len(names) == 1:
        a = names[0]
        options = [(a, "white" if a != "white" else "navy"), ("white" if a != "white" else "black", a), ("black", a)]
    else:
        d = cat["default_colourways"]
        options = [(d[0], "gold"), (d[1], "white"), (d[2], d[0])]
    options += [("navy", "gold"), ("white", "navy"), ("black", "white")]
    seen, out = set(), []
    for o in options:
        if o not in seen and o[0] != o[1]:
            seen.add(o)
            out.append(o)
    return out[:count]


def compose(logo_raw: bytes, body: str, trim: str, *, sleeve: str, personalisation: list[str],
            method: str) -> tuple[bytes, dict]:
    colours = policies.catalog()["colours"]
    body_rgb, trim_rgb = tuple(colours[body]), tuple(colours[trim])
    canvas = Image.new("RGB", (W, H), (238, 240, 245))
    draw = ImageDraw.Draw(canvas)
    for cx, front in ((420, True), (1180, False)):
        pts, ls, rs = _shirt(cx, sleeve or "half", front)
        draw.polygon(pts, fill=body_rgb, outline=(60, 60, 70))
        draw.line([pts[0], pts[-1], pts[-2]], fill=trim_rgb, width=14, joint="curve")   # collar
        if sleeve != "sleeveless":
            draw.line(ls, fill=trim_rgb, width=16)
            draw.line(rs, fill=trim_rgb, width=16)
        draw.line([(cx - 200, TOP + 592), (cx + 200, TOP + 592)], fill=trim_rgb, width=12)
        draw.text((cx, TOP + 640), "FRONT" if front else "BACK", fill=(90, 90, 100), font=_font(22), anchor="mm")
    logo = Image.open(io.BytesIO(logo_raw)).convert("RGBA")
    box_w, box_h = 230, 170
    scale = min(box_w / logo.width, box_h / logo.height)
    placed = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))),
                         Image.Resampling.LANCZOS)
    position = (420 - placed.width // 2, TOP + 250 - placed.height // 2)
    canvas.paste(placed, position, placed)
    if personalisation:
        if "names" in personalisation:
            draw.text((1180, TOP + 170), "PLAYER", fill=trim_rgb, font=_font(46), anchor="mm")
        if "numbers" in personalisation:
            draw.text((1180, TOP + 330), "10", fill=trim_rgb, font=_font(170), anchor="mm")
    draw.text((40, 40), f"ZAR proof  |  {body} body / {trim} trim  |  {method}  |  not to scale",
              fill=(40, 40, 50), font=_font(26))
    box = (*position, position[0] + placed.width, position[1] + placed.height)
    expected = canvas.crop(box)
    for quality in (90, 98, 100):
        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=quality, subsampling=0)
        encoded = Image.open(io.BytesIO(buf.getvalue())).convert("RGB").crop(box)
        score = sum(ImageStat.Stat(ImageChops.difference(expected, encoded)).mean) / 3
        if score <= 12:
            break
    aspect_in, aspect_out = logo.width / logo.height, placed.width / placed.height
    vis = [p[:3] for p in logo.resize((64, 64)).getdata() if p[3] > 20] or [(0, 0, 0)]
    logo_luma = sum(_luma(p) for p in vis) / len(vis)
    warnings = []
    if abs(logo_luma - _luma(body_rgb)) < 40:
        warnings.append(f"Low contrast: logo may disappear on a {body} body.")
    fidelity = {"aspect_in": round(aspect_in, 4), "aspect_out": round(aspect_out, 4),
                "aspect_ok": abs(aspect_out - aspect_in) / aspect_in < 0.01,
                "encoding_mae": round(score, 3), "threshold": 12, "colour_ok": score <= 12,
                "metric": "RGB mean absolute encoding error in the composited logo region (0–255)"}
    return buf.getvalue(), {"fidelity": fidelity, "warnings": warnings}


def run(req: CapabilityRequest) -> CapabilityResult:
    logo, _ = load_binary(req.payload, "logo")
    count = max(1, min(int(req.payload.get("count", 3)), 3))
    proofs = []
    for i, (body, trim) in enumerate(colourways(req.payload.get("colours") or [], count), 1):
        raw, checks = compose(logo, body, trim, sleeve=req.payload.get("sleeve") or "half",
                              personalisation=req.payload.get("personalisation") or [],
                              method=req.payload.get("print_method", ""))
        proofs.append({"proof_id": f"P{i}", "body": body, "trim": trim, "mime_type": "image/jpeg",
                       "image_b64": base64.b64encode(raw).decode(), **checks})
    ok = all(p["fidelity"]["aspect_ok"] and p["fidelity"]["colour_ok"] for p in proofs)
    return CapabilityResult(
        request_id=req.request_id, capability=req.capability, status="ok" if ok else "error",
        data={"proofs": proofs, "method": "deterministic_compositing", "logo_sha256": sha256(logo)},
        evidence=[Evidence(key="proof.aspect_preserved", value=ok, source="hubble:compose")],
        error=None if ok else "Logo proportion or colour encoding check failed", confidence=1.0, model_used="none",
        debug={"colourways": [f"{p['body']}/{p['trim']}" for p in proofs], "print_box_px": [230, 170],
               "canvas_px": [W, H], "fidelity": [p["fidelity"] for p in proofs]})
