"""Shared test data: the worked-example brief and synthetic logos."""
import io
from pathlib import Path
from PIL import Image, ImageDraw

BRIEF = ("Need jerseys for our company cricket tournament on 15 Nov. Around 40 people. "
         "Navy and gold, logo attached, budget roughly 25k, names on the back.")
TODAY = "2026-09-26"


def png(img: Image.Image) -> bytes:
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()


def gradient_logo(w=1200, h=800) -> bytes:
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = (int(255 * x / w), int(255 * y / h), 160, 255)
    return png(img)


def flat_logo(w=1200, h=600) -> bytes:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 600, 500], fill=(200, 20, 30, 255))
    d.ellipse([650, 100, 1100, 500], fill=(255, 255, 255, 255))
    return png(img)


ROOT = Path(__file__).resolve().parents[1]
