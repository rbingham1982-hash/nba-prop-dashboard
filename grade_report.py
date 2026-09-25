"""
grade_report.py — grade the week's boards and write down what happened.

weekly_picks.grade() already runs inside every newsletter build, so this does not exist to
make grading happen. It exists so the results are readable on the Tuesday after the games
rather than on the Friday the next issue goes out, and so the receiving slate — the board
with an actual open question attached — gets looked at properly instead of as one more row
in a record table.

Writes to logs/grade_report.log and prints the same thing, so it works equally as a
scheduled task and as something run by hand.

    python grade_report.py            # the most recent week that has results
    python grade_report.py 2026 3     # a specific week
"""
from __future__ import annotations

import datetime
import pathlib
import sys

LOG = pathlib.Path(__file__).with_name("logs") / "grade_report.log"


def _fmt_boards(entry: dict) -> list:
    """Every board, with what it said and what happened."""
    L = []
    ats = entry.get("ats") or []
    if ats:
        w = sum(1 for r in ats if r.get("outcome") == "win")
        l = sum(1 for r in ats if r.get("outcome") == "loss")
        L.append(f"ATS  {w}-{l}")
        for r in ats:
            L.append(f"    {r['game']:<12} {r['pick_label']:<11} "
                     f"{str(r.get('outcome') or 'pending'):<8} margin {r.get('result')}")

    legs = entry.get("parlay") or []
    if legs:
        w = sum(1 for r in legs if r.get("outcome") == "win")
        l = sum(1 for r in legs if r.get("outcome") == "loss")
        hit = legs and all(r.get("outcome") == "win" for r in legs)
        L.append(f"PARLAY  {w}-{l}" + ("  — HIT" if hit else "  — did not hit"))
        for r in legs:
            L.append(f"    {r['player']:<22} {r['stat_type']:<16} "
                     f"{str(r['side']).upper():<5} {r['line']:<6g} "
                     f"actual {str(r.get('actual')):<7} "
                     f"{str(r.get('outcome') or 'pending')}")

    td = entry.get("td") or []
    if td:
        w = sum(1 for r in td if r.get("outcome") == "win")
        l = sum(1 for r in td if r.get("outcome") == "loss")
        exp = sum(float(r.get("blended_prob") or 0) for r in td
                  if r.get("outcome") in ("win", "loss"))
        L.append(f"TOUCHDOWN BOARD  {w}-{l}   (expected {exp:.1f} of {w + l})")
        for r in td:
            L.append(f"    {r['player']:<22} {float(r.get('blended_prob') or 0):.0%}  "
                     f"{str(r.get('outcome') or 'pending')}")
    return L


def _receivers(entry: dict) -> list:
    """
    The receiving slate, read as the test it was logged to be.

    The slate exists because the touchdown board ranks by probability and a lead back
    outranks every receiver alive, so p_rec had never been graded as the dominant term.
    The question is not the hit rate — five players settle nothing — it is whether the
    model's DISAGREEMENTS with the book landed, because those are the only rows carrying
    information. Split accordingly.
    """
    rows = entry.get("receivers") or []
    if not rows:
        return ["RECEIVING SLATE  — none logged for this week"]
    graded = [r for r in rows if r.get("outcome") in ("win", "loss")]
    w = sum(1 for r in graded if r["outcome"] == "win")
    exp = sum(float(r.get("blended_prob") or 0) for r in graded)
    L = [f"RECEIVING SLATE  {w}-{len(graded) - w}   "
         f"(expected {exp:.1f} of {len(graded)})"]
    for r in rows:
        edge = float(r.get("edge") or 0)
        lean = "MODEL HIGH" if edge > 0.05 else ("MODEL LOW" if edge < -0.05 else "agrees")
        L.append(f"    {r['player']:<22} {str(r.get('position') or ''):<3} "
                 f"model {float(r.get('model_prob') or 0):.0%}  "
                 f"book {float(r.get('fair_prob') or 0):.0%}  "
                 f"{lean:<11} {str(r.get('outcome') or 'pending')}")

    # The rows where the model actually said something different from the market.
    hi = [r for r in graded if float(r.get("edge") or 0) > 0.05]
    lo = [r for r in graded if float(r.get("edge") or 0) < -0.05]
    if hi or lo:
        L.append("")
        L.append("    where the model disagreed with the book:")
        if hi:
            k = sum(1 for r in hi if r["outcome"] == "win")
            L.append(f"      model HIGHER than book: {k}/{len(hi)} scored "
                     f"(model liked them, book did not)")
        if lo:
            k = sum(1 for r in lo if r["outcome"] == "win")
            L.append(f"      model LOWER than book:  {k}/{len(lo)} scored "
                     f"(model faded them, book did not)")
        L.append("      One week of this decides nothing. It is logged so that twenty "
                 "weeks of it can.")
    return L


def _wind(entry: dict) -> list:
    """
    The wind board, reported on the residual first.

    The continuous effect is the established one (-0.389 points of total per mph, p=0.0004,
    replicated out of sample). The under/over record is NOT established and needs about 44
    seasons to become so, which is why it comes second and small.
    """
    rows = entry.get("wind") or []
    if not rows:
        return ["WIND BOARD  — none logged for this week"]
    graded = [r for r in rows if r.get("residual") is not None]
    L = ["WIND BOARD"]
    for r in rows:
        L.append(f"    {r['game']:<12} {float(r.get('wind_mph') or 0):>5.1f}mph  "
                 f"total {float(r['total_line']):>5.1f}  "
                 f"expected {float(r.get('expected_adj') or 0):+6.2f}  "
                 f"actual {str(r.get('actual_total') or '—'):>5}  "
                 f"residual {('%+.2f' % r['residual']) if r.get('residual') is not None else '—':>7}  "
                 f"{str(r.get('outcome') or 'pending')}")
    if graded:
        mean = sum(float(r["residual"]) for r in graded) / len(graded)
        exp = sum(float(r.get("expected_adj") or 0) for r in graded) / len(graded)
        u = sum(1 for r in graded if r.get("outcome") == "under")
        L.append("")
        L.append(f"    mean residual {mean:+.2f} against {exp:+.2f} expected "
                 f"— THIS is the number that matters")
        L.append(f"    under/over {u}-{len(graded) - u} (secondary; unproven either way)")
    return L


def report(season: int | None = None, week: int | None = None) -> str:
    import weekly_picks as wp
    wp.grade()
    data = wp._load()
    if season is None or week is None:
        # The latest week carrying any graded row, which after a Sunday is the week just
        # played and before one is the week before it.
        cand = [(e.get("season"), e.get("week"), e) for e in data.values()
                if any(r.get("outcome") for k in ("ats", "parlay", "td")
                       for r in (e.get(k) or []))]
        if not cand:
            return "nothing graded yet"
        season, week, entry = sorted(cand)[-1]
    else:
        entry = data.get(wp._key(season, week))
        if not entry:
            return f"no entry for {season} week {week}"

    pend = sum(1 for k in ("ats", "parlay", "td", "receivers", "wind")
               for r in (entry.get(k) or []) if not r.get("outcome"))
    L = ["=" * 72,
         f"Week {week} of {season} — graded {datetime.datetime.now():%Y-%m-%d %H:%M}",
         "=" * 72, ""]
    if pend:
        # Said loudly, because a partial week read as a final one is how a record acquires
        # a row that was never true.
        L += [f"*** {pend} rows still ungraded — the week is not complete. "
              f"Treat everything below as provisional. ***", ""]
    L += _fmt_boards(entry) + [""] + _receivers(entry) + [""] + _wind(entry) + [""]

    rec = wp.record()

    def _pc(v):
        """A board with nothing in it prints a dash, not the word None."""
        return "—" if v is None else f"{v}%"

    L += ["-" * 72, "SEASON TO DATE",
          f"  ATS            {rec.get('ats_record')} ({_pc(rec.get('ats_pct'))})  "
          f"break-even {rec.get('ats_breakeven')}%, backtest {rec.get('ats_backtest_pct')}%",
          f"  Parlay legs    {rec.get('leg_record')} ({_pc(rec.get('leg_pct'))})",
          f"  Parlays        {rec.get('parlays_hit')} of {rec.get('parlays_settled')} hit",
          f"  TD board       {rec.get('td_record')} ({_pc(rec.get('td_pct'))}) vs "
          f"{_pc(rec.get('td_expected_pct'))} expected",
          f"  Receiving      {rec.get('rec_record')} ({_pc(rec.get('rec_pct'))}) vs "
          f"{_pc(rec.get('rec_expected_pct'))} expected",
          f"  Wind residual  {rec.get('wind_mean_residual') if rec.get('wind_n') else '—'} "
          f"over {rec.get('wind_n')} games "
          f"(expected {rec.get('wind_expected_residual')} at "
          f"{rec.get('wind_threshold')}mph+)", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    s = int(args[0]) if len(args) > 0 else None
    w = int(args[1]) if len(args) > 1 else None
    text = report(s, w)
    print(text)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")
