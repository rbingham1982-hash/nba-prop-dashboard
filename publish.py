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

# beehiiv's `status` field defaults to "confirmed", and confirmed means SENT. This is a
# constant with no override path so that no future edit, flag or config can turn an
# automated draft into an automated send.
_BEEHIIV_STATUS = "draft"


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
        "instagram": bool(_secret("IG_ACCESS_TOKEN") and _secret("IG_USER_ID")
                          and _secret("CARD_BASE_URL")),
        "threads": bool(_secret("THREADS_ACCESS_TOKEN") and _secret("THREADS_USER_ID")),
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
                path: pathlib.Path | None = None, footer2: str = "") -> pathlib.Path:
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

    # Two footer lines, both supplied by the caller. They used to be one caller line plus a
    # hardcoded "not last week's box score", which read as boilerplate and was sometimes
    # simply false — on a pre-season sleepers card there IS no last week.
    d.line([(64, _H - 168), (_W - 64, _H - 168)], fill=(55, 65, 81), width=2)
    d.text((64, _H - 138), footer, font=_font(26), fill=_MUTED)
    if footer2:
        d.text((64, _H - 96), footer2, font=_font(26), fill=_MUTED)

    # JPEG, not PNG. Instagram's content-publishing API accepts JPEG only, and a card that
    # cannot be posted to the platform it was designed for is a card that does not work.
    # Quality 92 keeps flat colour and text crisp at this size; the files land around
    # 120KB, well inside every platform limit.
    path = path or (_OUT / "card.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
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

    # Sleepers lead the pre-season issue, so they get the first card. The right-hand
    # number is the GAP, not the projection — the card has to show the disagreement,
    # because "we like him more than the room does" is the entire claim.
    sl = data.get("sleepers") or []
    if len(sl) >= 4:
        rows = [(f"{r['player']}  ({r['position']})",
                 f"+{r['gap']}") for r in sl[:10]]
        out.append(render_card(
            "Sleepers", when, rows,
            "Spots higher at his position than consensus draft rank",
            _OUT / f"{stamp}-card-sleepers.jpg",
            footer2="Projected from usage and depth chart, not name recognition."))

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
            _OUT / f"{stamp}-card-waivers-{pos.lower()}.jpg",
            footer2="Projected from usage, not last week's box score."))

    dfs = data.get("dfs") or []
    if len(dfs) >= 6:
        # Both numbers, and the projection first. Stripped to a card, a lone value figure
        # reads as "best play on the slate" when it means "best points per dollar" — a
        # different claim, and one that could have someone start a $5,100 outfielder over a
        # star. In the newsletter a caveat sits next to it; a card has to carry its own.
        rows = [(f"{r['player']}  ${int(r['salary']):,}",
                 f"{float(r['proj_points']):.1f}  ·  {r['value']}/$K") for r in dfs[:10]]
        out.append(render_card(
            f"{data.get('dfs_sport','DFS')} Value", when, rows,
            "Projected points  ·  points per $1,000",
            _OUT / f"{stamp}-card-dfs.jpg",
            footer2="Value is not the best play — cheap players rank high by design."))
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


# ── Meta platforms: Instagram and Threads ───────────────────────────────────
#
# Both use the same two-step shape — create a media container, then publish it — and both
# share one requirement that is a HOSTING problem rather than a code one:
#
#     "We cURL media used in publishing attempts, so the media must be hosted on a
#      publicly accessible server."
#
# There is no file upload. A local card in newsletter_out/ cannot be posted no matter how
# valid the token is, so image_url must point at somewhere public — an S3/R2 bucket, GitHub
# Pages, or wherever the newsletter's own assets end up living. CARD_BASE_URL is that
# location, and both clients refuse rather than guess when it is unset.
#
# Instagram additionally needs a PROFESSIONAL account (Business or Creator); a personal
# account cannot publish through the API at all.

_IG_GRAPH = "https://graph.instagram.com/v21.0"
_THREADS_GRAPH = "https://graph.threads.net/v1.0"


def card_url(path) -> str:
    """
    Public URL for a rendered card, or "" when no host is configured.

    Returning "" rather than a guessed path is deliberate: a wrong URL fails inside Meta's
    fetch with an opaque error, which is a much worse way to learn the host is missing.
    """
    base = _secret("CARD_BASE_URL").rstrip("/")
    if not base:
        return ""
    return f"{base}/{pathlib.Path(path).name}"


def post_instagram(image_path, caption: str) -> dict:
    """
    Publish one image to Instagram. Two steps: container, then publish.

    Requires a professional account, an IG user id, and a token with
    instagram_business_content_publish. JPEG only — which is why render_card writes JPEG.
    """
    import requests
    token = _secret("IG_ACCESS_TOKEN")
    user_id = _secret("IG_USER_ID")
    if not (token and user_id):
        return {"ok": False, "skipped": True, "reason": "no Instagram credentials configured"}
    url = card_url(image_path)
    if not url:
        return {"ok": False, "skipped": True,
                "reason": "CARD_BASE_URL not set — Instagram fetches images by URL and "
                          "cannot accept a local file"}
    try:
        c = requests.post(f"{_IG_GRAPH}/{user_id}/media",
                          data={"image_url": url, "caption": caption[:2200],
                                "access_token": token}, timeout=30)
        if c.status_code >= 300:
            return {"ok": False, "step": "container", "status": c.status_code,
                    "body": c.text[:300]}
        cid = (c.json() or {}).get("id")
        if not cid:
            return {"ok": False, "step": "container", "reason": "no container id returned"}
        r = requests.post(f"{_IG_GRAPH}/{user_id}/media_publish",
                          data={"creation_id": cid, "access_token": token}, timeout=30)
        return {"ok": r.status_code < 300, "status": r.status_code,
                "post_id": (r.json() or {}).get("id") if r.status_code < 300 else None,
                "body": r.text[:300] if r.status_code >= 300 else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def threads_token_status() -> dict:
    """
    How long the Threads token has left, without changing anything.

    Worth having because this token is unlike every other credential here: the Discord
    webhook and beehiiv key are permanent, but a Threads long-lived token dies after 60
    DAYS. Nothing would announce that — the automation would simply start failing two
    months from now, which is precisely the silent-degradation shape this project has hit
    six times already.
    """
    import requests
    token = _secret("THREADS_ACCESS_TOKEN")
    if not token:
        return {"ok": False, "reason": "no Threads token configured"}
    try:
        r = requests.get(f"{_THREADS_GRAPH}/me",
                         params={"fields": "id,username", "access_token": token}, timeout=25)
        if r.status_code >= 300:
            return {"ok": False, "status": r.status_code, "body": r.text[:200],
                    "hint": "a 190 error usually means the token expired — refresh or "
                            "re-authorise"}
        d = r.json() or {}
        return {"ok": True, "user_id": d.get("id"), "username": d.get("username")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def refresh_threads_token() -> dict:
    """
    Exchange the current long-lived token for a fresh 60 days.

    GET /refresh_access_token with grant_type=th_refresh_token. Refreshable once the token
    is at least 24 hours old and not yet expired; past 60 days of inactivity it is gone and
    only a full re-authorisation brings it back.

    Deliberately does NOT write the new token back to secrets.toml. Rewriting a credentials
    file from automation is the kind of thing that works until it truncates the file at the
    wrong moment and takes the other keys with it. The new value is returned for a person
    to paste, and the expiry is reported so a calendar reminder can be set.
    """
    import requests
    token = _secret("THREADS_ACCESS_TOKEN")
    if not token:
        return {"ok": False, "reason": "no Threads token configured"}
    try:
        r = requests.get("https://graph.threads.net/refresh_access_token",
                         params={"grant_type": "th_refresh_token", "access_token": token},
                         timeout=25)
        if r.status_code >= 300:
            return {"ok": False, "status": r.status_code, "body": r.text[:300]}
        d = r.json() or {}
        secs = int(d.get("expires_in") or 0)
        return {"ok": True, "expires_in_days": round(secs / 86400, 1),
                "new_token": d.get("access_token"),
                "note": "paste new_token into .streamlit/secrets.toml — not written "
                        "automatically, on purpose"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def post_threads(text: str, image_path=None) -> dict:
    """
    Publish to Threads. Same container/publish flow; text-only posts skip the image URL,
    which means Threads is the ONE Meta surface that works without hosting.
    """
    import requests
    token = _secret("THREADS_ACCESS_TOKEN")
    user_id = _secret("THREADS_USER_ID")
    if not (token and user_id):
        return {"ok": False, "skipped": True, "reason": "no Threads credentials configured"}
    data = {"access_token": token}
    if image_path:
        url = card_url(image_path)
        if not url:
            return {"ok": False, "skipped": True,
                    "reason": "CARD_BASE_URL not set — Threads fetches images by URL"}
        data.update({"media_type": "IMAGE", "image_url": url, "text": text[:500]})
    else:
        data.update({"media_type": "TEXT", "text": text[:500]})
    try:
        c = requests.post(f"{_THREADS_GRAPH}/{user_id}/threads", data=data, timeout=30)
        if c.status_code >= 300:
            return {"ok": False, "step": "container", "status": c.status_code,
                    "body": c.text[:300]}
        cid = (c.json() or {}).get("id")
        if not cid:
            return {"ok": False, "step": "container", "reason": "no container id returned"}
        r = requests.post(f"{_THREADS_GRAPH}/{user_id}/threads_publish",
                          data={"creation_id": cid, "access_token": token}, timeout=30)
        return {"ok": r.status_code < 300, "status": r.status_code,
                "post_id": (r.json() or {}).get("id") if r.status_code < 300 else None,
                "body": r.text[:300] if r.status_code >= 300 else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def draft_beehiiv(title: str, html: str, subtitle: str = "") -> dict:
    """
    Create a beehiiv post as a DRAFT. Never sends.

    Verified against the v2 API reference rather than written from memory, which turned up
    a landmine: `status` DEFAULTS TO "confirmed", and confirmed means sent. Omitting one
    field would have mailed the list. So the value is a module constant, asserted before
    the request, and there is no parameter to override it — the only way to send this
    newsletter is for a human to press the button in beehiiv.

    That asymmetry is deliberate. A bad tweet is deletable in a minute; a bad send sits in
    every inbox permanently, and this project has produced four things today that would
    have published if nothing stopped them.

    POST /v2/publications/{id}/posts, Bearer auth, `body_content` for raw HTML (mutually
    exclusive with `blocks`). Creation is asynchronous and returns 201 with a post id.
    """
    import requests
    key, pub = _secret("BEEHIIV_API_KEY"), _secret("BEEHIIV_PUBLICATION_ID")
    if not (key and pub):
        return {"ok": False, "skipped": True, "reason": "no beehiiv credentials configured"}

    payload = {
        "title": title,
        "body_content": html,
        "status": _BEEHIIV_STATUS,
        # The title is a headline; a subject line is a different job. Without this beehiiv
        # reuses the title, which reads fine on the web and poorly in an inbox.
        "email_settings": {"email_subject_line": subtitle or title},
    }
    if subtitle:
        payload["subtitle"] = subtitle
    assert payload["status"] == "draft", "refusing to create a beehiiv post that would send"

    try:
        r = requests.post(
            f"https://api.beehiiv.com/v2/publications/{pub}/posts",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload, timeout=30)
        ok = r.status_code in (200, 201)
        out = {"ok": ok, "status": r.status_code}
        if ok:
            try:
                out["post_id"] = (r.json().get("data") or {}).get("id")
            except Exception:
                pass
            out["note"] = "created as a DRAFT — nothing was emailed"
        else:
            out["body"] = r.text[:300]
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)}


def test_beehiiv() -> dict:
    """
    Check credentials and reachability WITHOUT creating anything.

    A GET against the publication is the cheapest proof that the key is valid and the
    publication id is right — worth having, because the alternative way to discover a bad
    id is a failed post at publish time.
    """
    import requests
    key, pub = _secret("BEEHIIV_API_KEY"), _secret("BEEHIIV_PUBLICATION_ID")
    if not (key and pub):
        return {"ok": False, "reason": "no beehiiv credentials configured"}
    try:
        r = requests.get(f"https://api.beehiiv.com/v2/publications/{pub}",
                         headers={"Authorization": f"Bearer {key}"}, timeout=25)
        if r.status_code == 200:
            d = (r.json().get("data") or {})
            return {"ok": True, "publication": d.get("name"), "id": d.get("id")}
        return {"ok": False, "status": r.status_code, "body": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── The run ─────────────────────────────────────────────────────────────────

def run(dry_run: bool = True,
        channels: tuple = ("discord", "x", "threads", "instagram", "beehiiv")) -> dict:
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

    # Per channel, so a re-run tops up what has not gone out rather than being blocked by
    # what has. beehiiv is exempt: a draft sends nothing, so re-drafting is harmless and
    # being locked out of it by an earlier Discord post is just an obstacle.
    def _send(name, fn):
        if name not in channels:
            return
        if name != "beehiiv" and nl.already_sent(data, name):
            result["posted"][name] = {"ok": False, "skipped": True,
                                      "reason": "already sent to this channel today"}
            return
        res = fn()
        result["posted"][name] = res
        if res.get("ok"):
            nl.mark_published(data, name)

    _send("discord", lambda: post_webhook(headline, "discord"))
    _send("slack", lambda: post_webhook(headline, "slack"))
    _send("x", lambda: post_x(headline[:280]))
    _send("threads", lambda: post_threads(headline))
    _send("instagram", lambda: post_instagram(cards[0], headline) if cards
          else {"ok": False, "skipped": True, "reason": "no card rendered to post"})
    _send("beehiiv", lambda: draft_beehiiv(
        md.splitlines()[0].lstrip("# "), html,
        subtitle="Fantasy football and DFS, projected from usage"))

    result["action"] = "published"
    return result


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")
    live = "--live" in sys.argv
    print(json.dumps(run(dry_run=not live), indent=2, default=str))
