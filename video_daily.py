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
            title, desc = title_fn(path), _desc(path)
        desc += AI_NOTE
        _log(f"{key}: built {path.name}")
        if upload:
            try:
                vid = yu.upload(path, title, desc, tags, key=key)
                _log(f"{key}: uploaded PRIVATE https://youtu.be/{vid}")
                done.append((fmt, vid))
            except SystemExit as e:
                _log(f"{key}: not uploaded ({e})")
            except Exception:
                _log(f"{key}: upload FAILED\n{traceback.format_exc()}")
    return done


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    slot = args[0] if args and args[0] in ("morning", "afternoon") else "morning"
    dates = [a for a in args if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)]
    run(slot, datetime.date.fromisoformat(dates[0]) if dates else None, upload="--no-upload" not in sys.argv)
