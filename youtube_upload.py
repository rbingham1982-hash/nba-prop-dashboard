"""
youtube_upload.py — put a built Short on the Konjure Analytics YouTube channel.

Uploads go up PRIVATE, always. YouTube itself keeps API uploads from an unaudited app private,
and it is also the approval step: the morning job uploads, the owner watches each Short in
YouTube Studio and flips the good ones to Public. Nothing reaches viewers without that click.

Every upload is marked as containing synthetic media (AI voice, AI avatar), which is what
YouTube's altered-content policy asks for.

Credentials live outside the repo, in ~/.konjure/:
  youtube_client_secret.json   the OAuth "Desktop app" client from Google Cloud
  youtube_token.json           written by `python youtube_upload.py connect` (one browser sign-in)

    python youtube_upload.py connect                  # one time: sign in and allow uploads
    python youtube_upload.py whoami                   # which channel the token uploads to
    python youtube_upload.py upload FILE.mp4 "Title" [description.txt]
"""
from __future__ import annotations

import json
import pathlib
import sys

_DIR = pathlib.Path.home() / ".konjure"
_SECRET = _DIR / "youtube_client_secret.json"
_TOKEN = _DIR / "youtube_token.json"
_LOG = _DIR / "youtube_uploads.json"
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
           "https://www.googleapis.com/auth/youtube.readonly"]
SPORTS = "17"


def connect():
    """One-time browser sign-in; saves a refresh token so later runs need no browser."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not _SECRET.exists():
        raise SystemExit(f"missing {_SECRET} (the OAuth Desktop client from Google Cloud)")
    creds = InstalledAppFlow.from_client_secrets_file(str(_SECRET), _SCOPES).run_local_server(port=0)
    _TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return whoami()


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not _TOKEN.exists():
        raise SystemExit("not connected: run `python youtube_upload.py connect` once")
    creds = Credentials.from_authorized_user_file(str(_TOKEN), _SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        _TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def whoami() -> str:
    ch = _service().channels().list(part="snippet", mine=True).execute().get("items", [])
    return ch[0]["snippet"]["title"] if ch else "(no channel on this account)"


def uploaded() -> dict:
    return json.loads(_LOG.read_text()) if _LOG.exists() else {}


def upload(path, title: str, description: str = "", tags=(), key: str | None = None) -> str:
    """Upload `path` as a PRIVATE Short. `key` (e.g. "top5-2026-11-10") makes it idempotent:
    a key already uploaded returns the earlier video id instead of uploading twice."""
    from googleapiclient.http import MediaFileUpload
    log = uploaded()
    if key and key in log:
        return log[key]["id"]
    body = {"snippet": {"title": title[:100], "description": description[:4900], "tags": list(tags)[:20],
                        "categoryId": SPORTS},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
    req = _service().videos().insert(part="snippet,status", body=body,
                                     media_body=MediaFileUpload(str(path), mimetype="video/mp4",
                                                                chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    if key:
        log[key] = {"id": resp["id"], "title": title, "file": str(path)}
        _LOG.write_text(json.dumps(log, indent=1), encoding="utf-8")
    return resp["id"]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "connect":
        print("connected to:", connect())
    elif cmd == "whoami":
        print(whoami())
    elif cmd == "upload" and len(sys.argv) >= 4:
        desc = pathlib.Path(sys.argv[4]).read_text(encoding="utf-8") if len(sys.argv) > 4 else ""
        vid = upload(sys.argv[2], sys.argv[3], desc)
        print(f"uploaded PRIVATE: https://youtu.be/{vid}  (publish it in YouTube Studio)")
    else:
        print(__doc__)
