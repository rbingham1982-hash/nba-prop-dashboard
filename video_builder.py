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


def split_tile(away, home, size=210):
    """Diagonal split square in the two teams' colours: the matchup without anyone's logo."""
    k = ("split", away, home, size)
    if k not in _LAYERS:
        ca, ch = TEAM.get(away, (ORANGE, ""))[0], TEAM.get(home, (GOLD, ""))[0]
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
    size = 92
    f = F(size)
    txt = [w[2].upper() for w in cur]
    widths = [d.textlength(x, font=F(int(size * 1.15))) for x in txt]
    gap = 20
    x = (W - sum(widths) - gap * (len(txt) - 1)) / 2
    for (s, e, _), word, wd in zip(cur, txt, widths):
        active = s <= t < max(e, s + 0.18)
        if active:
            pop = out_back(prog(t, s, s + 0.14))
            put(img, layer(word, int(size * 1.15), GOLD, stroke=9), x + wd / 2, y + 52, scale=0.85 + 0.15 * pop)
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


def _tts(text: str) -> tuple[np.ndarray, list]:
    """(mono float audio at SR, [(start_s, end_s, word)]). Cached on disk; the free service
    drops requests in bursts, so it backs off rather than hammering."""
    import edge_tts
    import hashlib
    import json

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


def anchor_sprite(t, mood, prev, since, mouth, size=340):
    k = ("anchor_src", size)
    if k not in _LAYERS:
        im = Image.open(_anchor_path()).convert("RGB").crop(_ANCHOR_CROP)
        _LAYERS[k] = im.resize((size + 40, size + 40), Image.LANCZOS)
    src = _LAYERS[k]
    ox = 20 + 14 * math.sin(t * 0.31)                        # slow camera drift
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


# ── scenes + render ───────────────────────────────────────────────────────────────────────

class Scene:
    """narration + draw(img, d, t, sc). After voicing: .audio, .words, .dur, and .cue(word)
    for the time a word is spoken, so animation can land on it."""

    def __init__(self, narration, draw, min_dur=0.0, tail=0.45, sounds=None, shake_at=None, moods=None):
        self.narration, self.draw, self.min_dur, self.tail = narration, draw, min_dur, tail
        self.sounds = sounds or (lambda sc: [])
        self.shake_at = shake_at
        self.moods = moods or (lambda sc: [(0.0, "talk")])     # the host's script for this scene

    def voice(self):
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
        hits = [w[0] for w in self.words if w[2].strip(".,").lower() == word.lower()]
        return (hits[-1] if last else hits[0]) if hits else self.dur * default

    def shake(self, t):
        if self.shake_at is None:
            return 0.0
        at = self.shake_at(self)
        return 14 * math.exp(-(t - at) * 9) if t >= at else 0.0


def render(scenes: list, title: str, out: pathlib.Path) -> pathlib.Path:
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
        if HOST:
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
    name = TEAM.get(team, (None, team))[1]
    num = line.lstrip("+-").replace(".5", " and a half")
    return f"{name} {'plus' if line.startswith('+') else 'minus'} {num}"


def _cap(t):
    return t[:1].upper() + t[1:]


def _pick_facts(g):
    pick_home = g["pick"] == "home"
    mine = g["home_pts"] if pick_home else g["away_pts"]
    theirs = g["away_pts"] if pick_home else g["home_pts"]
    team = TEAM.get(g["home"] if pick_home else g["away"], (None, ""))[1]
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


def _title_scene(wins, losses, outcomes):
    w_l = f"{wins}-{losses}"
    col = GREEN if wins > losses else GOLD if wins == losses else RED
    at = lambda sc: sc.cue(str(wins), 0.35, last=False)

    def draw(img, d, t, sc):
        p1 = out_cubic(prog(t, 0.05, 0.4))
        put(img, layer("LAST NIGHT'S", 150, WHITE, stroke=6), -500 + p1 * (W / 2 + 500), 380)
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
        put(img, layer(f"{len(outcomes)} SPREAD PICKS  ·  LOGGED BEFORE TIP", 46, GOLD, stroke=3),
            W / 2, 1200, alpha=prog(t, 0.6, 1.0))

    def sounds(sc):
        ts = at(sc)
        return ([(0.04, "scratch", 0.6), (ts, "thud", 1.0)]
                + [(ts + 0.4 + 0.18 * i, "pop", 0.6) for i in range(len(outcomes))])

    react = "celebrate" if wins > losses else "facepalm" if wins < losses else "shrug"
    return Scene(f"We went {wins} and {losses} last night. Every pick was logged before tip-off. "
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
    w_, l_ = (int(x) for x in rec["record"].split("-"))
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


def _cta_scene():
    lines = [("EVERY PICK", WHITE, 0.15), ("LOGGED BEFORE TIP", WHITE, 0.45), ("GRADED IN PUBLIC", ORANGE, 0.8)]

    def draw(img, d, t, sc):
        for k, (txt, col, at) in enumerate(lines):
            p = out_back(prog(t, at, at + 0.3))
            put(img, layer(txt, 130, col, stroke=7), W / 2, 430 + k * 165, scale=0.5 + 0.5 * p, alpha=min(1, p * 2))
        pu = prog(t, 1.2, 1.5)
        put(img, hexplate(860, 130, GOLD, WHITE, "KONJUREANALYTICS.COM", 74, BLACK), W / 2, 1010,
            scale=(1 + 0.04 * math.sin(t * 6)) * out_back(pu), alpha=pu)
        pf = out_back(prog(t, 1.8, 2.1))
        put(img, layer("FOLLOW FOR TOMORROW'S RESULTS", 62, GOLD, stroke=5), W / 2, 1180, scale=pf, alpha=pf)

    def sounds(sc):
        return [(at, "pop", 0.6) for _, _, at in lines] + [(1.2, "thud", 0.6), (1.8, "pop", 0.6)]

    return Scene("Every pick, every result, at Konjure Analytics dot com. Follow to see tonight's picks graded "
                 "tomorrow. Not betting advice.", draw, min_dur=4.5, tail=0.8, sounds=sounds,
                 moods=lambda sc: [(0.0, "point"), (2.0, "talk")])


def results_short(night: datetime.date | None = None) -> pathlib.Path:
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
    return render(scenes, title, OUT / f"short-nba-results-{night.isoformat()}.mp4")


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


def player_photo(name: str):
    """(PIL image, credit) for a free-licence photo of `name`, or (None, None). Cached."""
    import hashlib
    import html
    import json
    import re
    cache = pathlib.Path.home() / ".konjure" / "photos"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(name.lower().encode()).hexdigest()[:16]
    meta_p, img_p = cache / f"{key}.json", cache / f"{key}.jpg"
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        if not meta.get("ok"):
            return None, None
        return Image.open(img_p).convert("RGB"), meta["credit"]
    api = "https://en.wikipedia.org/w/api.php"
    meta = {"ok": False}
    found = _wget(api, {"action": "query", "list": "search", "srsearch": f'"{name}" basketball', "srlimit": 6,
                        "format": "json"}) or {}
    for hit in found.get("query", {}).get("search", []):
        title = hit["title"]
        if title.split(" (")[0].lower() != name.lower():
            continue
        time.sleep(1)
        pg = _wget(api, {"action": "query", "titles": title, "prop": "pageimages|extracts", "piprop": "name",
                         "exintro": 1, "explaintext": 1, "exsentences": 2, "format": "json"}) or {}
        page = next(iter(pg.get("query", {}).get("pages", {}).values()), {})
        if "basketball" not in page.get("extract", "").lower() or "pageimage" not in page:
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


def photo_frame(img, team, initials, size=430):
    """The photo cropped square toward the top (where faces are), in a team-colour frame.
    Without a photo, the player's initials on his team colour."""
    k = ("photo", id(img), team, initials, size)
    if k in _LAYERS:
        return _LAYERS[k]
    col = TEAM.get(team, (ORANGE, ""))[0]
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


def _team_name(abbr):
    return TEAM.get(abbr, (None, abbr))[1]


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
    render(scenes, f"TOP 5 PLAYERS  {night.month}/{night.day}", out)
    credits = [f"{r['PLAYER_NAME']}: {c}" for r, (_, c) in zip(rows, shots) if c]
    desc = (f"The five best NBA players on {night.strftime('%B')} {night.day}, graded 0-100 against every "
            f"player-game from last season.\n\n"
            + "".join(f"#{i + 1} {r['PLAYER_NAME']} ({r['TEAM_ABBREVIATION']}) - {r['grade']}, "
                      f"{float(r['score']):.1f}: {r['PTS']} pts, {r['REB']} reb, {r['AST']} ast\n"
                      for i, r in enumerate(rows))
            + "\nEvery player graded every night: https://konjureanalytics.com\n21+. Not betting advice.\n"
            + ("\nPhoto credits (Wikimedia Commons):\n" + "\n".join(credits) + "\n" if credits else ""))
    out.with_suffix(".txt").write_text(desc, encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("results", "top5"):
        n = datetime.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None
        print((results_short if sys.argv[1] == "results" else top5_short)(n))
    else:
        print(__doc__)
