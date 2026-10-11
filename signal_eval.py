"""
signal_eval.py — two candidate signals the market might not fully price, tested against
graded results. Read-only. Run: venv/Scripts/python.exe signal_eval.py

Context: by 2026-10-11 the model's own disagreements with the price had realized 2-5
points BELOW that price in every sport, and get_market_blend gave the model zero weight.
So the open question is whether anything we observe that the price does not already
contain predicts results. These are the two candidates the log can test without new data. FanDuel legs are the bets; everything is measured as realized hit rate minus
the FanDuel price we would have paid (positive = beat the price).

A. Cross-book: Underdog quoted the same prop (player, stat, line, side, game day) at a
   different de-vigged probability. If Underdog is sometimes ahead of FanDuel, FanDuel
   legs that Underdog prices HIGHER should beat their FanDuel price.

B. Movement: FanDuel's own price moved between entry and a later snapshot. If the market
   under-reacts, legs that moved toward the side should beat even the LATER price.

First run, 2026-10-11: nothing consistent. Cross-book pointed opposite ways in MLB
(Underdog lower >3 -> -7.3, z=-2.2) and WNBA (Underdog higher >3 -> -11.4, z=-2.4);
MLB legs that moved EITHER way beat the later price, which reads as a measurement issue
in MLB's one-sided markets rather than a signal; WNBA "moved toward" was -15.7 (z=-3.0).
Twelve cells with contradictory signs: chance. Fixed hypotheses for the next run (NBA,
once each cell has n >= 300): (1) FanDuel legs Underdog prices >3 points LOWER realize
below the FanDuel price; (2) legs that moved >2 points toward the side do NOT beat the
later price. Anything else this prints is exploration, not confirmation.
"""
import sys, collections, math
sys.stdout.reconfigure(encoding="utf-8")
import parlay_tracker as pt

data = pt._load()


def key(leg):
    return (str(leg.get("player_name", "")).lower(), leg.get("stat_type"),
            float(leg.get("line_score") or 0), str(leg.get("side") or "over"),
            str(leg.get("start_time") or "")[:10])


fd, ud = {}, {}
for p in sorted(data["parlays"], key=lambda p: str(p.get("generated_at") or "")):
    book = p.get("sportsbook")
    if book not in ("FanDuel", "Underdog"):
        continue
    for leg in p["legs"]:
        if not leg.get("implied_prob"):
            continue
        k = (p.get("sport"),) + key(leg)
        target = fd if book == "FanDuel" else ud
        target.setdefault(k, leg)          # earliest quote wins


def report(title, groups):
    print(f"\n== {title}")
    for g in sorted(groups, key=str):
        rows = [(h, price) for h, price in groups[g] if h in (True, False)]
        if len(rows) < 30:
            print(f"  {str(g):28s} n={len(rows)} (too few)")
            continue
        n = len(rows)
        hit = sum(h for h, _ in rows) / n
        pr = sum(x for _, x in rows) / n
        se = math.sqrt(hit * (1 - hit) / n)
        print(f"  {str(g):28s} n={n:5d} hit={100*hit:5.1f} price={100*pr:5.1f} "
              f"beat={100*(hit-pr):+5.2f} (z={(hit-pr)/se:+.1f})")


# A. cross-book disagreement at entry
a = collections.defaultdict(list)
for k, leg in fd.items():
    u = ud.get(k)
    if u is None:
        continue
    gap = float(u["implied_prob"]) - float(leg["implied_prob"])
    b = ("UD higher >3" if gap > .03 else "UD lower >3" if gap < -.03 else "within 3")
    a[(k[0], b)].append((pt._leg_hit(leg), float(leg["implied_prob"])))
report("A. Underdog vs FanDuel at entry (bet the FanDuel price)", a)

# B. FanDuel movement, measured against the LATER price (what you could still get)
bm = collections.defaultdict(list)
for k, leg in fd.items():
    c = leg.get("closing_implied")
    if c is None or not leg.get("closing_side_keyed") and not leg.get("closing_repaired") \
            and str(leg.get("side") or "over") == "over" and k[0] != "MLB":
        continue                            # unrepaired two-way over close: unreliable
    move = float(c) - float(leg["implied_prob"])
    b = "moved toward >2" if move > .02 else "moved away >2" if move < -.02 else "flat"
    bm[(k[0], b)].append((pt._leg_hit(leg), float(c)))
report("B. FanDuel movement, graded against the later price", bm)
