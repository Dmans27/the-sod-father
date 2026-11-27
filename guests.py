"""
Guest accounts: "Sign in as guest" on the Sign in / Create account pages.

A guest gets a temporary account (no email, no password) that lives only in this browser's session cookie,
with GUEST_BUILDS free walk plans ("map builds"). After that the walk page asks them to create a free
account. Signing in or signing up ends the guest account.

  GET /account/guest?next=/walk     start a guest account (or keep the current one) and go on to `next`

What guests can do: browse everything and plan walks on the map (AI plans and voice) until their builds run out.
What still needs a real account: saving, posting, rating, comments, friends -- those show the usual sign-in sheet.

Only finished plans count; a "which area did you mean?" reply doesn't use a build.
Guest accounts older than GUEST_DAYS are deleted. To stop people clearing cookies for endless free builds, one
network address can start GUEST_PER_IP new guest accounts and plan GUEST_IP_BUILDS walks per day.

Settings (Render -> Environment), all optional:
  GUEST_BUILDS     free walk plans per guest (default 4)
  GUEST_DAYS       how long a guest account lasts (default 30)
  GUEST_PER_IP     new guest accounts per network address per day (default 6)
  GUEST_IP_BUILDS  guest walk plans per network address per day (default 16)
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, redirect, request, session

import db

guests_bp = Blueprint("guests", __name__)
_ready = False


def _int_env(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def builds_allowed():
    return _int_env("GUEST_BUILDS", 4)


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS guest_accounts (
            id          TEXT PRIMARY KEY,
            builds      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            last_seen   TEXT NOT NULL
        )""")
    _ready = True


def ip_key():
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    return hashlib.sha256(("guest-ip:" + ip).encode()).hexdigest()[:16]


def _cutoff():
    return (datetime.now(timezone.utc) - timedelta(days=_int_env("GUEST_DAYS", 30))).strftime("%Y-%m-%dT%H:%M:%SZ")


def current():
    """This browser's guest account {id, builds, left}, or None (also None for signed-in readers)."""
    gid = session.get("guest_account")
    if not gid or session.get("reader_id"):
        return None
    _tables()
    row = db.query_one("SELECT id, builds, created_at FROM guest_accounts WHERE id = :id", id=gid)
    if not row or row["created_at"] < _cutoff():
        session.pop("guest_account", None)
        return None
    row["left"] = max(0, builds_allowed() - int(row["builds"] or 0))
    return row


def start():
    """Make a guest account for this browser (or keep the one it has). Returns it, or None if this
    network address has started too many today."""
    g = current()
    if g:
        return g
    import ask as counters   # the daily counters (ask_usage table)
    ip = "guest:new:" + ip_key()
    try:
        if counters._used(ip) >= _int_env("GUEST_PER_IP", 6):
            return None
        counters._count(ip)
    except Exception as exc:  # noqa: BLE001
        print("guests: counter unavailable:", exc)
    _tables()
    if secrets.randbelow(20) == 0:   # now and then, clear out old guest accounts
        db.execute("DELETE FROM guest_accounts WHERE created_at < :c", c=_cutoff())
    gid, now = "g_" + secrets.token_hex(8), db.now_iso()
    db.execute("INSERT INTO guest_accounts (id, builds, created_at, last_seen) VALUES (:id, 0, :t, :t)", id=gid, t=now)
    session["guest_account"] = gid
    session.permanent = True
    return current()


def can_build():
    """(ok, reason): reason is 'guest_out' when the free builds are used up, 'busy' when this network
    address has planned a lot of guest walks today."""
    g = current()
    if not g:
        return False, "sign_in"
    if g["left"] <= 0:
        return False, "guest_out"
    import ask as counters
    try:
        if counters._used("guest:plans:" + ip_key()) >= _int_env("GUEST_IP_BUILDS", 16):
            return False, "busy"
    except Exception as exc:  # noqa: BLE001
        print("guests: counter unavailable:", exc)
    return True, ""


def use_build():
    """Count one finished plan. Returns how many free builds are left."""
    g = current()
    if not g:
        return 0
    db.execute("UPDATE guest_accounts SET builds = builds + 1, last_seen = :t WHERE id = :id", t=db.now_iso(), id=g["id"])
    try:
        import ask as counters
        counters._count("guest:plans:" + ip_key())
    except Exception as exc:  # noqa: BLE001
        print("guests: counter unavailable:", exc)
    return max(0, g["left"] - 1)


def end():
    """Signing in or creating an account: the guest account is no longer needed."""
    gid = session.pop("guest_account", None)
    if gid:
        try:
            _tables()
            db.execute("DELETE FROM guest_accounts WHERE id = :id", id=gid)
        except Exception as exc:  # noqa: BLE001
            print("guests: cleanup failed:", exc)


def _safe_next(raw):
    raw = str(raw or "")
    return raw if raw.startswith("/") and not raw.startswith("//") and "\\" not in raw else "/walk"


@guests_bp.get("/account/guest")
def start_guest():
    nxt = _safe_next(request.args.get("next"))
    if session.get("reader_id"):
        return redirect(nxt)
    if nxt == "/":
        nxt = "/walk"   # guests come for the map
    if not start():
        return redirect("/account/signup?next=" + nxt + "&guest=full")
    return redirect(nxt)
