"""
publish.py — distribution for the newsletter and social drafts.

Everything here is gated on newsletter.quality_gate(). Auto-posting is only defensible if
something refuses to post, and the gate is that something: an empty section, an
implausible projection, a dead source or betting-edge language all stop the run before a
single request is made.

CREDENTIALS ARE NEVER STORED HERE. Each client reads a token from Streamlit secrets or the
environment and does nothing at all when one is absent — so a fresh clone is inert rather
than half-configured. You create the accounts and supply the tokens; this file only uses
them.

    DISCORD_WEBHOOK_URL     your own server — no account creation, no approval friction
    SLACK_WEBHOOK_URL       same
    X_BEARER_TOKEN          X/Twitter API v2, needs a developer app you own
    BEEHIIV_API_KEY         Beehiiv publish API
    BEEHIIV_PUBLICATION_ID  which publication to draft into

Beehiiv posts are created as DRAFTS, never sent. Email is the one channel where a mistake
cannot be deleted after the fact — a bad tweet can be removed in a minute, a bad send is
in ten thousand inboxes permanently.
"""
from __future__ import annotations

import os
import pathlib

_OUT = pathlib.Path(__file__).parent / "newsletter_out"


def _secret(name: str) -> str:
    """Streamlit secrets first, environment second, empty string if neither."""
    try:
        import streamlit as st
        v = st.secrets.get(name, "")
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get(name, "")


def configured() -> dict:
    """Which channels have credentials. Useful for a dry run before wiring a scheduler."""
    return {
        "discord": bool(_secret("DISCORD_WEBHOOK_URL")),
        "slack": bool(_secret("SLACK_WEBHOOK_URL")),
        "x": bool(_secret("X_BEARER_TOKEN")),
        "beehiiv": bool(_secret("BEEHIIV_API_KEY") and _secret("BEEHIIV_PUBLICATION_ID")),
    }


# ── Image rendering for visual platforms ────────────────────────────────────

_W, _H = 1080, 1350          # 4:5, the aspect Instagram crops least
_BG = (17, 24, 39)
_FG = (243, 244, 246)
_MUTED = (156, 163, 175)
_ACCENT = (129, 140, 248)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_card(title: str, subtitle: str, rows: list, footer: str,
                path: pathlib.Path | None = None) -> pathlib.Path:
    """
    Render a board as a 1080x1350 card for Instagram/TikTok.

    `rows` is a list of (left, right) tuples — name and number. Deliberately sparse: a
    screenshot of a dataframe is unreadable on a phone, and the whole point of the visual
    channel is that one number is legible at thumbnail size.
    """
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (_W, _H), _BG)
    d = ImageDraw.Draw(img)

    d.text((64, 78), title, font=_font(58, True), fill=_FG)
    d.text((64, 152), subtitle, font=_font(30), fill=_MUTED)
    d.line([(64, 214), (_W - 64, 214)], fill=(55, 65, 81), width=2)

    # Positional splits make short cards the normal case — a QB board is often four names.
    # Stretching the rows to fill the frame looked wrong (a 104px cap still left half the
    # card empty, and lifting the cap spaces names absurdly far apart), so the block keeps
    # a comfortable fixed rhythm and is CENTRED in the space instead. Deliberate whitespace
    # reads as design; a top-aligned block with a void underneath reads as a bug.
    shown = rows[:14]
    top, bottom = 262, _H - 210
    step = 78 if len(shown) <= 8 else 66
    block = step * len(shown)
    y = top + max(0, (bottom - top - block) // 2)
    f_row, f_num = _font(38), _font(38, True)
    for left, right in shown:
        d.text((64, y), str(left)[:30], font=f_row, fill=_FG)
        rt = str(right)
        w = d.textlength(rt, font=f_num)
        d.text((_W - 64 - w, y), rt, font=f_num, fill=_ACCENT)
        y += step
        if y > bottom:
            break

    d.line([(64, _H - 168), (_W - 64, _H - 168)], fill=(55, 65, 81), width=2)
    d.text((64, _H - 138), footer, font=_font(26), fill=_MUTED)
    d.text((64, _H - 96), "Projected from usage, not last week's box score.",
           font=_font(26), fill=_MUTED)

    path = path or (_OUT / "card.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def render_cards(data: dict) -> list:
    """
    One card per POSITION for the waiver board, plus one for DFS.

    Split by position because a mixed card is quietly misleading: quarterbacks project
    10-12 points and receivers 5-6 under the same scoring, so putting them on one list
    makes Tyrod Taylor look like twice the play Dontayvion Wicks is when they are not
    comparable at all. Within a position every number means the same thing.

    DFS stays mixed on purpose — value is points per $1,000, which is exactly the quantity
    a salary cap makes comparable across positions.
    """
    stamp = data["generated"].strftime("%Y-%m-%d")
    when = data["generated"].strftime("%B %d")
    out = []

    w = data.get("waivers") or []
    by_pos: dict = {}
    for r in w:
        by_pos.setdefault(r.get("position") or "?", []).append(r)
    for pos in ("QB", "RB", "WR", "TE"):
        rows_ = sorted(by_pos.get(pos, []), key=lambda r: -float(r.get("proj_points") or 0))
        # Below three names it reads as a thin list rather than a board, which is worse
        # than not posting that position this week.
        if len(rows_) < 3:
            continue
        rows = [(r["player"], f"{float(r['proj_points']):.1f}") for r in rows_[:10]]
        out.append(render_card(
            f"Waiver Targets — {pos}", when, rows,
            "Outside the top 150 rostered · projected points",
            _OUT / f"{stamp}-card-waivers-{pos.lower()}.png"))

    dfs = data.get("dfs") or []
    if len(dfs) >= 6:
        rows = [(f"{r['player']}  ${int(r['salary']):,}", f"{r['value']}") for r in dfs[:10]]
        out.append(render_card(
            f"{data.get('dfs_sport','DFS')} Value", when, rows,
            "Projected points per $1,000",
            _OUT / f"{stamp}-card-dfs.png"))
    return out


# ── Channels ────────────────────────────────────────────────────────────────

def post_webhook(text: str, which: str = "discord") -> dict:
    """
    Post to your own Discord or Slack. No account creation and no third-party approval,
    because it is your own space — which is why this is the channel worth automating first.
    """
    import requests
    url = _secret("DISCORD_WEBHOOK_URL" if which == "discord" else "SLACK_WEBHOOK_URL")
    if not url:
        return {"ok": False, "skipped": True, "reason": f"no {which} webhook configured"}
    payload = {"content": text} if which == "discord" else {"text": text}
    try:
        r = requests.post(url, json=payload, timeout=20)
        return {"ok": r.status_code < 300, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def post_x(text: str) -> dict:
    """
    Post to X. Requires a developer app YOU own and a token you supply; this never creates
    an account or handles a password.
    """
    import requests
    token = _secret("X_BEARER_TOKEN")
    if not token:
        return {"ok": False, "skipped": True, "reason": "no X_BEARER_TOKEN configured"}
    if len(text) > 280:
        return {"ok": False, "reason": f"post is {len(text)} chars, over the 280 limit"}
    try:
        r = requests.post("https://api.twitter.com/2/tweets",
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          json={"text": text}, timeout=25)
        return {"ok": r.status_code < 300, "status": r.status_code,
                "body": r.text[:200] if r.status_code >= 300 else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def draft_beehiiv(title: str, html: str) -> dict:
    """
    Create a Beehiiv post as a DRAFT. Never sends.

    Email is the one channel where a mistake is permanent — a bad tweet is deletable in a
    minute, a bad send sits in every inbox forever. So the automation stops one step short
    and you press send.
    """
    import requests
    key, pub = _secret("BEEHIIV_API_KEY"), _secret("BEEHIIV_PUBLICATION_ID")
    if not (key and pub):
        return {"ok": False, "skipped": True, "reason": "no Beehiiv credentials configured"}
    try:
        r = requests.post(
            f"https://api.beehiiv.com/v2/publications/{pub}/posts",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"title": title, "body_content": html, "status": "draft"}, timeout=30)
        return {"ok": r.status_code < 300, "status": r.status_code,
                "body": r.text[:200] if r.status_code >= 300 else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── The run ─────────────────────────────────────────────────────────────────

def run(dry_run: bool = True, channels: tuple = ("discord", "x", "beehiiv")) -> dict:
    """
    Build an issue, gate it, and distribute it.

    dry_run=True is the default on purpose: the safe call is the one you get by accident.
    """
    import newsletter as nl
    data = nl.build_sections()
    md = nl.render_markdown(data)
    html = nl.render_html(md, data)
    ok, fails = nl.quality_gate(data, md)

    result = {"gate_passed": ok, "failures": fails, "dry_run": dry_run,
              "configured": configured(), "posted": {}}
    if not ok:
        result["action"] = "BLOCKED — nothing was sent"
        return result

    nl.generate()                      # write the files regardless
    cards = render_cards(data)
    result["cards"] = [str(c) for c in cards]

    # The post text lives INSIDE the ``` fences in the social draft. Taking the first
    # non-heading line instead grabbed "Copy-paste. Nothing here is posted automatically."
    # — the instruction to the human — and would have published it verbatim.
    social = nl.render_social(data)
    blocks, cur, inside = [], [], False
    for line in social.splitlines():
        if line.strip().startswith("```"):
            if inside and cur:
                blocks.append("\n".join(cur).strip())
            cur, inside = [], not inside
            continue
        if inside:
            cur.append(line)
    headline = blocks[0] if blocks else ""
    if not headline:
        result["gate_passed"] = False
        result["failures"] = ["social draft produced no post body"]
        result["action"] = "BLOCKED — nothing was sent"
        return result

    if dry_run:
        result["action"] = "DRY RUN — gate passed, nothing sent"
        result["would_post"] = {"webhook": headline[:280], "x": headline[:280],
                                "beehiiv_title": md.splitlines()[0].lstrip("# ")}
        return result

    if "discord" in channels:
        result["posted"]["discord"] = post_webhook(headline, "discord")
    if "slack" in channels:
        result["posted"]["slack"] = post_webhook(headline, "slack")
    if "x" in channels:
        result["posted"]["x"] = post_x(headline[:280])
    if "beehiiv" in channels:
        result["posted"]["beehiiv"] = draft_beehiiv(md.splitlines()[0].lstrip("# "), html)

    nl.mark_published(data)
    result["action"] = "published"
    return result


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")
    live = "--live" in sys.argv
    print(json.dumps(run(dry_run=not live), indent=2, default=str))
