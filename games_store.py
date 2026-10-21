"""
games_store.py — storage for the Konjure games (games.py): daily slates, entries and scores.

One table, one row per thing, keyed by a readable id so the rules live in the key:
  btm_slate:<date>            Beat the Market's five props for a day (shared by everyone)
  btm_entry:<date>:<email>    one person's Beat the Market entry for that day
  gtg_play:<date>:<email>     one person's Guess the Grade result for that day

Supabase table `konjure_games` on the hosted app (create it with GAMES_SQL), SQLite at
~/.konjure/games.db on the home machine, the same split as accounts.py and the parlay tickets.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sqlite3

import accounts as _acct

_TABLE = "konjure_games"
_LOCAL_DB = pathlib.Path.home() / ".konjure" / "games.db"

GAMES_SQL = f"""
create table if not exists {_TABLE} (
  id text primary key,
  kind text not null,
  game_date date not null,
  email text,
  payload jsonb not null,
  score double precision,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists {_TABLE}_kind_date on {_TABLE} (kind, game_date);
create index if not exists {_TABLE}_email on {_TABLE} (email);
alter table {_TABLE} enable row level security;
"""


def backend() -> str:
    return _acct.backend()


def _sb(method: str, params: dict | None = None, body=None, prefer: str | None = None):
    import requests
    url, key = _acct._supabase()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    if prefer:
        headers["Prefer"] = prefer
    r = requests.request(method, f"{url}/rest/v1/{_TABLE}", params=params, json=body,
                         headers=headers, timeout=15)
    r.raise_for_status()
    return r


def _local():
    _LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_LOCAL_DB)
    con.execute(f"create table if not exists {_TABLE} (id text primary key, kind text not null, "
                "game_date text not null, email text, payload text not null, score real, "
                "created_at text not null, updated_at text not null)")
    return con


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def get(row_id: str) -> dict | None:
    if backend() == "supabase":
        rows = _sb("GET", params={"id": f"eq.{row_id}", "select": "*"}).json()
        return rows[0] if rows else None
    with _local() as con:
        con.row_factory = sqlite3.Row
        r = con.execute(f"select * from {_TABLE} where id = ?", (row_id,)).fetchone()
    if not r:
        return None
    r = dict(r)
    r["payload"] = json.loads(r["payload"])
    return r


def put(row_id: str, kind: str, game_date: str, payload: dict, email: str | None = None,
        score: float | None = None, insert_only: bool = False) -> bool:
    """Write a row. With insert_only, an existing row is left alone and False is returned."""
    row = {"id": row_id, "kind": kind, "game_date": game_date, "email": email,
           "payload": payload, "score": score, "updated_at": _now()}
    if backend() == "supabase":
        if insert_only:
            r = _sb("POST", body=row, prefer="resolution=ignore-duplicates,return=representation")
            return bool(r.json())
        _sb("POST", body=row, prefer="resolution=merge-duplicates,return=minimal")
        return True
    with _local() as con:
        if insert_only:
            cur = con.execute(f"insert or ignore into {_TABLE} values (?,?,?,?,?,?,?,?)",
                              (row_id, kind, game_date, email, json.dumps(payload), score, _now(), _now()))
            return cur.rowcount == 1
        con.execute(f"insert into {_TABLE} values (?,?,?,?,?,?,?,?) on conflict(id) do update set "
                    "payload = excluded.payload, score = excluded.score, updated_at = excluded.updated_at",
                    (row_id, kind, game_date, email, json.dumps(payload), score, _now(), _now()))
    return True


def rows(kind: str, game_date: str | None = None, email: str | None = None) -> list:
    if backend() == "supabase":
        p = {"kind": f"eq.{kind}", "select": "*", "limit": 5000}
        if game_date:
            p["game_date"] = f"eq.{game_date}"
        if email:
            p["email"] = f"eq.{email}"
        return _sb("GET", params=p).json()
    q, args = f"select * from {_TABLE} where kind = ?", [kind]
    if game_date:
        q += " and game_date = ?"; args.append(game_date)
    if email:
        q += " and email = ?"; args.append(email)
    with _local() as con:
        con.row_factory = sqlite3.Row
        out = [dict(r) for r in con.execute(q, args)]
    for r in out:
        r["payload"] = json.loads(r["payload"])
    return out


def handle(email: str | None) -> str:
    """How a player appears on a leaderboard: the email's name part, masked."""
    if not email:
        return "guest"
    name = email.split("@")[0]
    return name if len(name) <= 3 else name[:3] + "•" * min(4, len(name) - 3)
