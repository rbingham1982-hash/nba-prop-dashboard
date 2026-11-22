"""
data_sync.py — fetch the logs and data snapshots from the main repo at runtime.

Why
---
Streamlit Cloud stuck on "Cloning repository…" from mid-November 2026. The repo had grown
to 73 MB of history across ~690 commits, nearly all of it the cloud board runs recommitting
the parlay logs (MLB 28 MB, WNBA 13 MB, NFL 11 MB, NBA 11 MB) several times a day. A host
that rebuilds from that repo slows down with every run.

The fix is to deploy the app from a slim repo (code only, no history of the logs) and have
it pull the current data files from this repo over HTTPS when it starts. A side benefit:
the app sees new board runs without a redeploy.

How
---
* Active only when a file named DATA_REMOTE sits next to this module (the slim deploy
  ships one, containing "owner/repo@branch"). In the full repo there is no such file and
  sync() returns immediately, so local runs read the checkout exactly as before.
* One GitHub API call lists the repo tree with each file's blob SHA. Only files whose SHA
  differs from the last download are fetched (raw.githubusercontent.com, parallel).
* Synced: root-level *.json logs and everything under nba_data/.
* At most once per _TTL seconds per process. Fails open: if GitHub is unreachable the app
  runs on whatever it already has.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
_MARKER = ROOT / "DATA_REMOTE"
_STATE = ROOT / ".data_sync_state.json"
_TTL = 10 * 60
_LOCK = threading.Lock()
_last = 0.0


def _remote() -> tuple[str, str] | None:
    if not _MARKER.exists():
        return None
    spec = _MARKER.read_text(encoding="utf-8").strip()
    repo, _, branch = spec.partition("@")
    return repo.strip(), (branch.strip() or "main")


def _wanted(path: str) -> bool:
    if "/" not in path:
        return path.endswith(".json")
    return path.startswith("nba_data/")


def _load_state() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sync(force: bool = False) -> dict:
    """Bring the data files up to date. Returns {"fetched": n, "skipped": reason|None}."""
    global _last
    remote = _remote()
    if remote is None:
        return {"fetched": 0, "skipped": "full repo"}
    if not force and time.time() - _last < _TTL:
        return {"fetched": 0, "skipped": "fresh"}
    with _LOCK:
        if not force and time.time() - _last < _TTL:
            return {"fetched": 0, "skipped": "fresh"}
        try:
            n = _sync(*remote)
        except Exception as e:                       # fail open
            print(f"data_sync: {e}; using files already on disk", file=sys.stderr)
            n = 0
        _last = time.time()
        return {"fetched": n, "skipped": None}


def _sync(repo: str, branch: str) -> int:
    import requests
    from concurrent.futures import ThreadPoolExecutor

    headers = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")             # optional: raises the API rate limit
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    state = _load_state()
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                         headers=headers, timeout=30)
        r.raise_for_status()
        tree = [t for t in r.json().get("tree", [])
                if t.get("type") == "blob" and _wanted(t["path"])]
        todo = [t for t in tree
                if state.get(t["path"]) != t["sha"] or not (ROOT / t["path"]).exists()]
    except Exception as e:
        # The tree API allows 60 unauthenticated calls an hour per IP, and a shared host can
        # exhaust that. Fall back to the file list bundled at build time (DATA_FILES), fetched
        # from raw.githubusercontent.com with ETags so unchanged files cost a 304.
        print(f"data_sync: tree listing failed ({e}); using bundled DATA_FILES", file=sys.stderr)
        listed = ROOT / "DATA_FILES"
        paths = listed.read_text(encoding="utf-8").split() if listed.exists() else []
        todo = [{"path": p, "sha": None} for p in paths]

    def fetch(t):
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/{t['path']}"
        dest = ROOT / t["path"]
        h = {}
        etag = state.get("etag:" + t["path"])
        if t["sha"] is None and etag and dest.exists():
            h["If-None-Match"] = etag
        resp = requests.get(url, headers=h, timeout=120)
        if resp.status_code == 304:
            return t["path"], state.get(t["path"]), etag, False
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(resp.content)
        os.replace(tmp, dest)                        # never leave a half-written log
        return t["path"], t["sha"], resp.headers.get("ETag"), True

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in [ex.submit(fetch, t) for t in todo]:
            try:
                path, sha, etag, changed = fut.result()
                if sha:
                    state[path] = sha
                if etag:
                    state["etag:" + path] = etag
                done += changed
            except Exception as e:
                print(f"data_sync: {e}", file=sys.stderr)
    _STATE.write_text(json.dumps(state), encoding="utf-8")
    return done


if __name__ == "__main__":
    t = time.time()
    print(sync(force=True), f"{time.time() - t:.1f}s")
