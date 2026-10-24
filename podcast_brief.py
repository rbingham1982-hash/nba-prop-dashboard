"""
podcast_brief.py — the weekly episode brief for The Opportunity Report podcast.

One page to record from: the running order with times, the talking points with every
number filled in, the honest record (misses included), the Guess the Grade segment with its
challenge link and an answer sheet, copy-paste show notes and clip ideas.

Built from the same data as the Tuesday issue (newsletter.build_sections), so what is said
on air matches what was published. publish.run writes it beside the issue for the tuesday
and full editions; the weekly-issue workflow attaches it to the run and prints it on the
run's summary page.

    python podcast_brief.py        # build the week's issue data and write the brief

Brand rule, the same as everywhere: tools and grades, never "locks". The record is read
out in full, and the brief says so where a host might be tempted to skip it.
"""
from __future__ import annotations

import datetime
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "newsletter_out"
SITE = "https://konjureanalytics.com"
SUBSCRIBE = "https://konjureanalytics.beehiiv.com/subscribe"
RESPONSIBLE = ("Informational only, not betting advice. 21+. If you or someone you know has a "
               "gambling problem, call 1-800-GAMBLER.")


def _f(v, fmt="{:.0f}") -> str:
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return "—"


def _rec(r: dict) -> str:
    return f"{r['player']} ({r['position']}, {r['team']}): {r['grade']} {_f(r['score'])}, {r['line']}"


def build(data: dict, day: datetime.date | None = None) -> str:
    """The brief as markdown, from newsletter.build_sections() output."""
    import games
    day = day or datetime.date.today()
    rc = data.get("recap") or {}
    rp = data.get("recap_picks") or {}
    pr = data.get("picks_record") or {}
    week = rc.get("week")
    title = f"Week {week} graded" if week else f"{day:%b %d}"
    L = [f"# Episode brief: The Opportunity Report · {title}", "",
         f"*Recording sheet for {day:%A %B %d, %Y}. Target 20–25 minutes. Every number below is from "
         "the published issue, so the show and the newsletter agree.*", ""]

    # ── running order ──
    seg = [("0:00", "Cold open", "1 min"), ("1:00", "The report card", "6 min"),
           ("7:00", "The honest record", "4 min"), ("11:00", "Guess the Grade, on air", "5 min"),
           ("16:00", "Fantasy: who to add, who to fade", "5 min"), ("21:00", "Close and call to action", "1 min")]
    L += ["## Running order", "", "| Time | Segment | Length |", "|---|---|---|"]
    L += [f"| {t} | {n} | {d} |" for t, n, d in seg] + [""]

    # ── 1. cold open ──
    L += ["## 1. Cold open", ""]
    p = rc.get("potw")
    if p:
        L += [f"- **Hook:** \"{p['line']}. That's {p['player']}'s Week {week}, and it graded "
              f"{p['grade']}, better than {_f(p['score'])}% of every {p['position']} week last season. "
              "Today: who else earned it, who didn't, and how our own board did. Including the misses.\"",
              ""]
    else:
        L += ["- No completed NFL week to open on: lead with the NBA board or the Guess the Grade "
              "segment instead.", ""]

    # ── 2. report card ──
    if rc:
        L += ["## 2. The report card", "",
              f"- {rc['n']} players graded; {rc['n_a']} earned an A-range grade. A grade is a percentile "
              "against every player-week of last season at the position: a 90 beats 90% of them.",
              "- **Top five of the week:**"]
        L += [f"  {i}. {_rec(r)}" for i, r in enumerate(rc.get("top", [])[:5], 1)]
        if rc.get("by_pos"):
            L += ["- **Best at each position:** " + "; ".join(
                f"{pos} {rows[0]['player']} ({rows[0]['grade']})" for pos, rows in rc["by_pos"].items() if rows)]
        if rc.get("letdowns"):
            L += ["- **Big role, rough day** (top-quarter workload, bottom grades): talk through why."]
            L += [f"  - {_rec(r)}" for r in rc["letdowns"][:3]]
        if rc.get("best_teams") and rc.get("worst_teams"):
            L += ["- **Offenses:** best by average grade " + ", ".join(f"{t} ({m:.0f})" for t, m in rc["best_teams"])
                  + "; toughest days " + ", ".join(f"{t} ({m:.0f})" for t, m in rc["worst_teams"]) + "."]
        L += ["- **Say it:** a grade describes the week that happened. It is not a projection.", ""]

    # ── 3. honest record ──
    L += ["## 3. The honest record", "",
          "*Read this segment even on a bad week. It's the reason people trust the rest.*", ""]
    mark = {"win": "won", "loss": "lost", "push": "pushed", "dnp": "did not play"}
    if rp.get("ats"):
        w = sum(r.get("outcome") == "win" for r in rp["ats"])
        l = sum(r.get("outcome") == "loss" for r in rp["ats"])
        L += [f"- **Week {week} spread board:** {w}-{l}."]
        L += [f"  - {r['game']}: model leaned {r['pick_label']}, {mark.get(r.get('outcome'), 'pending')}"
              for r in rp["ats"]]
    if rp.get("parlay"):
        hit = all(l.get("outcome") == "win" for l in rp["parlay"])
        L += [f"- **Week {week} parlay:** {'hit' if hit else 'did not hit'}. "
              + "; ".join(f"{l['player']} {str(l['side']).upper()} {_f(l['line'], '{:g}')} {l['stat_type']} "
                          f"({mark.get(l.get('outcome'), 'pending')})" for l in rp["parlay"])]
    elif rp.get("parlay_withheld"):
        L += ["- **Parlay:** held back that week: the legs didn't clear the break-even their payout needed."]
    if pr.get("ats_n"):
        L += [f"- **Season, NFL spread board:** {pr['ats_record']} ({_f(pr.get('ats_pct'), '{:.1f}')}%). "
              f"Break-even at standard odds is {_f(pr.get('ats_breakeven'), '{:.1f}')}%, so: no demonstrated edge, "
              "and we say that plainly."]
    if pr.get("parlays_settled"):
        L += [f"- **Season parlays:** {pr['parlays_hit']} of {pr['parlays_settled']} hit."]
    try:
        import nba_daily_picks as nd
        nr = nd.record()
        if nr["ats"]["n"]:
            L += [f"- **NBA daily spread picks:** {nr['ats']['record']} ({nr['ats']['pct']}%); the model on every "
                  f"game {nr['ats_all_games']['record']}. Backtest said {nd.BACKTEST['ats_pct']}% last season: "
                  "a tracked test, not an edge."]
    except Exception:
        pass
    L += [""]

    # ── 4. Guess the Grade ──
    code = games.challenge_code(day)
    link = games.CHALLENGE_URL.format(code=code)
    try:
        pz = games.gtg_challenge(day)
    except Exception:
        pz = {}
    L += ["## 4. Guess the Grade, on air", "",
          f"- **Challenge link (say it, put it in the notes):** {link}",
          "- Everyone who opens it gets these same five, with its own leaderboard. Read each stat line, "
          "guess out loud, then reveal. Invite listeners to beat your score.", ""]
    if pz:
        L += ["| # | Read this on air |", "|---|---|"]
        for i, r in enumerate(pz["rounds"], 1):
            L.append(f"| {i} | {r['team']} vs {r['opponent']} ({'won' if r['result'] == 'W' else 'lost'}, "
                     f"{r['night']}): {r['line']}, {r['minutes']} min, {r['plus_minus']:+d} |")
        L += ["", "*The answers are at the very bottom of this brief, so you can guess honestly on air.*", ""]
    else:
        L += ["- No graded NBA games in the past week to build a challenge from; skip the segment.", ""]

    # ── 5. fantasy ──
    w = data.get("waivers") or []
    fades = data.get("fades") or []
    if w or fades:
        L += ["## 5. Fantasy: who to add, who to fade", ""]
        if w:
            L += ["- **Waiver targets** (projected points, outside Sleeper's top 150):"]
            L += [f"  - {r['player']} ({r['position']}, {r.get('team') or 'FA'}): "
                  f"{_f(r.get('proj_points'), '{:.1f}')} projected, crowd {r.get('crowd', '—')}" for r in w[:5]]
        if fades:
            L += ["- **The crowd is wrong about these** (heavily added, outside our projection's top tier):"]
            L += [f"  - {t['name']} ({t.get('position') or '?'}, {t.get('team') or 'FA'}): "
                  f"added {int(t.get('count', 0)):,} times" for t in fades[:3]]
        fr = data.get("fantasy_record") or {}
        if fr.get("n"):
            L += [f"- **Our own waiver record:** {fr['startable']} of our past adds put up a startable week "
                  f"({fr['startable_rate']}%). Say it, then explain what that does and doesn't mean."]
        L += [""]

    # ── 6. close ──
    L += ["## 6. Close and call to action", "",
          f"- \"Play this week's challenge and try to beat my score: {link}\"",
          f"- \"The full report card and the waiver board are in the newsletter, free: {SUBSCRIBE}\"",
          f"- Read the disclaimer: *{RESPONSIBLE}*", ""]

    # ── show notes ──
    chapters = "\n".join(f"{t} {n}" for t, n, _ in seg)
    desc = (f"Week {week} graded: " if week else "") + (
        f"{p['player']} tops the report card with an {p['grade']}. " if p else "")
    L += ["## Show notes (copy-paste)", "", "```",
          f"The Opportunity Report · {title}",
          "",
          desc + "Every QB, RB, WR and TE graded 0-100 on efficiency, production, usage and mistakes; "
          "how our own board did, misses included; the on-air Guess the Grade challenge; and who to "
          "add and fade on waivers.",
          "",
          f"🎮 Play the episode challenge: {link}",
          f"📬 Free weekly report: {SUBSCRIBE}",
          f"📊 Dashboard and track record: {SITE}/record/",
          "",
          "Chapters",
          chapters,
          "",
          RESPONSIBLE,
          "```", ""]

    # ── clips ──
    clips = []
    if p:
        clips.append(f"\"{p['player']} graded {p['grade']}: {p['line']}. Here's what the grade sees "
                     "that the box score doesn't.\" (grade card on screen)")
    if rc.get("letdowns"):
        r = rc["letdowns"][0]
        clips.append(f"\"{r['player']} got the work and graded {r['grade']}. Why that matters for next "
                     "week.\"")
    if pr.get("ats_n"):
        clips.append(f"\"We're {pr['ats_record']} against the spread and we'll tell you every week. "
                     "Here's why we still don't sell picks.\"")
    if pz:
        clips.append("The hardest Guess the Grade reveal of the segment, cut from the stat line to the "
                     "grade. End card: the challenge link.")
    if clips:
        L += ["## Clip ideas (vertical, 30–45 s each)", ""] + [f"{i}. {c}" for i, c in enumerate(clips, 1)] + [""]
    if pz:
        L += ["---", "", "## Answer key (spoilers)", ""]
        L += [f"{i}. {r['player']} ({r['team']}): **{r['grade']}, {_f(r['score'])}**"
              for i, r in enumerate(pz["rounds"], 1)] + [""]
    return "\n".join(L)


def write(data: dict, day: datetime.date | None = None) -> pathlib.Path:
    day = day or datetime.date.today()
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{day.isoformat()}-podcast-brief.md"
    path.write_text(build(data, day), encoding="utf-8")
    return path


if __name__ == "__main__":
    import newsletter as nl
    print(write(nl.select_edition(nl.build_sections(), "tuesday")))
