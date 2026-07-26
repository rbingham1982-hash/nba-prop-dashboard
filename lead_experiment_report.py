"""
Snapshot of the lead-time experiment (early vs late picks), written to
logs/lead_experiment_check.log. Driven by the KonjureLeadExperimentCheck
scheduled task ~2 weeks after the flag shipped (2026-07-26), so the prospective
early-vs-late comparison is captured once it has data. Safe to run manually too.
"""
import os
import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import parlay_tracker as pt

lines = [f"Lead-Time Experiment check — {datetime.datetime.now().isoformat(timespec='seconds')}"]
for sport in ("MLB", "WNBA"):
    r = pt.get_lead_time_experiment(sport=sport)
    lines.append(f"\n[{sport}] threshold {r['threshold_hours']}h")
    for cohort in ("early", "late"):
        s = r[cohort]
        if s["avg_clv"] is not None:
            clv = f"CLV {s['avg_clv']*100:+.2f}pts ({s['pct_positive']*100:.0f}% beat close, n={s['n_clv']})"
        else:
            clv = f"CLV — (n={s['n_clv']})"
        if s["hit_rate"] is not None:
            realized = (f"hit {s['hit_rate']*100:.0f}% vs impl {s['implied']*100:.0f}% "
                        f"({(s['hit_rate']-s['implied'])*100:+.0f}pts, n={s['n_resolved']})")
        else:
            realized = f"realized — (n={s['n_resolved']})"
        lines.append(f"  {cohort:<6} {clv:<48} | {realized}")

verdict = ("\n  READ: does early beat late on BOTH CLV and realized? If yes and samples are "
           "adequate (n>=30/cohort), the lead-time edge is holding prospectively.")
report = "\n".join(lines) + verdict
print(report)
os.makedirs("logs", exist_ok=True)
with open("logs/lead_experiment_check.log", "a", encoding="utf-8") as f:
    f.write(report + "\n" + "=" * 62 + "\n")
