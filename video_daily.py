"""
video_daily.py — the morning YouTube job: build last night's Shorts and upload them PRIVATE.

Runs on this PC (Task Scheduler, KonjureVideoDaily) rather than the cloud runner, because the
host avatar and the YouTube token live in ~/.konjure and never go into the repo.

  1. git pull --rebase --autostash, so the board the cloud runner graded overnight is here
     (autostash keeps any uncommitted local edits, e.g. fantasy_log.json, as they were).
  2. Build "How we did last night" (when there were published picks, all graded) and
     "Players of the night" for yesterday.
  3. Upload each as a PRIVATE Short (youtube_upload). The owner publishes from YouTube Studio;
     nothing goes public from here. Re-running is safe: each Short is keyed by format + date.

A night with nothing to show (no slate, picks not graded yet) is skipped, not an error.
Everything is logged to ~/.konjure/video_daily.log.

    python video_daily.py [YYYY-MM-DD] [--no-upload]
"""
from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys
import traceback

ROOT = pathlib.Path(__file__).parent
LOG = pathlib.Path.home() / ".konjure" / "video_daily.log"
TAGS = ["NBA", "NBA results", "sports analytics", "basketball", "Konjure Analytics", "Shorts"]


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
    title = f"NBA Picks Graded {night.month}/{night.day}: We Went {w}-{l_} #Shorts"
    lines = "".join(f"{g['pick_label']}: {g['outcome'].upper()} ({g['away']} {g['away_pts']}, "
                    f"{g['home']} {g['home_pts']})\n" for g in pub)
    desc = (f"Every NBA pick we make is logged before tip-off and graded in public, losses included.\n\n"
            f"{night.strftime('%A, %B')} {night.day}: {w}-{l_}\n{lines}\n"
            f"Season: {rec['record']} ({rec['pct']}%). Break-even at standard odds is 52.4%.\n\n"
            f"Every pick, every result: https://konjureanalytics.com\n"
            f"21+. Not betting advice. Gambling problem? Call 1-800-GAMBLER.\n"
            f"Narration and host are AI-generated.")
    return title, desc


def run(night: datetime.date | None = None, upload: bool = True) -> list:
    night = night or datetime.date.today() - datetime.timedelta(days=1)
    pulled = subprocess.run(["git", "pull", "-q", "--rebase", "--autostash"], cwd=ROOT,
                            capture_output=True, text=True)
    if pulled.returncode:
        _log(f"git pull failed, building from local data: {pulled.stderr.strip()[:200]}")
    import video_builder as vb
    done = []
    for fmt, build in (("results", vb.results_short), ("top5", vb.top5_short)):
        try:
            path = build(night)
        except SystemExit as e:                       # nothing to show for that night
            _log(f"{fmt} {night}: skipped ({e})")
            continue
        except Exception:
            _log(f"{fmt} {night}: build FAILED\n{traceback.format_exc()}")
            continue
        if fmt == "results":
            title, desc = _results_meta(night)
        else:
            title = f"Top 5 NBA Players {night.month}/{night.day}, Graded #Shorts"
            desc = path.with_suffix(".txt").read_text(encoding="utf-8") + "Narration and host are AI-generated.\n"
        _log(f"{fmt} {night}: built {path.name}")
        if upload:
            try:
                import youtube_upload as yu
                vid = yu.upload(path, title, desc, TAGS, key=f"{fmt}-{night.isoformat()}")
                _log(f"{fmt} {night}: uploaded PRIVATE https://youtu.be/{vid}")
                done.append((fmt, vid))
            except SystemExit as e:
                _log(f"{fmt} {night}: not uploaded ({e})")
            except Exception:
                _log(f"{fmt} {night}: upload FAILED\n{traceback.format_exc()}")
    return done


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = datetime.date.fromisoformat(args[0]) if args else None
    run(n, upload="--no-upload" not in sys.argv)
