"""
video_daily.py — the YouTube jobs: build the day's Shorts and upload them PRIVATE.

Runs on this PC (Task Scheduler) rather than the cloud runner, because the host avatar and the
YouTube token live in ~/.konjure and never go into the repo.

  morning   (KonjureVideoDaily, 10:30)      every day: NBA results + NBA top 5 for last night,
                                            Guess the Grade for today.
                                            Sunday + Thursday: the day's NFL TD board (before kickoff).
                                            Monday: Sunday's NFL spread results + NFL top 5.
  afternoon (KonjureVideoAfternoon, 16:30)  Tonight's top 5 NBA picks (frozen into a log first,
                                            graded by the next morning's run), then Beat the Market
                                            once the site has frozen its slate (needs the site's
                                            Supabase credentials; skipped on Mondays for quota).

Each run first does git pull --rebase --autostash, so whatever the cloud runner graded is here
(autostash keeps uncommitted local edits, e.g. fantasy_log.json, as they were). Every Short is
uploaded PRIVATE; the owner publishes from YouTube Studio. Re-running is safe: uploads are keyed
by format + date. A format with nothing to show (no slate, not graded yet) is skipped.

YouTube's free quota is about six uploads a day; Monday is the full day (five + one).
Everything is logged to ~/.konjure/video_daily.log.

    python video_daily.py [morning|afternoon] [YYYY-MM-DD] [--no-upload]
"""
from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys
import traceback

ROOT = pathlib.Path(__file__).parent
LOG = pathlib.Path.home() / ".konjure" / "video_daily.log"
NBA_TAGS = ["NBA", "basketball", "sports analytics", "Konjure Analytics", "Shorts"]
NFL_TAGS = ["NFL", "football", "sports analytics", "Konjure Analytics", "Shorts"]
AI_NOTE = "\nNarration and host are AI-generated.\n"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _results_meta(night) -> tuple[str, str]:
    import nba_daily_picks as ndp
    e = ndp._load().get(night.isoformat()) or {}
    pub = [g for g in e.get("ats", []) if g.get("published")]
    w = sum(g["outcome"] == "win" for g in pub)
    l_ = sum(g["outcome"] == "loss" for g in pub)
    rec = ndp.record()["ats"]
    lines = "".join(f"{g['pick_label']}: {g['outcome'].upper()} ({g['away']} {g['away_pts']}, "
                    f"{g['home']} {g['home_pts']})\n" for g in pub)
    desc = (f"Every NBA pick we make is logged before tip-off and graded in public, losses included.\n\n"
            f"{night.strftime('%A, %B')} {night.day}: {w}-{l_}\n{lines}\n"
            f"Season: {rec['record']} ({rec['pct']}%). Break-even at standard odds is 52.4%.\n\n"
            f"Every pick, every result: https://konjureanalytics.com\n"
            f"21+. Not betting advice. Gambling problem? Call 1-800-GAMBLER.\n")
    return f"NBA Picks Graded {night.month}/{night.day}: We Went {w}-{l_} #Shorts", desc


def _desc(path) -> str:
    return path.with_suffix(".txt").read_text(encoding="utf-8")


KIT = pathlib.Path.home() / "Videos" / "Konjure Posting Kit"
_BETTING = {"results", "picks", "btm", "nfl-td", "nfl-results", "nfl-picks"}
_SOCIAL_TAGS = {"nba": "#NBA #basketball #nbastats #sportsanalytics",
                "nfl": "#NFL #football #fantasyfootball #sportsanalytics"}


def _utm(source: str, fmt: str, day=None) -> str:
    camp = f"{fmt}-{day.isoformat()}" if day else fmt
    return f"https://konjureanalytics.com/?utm_source={source}&utm_medium=social&utm_campaign={camp}"


def posting_kit(fmt: str, day, path, title: str, desc: str) -> pathlib.Path:
    """A folder per Short with the video and a caption for each platform, so cross-posting
    to TikTok, Reels, X and Facebook is drag-and-drop (or a scheduler like Metricool/Buffer).
    Every link is UTM-tagged by platform so the site's analytics can tell them apart. Betting
    formats carry the 21+ line; nothing links to a sportsbook (TikTok and Instagram restrict
    gambling content)."""
    import shutil
    import subprocess as sp
    out = KIT / day.isoformat() / fmt
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, out / path.name)
    # A 720p copy under 10 MB for browser uploads to Buffer; the apps re-compress anyway.
    try:
        import imageio_ffmpeg
        sp.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(path), "-vf", "scale=720:1280",
                "-c:v", "libx264", "-preset", "slow", "-b:v", "650k", "-maxrate", "800k", "-bufsize", "1600k",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                str(out / (path.stem + "-720.mp4"))], check=True, timeout=600)
    except Exception:
        pass
    hook = title.replace(" #Shorts", "").strip()
    summary = next((ln for ln in desc.splitlines() if ln.strip()), "")
    tags = _SOCIAL_TAGS["nfl" if fmt.startswith("nfl") else "nba"]
    legal = "21+. Not betting advice." if fmt in _BETTING else ""
    x_tail = " ".join(x for x in (legal, "#NFL" if fmt.startswith("nfl") else "#NBA") if x)
    # Buffer composer: one post, a tab per channel. Instagram takes no clickable links (bio),
    # Threads allows one, X counts every character (a link counts as 23).
    x_post = f"{hook}\n{_utm('x', fmt, day)}\n{x_tail}"
    if len(hook) + 24 + len(x_tail) + 2 > 280:
        x_post = f"{hook[:280 - 24 - len(x_tail) - 3]}\n{_utm('x', fmt, day)}\n{x_tail}"
    threads = f"{hook}\n\n{summary}\n\n{_utm('threads', fmt, day)}\n{legal}".strip()[:500]
    text = (
        f"##### BUFFER: one post, a tab per channel. Attach {path.stem}-720.mp4 to all three; turn on "
        f"AI-Generated for X and Instagram. #####\n\n"
        f"=== Instagram (Reel; link in bio) ===\n{hook}\n\n{summary}\nEvery result, graded in public: link in bio.\n"
        f"{legal}\n{tags}\n\n"
        f"=== Threads ===\n{threads}\n\n"
        f"=== X ===\n{x_post}\n\n"
        f"##### Other platforms #####\n\n"
        f"=== TikTok (link in bio) ===\n{hook}\n\n{summary}\n"
        f"Every result graded in public. Link in bio.\n{legal}\n{tags} #fyp\n\n"
        f"=== Facebook Reels ===\n{hook}\n\n{summary}\n{_utm('facebook', fmt, day)}\n{legal}\n\n"
        f"=== Bio links (set once per platform) ===\n"
        f"Instagram: {_utm('instagram', 'bio')}\nTikTok: {_utm('tiktok', 'bio')}\n")
    (out / "captions.txt").write_text(text.replace("\n\n\n", "\n\n"), encoding="utf-8")
    return out


def _lead_title(fmt: str, path, fallback: str) -> str:
    """A title that leads with the headline name (search finds "Paul George A+", not "Top 5
    NBA Players 11/10"), read back from the Short's own description. Guess the Grade never
    names anyone: the names are the answers."""
    try:
        desc = _desc(path)
    except OSError:
        return fallback
    cap = lambda s: " ".join(w[:1].upper() + w[1:] for w in s.split())
    if fmt in ("top5", "nfl-top5"):
        m = re.search(r"#1 (.+?) \(.*?\) - ([A-F][+-]?), ([\d.]+)", desc)
        if m:
            where = "NBA" if fmt == "top5" else "NFL"
            return f"{m.group(1)} {m.group(2)} ({m.group(3)}) Leads the Top 5 {where} Players {fallback} #Shorts"
    if fmt == "picks":
        m = re.search(r"#1 (.+?)\s+·\s+(.+?) \(([+-]\d+)\)", desc)
        if m:
            return f"{m.group(1)} {cap(m.group(2))} Leads Tonight's Top 5 NBA Picks {fallback} #Shorts"
    if fmt == "nfl-picks":
        m = re.search(r"#1 (.+?) (under|over) ([\d.]+) (.+?) \(", desc)
        if m:
            return f"{m.group(1)} {cap(m.group(2))} {m.group(3)} {m.group(4)}: Top 5 NFL Picks {fallback} #Shorts"
    if fmt == "nfl-td":
        m = re.search(r"#1 (.+?) \(", desc)
        if m:
            return f"{m.group(1)} Tops the NFL Touchdown Board {fallback}: Model vs Market #Shorts"
    if fmt == "btm":
        names = re.findall(r"^(.+?): [\d.]+ ", desc, re.M)
        if len(names) >= 3:
            last = [n.split()[-1] for n in names[:3]]
            return f"{', '.join(last)}: Can You Beat the Market? {fallback} Free NBA Game #Shorts"
    return None


def _jobs(slot: str, today: datetime.date) -> list:
    """(key format, date, builder name, title fn, tags) for this slot and day."""
    y = today - datetime.timedelta(days=1)
    md = lambda d: f"{d.month}/{d.day}"
    jobs = []
    if slot == "morning":
        jobs += [("results", y, "results_short", None, NBA_TAGS),
                 ("top5", y, "top5_short", lambda p: f"Top 5 NBA Players {md(y)}, Graded #Shorts", NBA_TAGS),
                 ("gtg", today, "gtg_short", lambda p: f"Guess the Grade {md(today)}: Can You Get All 3? #Shorts",
                  NBA_TAGS + NFL_TAGS[:2])]
        if today.weekday() in (6, 3):                                 # Sunday, Thursday
            day = "Sunday" if today.weekday() == 6 else "Thursday Night"
            jobs.append(("nfl-td", today, "nfl_td_short",
                         lambda p: f"{day} NFL Touchdown Board {md(today)}: Model vs Market #Shorts", NFL_TAGS))
        if today.weekday() == 6:                                      # Sunday: the board's five picks
            jobs.append(("nfl-picks", today, "nfl_picks_short",
                         lambda p: f"Top 5 NFL Picks for Sunday {md(today)}: Logged & Graded #Shorts", NFL_TAGS))
        if today.weekday() == 0:                                      # Monday: Sunday's games
            def res_title(p):
                m = re.search(r"Week (\d+) Sunday: (\d+-\d+)", _desc(p))
                return (f"NFL Picks Graded, Week {m.group(1)}: We Went {m.group(2)} #Shorts" if m
                        else f"NFL Picks Graded {md(y)} #Shorts")
            jobs += [("nfl-results", y, "nfl_results_short", res_title, NFL_TAGS),
                     ("nfl-top5", y, "nfl_top5_short", lambda p: f"Top 5 NFL Players From Sunday {md(y)}, Graded #Shorts",
                      NFL_TAGS)]
    elif slot == "afternoon":
        jobs.append(("picks", today, "picks_short", lambda p: f"Top 5 NBA Picks Tonight {md(today)}: Logged & Graded #Shorts",
                     NBA_TAGS))
        if today.weekday() not in (0, 6):             # Sun/Mon mornings already use five of ~6 uploads
            jobs.append(("btm", today, "btm_short",
                         lambda p: f"Beat the Market {md(today)}: Tonight's 5 NBA Props #Shorts", NBA_TAGS))
    return jobs


_ANNOUNCED = pathlib.Path.home() / ".konjure" / "youtube_announced.json"
_COMMENTED = pathlib.Path.home() / ".konjure" / "youtube_commented.json"
_RECORD = "konjureanalytics.com/record/"
_CTA = {
    "results": f"Every pick is logged before tip and graded right here, wins and losses. Full record: {_RECORD}"
               " · 21+ · Not betting advice",
    "nfl-results": f"Every pick is logged before kickoff and graded in public, wins and losses. Full record: {_RECORD}"
                   " · 21+ · Not betting advice",
    "top5": "Every player graded every night. Who did we get wrong? Full grades: konjureanalytics.com",
    "nfl-top5": "Every player graded every week. Who did we get wrong? Full grades: konjureanalytics.com",
    "picks": f"All five get graded tomorrow morning, right here. Which one hits? Record: {_RECORD}"
             " · 21+ · Not betting advice",
    "nfl-picks": f"All five get graded Monday, right here. Which one hits? Record: {_RECORD} · 21+ · Not betting advice",
    "nfl-td": f"Who finds the end zone? This board gets graded after the games: {_RECORD} · 21+ · Not betting advice",
    "btm": "Think you can beat the book? Play tonight's five free before tip-off: konjureanalytics.com"
           " · No prizes, just bragging rights · 21+",
}


def engage(today: datetime.date | None = None) -> int:
    """Comment from the channel on our Shorts once they are public: each format's call to action
    (pin it in Studio; the API cannot pin), and on Guess the Grade the answers the day after.
    Each comment goes out once. Quietly does nothing until comments are connected."""
    import json
    import youtube_upload as yu
    import site_build as sb
    today = today or datetime.date.today()
    public = {v for v, _ in sb._shorts(15)}
    done = set(json.loads(_COMMENTED.read_text())) if _COMMENTED.exists() else set()
    n = 0
    for key, row in yu.uploaded().items():
        m = re.fullmatch(r"([a-z0-9-]+?)-(\d{4}-\d{2}-\d{2})", key)
        if not m or row["id"] not in public:
            continue
        fmt, day = m.group(1), datetime.date.fromisoformat(m.group(2))
        todo = []
        if fmt == "gtg":
            code = day.strftime("%Y%m%d")
            todo.append((f"{row['id']}:cta", "Drop your three guesses (0-100)! Answers go up here tomorrow. Play today's "
                                             f"full five free: konjureanalytics.com/app/?challenge={code}"))
            if today > day:
                try:
                    lines = re.findall(r"^Round (\d): (.+?) - ([A-F][+-]?) \(([\d.]+)\)",
                                       pathlib.Path(row["file"]).with_suffix(".txt").read_text(encoding="utf-8"), re.M)
                except OSError:
                    lines = []
                if lines:
                    todo.append((f"{row['id']}:answers", "Answers: " + " · ".join(
                        f"Round {r}: {name}, {g} ({s})" for r, name, g, s in lines) + ". How close were you?"))
        elif fmt in _CTA:
            todo.append((f"{row['id']}:cta", _CTA[fmt]))
        for tag, text in todo:
            if tag in done:
                continue
            try:
                yu.comment(row["id"], text)
            except SystemExit:
                return n                                  # comments not connected yet
            except Exception:
                _log(f"comment on {key} FAILED\n{traceback.format_exc()}")
                continue
            done.add(tag)
            _COMMENTED.write_text(json.dumps(sorted(done)))
            _log(f"commented on {key} ({tag.split(':')[1]})")
            n += 1
    return n


def announce(dry_run: bool = False) -> list:
    """Post every newly PUBLIC Short to Discord, once each. Run by hand: publishing to Discord
    is the owner's call, the same rule as the daily cards. Reads the channel's public feed, so
    a private upload is never announced."""
    import json
    import publish
    import site_build as sb
    seen = set(json.loads(_ANNOUNCED.read_text())) if _ANNOUNCED.exists() else set()
    new = [(v, t) for v, t in reversed(sb._shorts(15)) if v not in seen]
    if not new or dry_run:
        return new
    text = "**New on the Konjure desk**\n" + "\n".join(
        f"▶ {t}\nhttps://www.youtube.com/shorts/{v}" for v, t in new) + "\n21+ · Not betting advice"
    r = publish.post_webhook(text[:1990], "discord")
    if r.get("ok"):
        _ANNOUNCED.write_text(json.dumps(sorted(seen | {v for v, _ in new})))
    _log(f"announce: {len(new)} Shorts -> Discord {r}")
    return new


def run(slot: str = "morning", today: datetime.date | None = None, upload: bool = True) -> list:
    today = today or datetime.date.today()
    pulled = subprocess.run(["git", "pull", "-q", "--rebase", "--autostash"], cwd=ROOT,
                            capture_output=True, text=True)
    if pulled.returncode:
        _log(f"git pull failed, building from local data: {pulled.stderr.strip()[:200]}")
    import video_builder as vb
    if slot == "morning":
        try:
            import nba_top5_picks as tp
            _log(f"top-5 picks graded: {tp.grade(today)}  record {tp.record()}")
        except Exception:
            _log(f"top-5 picks grading FAILED\n{traceback.format_exc()}")
    done = []
    for fmt, day, builder, title_fn, tags in _jobs(slot, today):
        key = f"{fmt}-{day.isoformat()}"
        if upload:
            import youtube_upload as yu
            if key in yu.uploaded():
                _log(f"{key}: already uploaded")
                continue
        try:
            path = getattr(vb, builder)(day)
        except SystemExit as e:                       # nothing to show for that day
            _log(f"{key}: skipped ({e})")
            continue
        except Exception:
            _log(f"{key}: build FAILED\n{traceback.format_exc()}")
            continue
        if fmt == "results":
            title, desc = _results_meta(day)
        else:
            title = _lead_title(fmt, path, f"{day.month}/{day.day}") or title_fn(path)
            desc = _desc(path)
        desc += AI_NOTE
        _log(f"{key}: built {path.name}")
        try:
            _log(f"{key}: posting kit at {posting_kit(fmt, day, path, title, desc)}")
        except Exception:
            _log(f"{key}: posting kit FAILED\n{traceback.format_exc()}")
        if upload:
            try:
                vid = yu.upload(path, title, desc, tags, key=key)
                _log(f"{key}: uploaded PRIVATE https://youtu.be/{vid}")
                done.append((fmt, vid))
            except SystemExit as e:
                _log(f"{key}: not uploaded ({e})")
            except Exception:
                _log(f"{key}: upload FAILED\n{traceback.format_exc()}")
    try:
        engage(today)                     # comments on whatever the owner has published since
    except Exception:
        _log(f"engage FAILED\n{traceback.format_exc()}")
    return done


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args and args[0] == "announce":
        for v, t in announce(dry_run="--dry-run" in sys.argv):
            print(v, t)
        sys.exit()
    slot = args[0] if args and args[0] in ("morning", "afternoon") else "morning"
    dates = [a for a in args if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)]
    run(slot, datetime.date.fromisoformat(dates[0]) if dates else None, upload="--no-upload" not in sys.argv)
