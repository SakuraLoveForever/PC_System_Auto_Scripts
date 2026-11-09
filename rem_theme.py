"""Rem theme art using the original anime character asset.

Everything here is cached by (size, colours): the UI re-styles on every theme
switch and every list refresh, so drawing on demand would be visibly slow.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def hex_rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgba(value: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    return (*hex_rgb(value), alpha)


def mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours; t=0 -> a, t=1 -> b."""
    ca, cb = hex_rgb(a), hex_rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


# ---------------------------------------------------------------------------
# Theme palette for the surrounding glow and decorations
# ---------------------------------------------------------------------------
CHAR_DEFAULTS: Dict[str, str] = {
    "hair": "#5fade0",
    "hair_d": "#3b86bd",
    "hair_l": "#b7e4ff",
    "skin": "#ffe6da",
    "skin_s": "#f3c2b4",
    "eye": "#3d8fe0",
    "eye_d": "#173f78",
    "cloth": "#1d2540",
    "cloth_l": "#303c5e",
    "line": "#33283c",
    "ribbon": "#f4f7ff",
    "blush": "#ffb3bd",
}

_SUPERSAMPLE = 4


def palette_from_theme(theme) -> Dict[str, str]:
    """Keep the existing theme palette API for decorative colours."""
    p = dict(CHAR_DEFAULTS)
    p.update(getattr(theme, "char_palette", None) or {})
    return p


# ---------------------------------------------------------------------------
# Rem bust
# ---------------------------------------------------------------------------

def _star(d, cx, cy, r, fill, inner=0.42):
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d.polygon(pts, fill=fill)


@lru_cache(maxsize=1)
def _character_art() -> Image.Image:
    # __file__ resolves inside PyInstaller's bundle as well as the source tree.
    with Image.open(Path(__file__).resolve().parent / "assets" / "rem" / "classic.png") as source:
        return source.convert("RGBA")


def draw_rem(size: int = 512, palette: Dict[str, str] | None = None) -> Image.Image:
    """Original upper-body art; theme colours never alter the character."""
    return _character_art().crop((180, 110, 900, 830)).resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

def _palette_key(palette: Dict[str, str]) -> Tuple:
    return tuple(sorted(palette.items()))


@lru_cache(maxsize=4)
def _rem_sprite(size: int, key: Tuple) -> Image.Image:
    return draw_rem(size, dict(key))


def rem_sprite(size: int, palette: Dict[str, str]) -> Image.Image:
    return _rem_sprite(int(size), _palette_key(palette))


@lru_cache(maxsize=4)
def _rem_round(size: int, key: Tuple, ring: str) -> Image.Image:
    """Rem in a circular avatar with a ring and a soft glow — the sidebar badge."""
    S = _SUPERSAMPLE
    W = size * S
    # Frame the original face directly so it stays legible at 36 logical pixels.
    crop = _character_art().crop((410, 120, 770, 480)).resize((W, W), Image.LANCZOS)
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, W-1, W-1], fill=255)
    glow = Image.new("RGBA", (W, W), rgba(ring, 255))
    glow.putalpha(mask.filter(ImageFilter.GaussianBlur(int(0.05*W))))
    out = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    out.alpha_composite(glow)
    disc = Image.new("RGBA", (W, W), rgba("#0b1020"))
    disc.putalpha(mask)
    out.alpha_composite(disc)
    inner = crop.copy()
    inner.putalpha(ImageChops.multiply(inner.getchannel("A"), mask))
    out.alpha_composite(inner)
    ring_mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(ring_mask).ellipse([0, 0, W-1, W-1], outline=255, width=int(0.045*W))
    out.alpha_composite(Image.composite(Image.new("RGBA", (W, W), rgba(ring)), out, ring_mask))
    return out.resize((size, size), Image.LANCZOS)


def rem_avatar(size: int, palette: Dict[str, str], ring: str = "#6fd0ff") -> Image.Image:
    return _rem_round(int(size), _palette_key(palette), ring)

@lru_cache(maxsize=4)
def _backdrop(width: int, height: int, key: Tuple, canvas: str, glow: str) -> Image.Image:
    """Rem fading in from the right edge, over a starlit water glow, colour-keyed to the canvas."""
    palette = dict(key)
    canvas_rgb = hex_rgb(canvas)
    out = Image.new("RGBA", (width, height), (*canvas_rgb, 255))

    # Water-blue glow bled in from the right. Blurred on a 1/4-scale canvas and
    # upscaled: a full-size Gaussian here was 15ms+ per paint and grew with the
    # window, which is what made resizing feel sticky.
    gw, gh = max(16, width // 4), max(16, height // 4)
    glow_layer = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.ellipse([int(gw * 0.30), int(gh * 0.05), int(gw * 1.45), int(gh * 1.25)],
               fill=rgba(glow, 90))
    gd.ellipse([int(gw * 0.80), int(gh * 0.45), int(gw * 1.30), int(gh * 1.30)],
               fill=rgba(palette["hair_l"], 46))
    blurred = glow_layer.filter(ImageFilter.GaussianBlur(max(2, height // 48)))
    out.alpha_composite(blurred.resize((width, height), Image.BILINEAR))

    # character bust, anchored bottom-right
    bust_h = int(height * 0.86)
    bust_w = int(bust_h * 1.0)
    sprite = rem_sprite(bust_h, palette)
    bust = Image.new("RGBA", (bust_w, bust_h), (0, 0, 0, 0))
    bust.alpha_composite(sprite, (0, 0))

    # fade the bust out towards the left edge and bottom so it reads as atmosphere
    # (starts at 0 so the sprite's colorkey-black edge never shows as a seam)
    # Gradients built as 256px strips and resized: a per-pixel loop here cost
    # ~20ms per repaint and was the visible stutter while resizing.
    ramp_x = Image.new("L", (256, 1))
    ramp_x.putdata([int(255 * min(1.0, 1.25 * (x / 255.0))) for x in range(256)])
    fade = ramp_x.resize((bust_w, bust_h), Image.BILINEAR)
    ramp_y = Image.new("L", (1, 256))
    ramp_y.putdata([255 if y / 255.0 <= 0.62 else int(255 * (1 - (y / 255.0 - 0.62) / 0.38))
                    for y in range(256)])
    bottom = ramp_y.resize((bust_w, bust_h), Image.BILINEAR)
    mask = ImageChops.multiply(fade, bottom)
    mask = ImageChops.multiply(mask, Image.new("L", (bust_w, bust_h), 235))
    bust.putalpha(ImageChops.multiply(bust.getchannel("A"), mask))

    out.alpha_composite(bust, (width - bust_w + int(bust_w * 0.12), height - bust_h))

    # starlight specks
    sd = ImageDraw.Draw(out)
    step = max(46, width // 9)
    for i, x in enumerate(range(int(width * 0.04), int(width * 0.62), step)):
        y = (i * 37 + height // 9) % max(1, height - 40) + 20
        r = 6 + (i * 13) % 9
        _star(sd, x + (i * 53) % step, y, r, rgba(palette["hair_l"], 70 + (i * 29) % 60))
    return out


def backdrop(width: int, height: int, palette: Dict[str, str], canvas: str,
             glow: str) -> Image.Image:
    return _backdrop(max(64, int(width)), max(64, int(height)), _palette_key(palette),
                     canvas, glow)


@lru_cache(maxsize=4)
def _portrait(width: int, height: int, key: Tuple, canvas: str, glow: str) -> Image.Image:
    """Rem filling a tall narrow rail: whole bust, not a wider picture cropped."""
    palette = dict(key)
    canvas_rgb = hex_rgb(canvas)
    out = Image.new("RGBA", (width, height), (*canvas_rgb, 255))

    # Glow behind her, so the rail reads as lit water rather than a flat strip.
    gw, gh = max(16, width // 4), max(16, height // 4)
    glow_layer = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.ellipse([int(gw * 0.05), int(gh * 0.02), int(gw * 1.05), int(gh * 1.05)],
               fill=rgba(glow, 70))
    gd.ellipse([int(gw * 0.30), int(gh * 0.55), int(gw * 1.10), int(gh * 1.20)],
               fill=rgba(palette["hair_l"], 40))
    blurred = glow_layer.filter(ImageFilter.GaussianBlur(max(2, height // 40)))
    out.alpha_composite(blurred.resize((width, height), Image.BILINEAR))

    # The sprite is a square head-and-shoulders figure. Drawn at rail height it
    # would be 580px wide and lose 190px off each side to the 200px rail, which
    # cuts through the hair. Draw it once at a size whose head-and-torso crop
    # fits the rail width, then place that crop.
    sprite_px = max(width, 256) * 3
    sprite = rem_sprite(sprite_px, palette)
    box = sprite.getbbox() or (0, 0, sprite_px, sprite_px)
    # Centre column of the sprite: the rail's aspect ratio decides how much of
    # her height fits, anchored at the top of the head.
    face_cx = (box[0] + box[2]) // 2
    half_w = int(sprite_px * width / (2.0 * height))
    top = box[1]
    crop = sprite.crop((max(box[0], face_cx - half_w), top,
                        min(box[2], face_cx + half_w), box[3]))
    # Fill the rail width, then stretch vertically to the rail's full height so
    # she occupies the strip instead of floating in its top third.
    crop = crop.resize((width, height), Image.LANCZOS)
    bust = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    bust.alpha_composite(crop, (0, 0))

    # Soft left edge so the rail blends into the content column instead of
    # ending on a hard vertical cut, plus a bottom fade so her skirt dissolves
    # rather than being clipped by the window edge.
    ramp_x = Image.new("L", (256, 1))
    ramp_x.putdata([int(255 * min(1.0, 4.0 * (x / 255.0))) for x in range(256)])
    ramp_y = Image.new("L", (1, 256))
    ramp_y.putdata([255 if y / 255.0 <= 0.86 else int(255 * (1 - (y / 255.0 - 0.86) / 0.14))
                    for y in range(256)])
    mask = ImageChops.multiply(ramp_x.resize(bust.size, Image.BILINEAR),
                               ramp_y.resize(bust.size, Image.BILINEAR))
    bust.putalpha(ImageChops.multiply(bust.getchannel("A"), mask))
    out.alpha_composite(bust, (0, 0))
    return out


def char_portrait(width: int, height: int, palette: Dict[str, str], canvas: str,
                  glow: str) -> Image.Image:
    return _portrait(max(64, int(width)), max(64, int(height)), _palette_key(palette),
                     canvas, glow)