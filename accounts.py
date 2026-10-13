"""
accounts.py — email + password accounts, and the gate in front of the Edge Finder.

Storage
-------
Accounts have to outlive a restart, and Streamlit Community Cloud wipes its disk on every
one, while the repository is public, so neither can hold them. Production storage is a
Supabase (hosted Postgres) table reached over its REST API with plain `requests`:

    create table if not exists konjure_users (
      email       text primary key,
      pw_hash     text not null,
      salt        text not null,
      created_at  timestamptz not null default now(),
      last_login  timestamptz
    );
    alter table konjure_users enable row level security;   -- no public policies: only the
                                                            -- service key can read or write

Streamlit secrets (or environment): SUPABASE_URL, SUPABASE_SERVICE_KEY. The service key
stays on the server; it is never sent to a browser.

Without those, a SQLite file OUTSIDE the repository (~/.konjure/accounts.db) is used so the
flow can be developed and tested on the home machine. On Streamlit Cloud with no Supabase
configured, sign-up is closed rather than writing accounts somewhere that will vanish.

Passwords are hashed with scrypt (hashlib, no extra dependency), a random 16-byte salt per
account, and compared in constant time. Plaintext passwords are never stored or logged.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import pathlib
import re
import secrets
import sqlite3
import time

_TABLE = "konjure_users"
_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
_MIN_PASSWORD = 8
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_MAX_FAILS, _COOLDOWN_S = 5, 60
_LOCAL_DB = pathlib.Path.home() / ".konjure" / "accounts.db"


def on_streamlit_cloud() -> bool:
    """Streamlit Community Cloud checks the repository out under /mount/src."""
    return pathlib.Path(__file__).resolve().as_posix().startswith("/mount/src")


def _secret(name: str) -> str:
    try:
        import streamlit as st
        v = st.secrets.get(name, "")
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get(name, "")


def _supabase():
    url, key = _secret("SUPABASE_URL").rstrip("/"), _secret("SUPABASE_SERVICE_KEY")
    return (url, key) if url and key else None


def backend() -> str:
    """'supabase', 'local' (SQLite, development only) or 'closed'."""
    if _supabase():
        return "supabase"
    return "closed" if on_streamlit_cloud() else "local"


# ── storage ───────────────────────────────────────────────────────────────────────────────

def _sb_request(method: str, params: dict | None = None, json=None, prefer: str | None = None):
    import requests
    url, key = _supabase()
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return requests.request(method, f"{url}/rest/v1/{_TABLE}", params=params, json=json,
                            headers=headers, timeout=15)


def _local():
    _LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_LOCAL_DB)
    con.execute(f"create table if not exists {_TABLE} (email text primary key, pw_hash text not null, "
                "salt text not null, created_at text not null, last_login text)")
    return con


def _get(email: str):
    """(pw_hash, salt) for an account, or None."""
    if backend() == "supabase":
        r = _sb_request("GET", params={"email": f"eq.{email}", "select": "pw_hash,salt"})
        r.raise_for_status()
        rows = r.json()
        return (rows[0]["pw_hash"], rows[0]["salt"]) if rows else None
    with _local() as con:
        row = con.execute(f"select pw_hash, salt from {_TABLE} where email = ?", (email,)).fetchone()
        return tuple(row) if row else None


def _insert(email: str, pw_hash: str, salt: str) -> bool:
    """True if created, False if the email already has an account."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if backend() == "supabase":
        r = _sb_request("POST", json={"email": email, "pw_hash": pw_hash, "salt": salt, "created_at": now},
                        prefer="return=minimal")
        if r.status_code == 409:
            return False
        r.raise_for_status()
        return True
    try:
        with _local() as con:
            con.execute(f"insert into {_TABLE} (email, pw_hash, salt, created_at) values (?, ?, ?, ?)",
                        (email, pw_hash, salt, now))
        return True
    except sqlite3.IntegrityError:
        return False


def _touch(email: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    try:
        if backend() == "supabase":
            _sb_request("PATCH", params={"email": f"eq.{email}"}, json={"last_login": now},
                        prefer="return=minimal")
        else:
            with _local() as con:
                con.execute(f"update {_TABLE} set last_login = ? where email = ?", (now, email))
    except Exception:
        pass                                  # bookkeeping only; never blocks a sign-in


# ── the account rules ─────────────────────────────────────────────────────────────────────

def _hash(password: str, salt_hex: str) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), **_SCRYPT).hex()


def normalize(email: str) -> str:
    return str(email or "").strip().lower()


def create_account(email: str, password: str) -> tuple[bool, str]:
    email = normalize(email)
    if backend() == "closed":
        return False, "Sign-ups are not open yet."
    if not _EMAIL.match(email):
        return False, "Enter a valid email address."
    if len(password or "") < _MIN_PASSWORD:
        return False, f"Use a password of at least {_MIN_PASSWORD} characters."
    salt = secrets.token_hex(16)
    try:
        created = _insert(email, _hash(password, salt), salt)
    except Exception:
        return False, "Could not create the account right now. Try again shortly."
    return (True, "Account created.") if created else (False, "That email already has an account. Sign in instead.")


def verify(email: str, password: str) -> bool:
    email = normalize(email)
    if backend() == "closed" or not email or not password:
        return False
    try:
        row = _get(email)
    except Exception:
        return False
    if row is None:
        _hash(password, secrets.token_hex(16))   # same work either way: no timing hint about which emails exist
        return False
    ok = hmac.compare_digest(_hash(password, row[1]), row[0])
    if ok:
        _touch(email)
    return ok


# ── the gate ──────────────────────────────────────────────────────────────────────────────

def signed_in_email():
    import streamlit as st
    return st.session_state.get("_acct_email")


def sign_out() -> None:
    import streamlit as st
    st.session_state.pop("_acct_email", None)


def gate(where: str, owner_bypass: bool = True) -> bool:
    """
    True if the visitor may see a gated page. Otherwise draws the sign-in / create-account
    forms in place and returns False, so the caller skips the page body entirely (nothing
    gated is computed or sent to the browser).

    `where` keeps each placement's widgets distinct: several gated tabs render on one page.
    Running on the home machine (not Streamlit Cloud) is the owner's own dashboard, which
    skips the gate unless owner_bypass is False.
    """
    import streamlit as st
    # KONJURE_FORCE_GATE=1 shows the gate on the home machine too, for testing the flow.
    if owner_bypass and not on_streamlit_cloud() and not os.environ.get("KONJURE_FORCE_GATE"):
        return True
    if signed_in_email():
        return True
    st.markdown("#### 🔒 Create a free account to use the Edge Finder")
    st.caption("The Edge Finder, the sportsbook boards and the parlay builders are free with an "
               "account. We only store your email and a one-way hash of your password — see the "
               "[privacy policy](https://konjureanalytics.com/privacy.html).")
    if backend() == "closed":
        st.info("Sign-ups open soon.")
        return False
    tab_in, tab_up = st.tabs(["Sign in", "Create account"])
    fails = st.session_state.setdefault("_acct_fails", [])
    now = time.time()
    fails[:] = [t for t in fails if now - t < _COOLDOWN_S]
    with tab_in:
        with st.form(f"acct_in_{where}"):
            e = st.text_input("Email", key=f"acct_in_e_{where}")
            p = st.text_input("Password", type="password", key=f"acct_in_p_{where}")
            go = st.form_submit_button("Sign in")
        if go:
            if len(fails) >= _MAX_FAILS:
                st.error("Too many attempts. Wait a minute and try again.")
            elif verify(e, p):
                st.session_state["_acct_email"] = normalize(e)
                st.session_state["_acct_fails"] = []
                st.rerun()
            else:
                fails.append(now)
                time.sleep(0.5)
                st.error("Email or password is incorrect.")
        st.caption("Forgot your password? Email info@konjureanalytics.com from your account's address.")
    with tab_up:
        with st.form(f"acct_up_{where}"):
            e2 = st.text_input("Email", key=f"acct_up_e_{where}")
            p2 = st.text_input("Password (8+ characters)", type="password", key=f"acct_up_p_{where}")
            p3 = st.text_input("Confirm password", type="password", key=f"acct_up_c_{where}")
            agree = st.checkbox("I am 18 or older and agree to the privacy policy", key=f"acct_up_a_{where}")
            go2 = st.form_submit_button("Create account")
        if go2:
            if p2 != p3:
                st.error("The passwords do not match.")
            elif not agree:
                st.error("Please confirm you are 18+ and agree to the privacy policy.")
            else:
                ok, msg = create_account(e2, p2)
                if ok:
                    st.session_state["_acct_email"] = normalize(e2)
                    st.rerun()
                else:
                    st.error(msg)
    return False
