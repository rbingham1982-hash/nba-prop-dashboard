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

    w = data.get("waivers") or []
    st = data.get("waiver_stats") or {}
    if not w and st.get("scored"):
        L += ["## Waiver targets", "",
              "**Nothing worth adding this week.** "
              f"{st.get('after_rank', 0)} unrostered players were projected and none cleared "
              "the bar for a startable add — which is the normal state of a waiver wire "
              "before the season, when everyone with a real role is already taken.", "",
              "We would rather say that than pad the list.", ""]
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

    if w:
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
_MAX_CREDIBLE_WAIVER = {"QB": 16.0, "RB": 14.0, "WR": 13.0, "TE": 10.0}

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

    # Duplicate guard: publishing the same day twice reads as a bot malfunction.
    stamp = data["generated"].strftime("%Y-%m-%d")
    marker = OUT_DIR / f".published-{stamp}"
    if marker.exists():
        fails.append(f"an issue was already published for {stamp}")

    return (not fails), fails


def mark_published(data: dict) -> None:
    """Record that an issue went out, so the duplicate guard can see it."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = data["generated"].strftime("%Y-%m-%d")
    (OUT_DIR / f".published-{stamp}").write_text(
        datetime.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
