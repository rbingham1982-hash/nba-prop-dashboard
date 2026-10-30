"""
nba_cards.py — the "research behind the bets" card: one panel per leg with its price and a
green-plus / amber-bang note for and against, then the ticket's price band.

The layout of the hand-made cards posted on 2026-10-26 and 10-29 (and the NFL Sunday card),
so the automated daily post looks like the ones people have already seen.
"""
from __future__ import annotations

import io
import pathlib

import requests
from PIL import Image, ImageDraw, ImageFilter

from publish import _font

W, H = 1080, 1350
BG = (12, 14, 22)
CARD = (22, 26, 40)
LINE = (44, 50, 72)
FG = (243, 244, 246)
MUTED = (139, 146, 168)
ACCENT = (129, 140, 248)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
SHOT = "https://cdn.nba.com/headshots/nba/latest/1040x760/{}.png"

# Ring colours, legible on the dark card (navy/black primaries swapped for a secondary).
RING = {"ATL": (224, 58, 62), "BOS": (0, 122, 51), "BKN": (160, 160, 165), "CHA": (0, 120, 140),
        "CHI": (206, 17, 65), "CLE": (134, 0, 56), "DAL": (0, 83, 188), "DEN": (254, 197, 36),
        "DET": (200, 16, 46), "GSW": (255, 199, 44), "HOU": (206, 17, 65), "IND": (253, 187, 48),
        "LAC": (200, 16, 46), "LAL": (253, 185, 39), "MEM": (93, 118, 169), "MIA": (152, 0, 46),
        "MIL": (0, 71, 27), "MIN": (35, 97, 146), "NOP": (180, 151, 90), "NYK": (245, 132, 38),
        "OKC": (0, 122, 193), "ORL": (0, 119, 192), "PHI": (0, 107, 182), "PHX": (229, 95, 32),
        "POR": (224, 58, 62), "SAC": (91, 43, 130), "SAS": (196, 206, 212), "TOR": (206, 17, 65),
        "UTA": (117, 59, 189), "WAS": (227, 24, 55)}


def headshot(pid, ring, size: int = 118):
    try:
        im = Image.open(io.BytesIO(requests.get(SHOT.format(int(pid)), timeout=15).content)).convert("RGBA")
    except Exception:
        im = Image.new("RGBA", (size, size), (40, 46, 66, 255))
    s = min(im.size)
    im = im.crop(((im.width - s) // 2, 0, (im.width + s) // 2, s)).resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size + 10, size + 10), (0, 0, 0, 0))
    ImageDraw.Draw(out).ellipse((0, 0, size + 9, size + 9), fill=tuple(ring) + (255,))
    disc = Image.new("RGBA", (size, size), (28, 32, 48, 255))
    disc.paste(im, (0, 0), im)
    out.paste(disc, (5, 5), mask)
    return out


def _fit(d, text: str, size: int, bold: bool, width: int):
    while size > 16 and d.textlength(text, font=_font(size, bold)) > width:
        size -= 1
    return _font(size, bold)


def render_research(legs: list, kicker: str, subtitle: str, band: tuple, source: str,
                    path: pathlib.Path) -> pathlib.Path:
    """
    legs: [{game, odds, pid, team, title, notes: [("+"|"!", text), ...]}], 3 or 4 of them.
    band: (headline, small tag, line under it).
    """
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-200, -380, 900, 330), fill=(55, 48, 163))
    gd.ellipse((560, -300, 1400, 260), fill=(30, 64, 120))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(120)), 0.75)
    d = ImageDraw.Draw(img)

    d.text((60, 52), kicker, font=_font(26, True), fill=(165, 180, 252))
    d.text((58, 88), "The research behind the bets", font=_font(56, True), fill=FG)
    d.text((60, 160), subtitle, font=_fit(d, subtitle, 25, False, W - 120), fill=(199, 210, 254))

    top, gap = 210, 12
    n = len(legs)
    ph = (1152 - top - (n - 1) * gap) // n
    f_game, f_odds = _font(24, True), _font(34, True)
    for i, p in enumerate(legs):
        y0 = top + i * (ph + gap)
        d.rounded_rectangle((40, y0, W - 40, y0 + ph), radius=22, fill=CARD, outline=LINE, width=2)
        hs = headshot(p["pid"], RING.get(p.get("team"), ACCENT))
        img.paste(hs, (62, y0 + (ph - 128) // 2), hs)
        x = 210
        d.text((x, y0 + 18), p["game"], font=f_game, fill=MUTED)
        ow = d.textlength(p["odds"], font=f_odds)
        plus = p["odds"].startswith("+")
        d.rounded_rectangle((W - 72 - ow - 26, y0 + 12, W - 62, y0 + 56), radius=14,
                            fill=(20, 83, 45) if plus else (30, 41, 59))
        d.text((W - 62 - 13 - ow, y0 + 13), p["odds"], font=f_odds, fill=GREEN if plus else FG)
        d.text((x, y0 + 54), p["title"], font=_fit(d, p["title"], 30, True, W - 66 - x - ow - 40), fill=FG)
        ny = y0 + 100
        for kind, text in p["notes"][:3]:
            col = GREEN if kind == "+" else AMBER
            d.ellipse((x, ny + 3, x + 24, ny + 27), fill=col)
            sw = d.textlength(kind, font=_font(20, True))
            d.text((x + 12 - sw / 2, ny + 2), kind, font=_font(20, True), fill=BG)
            d.text((x + 36, ny + 1), text, font=_fit(d, text, 22, False, W - 80 - x - 36),
                   fill=(214, 218, 230))
            ny += 36

    head, tag, line = band
    d.rounded_rectangle((40, 1164, W - 40, 1244), radius=20, fill=(20, 83, 45))
    d.text((66, 1170), head, font=_font(32, True), fill=GREEN)
    d.text((66 + d.textlength(head, font=_font(32, True)) + 16, 1182), tag,
           font=_font(20, True), fill=(187, 247, 208))
    d.text((66, 1212), line, font=_fit(d, line, 20, False, W - 132), fill=(187, 247, 208))

    d.line([(60, 1258), (W - 60, 1258)], fill=LINE, width=2)
    d.text((60, 1272), "KONJURE ANALYTICS", font=_font(24, True), fill=ACCENT)
    d.text((60, 1306), source, font=_font(20), fill=MUTED)
    t = "21+  ·  Not betting advice  ·  1-800-GAMBLER"
    d.text((W - 60 - d.textlength(t, font=_font(20)), 1278), t, font=_font(20), fill=MUTED)
    t2 = "konjureanalytics.com"
    d.text((W - 60 - d.textlength(t2, font=_font(20, True)), 1306), t2, font=_font(20, True), fill=FG)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=93, optimize=True)
    return path
