"""Render the two Instagram cards for Sunday's slate."""
import sys, pathlib
sys.path.insert(0, r"C:\Users\rbing\nba-prop-dashboard")
sys.stdout.reconfigure(encoding="utf-8")
from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(r"C:\Users\rbing\nba-prop-dashboard\newsletter_out")
OUT.mkdir(exist_ok=True)
W, H = 1080, 1350
BG, PANEL = (13, 18, 28), (20, 27, 40)
FG, MUTED = (237, 241, 247), (140, 152, 170)
ACC, POS, NEG = (122, 162, 247), (109, 199, 145), (224, 122, 108)


def f(sz, bold=True):
    for n in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(n, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def frame(title, kicker):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 232], fill=PANEL)
    d.line([(0, 232), (W, 232)], fill=ACC, width=4)
    d.text((60, 52), "KONJURE ANALYTICS", font=f(28), fill=ACC)
    d.text((58, 96), title, font=f(72), fill=FG)
    d.text((60, 180), kicker, font=f(28, False), fill=MUTED)
    return img, d


def footer(d, line1, line2):
    d.line([(60, H - 150), (W - 60, H - 150)], fill=(44, 56, 76), width=2)
    d.text((60, H - 124), line1, font=f(25, False), fill=MUTED)
    d.text((60, H - 88), line2, font=f(25, False), fill=MUTED)


# ---------------- card 1: ATS ----------------
ats = [
    ("BAL @ IND", "IND +3.5", "+7.4"),
    ("DAL @ NYG", "NYG +3.0", "+5.6"),
    ("WAS @ PHI", "PHI -6.0", "+5.2"),
    ("MIA @ LV",  "MIA +3.0", "+4.8"),
    ("TB @ CIN",  "TB +3.5",  "+4.3"),
]
img, d = frame("SUNDAY ATS", "Week 1  ·  where the model disagrees most with the line")
y = 300
d.text((60, y), "MATCHUP", font=f(24), fill=MUTED)
d.text((470, y), "MODEL SIDE", font=f(24), fill=MUTED)
rt = "EDGE vs LINE"
d.text((W - 60 - d.textlength(rt, font=f(24)), y), rt, font=f(24), fill=MUTED)
y += 52
d.line([(60, y), (W - 60, y)], fill=(44, 56, 76), width=2)
y += 34
for m, pick, edge in ats:
    d.text((60, y), m, font=f(40, False), fill=FG)
    d.text((470, y), pick, font=f(40), fill=FG)
    t = edge + " pts"
    d.text((W - 60 - d.textlength(t, font=f(40)), y), t, font=f(40), fill=ACC)
    y += 94
footer(d, "Edge = model's projected margin minus the posted spread.",
       "Model output, not advice. Backtests near 50% ATS — read as analysis.")
p1 = OUT / "2026-09-13-card-ats.jpg"
img.convert("RGB").save(p1, "JPEG", quality=92, optimize=True)

# ---------------- card 2: parlay ----------------
legs = [
    ("Dalton Schultz",  "Rec Yds",  "OVER 33.5",   "-114"),
    ("Kayshon Boutte",  "Receptions", "OVER 1.5",  "-172"),
    ("James Cook",      "Rush Yds", "OVER 74.5",   "-114"),
    ("Justin Herbert",  "Pass TDs", "UNDER 1.5",   "+154"),
    ("Geno Smith",      "Pass Yds", "UNDER 212.5", "-114"),
]
img, d = frame("5-LEG PARLAY", "Five players  ·  five different stat categories")
y = 296
for name, cat, line, odds in legs:
    d.rounded_rectangle([56, y, W - 56, y + 132], radius=10, fill=PANEL)
    d.text((80, y + 20), name, font=f(40), fill=FG)
    d.text((80, y + 74), cat.upper(), font=f(26), fill=ACC)
    wl = d.textlength(line, font=f(36))
    d.text((W - 84 - wl, y + 22), line, font=f(36), fill=FG)
    wo = d.textlength(odds, font=f(30, False))
    d.text((W - 84 - wo, y + 76), odds, font=f(30, False), fill=MUTED)
    y += 150
d.text((60, y + 8), "PAYOUT", font=f(26), fill=MUTED)
d.text((60, y + 44), "+2557", font=f(56), fill=POS)
rt = "MODEL PROBABILITY"
d.text((W - 60 - d.textlength(rt, font=f(26)), y + 8), rt, font=f(26), fill=MUTED)
pv = "10.9%"
d.text((W - 60 - d.textlength(pv, font=f(56)), y + 44), pv, font=f(56), fill=FG)
footer(d, "Raw model probability. Blended against the book it prices near 3%.",
       "A 5-leg parlay is a longshot by construction. Entertainment, not a system.")
p2 = OUT / "2026-09-13-card-parlay.jpg"
img.convert("RGB").save(p2, "JPEG", quality=92, optimize=True)
print(p1.name, round(p1.stat().st_size / 1024), "KB")
print(p2.name, round(p2.stat().st_size / 1024), "KB")
