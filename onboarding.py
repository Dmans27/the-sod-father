"""
Onboarding: confirm your email with a 6-digit code, then a few questions, one per
screen (templates/welcome.html), right after creating an account.

  GET  /welcome                 the screens (signed in). ?step=verify|profile, ?next=where to go after
  POST /api/verify/send         email a new code (one a minute, VERIFY_SENDS_PER_DAY a day)
  POST /api/verify/check        {code} -> verified (VERIFY_ATTEMPTS tries per code, VERIFY_MINUTES to use it)
  POST /api/profile             {purpose, city: {name, label, lat, lng}, interests: [...], walk_length, avoid: [...]}

Verified email: until then people can browse and plan walks, but posting walks,
rating places, commenting and the newsletter answer {error, verify: true} with a
403 (verify_block), and the page shows the "Verify your email" card
(templates/_verify_prompt.html). The requirement is on once Resend sends from your
own domain (NEWSLETTER_FROM); REQUIRE_VERIFIED_EMAIL=1 or 0 on Render overrides that.

The codes go out through Resend, like the newsletter (newsletter_send.send_email,
RESEND_API_KEY and NEWSLETTER_FROM). With Resend's test sender they only reach
your own Resend account email: verify a domain in Resend to email everyone.
Without RESEND_API_KEY (running locally) the code is printed to the log.

The profile answers shape the app: the city opens your feed there (the mf_city
cookie), and the walk planner hears what you like, how far you like to walk and
what to avoid (planner_note).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import db
from content import CATEGORIES
from readers import current_reader, reader_required_json, safe_next

onboarding_bp = Blueprint("onboarding", __name__)

CODE_DIGITS = 6
VERIFY_MINUTES = 15
VERIFY_ATTEMPTS = 5
RESEND_SECONDS = 45
VERIFY_SENDS_PER_DAY = 8

PURPOSES = {"local": "Live here", "new": "New in town", "visiting": "Visiting", "planning": "Planning a trip"}
INTERESTS = {
    "coffee": "Coffee", "food": "Food", "bars": "Bars & cocktails", "wine": "Wine", "museums": "Museums & art",
    "parks": "Parks & nature", "shopping": "Shopping", "history": "History & architecture", "nightlife": "Nightlife",
    "markets": "Markets", "sweets": "Bakeries & sweets", "hidden": "Hidden gems",
}
WALK_LENGTHS = {"short": ("Short strolls", "under 30 minutes, about 2 km"),
                "half": ("Half a day", "1 to 3 hours, about 5 km"),
                "all": ("All-day explorer", "a whole day, 10 km or more")}
AVOID = {"stairs": "Stairs", "hills": "Steep hills", "busy": "Busy streets", "night": "Walking after dark"}

_ready = False


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS reader_verification (
            reader_id    TEXT PRIMARY KEY,
            email        TEXT NOT NULL,
            code_hash    TEXT,
            expires_at   REAL,
            attempts     INTEGER NOT NULL DEFAULT 0,
            last_sent    REAL,
            sent_day     TEXT,
            sent_count   INTEGER NOT NULL DEFAULT 0,
            verified_at  TEXT
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS reader_profiles (
            reader_id    TEXT PRIMARY KEY,
            purpose      TEXT,
            city_name    TEXT,
            city_label   TEXT,
            city_lat     REAL,
            city_lng     REAL,
            interests    TEXT,
            walk_length  TEXT,
            avoid        TEXT,
            onboarded_at TEXT,
            updated_at   TEXT
        )""")
    _ready = True


def required():
    """Is a verified email needed to post, rate and comment? REQUIRE_VERIFIED_EMAIL=1/0 decides; when it isn't
    set, only once the codes can actually reach people (Resend set up with your own domain, not its test sender)."""
    setting = (os.environ.get("REQUIRE_VERIFIED_EMAIL") or "").strip().lower()
    if setting:
        return setting not in ("0", "false", "no", "off")
    import newsletter_send
    return bool(newsletter_send.api_key()) and not newsletter_send.is_test_sender()


def can_send():
    """Can a code reach the reader's inbox? Resend with your own domain -- or running locally
    (not on Render), where the code is printed to the log instead."""
    import newsletter_send
    if newsletter_send.api_key():
        return not newsletter_send.is_test_sender()
    return not os.environ.get("RENDER")


# ── Verification ─────────────────────────────────────────────────────────

def _hash(reader_id, code):
    secret = (os.environ.get("SECRET_KEY") or "verify").encode()
    return hmac.new(secret, f"{reader_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _row(reader_id):
    _tables()
    return db.query_one("SELECT * FROM reader_verification WHERE reader_id = :r", r=reader_id)


def is_verified(reader):
    """True when the reader confirmed the email address they have now. Never fails (False)."""
    if not reader:
        return False
    try:
        row = _row(reader["id"])
    except Exception as exc:  # noqa: BLE001
        print("verify: could not check:", exc)
        return False
    return bool(row and row.get("verified_at") and (row.get("email") or "").lower() == (reader.get("email") or "").lower())


def verify_block(reader, what="do that"):
    """A 403 JSON reply when the reader still has to verify their email (or None when they're fine)."""
    if not required() or not reader or is_verified(reader):
        return None
    return jsonify(error=f"Please verify your email to {what}.", verify=True), 403


def email_parts(code):
    """(subject, html, text) for the code email: Matcha House colours, the code big and easy to copy.
    Table layout and inline styles so it looks the same in Gmail, Outlook and Apple Mail."""
    subject = f"{code} is your LocalLane code"
    text = (f"Your LocalLane code is {code}\n\nEnter it in the app to confirm this is your email. "
            f"It works for {VERIFY_MINUTES} minutes.\n\nIf you didn't create an account, you can ignore this email.")
    digits = "".join(
        f'<td style="width:44px;height:56px;padding:0 3px;">'
        f'<div style="width:42px;height:54px;line-height:54px;border-radius:12px;background:#eef5e8;border:1px solid #d6e6c8;'
        f'text-align:center;font:800 28px/54px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f4128;">{d}</div></td>'
        for d in str(code))
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{subject}</title></head>
<body style="margin:0;padding:0;background:#f6f3ec;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">Your code is {code}. It works for {VERIFY_MINUTES} minutes.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f3ec;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:460px;">
    <tr><td style="padding:0 6px 18px;font:700 22px Georgia,'Times New Roman',serif;color:#2f5e3a;">Local<span style="color:#5e9a4c;">Lane</span></td></tr>
    <tr><td style="background:#ffffff;border-radius:20px;padding:30px 26px;border:1px solid #ebe6dc;">
      <p style="margin:0 0 6px;font:800 12px -apple-system,Segoe UI,Helvetica,Arial,sans-serif;letter-spacing:1.2px;text-transform:uppercase;color:#2f5e3a;">Confirm your email</p>
      <h1 style="margin:0 0 14px;font:700 26px/1.2 Georgia,'Times New Roman',serif;color:#1f2522;">Here's your code</h1>
      <p style="margin:0 0 22px;font:400 16px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#4a524c;">Enter it in the app to confirm this is your email.</p>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 22px;"><tr>{digits}</tr></table>
      <p style="margin:0;font:400 14px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#6b736d;">It works for {VERIFY_MINUTES} minutes. If it runs out, tap <b style="color:#1f2522;">Send a new code</b> in the app.</p>
    </td></tr>
    <tr><td style="padding:18px 8px 0;font:400 12.5px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#8a8f87;">
      If you didn't create a LocalLane account, you can ignore this email. Nobody can sign in with this code alone.
    </td></tr>
  </table>
</td></tr></table></body></html>"""
    return subject, html, text


def _email(to, code):
    import newsletter_send
    subject, html, text = email_parts(code)
    if not newsletter_send.api_key():
        print(f"verify: (no RESEND_API_KEY) code for {to}: {code}", flush=True)
        return True
    try:
        ok, err = newsletter_send.send_email(to, subject, html, text)
    except Exception as exc:  # noqa: BLE001 -- e.g. Resend's quota used up
        ok, err = False, str(exc)
    if not ok:
        print("verify: could not email the code:", err, flush=True)
    return ok


def send_code(reader):
    """(ok, message) -- emails a fresh code, within the limits."""
    _tables()
    now, today = time.time(), datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = _row(reader["id"])
    if row and row.get("verified_at") and (row.get("email") or "").lower() == reader["email"].lower():
        return True, "Your email is already verified."
    if row and row.get("last_sent") and now - float(row["last_sent"]) < RESEND_SECONDS:
        return False, "We just sent a code. Give it a minute, then try again."
    sent_today = int(row.get("sent_count") or 0) if row and row.get("sent_day") == today else 0
    if sent_today >= VERIFY_SENDS_PER_DAY:
        return False, "That's a lot of codes for one day. Try again tomorrow."
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
    params = dict(r=reader["id"], e=reader["email"].lower(), h=_hash(reader["id"], code), x=now + VERIFY_MINUTES * 60,
                  s=now, d=today, n=sent_today + 1)
    if row:
        db.execute("UPDATE reader_verification SET email = :e, code_hash = :h, expires_at = :x, attempts = 0, last_sent = :s, "
                   "sent_day = :d, sent_count = :n, verified_at = NULL WHERE reader_id = :r", **params)
    else:
        db.execute("INSERT INTO reader_verification (reader_id, email, code_hash, expires_at, attempts, last_sent, sent_day, sent_count) "
                   "VALUES (:r, :e, :h, :x, 0, :s, :d, :n)", **params)
    if not _email(reader["email"], code):
        return False, "We couldn't send the email just now. Try again in a minute."
    return True, f"We sent a code to {reader['email']}."


@onboarding_bp.post("/api/verify/send")
@reader_required_json
def api_send():
    ok, message = send_code(current_reader())
    return jsonify(ok=ok, message=message), (200 if ok else 429)


@onboarding_bp.post("/api/verify/check")
@reader_required_json
def api_check():
    reader = current_reader()
    code = re.sub(r"\D", "", str((request.get_json(silent=True) or {}).get("code") or ""))
    row = _row(reader["id"])
    if is_verified(reader):
        return jsonify(ok=True, verified=True)
    if not row or not row.get("code_hash"):
        return jsonify(ok=False, error="Ask for a code first."), 400
    if time.time() > float(row.get("expires_at") or 0):
        return jsonify(ok=False, error="That code has expired. Tap “Send a new code”."), 400
    if int(row.get("attempts") or 0) >= VERIFY_ATTEMPTS:
        return jsonify(ok=False, error="Too many tries. Tap “Send a new code”."), 400
    if len(code) != CODE_DIGITS or not hmac.compare_digest(_hash(reader["id"], code), row["code_hash"]):
        db.execute("UPDATE reader_verification SET attempts = attempts + 1 WHERE reader_id = :r", r=reader["id"])
        left = VERIFY_ATTEMPTS - int(row.get("attempts") or 0) - 1
        return jsonify(ok=False, error="That code isn't right." + (f" {left} tries left." if left > 0 else " Tap “Send a new code”.")), 400
    db.execute("UPDATE reader_verification SET verified_at = :t, code_hash = NULL WHERE reader_id = :r",
               t=db.now_iso(), r=reader["id"])
    return jsonify(ok=True, verified=True)


# ── Profile questions ────────────────────────────────────────────────────

def get_profile(reader_id):
    try:
        _tables()
        row = db.query_one("SELECT * FROM reader_profiles WHERE reader_id = :r", r=reader_id)
    except Exception as exc:  # noqa: BLE001
        print("profile: could not read:", exc)
        return {}
    if not row:
        return {}
    for k in ("interests", "avoid"):
        try:
            row[k] = json.loads(row.get(k) or "[]")
        except ValueError:
            row[k] = []
    return row


def planner_note(reader):
    """One line for the walk planner about this walker (empty when they told us nothing)."""
    if not reader:
        return ""
    p = get_profile(reader["id"])
    bits = []
    likes = [INTERESTS[k] for k in p.get("interests") or [] if k in INTERESTS]
    if likes:
        bits.append("likes " + ", ".join(likes).lower())
    if p.get("walk_length") in WALK_LENGTHS:
        bits.append("prefers walks of " + WALK_LENGTHS[p["walk_length"]][1])
    avoid = [AVOID[k] for k in p.get("avoid") or [] if k in AVOID]
    if avoid:
        bits.append("would rather avoid " + ", ".join(avoid).lower())
    if p.get("purpose") in PURPOSES:
        bits.append({"local": "lives in the city", "new": "is new in town", "visiting": "is visiting",
                     "planning": "is planning a trip"}[p["purpose"]])
    return ("About the walker (use only when the request doesn't say otherwise): " + "; ".join(bits) + ".") if bits else ""


@onboarding_bp.post("/api/profile")
@reader_required_json
def api_profile():
    reader = current_reader()
    data = request.get_json(silent=True) or {}
    purpose = data.get("purpose") if data.get("purpose") in PURPOSES else None
    interests = [k for k in dict.fromkeys(data.get("interests") or []) if k in INTERESTS]
    walk_length = data.get("walk_length") if data.get("walk_length") in WALK_LENGTHS else None
    avoid = [k for k in dict.fromkeys(data.get("avoid") or []) if k in AVOID]
    city = None
    c = data.get("city") if isinstance(data.get("city"), dict) else None
    if c:
        import city_feed
        city = city_feed._clean_city(c.get("name"), c.get("label"), c.get("lat"), c.get("lng"))
    _tables()
    now = db.now_iso()
    params = dict(r=reader["id"], p=purpose, cn=city and city["name"], cl=city and city["label"], la=city and city["lat"],
                  ln=city and city["lng"], i=json.dumps(interests), w=walk_length, a=json.dumps(avoid), t=now)
    if db.query_one("SELECT 1 AS x FROM reader_profiles WHERE reader_id = :r", r=reader["id"]):
        db.execute("UPDATE reader_profiles SET purpose = :p, city_name = :cn, city_label = :cl, city_lat = :la, city_lng = :ln, "
                   "interests = :i, walk_length = :w, avoid = :a, onboarded_at = COALESCE(onboarded_at, :t), updated_at = :t "
                   "WHERE reader_id = :r", **params)
    else:
        db.execute("INSERT INTO reader_profiles (reader_id, purpose, city_name, city_label, city_lat, city_lng, interests, "
                   "walk_length, avoid, onboarded_at, updated_at) VALUES (:r, :p, :cn, :cl, :la, :ln, :i, :w, :a, :t, :t)", **params)
    reply = jsonify(ok=True)
    if city:   # their feed opens on their city (city_feed.py)
        import city_feed
        reply.set_cookie(city_feed.COOKIE, quote(json.dumps(city, separators=(",", ":"))), max_age=365 * 86400,
                         samesite="Lax", secure=request.is_secure)
    return reply


def delete_reader_data(reader_id):
    _tables()
    db.execute("DELETE FROM reader_verification WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM reader_profiles WHERE reader_id = :r", r=reader_id)


# ── The screens ──────────────────────────────────────────────────────────

VIDEO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "video")


def tour_video():
    """The screen recording for the "How it works" tour: static/video/onboarding.mp4 (with an optional
    onboarding.jpg still for before it loads). None when there's no video: the tour is just the cards."""
    path = os.path.join(VIDEO_DIR, "onboarding.mp4")
    if not os.path.isfile(path):
        return None
    v = int(os.path.getmtime(path))
    poster = os.path.join(VIDEO_DIR, "onboarding.jpg")
    return {"src": f"/static/video/onboarding.mp4?v={v}",
            "poster": f"/static/video/onboarding.jpg?v={v}" if os.path.isfile(poster) else None}


@onboarding_bp.get("/welcome")
def welcome():
    reader = current_reader()
    nxt = safe_next(request.args.get("next"), "/")
    if not reader:
        return redirect(url_for("readers.login", next="/welcome?next=" + quote(nxt)))
    verified = is_verified(reader)
    sent = None
    if not verified and not (required() or can_send()):
        verified = True   # codes can't reach people yet (Resend's test sender): skip that screen for now
    if not verified:
        row = _row(reader["id"])
        fresh = row and row.get("code_hash") and time.time() < float(row.get("expires_at") or 0)
        if not fresh:   # first visit (just signed up) or the last code ran out: send one now
            ok, sent = send_code(reader)
    return render_template("welcome.html", verified=verified, sent=sent, next=nxt, step=request.args.get("step") or "",
                           tour_video=tour_video(),
                           profile=get_profile(reader["id"]), email=reader["email"], name=reader["display_name"],
                           purposes=PURPOSES, interests=INTERESTS, walk_lengths=WALK_LENGTHS, avoid=AVOID,
                           categories=CATEGORIES, active_category=None)


# ── Admin: is verification live, and what does the email look like ──────

@onboarding_bp.get("/admin/verify-email")
def admin_verify_email():
    """The owner's check: whether codes can reach people, the sender, a preview of the email, and a test send."""
    from admin import is_owner
    if not is_owner():
        return redirect(url_for("admin.login"))
    import newsletter_send
    from readers import csrf_token
    status = {"api_key": bool(newsletter_send.api_key()), "sender": newsletter_send.from_address(),
              "test_sender": newsletter_send.is_test_sender(), "can_send": can_send(), "required": required(),
              "override": (os.environ.get("REQUIRE_VERIFIED_EMAIL") or "").strip()}
    _subject, html, _text = email_parts("482913")
    return render_template("admin/verify_email.html", status=status, preview=html, csrf=csrf_token())


@onboarding_bp.post("/admin/verify-email/test")
def admin_verify_email_test():
    from admin import is_owner
    from readers import _csrf_ok
    if not is_owner() or not _csrf_ok():
        abort(403)
    import newsletter_send
    to = (request.form.get("to") or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to):
        flash("Enter the email address to send the test to.", "warning")
        return redirect(url_for("onboarding.admin_verify_email"))
    if not newsletter_send.api_key():
        flash("RESEND_API_KEY isn't set on Render, so nothing can be sent yet.", "warning")
        return redirect(url_for("onboarding.admin_verify_email"))
    subject, html, text = email_parts("".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS)))
    try:
        ok, err = newsletter_send.send_email(to, "Test: " + subject, html, text)
    except Exception as exc:  # noqa: BLE001
        ok, err = False, str(exc)
    flash(f"Test email sent to {to}. Check the inbox (and the spam folder)." if ok else f"Resend said: {err}",
          "success" if ok else "warning")
    return redirect(url_for("onboarding.admin_verify_email"))

