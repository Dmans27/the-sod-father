"""
Push notifications to the iOS app (Apple Push Notification service, APNs).

The app (static/js/native.js) asks iOS for permission, gets a device token and sends it here. When something
happens that a reader would want to know about, the code calls notify(reader_id, title, body, url):

  friends.py      someone sent you a friend request / accepted yours
  group_maps.py   a friend added you to a group map
  community.py    someone liked a walk you posted

Tapping the notification opens `url` inside the app.

  POST /api/push/register    {token, test?}   save this phone's token for the signed-in reader
  POST /api/push/unregister  {token}          forget it (Notifications switched off in the app)
  GET  /admin/push                            owner only: is it set up, which phones are registered, what
                                              Apple said lately, and a "Send test" button per phone
  Signing out (readers.py) and deleting the account (moderation.py) forget the reader's tokens too.

Render settings (from developer.apple.com -> Certificates, IDs & Profiles -> Keys -> an APNs key):
  APNS_KEY        the contents of the .p8 file (or the same, base64-encoded)
  APNS_KEY_ID     the key's 10-character ID
  APNS_TEAM_ID    your 10-character Team ID (Membership page)
  APNS_TOPIC      the app's bundle ID (default com.mansfield.app)
Without them notify() quietly does nothing, so the site runs fine locally.

Tokens from App Store / TestFlight builds go to Apple's production server; builds run straight from Xcode
use the sandbox. Each token is tried on production first and remembered on whichever accepts it.
Sending happens on a background thread so no request waits for Apple.
"""
import base64
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import db
from readers import current_reader, reader_required_json

push_bp = Blueprint("push", __name__)

HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
MAX_TOKENS = 10            # phones per reader
REPEAT_GAP = 60 * 60       # the same event (e.g. like, unlike, like) pings at most once an hour

_ready = False
_jwt = {"token": None, "at": 0}
_recent = {}
_lock = threading.Lock()
_log = deque(maxlen=40)   # what happened lately, for /admin/push
_warned = {"config": False}


def _note(what, token="", detail=""):
    _log.appendleft({"at": datetime.now(timezone.utc).strftime("%H:%M:%S"), "what": what,
                     "token": (token or "")[-6:], "detail": str(detail)[:200]})
    print("push:", what, (token or "")[-6:], detail)


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS push_tokens (
            token       TEXT PRIMARY KEY,
            reader_id   TEXT NOT NULL,
            platform    TEXT NOT NULL,
            env         TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_push_tokens_reader ON push_tokens (reader_id)")
    _ready = True


def _key():
    raw = (os.environ.get("APNS_KEY") or "").strip()
    if not raw:
        return ""
    if "BEGIN" not in raw:
        try:
            raw = base64.b64decode(raw).decode()
        except Exception:  # noqa: BLE001
            return ""
    return raw.replace("\\n", "\n")


def configured():
    return bool(_key() and os.environ.get("APNS_KEY_ID") and os.environ.get("APNS_TEAM_ID"))


def _topic():
    return (os.environ.get("APNS_TOPIC") or "com.mansfield.app").strip()


def _auth():
    """The signed token APNs wants; Apple asks for a new one at most every 20-60 minutes."""
    with _lock:
        if _jwt["token"] and time.time() - _jwt["at"] < 40 * 60:
            return _jwt["token"]
        import jwt   # PyJWT[crypto]
        tok = jwt.encode({"iss": os.environ["APNS_TEAM_ID"].strip(), "iat": int(time.time())}, _key(),
                         algorithm="ES256", headers={"kid": os.environ["APNS_KEY_ID"].strip()})
        _jwt.update(token=tok, at=time.time())
        return tok


# ── Tokens ───────────────────────────────────────────────────────────────

def _clean_token(t):
    t = str(t or "").strip().lower()
    return t if 32 <= len(t) <= 200 and all(c in "0123456789abcdef" for c in t) else ""


def save_token(reader_id, token, platform="ios"):
    _tables()
    now = db.now_iso()
    db.execute("DELETE FROM push_tokens WHERE token = :t", t=token)   # a phone that changed hands moves over
    db.execute("INSERT INTO push_tokens (token, reader_id, platform, env, created_at, updated_at) "
               "VALUES (:t, :r, :p, NULL, :n, :n)", t=token, r=reader_id, p=platform, n=now)
    extra = db.query("SELECT token FROM push_tokens WHERE reader_id = :r ORDER BY updated_at DESC", r=reader_id)[MAX_TOKENS:]
    for row in extra:
        db.execute("DELETE FROM push_tokens WHERE token = :t", t=row["token"])


def forget(token):
    token = _clean_token(token)
    if token:
        _tables()
        db.execute("DELETE FROM push_tokens WHERE token = :t", t=token)


def delete_reader_data(reader_id):
    _tables()
    db.execute("DELETE FROM push_tokens WHERE reader_id = :r", r=reader_id)


# ── Sending ──────────────────────────────────────────────────────────────

def _send_one(client, row, payload):
    """Returns the env Apple accepted it on, or None (and drops tokens Apple says are dead)."""
    envs = [row["env"]] if row.get("env") in HOSTS else ["production", "sandbox"]
    for env in envs:
        try:
            r = client.post(f"{HOSTS[env]}/3/device/{row['token']}", json=payload, headers={
                "authorization": "bearer " + _auth(), "apns-topic": _topic(),
                "apns-push-type": "alert", "apns-priority": "10"})
        except Exception as exc:  # noqa: BLE001
            _note("send failed", row["token"], exc)
            return None
        if r.status_code == 200:
            if row.get("env") != env:
                db.execute("UPDATE push_tokens SET env = :e WHERE token = :t", e=env, t=row["token"])
            _note("delivered", row["token"], env)
            return env
        reason = ""
        try:
            reason = (r.json() or {}).get("reason", "")
        except Exception:  # noqa: BLE001
            pass
        if r.status_code == 410 or reason in ("Unregistered", "DeviceTokenNotForTopic"):
            db.execute("DELETE FROM push_tokens WHERE token = :t", t=row["token"])
            _note("dropped token", row["token"], reason or "410 Unregistered")
            return None
        if reason == "BadDeviceToken" and env != envs[-1]:
            continue   # maybe a sandbox (Xcode) token: try there
        if reason == "BadDeviceToken":
            db.execute("DELETE FROM push_tokens WHERE token = :t", t=row["token"])
            _note("dropped token", row["token"], "BadDeviceToken on both servers: check APNS_TOPIC is the app's bundle ID")
        _note(f"apple said {r.status_code}", row["token"], f"{reason} ({env})")
        return None
    return None


def _deliver(rows, payload):
    try:
        import httpx   # httpx[http2]: APNs only speaks HTTP/2
        with httpx.Client(http2=True, timeout=10) as client:
            for row in rows:
                _send_one(client, row, payload)
    except Exception as exc:  # noqa: BLE001
        _note("delivery failed", "", exc)


def notify(reader_id, title, body, url="/walk", key=None):
    """Ping every phone the reader has the app on. Never raises; returns at once."""
    try:
        if not reader_id:
            return
        if not configured():
            if not _warned["config"]:
                _warned["config"] = True
                _note("not set up", "", "APNS_KEY / APNS_KEY_ID / APNS_TEAM_ID missing on Render, so nothing is sent")
            return
        if key:
            k, now = (reader_id, key), time.time()
            with _lock:
                if now - _recent.get(k, 0) < REPEAT_GAP:
                    return
                _recent[k] = now
                if len(_recent) > 5000:
                    for old in [x for x, t in _recent.items() if now - t > REPEAT_GAP]:
                        _recent.pop(old, None)
        _tables()
        rows = db.query("SELECT token, env FROM push_tokens WHERE reader_id = :r", r=reader_id)
        if not rows:
            _note("no phone registered", "", f"reader {reader_id[-6:]} has no token, so nothing to send")
            return
        payload = {"aps": {"alert": {"title": str(title)[:120], "body": str(body)[:240]}, "sound": "default"},
                   "url": url if str(url).startswith("/") and not str(url).startswith("//") else "/walk"}
        threading.Thread(target=_deliver, args=(rows, payload), daemon=True).start()
    except Exception as exc:  # noqa: BLE001
        print("push: notify failed:", exc)


# ── Routes ───────────────────────────────────────────────────────────────

@push_bp.post("/api/push/register")
@reader_required_json
def api_register():
    reader = current_reader()
    data = request.get_json(silent=True) or {}
    token = _clean_token(data.get("token"))
    if not token:
        return jsonify(error="bad_token"), 400
    save_token(reader["id"], token, "ios")
    _note("token saved", token, f"{reader['display_name']}{' (test asked)' if data.get('test') else ''}")
    if data.get("test"):
        notify(reader["id"], "Notifications are on", "We’ll let you know when friends add you or share a map.",
               "/account/profile")
    return jsonify(ok=True, live=configured())


@push_bp.post("/api/push/unregister")
@reader_required_json
def api_unregister():
    forget((request.get_json(silent=True) or {}).get("token"))
    return jsonify(ok=True)


# ── Admin: is it working? ────────────────────────────────────────────────

def _check():
    """Settings and libraries, each with ok/problem text."""
    out = []
    for name in ("APNS_KEY_ID", "APNS_TEAM_ID"):
        v = (os.environ.get(name) or "").strip()
        out.append((name, bool(v) and len(v) == 10, v or "not set", "should be 10 letters/numbers"))
    k = _key()
    out.append(("APNS_KEY", "BEGIN PRIVATE KEY" in k, "set" if k else "not set",
                "paste the whole .p8, including the BEGIN/END lines"))
    out.append(("APNS_TOPIC", True, _topic(), ""))
    try:
        import httpx, h2  # noqa: F401
        out.append(("httpx + h2 installed", True, "yes", ""))
    except Exception as exc:  # noqa: BLE001
        out.append(("httpx + h2 installed", False, str(exc), "requirements.txt needs httpx[http2]"))
    if configured():
        try:
            _jwt.update(token=None, at=0)
            _auth()
            out.append(("Key signs a token", True, "yes", ""))
        except Exception as exc:  # noqa: BLE001
            out.append(("Key signs a token", False, str(exc)[:160], "the .p8 text or PyJWT[crypto] is wrong"))
    return out


@push_bp.get("/admin/push")
def admin_push():
    from admin import is_owner
    if not is_owner():
        return redirect(url_for("admin.login"))
    from readers import csrf_token
    _tables()
    phones = db.query("SELECT p.token, p.env, p.created_at, p.updated_at, r.display_name, r.email FROM push_tokens p "
                      "LEFT JOIN readers r ON r.id = p.reader_id ORDER BY p.updated_at DESC LIMIT 100")
    for ph in phones:
        ph["tail"] = ph["token"][-6:]
    return render_template("admin/push.html", checks=_check(), phones=phones, log=list(_log), csrf=csrf_token())


@push_bp.post("/admin/push/test")
def admin_push_test():
    """Sends right now (not on a thread) and shows exactly what Apple answered."""
    from admin import is_owner
    from readers import _csrf_ok
    if not is_owner() or not _csrf_ok():
        abort(403)
    tail = (request.form.get("tail") or "").strip().lower()
    _tables()
    row = next((r for r in db.query("SELECT token, env FROM push_tokens") if r["token"].endswith(tail)), None) if tail else None
    if not row:
        flash("That phone isn't registered any more.", "warning")
    elif not configured():
        flash("APNS_KEY, APNS_KEY_ID and APNS_TEAM_ID need to be set on Render first.", "warning")
    else:
        payload = {"aps": {"alert": {"title": "Test from LocalLane", "body": "If you can read this, notifications work."},
                           "sound": "default"}, "url": "/account/profile"}
        try:
            import httpx
            with httpx.Client(http2=True, timeout=10) as client:
                env = _send_one(client, row, payload)
        except Exception as exc:  # noqa: BLE001
            env = None
            _note("send failed", row["token"], exc)
        last = _log[0] if _log else {}
        flash(f"Apple accepted it ({env}). It should be on the phone now." if env else
              f"Not delivered: {last.get('what', '')} {last.get('detail', '')}", "success" if env else "warning")
    return redirect(url_for("push.admin_push"))
