"""
Daily strong-edge pocket-day alert. Scans today's logged pocket-market props (K's + WNBA
reb/ast) and reports any that clear both a strong-edge bar and an odds floor — the only days
worth a straight bet. Writes to logs/pocket_alert.log. Safe to run manually or on a schedule
(after the daily parlay builder, so today's slate is already logged).
"""
import os
import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import parlay_tracker as pt

a = pt.get_pocket_alert()   # all sports, today
stamp = datetime.datetime.now().isoformat(timespec="seconds")
lines = [f"Pocket-day alert — {stamp} — date {a['date']}",
         f"  threshold: edge >= {a['min_edge']*100:.0f}%  AND  odds >= {a['min_odds']:+d}",
         f"  pocket props scanned: {a['n_pocket']}"]
if a["qualifies"]:
    lines.append(f"  ** STRONG-EDGE DAY ** {len(a['plays'])} qualifying play(s):")
    for p in a["plays"]:
        lines.append(f"    {p['edge']*100:+5.1f}% edge  {p['player']:<22} {p['stat']:<10} "
                     f"o{p['line']} ({p['odds']:+d}) {p['book']:<9} {p['game']}")
else:
    lines.append("  no qualifying plays — dry board, skip today.")

report = "\n".join(lines)
print(report)
os.makedirs("logs", exist_ok=True)
with open("logs/pocket_alert.log", "a", encoding="utf-8") as f:
    f.write(report + "\n" + "=" * 62 + "\n")
