"""
site_build.py — konjureanalytics.com's homepage, track-record page and about page.

The homepage used to be hand-written and said "every pick graded in public" without showing
one. It is generated now, so the proof on it stays current: the latest week's report card
and the running record, losses included. The record page is that record in full, and the
about page explains what the numbers are and what they are not.

    python site_build.py            # rebuild docs/index.html, docs/record/, docs/about.html

Run by the cloud board (daily-board.yml) after grading, so a new result reaches the site
the same day. Output is deterministic: it carries no build time, so a run with no new
result rewrites nothing. Social links render only once set below; nothing here invents an
account that does not exist.
"""
from __future__ import annotations

import html as _h
import pathlib

import report_archive as ra

ROOT = pathlib.Path(__file__).resolve().parent
DOCS = ROOT / "docs"
SITE = "https://konjureanalytics.com"
BEEHIIV_FORM = "88c44e3c-0ca9-4e06-9c17-0a25f31cbc55"   # beehiiv Subscribe forms, 2026-10-19

# Fill these in to show the links; an empty string hides that link everywhere.
SOCIAL = {
    "X": "https://x.com/konjureanalytic",
    "Instagram": "https://www.instagram.com/konjureanalytics",
    "Threads": "https://www.threads.net/@konjureanalytics",
    "Facebook": "https://www.facebook.com/konjureanalytics",
    "Discord": "",          # e.g. https://discord.gg/xxxxxxx
}

_EXTRA_CSS = """
.hero{margin:.6rem 0 1.6rem}
.hero h1{color:var(--head);font-size:2.1rem;line-height:1.18;margin:.5rem 0 .9rem;letter-spacing:-.01em}
.hero p{font-size:1.06rem;max-width:620px}
.row{display:flex;flex-wrap:wrap;gap:.7rem;margin:1.4rem 0 0}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--head)}
.btn.ghost:hover{border-color:var(--accent);background:transparent}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.8rem;margin:1.8rem 0 .6rem}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.95rem 1rem}
.stat .n{color:var(--head);font-size:1.45rem;font-weight:800;line-height:1.2}
.stat .l{font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--accent);font-weight:700}
.stat .d{font-size:.84rem;color:var(--muted);margin-top:.25rem}
.note{font-size:.84rem;color:var(--muted);margin-bottom:2.2rem}
h2.s{color:var(--head);font-size:1.2rem;margin:2.4rem 0 .8rem}
.potw{display:flex;gap:1rem;align-items:center;background:linear-gradient(135deg,#1b1f33,#161a26);
border:1px solid var(--line);border-radius:14px;padding:1.1rem 1.2rem;margin-bottom:.9rem}
.potw .g{font-size:2.3rem;font-weight:800;color:var(--accent);min-width:3.6rem;text-align:center}
.potw .who{color:var(--head);font-weight:800;font-size:1.1rem}
.potw .what{font-size:.88rem;color:var(--muted)}
table.t{width:100%;border-collapse:collapse;font-size:.9rem;margin:.4rem 0 1rem}
table.t th{text-align:left;color:var(--muted);font-weight:600;font-size:.75rem;letter-spacing:.06em;
text-transform:uppercase;border-bottom:1px solid var(--line);padding:.45rem .4rem}
table.t td{border-bottom:1px solid var(--line);padding:.5rem .4rem}
table.t td.r,table.t th.r{text-align:right}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.9rem;margin:.6rem 0 1rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1rem 1.1rem}
.card h3{color:var(--head);font-size:1rem;margin-bottom:.3rem}
.card p{font-size:.9rem}
.news{background:linear-gradient(135deg,#1b1f33,#161a26);border:1px solid var(--line);border-radius:14px;
padding:1.4rem 1.3rem;margin:2.4rem 0}
.news p{margin:.4rem 0 1rem;max-width:580px}
.small{font-size:.82rem;color:var(--muted)}
.social{display:flex;flex-wrap:wrap;gap:.6rem;margin-top:.6rem}
.prose h2{color:var(--head);font-size:1.15rem;margin:1.8rem 0 .5rem}
.prose p,.prose li{margin:.45rem 0;max-width:660px}
.prose ul{padding-left:1.2rem}
@media (max-width:520px){.hero h1{font-size:1.65rem}.btn{flex:1 1 100%;text-align:center}}
"""


def _page(title: str, desc: str, body: str, depth: str, path: str) -> str:
    """The archive's page shell, plus share tags and the homepage's extra styles."""
    page = ra._page(title, desc, body, depth=depth)
    share = (f'<meta property="og:title" content="{_h.escape(title)}">'
             f'<meta property="og:description" content="{_h.escape(desc)}">'
             f'<meta property="og:type" content="website"><meta property="og:url" content="{SITE}/{path}">'
             f'<meta property="og:image" content="{SITE}/og.jpg">'
             f'<meta name="twitter:card" content="summary_large_image">'
             f'<style>{_EXTRA_CSS}</style>')
    return page.replace("</head>", share + "</head>", 1)


def _write(rel: str, text: str, changed: list) -> None:
    path = DOCS / rel
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    changed.append(f"docs/{rel}")


def _social() -> str:
    links = [f'<a class="btn ghost" href="{_h.escape(u)}">{n}</a>' for n, u in SOCIAL.items() if u]
    return f'<div class="social">{"".join(links)}</div>' if links else ""


def _form() -> str:
    # beehiiv's embed, with a plain link for anyone whose browser blocks its script.
    return (f'<script async src="https://subscribe-forms.beehiiv.com/v3/loader.js" '
            f'data-beehiiv-form="{BEEHIIV_FORM}"></script>'
            f'<noscript><a class="btn" href="{ra.SUBSCRIBE}">Subscribe free</a></noscript>'
            f'<p class="small" style="margin-top:.7rem">Or <a href="{ra.SUBSCRIBE}">subscribe on beehiiv</a>. '
            f'Free. Unsubscribe in one click.</p>')


# ── data ──────────────────────────────────────────────────────────────────────────────────

def _latest_grades():
    """The newest fully played NFL week's report card, or None."""
    import datetime
    import nfl_analysis as nfl
    import nfl_grades as ng
    import newsletter as nl
    season = nfl.season_for_date(datetime.date.today())
    done = ng.completed_weeks(season)
    if not done:
        return None
    g = ng.grade_week(season, done[-1])
    return nl._recap(g, season, done[-1]) if not g.empty else None


def _records() -> dict:
    out = {}
    try:
        import weekly_picks as wp
        out["nfl"] = wp.record()
    except Exception:
        out["nfl"] = {}
    try:
        import nba_weekly_picks as nw
        out["nba"] = nw.record()
    except Exception:
        out["nba"] = {}
    try:
        import nba_daily_picks as ndp
        out["nba_daily"] = ndp.record()
    except Exception:
        out["nba_daily"] = {}
    tests = []
    for label, sport, fn in (("NFL recent-form unders", "nfl", "form"),
                             ("NBA recent-form unders", "nba", "form"),
                             ("NFL model-edge overs", "nfl", "edge"),
                             ("NFL 70%+ moneyline favourites", "nfl", "fav"),
                             ("NBA 70%+ moneyline favourites", "nba", "fav")):
        try:
            if fn == "form":
                import form_signal
                r = form_signal.record(sport)
            elif fn == "edge":
                import edge_signal
                r = edge_signal.record(sport)
            else:
                import favorite_signal
                r = favorite_signal.record(sport).get("at_last_price", {})
                r = {"n": r.get("n", 0), "win_pct": r.get("won_pct"), "breakeven_pct": r.get("breakeven_pct"),
                     "roi_pct": r.get("roi_pct"), "roi_se_pct": r.get("roi_se_pct")}
        except Exception:
            continue
        tests.append((label, r))
    out["tests"] = tests
    return out


# ── pages ─────────────────────────────────────────────────────────────────────────────────

def _stat(label: str, value: str, detail: str) -> str:
    return (f'<div class="stat"><div class="l">{_h.escape(label)}</div><div class="n">{_h.escape(value)}</div>'
            f'<div class="d">{_h.escape(detail)}</div></div>')


def _proof(rec: dict) -> str:
    n = rec.get("nfl") or {}
    tiles = []
    if n.get("ats_n"):
        tiles.append(_stat("NFL spread picks", f"{n['ats_record']}",
                           f"{_pct(n['ats_pct'])} · break-even {_pct(n['ats_breakeven'])}"))
    if n.get("td_n"):
        tiles.append(_stat("Touchdown scorer board", f"{n['td_record']}",
                           f"{_pct(n['td_pct'])} hit · the book's odds expected {_pct(n['td_expected_pct'])}"))
    nd = (rec.get("nba_daily") or {}).get("ats") or {}
    if nd.get("n"):
        tiles.append(_stat("NBA spread picks", nd["record"], f"{_pct(nd['pct'])} · break-even 52.4%"))
    total = sum(int(n.get(k) or 0) for k in ("ats_n", "leg_n", "td_n", "rec_n")) + int(nd.get("n") or 0)
    if total:
        tiles.append(_stat("Picks graded in public", f"{total}",
                           "every one logged before kickoff"))
    return f'<div class="stats">{"".join(tiles)}</div>' if tiles else ""


def _grades_block(rc: dict) -> str:
    if not rc:
        return ""
    p = rc["potw"]
    rows = "".join(
        f'<tr><td>{_h.escape(str(r["player"]))}</td><td>{_h.escape(str(r["position"]))}</td>'
        f'<td>{_h.escape(str(r["team"]))}</td><td class="r"><b>{_h.escape(str(r["grade"]))}</b></td>'
        f'<td class="r">{float(r["score"]):.0f}</td></tr>' for r in rc["top"][:8])
    return (f'<h2 class="s">Week {rc["week"]} report card</h2>'
            f'<div class="potw"><div class="g">{_h.escape(str(p["grade"]))}</div><div>'
            f'<div class="k">Player of the week</div>'
            f'<div class="who">{_h.escape(str(p["player"]))} · {_h.escape(str(p["position"]))}, '
            f'{_h.escape(str(p["team"]))} vs {_h.escape(str(p["opponent"]))}</div>'
            f'<div class="what">{float(p["score"]):.1f} · {_h.escape(str(p["line"]))}</div></div></div>'
            f'<table class="t"><tr><th>Player</th><th>Pos</th><th>Team</th><th class="r">Grade</th>'
            f'<th class="r">Score</th></tr>{rows}</table>'
            f'<p class="small">{rc["n"]} players graded on efficiency, production, share of the work and '
            f'mistakes, against every player-week of last season at the position. A 90 beats 90% of them. '
            f'The full card, every position, is in the Tuesday issue.</p>')


def home(rc, rec) -> str:
    body = f"""
<div class="hero"><div class="k">Player grades · projections · every pick graded</div>
<h1>Sports projections built from opportunity, not last week's box score.</h1>
<p>Player grades, projections and prop-market analysis for the NFL, NBA, MLB and WNBA, driven by
usage: snaps, targets, minutes, plate appearances. Every published pick is written down before
kickoff and graded afterwards, wins and losses alike.</p>
<div class="row"><a class="btn" href="#subscribe">Get the free report</a>
<a class="btn ghost" href="record/">See every pick graded</a></div></div>
{_proof(rec)}
<p class="note">Shown as it is, including when it is unflattering. A pick service that only shows
its wins is hiding something. <a href="record/">Full record →</a></p>
{_grades_block(rc)}
<div class="news" id="subscribe"><div class="k">The Opportunity Report · free, twice a week</div>
<p><b>Tuesday:</b> the week just played, graded, and who to pick up.<br>
<b>Friday:</b> this week's board, written down before kickoff.</p>
{_form()}{_social()}</div>
<h2 class="s">What's inside</h2>
<div class="grid">
<div class="card"><h3>Weekly hub</h3><p>Player grades, projections and the week's picks for every sport, refreshed through the day.</p></div>
<div class="card"><h3>Edge Finder · free account</h3><p>Sportsbook boards and a build-your-own parlay that shows its real chance of hitting, our model beside the market.</p></div>
<div class="card"><h3>Track record</h3><p>Every logged pick and every live test, graded in public. <a href="record/">See it →</a></p></div>
</div>
<div class="row"><a class="btn ghost" href="app/">Open the dashboard →</a><a class="btn ghost" href="report/">Past issues</a>
<a class="btn ghost" href="about.html">How it works</a></div>
<p class="small" style="margin-top:2rem">For adults 18+ and of legal age for sports wagering where you live.
Everything here is model output for informational and entertainment purposes. It is not betting advice,
and no outcome is guaranteed. If gambling stops being fun, call 1-800-GAMBLER.</p>
"""
    return _page("Konjure Analytics: player grades and projections, every pick graded in public",
                 "Player grades, projections and prop-market analysis for the NFL, NBA, MLB and WNBA, built "
                 "from usage. Every pick written down before kickoff and graded in public.",
                 body, depth="", path="")


def _pct(v) -> str:
    return "—" if v is None else f"{float(v):.1f}%"


def record_page(rec) -> str:
    n = rec.get("nfl") or {}
    rows = []
    if n.get("ats_n"):
        rows.append(("Spread picks", n["ats_record"], _pct(n["ats_pct"]),
                     f'{_pct(n["ats_breakeven"])} to break even at -110'))
    if n.get("td_n"):
        rows.append(("Touchdown scorer board", n["td_record"], _pct(n["td_pct"]),
                     f'the book\'s odds expected {_pct(n["td_expected_pct"])}'))
    if n.get("leg_n"):
        rows.append(("Five-leg parlay, legs", n["leg_record"], _pct(n["leg_pct"]),
                     f'{n["parlays_hit"]} of {n["parlays_settled"]} tickets hit'))
    if n.get("rec_n"):
        rows.append(("Receiving TD slate", n["rec_record"], _pct(n["rec_pct"]),
                     f'the book\'s odds expected {_pct(n["rec_expected_pct"])}'))
    if n.get("td_parlay_settled"):
        rows.append(("Touchdown parlay", f'{n["td_parlay_hit"]}-{n["td_parlay_settled"] - n["td_parlay_hit"]}',
                     "", f'{n["td_parlay_units"]:+.2f} units'))
    if n.get("form_parlay_settled"):
        rows.append(("Form-under parlay", f'{n["form_parlay_hit"]}-{n["form_parlay_settled"] - n["form_parlay_hit"]}',
                     "", f'{n["form_parlay_units"]:+.2f} units'))
    b = rec.get("nba") or {}
    if b.get("scorer_n"):
        rows.append(("NBA scorer board", b["scorer_record"], _pct(b.get("scorer_pct")),
                     f'expected {_pct(b.get("scorer_expected_pct"))}'))
    if b.get("parlays_settled"):
        rows.append(("NBA weekly parlay", f'{b["parlays_hit"]}-{b["parlays_settled"] - b["parlays_hit"]}', "", ""))
    nd = rec.get("nba_daily") or {}
    if (nd.get("ats") or {}).get("n"):
        rows.append(("NBA spread picks (daily)", nd["ats"]["record"], _pct(nd["ats"]["pct"]),
                     "52.4% breaks even; 48.1% in the 2025-26 backtest"))
    if (nd.get("ats_all_games") or {}).get("n"):
        rows.append(("NBA spread model, every game", nd["ats_all_games"]["record"], _pct(nd["ats_all_games"]["pct"]),
                     "all games logged, published or not"))
    if nd.get("parlays_settled"):
        rows.append(("NBA daily parlay", f'{nd["parlays_hit"]}-{nd["parlays_settled"] - nd["parlays_hit"]}', "",
                     f'legs {nd["leg_record"]}'))
    board = "".join(f'<tr><td>{_h.escape(a)}</td><td class="r"><b>{_h.escape(r)}</b></td>'
                    f'<td class="r">{_h.escape(p)}</td><td>{_h.escape(c)}</td></tr>' for a, r, p, c in rows)
    weeks = "".join(f'<tr><td>Week {int(str(w["week"]).split("-")[-1])}</td>'
                    f'<td class="r">{_h.escape(str(w["ats"]))}</td><td class="r">{_h.escape(str(w["td"]))}</td>'
                    f'<td class="r">{_h.escape(str(w["legs"]))}</td><td class="r">{_h.escape(str(w["rec"]))}</td></tr>'
                    for w in (n.get("weeks") or []))
    tests = "".join(
        f'<tr><td>{_h.escape(lbl)}</td><td class="r">{int(r.get("n") or 0)}</td><td class="r">{_pct(r.get("win_pct"))}</td>'
        f'<td class="r">{_pct(r.get("breakeven_pct"))}</td>'
        f'<td class="r">{"—" if r.get("roi_pct") is None else f"{float(r["roi_pct"]):+.1f}% ± {float(r.get("roi_se_pct") or 0):.1f}"}</td></tr>'
        for lbl, r in rec.get("tests", []) if r.get("n"))     # a test with no result yet is not news
    body = f"""
<div class="k">Track record</div><h1 class="page">Every pick, graded in public</h1>
<p class="lede">Each board is written down before the games start and graded from the box score afterwards.
Nothing is removed, re-scored or explained away. This page updates itself after every graded slate.</p>
<h2 class="s">The published boards</h2>
<table class="t"><tr><th>Board</th><th class="r">Record</th><th class="r">Hit rate</th><th>For context</th></tr>{board}</table>
<p class="small">Hit rates beside "expected" compare against what the sportsbook's own odds implied. Beating that
is what an edge would look like; matching it means the model agrees with the market.</p>
{f'<h2 class="s">NFL, week by week</h2><table class="t"><tr><th>Week</th><th class="r">Spread</th><th class="r">TD board</th><th class="r">Parlay legs</th><th class="r">Rec TD</th></tr>{weeks}</table>' if weeks else ''}
<h2 class="s">Live tests</h2>
<p class="small">Fixed rules, set before any result was in, recorded at the odds actually offered. Return per
one-unit bet, with its standard error: a result inside about two standard errors of zero is not yet
distinguishable from luck.</p>
<table class="t"><tr><th>Test</th><th class="r">Bets</th><th class="r">Won</th><th class="r">Break-even</th>
<th class="r">Return</th></tr>{tests}</table>
<h2 class="s">What this record says</h2>
<div class="prose"><p>{_reading(n)} Every board is published the same way, because a record that only
shows its wins is not a record. See <a href="../about.html">how it works</a>.</p></div>
"""
    return _page("Track record · Konjure Analytics",
                 "Every Konjure Analytics pick, written down before kickoff and graded in public, losses included.",
                 body, depth="../", path="record/")


def _reading(n: dict) -> str:
    """The record in plain words, written from the numbers so it cannot go stale."""
    bits = []
    if n.get("ats_n"):
        if float(n["ats_pct"]) < float(n["ats_breakeven"]):
            bits.append(f"The spread picks sit below the {_pct(n['ats_breakeven'])} it takes to break even, near the "
                        f"{_pct(n.get('ats_backtest_pct'))} the backtest predicted: the spread model has not beaten "
                        "the closing line.")
        else:
            bits.append(f"The spread picks are above break-even so far, over {n['ats_n']} games, too few to "
                        "separate skill from luck.")
    if n.get("td_n") and n.get("td_expected_pct") is not None:
        gap = float(n["td_pct"]) - float(n["td_expected_pct"])
        if abs(gap) < 3:
            bits.append("The touchdown board has hit about as often as the sportsbook's odds implied.")
        else:
            bits.append(f"The touchdown board has hit {'more' if gap > 0 else 'less'} often than the sportsbook's "
                        f"odds implied ({_pct(n['td_pct'])} against {_pct(n['td_expected_pct'])}) over "
                        f"{n['td_n']} picks, still too few to call an edge.")
    return " ".join(bits)


def about() -> str:
    body = """
<div class="k">About</div><h1 class="page">How Konjure Analytics works</h1>
<div class="prose">
<p>Konjure Analytics builds player projections and grades from opportunity: how much of a team's work a player
actually gets. Snaps, routes, targets and carries in football; minutes, usage and shot volume in basketball;
plate appearances in baseball. Opportunity is steadier than last week's box score, so it is where the projections start.</p>

<h2>The weekly report card</h2>
<p>Every quarterback, running back, receiver and tight end with a real role is graded 0-100 on four things:
efficiency (EPA per play), production, share of the team's work, and mistakes. Each is compared with every
player-week from last season at the same position, so a 90 means better than 90% of them. Grades are final once
the week's last game is in, which is why they publish on Tuesday.</p>

<h2>The boards, and how they are graded</h2>
<p>The NFL board (spread picks, a touchdown scorer board and the parlays) is saved on Friday afternoon, before any
game it covers. The NBA board is saved the same way for one night's slate. Nothing is edited afterwards: if a line
moves, the published pick stays as published. Each is graded from the official box score and added to the
<a href="record/">public record</a>.</p>

<h2>What we do not claim</h2>
<ul>
<li>We do not claim to beat the sportsbooks. Our model's picks have not beaten the closing line, and the record says so.</li>
<li>Where the board shows a probability, it is the sportsbook's own price with its margin removed, with our model
shown beside it. When the two disagree, the market has usually been right.</li>
<li>Parlays are only published when their price clears break-even; the ones that do not are still logged and graded.</li>
<li>"Lock", "guaranteed" and similar words are blocked by the publishing system itself.</li>
</ul>

<h2>Live tests</h2>
<p>A few betting rules are being tested in the open with fixed rules set in advance, such as taking the under when a
player's recent form sits below the line. They are recorded at the odds actually offered and reported with their
uncertainty on the <a href="record/">record page</a>. A test is not a recommendation.</p>

<h2>Contact</h2>
<p>Questions, corrections or data problems: <a href="mailto:info@konjureanalytics.com">info@konjureanalytics.com</a>.</p>

<h2>Play responsibly</h2>
<p>For adults 18+ and of legal age for sports wagering where you live. Everything here is model output for
informational and entertainment purposes, not betting advice. If gambling stops being fun, call 1-800-GAMBLER.</p>
</div>
"""
    return _page("How it works · Konjure Analytics",
                 "How Konjure Analytics grades players and logs its picks, and what it does not claim.",
                 body, depth="", path="about.html")


# Set before publishing the terms: the state whose law governs them, and where disputes go.
GOVERNING_LAW = "the State of Illinois"
TERMS_DATE = "October 19, 2026"


def terms() -> str:
    body = f"""
<div class="k">Legal</div><h1 class="page">Terms of Service</h1>
<p class="small">Last updated: {TERMS_DATE}</p>
<div class="prose">
<p>These terms cover your use of Konjure Analytics: this website (konjureanalytics.com), the Konjure
Analytics dashboard and app, The Opportunity Report newsletter, and the Konjure Pro subscription
(together, "the service"). By using the service you agree to them. If you do not agree, please do not
use it.</p>

<h2>1. Who can use the service</h2>
<p>You must be at least 18, and of legal age for sports wagering where you live if you use the service
in connection with betting. You are responsible for knowing and following the laws that apply to you.</p>

<h2>2. Not betting advice</h2>
<p>Everything we publish (grades, projections, probabilities, boards, parlays, alerts and the live tests)
is model output and analysis for informational and entertainment purposes. It is not betting, financial or
investment advice, and no outcome is guaranteed. Past results, including our public record, do not predict
future results. Our model has not beaten sportsbook closing lines, and we say so on our
<a href="record/">record page</a>. Any decision to bet is yours alone, and so are its results.
If gambling stops being fun, call 1-800-GAMBLER.</p>

<h2>3. Accounts</h2>
<p>Some features need a free account. Give a real email address you control, keep your password private,
and use one account per person. You are responsible for activity on your account. We may suspend or close
an account that breaks these terms or is used to harm the service or other people.</p>

<h2>4. Konjure Pro</h2>
<ul>
<li><b>Billing.</b> Konjure Pro is a subscription billed in advance, monthly, at the price shown at checkout,
plus any tax that applies. Payments are processed by Stripe.</li>
<li><b>Renewal.</b> Your subscription renews automatically each period until you cancel.</li>
<li><b>Cancelling.</b> You can cancel at any time from the subscription management link in the Pro page or
by emailing us. Cancelling stops future charges; you keep Pro until the end of the period you have
paid for.</li>
<li><b>Refunds.</b> See our <a href="refunds.html">refund policy</a>.</li>
<li><b>Price changes.</b> If we change the price, we will tell you at least 30 days before it applies to
your next renewal, and you can cancel before then.</li>
<li><b>What Pro is.</b> Pro provides tools and data (opportunity alerts and the grades database). It does
not provide picks, and it does not change what is published for free. Features may be improved, changed
or retired; if we remove a core Pro feature, you may cancel and ask for a prorated refund of the unused
period.</li>
</ul>

<h2>5. Acceptable use</h2>
<p>Do not scrape, copy in bulk, resell or republish the service or its data, run automated tools against
it, try to get around the account gate or the Pro paywall, or interfere with how it works. Data you
download as a Pro subscriber (such as the grades CSV) is for your own personal use; you may quote or share
individual figures with credit to Konjure Analytics, but not redistribute the dataset.</p>

<h2>6. Our content</h2>
<p>The grades, projections, write-ups, graphics and software are ours or our licensors'. We give you a
personal, non-commercial, revocable licence to use them through the service. Sports statistics and
sportsbook prices come from third-party and public sources, which remain theirs. If you send us
feedback, we may use it without owing you anything.</p>

<h2>7. Third-party services and links</h2>
<p>The service relies on and links to others, including sportsbooks and fantasy sites, data providers,
Stripe, beehiiv and our hosting providers. We are not affiliated with or endorsed by any league,
team or sportsbook, and we are not responsible for third-party sites or their accuracy. Some links may
be affiliate links that pay us if you sign up; when they are, we say so. Affiliate arrangements never
change what our boards or grades say: those are produced by fixed rules and logged before the games.</p>

<h2>8. Data accuracy</h2>
<p>Data can be late, missing or wrong, and odds move. We work to catch errors and grade everything from
official box scores, but we do not guarantee that any figure is accurate, complete or current.</p>

<h2>9. Disclaimers</h2>
<p>The service is provided "as is" and "as available", without warranties of any kind, express or implied,
including merchantability, fitness for a particular purpose and non-infringement, to the fullest extent
the law allows. We do not promise the service will be uninterrupted or error-free.</p>

<h2>10. Limitation of liability</h2>
<p>To the fullest extent the law allows, Konjure Analytics is not liable for any indirect, incidental,
special, consequential or punitive damages, or for any betting or trading losses, lost profits or lost
data, arising from your use of the service. Our total liability for any claim relating to the service is
limited to the amount you paid us in the 12 months before the claim, or $50 if you paid nothing.</p>

<h2>11. Indemnity</h2>
<p>You agree to cover our reasonable costs from claims by others that arise from your breach of these terms
or your misuse of the service.</p>

<h2>12. Ending use</h2>
<p>You can stop using the service and delete your account at any time (see the
<a href="data-deletion.html">data deletion page</a>). We may suspend or end access for a breach of these
terms. Sections 2 and 6 to 13 continue after access ends.</p>

<h2>13. Governing law</h2>
<p>These terms are governed by the laws of {GOVERNING_LAW}, without regard to its conflict-of-law rules, and
any dispute will be resolved in the state or federal courts located there, unless the law where you live
gives you the right to bring it elsewhere.</p>

<h2>14. Changes</h2>
<p>We may update these terms. We will change the date above, and for material changes we will give notice
on the site or by email before they take effect. Continuing to use the service after that means you accept
the updated terms.</p>

<h2>15. Contact</h2>
<p><a href="mailto:info@konjureanalytics.com">info@konjureanalytics.com</a></p>
</div>
"""
    return _page("Terms of Service · Konjure Analytics",
                 "The terms for using Konjure Analytics and the Konjure Pro subscription.",
                 body, depth="", path="terms.html")


def refunds() -> str:
    body = f"""
<div class="k">Legal</div><h1 class="page">Refund policy</h1>
<p class="small">Last updated: {TERMS_DATE}</p>
<div class="prose">
<p>The free parts of Konjure Analytics cost nothing and need no refund. This policy covers the Konjure Pro
subscription.</p>

<h2>Cancel any time</h2>
<p>You can cancel Pro whenever you like, from the subscription management link in the Pro page or by emailing
us. You will not be charged again, and you keep Pro until the end of the period you have already paid for.</p>

<h2>Your first payment: 7-day refund</h2>
<p>If Pro is not for you, email us within 7 days of your first payment and we will refund it in full and end
your subscription. No reason needed. This applies to your first payment on an account, once.</p>

<h2>Renewals</h2>
<p>Renewal payments are not refunded, because you can cancel at any time before the next one. Remember that
cancelling keeps your access to the end of the paid period.</p>

<h2>When we always refund</h2>
<ul>
<li>You were charged twice, or charged after cancelling.</li>
<li>A billing error on our side.</li>
<li>Pro was unavailable for most of a billing period because of a problem on our side.</li>
<li>We removed a core Pro feature (see the <a href="terms.html">Terms of Service</a>): a prorated refund for the unused part of
the period.</li>
</ul>

<h2>Results are not a reason for a refund</h2>
<p>Pro sells tools and data, not picks, and nothing we publish guarantees any outcome. We cannot refund a
subscription because of how a game, a bet or a fantasy week turned out.</p>

<h2>How to ask</h2>
<p>Email <a href="mailto:info@konjureanalytics.com">info@konjureanalytics.com</a> from, or mentioning, the email
address on your subscription. We reply within 2 business days. Approved refunds go back to the original payment
method through Stripe and usually appear within 5 to 10 business days, depending on your bank.</p>

<h2>Before disputing a charge</h2>
<p>Please email us first; we can usually fix a billing problem faster than a card dispute can.</p>
</div>
"""
    return _page("Refund policy · Konjure Analytics",
                 "How cancelling and refunds work for the Konjure Pro subscription.",
                 body, depth="", path="refunds.html")


def og_image() -> list:
    """docs/og.jpg, the share card for every page. Made once: a brand card, not data."""
    path = DOCS / "og.jpg"
    if path.exists():
        return []
    from PIL import Image, ImageDraw
    import publish as p
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), p._BG)
    d = ImageDraw.Draw(img)
    d.text((80, 90), "KONJURE ANALYTICS", font=p._font(30, True), fill=p._ACCENT)
    d.text((80, 150), "Every pick written down", font=p._font(72, True), fill=p._FG)
    d.text((80, 240), "before kickoff.", font=p._font(72, True), fill=p._FG)
    d.text((80, 330), "Graded in public, losses included.", font=p._font(40), fill=p._MUTED)
    d.text((80, 430), "Player grades · projections · NFL, NBA, MLB, WNBA", font=p._font(30), fill=p._FG)
    d.line([(80, H - 100), (W - 80, H - 100)], fill=(55, 65, 81), width=2)
    d.text((80, H - 80), "konjureanalytics.com", font=p._font(28), fill=p._MUTED)
    img.save(path, "JPEG", quality=92, optimize=True)
    return ["docs/og.jpg"]


def build() -> list[str]:
    changed: list = []
    try:
        rc = _latest_grades()
    except Exception as e:
        print(f"  report card unavailable ({type(e).__name__}: {e})")
        rc = None
    rec = _records()
    _write("index.html", home(rc, rec), changed)
    _write("record/index.html", record_page(rec), changed)
    _write("about.html", about(), changed)
    _write("terms.html", terms(), changed)
    _write("refunds.html", refunds(), changed)
    changed += og_image()
    changed += ra.build()                      # the archive's nav links to the new pages
    return changed


if __name__ == "__main__":
    names = build()
    print(f"site: {len(names)} file(s) updated" + (": " + ", ".join(names) if names else ""))
