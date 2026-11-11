"""
video_builder.py — faceless YouTube Shorts for Konjure Analytics, built from data the site
already logs.

First format: "How we did last night" — yesterday's published NBA spread picks, graded,
then the season record against break-even. The point of the channel is that every pick is
logged before tip and graded in public, losses included, so the script reads the record as
it is and never rounds a bad night up.

Look: sports-picks broadcast style — condensed Impact type in orange and gold on charcoal,
a thick orange frame, gold hexagon bands, diagonal split matchup tiles. No league logos or
player images: both are the leagues' marks and draw claims on a monetized channel, so teams
are drawn in their colours with their abbreviations.

How a Short is made
-------------------
Every frame is drawn (30 fps, 1080x1920); a scene is a draw(img, t) function plus a line of
narration, and its animation is timed to the voice.
  * Narration: neural TTS with word timings, so captions light up word by word (most
    Shorts are watched muted) and the WIN/LOSS stamp lands on "That covers" / "That's a loss".
  * Sound: a boom-bap beat, scratches and hits, synthesised here with numpy so there is no
    licensing question. To use a real track instead (e.g. from the YouTube Audio Library,
    which is cleared for monetized videos), drop an mp3 in assets/music/ or set
    KONJURE_MUSIC to its path; it is looped and ducked under the voice the same way.

Voice: edge-tts (Microsoft's neural voices) for previews. It is not a licensed commercial
TTS service; before the channel monetizes, swap _tts() for a paid provider.

    python video_builder.py results [YYYY-MM-DD]     # results for that night (default: yesterday)
    python video_builder.py top5 [YYYY-MM-DD]        # players of the night, a 5-to-1 countdown
    python video_builder.py clone-voice RECORDING    # ElevenLabs instant clone of the owner's voice
    python video_builder.py gtg [YYYY-MM-DD]         # Guess the Grade: 3 of the day's challenge lines
    python video_builder.py btm [YYYY-MM-DD]         # Beat the Market: tonight's frozen five props
    python video_builder.py picks [YYYY-MM-DD]       # tonight's top 5 NBA picks (logged, graded tomorrow)
    python video_builder.py nfl-td [YYYY-MM-DD]      # the day's NFL anytime-TD board (Sun / Thu)
    python video_builder.py nfl-results [SUNDAY]     # Sunday's NFL spread picks, graded (Mon)
    python video_builder.py nfl-top5 [SUNDAY]        # Sunday's top five graded NFL players (Mon)
"""
from __future__ import annotations

import asyncio
import datetime
import io
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from publish import _font

W, H, FPS, SR = 1080, 1920, 30, 44100
CHAR = (24, 24, 27)
WHITE = (250, 250, 250)
MUTED = (160, 160, 168)
ORANGE = (255, 140, 26)
GOLD = (247, 188, 0)
GREEN = (46, 204, 113)
RED = (235, 64, 64)
BLACK = (0, 0, 0)
VOICE = "en-US-AndrewNeural"
_RATE = "+12%"
OUT = pathlib.Path.home() / "Downloads"
ROOT = pathlib.Path(__file__).parent

TEAM = {  # primary colour, name for narration
    "ATL": ((225, 68, 52), "Atlanta"), "BOS": ((0, 122, 51), "Boston"), "BKN": ((80, 80, 80), "Brooklyn"),
    "CHA": ((29, 17, 96), "Charlotte"), "CHI": ((206, 17, 65), "Chicago"), "CLE": ((134, 0, 56), "Cleveland"),
    "DAL": ((0, 83, 188), "Dallas"), "DEN": ((13, 34, 64), "Denver"), "DET": ((200, 16, 46), "Detroit"),
    "GSW": ((29, 66, 138), "Golden State"), "HOU": ((206, 17, 65), "Houston"), "IND": ((0, 45, 98), "Indiana"),
    "LAC": ((200, 16, 46), "the Clippers"), "LAL": ((85, 37, 130), "the Lakers"), "MEM": ((93, 118, 169), "Memphis"),
    "MIA": ((152, 0, 46), "Miami"), "MIL": ((0, 71, 27), "Milwaukee"), "MIN": ((12, 35, 64), "Minnesota"),
    "NOP": ((0, 22, 65), "New Orleans"), "NYK": ((0, 107, 182), "New York"), "OKC": ((0, 125, 195), "Oklahoma City"),
    "ORL": ((0, 125, 197), "Orlando"), "PHI": ((0, 107, 182), "Philadelphia"), "PHX": ((229, 95, 32), "Phoenix"),
    "POR": ((224, 58, 62), "Portland"), "SAC": ((91, 43, 130), "Sacramento"), "SAS": ((110, 110, 120), "San Antonio"),
    "TOR": ((206, 17, 65), "Toronto"), "UTA": ((0, 43, 92), "Utah"), "WAS": ((0, 43, 92), "Washington"),
}


# NBA and NFL share abbreviations (MIA, DAL, CHI...) with different colours, so every team
# lookup goes through _tm() with the sport; SPORT is the default a format sets before building.
SPORT = "nba"
_NFL_TEAMS: dict = {}


def _rgb(h):
    h = (h or "").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else None


def _tm(abbr, sport=None):
    """(colour, spoken name) for a team. NFL from nflverse's team table, nickname spoken."""
    if (sport or SPORT) == "nfl":
        if not _NFL_TEAMS:
            import nfl_grades as ng
            for a, m in ng.team_meta().items():
                cols = [c for c in (_rgb(m.get("color")), _rgb(m.get("color2"))) if c]
                bright = [c for c in cols if 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 40]
                _NFL_TEAMS[a] = ((bright or cols or [ORANGE])[0], m.get("nick") or a)
        return _NFL_TEAMS.get(abbr, (ORANGE, abbr))
    return TEAM.get(abbr, (ORANGE, abbr))


# ── easing + drawing helpers ──────────────────────────────────────────────────────────────

def clamp(x):
    return max(0.0, min(1.0, x))


def prog(t, a, b):
    return clamp((t - a) / (b - a)) if b > a else float(t >= a)


def out_cubic(x):
    return 1 - (1 - x) ** 3


def out_back(x, s=1.9):
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


_FONTS: dict = {}
_LAYERS: dict = {}


def F(size, head=True):
    """head=True: the condensed display face (Impact on Windows; Anton/DejaVu fallback)."""
    k = (size, head)
    if k not in _FONTS:
        f = None
        if head:
            for name in ("impact.ttf", str(ROOT / "assets" / "fonts" / "Anton-Regular.ttf"),
                         "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"):
                try:
                    f = ImageFont.truetype(name, size)
                    break
                except Exception:
                    continue
        _FONTS[k] = f or _font(size, True)
    return _FONTS[k]


def layer(text, size, fill, head=True, stroke=0):
    """Text on a transparent sprite, cached, so it can be scaled and faded per frame."""
    k = (text, size, fill, head, stroke)
    if k not in _LAYERS:
        f = F(size, head)
        x0, y0, x1, y1 = f.getbbox(text, stroke_width=stroke)
        im = Image.new("RGBA", (x1 - x0 + 24, y1 - y0 + 24), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((12 - x0, 12 - y0), text, font=f, fill=fill,
                                stroke_width=stroke, stroke_fill=BLACK)
        _LAYERS[k] = im
    return _LAYERS[k]


def put(img, sprite, cx, cy, scale=1.0, alpha=1.0, angle=0.0):
    """Paste a sprite centred on (cx, cy) with scale, opacity and rotation."""
    if scale <= 0.02 or alpha <= 0.02:
        return
    s = sprite
    if abs(scale - 1) > 0.004:
        s = s.resize((max(1, int(s.width * scale)), max(1, int(s.height * scale))), Image.BILINEAR)
    if angle:
        s = s.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 0.995:
        s = s.copy()
        s.putalpha(s.getchannel("A").point(lambda v: int(v * alpha)))
    img.paste(s, (int(cx - s.width / 2), int(cy - s.height / 2)), s)


def _hex(w, h, inset=0):
    i = inset
    return [(h / 2 + i, i), (w - h / 2 - i, i), (w - i, h / 2), (w - h / 2 - i, h - i), (h / 2 + i, h - i), (i, h / 2)]


def hexplate(w, h, fill, edge=GOLD, text=None, size=0, fg=BLACK):
    """A broadcast hexagon plate: dark rim, coloured face, optional centred text."""
    k = ("hex", w, h, fill, edge, text, size, fg)
    if k not in _LAYERS:
        im = Image.new("RGBA", (w + 30, h + 30), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).polygon([(x + 15, y + 22) for x, y in _hex(w, h)], fill=(0, 0, 0, 170))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(8)))
        d = ImageDraw.Draw(im)
        d.polygon([(x + 15, y + 15) for x, y in _hex(w, h)], fill=edge + (255,))
        d.polygon([(x + 15, y + 15) for x, y in _hex(w, h, 9)], fill=fill + (255,))
        if text:
            f = F(size)
            x0, y0, x1, y1 = f.getbbox(text)
            d.text((15 + (w - (x1 - x0)) / 2 - x0, 15 + (h - (y1 - y0)) / 2 - y0), text, font=f, fill=fg)
        _LAYERS[k] = im
    return _LAYERS[k]


def glow(color, size=900):
    """A soft coloured halo sprite."""
    k = ("glow", color, size)
    if k not in _LAYERS:
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((size * 0.2, size * 0.2, size * 0.8, size * 0.8), fill=color + (150,))
        _LAYERS[k] = im.filter(ImageFilter.GaussianBlur(size * 0.12))
    return _LAYERS[k]


def band(w=W - 60, h=250):
    """The gold hexagon band a matchup sits on, with dark chevron wings."""
    k = ("band", w, h)
    if k not in _LAYERS:
        im = Image.new("RGBA", (w, h + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 40, w, h), fill=(16, 16, 18, 235))
        d.line((0, 32, w, 32), fill=(220, 220, 225, 255), width=6)
        d.line((0, h + 8, w, h + 8), fill=(220, 220, 225, 255), width=6)
        for x in range(-40, w, 90):                                   # chevron wings
            d.polygon([(x, 40), (x + 40, 40), (x + 80, (40 + h) / 2), (x + 40, h), (x, h), (x + 40, (40 + h) / 2)],
                      fill=(38, 38, 42, 255))
        pw = int(w * 0.62)
        px = (w - pw) // 2
        d.polygon([(px + x, 40 + y) for x, y in _hex(pw, h - 40)], fill=(255, 226, 90, 255))
        d.polygon([(px + x, 40 + y) for x, y in _hex(pw, h - 40, 10)], fill=GOLD + (255,))
        _LAYERS[k] = im
    return _LAYERS[k]


def split_tile(away, home, size=210, sport=None):
    """Diagonal split square in the two teams' colours: the matchup without anyone's logo."""
    sport = sport or SPORT
    k = ("split", away, home, size, sport)
    if k not in _LAYERS:
        ca, ch = _tm(away, sport)[0], _tm(home, sport)[0]
        im = Image.new("RGBA", (size + 20, size + 20), (0, 0, 0, 0))
        m = Image.new("L", (size, size), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=34, fill=255)
        face = Image.new("RGBA", (size, size), ca + (255,))
        ImageDraw.Draw(face).polygon([(size, 0), (size, size), (0, size)], fill=ch + (255,))
        fd = ImageDraw.Draw(face)
        fd.line((size, 0, 0, size), fill=WHITE, width=6)
        f = F(int(size * 0.3))
        fd.text((size * 0.1, size * 0.08), away, font=f, fill=WHITE, stroke_width=3, stroke_fill=BLACK)
        tw = fd.textlength(home, font=f)
        fd.text((size * 0.9 - tw, size * 0.56), home, font=f, fill=WHITE, stroke_width=3, stroke_fill=BLACK)
        ring = Image.new("RGBA", (size + 20, size + 20), (0, 0, 0, 0))
        ImageDraw.Draw(ring).rounded_rectangle((0, 0, size + 19, size + 19), radius=42, fill=(255, 236, 160, 255))
        im = Image.alpha_composite(im, ring)
        im.paste(face, (10, 10), m)
        _LAYERS[k] = im
    return _LAYERS[k]


# ── background, captions, frame ───────────────────────────────────────────────────────────

_BG = None


def background(t, shake=0.0):
    """Charcoal with slow diagonal stripes and a vignette. Built once, cropped per frame."""
    global _BG
    if _BG is None:
        big = Image.new("RGB", (W + 400, H + 400), CHAR)
        d = ImageDraw.Draw(big)
        for x in range(-H - 400, W + 400, 64):
            d.polygon([(x, 0), (x + 26, 0), (x + 26 + H + 400, H + 400), (x + H + 400, H + 400)], fill=(30, 30, 34))
        vig = Image.new("L", big.size, 0)
        ImageDraw.Draw(vig).ellipse((-300, -100, W + 700, H + 500), fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(260))
        _BG = Image.composite(big, Image.new("RGB", big.size, (8, 8, 10)), vig)
    ox = 200 + (t * 40) % 64 + shake * math.sin(t * 90)
    oy = 200 + shake * math.cos(t * 77)
    return _BG.crop((int(ox), int(oy), int(ox) + W, int(oy) + H))


def frame_chrome(img, d, title, frac):
    """Orange frame, the top banner with today's title, a gold progress line, the footer."""
    d.rounded_rectangle((14, 14, W - 14, H - 14), radius=44, outline=ORANGE, width=16)
    d.rounded_rectangle((52, 70, W - 52, 200), radius=26, fill=(12, 12, 14), outline=ORANGE, width=6)
    f = F(78)
    tw = d.textlength(title, font=f)
    d.text(((W - tw) / 2, 82), title, font=f, fill=ORANGE)
    d.rectangle((80, 186, 80 + int((W - 160) * frac), 194), fill=GOLD)
    d.text((70, H - 175), "KONJURE ANALYTICS", font=F(54), fill=ORANGE)
    d.text((72, H - 105), "21+  ·  Not betting advice  ·  1-800-GAMBLER", font=F(26, False), fill=MUTED)


def _chunks(words, max_chars=22):
    """Caption phrases. Word timings carry no punctuation, so a pause in the voice (> 0.2 s)
    stands in for a sentence break; otherwise a phrase holds ~22 characters."""
    out, cur = [], []
    for w in words:
        if cur and (len(" ".join(x[2] for x in cur + [w])) > max_chars or w[0] - cur[-1][1] > 0.2):
            out.append(cur)
            cur = []
        cur.append(w)
    return out + ([cur] if cur else [])


def captions(img, d, words, t, y=1262):
    """Karaoke captions: the current phrase, the spoken word in gold and a size up."""
    if not words:
        return
    groups = _chunks(words)
    cur = None
    for gi, g in enumerate(groups):
        end = groups[gi + 1][0][0] if gi + 1 < len(groups) else g[-1][1] + 0.6
        if g[0][0] - 0.05 <= t < end:
            cur = g
    if cur is None:
        return
    txt = [w[2].upper() for w in cur]
    size = 92
    while size > 56:                                   # shrink a long phrase to fit the frame
        if sum(d.textlength(x, font=F(int(size * 1.15))) for x in txt) + 20 * (len(txt) - 1) <= W - 100:
            break
        size -= 6
    f = F(size)
    widths = [d.textlength(x, font=F(int(size * 1.15))) for x in txt]
    gap = 20
    x = (W - sum(widths) - gap * (len(txt) - 1)) / 2
    for (s, e, _), word, wd in zip(cur, txt, widths):
        active = s <= t < max(e, s + 0.18)
        if active:
            pop = out_back(prog(t, s, s + 0.14))
            put(img, layer(word, int(size * 1.15), GOLD, stroke=9), x + wd / 2, y + size * 0.57, scale=0.85 + 0.15 * pop)
        else:
            d.text((x + (wd - d.textlength(word, font=f)) / 2, y), word, font=f,
                   fill=WHITE if t >= s else (200, 200, 205), stroke_width=9, stroke_fill=BLACK)
        x += wd + gap


# ── sound ─────────────────────────────────────────────────────────────────────────────────

def _env(n, attack=0.005, decay=0.2):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / decay)


def _hp(x):
    return np.diff(x, prepend=0.0)


def sfx(kind):
    rng = np.random.default_rng(11)
    if kind == "scratch":                                   # transition: a vinyl back-spin
        n = int(0.32 * SR)
        t = np.arange(n) / SR
        f = 700 + 900 * np.sin(2 * np.pi * 6.5 * t) * np.exp(-t * 3)
        tone = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * 0.35
        return 0.45 * (tone + 0.4 * _hp(rng.standard_normal(n))) * np.sin(np.pi * t / t[-1]) ** 0.6
    if kind == "thud":
        n = int(0.5 * SR)
        t = np.arange(n) / SR
        f = 42 + 110 * np.exp(-t * 20)
        return 1.0 * np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.14)
    if kind == "tick":
        n = int(0.05 * SR)
        t = np.arange(n) / SR
        return 0.3 * np.sin(2 * np.pi * 2100 * t) * _env(n, 0.001, 0.012)
    if kind == "win":
        n = int(1.0 * SR)
        t = np.arange(n) / SR
        a = sum(np.sin(2 * np.pi * f * t) for f in (784, 988, 1175)) / 3 * _env(n, 0.003, 0.35)
        b = np.zeros(n)
        k = int(0.1 * SR)
        b[k:] = (sum(np.sin(2 * np.pi * f * t[:n - k]) for f in (1047, 1319, 1568)) / 3) * _env(n - k, 0.003, 0.45)
        return 0.55 * (a + b)
    if kind == "loss":
        n = int(0.8 * SR)
        t = np.arange(n) / SR
        f = 200 * np.exp(-t * 1.3)
        ph = 2 * np.pi * np.cumsum(f) / SR
        return 0.32 * (0.5 * np.sign(np.sin(ph)) + 0.5 * np.sin(ph)) * _env(n, 0.004, 0.25)
    if kind == "pop":
        n = int(0.12 * SR)
        t = np.arange(n) / SR
        return 0.45 * np.sin(2 * np.pi * (500 + 900 * np.exp(-t * 40)) * t) * _env(n, 0.001, 0.04)
    return np.zeros(1)


def _drum(kind, rng):
    if kind == "kick":
        n = int(0.42 * SR)
        t = np.arange(n) / SR
        f = 48 + 120 * np.exp(-t * 28)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.001, 0.2)
        click = _hp(rng.standard_normal(n)) * _env(n, 0.0005, 0.004) * 0.4
        return np.tanh(1.6 * (body + click))
    if kind == "snare":
        n = int(0.35 * SR)
        t = np.arange(n) / SR
        tone = np.sin(2 * np.pi * 190 * t) * _env(n, 0.001, 0.06)
        noise = _hp(rng.standard_normal(n)) * _env(n, 0.001, 0.14)
        s = 0.55 * tone + 0.5 * noise
        echo = np.zeros(n)
        k = int(0.09 * SR)
        echo[k:] = s[:n - k] * 0.25                                  # a little room
        return s + echo
    n = int((0.2 if kind == "ohat" else 0.035) * SR)
    x = _hp(_hp(rng.standard_normal(n)))
    return 0.22 * x * _env(n, 0.0005, 0.09 if kind == "ohat" else 0.012)


def _music_file():
    p = os.environ.get("KONJURE_MUSIC")
    if p and pathlib.Path(p).exists():
        return pathlib.Path(p)
    found = sorted((ROOT / "assets" / "music").glob("*.mp3")) if (ROOT / "assets" / "music").exists() else []
    return found[0] if found else None


def music(seconds):
    """The bed under the voice. A licensed track if one is provided, else a boom-bap loop:
    88 bpm, swung hats, kick-snare, sub bass and a minor-seventh electric-piano progression."""
    n = int(seconds * SR)
    track = _music_file()
    if track:
        pcm = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", str(track), "-ac", "1", "-ar", str(SR),
                              "-f", "s16le", "pipe:1"], capture_output=True, check=True).stdout
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        out = np.tile(a, n // max(1, len(a)) + 1)[:n]
    else:
        rng = np.random.default_rng(5)
        out = np.zeros(n + SR)
        beat = 60 / 88
        step = beat / 4
        swing = 0.16 * step
        kick, snare, hat, ohat = (_drum(k, rng) for k in ("kick", "snare", "hat", "ohat"))
        chords = [(220.0, 261.6, 329.6, 392.0), (146.8, 174.6, 220.0, 261.6), (174.6, 220.0, 261.6, 329.6),
                  (164.8, 207.7, 246.9, 293.7)]
        roots = [55.0, 73.42, 87.31, 82.41]
        kicks = [(0, 7, 10), (0, 3, 8, 10)]

        def add(sig, at, g=1.0):
            a = int(at * SR)
            if a < len(out):
                m = min(len(sig), len(out) - a)
                out[a:a + m] += sig[:m] * g

        bar = 16 * step
        for b in range(int(seconds / bar) + 1):
            t0 = b * bar
            ch, root = chords[b % 4], roots[b % 4]
            for s in kicks[b % 2]:
                add(kick, t0 + s * step, 0.9)
            for s in (4, 12):
                add(snare, t0 + s * step, 0.75)
            for s in range(0, 16, 2):
                add(ohat if s == 14 else hat, t0 + s * step + (swing if s % 4 == 2 else 0),
                    0.9 if s % 4 == 0 else 0.6)
            # sub bass on the kicks, gliding off the root
            for s in kicks[b % 2]:
                ln = int(0.55 * SR)
                tt = np.arange(ln) / SR
                add(np.sin(2 * np.pi * root * tt) * _env(ln, 0.005, 0.35) + 0.25 * np.sin(4 * np.pi * root * tt) *
                    _env(ln, 0.005, 0.15), t0 + s * step, 0.55)
            # electric piano: chord on 1 and on the and-of-3, with tremolo
            for s, g in ((0, 0.24), (10, 0.16)):
                ln = int(1.8 * SR)
                tt = np.arange(ln) / SR
                ep = sum(np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(4 * np.pi * f * tt) * np.exp(-tt * 4)
                         for f in ch) / len(ch)
                add(ep * _env(ln, 0.01, 0.9) * (1 + 0.15 * np.sin(2 * np.pi * 5 * tt)), t0 + s * step, g)
        crackle = np.zeros(len(out))
        idx = rng.integers(0, len(out), size=int(seconds * 18))
        crackle[idx] = rng.uniform(-0.12, 0.12, size=len(idx))
        out = (out + crackle)[:n]
    fade = int(1.2 * SR)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out / max(1e-6, np.abs(out).max())


# ── voice ─────────────────────────────────────────────────────────────────────────────────

def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# The owner's cloned voice, from ElevenLabs, whenever ~/.konjure has both its API key and the
# voice id; otherwise the free preview voice. Never a mix inside one Short: an ElevenLabs
# failure fails the build rather than falling back mid-video.
_KDIR = pathlib.Path.home() / ".konjure"


def _eleven():
    k, v = _KDIR / "elevenlabs.key", _KDIR / "elevenlabs_voice_id"
    if k.exists() and v.exists():
        key, vid = k.read_text().strip(), v.read_text().strip()
        if key and vid:
            return key, vid
    return None


def _eleven_tts(text: str, key: str, vid: str) -> tuple[bytes, list]:
    """mp3 bytes and word timings, from ElevenLabs' character alignment."""
    import base64
    import requests
    for attempt in range(4):
        r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps",
                          params={"output_format": "mp3_44100_128"},
                          headers={"xi-api-key": key, "Content-Type": "application/json"},
                          json={"text": text, "model_id": "eleven_multilingual_v2",
                                "voice_settings": {"stability": 0.45, "similarity_boost": 0.85,
                                                   "style": 0.3, "use_speaker_boost": True}},
                          timeout=90)
        if r.status_code == 200:
            break
        if r.status_code in (401, 403, 404, 422) or attempt == 3:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
        time.sleep(3 + 3 * attempt)
    j = r.json()
    al = j.get("alignment") or j.get("normalized_alignment") or {}
    chars, st, en = al.get("characters", []), al.get("character_start_times_seconds", []), \
        al.get("character_end_times_seconds", [])
    words, cur, s0, e0 = [], "", None, None
    for c, a, b in zip(chars, st, en):
        if c.isspace():
            if cur:
                words.append((s0, e0, cur.strip(".,!?;:\"'")))
            cur, s0 = "", None
            continue
        if s0 is None:
            s0 = a
        cur, e0 = cur + c, b
    if cur:
        words.append((s0, e0, cur.strip(".,!?;:\"'")))
    return base64.b64decode(j["audio_base64"]), [w for w in words if w[2]]


def clone_voice(recording: str, name: str = "Konjure Desk") -> str:
    """Create an ElevenLabs instant voice clone from the owner's recording; saves its voice id."""
    import requests
    key = (_KDIR / "elevenlabs.key").read_text().strip()
    with open(recording, "rb") as f:
        r = requests.post("https://api.elevenlabs.io/v1/voices/add", headers={"xi-api-key": key},
                          data={"name": name, "description": "Konjure Analytics narration (owner's own voice)"},
                          files={"files": (pathlib.Path(recording).name, f)}, timeout=180)
    r.raise_for_status()
    vid = r.json()["voice_id"]
    (_KDIR / "elevenlabs_voice_id").write_text(vid)
    return vid


def _tts(text: str) -> tuple[np.ndarray, list]:
    """(mono float audio at SR, [(start_s, end_s, word)]). Cached on disk; the free service
    drops requests in bursts, so it backs off rather than hammering."""
    import edge_tts
    import hashlib
    import json

    el = _eleven()
    if el:
        cache = _KDIR / "tts_cache"
        cache.mkdir(parents=True, exist_ok=True)
        ck = hashlib.sha1(f"eleven|{el[1]}|{text}".encode()).hexdigest()[:20]
        mp3_p, words_p = cache / f"{ck}.mp3", cache / f"{ck}.json"
        if mp3_p.exists() and words_p.exists():
            mp3, words = mp3_p.read_bytes(), [tuple(w) for w in json.loads(words_p.read_text())]
        else:
            mp3, words = _eleven_tts(text, *el)
            mp3_p.write_bytes(mp3)
            words_p.write_text(json.dumps(words))
        pcm = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR),
                              "-f", "s16le", "pipe:1"], input=mp3, capture_output=True, check=True).stdout
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0, words

    async def go(text=text):
        c = edge_tts.Communicate(text, VOICE, rate=_RATE, boundary="WordBoundary")
        audio, words = bytearray(), []
        async for ch in c.stream():
            if ch["type"] == "audio":
                audio += ch["data"]
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append((s, s + ch["duration"] / 1e7, ch["text"]))
        return bytes(audio), words

    cache = pathlib.Path.home() / ".konjure" / "tts_cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{VOICE}|{_RATE}|{text}".encode()).hexdigest()[:20]
    mp3_p, words_p = cache / f"{key}.mp3", cache / f"{key}.json"
    if mp3_p.exists() and words_p.exists():
        mp3, words = mp3_p.read_bytes(), [tuple(w) for w in json.loads(words_p.read_text())]
    else:
        mp3 = None
        # The free service refuses some lines for minutes at a time and accepts a reworded or
        # split version at once, so: retry, then the line without commas, then sentence by
        # sentence with the pieces' audio and word timings stitched back together.
        for attempt, variant in enumerate([text] * 3 + [text.replace(",", "")] * 2):
            try:
                mp3, words = asyncio.run(go(variant))
                break
            except Exception:
                time.sleep(2 + 2 * attempt)
        if mp3 is None:
            import re
            parts, words, off = [], [], 0.0
            for sent in [x for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]:
                for attempt in range(4):
                    try:
                        a, w = asyncio.run(go(sent))
                        break
                    except Exception:
                        if attempt == 3:
                            raise
                        time.sleep(3 + 3 * attempt)
                pcm_s = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR),
                                        "-f", "s16le", "pipe:1"], input=a, capture_output=True, check=True).stdout
                parts.append(pcm_s)
                words += [(s0 + off, e0 + off, wd) for s0, e0, wd in w]
                off += len(pcm_s) / 2 / SR
            wav = io.BytesIO()
            with wave.open(wav, "wb") as wv:
                wv.setnchannels(1)
                wv.setsampwidth(2)
                wv.setframerate(SR)
                wv.writeframes(b"".join(parts))
            mp3 = wav.getvalue()                       # a WAV; ffmpeg below decodes either
        mp3_p.write_bytes(mp3)
        words_p.write_text(json.dumps(words))
    pcm = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "pipe:1"], input=mp3, capture_output=True, check=True).stdout
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0, words



# ── the host: "The Konjurer" (placeholder art, drawn in code) ─────────────────────────────
# A cartoon sports wizard with a basketball crystal ball. Mouth opens with the narration's
# loudness, eyes blink, he bobs to the beat, and each scene scripts his reactions: point at
# the graphic, celebrate a win, face-palm a loss, shrug at break-even. Real artwork (a set of
# poses from an illustrator) can replace host_sprite() without touching the scenes.
HOST = True
_PURPLE, _PURPLE_D, _SKIN = (98, 52, 186), (60, 28, 128), (243, 199, 152)
_HW, _HH = 460, 630          # 60 px of headroom for the hat and the jump
# (gesture hand, ball hand, jump, head tilt) in sprite coordinates
_POSES = {
    "talk":      ((96, 400), (362, 420), 0.0, 0),
    "point":     ((24, 140), (362, 420), 0.0, -5),
    "celebrate": ((84, 96), (376, 96), 1.0, 0),
    "facepalm":  ((206, 238), (360, 440), 0.0, 9),
    "shrug":     ((60, 318), (400, 318), 0.0, -7),
}


def _star(d, x, y, r, fill):
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def _arm(d, shoulder, hand):
    d.line([shoulder, hand], fill=_PURPLE_D + (255,), width=36)
    d.line([shoulder, hand], fill=_PURPLE + (255,), width=26)
    x, y = hand
    d.ellipse((x - 20, y - 20, x + 20, y + 20), fill=_SKIN + (255,), outline=(130, 86, 54, 255), width=3)


def _ball(d, x, y, t, r=38):
    g = 10 + 5 * math.sin(t * 5)
    d.ellipse((x - r - g, y - r - g, x + r + g, y + r + g), fill=(255, 214, 110, 90))
    d.ellipse((x - r, y - r, x + r, y + r), fill=(238, 118, 30, 255), outline=(60, 25, 5, 255), width=4)
    d.line((x - r, y, x + r, y), fill=(60, 25, 5, 255), width=3)
    d.line((x, y - r, x, y + r), fill=(60, 25, 5, 255), width=3)
    d.arc((x - r * 1.6, y - r, x - r * 0.35, y + r), -60, 60, fill=(60, 25, 5, 255), width=3)
    d.arc((x + r * 0.35, y - r, x + r * 1.6, y + r), 120, 240, fill=(60, 25, 5, 255), width=3)
    d.ellipse((x - r * 0.55, y - r * 0.7, x - r * 0.2, y - r * 0.35), fill=(255, 230, 190, 160))


def host_sprite(t, mood, prev, since, mouth):
    u = out_cubic(clamp(since / 0.22))
    pa, pb = _POSES.get(prev, _POSES["talk"]), _POSES.get(mood, _POSES["talk"])
    lerp = lambda a, b: a + (b - a) * u
    ga = (lerp(pa[0][0], pb[0][0]), lerp(pa[0][1], pb[0][1]))
    bh = (lerp(pa[1][0], pb[1][0]), lerp(pa[1][1], pb[1][1]))
    tilt = lerp(pa[3], pb[3])
    if mood == "talk":                                   # talk with the free hand
        ga = (ga[0] + 14 * math.sin(t * 5.1), ga[1] + 18 * math.sin(t * 3.3))
    jump = (abs(math.sin(t * 7.5)) * 36 * pb[2] * u) if mood == "celebrate" else 0.0
    oy = 60 - jump
    im = Image.new("RGBA", (_HW, _HH), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((120, 598, 340, 622), fill=(0, 0, 0, 120))
    # robe
    d.polygon([(158, 300 + oy), (302, 300 + oy), (370, 532 + oy), (90, 532 + oy)], fill=_PURPLE + (255,))
    d.polygon([(158, 300 + oy), (230, 300 + oy), (200, 532 + oy), (90, 532 + oy)], fill=(86, 44, 168, 255))
    d.line([(90, 530 + oy), (370, 530 + oy)], fill=GOLD + (255,), width=12)
    d.line([(230, 306 + oy), (230, 526 + oy)], fill=GOLD + (255,), width=5)
    _star(d, 168, 450 + oy, 15, GOLD + (255,))
    _star(d, 296, 410 + oy, 11, GOLD + (255,))
    _star(d, 262, 488 + oy, 9, (255, 236, 160, 255))
    # ball arm, then the ball
    _arm(d, (300, 336 + oy), (bh[0], bh[1] + oy))
    _ball(d, bh[0], bh[1] + oy - 44, t)
    # head
    hx, hy = 230 + tilt, 228 + oy + abs(tilt) * 0.8
    d.ellipse((hx - 82, hy - 80, hx + 82, hy + 84), fill=_SKIN + (255,), outline=(150, 100, 64, 255), width=4)
    d.ellipse((hx - 66, hy + 18, hx - 38, hy + 38), fill=(240, 150, 140, 150))
    d.ellipse((hx + 38, hy + 18, hx + 66, hy + 38), fill=(240, 150, 140, 150))
    # beard: a short white goatee under the mouth
    d.polygon([(hx - 34, hy + 58), (hx + 34, hy + 58), (hx, hy + 112)], fill=(245, 245, 250, 255))
    # eyes
    blink = (t % 3.7) < 0.12
    closed = blink or mood == "facepalm"
    for ex in (hx - 30, hx + 30):
        if closed:
            d.arc((ex - 16, hy - 14, ex + 16, hy + 12), 200, 340, fill=(40, 20, 20, 255), width=5)
        else:
            d.ellipse((ex - 17, hy - 22, ex + 17, hy + 18), fill=(255, 255, 255, 255), outline=(60, 40, 40, 255), width=3)
            px, py = (ex - 5, hy - 8) if mood in ("point", "talk") else (ex, hy - 2)
            d.ellipse((px - 9, py - 9, px + 9, py + 9), fill=(25, 20, 30, 255))
            d.ellipse((px - 4, py - 6, px, py - 2), fill=(255, 255, 255, 255))
    # brows
    lift = {"celebrate": -14, "shrug": -10}.get(mood, 0) * u
    for k, ex in enumerate((hx - 30, hx + 30)):
        if mood == "facepalm":
            a, b = ((ex - 18, hy - 30), (ex + 14, hy - 40)) if k == 0 else ((ex - 14, hy - 40), (ex + 18, hy - 30))
        else:
            a, b = (ex - 18, hy - 36 + lift), (ex + 18, hy - 38 + lift)
        d.line([a, b], fill=(70, 45, 30, 255), width=7)
    # mouth: smile at rest, open with the voice, a grin when celebrating
    mx, my = hx, hy + 44
    if mood == "celebrate":
        d.chord((mx - 34, my - 22, mx + 34, my + 30), 0, 180, fill=(90, 20, 34, 255))
        d.chord((mx - 20, my + 6, mx + 20, my + 28), 0, 180, fill=(225, 96, 110, 255))
    elif mouth > 0.12:
        h = 8 + 30 * mouth
        d.ellipse((mx - 18 - 8 * mouth, my - h / 2, mx + 18 + 8 * mouth, my + h / 2), fill=(90, 20, 34, 255))
        d.ellipse((mx - 10, my + h / 2 - 9, mx + 10, my + h / 2 - 1), fill=(225, 96, 110, 255))
    elif mood == "facepalm":
        d.arc((mx - 18, my + 2, mx + 18, my + 22), 200, 340, fill=(90, 20, 34, 255), width=5)
    else:
        d.arc((mx - 22, my - 16, mx + 22, my + 12), 20, 160, fill=(90, 20, 34, 255), width=6)
    # hat
    sway = 7 * math.sin(t * 2.1)
    d.ellipse((hx - 108, hy - 70, hx + 108, hy - 34), fill=_PURPLE_D + (255,))
    d.polygon([(hx - 78, hy - 54), (hx + 78, hy - 54), (hx + 30 + sway, hy - 238)], fill=_PURPLE + (255,))
    d.polygon([(hx - 78, hy - 54), (hx + 2, hy - 54), (hx + 30 + sway, hy - 238)], fill=(86, 44, 168, 255))
    d.polygon([(hx - 76, hy - 58), (hx + 76, hy - 58), (hx + 66, hy - 84), (hx - 66, hy - 84)], fill=GOLD + (255,))
    _star(d, hx - 6, hy - 128, 16, GOLD + (255,))
    _star(d, hx + 26, hy - 178, 10, (255, 236, 160, 255))
    _star(d, hx + 30 + sway, hy - 240, 13, GOLD + (255,))
    # the gesture arm goes last so a face-palm covers the face
    _arm(d, (160, 336 + oy), (ga[0], ga[1] + oy))
    if mood == "celebrate":                              # sparkles
        for k in range(7):
            a = t * 3 + k * 0.9
            sx, sy = 230 + 190 * math.cos(a), 200 + 140 * math.sin(a * 1.3)
            if (int(t * 10) + k) % 3:
                _star(d, sx, sy + oy, 9, (255, 236, 160, 255))
    return im



# ── the anchor cam: the user's own avatar image as the host ───────────────────────────────
# A photoreal still is not animated in code: warping a real-looking face reads as uncanny.
# It plays as a picture-in-picture desk feed instead: a slow camera drift, a LIVE tag and a
# voice meter driven by the narration, the frame lighting green/red/gold with the moment,
# and a reaction sticker. The image lives outside the repo (~/.konjure/host/anchor.png, or
# KONJURE_HOST_IMAGE); without it the cartoon Konjurer is used. Crop box is in source pixels.
_ANCHOR_CROP = (250, 400, 1560, 1710)
_STICKER = {"celebrate": ("LET'S GO", GREEN), "facepalm": ("OUCH", RED), "shrug": ("HMM", GOLD)}


def _anchor_path():
    p = os.environ.get("KONJURE_HOST_IMAGE")
    p = pathlib.Path(p) if p else pathlib.Path.home() / ".konjure" / "host" / "anchor.png"
    return p if p.exists() else None


# A short animated clip of the same desk shot (~/.konjure/host/anchor.mp4, no speech in it)
# makes the host move: the corner cam loops it, and every Short opens on it full screen for a
# beat before the narration starts. It is never shown full screen under the voice: a closed
# mouth over narration is what would look fake. Crop box is the still's, scaled to the clip.
def _anchor_clip():
    p = pathlib.Path.home() / ".konjure" / "host" / "anchor.mp4"
    return p if p.exists() else None


def _clip_frames(kind: str):
    """The clip decoded once at 30 fps: 'cam' = the head-and-shoulders square for the corner,
    'full' = the opening, 1080x1920. Returned as a list of RGB images."""
    k = ("clip", kind)
    if k in _LAYERS:
        return _LAYERS[k]
    src = _anchor_clip()
    probe = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True).stderr
    import re
    m = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", probe)
    cw, ch = (int(m.group(1)), int(m.group(2))) if m else (1080, 1446)
    if kind == "cam":
        sx = cw / 1760.0                                       # the still is 1760 px wide
        x0, y0, x1, y1 = (int(v * sx) for v in _ANCHOR_CROP)
        size = 340
        vf = f"fps=30,crop={x1 - x0}:{y1 - y0}:{x0}:{y0},scale={size}:{size}"
        w_, h_ = size, size
        extra = []
    else:
        vf = f"fps=30,scale=-2:{H},crop={W}:{H}"
        w_, h_ = W, H
        extra = ["-t", "1.8"]
    raw = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", str(src), *extra, "-vf", vf, "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True).stdout
    n = len(raw) // (w_ * h_ * 3)
    frames = [Image.frombuffer("RGB", (w_, h_), raw[i * w_ * h_ * 3:(i + 1) * w_ * h_ * 3]) for i in range(n)]
    _LAYERS[k] = frames
    return frames


def _pingpong(frames, t):
    n = len(frames)
    if n < 2:
        return frames[0]
    i = int(t * FPS) % (2 * n - 2)
    return frames[i if i < n else 2 * n - 2 - i]


def anchor_sprite(t, mood, prev, since, mouth, size=340):
    if _anchor_clip():
        face = _pingpong(_clip_frames("cam"), t)
    else:
        k = ("anchor_src", size)
        if k not in _LAYERS:
            im = Image.open(_anchor_path()).convert("RGB").crop(_ANCHOR_CROP)
            _LAYERS[k] = im.resize((size + 40, size + 40), Image.LANCZOS)
        src = _LAYERS[k]
        ox = 20 + 14 * math.sin(t * 0.31)                    # slow camera drift on the still
        oy = 20 + 12 * math.cos(t * 0.23)
        face = src.crop((int(ox), int(oy), int(ox) + size, int(oy) + size))
    col = {"celebrate": GREEN, "facepalm": RED, "shrug": GOLD}.get(mood, ORANGE)
    pulse = 0.5 + 0.5 * math.sin(t * 9) if mood == "celebrate" else 1.0
    strip, top = 64, 84                                      # nameplate height; headroom for the sticker
    out = Image.new("RGBA", (size + 40, top + size + strip + 40), (0, 0, 0, 0))
    frame = Image.new("RGBA", (size + 40, size + strip + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(frame)
    if mood in _STICKER:                                     # glow behind the frame
        g = glow(col, size + 160).resize(frame.size)
        frame.paste(g, (0, 0), g)
    d.rounded_rectangle((8, 8, size + 31, size + strip + 31), radius=34,
                        fill=(12, 12, 14, 255), outline=tuple(int(c * (0.6 + 0.4 * pulse)) for c in col) + (255,),
                        width=9)
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=26, fill=255)
    frame.paste(face, (20, 20), m)
    # nameplate strip: LIVE tag, name, voice meter
    y0 = size + 24
    if int(t * 2) % 2 == 0:
        d.ellipse((30, y0 + 20, 48, y0 + 38), fill=(235, 40, 40, 255))
    d.text((56, y0 + 12), "LIVE", font=F(30), fill=(235, 40, 40))
    d.text((118, y0 + 12), "KONJURE DESK", font=F(30), fill=GOLD)
    for b in range(5):
        h = 6 + 34 * mouth * (0.55 + 0.45 * abs(math.sin(t * 11 + b * 1.7)))
        x = size - 52 + b * 13
        d.rectangle((x, y0 + 46 - h, x + 9, y0 + 46), fill=ORANGE + (255,))
    out.paste(frame, (0, top), frame)
    if mood in _STICKER and since < 1.6:                     # sticker above the frame, off the face
        txt, c = _STICKER[mood]
        p = out_back(prog(since, 0.0, 0.25))
        st = hexplate(250, 92, c, WHITE, txt, 58, WHITE)
        put(out, st, size / 2 + 20, top - 30, scale=p, alpha=min(1.0, p * 2) * (1 - prog(since, 1.3, 1.6)), angle=-6)
    return out



# ── the talking desk: lip-synced clips of the anchor, made once and reused ───────────────
# ~/.konjure/host/desk/<name>.mp4 are OmniHuman clips of the desk avatar speaking a fixed line
# in the owner's cloned voice (generated in Picsart, one per format plus a sign-off), and
# desk.json holds each line's text so the captions can use its word timings. A Short whose
# format has one opens on it full screen, and every Short ends on the sign-off. Formats
# without a clip keep the silent desk open. The clip's own audio is the narration.
_DESK = pathlib.Path.home() / ".konjure" / "host" / "desk"
DESK_OPEN = {"results": "open-results", "top5": "open-top5", "gtg": "open-gtg", "btm": "open-btm",
             "picks": "open-picks", "nfl-picks": "open-nfl-picks",
             "nfl-td": "open-nfl-td", "nfl-results": "open-nfl-results", "nfl-top5": "open-nfl-top5"}


def _desk_clip(name):
    p = _DESK / f"{name}.mp4"
    return p if p.exists() else None


class _ClipReader:
    """Frames of a clip at 30 fps, 1080x1920, read in order as the render asks for them."""

    def __init__(self, path):
        self.path, self.proc, self.i, self.last = path, None, -1, None

    def frame(self, k):
        if self.proc is None:
            self.proc = subprocess.Popen([_ffmpeg(), "-loglevel", "error", "-i", str(self.path), "-vf",
                                          f"fps={FPS},scale=-2:{H},crop={W}:{H}", "-f", "rawvideo",
                                          "-pix_fmt", "rgb24", "pipe:1"], stdout=subprocess.PIPE)
        while self.i < k:
            raw = self.proc.stdout.read(W * H * 3)
            if len(raw) < W * H * 3:
                break                                   # past the end: hold the last frame
            self.last, self.i = Image.frombuffer("RGB", (W, H), raw), self.i + 1
        return self.last.copy() if self.last is not None else Image.new("RGB", (W, H), CHAR)


def _talk_scene(name, kicker, sub):
    import json
    path = _desk_clip(name)
    meta = json.loads((_DESK / "desk.json").read_text(encoding="utf-8")).get(name, {}) \
        if (_DESK / "desk.json").exists() else {}
    reader = _ClipReader(path)

    def draw(img, d, t, sc):
        img.paste(reader.frame(int(t * FPS)), (0, 0))
        p = out_cubic(prog(t, 0.15, 0.45))
        # lower third below the captions (which sit at ~1262-1360)
        put(img, hexplate(760, 110, (14, 14, 16), ORANGE, kicker, 76 if len(kicker) < 16 else 60, ORANGE),
            440 - (1 - p) * 900, 1500)
        if sub:
            put(img, layer(sub, 46, WHITE, stroke=5), 440 - (1 - prog(t, 0.3, 0.6)) * 900, 1590)
        if int(t * 2) % 2 == 0:
            d.ellipse((96, 1648, 120, 1672), fill=(235, 40, 40))
        put(img, layer("LIVE", 36, (235, 40, 40), stroke=3), 162, 1660, alpha=prog(t, 0.3, 0.5))

    sc = Scene("", draw, min_dur=0.1, sounds=lambda sc: [(0.0, "thud", 0.6)])
    sc.no_host = True

    def voice():
        pcm = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                              "-f", "s16le", "pipe:1"], capture_output=True, check=True).stdout
        sc.audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        sc.dur = len(sc.audio) / SR + 0.15
        sc.mouth = np.zeros(1)
        sc.words = []
        if meta.get("text"):                           # captions from the line's own word timings
            _, w = _tts(meta["text"])
            off = float(meta.get("pad", 0.25))
            sc.words = [(a + off, b + off, x) for a, b, x in w]

    sc.voice = voice
    return sc

# ── scenes + render ───────────────────────────────────────────────────────────────────────

class Scene:
    """narration + draw(img, d, t, sc). After voicing: .audio, .words, .dur, and .cue(word)
    for the time a word is spoken, so animation can land on it."""

    def __init__(self, narration, draw, min_dur=0.0, tail=0.45, sounds=None, shake_at=None, moods=None, tag=""):
        self.tag = tag
        self.narration, self.draw, self.min_dur, self.tail = narration, draw, min_dur, tail
        self.sounds = sounds or (lambda sc: [])
        self.shake_at = shake_at
        self.moods = moods or (lambda sc: [(0.0, "talk")])     # the host's script for this scene

    def voice(self):
        if not self.narration:                                   # a silent beat, e.g. the desk open
            self.audio, self.words = np.zeros(int(self.min_dur * SR), dtype=np.float32), []
            self.dur, self.mouth = self.min_dur, np.zeros(1)
            return
        self.audio, self.words = _tts(self.narration)
        self.dur = max(self.min_dur, len(self.audio) / SR + self.tail)
        hop = SR // FPS                                          # loudness per frame drives the mouth
        n = len(self.audio) // hop
        rms = np.sqrt((self.audio[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) if n else np.zeros(1)
        self.mouth = np.clip(rms / max(1e-4, np.percentile(rms, 95)), 0, 1)

    def after(self, word, default=0.6):
        """When the word after `word` starts (e.g. the letter after "Grade")."""
        for i, w in enumerate(self.words):
            if w[2].strip(".,:").lower() == word.lower() and i + 1 < len(self.words):
                return self.words[i + 1][0]
        return self.dur * default

    def host_state(self, t):
        ms = sorted(self.moods(self))
        cur, prev, since = ms[0][1], ms[0][1], t
        for i, (at, m) in enumerate(ms):
            if t >= at:
                cur, prev, since = m, (ms[i - 1][1] if i else m), t - at
        k = int(t * FPS)
        mouth = float(self.mouth[k]) if k < len(self.mouth) else 0.0
        return cur, prev, since, mouth

    def cue(self, word, default=0.6, last=True):
        # "That's" counts as "That": ElevenLabs keeps contractions as one word
        hits = [w[0] for w in self.words
                if word.lower() in (w[2].strip(".,").lower(), w[2].strip(".,").lower().split("'")[0])]
        return (hits[-1] if last else hits[0]) if hits else self.dur * default

    def shake(self, t):
        if self.shake_at is None:
            return 0.0
        at = self.shake_at(self)
        return 14 * math.exp(-(t - at) * 9) if t >= at else 0.0


def _desk_open(title: str):
    """1.7 s of the anchor at the desk, full screen, under a lower third and a sting."""
    def draw(img, d, t, sc):
        img.paste(_pingpong(_clip_frames("full"), t), (0, 0))
        p = out_cubic(prog(t, 0.15, 0.45))
        put(img, hexplate(760, 120, (14, 14, 16), ORANGE, "KONJURE DESK", 82, ORANGE), 60 + 380 - (1 - p) * 900, 1380)
        put(img, layer(title, 52, WHITE, stroke=5), 60 + 380 - (1 - prog(t, 0.3, 0.6)) * 900, 1478)
        if int(t * 2) % 2 == 0:
            d.ellipse((96, 1556, 124, 1584), fill=(235, 40, 40))
        put(img, layer("LIVE", 40, (235, 40, 40), stroke=3), 170, 1570, alpha=prog(t, 0.3, 0.5))

    sc = Scene("", draw, min_dur=1.7, sounds=lambda sc: [(0.0, "thud", 0.7), (0.12, "scratch", 0.5)])
    sc.no_host = True
    return sc


def render(scenes: list, title: str, out: pathlib.Path, fmt: str | None = None) -> pathlib.Path:
    scenes = list(scenes)
    if HOST and fmt and _desk_clip(DESK_OPEN.get(fmt, "")):
        scenes = [_talk_scene(DESK_OPEN[fmt], "KONJURE DESK", title)] + scenes
    elif HOST and _anchor_clip():
        scenes = [_desk_open(title)] + scenes
    if HOST and _desk_clip("signoff"):
        if getattr(scenes[-1], "tag", "") == "cta_results":
            scenes = scenes[:-1]                      # the sign-off says the same thing, on camera
        scenes.append(_talk_scene("signoff", "KONJUREANALYTICS.COM", "21+  ·  NOT BETTING ADVICE"))
    for sc in scenes:
        sc.voice()
    starts, t0 = [], 0.0
    for sc in scenes:
        starts.append(t0)
        t0 += sc.dur
    total = t0

    # narration, effects, and the beat ducked under the voice
    n = int(total * SR)
    voice, fx = np.zeros(n + SR), np.zeros(n + SR)
    for sc, s in zip(scenes, starts):
        a = int(s * SR)
        voice[a:a + len(sc.audio)] += sc.audio
        for at, kind, vol in ([(0.0, "scratch", 0.55)] if s > 0 else []) + sc.sounds(sc):
            x = sfx(kind) * vol
            b = int((s + at) * SR)
            m = min(len(x), len(fx) - b)
            if m > 0:
                fx[b:b + m] += x[:m]
    win = int(0.12 * SR)
    env = np.convolve(np.abs(voice), np.ones(win) / win, mode="same")
    duck = 1 - 0.5 * np.clip(env * 14, 0, 1)
    bed = np.zeros(n + SR)
    bed[:n] = music(total)
    mix = (voice * 1.0 + fx * 0.8 + bed * 0.34 * duck)[:n]
    mix = np.clip(mix / max(1.0, np.abs(mix).max() / 0.97), -1, 1)

    ff = _ffmpeg()
    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "mix.wav"
        with wave.open(str(wav), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(2)
            wv.setframerate(SR)
            wv.writeframes((mix * 32767).astype(np.int16).tobytes())
        proc = subprocess.Popen([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0", "-i", str(wav),
                                 "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                                 "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                                 str(out)], stdin=subprocess.PIPE)
        try:
            _frames(scenes, starts, total, title, proc)
        except BaseException:
            proc.kill()                        # release the temp files before re-raising
            proc.wait()
            raise
        proc.stdin.close()
        proc.wait()
    return out


def _frames(scenes, starts, total, title, proc):
    k = 0
    for i in range(int(total * FPS)):
        tg = i / FPS
        while k + 1 < len(scenes) and tg >= starts[k + 1]:
            k += 1
        sc, t = scenes[k], tg - starts[k]
        img = background(tg, sc.shake(t))
        d = ImageDraw.Draw(img)
        sc.draw(img, d, t, sc)
        if HOST and not getattr(sc, "no_host", False):
            mood, prev, since, mouth = sc.host_state(t)
            if _anchor_path():
                put(img, anchor_sprite(tg, mood, prev, since, mouth), W - 225, 1583)
            else:
                spr = host_sprite(tg, mood, prev, since, mouth)
                put(img, spr, W - 225, 1560 + 5 * math.sin(2 * math.pi * tg * 88 / 60), scale=0.8)
        captions(img, d, sc.words, t)
        frame_chrome(img, d, title, tg / total)
        if k > 0 and t < 0.14:                                    # cut flash
            img = Image.blend(img, Image.new("RGB", (W, H), ORANGE), 0.3 * (1 - t / 0.14))
        proc.stdin.write(img.tobytes())


# ── the results format ────────────────────────────────────────────────────────────────────

def _say_spread(label: str) -> str:
    team, line = label.rsplit(" ", 1)
    name = _tm(team)[1]
    num = line.lstrip("+-").replace(".5", " and a half")
    return f"{name} {'plus' if line.startswith('+') else 'minus'} {num}"


def _cap(t):
    return t[:1].upper() + t[1:]


def _pick_facts(g):
    pick_home = g["pick"] == "home"
    mine = g["home_pts"] if pick_home else g["away_pts"]
    theirs = g["away_pts"] if pick_home else g["home_pts"]
    team = _tm(g["home"] if pick_home else g["away"])[1]
    spread = float(g["pick_label"].rsplit(" ", 1)[1])
    diff = mine - theirs
    cover = diff + spread
    res = f"won by {diff}" if diff > 0 else f"lost by {-diff}" if diff < 0 else "tied"
    verdict = {"win": "That covers.", "loss": "That's a loss.", "push": "That's a push."}[g["outcome"]]
    line = f"{_cap(_say_spread(g['pick_label']))}. {_cap(team)} {res}. {verdict}"
    sub = (f"COVERED BY {cover:g}" if cover > 0 else f"MISSED BY {-cover:g}" if cover < 0 else "PUSH")
    return line, sub


def _verdict(outcome):
    return {"win": (GREEN, "WIN"), "loss": (RED, "LOSS")}.get(outcome, (GOLD, "PUSH"))


def _title_scene(wins, losses, outcomes, when="LAST NIGHT'S", say_when="last night", tip="tip-off"):
    w_l = f"{wins}-{losses}"
    col = GREEN if wins > losses else GOLD if wins == losses else RED
    at = lambda sc: sc.cue(str(wins), 0.35, last=False)

    def draw(img, d, t, sc):
        p1 = out_cubic(prog(t, 0.05, 0.4))
        put(img, layer(when, 150, WHITE, stroke=6), -500 + p1 * (W / 2 + 500), 380)
        p2 = out_cubic(prog(t, 0.15, 0.5))
        put(img, layer("RESULTS", 210, ORANGE, stroke=7), W + 600 - p2 * (W / 2 + 600), 560)
        ts = at(sc)
        pb = out_cubic(prog(t, ts - 0.35, ts))
        put(img, band(), W / 2 + (1 - pb) * W, 880)
        p3 = prog(t, ts, ts + 0.26)
        if p3 > 0:
            put(img, layer(w_l, 230, col, stroke=10), W / 2, 895, scale=2.5 - 1.5 * out_back(p3),
                alpha=min(1, p3 * 3))
        cw, gap = 170, 26
        x0 = (W - (len(outcomes) * cw + (len(outcomes) - 1) * gap)) / 2
        for i, r in enumerate(outcomes):
            pc = prog(t, ts + 0.4 + 0.18 * i, ts + 0.62 + 0.18 * i)
            c, tag = _verdict(r)
            put(img, hexplate(cw, 92, c, WHITE, tag[0], 64, WHITE), x0 + i * (cw + gap) + cw / 2, 1110,
                scale=out_back(pc), alpha=pc)
        put(img, layer(f"{len(outcomes)} SPREAD PICKS  ·  LOGGED BEFORE {'KICKOFF' if tip == 'kickoff' else 'TIP'}",
                       46, GOLD, stroke=3),
            W / 2, 1200, alpha=prog(t, 0.6, 1.0))

    def sounds(sc):
        ts = at(sc)
        return ([(0.04, "scratch", 0.6), (ts, "thud", 1.0)]
                + [(ts + 0.4 + 0.18 * i, "pop", 0.6) for i in range(len(outcomes))])

    react = "celebrate" if wins > losses else "facepalm" if wins < losses else "shrug"
    return Scene(f"We went {wins} and {losses} {say_when}. Every pick was logged before {tip}. "
                 f"Here's how each one finished.", draw, min_dur=4.0, sounds=sounds, shake_at=at,
                 moods=lambda sc: [(0.0, "point"), (at(sc), react), (at(sc) + 1.3, "talk")])


def _pick_scene(i, n, g):
    line, sub = _pick_facts(g)
    col, tag = _verdict(g["outcome"])
    at = lambda sc: sc.cue("That", 0.62)

    def draw(img, d, t, sc):
        put(img, layer(f"PICK {i} OF {n}", 58, GOLD, stroke=4), W / 2, 280, alpha=prog(t, 0, 0.2))
        pl = out_back(prog(t, 0.06, 0.34))
        put(img, hexplate(700, 150, (14, 14, 16), ORANGE, g["pick_label"], 104, ORANGE), W / 2, 420,
            scale=0.4 + 0.6 * pl, alpha=min(1, pl * 2))
        pb = out_cubic(prog(t, 0.2, 0.5))
        put(img, band(), W / 2 - (1 - pb) * W, 690)
        pt = out_back(prog(t, 0.42, 0.72))
        put(img, split_tile(g["away"], g["home"]), W / 2, 712, scale=pt, alpha=min(1, pt * 2), angle=(1 - pt) * 20)
        pc = out_cubic(prog(t, 0.6, 1.5))
        for x, ab, pts in ((190, g["away"], g["away_pts"]), (W - 190, g["home"], g["home_pts"])):
            a = prog(t, 0.55, 0.7)
            put(img, layer(ab, 70, WHITE, stroke=5), x, 640, alpha=a)
            put(img, layer(str(int(round(pts * pc))), 110, ORANGE, stroke=6), x, 760, alpha=a)
        put(img, layer("FINAL", 44, MUTED, stroke=3), W / 2, 880, alpha=prog(t, 1.5, 1.7))
        ts = at(sc)
        if t >= ts:
            p = prog(t, ts, ts + 0.28)
            put(img, hexplate(520, 170, col, WHITE, tag, 130, WHITE), W / 2, 1060,
                scale=2.4 - 1.4 * out_back(p), alpha=min(1, p * 4), angle=-8 + 5 * p)
            put(img, layer(sub, 64, col, stroke=5), W / 2, 1190, alpha=prog(t, ts + 0.3, ts + 0.55))

    def sounds(sc):
        ts = at(sc)
        return ([(0.08, "pop", 0.5), (0.45, "pop", 0.5)] + [(0.62 + 0.12 * k, "tick", 0.5) for k in range(7)]
                + [(ts, "thud", 0.8), (ts + 0.02, "win" if g["outcome"] == "win" else "loss", 0.9)])

    react = "celebrate" if g["outcome"] == "win" else "facepalm" if g["outcome"] == "loss" else "shrug"
    return Scene(line, draw, min_dur=3.6, sounds=sounds, shake_at=at,
                 moods=lambda sc: [(0.0, "point"), (0.9, "talk"), (at(sc), react)])


def _record_scene(rec, be):
    w_, l_ = (int(x) for x in rec["record"].split("-")[:2])
    pct = float(rec["pct"])
    above = pct >= be
    col = GREEN if above else GOLD
    at = lambda sc: sc.cue("Break-even", 0.6, last=False)
    line = (f"For the season, we're {w_} and {l_}. That's {pct:.0f} percent. Break-even is {be:.1f}. "
            + ("We're above it." if above else "We're not there yet, and we'll show you either way."))

    def draw(img, d, t, sc):
        put(img, hexplate(760, 120, (14, 14, 16), ORANGE, "SEASON RECORD", 76, ORANGE), W / 2, 330,
            scale=out_back(prog(t, 0, 0.3)))
        pc = out_cubic(prog(t, 0.2, 1.3))
        put(img, layer(f"{round(w_ * pc)}-{round(l_ * pc)}", 250, WHITE, stroke=10), W / 2, 560)
        pp = out_cubic(prog(t, 0.5, 1.6))
        put(img, layer(f"{pct * pp:.1f}%", 120, col, stroke=7), W / 2, 760, alpha=prog(t, 0.45, 0.6))
        x0, x1, y = 110, W - 110, 900
        lo, hi = 40.0, 60.0
        pos = lambda v: x0 + (min(max(v, lo), hi) - lo) / (hi - lo) * (x1 - x0)
        d.rounded_rectangle((x0 - 6, y - 6, x1 + 6, y + 58), radius=32, fill=BLACK, outline=ORANGE, width=4)
        fill = pos(lo + (pct - lo) * out_cubic(prog(t, 0.6, 1.8)))
        if fill > x0 + 52:
            d.rounded_rectangle((x0, y, fill, y + 52), radius=26, fill=col)
        for lab, v in (("40%", 40), ("50%", 50), ("60%", 60)):
            put(img, layer(lab, 38, MUTED, stroke=3), pos(v), y - 44, alpha=prog(t, 0.6, 0.9))
        tb = at(sc)
        pb = out_back(prog(t, tb, tb + 0.3))
        if pb > 0:
            bx, drop = pos(be), (1 - pb) * -140
            d.rectangle((bx - 5, y - 40 + drop, bx + 5, y + 92 + drop), fill=WHITE)
            put(img, hexplate(470, 96, (14, 14, 16), WHITE, f"BREAK-EVEN {be:.1f}%", 52, WHITE),
                bx if bx < W - 260 else W - 260, y + 170 + drop, alpha=min(1, pb * 2))

    def sounds(sc):
        return [(0.2 + 0.1 * k, "tick", 0.45) for k in range(10)] + [(at(sc), "thud", 0.9)]

    return Scene(line, draw, min_dur=4.0, sounds=sounds, shake_at=at,
                 moods=lambda sc: [(0.0, "talk"), (at(sc), "celebrate" if above else "shrug")])


def _cta_scene(nfl=False):
    lines = [("EVERY PICK", WHITE, 0.15), ("LOGGED BEFORE " + ("KICKOFF" if nfl else "TIP"), WHITE, 0.45),
             ("GRADED IN PUBLIC", ORANGE, 0.8)]

    def draw(img, d, t, sc):
        for k, (txt, col, at) in enumerate(lines):
            p = out_back(prog(t, at, at + 0.3))
            put(img, layer(txt, 130, col, stroke=7), W / 2, 430 + k * 165, scale=0.5 + 0.5 * p, alpha=min(1, p * 2))
        pu = prog(t, 1.2, 1.5)
        put(img, hexplate(860, 130, GOLD, WHITE, "KONJUREANALYTICS.COM", 74, BLACK), W / 2, 1010,
            scale=(1 + 0.04 * math.sin(t * 6)) * out_back(pu), alpha=pu)
        pf = out_back(prog(t, 1.8, 2.1))
        put(img, layer("FOLLOW FOR NEXT WEEK'S RESULTS" if nfl else "FOLLOW FOR TOMORROW'S RESULTS", 62, GOLD,
                       stroke=5), W / 2, 1180, scale=pf, alpha=pf)

    def sounds(sc):
        return [(at, "pop", 0.6) for _, _, at in lines] + [(1.2, "thud", 0.6), (1.8, "pop", 0.6)]

    return Scene("Every pick, every result, at Konjure Analytics dot com. "
                 + ("Follow for next week's picks, graded the same way. " if nfl else
                    "Follow to see tonight's picks graded tomorrow. ") + "Not betting advice.", draw, min_dur=4.5, tail=0.8, sounds=sounds,
                 moods=lambda sc: [(0.0, "point"), (2.0, "talk")], tag="cta_results")


def results_short(night: datetime.date | None = None) -> pathlib.Path:
    global SPORT
    SPORT = "nba"
    import nba_daily_picks as ndp
    night = night or datetime.date.today() - datetime.timedelta(days=1)
    e = ndp._load().get(night.isoformat())
    if not e:
        raise SystemExit(f"no NBA board logged for {night}")
    pub = [g for g in e.get("ats", []) if g.get("published")]
    if not pub or any(g.get("outcome") is None for g in pub):
        raise SystemExit(f"{night}: picks not all graded yet")
    wins = sum(g["outcome"] == "win" for g in pub)
    losses = sum(g["outcome"] == "loss" for g in pub)
    r = ndp.record()
    be = float(r.get("breakeven") or 52.38)
    title = f"NBA RESULTS  {night.month}/{night.day}"
    scenes = ([_title_scene(wins, losses, [g["outcome"] for g in pub])]
              + [_pick_scene(i, len(pub), g) for i, g in enumerate(pub, 1)]
              + [_record_scene(r["ats"], be), _cta_scene()])
    return render(scenes, title, OUT / f"short-nba-results-{night.isoformat()}.mp4", fmt="results")


# ── free-licence player photos ────────────────────────────────────────────────────────────
# League headshots are not ours to use. Wikimedia Commons carries photos of most players
# under licences that allow commercial use with credit; only those licences are taken, and
# each video carries the credit on screen and in its description file.
_UA = {"User-Agent": "KonjureAnalytics/1.0 (https://konjureanalytics.com; video photo credits)"}
_FREE = ("CC BY ", "CC BY-SA ", "CC0", "PUBLIC DOMAIN", "PD")


def _wget(url, params=None, tries=4):
    import requests
    for k in range(tries):
        try:
            r = requests.get(url, params=params, headers=_UA, timeout=25)
            return r.json() if params is not None else r.content
        except Exception:
            time.sleep(3 + 3 * k)
    return None


def player_photo(name: str, sport: str = "nba"):
    """(PIL image, credit) for a free-licence photo of `name`, or (None, None). Cached."""
    word = "football" if sport == "nfl" else "basketball"
    import hashlib
    import html
    import json
    import re
    cache = pathlib.Path.home() / ".konjure" / "photos"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{name.lower()}|{sport}".encode()).hexdigest()[:16] if sport != "nba" else \
        hashlib.sha1(name.lower().encode()).hexdigest()[:16]
    meta_p, img_p = cache / f"{key}.json", cache / f"{key}.jpg"
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        if not meta.get("ok"):
            return None, None
        return Image.open(img_p).convert("RGB"), meta["credit"]
    api = "https://en.wikipedia.org/w/api.php"
    meta = {"ok": False}
    found = _wget(api, {"action": "query", "list": "search", "srsearch": f'"{name}" {word}', "srlimit": 6,
                        "format": "json"}) or {}
    for hit in found.get("query", {}).get("search", []):
        title = hit["title"]
        if title.split(" (")[0].lower() != name.lower():
            continue
        time.sleep(1)
        pg = _wget(api, {"action": "query", "titles": title, "prop": "pageimages|extracts", "piprop": "name",
                         "exintro": 1, "explaintext": 1, "exsentences": 2, "format": "json"}) or {}
        page = next(iter(pg.get("query", {}).get("pages", {}).values()), {})
        if word not in page.get("extract", "").lower() or "pageimage" not in page:
            continue
        time.sleep(1)
        fi = _wget("https://commons.wikimedia.org/w/api.php",
                   {"action": "query", "titles": "File:" + page["pageimage"], "prop": "imageinfo",
                    "iiprop": "extmetadata|url", "iiurlwidth": 900, "format": "json"}) or {}
        info = (next(iter(fi.get("query", {}).get("pages", {}).values()), {}).get("imageinfo") or [{}])[0]
        md = info.get("extmetadata", {})
        lic = md.get("LicenseShortName", {}).get("value", "").strip()
        if not lic.upper().startswith(_FREE) or not info.get("thumburl"):
            break
        artist = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", html.unescape(md.get("Artist", {}).get("value", "")))).strip()
        raw = _wget(info["thumburl"])
        if not raw:
            break
        img_p.write_bytes(raw)
        artist = artist.replace(", Wikimedia Commons", "").strip()
        meta = {"ok": True, "credit": f"Photo: {artist[:48]} / {lic}", "file": page["pageimage"],
                "page": info.get("descriptionurl", ""), "license": lic}
        break
    meta_p.write_text(json.dumps(meta))
    if not meta["ok"]:
        return None, None
    return Image.open(img_p).convert("RGB"), meta["credit"]


def photo_frame(img, team, initials, size=430, sport=None):
    """The photo cropped square toward the top (where faces are), in a team-colour frame.
    Without a photo, the player's initials on his team colour."""
    sport = sport or SPORT
    k = ("photo", id(img), team, initials, size, sport)
    if k in _LAYERS:
        return _LAYERS[k]
    col = _tm(team, sport)[0]
    if img is not None:
        w, h = img.size
        if h >= w:
            top = int(h * 0.04)
            face = img.crop((0, top, w, top + w))
        else:
            face = img.crop(((w - h) // 2, 0, (w - h) // 2 + h, h))
        face = face.resize((size, size), Image.LANCZOS)
    else:
        face = Image.new("RGB", (size, size), col)
        fd = ImageDraw.Draw(face)
        f = F(int(size * 0.42))
        fd.text(((size - fd.textlength(initials, font=f)) / 2, size * 0.24), initials, font=f, fill=WHITE,
                stroke_width=6, stroke_fill=BLACK)
    out = Image.new("RGBA", (size + 44, size + 44), (0, 0, 0, 0))
    od = ImageDraw.Draw(out)
    od.rounded_rectangle((0, 0, size + 43, size + 43), radius=58, fill=GOLD + (255,))
    od.rounded_rectangle((8, 8, size + 35, size + 35), radius=52, fill=col + (255,))
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=44, fill=255)
    out.paste(face, (22, 22), m)
    _LAYERS[k] = out
    return out


# ── players of the night ──────────────────────────────────────────────────────────────────

def _cue_any(sc, plural, default):
    """When "points"/"point" (either form) is spoken."""
    for w in sc.words:
        if w[2].strip(".,").lower() in (plural, plural[:-1]):
            return w[0]
    return sc.dur * default


def _say_grade(g):
    return g.replace("+", " plus").replace("-", " minus")


def _team_name(abbr, sport=None):
    return _tm(abbr, sport)[1]


def _why(r):
    """The component that carried his night. Component percentiles are within that night's
    graded players, so the line says "of the night", never "of the season"."""
    comp = {"efficiency_pct": ("the most efficient scoring", "efficiency"),
            "production_pct": ("the biggest box score", "production"),
            "usage_pct": ("the heaviest workload", "usage")}
    k = max(comp, key=lambda c: float(r.get(c) or 0))
    pct = float(r.get(k) or 0)
    best, label = comp[k]
    if pct >= 99.5:
        return f"He had {best} of the night.", f"{label.upper()}: BEST OF THE NIGHT"
    return (f"His {label} beat {int(pct)} percent of the night's players.",
            f"{label.upper()}: TOP {max(1, 100 - int(pct))}% OF THE NIGHT")


def _top5_title(day):
    at = lambda sc: sc.cue("five", 0.3, last=False)

    def draw(img, d, t, sc):
        p1 = out_cubic(prog(t, 0.05, 0.4))
        put(img, layer("PLAYERS OF", 150, WHITE, stroke=6), -500 + p1 * (W / 2 + 500), 380)
        p2 = out_cubic(prog(t, 0.15, 0.5))
        put(img, layer("THE NIGHT", 200, ORANGE, stroke=7), W + 600 - p2 * (W / 2 + 600), 560)
        ts = at(sc)
        pb = out_cubic(prog(t, ts - 0.35, ts))
        put(img, band(), W / 2 + (1 - pb) * W, 880)
        p3 = prog(t, ts, ts + 0.26)
        if p3 > 0:
            put(img, layer("TOP 5", 210, (20, 20, 22)), W / 2, 895, scale=2.5 - 1.5 * out_back(p3),
                alpha=min(1, p3 * 3))
        put(img, layer("GRADED 0-100 AGAINST EVERY GAME LAST SEASON", 40, GOLD, stroke=3), W / 2, 1110,
            alpha=prog(t, 0.8, 1.2))

    def sounds(sc):
        return [(0.04, "scratch", 0.6), (at(sc), "thud", 1.0)]

    return Scene("These are the five best players in the NBA last night, graded by the numbers. "
                 "Let's count them down.", draw, min_dur=4.0, sounds=sounds, shake_at=at,
                 moods=lambda sc: [(0.0, "point"), (at(sc), "celebrate"), (at(sc) + 1.2, "talk")])


def _player_scene(rank, r, photo, credit):
    name, team, opp = r["PLAYER_NAME"], r["TEAM_ABBREVIATION"], r["opponent"]
    pts, reb, ast = int(r["PTS"]), int(r["REB"]), int(r["AST"])
    ts_pct = r["ts_pct"] if r["ts_pct"] == r["ts_pct"] else None
    grade, score = r["grade"], float(r["score"])
    _, why_show = _why(r)
    won = r["WL"] == "W"
    result = f"in a win over {_team_name(opp)}" if won else f"in a loss to {_team_name(opp)}"
    opener = "And the player of the night." if rank == 1 else f"Number {rank}."
    n_ = lambda v, one, many: f"{v} {one if v == 1 else many}"
    line = (f"{opener} {name}, {_cap(_team_name(team))}. {n_(pts, 'point', 'points')}, "
            f"{n_(reb, 'rebound', 'rebounds')} and {n_(ast, 'assist', 'assists')}, "
            f"{result}. Grade: {_say_grade(grade)}.")      # the "why" is on screen, not read
    initials = "".join(p[0] for p in name.replace(".", "").split()[:2]).upper()
    stats = [(f"{pts}", "PTS", "points"), (f"{reb}", "REB", "rebounds"), (f"{ast}", "AST", "assists")]
    if ts_pct is not None:
        stats.append((f"{ts_pct * 100:.0f}%", "TS", None))
    gcol = GREEN if grade.startswith("A") else GOLD if grade.startswith("B") else ORANGE
    at_grade = lambda sc: sc.after("Grade", 0.85)

    def draw(img, d, t, sc):
        pr = prog(t, 0.0, 0.25)
        tag = f"#{rank}"
        put(img, hexplate(250, 170, ORANGE if rank == 1 else (14, 14, 16), WHITE, tag, 130,
                          BLACK if rank == 1 else ORANGE), 820, 360, scale=2.2 - 1.2 * out_back(pr),
            alpha=min(1, pr * 3))
        if rank == 1:
            put(img, layer("PLAYER OF THE NIGHT", 52, GOLD, stroke=4), 820, 480, alpha=prog(t, 0.3, 0.6))
        pp = out_cubic(prog(t, 0.12, 0.5))
        put(img, photo_frame(photo, team, initials), -300 + pp * (300 + 330), 520, angle=(1 - pp) * 12)
        if credit:
            cl = layer(credit, 22, (200, 200, 205), head=False, stroke=2)
            put(img, cl, max(330, cl.width / 2 + 44), 770, alpha=prog(t, 0.5, 0.8))
        pn = out_cubic(prog(t, 0.35, 0.65))
        put(img, hexplate(980, 120, (14, 14, 16), ORANGE, name.upper(), 84 if len(name) < 20 else 70, WHITE),
            W / 2 + (1 - pn) * W, 860)
        put(img, layer(f"{team}  ·  {'W' if won else 'L'} {'VS' if won else 'TO'} {opp}", 50, GOLD, stroke=4),
            W / 2, 960, alpha=prog(t, 0.6, 0.85))
        cw, gap = 225, 20
        x0 = (W - (len(stats) * cw + (len(stats) - 1) * gap)) / 2
        for k, (val, lab, word) in enumerate(stats):
            at = (_cue_any(sc, word, 0.3 + 0.08 * k) if word else _cue_any(sc, "assists", 0.5) + 0.35)
            pc = out_back(prog(t, at - 0.05, at + 0.2))
            put(img, hexplate(cw, 110, (14, 14, 16), GOLD, f"{val} {lab}", 58, WHITE),
                x0 + k * (cw + gap) + cw / 2, 1075, scale=pc, alpha=min(1, pc * 2))
        tg = at_grade(sc)
        tw = (_cue_any(sc, "assists", 0.5) + tg) / 2
        put(img, layer(why_show, 40, ORANGE, stroke=3), W / 2, 1180, alpha=prog(t, tw, tw + 0.3))
        if t >= tg:
            p = prog(t, tg, tg + 0.28)
            put(img, hexplate(250, 170, gcol, WHITE, grade, 120, WHITE), 820, 640,
                scale=2.4 - 1.4 * out_back(p), alpha=min(1, p * 4), angle=-8 + 5 * p)
            put(img, layer(f"{score:.1f}", 46, gcol, stroke=4), 820, 750, alpha=prog(t, tg + 0.2, tg + 0.4))

    def sounds(sc):
        tg = at_grade(sc)
        s = [(0.02, "thud", 0.8), (0.15, "scratch", 0.4)]
        s += [(_cue_any(sc, w, 0.3), "pop", 0.55) for _, _, w in stats if w]
        return s + [(tg, "thud", 0.9), (tg + 0.02, "win", 0.8 if grade.startswith("A") else 0.4)]

    react = "celebrate" if grade.startswith("A") else "talk"
    return Scene(line, draw, min_dur=5.0, tail=1.3, sounds=sounds, shake_at=at_grade,
                 moods=lambda sc: [(0.0, "point"), (1.0, "talk"), (at_grade(sc), react),
                                   (at_grade(sc) + 1.4, "talk")])


def _top5_cta():
    def draw(img, d, t, sc):
        for k, (txt, col, at) in enumerate((("EVERY PLAYER", WHITE, 0.15), ("GRADED", WHITE, 0.45),
                                            ("EVERY NIGHT", ORANGE, 0.75))):
            p = out_back(prog(t, at, at + 0.3))
            put(img, layer(txt, 140, col, stroke=7), W / 2, 430 + k * 170, scale=0.5 + 0.5 * p, alpha=min(1, p * 2))
        pu = prog(t, 1.2, 1.5)
        put(img, hexplate(860, 130, GOLD, WHITE, "KONJUREANALYTICS.COM", 74, BLACK), W / 2, 1010,
            scale=(1 + 0.04 * math.sin(t * 6)) * out_back(pu), alpha=pu)
        pf = out_back(prog(t, 1.8, 2.1))
        put(img, layer("FOLLOW FOR TONIGHT'S TOP 5", 62, GOLD, stroke=5), W / 2, 1170, scale=pf, alpha=pf)

    return Scene("Every player, graded every night, at Konjure Analytics dot com. Follow for tonight's top five.",
                 draw, min_dur=4.5, tail=0.8,
                 sounds=lambda sc: [(0.15, "pop", 0.6), (0.45, "pop", 0.6), (0.75, "pop", 0.6), (1.2, "thud", 0.6)],
                 moods=lambda sc: [(0.0, "point"), (2.0, "celebrate")])


def top5_short(night: datetime.date | None = None) -> pathlib.Path:
    global SPORT
    SPORT = "nba"
    import nba_analysis as na
    import nba_grades as ng
    night = night or datetime.date.today() - datetime.timedelta(days=1)
    season = na.seasons(night)[0]
    g = ng.grade_night(season, night.isoformat())
    if g is None or g.empty:
        raise SystemExit(f"no graded NBA games on {night}")
    top = g.sort_values("score", ascending=False).head(5).reset_index(drop=True)
    rows = [top.iloc[i] for i in range(len(top))]
    shots = [player_photo(r["PLAYER_NAME"]) for r in rows]
    scenes = [_top5_title(night)]
    for rank in range(len(rows), 0, -1):                          # countdown: 5 ... 1
        ph, cr = shots[rank - 1]
        scenes.append(_player_scene(rank, rows[rank - 1], ph, cr))
    scenes.append(_top5_cta())
    out = OUT / f"short-nba-top5-{night.isoformat()}.mp4"
    render(scenes, f"TOP 5 PLAYERS  {night.month}/{night.day}", out, fmt="top5")
    credits = [f"{r['PLAYER_NAME']}: {c.removeprefix('Photo: ')}" for r, (_, c) in zip(rows, shots) if c]
    desc = (f"The five best NBA players on {night.strftime('%B')} {night.day}, graded 0-100 against every "
            f"player-game from last season.\n\n"
            + "".join(f"#{i + 1} {r['PLAYER_NAME']} ({r['TEAM_ABBREVIATION']}) - {r['grade']}, "
                      f"{float(r['score']):.1f}: {r['PTS']} pts, {r['REB']} reb, {r['AST']} ast\n"
                      for i, r in enumerate(rows))
            + "\nEvery player graded every night: https://konjureanalytics.com\n21+. Not betting advice.\n"
            + ("\nPhoto credits (Wikimedia Commons):\n" + "\n".join(credits) + "\n" if credits else ""))
    out.with_suffix(".txt").write_text(desc, encoding="utf-8")
    return out


# ── shared bits for the new formats ───────────────────────────────────────────────────────

def _say_odds(o):
    o = int(o)
    return f"plus {o}" if o > 0 else f"minus {abs(o)}"


def _ordinal_scene_title(kicker_big, kicker_small, line_big, sub, narration, moods_react="celebrate"):
    """The opening card every countdown uses: two stacked words, a slam on the band, a sub-line."""
    at = lambda sc: sc.cue(line_big.split()[0].lower(), 0.3, last=False) if sc.words else 0.9

    def draw(img, d, t, sc):
        p1 = out_cubic(prog(t, 0.05, 0.4))
        put(img, layer(kicker_small, 140, WHITE, stroke=6), -500 + p1 * (W / 2 + 500), 380)
        p2 = out_cubic(prog(t, 0.15, 0.5))
        put(img, layer(kicker_big, 190, ORANGE, stroke=7), W + 600 - p2 * (W / 2 + 600), 560)
        ts = at(sc)
        pb = out_cubic(prog(t, ts - 0.35, ts))
        put(img, band(), W / 2 + (1 - pb) * W, 880)
        p3 = prog(t, ts, ts + 0.26)
        if p3 > 0:
            put(img, layer(line_big, 150 if len(line_big) < 9 else 110, (20, 20, 22)), W / 2, 895,
                scale=2.5 - 1.5 * out_back(p3), alpha=min(1, p3 * 3))
        put(img, layer(sub, 40, GOLD, stroke=3), W / 2, 1110, alpha=prog(t, 0.8, 1.2))

    return Scene(narration, draw, min_dur=4.0, shake_at=at,
                 sounds=lambda sc: [(0.04, "scratch", 0.6), (at(sc), "thud", 1.0)],
                 moods=lambda sc: [(0.0, "point"), (at(sc), moods_react), (at(sc) + 1.2, "talk")])


def _cta(lines, url_text, follow, narration):
    def draw(img, d, t, sc):
        for k, (txt, col, at) in enumerate(lines):
            p = out_back(prog(t, at, at + 0.3))
            put(img, layer(txt, 120, col, stroke=7), W / 2, 430 + k * 160, scale=0.5 + 0.5 * p, alpha=min(1, p * 2))
        pu = prog(t, 1.2, 1.5)
        put(img, hexplate(900, 130, GOLD, WHITE, url_text, 66 if len(url_text) > 22 else 74, BLACK), W / 2, 1010,
            scale=(1 + 0.04 * math.sin(t * 6)) * out_back(pu), alpha=pu)
        pf = out_back(prog(t, 1.8, 2.1))
        put(img, layer(follow, 58, GOLD, stroke=5), W / 2, 1170, scale=pf, alpha=pf)

    return Scene(narration, draw, min_dur=4.5, tail=0.8,
                 sounds=lambda sc: [(at, "pop", 0.6) for _, _, at in lines] + [(1.2, "thud", 0.6), (1.8, "pop", 0.6)],
                 moods=lambda sc: [(0.0, "point"), (2.0, "celebrate")])


def _wrap_text(text, size, width):
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    f, out, cur = F(size), [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=f) > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = t
    return out + ([cur] if cur else [])


# ── Guess the Grade ───────────────────────────────────────────────────────────────────────
# Three of the day's challenge lines (the same seeded five the site's ?challenge= link plays):
# the middle one, the dud, then the star, so the reveals rise. Viewers are asked to comment a
# guess during a 3-2-1; the full five are on the site.

def _gtg_stats(r):
    import re
    if r["sport"] == "NFL":
        return None
    m = {k: int(v) for v, k in re.findall(r"(\d+) (pts|reb|ast|stl|blk|TO)", r["line"])}
    ts = re.search(r"(\d+)% true shooting", r["line"])
    return m, (int(ts.group(1)) if ts else None)


def _gtg_round_scene(i, n, r, photo, credit):
    sport = r["sport"].lower()
    team, opp = r["team"], r["opponent"]
    say = lambda a: _tm(a, sport)[1]
    score, grade = float(r["score"]), r["grade"]
    st = _gtg_stats(r)
    if st:
        m, ts = st
        won = r.get("result") == "W"
        body = (f"{_cap(say(team))} against {say(opp)}, {'a win' if won else 'a loss'}. "
                f"{m.get('pts', 0)} points, {m.get('reb', 0)} rebounds and {m.get('ast', 0)} assists"
                + (f", on {ts} percent true shooting" if ts is not None else "") + f", in {r['minutes']} minutes.")
        chips = [(f"{m.get('pts', 0)}", "PTS"), (f"{m.get('reb', 0)}", "REB"), (f"{m.get('ast', 0)}", "AST"),
                 (f"{m.get('stl', 0)}", "STL"), (f"{m.get('blk', 0)}", "BLK"), (f"{m.get('TO', 0)}", "TO")]
        if ts is not None:
            chips.append((f"{ts}%", "TS"))
        chips.append((f"{r['minutes']}", "MIN"))
        context = f"{team} VS {opp}  ·  {'WIN' if won else 'LOSS'}  ·  {r['night'].upper()}"
    else:
        body = f"A {r['position']} for the {say(team)}, against the {say(opp)}, {r['night']}. Here's his line."
        chips = None
        context = f"{r['position']}  ·  {team} VS {opp}  ·  {r['night'].upper()}"
    line = (f"Round {i}. {body} What's his grade? Three. Two. One. {r['player']}. {score:.0f}. "
            f"{_say_grade(grade)}.")
    gcol = GREEN if score >= 80 else GOLD if score >= 50 else RED
    initials = "".join(p_[0] for p_ in r["player"].replace(".", "").split()[:2]).upper()
    cd = lambda sc: [sc.cue(w, 0.5 + 0.08 * k) for k, w in enumerate(("Three", "Two", "One"))]
    reveal = lambda sc: sc.after("One", 0.82)

    def draw(img, d, t, sc):
        put(img, layer(f"ROUND {i} OF {n}", 58, GOLD, stroke=4), W / 2, 270, alpha=prog(t, 0, 0.2))
        put(img, hexplate(330, 84, (14, 14, 16), ORANGE, r["sport"], 54, ORANGE), W / 2, 350,
            scale=out_back(prog(t, 0.05, 0.3)))
        tr = reveal(sc)
        pp = out_cubic(prog(t, 0.1, 0.45))
        cx = -300 + pp * (300 + 300)
        if t < tr:
            put(img, photo_frame(None, team, "?", 380, sport), cx, 600, angle=(1 - pp) * 10)
        else:
            pr = out_back(prog(t, tr, tr + 0.3))
            put(img, photo_frame(photo, team, initials, 380, sport), 300, 600, scale=0.6 + 0.4 * pr)
            if credit:
                cl = layer(credit, 20, (200, 200, 205), head=False, stroke=2)
                put(img, cl, max(300, cl.width / 2 + 44), 815, alpha=prog(t, tr + 0.3, tr + 0.6))
        put(img, layer(context, 40, GOLD, stroke=3), W / 2, 870, alpha=prog(t, 0.3, 0.6))
        if chips:
            per_row = 4
            for k, (v, lab) in enumerate(chips):
                row, col = divmod(k, per_row)
                cnt = min(per_row, len(chips) - row * per_row)
                cw, gap = 225, 18
                x0 = (W - (cnt * cw + (cnt - 1) * gap)) / 2
                pc = out_back(prog(t, 0.5 + 0.08 * k, 0.75 + 0.08 * k))
                put(img, hexplate(cw, 96, (14, 14, 16), GOLD, f"{v} {lab}", 50, WHITE),
                    x0 + col * (cw + gap) + cw / 2, 970 + row * 110, scale=pc, alpha=min(1, pc * 2))
        else:
            for k, ln in enumerate(_wrap_text(r["line"], 52, W - 160)[:3]):
                put(img, layer(ln, 52, WHITE, stroke=4), W / 2, 960 + k * 70, alpha=prog(t, 0.5 + 0.1 * k, 0.8 + 0.1 * k))
        c3 = cd(sc)
        if c3[0] - 0.3 <= t < tr:                                   # the guess window
            put(img, layer("COMMENT YOUR GUESS: 0-100", 40, ORANGE, stroke=4), 790, 500,
                scale=1 + 0.05 * math.sin(t * 8), alpha=prog(t, c3[0] - 0.3, c3[0]))
            num = "3" if t < c3[1] else "2" if t < c3[2] else "1"
            at = c3[0] if num == "3" else c3[1] if num == "2" else c3[2]
            pn = out_back(prog(t, at, at + 0.2))
            put(img, hexplate(220, 190, (14, 14, 16), ORANGE, num, 150, ORANGE), 790, 650, scale=0.5 + 0.5 * pn)
        if t >= tr:
            p = prog(t, tr, tr + 0.28)
            put(img, hexplate(560, 120, (14, 14, 16), ORANGE, r["player"].upper(), 64 if len(r["player"]) < 18 else 52,
                              WHITE), W / 2 + 0 * p, 870 - 0 * p, alpha=min(1, p * 3))
            put(img, hexplate(250, 170, gcol, WHITE, grade, 120, WHITE), 790, 560,
                scale=2.4 - 1.4 * out_back(p), alpha=min(1, p * 4), angle=-8 + 5 * p)
            put(img, layer(f"{score:.1f}", 60, gcol, stroke=5), 790, 690, alpha=prog(t, tr + 0.2, tr + 0.4))

    def sounds(sc):
        c3, tr = cd(sc), reveal(sc)
        return ([(0.05, "pop", 0.5)] + [(x, "tick", 0.9) for x in c3] + [(c3[0], "thud", 0.4)]
                + [(tr, "thud", 0.9), (tr + 0.02, "win" if score >= 50 else "loss", 0.8)])

    react = "celebrate" if score >= 80 else "facepalm" if score < 30 else "shrug"
    return Scene(line, draw, min_dur=6.0, tail=1.3, sounds=sounds, shake_at=reveal,
                 moods=lambda sc: [(0.0, "point"), (1.0, "talk"), (cd(sc)[0], "shrug"), (reveal(sc), react),
                                   (reveal(sc) + 1.4, "talk")])


def gtg_short(day: datetime.date | None = None) -> pathlib.Path:
    """Guess the Grade: three of `day`'s challenge lines, guessed in the comments, then revealed."""
    global SPORT
    import games as gm
    day = day or datetime.date.today()
    c = gm.gtg_challenge(day)
    if not c:
        raise SystemExit(f"no Guess the Grade challenge for {day}")
    by = sorted(c["rounds"], key=lambda r: r["score"])
    picks = [by[len(by) // 2], by[0], by[-1]]                   # middle, dud, star
    scenes = [_ordinal_scene_title("THE GRADE", "GUESS", "3 PLAYERS",
                                   "COMMENT YOUR GUESSES: 0 TO 100",
                                   "Guess the grade. Three stat lines from this week, names hidden. Comment your "
                                   "guess for each, zero to a hundred. 3 players. Let's go.")]
    for i, r in enumerate(picks, 1):
        SPORT = r["sport"].lower()
        ph, cr = player_photo(r["player"], SPORT)
        scenes.append(_gtg_round_scene(i, len(picks), r, ph, cr))
    SPORT = "nba"
    code = gm.challenge_code(day)
    scenes.append(_cta([("HOW'D YOU DO?", WHITE, 0.15), ("2 MORE LINES", WHITE, 0.45), ("PLAY THE FULL FIVE", ORANGE, 0.75)],
                       "KONJUREANALYTICS.COM", f"TODAY'S CHALLENGE: {code}",
                       "How'd you do? Two more mystery lines are waiting in today's full challenge. "
                       "Play free at Konjure Analytics dot com."))
    out = OUT / f"short-gtg-{day.isoformat()}.mp4"
    render(scenes, f"GUESS THE GRADE  {day.month}/{day.day}", out, fmt="gtg")
    out.with_suffix(".txt").write_text(
        "Guess the grade: three real stat lines from this week, names hidden. Comment your guesses, 0-100.\n\n"
        + "".join(f"Round {i}: {r['player']} - {r['grade']} ({float(r['score']):.1f})\n" for i, r in enumerate(picks, 1))
        + f"\nPlay today's full five free: https://konjureanalytics.com/app/?challenge={code}\n"
        + "Grades are percentiles against every player-game last season.\n", encoding="utf-8")
    return out


# ── Beat the Market ───────────────────────────────────────────────────────────────────────
# Tonight's five props, as the site froze them (games_store: Supabase in production). Without
# those credentials here the slate cannot be read, and a Short of different props would not be
# the game players see, so the format skips.

_STAT_SAY = {"Points": "points", "Rebounds": "rebounds", "Assists": "assists", "3-PT Made": "threes",
             "Pts+Rebs+Asts": "points, rebounds and assists"}
_STAT_SHOW = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3-PT Made": "3PM", "Pts+Rebs+Asts": "PRA"}


def _btm_scene(i, n, pr, photo, credit):
    mk = float(pr["p_over_market"])
    md = pr.get("p_over_model")
    line = (f"Prop {i}. {pr['player']}, {pr['line']:g} {_STAT_SAY.get(pr['stat'], pr['stat'].lower())}. "
            f"The market has the over at {mk * 100:.0f} percent."
            + (f" Our model says {float(md) * 100:.0f}." if md is not None else ""))
    initials = "".join(p_[0] for p_ in pr["player"].replace(".", "").split()[:2]).upper()
    away, home = [x.strip() for x in pr["game"].split("@")]

    def draw(img, d, t, sc):
        put(img, layer(f"PROP {i} OF {n}", 58, GOLD, stroke=4), W / 2, 270, alpha=prog(t, 0, 0.2))
        pp = out_cubic(prog(t, 0.1, 0.45))
        put(img, photo_frame(photo, "", initials, 360), -300 + pp * 600, 560, angle=(1 - pp) * 10)
        if credit:
            cl = layer(credit, 20, (200, 200, 205), head=False, stroke=2)
            put(img, cl, max(300, cl.width / 2 + 44), 765, alpha=prog(t, 0.5, 0.8))
        put(img, split_tile(away, home, 170), 800, 520, scale=out_back(prog(t, 0.2, 0.45)))
        put(img, layer(pr["game"], 44, GOLD, stroke=3), 800, 650, alpha=prog(t, 0.3, 0.6))
        pn = out_cubic(prog(t, 0.3, 0.6))
        put(img, hexplate(980, 120, (14, 14, 16), ORANGE,
                          f"{pr['player'].upper()}  O/U {pr['line']:g} {_STAT_SHOW.get(pr['stat'], '')}",
                          62 if len(pr["player"]) < 16 else 52, WHITE), W / 2 + (1 - pn) * W, 860)
        ta = sc.cue("market", 0.4, last=False)
        x0, x1, y = 120, W - 120, 990
        fill = out_cubic(prog(t, ta, ta + 0.6))
        d.rounded_rectangle((x0 - 6, y - 6, x1 + 6, y + 70), radius=38, fill=BLACK, outline=ORANGE, width=4)
        split = x0 + (x1 - x0) * mk * fill
        if fill > 0:
            d.rounded_rectangle((x0, y, max(x0 + 60, split), y + 64), radius=32, fill=GREEN)
        put(img, layer(f"OVER {mk * 100:.0f}%", 44, WHITE, stroke=4), x0 + 140, y + 32, alpha=prog(t, ta + 0.3, ta + 0.5))
        put(img, layer(f"UNDER {(1 - mk) * 100:.0f}%", 44, WHITE, stroke=4), x1 - 150, y + 32,
            alpha=prog(t, ta + 0.3, ta + 0.5))
        put(img, layer("THE MARKET (FANDUEL, VIG OUT)", 32, MUTED, stroke=2), W / 2, y - 34, alpha=prog(t, ta, ta + 0.3))
        if md is not None:
            tm = sc.cue("model", 0.7, last=False)
            pm_ = out_back(prog(t, tm, tm + 0.25))
            put(img, hexplate(620, 100, (40, 30, 80), GOLD, f"OUR MODEL: {float(md) * 100:.0f}% OVER", 54, GOLD),
                W / 2, 1150, scale=pm_, alpha=min(1, pm_ * 2))

    def sounds(sc):
        return [(0.05, "pop", 0.5), (sc.cue("market", 0.4, last=False), "thud", 0.6)] + \
               ([(sc.cue("model", 0.7, last=False), "pop", 0.7)] if md is not None else [])

    return Scene(line, draw, min_dur=4.5, tail=0.9, sounds=sounds,
                 moods=lambda sc: [(0.0, "point"), (1.0, "talk")])


def btm_short(day: datetime.date | None = None) -> pathlib.Path:
    global SPORT
    SPORT = "nba"
    import games as gm
    import games_store as gs
    day = day or datetime.date.today()
    if gs.backend() != "supabase":
        raise SystemExit("Beat the Market needs the site's Supabase credentials to read the frozen slate")
    sl = gm.btm_slate(day, create=False)
    if not sl or not sl.get("props"):
        raise SystemExit(f"no Beat the Market slate frozen yet for {day}")
    props = sl["props"]
    scenes = [_ordinal_scene_title("THE MARKET", "BEAT", "5 PROPS", "PICK OVER OR UNDER, HOW SURE YOU ARE",
                                   "Can you beat the market? Tonight's five props: pick over or under, and how "
                                   "sure you are. 5 props. Here they are.")]
    for i, pr in enumerate(props, 1):
        ph, cr = player_photo(pr["player"], "nba")
        scenes.append(_btm_scene(i, len(props), pr, ph, cr))
    scenes.append(_cta([("BEAT THE BOOK", WHITE, 0.15), ("BEAT OUR MODEL", WHITE, 0.45), ("FREE, BEFORE TIP", ORANGE, 0.75)],
                       "KONJUREANALYTICS.COM", "ENTRIES LOCK AT FIRST TIP-OFF",
                       "Beat the book, beat our model. Play free before tip-off at Konjure Analytics dot com. "
                       "No prizes, just bragging rights."))
    out = OUT / f"short-btm-{day.isoformat()}.mp4"
    render(scenes, f"BEAT THE MARKET  {day.month}/{day.day}", out, fmt="btm")
    out.with_suffix(".txt").write_text(
        "Tonight's Beat the Market: five NBA props. Pick over or under and how sure you are; you're scored "
        "against FanDuel's price with the margin taken out.\n\n"
        + "".join(f"{p_['player']}: {p_['line']:g} {p_['stat']} (market {float(p_['p_over_market']) * 100:.0f}% over)\n"
                  for p_ in props)
        + "\nPlay free before tip-off: https://konjureanalytics.com\nFree game, no prizes. 21+.\n", encoding="utf-8")
    return out


# ── NFL ───────────────────────────────────────────────────────────────────────────────────
# Sunday and Thursday: the day's anytime-TD board, model against market, counted down.
# Monday: Sunday's spread picks graded, and Sunday's top five graded players.

def _nfl_team_of(player):
    import nfl_analysis as nfl
    wk = nfl._load_weekly(nfl.season_for_date(datetime.date.today()))
    me = wk[wk["player_display_name"].map(nfl._name_key) == nfl._name_key(player)]
    return str(me.sort_values("week")["team"].iloc[-1]) if not me.empty else ""


def _short_game(label):
    import nfl_hub
    abbr = {m["name"]: a for a, m in nfl_hub._teams().items()}
    a, h = [x.strip() for x in label.split("@")]
    return abbr.get(a, a), abbr.get(h, h)


def _td_scene(rank, r, team, photo, credit):
    away, home = _short_game(r["game"])
    m, k, o = float(r["model_prob"]) * 100, float(r["fair_prob"]) * 100, int(r["american_odds"])
    line = (f"Number {rank}. {r['player']}, {_tm(team, 'nfl')[1] if team else ''}. Our model gives him {m:.0f} percent "
            f"to score. The market says {k:.0f}. {_say_odds(o)}.")
    initials = "".join(p_[0] for p_ in r["player"].replace(".", "").split()[:2]).upper()
    hot = m > k

    def draw(img, d, t, sc):
        pr = prog(t, 0.0, 0.25)
        put(img, hexplate(250, 170, ORANGE if rank == 1 else (14, 14, 16), WHITE, f"#{rank}", 130,
                          BLACK if rank == 1 else ORANGE), 820, 360, scale=2.2 - 1.2 * out_back(pr), alpha=min(1, pr * 3))
        pp = out_cubic(prog(t, 0.12, 0.5))
        put(img, photo_frame(photo, team, initials), -300 + pp * 630, 520, angle=(1 - pp) * 12)
        if credit:
            cl = layer(credit, 22, (200, 200, 205), head=False, stroke=2)
            put(img, cl, max(330, cl.width / 2 + 44), 770, alpha=prog(t, 0.5, 0.8))
        put(img, split_tile(away, home, 150), 820, 560, scale=out_back(prog(t, 0.3, 0.55)))
        pn = out_cubic(prog(t, 0.35, 0.65))
        put(img, hexplate(980, 120, (14, 14, 16), ORANGE, r["player"].upper(), 84 if len(r["player"]) < 20 else 70, WHITE),
            W / 2 + (1 - pn) * W, 860)
        put(img, layer(f"{away} @ {home}  ·  ANYTIME TD  ·  {o:+d}", 48, GOLD, stroke=4), W / 2, 960,
            alpha=prog(t, 0.6, 0.85))
        tm, tk = sc.cue("model", 0.4, last=False), sc.cue("market", 0.65, last=False)
        for x, lab, v, at, col in ((290, "OUR MODEL", m, tm, GREEN if hot else WHITE), (W - 290, "THE MARKET", k, tk, WHITE)):
            pc = out_back(prog(t, at, at + 0.25))
            put(img, hexplate(420, 150, (14, 14, 16), GOLD, f"{v:.0f}%", 100, col), x, 1100, scale=pc, alpha=min(1, pc * 2))
            put(img, layer(lab, 38, MUTED, stroke=3), x, 1010 + 0 * pc, alpha=prog(t, at, at + 0.2))

    def sounds(sc):
        return [(0.02, "thud", 0.8), (0.15, "scratch", 0.4), (sc.cue("model", 0.4, last=False), "pop", 0.6),
                (sc.cue("market", 0.65, last=False), "pop", 0.6)]

    return Scene(line, draw, min_dur=4.8, tail=0.9, sounds=sounds,
                 moods=lambda sc: [(0.0, "point"), (1.0, "talk"),
                                   (sc.cue("market", 0.65, last=False), "celebrate" if hot else "talk")])


def nfl_td_short(day: datetime.date | None = None) -> pathlib.Path:
    """The day's anytime-TD board (live FanDuel prices, our TD model), top five counted down."""
    global SPORT
    SPORT = "nfl"
    import nba_weekly_picks as nwp
    import weekly_picks as wp
    day = day or datetime.date.today()
    pool = [r for r in wp._td_pool() if nwp._local_date(r.get("start_time") or "") == day]
    if not pool:
        raise SystemExit(f"no NFL anytime-TD prices for games on {day}")
    top = sorted(pool, key=lambda r: -float(r["model_prob"]))[:5]
    label = "THURSDAY NIGHT" if day.weekday() == 3 else "MONDAY NIGHT" if day.weekday() == 0 else day.strftime("%A").upper()
    scenes = [_ordinal_scene_title("TD BOARD", label, "TOP 5", "OUR MODEL BESIDE FANDUEL'S PRICE",
                                   f"{_cap(label.lower())} touchdown board. The five likeliest scorers by our model, "
                                   f"beside the market. Top 5. Research, not picks.")]
    for rank in range(len(top), 0, -1):
        r = top[rank - 1]
        team = _nfl_team_of(r["player"])
        ph, cr = player_photo(r["player"], "nfl")
        scenes.append(_td_scene(rank, r, team, ph, cr))
    scenes.append(_cta([("EVERY TD PRICE", WHITE, 0.15), ("LOGGED AND", WHITE, 0.45), ("GRADED", ORANGE, 0.75)],
                       "KONJUREANALYTICS.COM", "FOLLOW FOR THE RESULTS",
                       "Every touchdown board is logged and graded at Konjure Analytics dot com. Twenty-one and over. "
                       "Not betting advice."))
    SPORT = "nfl"
    out = OUT / f"short-nfl-td-{day.isoformat()}.mp4"
    render(scenes, f"NFL TD BOARD  {day.month}/{day.day}", out, fmt="nfl-td")
    SPORT = "nba"
    out.with_suffix(".txt").write_text(
        f"{label.title()} anytime-touchdown board: our TD model beside FanDuel's price. Research, not picks.\n\n"
        + "".join(f"#{i + 1} {r['player']} ({r['game']}): model {float(r['model_prob']) * 100:.0f}%, market "
                  f"{float(r['fair_prob']) * 100:.0f}%, {int(r['american_odds']):+d}\n" for i, r in enumerate(top))
        + "\nEvery board logged and graded: https://konjureanalytics.com\n21+. Not betting advice. 1-800-GAMBLER.\n",
        encoding="utf-8")
    return out


def _sunday_games(day):
    import nfl_game_model as gm
    s = gm.schedule()
    return s[s["gameday"].astype(str).str[:10] == day.isoformat()]


def nfl_results_short(sunday: datetime.date | None = None) -> pathlib.Path:
    """Sunday's spread picks, graded, and the season ATS record against break-even."""
    global SPORT
    SPORT = "nfl"
    import json
    import weekly_picks as wp
    sunday = sunday or datetime.date.today() - datetime.timedelta(days=1)
    games = _sunday_games(sunday)
    if games.empty:
        raise SystemExit(f"no NFL games on {sunday}")
    week, season = int(games["week"].iloc[0]), int(games["season"].iloc[0])
    e = json.loads((ROOT / "weekly_picks.json").read_text(encoding="utf-8")).get(f"{season}-{week:02d}") or {}
    score = {(r.away_team, r.home_team): (r.away_score, r.home_score) for r in games.itertuples()}
    pub = []
    for g in e.get("ats", []):
        k = (g["away_team"], g["home_team"])
        if k not in score:
            continue                                      # Thursday or Monday game
        a, h = score[k]
        if g.get("outcome") is None or a != a:
            raise SystemExit(f"{sunday}: NFL picks not all graded yet")
        pub.append({"pick_label": _nfl_label(g["pick_label"]), "pick": g["pick"], "away": g["away_team"],
                    "home": g["home_team"], "away_pts": int(a), "home_pts": int(h), "outcome": g["outcome"]})
    if not pub:
        raise SystemExit(f"no NFL spread picks on {sunday}")
    wins = sum(g["outcome"] == "win" for g in pub)
    losses = sum(g["outcome"] == "loss" for g in pub)
    rec = wp.record(season)
    r2 = {"record": rec["ats_record"], "pct": rec["ats_pct"]}
    sc0 = _title_scene(wins, losses, [g["outcome"] for g in pub], "SUNDAY'S", "on Sunday", "kickoff")
    scenes = [sc0] + [_pick_scene(i, len(pub), g) for i, g in enumerate(pub, 1)] + \
             [_record_scene(r2, float(rec.get("ats_breakeven") or 52.38)), _cta_scene(nfl=True)]
    out = OUT / f"short-nfl-results-{sunday.isoformat()}.mp4"
    render(scenes, f"NFL RESULTS  WEEK {week}", out, fmt="nfl-results")
    SPORT = "nba"
    out.with_suffix(".txt").write_text(
        f"Every NFL spread pick is logged before kickoff and graded in public, losses included.\n\n"
        f"Week {week} Sunday: {wins}-{losses}\n"
        + "".join(f"{g['pick_label']}: {g['outcome'].upper()} ({g['away']} {g['away_pts']}, {g['home']} {g['home_pts']})\n"
                  for g in pub)
        + f"\nSeason: {rec['ats_record']} ({rec['ats_pct']}%). Break-even at standard odds is 52.4%.\n\n"
        + "https://konjureanalytics.com\n21+. Not betting advice. 1-800-GAMBLER.\n", encoding="utf-8")
    return out


def _nfl_label(label):
    """'LAC +6.0' -> 'LAC +6': the weekly file keeps one decimal on whole-number spreads."""
    import re
    return re.sub(r"\.0$", "", label.strip())


def _speak_nfl_line(line, pos):
    import re
    out = []
    for seg in [x.strip() for x in re.split(r"[·•]", line) if x.strip()]:
        parts = [p_.strip() for p_ in seg.split(",")]
        spoken = []
        for k, p_ in enumerate(parts):
            m = re.fullmatch(r"(\d+)/(\d+)", p_)
            if m:
                a, b = m.groups()
                spoken.append(f"{a} of {b}" if pos == "QB" and not out else f"{a} catches on {b} targets")
                continue
            m = re.fullmatch(r"(-?\d+)-(-?\d+) rush", p_) or re.fullmatch(r"(-?\d+)-(-?\d+)", p_)
            if m:
                a, b = m.groups()
                spoken.append(f"{a} {'carry' if a == '1' else 'carries'} for {b} yards")
                continue
            p_ = re.sub(r"(\d+) yds", r"\1 yards", p_)
            p_ = re.sub(r"(\d+) rec\b", r"\1 receiving yards", p_)
            p_ = re.sub(r"\b1 TD\b", "1 touchdown", p_)
            p_ = re.sub(r"(\d+) TD\b", r"\1 touchdowns", p_)
            p_ = re.sub(r"\b1 INT\b", "1 interception", p_)
            p_ = re.sub(r"(\d+) INT\b", r"\1 interceptions", p_)
            spoken.append(p_)
        out.append(", ".join(spoken))
    return ". ".join(out)


def _nfl_player_scene(rank, r, photo, credit):
    name, team, opp, pos = r["player"], r["team"], r["opponent"], r["position"]
    grade, score = r["grade"], float(r["score"])
    opener = "And the player of the day." if rank == 1 else f"Number {rank}."
    line = (f"{opener} {name}, {pos} for the {_tm(team, 'nfl')[1]}. {_speak_nfl_line(r['line'], pos)}. "
            f"Grade: {_say_grade(grade)}.")
    initials = "".join(p_[0] for p_ in name.replace(".", "").split()[:2]).upper()
    gcol = GREEN if grade.startswith("A") else GOLD if grade.startswith("B") else ORANGE
    at_grade = lambda sc: sc.after("Grade", 0.85)
    rows = _wrap_text(r["line"], 54, W - 160)[:2]

    def draw(img, d, t, sc):
        pr = prog(t, 0.0, 0.25)
        put(img, hexplate(250, 170, ORANGE if rank == 1 else (14, 14, 16), WHITE, f"#{rank}", 130,
                          BLACK if rank == 1 else ORANGE), 820, 360, scale=2.2 - 1.2 * out_back(pr), alpha=min(1, pr * 3))
        if rank == 1:
            put(img, layer("PLAYER OF THE DAY", 52, GOLD, stroke=4), 820, 480, alpha=prog(t, 0.3, 0.6))
        pp = out_cubic(prog(t, 0.12, 0.5))
        put(img, photo_frame(photo, team, initials), -300 + pp * 630, 520, angle=(1 - pp) * 12)
        if credit:
            cl = layer(credit, 22, (200, 200, 205), head=False, stroke=2)
            put(img, cl, max(330, cl.width / 2 + 44), 770, alpha=prog(t, 0.5, 0.8))
        pn = out_cubic(prog(t, 0.35, 0.65))
        put(img, hexplate(980, 120, (14, 14, 16), ORANGE, name.upper(), 84 if len(name) < 20 else 70, WHITE),
            W / 2 + (1 - pn) * W, 860)
        put(img, layer(f"{pos}  ·  {team} VS {opp}", 50, GOLD, stroke=4), W / 2, 960, alpha=prog(t, 0.6, 0.85))
        for k, ln in enumerate(rows):
            put(img, layer(ln, 54, WHITE, stroke=4), W / 2, 1060 + k * 72, alpha=prog(t, 0.8 + 0.2 * k, 1.1 + 0.2 * k))
        tg = at_grade(sc)
        if t >= tg:
            p = prog(t, tg, tg + 0.28)
            put(img, hexplate(250, 170, gcol, WHITE, grade, 120, WHITE), 820, 640,
                scale=2.4 - 1.4 * out_back(p), alpha=min(1, p * 4), angle=-8 + 5 * p)
            put(img, layer(f"{score:.1f}", 46, gcol, stroke=4), 820, 750, alpha=prog(t, tg + 0.2, tg + 0.4))

    def sounds(sc):
        tg = at_grade(sc)
        return [(0.02, "thud", 0.8), (0.15, "scratch", 0.4), (tg, "thud", 0.9),
                (tg + 0.02, "win", 0.8 if grade.startswith("A") else 0.4)]

    react = "celebrate" if grade.startswith("A") else "talk"
    return Scene(line, draw, min_dur=5.0, tail=1.3, sounds=sounds, shake_at=at_grade,
                 moods=lambda sc: [(0.0, "point"), (1.0, "talk"), (at_grade(sc), react), (at_grade(sc) + 1.4, "talk")])


def nfl_top5_short(sunday: datetime.date | None = None) -> pathlib.Path:
    """Sunday's five best graded players (nfl_grades: against last season, by position)."""
    global SPORT
    SPORT = "nfl"
    import nfl_grades as ng
    sunday = sunday or datetime.date.today() - datetime.timedelta(days=1)
    games = _sunday_games(sunday)
    if games.empty:
        raise SystemExit(f"no NFL games on {sunday}")
    week, season = int(games["week"].iloc[0]), int(games["season"].iloc[0])
    teams = set(games["home_team"]) | set(games["away_team"])
    g = ng.grade_week(season, week)
    g = g[g["team"].isin(teams)]
    if g.empty:
        raise SystemExit(f"{sunday}: Week {week} stats not in yet")
    top = g.sort_values("score", ascending=False).head(5).reset_index(drop=True)
    rows = [top.iloc[i] for i in range(len(top))]
    shots = [player_photo(r["player"], "nfl") for r in rows]
    scenes = [_ordinal_scene_title("OF THE DAY", "PLAYERS", "TOP 5", "GRADED 0-100 AGAINST LAST SEASON, BY POSITION",
                                   "The five best players in the NFL on Sunday, graded by the numbers. "
                                   "Top 5. Let's count them down.")]
    for rank in range(len(rows), 0, -1):
        ph, cr = shots[rank - 1]
        scenes.append(_nfl_player_scene(rank, rows[rank - 1], ph, cr))
    scenes.append(_cta([("EVERY PLAYER", WHITE, 0.15), ("GRADED", WHITE, 0.45), ("EVERY WEEK", ORANGE, 0.75)],
                       "KONJUREANALYTICS.COM", "FOLLOW FOR THURSDAY'S BOARD",
                       "Every player graded every week at Konjure Analytics dot com. Follow for Thursday night."))
    SPORT = "nfl"
    out = OUT / f"short-nfl-top5-{sunday.isoformat()}.mp4"
    render(scenes, f"NFL TOP 5  WEEK {week}", out, fmt="nfl-top5")
    SPORT = "nba"
    credits = [f"{r['player']}: {c.removeprefix('Photo: ')}" for r, (_, c) in zip(rows, shots) if c]
    out.with_suffix(".txt").write_text(
        f"Sunday's five best NFL players, Week {week}, graded 0-100 against last season at the same position.\n\n"
        + "".join(f"#{i + 1} {r['player']} ({r['team']} {r['position']}) - {r['grade']}, {float(r['score']):.1f}: "
                  f"{r['line']}\n" for i, r in enumerate(rows))
        + "\nEvery player graded every week: https://konjureanalytics.com\n"
        + ("\nPhoto credits (Wikimedia Commons):\n" + "\n".join(credits) + "\n" if credits else ""), encoding="utf-8")
    return out


# ── tonight's top 5 picks ─────────────────────────────────────────────────────────────────
# The five from nba_top5_picks (frozen before tip, graded the next morning), counted down by
# our model's probability. The opening card carries the running record so a bad stretch is on
# screen, not buried; each pick shows the market's number beside ours and one note each way.

def _speak_note(text, sport="nba"):
    import re
    m = re.match(r"^([A-Z]{2,3}) allow (.*)$", text)
    if m:
        text = f"{_cap(_tm(m.group(1), sport)[1])} allow {m.group(2)}"
    text = re.sub(r"\s*\([^)]*\)", "", text)          # the game-by-game list stays on screen, unread
    return text.replace(" · ", ", ")


def _pick_scene_nba(rank, pk, photo, credit, sport="nba"):
    word = {"Points": "points", "Rebounds": "rebounds", "Assists": "assists", "3-PT Made": "threes",
            "Pts+Rebs+Asts": "points, rebounds and assists"}.get(pk["stat_type"], pk["stat_type"].lower())
    m, k = pk["model_prob"] * 100, pk["market_prob"] * 100
    plus = next((t for kind, t in pk["notes"] if kind == "+"), "")
    risk = next((t for kind, t in pk["notes"] if kind == "!"), "")
    line = (f"Number {rank}. {pk['player']}, {pk['side']} {pk['line']:g} {word}, {_say_odds(pk['american_odds'])}. "
            f"{_speak_note(plus, sport)}. The market says {k:.0f} percent. Our model, {m:.0f}.")
    initials = "".join(p_[0] for p_ in pk["player"].replace(".", "").split()[:2]).upper()
    away, home = [x.strip() for x in pk["game"].split("@")]
    short = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3-PT Made": "3PM", "Pts+Rebs+Asts": "PRA",
             "Passing Yards": "PASS YDS", "Passing TDs": "PASS TD", "Rushing Yards": "RUSH YDS",
             "Receiving Yards": "REC YDS", "Receptions": "REC", "Completions": "CMP", "Interceptions": "INT",
             "Rushing Attempts": "CARRIES", "Pass Attempts": "ATT"}.get(pk["stat_type"], "")

    def draw(img, d, t, sc):
        pr = prog(t, 0.0, 0.25)
        put(img, hexplate(250, 170, ORANGE if rank == 1 else (14, 14, 16), WHITE, f"#{rank}", 130,
                          BLACK if rank == 1 else ORANGE), 820, 360, scale=2.2 - 1.2 * out_back(pr), alpha=min(1, pr * 3))
        pp = out_cubic(prog(t, 0.12, 0.5))
        put(img, photo_frame(photo, pk["team"], initials, 430, sport), -300 + pp * 630, 520, angle=(1 - pp) * 12)
        if credit:
            cl = layer(credit, 22, (200, 200, 205), head=False, stroke=2)
            put(img, cl, max(330, cl.width / 2 + 44), 770, alpha=prog(t, 0.5, 0.8))
        put(img, split_tile(away, home, 150, sport), 820, 560, scale=out_back(prog(t, 0.3, 0.55)))
        pn = out_cubic(prog(t, 0.35, 0.65))
        put(img, hexplate(980, 120, (14, 14, 16), ORANGE,
                          f"{pk['player'].upper()}  {pk['side'].upper()} {pk['line']:g} {short}",
                          66 if len(pk["player"]) < 16 else 54, WHITE), W / 2 + (1 - pn) * W, 860)
        put(img, layer(f"{pk['game_hdr'].upper()}  ·  {pk['american_odds']:+d}", 44, GOLD, stroke=4), W / 2, 955,
            alpha=prog(t, 0.6, 0.85))
        tk, tm = sc.cue("market", 0.6, last=False), sc.cue("model", 0.8, last=False)
        for x, lab, v, at, col in ((290, "THE MARKET", k, tk, WHITE), (W - 290, "OUR MODEL", m, tm, GREEN)):
            pc = out_back(prog(t, at, at + 0.25))
            put(img, layer(lab, 34, MUTED, stroke=3), x, 1015, alpha=prog(t, at, at + 0.2))
            put(img, hexplate(380, 120, (14, 14, 16), GOLD, f"{v:.0f}%", 84, col), x, 1090, scale=pc, alpha=min(1, pc * 2))
        put(img, layer("+ " + plus, 30, GREEN, head=False, stroke=3), W / 2, 1175, alpha=prog(t, 1.0, 1.3))
        put(img, layer("! " + risk, 30, GOLD, head=False, stroke=3), W / 2, 1215, alpha=prog(t, 1.4, 1.7))

    return Scene(line, draw, min_dur=5.0, tail=0.9,
                 sounds=lambda sc: [(0.02, "thud", 0.8), (0.15, "scratch", 0.4),
                                    (sc.cue("market", 0.6, last=False), "pop", 0.6),
                                    (sc.cue("model", 0.8, last=False), "pop", 0.6)],
                 moods=lambda sc: [(0.0, "point"), (1.0, "talk")])


def picks_short(day: datetime.date | None = None) -> pathlib.Path:
    """Tonight's top 5 NBA picks: frozen and logged first, then counted down."""
    global SPORT
    SPORT = "nba"
    import nba_top5_picks as tp
    day = day or datetime.date.today()
    picks = tp.log(day)
    if not picks:
        raise SystemExit(f"no top-5 picks for {day} (slate too thin, or already started)")
    tp.grade(day)
    rec = tp.record()
    if rec["n"]:
        sub = f"RECORD SO FAR: {rec['wins']}-{rec['losses']}  ·  GRADED EVERY MORNING"
        say_rec = f"So far, these picks are {rec['wins']} and {rec['losses']}."
        if rec["last"]:
            say_rec = f"Last night, {rec['last'][1]} of {rec['last'][2]}. " + say_rec
    else:
        sub, say_rec = "DAY ONE OF THE RECORD  ·  GRADED EVERY MORNING", "This is day one of the record."
    scenes = [_ordinal_scene_title("TOP 5 PICKS", "TONIGHT'S", "TOP 5", sub,
                                   f"Tonight's top 5 picks. Every one is logged before tip-off and graded tomorrow. "
                                   f"{say_rec}")]
    for rank in range(len(picks), 0, -1):
        pk = picks[rank - 1]
        ph, cr = player_photo(pk["player"], "nba")
        scenes.append(_pick_scene_nba(rank, pk, ph, cr))
    scenes.append(_cta([("ALL FIVE", WHITE, 0.15), ("GRADED", WHITE, 0.45), ("TOMORROW", ORANGE, 0.75)],
                       "KONJUREANALYTICS.COM", "21+  ·  NOT BETTING ADVICE",
                       "We grade all five tomorrow morning, wins and losses. Our model hasn't beaten the market yet, "
                       "and we'll show you either way. Twenty-one and over. Not betting advice."))
    out = OUT / f"short-nba-picks-{day.isoformat()}.mp4"
    render(scenes, f"TOP 5 PICKS  {day.month}/{day.day}", out, fmt="picks")
    out.with_suffix(".txt").write_text(
        f"Tonight's top 5 NBA picks, {day.strftime('%B')} {day.day}. Logged before tip-off, graded tomorrow.\n"
        + (f"Record so far: {rec['wins']}-{rec['losses']}.\n" if rec["n"] else "Day one of the record.\n") + "\n"
        + "".join(f"#{i + 1} {pk['title']} ({pk['american_odds']:+d}) - market {pk['market_prob'] * 100:.0f}%, "
                  f"our model {pk['model_prob'] * 100:.0f}%\n" for i, pk in enumerate(picks))
        + "\nPicks are props where our model and the market lean the same way and the matchup doesn't work against "
          "them. Our model has not beaten the market; treat these as research, not advice.\n"
          "Every pick, every result: https://konjureanalytics.com\n21+. Not betting advice. 1-800-GAMBLER.\n",
        encoding="utf-8")
    return out


def nfl_picks_short(day: datetime.date | None = None) -> pathlib.Path:
    """Sunday's top 5 NFL picks: the weekly board's five legs (saved Friday, graded Monday by
    weekly_picks) on that day's games, counted down, opening on the season's leg record."""
    global SPORT
    SPORT = "nfl"
    import json
    import nfl_analysis as nfl
    day = day or datetime.date.today()
    games = _sunday_games(day)
    if games.empty:
        raise SystemExit(f"no NFL games on {day}")
    week, season = int(games["week"].iloc[0]), int(games["season"].iloc[0])
    allw = json.loads((ROOT / "weekly_picks.json").read_text(encoding="utf-8"))
    e = allw.get(f"{season}-{week:02d}") or {}
    legs = e.get("parlay") or e.get("parlay_withheld") or []
    label = {(r.away_team, r.home_team) for r in games.itertuples()}
    teams = {t for pair in label for t in pair}
    wk = nfl._load_weekly(season)
    wk = wk.assign(_k=wk["player_display_name"].map(nfl._name_key))
    picks = []
    for l in legs:
        me = wk[wk["_k"] == nfl._name_key(l["player"])].sort_values("week")
        if me.empty or str(me["team"].iloc[-1]) not in teams:
            continue
        team = str(me["team"].iloc[-1])
        away, home = next(pair for pair in label if team in pair)
        col = nfl.PROP_STATS.get(l["stat_type"], (None,))[0]
        vals = [int(v) for v in me[col].fillna(0).tail(6)] if col in me else []
        over = l["side"] == "over"
        hits = sum((v > l["line"]) if over else (v < l["line"]) for v in vals)
        m = l.get("matchup") or {}
        notes = []
        if m and m.get("tilt", 0) >= 0:
            notes.append(("+", f"{m['opp']} allow {m['allowed']:.1f} to {m['role']}s against a {m['avg']:.1f} average"))
        if vals:
            notes.append(("+" if hits >= len(vals) / 2 else "!",
                          f"{'Over' if over else 'Under'} in {hits} of his last {len(vals)} ({', '.join(map(str, vals))})"))
        if m and m.get("tilt", 0) < 0:
            notes.append(("!", f"{m['opp']} allow {m['allowed']:.1f} to {m['role']}s, more than the {m['avg']:.1f} average"))
        notes.append(("!", "Our model hasn't beaten the market: research, not a sure thing"))
        side = "over" if over else "under"
        picks.append({"player": l["player"], "stat_type": l["stat_type"], "line": float(l["line"]), "side": side,
                      "american_odds": int(l["american_odds"]), "team": team, "game": f"{away} @ {home}",
                      "game_hdr": f"{away} @ {home}  ·  SUNDAY", "notes": notes,
                      "model_prob": float(l.get("model_prob") or l.get("blended_prob")),
                      "market_prob": float(l.get("implied_prob") or l.get("blended_prob"))})
    if len(picks) < 3:
        raise SystemExit(f"{day}: fewer than 3 board picks on Sunday's games")
    picks.sort(key=lambda r: -r["model_prob"])
    done = [x for w_ in allw.values() for x in (w_.get("parlay") or w_.get("parlay_withheld") or [])
            if x.get("outcome") in ("win", "loss")]
    wins = sum(x["outcome"] == "win" for x in done)
    sub = (f"SEASON RECORD: {wins}-{len(done) - wins}  ·  GRADED MONDAY" if done else "GRADED MONDAY")
    say = f"So far this season, these picks are {wins} and {len(done) - wins}." if done else ""
    scenes = [_ordinal_scene_title("TOP 5 PICKS", "SUNDAY'S", "TOP 5", sub,
                                   f"Sunday's top 5 picks. Every one was logged before kickoff and gets graded on "
                                   f"Monday. {say}")]
    for rank in range(len(picks), 0, -1):
        pk = picks[rank - 1]
        ph, cr = player_photo(pk["player"], "nfl")
        scenes.append(_pick_scene_nba(rank, pk, ph, cr, sport="nfl"))
    scenes.append(_cta([("ALL FIVE", WHITE, 0.15), ("GRADED", WHITE, 0.45), ("ON MONDAY", ORANGE, 0.75)],
                       "KONJUREANALYTICS.COM", "21+  ·  NOT BETTING ADVICE",
                       "We grade all five on Monday, wins and losses. Our model hasn't beaten the market yet, and we'll "
                       "show you either way. Twenty-one and over. Not betting advice."))
    SPORT = "nfl"
    out = OUT / f"short-nfl-picks-{day.isoformat()}.mp4"
    render(scenes, f"NFL TOP 5 PICKS  WEEK {week}", out, fmt="nfl-picks")
    SPORT = "nba"
    out.with_suffix(".txt").write_text(
        f"Sunday's top 5 NFL picks, Week {week}. Saved before kickoff, graded Monday.\n"
        + (f"Season record of these picks: {wins}-{len(done) - wins}.\n" if done else "") + "\n"
        + "".join(f"#{i + 1} {pk['player']} {pk['side']} {pk['line']:g} {pk['stat_type']} ({pk['american_odds']:+d}) - "
                  f"market {pk['market_prob'] * 100:.0f}%, our model {pk['model_prob'] * 100:.0f}%\n"
                  for i, pk in enumerate(picks))
        + "\nThe five likeliest legs on our weekly board, after the matchup veto. Our model has not beaten the "
          "market; treat these as research, not advice.\nhttps://konjureanalytics.com\n21+. Not betting advice. "
          "1-800-GAMBLER.\n", encoding="utf-8")
    return out


FORMATS = {"results": results_short, "top5": top5_short, "gtg": gtg_short, "btm": btm_short,
           "nfl-td": nfl_td_short, "nfl-results": nfl_results_short, "nfl-top5": nfl_top5_short,
           "picks": picks_short, "nfl-picks": nfl_picks_short}


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "clone-voice":
        print("voice id saved:", clone_voice(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] in FORMATS:
        n = datetime.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None
        print(FORMATS[sys.argv[1]](n))
    else:
        print(__doc__)
