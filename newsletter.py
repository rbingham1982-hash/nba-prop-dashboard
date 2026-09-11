"""
newsletter.py — turn the projection engine into publishable content.

Generates a fantasy football / DFS newsletter and matching short-form social posts from
the same projections the dashboard runs on. Writes files; it does not send or post
anything. Distribution is deliberately a separate, human-triggered step — see send.py
notes at the bottom of this docstring.

Why fantasy rather than betting picks: the props model has to clear a 12-19% hold with a
market blend weight near 0.11, its closing-line value is negative, and the paper-trading
verdict is INCONCLUSIVE. Publishing bet recommendations off that would be selling
something the data does not support. The fantasy projections are a different product —
there is no hold, the competition is consensus rankings, and the same engine that cannot
beat a book can beat an ADP list.

Outputs, all into ./newsletter_out/:

    YYYY-MM-DD-newsletter.md     source of truth, human-editable
    YYYY-MM-DD-newsletter.html   styled, paste-ready for any email platform
    YYYY-MM-DD-social.md         drafted posts, one per platform, for YOU to publish

Nothing here touches an account, a credential, or an API that posts. That is on purpose:
account creation and credential entry are not things this tool should do, and publishing
should stay a decision a person makes each time.
"""
from __future__ import annotations

import datetime
import pathlib

OUT_DIR = pathlib.Path(__file__).parent / "newsletter_out"

# House style. Kept explicit so the voice does not drift between issues, and so the honesty
# constraints are part of the template rather than something to remember each week.
_DISCLAIMER = (
    "Projections are model output, not advice. They are built from usage and opportunity "
    "— snaps, targets, plate appearances — not from last week's box score. They will be "
    "wrong regularly; the aim is to be wrong less often than consensus."
)


def _fmt_pts(v) -> str:
    return "—" if v is None else f"{float(v):.1f}"


def build_sections(nfl_limit: int = 16, dfs_sport: str = "MLB") -> dict:
    """
    Gather every section's data. Returns {} entries rather than raising when a source is
    unavailable, so one dead feed does not kill the issue — the renderer skips empty
    sections and says so.
    """
    import fantasy_tools as ft
    out: dict = {"generated": datetime.datetime.now(), "errors": []}

    # Pre-season the waiver wire has nothing to say — nobody has been dropped yet — so
    # the sleepers list carries the issue. It answers the question people actually have in
    # late August: who is going later than he should.
    try:
        out["sleepers"] = ft.sleepers(limit=16)
    except Exception as e:
        out["sleepers"] = []
        out["errors"].append(f"sleepers: {e}")

    try:
        rows, stats = ft.waiver_board(limit=nfl_limit, with_stats=True)
        out["waivers"], out["waiver_stats"] = rows, stats
    except Exception as e:
        out["waivers"], out["waiver_stats"] = [], {}
        out["errors"].append(f"waiver board: {e}")

    try:
        slate = ft.dfs_slate(dfs_sport)
        out["dfs"] = [] if slate is None or slate.empty else slate.head(12).to_dict("records")
        out["dfs_sport"] = dfs_sport
    except Exception as e:
        out["dfs"] = []
        out["errors"].append(f"dfs slate: {e}")

    # The week's NFL boards, read back from the PREDICTION LOG rather than freshly
    # generated. log_picks writes once per week and refuses to overwrite, so whichever run
    # first built the week is the record, and the card published later always shows exactly
    # what was committed to. Generating the board a second time at render time would let a
    # line that moved midweek quietly change the published pick while the log kept the old
    # one — and a record that disagrees with what went out is worse than no record.
    try:
        import weekly_picks as wp
        wp.grade()                     # fill in last week's results before reporting
        entry = wp.log_picks()
        out["season"], out["week"] = entry["season"], entry["week"]
        out["ats"] = entry.get("ats") or []
        out["parlay"] = entry.get("parlay") or []
        out["parlay_price"] = entry.get("parlay_price") or {}
        out["picks_locked"] = entry.get("locked", True)
        out["picks_lock_day"] = entry.get("locks_on", "Friday")
        out["picks_record"] = wp.record()
    except Exception as e:
        out["ats"], out["parlay"], out["parlay_price"] = [], [], {}
        out["picks_record"] = {}
        out["errors"].append(f"weekly picks: {e}")

    try:
        adds = ft.sleeper_trending("add", limit=12)
        out["trending"] = adds
    except Exception as e:
        out["trending"] = []
        out["errors"].append(f"sleeper trending: {e}")

    # The interesting cross-section: players the crowd is piling into who our projection
    # does NOT like. That contrast is the reason to read a projection-based newsletter
    # rather than a waiver-wire listicle.
    proj = {r["player"]: r for r in out.get("waivers", [])}
    fades = []
    for t in out.get("trending", []):
        nm = t.get("name")
        if nm and nm not in proj and t.get("count", 0) > 50000:
            fades.append(t)
    out["fades"] = fades[:6]
    return out


def render_markdown(data: dict) -> str:
    d = data["generated"].strftime("%B %d, %Y")
    L = [f"# The Opportunity Report — {d}", ""]
    L.append("*Fantasy football and DFS, projected from usage rather than from last week's "
             "box score.*")
    L.append("")

    sl = data.get("sleepers") or []
    if sl:
        L += ["## Sleepers — going later than they should", "",
              "Every rank below is **within position**, so the gap reads as: we have him "
              "this many spots higher at his position than consensus does. It measures a "
              "disagreement, not a good player — the best players alive are nobody's "
              "sleeper.", "",
              "Consensus comes from Sleeper's popularity ordering, which stands in for ADP. "
              "Backups are discounted to the workload their depth-chart slot implies, and "
              "unsigned free agents are excluded outright.", "",
              "| Player | Pos | Team | Proj | Ours | Consensus | Gap |",
              "|---|---|---|---:|---:|---:|---:|"]
        for r in sl[:14]:
            L.append(f"| {r['player']} | {r['position']} | {r.get('team') or '—'} | "
                     f"{_fmt_pts(r.get('proj_points'))} | {r['position']}{r['our_pos_rank']} | "
                     f"{r['position']}{r['consensus_pos_rank']} | +{r['gap']} |")
        L.append("")

    w = data.get("waivers") or []
    st = data.get("waiver_stats") or {}
    if not w and st.get("scored"):
        L += ["## Waiver targets", "",
              "**Nothing worth adding this week.** "
              f"{st.get('after_rank', 0)} unrostered players were projected and none cleared "
              "the bar for a startable add — which is the normal state of a waiver wire "
              "before the season, when everyone with a real role is already taken.", "",
              "That changes the week players start getting dropped. Until then, the "
              "sleepers list above is where the value is.", ""]
    if w:
        L += ["## Waiver targets", "",
              "Ranked by projected points among players outside Sleeper's top 150 — so these "
              "are plausibly still available. **quiet** means the projection likes him and "
              "the crowd has not moved yet.", "",
              "| Player | Pos | Team | Proj | Crowd |", "|---|---|---|---:|---|"]
        for r in w[:12]:
            L.append(f"| {r['player']} | {r['position']} | {r.get('team') or '—'} | "
                     f"{_fmt_pts(r.get('proj_points'))} | {r.get('crowd','—')} |")
        L.append("")

    f = data.get("fades") or []
    if f:
        L += ["## The crowd is wrong about these", "",
              "Heavily added on Sleeper this week, and outside our projection's top tier.",
              ""]
        for t in f:
            L.append(f"- **{t['name']}** ({t.get('position') or '?'}, {t.get('team') or 'FA'}) "
                     f"— {t.get('count', 0):,} adds")
        L.append("")

    # Say why the section is missing rather than leaving a hole. An issue that silently
    # drops its NFL board on a Tuesday looks broken; one that explains it locks on Friday
    # is describing a deliberate choice, which is what it is.
    if not (data.get("ats") or data.get("parlay")) and data.get("picks_locked") is False:
        # "lock" is deliberately absent from this copy. In betting slang a lock is a
        # guaranteed winner, which is exactly the claim _BANNED_PHRASES exists to prevent —
        # and the gate duly rejected the first draft of this very paragraph.
        L += [f"## Week {data.get('week', '?')} boards arrive "
              f"{data.get('picks_lock_day', 'Friday')}", "",
              "The spread board and the parlay are committed close to kickoff, not at the "
              "start of the week — early lines move a long way, and a prediction recorded on "
              "Tuesday would be graded against a market it never saw. They will be here in "
              "the next issue, and written down before any of these games are played.", ""]

    ats = data.get("ats") or []
    if ats:
        L += [f"## Model vs line — Week {data.get('week', '?')}", "",
              "The games where the spread model and the market disagree most. **This is not "
              "a pick list.** Against the closing line the model hits 49.3% over 1,359 "
              "out-of-sample games, where 52.4% is break-even at -110 — so the interesting "
              "content is the disagreement itself, not a recommendation to act on it.", "",
              "`Model` is the projected home margin from EPA-based team ratings, built only "
              "from games that finished before kickoff. Week 1 ratings carry over from last "
              "season at half weight.", "",
              "| Game | Line | Model | Leans | Gap |", "|---|---:|---:|---|---:|"]
        for r in ats:
            L.append(f"| {r['game']} | {float(r['spread_line']):+.1f} | "
                     f"{float(r['pred_margin']):+.1f} | {r['pick_label']} | "
                     f"{abs(float(r['edge'])):.1f} |")
        L.append("")

    legs = data.get("parlay") or []
    price = data.get("parlay_price") or {}
    if legs:
        cats = len({l.get("stat_type") for l in legs})
        L += [f"## The {len(legs)}-leg parlay", "",
              f"{len(legs)} players across {cats} different stat categories. The category "
              "spread is the only part of this that is defensible on its own terms: five "
              "legs from one market are close to the same bet five times over, and they "
              "price as independent when they are not.", "",
              "Probabilities are **blended against the book**, which is the number this "
              "project is allowed to publish. The raw model figure is roughly double it and "
              "has no resolved history behind it yet.", "",
              "| Player | Category | Pick | Odds | Model prob |",
              "|---|---|---|---:|---:|"]
        for l in legs:
            L.append(f"| {l['player']} | {l['stat_type']} | "
                     f"{str(l['side']).upper()} {float(l['line']):g} | "
                     f"{int(l.get('american_odds', -110)):+d} | "
                     f"{float(l.get('blended_prob', 0)):.1%} |")
        L.append("")
        if price:
            # Two decimals deliberately: at one, 7.84% against 7.75% both print as 7.8%
            # and the sentence reads as a tautology when the point is how narrow the margin
            # genuinely is.
            L.append(f"Combined: **{price.get('american', 0):+d}**, model probability "
                     f"**{float(price.get('blended_prob', 0)):.2%}** against "
                     f"**{float(price.get('breakeven_prob', 0)):.2%}** needed to break even. "
                     f"A five-leg parlay is a longshot by construction.")
            L.append("")

    # The running record. This exists so the two boards above can be checked rather than
    # taken on trust, and it prints even when it is unflattering — especially then. A
    # published prediction with no scoreboard is just content.
    rec = data.get("picks_record") or {}
    if rec.get("ats_n") or rec.get("leg_n"):
        L += ["## The record so far", ""]
        if rec.get("ats_n"):
            L.append(f"- **Against the spread:** {rec['ats_record']} "
                     f"({rec['ats_pct']}%) over {rec['ats_n']} graded picks. "
                     f"Break-even is {rec['ats_breakeven']}%; the backtest says "
                     f"{rec.get('ats_backtest_pct')}%.")
        if rec.get("leg_n"):
            L.append(f"- **Parlay legs:** {rec['leg_record']} ({rec['leg_pct']}%) "
                     f"over {rec['leg_n']} graded legs"
                     + (f", {rec['legs_dnp']} did not play." if rec.get("legs_dnp") else "."))
        if rec.get("parlays_settled"):
            L.append(f"- **Parlays:** {rec['parlays_hit']} of {rec['parlays_settled']} hit.")
        L.append("")
        if rec.get("weeks"):
            L += ["| Week | ATS | Legs |", "|---|---|---|"]
            for w in rec["weeks"]:
                L.append(f"| {w['week']} | {w['ats']} | {w['legs']} |")
            L.append("")

    dfs = data.get("dfs") or []
    if dfs:
        L += [f"## {data.get('dfs_sport','DFS')} value plays", "",
              "Projected points per $1,000 of salary. Cheap players rank high by "
              "construction — that is what a salary cap does — so read `value` next to "
              "`proj` rather than on its own.", "",
              "| Player | Pos | Salary | Proj | Value |", "|---|---|---:|---:|---:|"]
        for r in dfs:
            L.append(f"| {r.get('player')} | {r.get('position')} | ${r.get('salary'):,} | "
                     f"{_fmt_pts(r.get('proj_points'))} | {r.get('value')} |")
        L.append("")

    L += ["---", "", f"*{_DISCLAIMER}*", ""]
    if data.get("errors"):
        L.append(f"<!-- sections unavailable this issue: {'; '.join(data['errors'])} -->")
    return "\n".join(L)


def render_html(md: str, data: dict) -> str:
    """Minimal inline-styled HTML — email clients strip stylesheets, so styles are inline."""
    import re
    body = md
    body = re.sub(r"^# (.+)$", r"<h1>\1</h1>", body, flags=re.M)
    body = re.sub(r"^## (.+)$", r"<h2>\1</h2>", body, flags=re.M)
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    body = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", body)

    html_rows, in_table = [], False
    for line in body.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                html_rows.append('<table style="width:100%;border-collapse:collapse;'
                                 'font-size:14px;margin:12px 0;">')
                in_table = True
                tag = "th"
            else:
                tag = "td"
            style = ("padding:6px 8px;border-bottom:1px solid #e5e7eb;"
                     + ("text-align:left;font-weight:700;background:#f8fafc;" if tag == "th"
                        else "text-align:left;"))
            html_rows.append("<tr>" + "".join(f'<{tag} style="{style}">{c}</{tag}>'
                                              for c in cells) + "</tr>")
        else:
            if in_table:
                html_rows.append("</table>")
                in_table = False
            if line.startswith("- "):
                html_rows.append(f'<li style="margin:4px 0;">{line[2:]}</li>')
            elif line.strip() == "---":
                html_rows.append('<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">')
            elif line.strip():
                html_rows.append(f'<p style="margin:10px 0;line-height:1.55;">{line}</p>')
    if in_table:
        html_rows.append("</table>")

    return (
        '<div style="max-width:640px;margin:0 auto;font-family:-apple-system,Segoe UI,'
        'Roboto,Helvetica,Arial,sans-serif;color:#111827;">'
        + "".join(html_rows) +
        '</div>'
    )


def render_social(data: dict) -> str:
    """
    Drafted posts, one block per platform, for a person to publish.

    Deliberately short and specific. A projection newsletter's whole claim is that it says
    something consensus does not, so every post leads with a number and a name rather than
    a tease.
    """
    d = data["generated"].strftime("%b %d")
    w = data.get("waivers") or []
    fades = data.get("fades") or []
    dfs = data.get("dfs") or []
    L = [f"# Social drafts — {d}", "",
         "Copy-paste. Nothing here is posted automatically.", ""]

    sl = data.get("sleepers") or []
    if sl:
        t = sl[0]
        L += ["## X / Twitter", "",
              "```",
              f"{t['player']} is going {t['position']}{t['consensus_pos_rank']} in drafts.",
              f"We have him {t['position']}{t['our_pos_rank']} — a {t['gap']}-spot gap at his "
              f"position.",
              "",
              "Projected from usage, not last year's box score.",
              "```", ""]
    elif w:
        top = w[0]
        L += ["## X / Twitter", "",
              "```",
              f"Waiver target nobody is talking about: {top['player']} ({top['position']}, "
              f"{top.get('team') or 'FA'}).",
              f"Projects {_fmt_pts(top.get('proj_points'))} pts — outside the top 150 on Sleeper, "
              f"and the crowd hasn't moved.",
              "",
              "We project from usage, not last week's box score.",
              "```", ""]

    if fades:
        t = fades[0]
        L += ["## X / Twitter — the contrarian one", "",
              "```",
              f"{t['name']} has {t.get('count',0):,} adds this week.",
              "Our projection doesn't have him in the top tier.",
              "",
              "Being added a lot and being good are different things.",
              "```", ""]

    if dfs:
        r = dfs[0]
        L += [f"## {data.get('dfs_sport','DFS')} value post", "",
              "```",
              f"{data.get('dfs_sport','DFS')} value play: {r.get('player')} at ${r.get('salary'):,}.",
              f"{_fmt_pts(r.get('proj_points'))} projected pts — {r.get('value')} per $1K, "
              "best on the slate.",
              "```", ""]

    L += ["## Notes on tone", "",
          "- Lead with the number and the name. No teasing.",
          "- Publish the misses too. A projection service that only posts hits is a tip "
          "service, and readers work that out fast.",
          "- Never imply a betting edge. The model does not have one and saying otherwise "
          "is the fastest way to lose the audience that matters.", ""]
    return "\n".join(L)


def generate(nfl_limit: int = 16, dfs_sport: str = "MLB", out_dir: pathlib.Path | None = None) -> dict:
    """Build one issue and write it. Returns the paths written."""
    out_dir = out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build_sections(nfl_limit=nfl_limit, dfs_sport=dfs_sport)
    stamp = data["generated"].strftime("%Y-%m-%d")
    md = render_markdown(data)
    paths = {
        "markdown": out_dir / f"{stamp}-newsletter.md",
        "html": out_dir / f"{stamp}-newsletter.html",
        "social": out_dir / f"{stamp}-social.md",
    }
    paths["markdown"].write_text(md, encoding="utf-8")
    paths["html"].write_text(render_html(md, data), encoding="utf-8")
    paths["social"].write_text(render_social(data), encoding="utf-8")
    return {"paths": {k: str(v) for k, v in paths.items()},
            "sections": {k: len(data.get(k) or []) for k in ("waivers", "dfs", "fades", "trending")},
            "errors": data.get("errors", [])}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    res = generate()
    print("wrote:")
    for k, v in res["paths"].items():
        print(f"  {k:<9} {v}")
    print("sections:", res["sections"])
    if res["errors"]:
        print("errors:", res["errors"])


# ── Quality gate ────────────────────────────────────────────────────────────
#
# Auto-posting is only defensible if something refuses to post. These are the checks that
# would have caught the failures this project has actually produced, rather than the ones
# that sound thorough:
#
#   - Lamar Jackson appeared as a "waiver target" because a third-party availability rank
#     said 1059. One obviously wrong name discredits an entire list.
#   - starters_on() returned {} for every date for an hour, and looked like it worked.
#   - A feature ran, logged success, and changed nothing, six separate times in a day.
#
# So the gate is biased toward blocking: an empty section, an implausible number, or a
# source that came back silent all stop the issue. A missed post costs nothing. A wrong
# one published under your name costs the audience.

# Projected points above which a "waiver target" is not credible — nobody projecting this
# is unrostered in a real league, whatever a rank field claims.
#
# TE sits at 12.0 rather than 10.0 because the position's projections are compressed: TE9
# through TE16 span half a point (Kincaid 10.68 down to Waller 10.16), so a cap of 10.0
# fell inside a cluster the model cannot resolve and flagged an ordinary TE1 as a data
# error. 12.0 still leaves the genuine studs above it — McBride 18.3, Kittle 14.0, Kraft
# 12.8 — which is the case the cap exists to catch.
_MAX_CREDIBLE_WAIVER = {"QB": 16.0, "RB": 14.0, "WR": 13.0, "TE": 12.0}

# Phrases that imply a betting edge. The model does not have one — negative CLV, an
# INCONCLUSIVE paper verdict — so this is a house rule enforced in code rather than left
# to whoever is writing that week.
_BANNED_PHRASES = (
    "lock", "guaranteed", "can't lose", "cant lose", "free money", "sure thing",
    "best bet", "max bet", "hammer", "+ev play", "positive ev", "beat the book",
    "edge over the book", "sharp play",
)


def quality_gate(data: dict, rendered: str = "") -> tuple:
    """
    Decide whether an issue is safe to publish automatically.

    Returns (ok, failures). `failures` is a list of human-readable reasons; anything in it
    blocks the post.
    """
    fails = []

    if data.get("errors"):
        fails.append(f"a data source failed: {'; '.join(data['errors'])}")

    waivers = data.get("waivers") or []
    dfs = data.get("dfs") or []
    st = data.get("waiver_stats") or {}
    # An empty board is only a failure if nothing was SCORED. Zero players clearing the
    # floor is a true answer — in late August everyone with a role is rostered — and
    # publishing "nothing worth adding" is what makes the weeks with a real name credible.
    if not st.get("scored"):
        fails.append("waiver board scored no players at all — the projection or Sleeper "
                     "feed is down")
    elif not waivers:
        pass

    for r in waivers:
        pos, pts = r.get("position"), r.get("proj_points")
        cap = _MAX_CREDIBLE_WAIVER.get(pos)
        if cap and pts and float(pts) > cap:
            fails.append(f"{r.get('player')} projects {pts} at {pos} — too high to be a "
                         f"credible waiver add (cap {cap}); availability data is probably wrong")
        if not r.get("player") or not pos:
            fails.append(f"waiver row missing player or position: {r}")
        if pts is None or float(pts) <= 0:
            fails.append(f"{r.get('player')} has no usable projection")

    for r in (data.get("sleepers") or []):
        pos, pts = r.get("position"), r.get("proj_points")
        if not r.get("team"):
            fails.append(f"{r.get('player')} has no team — an unsigned free agent cannot "
                         f"be a sleeper")
        cap = _MAX_CREDIBLE_WAIVER.get(pos)
        # Sleepers are drafted players, so the ceiling is higher than a waiver add's — but
        # a projection far above it still means the depth or availability data is wrong.
        if cap and pts and float(pts) > cap * 1.8:
            fails.append(f"{r.get('player')} projects {pts} at {pos} — implausibly high "
                         f"for someone going outside the top {r.get('consensus_rank')}")
        if r.get("gap", 0) <= 0:
            fails.append(f"{r.get('player')} has no positive gap and should not be listed")

    # The ATS board is published as model-vs-line disagreement, so the checks are about
    # whether a row is COHERENT, not whether it looks like a good bet. A pick label that
    # contradicts its own spread is the failure that matters here: it is invisible in the
    # data and obvious on the card, which is the worst combination.
    for r in (data.get("ats") or []):
        sp, pick, label = r.get("spread_line"), r.get("pick"), r.get("pick_label") or ""
        if sp is None or pick not in ("home", "away"):
            fails.append(f"ATS row is unusable: {r}")
            continue
        want = (r.get("home_team") if pick == "home" else r.get("away_team")) or ""
        if not label.startswith(want):
            fails.append(f"ATS label {label!r} does not name the picked side ({want})")
        # The displayed number must be the picked side's spread, which is the negative of
        # spread_line for a home pick. A sign error here prints a dog as a favourite.
        expect = -float(sp) if pick == "home" else float(sp)
        try:
            shown = float(label.rsplit(" ", 1)[-1])
        except ValueError:
            fails.append(f"ATS label {label!r} has no readable number")
            continue
        if abs(shown - expect) > 0.01:
            fails.append(f"ATS label {label!r} shows {shown:+.1f} but the picked side is "
                         f"{expect:+.1f} — the spread sign is inverted")
        if r.get("pred_margin") is None or abs(float(r["pred_margin"])) > 30:
            fails.append(f"ATS row {r.get('game')} projects an implausible margin "
                         f"{r.get('pred_margin')}")

    # A five-leg parlay has to actually be five different categories and five different
    # players, because that is the only property of it being published as a feature.
    legs = data.get("parlay") or []
    if legs:
        if len({l.get("stat_type") for l in legs}) != len(legs):
            fails.append("parlay repeats a stat category — the legs are closer to the same "
                         "bet than the card claims")
        if len({l.get("player") for l in legs}) != len(legs):
            fails.append("parlay repeats a player")
        for l in legs:
            bp = l.get("blended_prob")
            if bp is None or not (0.0 < float(bp) < 1.0):
                fails.append(f"parlay leg {l.get('player')} has no usable probability")
            mp = l.get("model_prob")
            if mp is not None and bp is not None and float(mp) < float(bp) - 1e-9:
                fails.append(f"parlay leg {l.get('player')} blends ABOVE the raw model "
                             f"probability — the blend is backwards")

    for r in dfs:
        sal, pts = r.get("salary"), r.get("proj_points")
        if not sal or float(sal) <= 0:
            fails.append(f"DFS row for {r.get('player')} has no salary")
        if pts is None or float(pts) < 0:
            fails.append(f"DFS row for {r.get('player')} has no usable projection")

    low = (rendered or "").lower()
    for phrase in _BANNED_PHRASES:
        if phrase in low:
            fails.append(f"content implies a betting edge ('{phrase}') — the model does not "
                         f"have one and the house rule forbids claiming it")

    # NOTE: the duplicate-send guard deliberately does NOT live here. This function judges
    # whether the CONTENT is fit to publish; whether a given channel has already received
    # it is a delivery question, and conflating the two was wrong in a way that showed up
    # immediately — posting to Discord marked the whole day published, which then blocked
    # creating the beehiiv draft. A draft is not a duplicate of a Discord post, and it
    # cannot be a duplicate publication at all because nothing is sent. See already_sent().
    return (not fails), fails


def already_sent(data: dict, channel: str) -> bool:
    """True if this issue has already gone to this specific channel today."""
    stamp = data["generated"].strftime("%Y-%m-%d")
    return (OUT_DIR / f".published-{stamp}-{channel}").exists()


def mark_published(data: dict, channel: str) -> None:
    """Record that an issue reached one channel, so a re-run does not double-post it."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = data["generated"].strftime("%Y-%m-%d")
    (OUT_DIR / f".published-{stamp}-{channel}").write_text(
        datetime.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
