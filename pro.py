"""
pro.py — Konjure Pro: the paid tier, and the page it unlocks.

What Pro sells is time and tools, never picks: the model has not beaten the closing line, so
a paid "picks" tier would be selling an edge the public record says we do not have.

  * Opportunity alerts (opportunity.py): who just got more, or less, of his team's work.
  * The grades database: every graded player-week (NFL) and player-game (NBA) this season,
    filterable and downloadable as CSV.

Billing is Stripe, with no Stripe library and no webhook server. The upgrade button is a
Stripe Payment Link (prefilled with the signed-in email); entitlement is checked against
Stripe's API by that email (an active or trialing subscription) and cached in the session.
Managing or cancelling is Stripe's hosted customer portal. Configure in Streamlit secrets
(or the environment):

    STRIPE_SECRET_KEY    restricted key with read access to Customers and Subscriptions
    PRO_PAYMENT_LINK     https://buy.stripe.com/...   (the Pro subscription's Payment Link)
    PRO_PORTAL_LINK      https://billing.stripe.com/p/login/...   (customer portal)
    PRO_PRICE_LABEL      e.g. "$6 / month"

Until PRO_PAYMENT_LINK is set the page shows a waitlist instead of a checkout. The owner's
own machine (not Streamlit Cloud) sees everything, like the account gate.
"""
from __future__ import annotations

import os
import time
import urllib.parse

import accounts as _acct

_CACHE_S = 1800


def _cfg(name: str) -> str:
    return _acct._secret(name)


def configured() -> bool:
    return bool(_cfg("PRO_PAYMENT_LINK") and _cfg("STRIPE_SECRET_KEY"))


def _stripe_get(path: str, params: dict) -> dict:
    import requests
    r = requests.get(f"https://api.stripe.com/v1/{path}", params=params, timeout=15,
                     headers={"Authorization": f"Bearer {_cfg('STRIPE_SECRET_KEY')}"})
    r.raise_for_status()
    return r.json()


def stripe_status(email: str) -> bool:
    """True if this email has an active or trialing Stripe subscription."""
    if not _cfg("STRIPE_SECRET_KEY") or not email:
        return False
    for c in _stripe_get("customers", {"email": email, "limit": 10}).get("data", []):
        subs = _stripe_get("subscriptions", {"customer": c["id"], "status": "all", "limit": 20})
        if any(s.get("status") in ("active", "trialing") for s in subs.get("data", [])):
            return True
    return False


def owner() -> bool:
    return not _acct.on_streamlit_cloud() and not os.environ.get("KONJURE_FORCE_PRO_GATE")


def is_pro(force_refresh: bool = False) -> bool:
    """Whether the current visitor gets Pro. Cached per session for half an hour."""
    import streamlit as st
    if owner():
        return True
    email = _acct.signed_in_email()
    if not email:
        return False
    hit = st.session_state.get("_pro_status")
    if hit and hit[0] == email and not force_refresh and time.time() - hit[2] < _CACHE_S:
        return hit[1]
    try:
        ok = stripe_status(email)
    except Exception:
        ok = bool(hit and hit[0] == email and hit[1])   # Stripe unreachable: keep the last answer
    st.session_state["_pro_status"] = (email, ok, time.time())
    return ok


def checkout_url(email: str) -> str:
    link = _cfg("PRO_PAYMENT_LINK")
    return f"{link}?{urllib.parse.urlencode({'prefilled_email': email})}" if email else link


# ── cached data ───────────────────────────────────────────────────────────────────────────

def _season():
    import datetime
    import nfl_analysis as nfl
    return nfl.season_for_date(datetime.date.today())


def _render_pitch(email: str | None) -> None:
    import streamlit as st
    price = _cfg("PRO_PRICE_LABEL")
    st.markdown("#### ⭐ Konjure Pro")
    st.markdown(
        "Tools, not picks. Pro is built on the same thing the projections are: **opportunity**.\n\n"
        "- **Opportunity alerts.** Every week, the RBs, WRs and TEs whose share of the work jumped "
        "(snaps, targets, carries) and the ones losing it, before the waiver wire catches up. NBA "
        "minutes risers and fallers through the season.\n"
        "- **The grades database.** Every graded player-week this season, every position, "
        "filterable and downloadable as CSV.\n"
        "- **Everything free stays free.** The report card, the boards, the record and the parlay "
        "builder are not going behind the paywall.")
    st.caption("We will never sell picks. Our model has not beaten the closing line, and the public "
               "record says so; Pro pays for tools that save you time.")
    if not configured():
        st.info("Pro opens soon. Join the free newsletter to hear first.")
        st.link_button("Get the free newsletter", "https://konjureanalytics.beehiiv.com/subscribe")
        return
    if not email:
        st.info("Create a free account (Edge Finder, any sport) to subscribe.")
        return
    c1, c2 = st.columns([1, 2])
    c1.link_button(f"Upgrade{' · ' + price if price else ''}", checkout_url(email), type="primary")
    if c2.button("I've subscribed, refresh my access", key="pro_refresh"):
        if is_pro(force_refresh=True):
            st.rerun()
        st.warning("No active subscription found for this email yet. Use the same email at checkout; "
                   "it can take a minute to appear.")


def _teaser() -> None:
    """One free riser, so the pitch shows the product rather than describing it."""
    import streamlit as st
    try:
        import nfl_grades as ng
        import opportunity as op
        season = _season()
        week = ng.completed_weeks(season)[-1]
        a = op.nfl_alerts(season, week)
    except Exception:
        return
    if a["risers"].empty:
        return
    r = a["risers"].iloc[0]
    st.markdown(f"**Free preview: Week {week}'s biggest riser.** {r['player']} ({r['position']}, {r['team']}) "
                f"played {r['snap_pct']:.0f}% of snaps ({r['snap_pct_chg']:+.0f} points on his last three games), "
                f"target share {r['target_share_chg']:+.0f}, carry share {r['carry_share_chg']:+.0f}. "
                f"Pro lists all {len(a['risers'])} risers and {len(a['fallers'])} fallers.")


def render(key: str = "pro") -> None:
    """The Pro page: the pitch for everyone else, the tools for subscribers."""
    import streamlit as st
    email = _acct.signed_in_email()
    if not is_pro():
        _render_pitch(email)
        _teaser()
        return

    st.markdown("#### ⭐ Konjure Pro")
    portal = _cfg("PRO_PORTAL_LINK")
    if portal and not owner():
        st.caption(f"Signed in as {email}. [Manage or cancel your subscription]({portal})")
    t_opp, t_nba, t_db = st.tabs(["NFL opportunity alerts", "NBA minutes", "Grades database"])

    with t_opp:
        _nfl_opportunity(key)
    with t_nba:
        _nba_minutes()
    with t_db:
        _grades_db(key)


def _nfl_opportunity(key: str) -> None:
    import streamlit as st
    import nfl_grades as ng
    import opportunity as op

    @st.cache_data(ttl=3600, show_spinner=False)
    def _usage(season):
        return op.nfl_usage(season)

    season = _season()
    weeks = ng.completed_weeks(season)
    if not weeks:
        st.info("Alerts start after Week 1 is complete.")
        return
    week = st.selectbox("Week", weeks[::-1], key=f"{key}_wk", format_func=lambda w: f"Week {w}")
    a = op.nfl_alerts(season, week, _usage(season))
    st.caption("Change against each player's previous three games, in percentage points. A riser gained 15+ "
               "points of snap share, 8+ of target share or 15+ of carry share; ranked by his gain in the team's "
               "targets plus carries. One big game can be a one-off: check why the role moved.")
    cols = {"player": "Player", "position": "Pos", "team": "Team", "opponent": "Opp", "snap_pct": "Snap %",
            "snap_pct_chg": "Snap Δ", "target_share": "Tgt share", "target_share_chg": "Tgt Δ",
            "carry_share": "Carry share", "carry_share_chg": "Carry Δ", "opp_share_chg": "Opp Δ"}
    st.markdown(f"**Risers ({len(a['risers'])})**")
    st.dataframe(a["risers"].rename(columns=cols), hide_index=True, width="stretch")
    st.markdown(f"**Fallers ({len(a['fallers'])})**")
    st.dataframe(a["fallers"].rename(columns=cols), hide_index=True, width="stretch")


def _nba_minutes() -> None:
    import streamlit as st
    import opportunity as op

    @st.cache_data(ttl=3600, show_spinner=False)
    def _alerts():
        return op.nba_alerts()

    a = _alerts()
    if a["risers"].empty and a["fallers"].empty:
        st.info(f"Minutes alerts start once {a.get('season', 'the season')} players have eight games: "
                "five to set the baseline, three to compare.")
        return
    cols = {"player": "Player", "team": "Team", "min_before": "Min before", "min_recent": "Min last 3",
            "min_chg": "Change", "pts_recent": "Pts last 3", "games": "Games", "last_game": "Last game"}
    st.caption("Minutes over the last three games against the season average before them. 5+ minutes either way.")
    st.markdown(f"**Risers ({len(a['risers'])})**")
    st.dataframe(a["risers"].rename(columns=cols), hide_index=True, width="stretch")
    st.markdown(f"**Fallers ({len(a['fallers'])})**")
    st.dataframe(a["fallers"].rename(columns=cols), hide_index=True, width="stretch")


def _grades_db(key: str) -> None:
    import streamlit as st
    import nfl_grades as ng

    @st.cache_data(ttl=3600, show_spinner=False)
    def _season_grades(season):
        return ng.grade_season(season)

    season = _season()
    g = _season_grades(season)
    if g is None or g.empty:
        st.info("No graded weeks yet this season.")
        return
    f1, f2, f3 = st.columns(3)
    pos = f1.multiselect("Position", sorted(g["position"].unique()), key=f"{key}_pos")
    team = f2.multiselect("Team", sorted(g["team"].unique()), key=f"{key}_team")
    who = f3.text_input("Player", key=f"{key}_who").strip().lower()
    d = g
    if pos:
        d = d[d["position"].isin(pos)]
    if team:
        d = d[d["team"].isin(team)]
    if who:
        d = d[d["player"].str.lower().str.contains(who, regex=False)]
    show = [c for c in ("week", "player", "position", "team", "opponent", "grade", "score", "efficiency",
                        "production", "usage", "mistakes", "ppr", "line") if c in d.columns]
    st.caption(f"{len(d):,} graded player-weeks. Score is a 0-100 percentile against last season at the "
               "position; the four components are percentiles too (mistakes: higher is cleaner).")
    st.dataframe(d[show], hide_index=True, width="stretch")
    st.download_button("Download CSV", d[show].to_csv(index=False).encode("utf-8"),
                       file_name=f"konjure-nfl-grades-{season}.csv", mime="text/csv", key=f"{key}_dl")
