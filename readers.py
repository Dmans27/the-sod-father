"""
Reader accounts: anyone can sign up (email + password) to save stories and
comment on them. Separate from the /admin portal and from the author
accounts in author_auth.py -- those are for people who *write* here; these
are for people who *read*.

  /account/signup, /account/login, POST /account/logout, /account (saved list)
  GET/POST /api/comments/<slug>, DELETE /api/comments/<comment_id>
  POST /api/save/<slug>   (toggles saved on/off)
  GET/POST /api/places, DELETE /api/places/<id>   (places to try, saved from
      the map pins inside stories -- see static/js/places.js)

Signed-out visitors can still read everything and use Share. Save and
Comment send them to sign in first, then bring them back to the story --
and a pending "save" is applied automatically once they're signed in.

Storage is db.py (Postgres on Render, SQLite locally).
"""
import os
import re
import secrets
import time
from datetime import timedelta
from functools import wraps

from flask import (Blueprint, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

import db
from authors import get_author, get_author_by_email
from content import CATEGORIES, get_article, map_embed_hosts

readers_bp = Blueprint("readers", __name__)

MAX_SAVED_PLACES = 500   # per reader -- a sanity cap, not a product limit
MIN_PASSWORD_LENGTH = 8
MAX_COMMENT_LENGTH = 1000
COMMENT_COOLDOWN_SECONDS = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Very small in-memory brake on password guessing (per server process --
# not a substitute for a real rate limiter, but stops casual hammering).
_login_attempts = {}
LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_ATTEMPTS = 10


# ── Helpers ──────────────────────────────────────────────────────────────

def current_reader():
    """The signed-in reader as a dict, or None. Cached for the request."""
    if "reader" in g.__dict__:
        return g.reader
    g.reader = None
    reader_id = session.get("reader_id")
    if reader_id:
        try:
            g.reader = db.query_one(
                "SELECT id, email, display_name FROM readers WHERE id = :id", id=reader_id)
        except Exception as exc:  # database down/misconfigured: act signed-out
            print("readers: could not load reader:", exc)
        if g.reader is None:
            session.pop("reader_id", None)
    return g.reader


def csrf_token():
    if not session.get("csrf"):
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def _csrf_ok():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf") or ""
    expected = session.get("csrf") or ""
    return bool(expected) and secrets.compare_digest(sent, expected)


def saved_slugs():
    reader = current_reader()
    if not reader:
        return set()
    try:
        rows = db.query("SELECT slug FROM saved_articles WHERE reader_id = :r", r=reader["id"])
    except Exception as exc:
        print("readers: could not load saved:", exc)
        return set()
    return {r["slug"] for r in rows}


def comment_counts():
    """{slug: number of comments} for the homepage's comment buttons."""
    try:
        rows = db.query("SELECT slug, COUNT(*) AS n FROM comments GROUP BY slug")
    except Exception as exc:
        print("readers: could not count comments:", exc)
        return {}
    return {r["slug"]: int(r["n"]) for r in rows}


def safe_next(target, fallback="/"):
    """Only ever redirect to a path on this site (no open redirects)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback


def reader_required_json(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_reader():
            return jsonify(error="sign_in_required"), 401
        if not _csrf_ok():
            return jsonify(error="bad_csrf"), 403
        return view(*args, **kwargs)
    return wrapped


def _set_saved(reader_id, slug, saved):
    """Idempotent save/unsave."""
    if saved:
        try:
            db.execute(
                "INSERT INTO saved_articles (reader_id, slug, created_at) VALUES (:r, :s, :t)",
                r=reader_id, s=slug, t=db.now_iso())
        except IntegrityError:
            pass  # already saved
    else:
        db.execute("DELETE FROM saved_articles WHERE reader_id = :r AND slug = :s",
                   r=reader_id, s=slug)


def _finish_auth(reader_id):
    """Sign the reader in, apply any pending action, and go back where they were."""
    session.pop("reader_id", None)
    session["reader_id"] = reader_id
    session.permanent = True  # stay signed in (see PERMANENT_SESSION_LIFETIME below)
    csrf_token()
    then = request.form.get("then", "")
    if then.startswith("save:"):
        slug = then[len("save:"):]
        if get_article(slug):
            _set_saved(reader_id, slug, True)
    return redirect(safe_next(request.form.get("next")))


def _auth_context():
    return {
        "categories": CATEGORIES,
        "active_category": None,
        "next": safe_next(request.values.get("next")),
        "then": request.values.get("then", ""),
    }


# ── Sign up / sign in / sign out ─────────────────────────────────────────

@readers_bp.get("/account/signup")
def signup():
    if current_reader():
        return redirect(safe_next(request.args.get("next"), "/account"))
    return render_template("account/signup.html", **_auth_context())


@readers_bp.post("/account/signup")
def signup_submit():
    ctx = _auth_context()
    if not _csrf_ok():
        return render_template("account/signup.html", error="Your session expired -- please try again.", **ctx), 400

    name = " ".join((request.form.get("display_name") or "").split())
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    form = {"display_name": name, "email": email}

    error = None
    if not (2 <= len(name) <= 40):
        error = "Pick a display name between 2 and 40 characters."
    elif not EMAIL_RE.match(email) or len(email) > 254:
        error = "That doesn't look like a valid email address."
    elif len(password) < MIN_PASSWORD_LENGTH:
        error = f"Use a password of at least {MIN_PASSWORD_LENGTH} characters."
    if error:
        return render_template("account/signup.html", error=error, form=form, **ctx), 400

    reader_id = db.new_id()
    try:
        db.execute(
            "INSERT INTO readers (id, email, display_name, password_hash, created_at) "
            "VALUES (:id, :email, :name, :pw, :t)",
            id=reader_id, email=email, name=name,
            pw=generate_password_hash(password), t=db.now_iso())
    except IntegrityError:
        return render_template(
            "account/signup.html", form=form, **ctx,
            error="There's already an account with that email. Try signing in instead."), 400
    return _finish_auth(reader_id)


@readers_bp.get("/account/login")
def login():
    if current_reader():
        return redirect(safe_next(request.args.get("next"), "/account"))
    return render_template("account/login.html", **_auth_context())


@readers_bp.post("/account/login")
def login_submit():
    ctx = _auth_context()
    if not _csrf_ok():
        return render_template("account/login.html", error="Your session expired -- please try again.", **ctx), 400

    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        return render_template("account/login.html", error="Too many attempts. Try again in a few minutes.", **ctx), 429

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    row = db.query_one(
        "SELECT id, password_hash FROM readers WHERE email = :e", e=email)
    if not row or not check_password_hash(row["password_hash"], password):
        attempts.append(now)
        _login_attempts[ip] = attempts
        return render_template(
            "account/login.html", form={"email": email}, **ctx,
            error="That email and password don't match."), 400

    _login_attempts.pop(ip, None)
    return _finish_auth(row["id"])


@readers_bp.post("/account/logout")
def logout():
    if _csrf_ok():
        session.pop("reader_id", None)
    return redirect("/")


@readers_bp.get("/account")
def account():
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account"))
    rows = db.query(
        "SELECT slug FROM saved_articles WHERE reader_id = :r ORDER BY created_at DESC",
        r=reader["id"])
    saved = [a for a in (get_article(r["slug"]) for r in rows) if a]
    places = []
    try:
        places = saved_places(reader["id"])
    except Exception as exc:
        print("readers: could not load saved places:", exc)
    return render_template(
        "account/saved.html", saved=saved, places=places, categories=CATEGORIES, active_category=None)


@readers_bp.get("/account/profile")
def profile():
    """The Account tab: who you're signed in as, a link to saved stories and
    sign out. Signed-out visitors are sent to sign in first."""
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account/profile"))
    saved_count = places_count = 0
    try:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM saved_articles WHERE reader_id = :r", r=reader["id"])
        saved_count = int((row or {}).get("n", 0))
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM saved_places WHERE reader_id = :r", r=reader["id"])
        places_count = int((row or {}).get("n", 0))
    except Exception as exc:
        print("readers: could not count saved stories/places:", exc)
    return render_template(
        "account/profile.html", saved_count=saved_count, places_count=places_count,
        categories=CATEGORIES, active_category=None)


# ── JSON API: save + comments ────────────────────────────────────────────

@readers_bp.post("/api/save/<slug>")
@reader_required_json
def api_save(slug):
    if not get_article(slug):
        return jsonify(error="not_found"), 404
    reader = current_reader()
    already = db.query_one(
        "SELECT 1 AS x FROM saved_articles WHERE reader_id = :r AND slug = :s",
        r=reader["id"], s=slug)
    _set_saved(reader["id"], slug, not already)
    return jsonify(saved=not already)


@readers_bp.get("/api/comments/<slug>")
def api_comments(slug):
    if not get_article(slug):
        return jsonify(error="not_found"), 404
    reader = current_reader()
    rows = db.query(
        "SELECT c.id, c.body, c.created_at, c.reader_id, r.display_name "
        "FROM comments c JOIN readers r ON r.id = c.reader_id "
        "WHERE c.slug = :s ORDER BY c.created_at ASC", s=slug)
    is_admin = bool(session.get("is_admin"))
    return jsonify(comments=[{
        "id": r["id"],
        "name": r["display_name"],
        "body": r["body"],
        "created_at": r["created_at"],
        "can_delete": is_admin or bool(reader and reader["id"] == r["reader_id"]),
    } for r in rows])


@readers_bp.post("/api/comments/<slug>")
@reader_required_json
def api_comment_create(slug):
    if not get_article(slug):
        return jsonify(error="not_found"), 404
    reader = current_reader()
    payload = request.get_json(silent=True) or {}
    body = (payload.get("body") or "").strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if not body:
        return jsonify(error="empty"), 400
    if len(body) > MAX_COMMENT_LENGTH:
        return jsonify(error="too_long", max=MAX_COMMENT_LENGTH), 400

    last = db.query_one(
        "SELECT created_at FROM comments WHERE reader_id = :r ORDER BY created_at DESC LIMIT 1",
        r=reader["id"])
    if last:
        from datetime import datetime, timezone
        last_dt = datetime.strptime(last["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_dt).total_seconds() < COMMENT_COOLDOWN_SECONDS:
            return jsonify(error="slow_down"), 429

    comment_id = db.new_id()
    created = db.now_iso()
    db.execute(
        "INSERT INTO comments (id, slug, reader_id, body, created_at) VALUES (:id, :s, :r, :b, :t)",
        id=comment_id, s=slug, r=reader["id"], b=body, t=created)
    return jsonify(comment={
        "id": comment_id, "name": reader["display_name"], "body": body,
        "created_at": created, "can_delete": True}), 201


@readers_bp.delete("/api/comments/<comment_id>")
def api_comment_delete(comment_id):
    reader = current_reader()
    is_admin = bool(session.get("is_admin"))
    if not (reader or is_admin):
        return jsonify(error="sign_in_required"), 401
    if not _csrf_ok():
        return jsonify(error="bad_csrf"), 403
    row = db.query_one("SELECT reader_id FROM comments WHERE id = :id", id=comment_id)
    if not row:
        return jsonify(error="not_found"), 404
    if not is_admin and row["reader_id"] != reader["id"]:
        return jsonify(error="forbidden"), 403
    db.execute("DELETE FROM comments WHERE id = :id", id=comment_id)
    return jsonify(ok=True)


# ── Places to try ────────────────────────────────────────────────────────
# Readers save a restaurant / place from a map pin inside a story. The map
# (an iframe from the ai-app) only *asks* its parent page to save; the page's
# script (static/js/places.js) checks who is asking and calls this API, so
# a save always needs a signed-in reader and the CSRF token.
#
# Everything in the request is untrusted, so each field is checked and
# trimmed before it is stored, and links must point at the map's own host.

def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _link(value, host=None, limit=500):
    """An http(s) link, optionally required to be on `host`; '' if not."""
    from urllib.parse import urlparse
    value = str(value or "").strip()
    if not value or len(value) > limit:
        return ""
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username:
        return ""
    if host and parsed.netloc.lower() != host:
        return ""
    return value


def clean_place(raw):
    """Validate the place a map sent us. Returns a dict ready to store, or
    None if it isn't a place from one of our allowed map hosts."""
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or "").strip()
    host, _, ident = key.rpartition(":")
    host, ident = host.lower(), ident.upper()
    # A pin is "<host>:<number>"; a whole-business card is "<host>:L<number>".
    number = ident[1:] if ident.startswith("L") else ident
    if not (number.isdigit() and 1 <= len(number) <= 12) or host not in map_embed_hosts():
        return None
    key = f"{host}:{ident}"
    name = _text(raw.get("name"), 120)
    try:
        lat, lng = float(raw.get("lat")), float(raw.get("lng"))
    except (TypeError, ValueError):
        return None
    if not name or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {
        "key": key, "name": name,
        "category": _text(raw.get("category"), 40).replace("_", " "),
        "address": _text(raw.get("address"), 200),
        "lat": lat, "lng": lng,
        "website": _link(raw.get("website")),
        "photo": _link(raw.get("photo"), host),
        "listing": _link(raw.get("listing"), host),
    }


def saved_place_keys(reader_id):
    rows = db.query("SELECT place_key FROM saved_places WHERE reader_id = :r", r=reader_id)
    return [r["place_key"] for r in rows]


def saved_places(reader_id):
    """The reader's places for the account page, newest first. Each carries
    a ready-made directions link and, when the story still exists, its title."""
    rows = db.query(
        "SELECT id, place_key, name, category, address, lat, lng, website, photo_url, "
        "listing_url, article_slug, created_at FROM saved_places "
        "WHERE reader_id = :r ORDER BY created_at DESC, id", r=reader_id)
    out = []
    for r in rows:
        article = get_article(r["article_slug"]) if r["article_slug"] else None
        out.append({
            **r,
            "directions": f"https://www.google.com/maps/dir/?api=1&destination={r['lat']},{r['lng']}",
            "article": {"slug": article["slug"], "title": article["title"]} if article else None,
        })
    return out


@readers_bp.get("/api/places")
def api_places():
    """The place keys this reader has saved (the map uses them to show
    which pins are already saved). Signed-out readers just get none."""
    reader = current_reader()
    if not reader:
        return jsonify(signedIn=False, keys=[])
    return jsonify(signedIn=True, keys=saved_place_keys(reader["id"]))


@readers_bp.post("/api/places")
@reader_required_json
def api_places_save():
    """Save or unsave a place. Body: {place: {...}, article: "<slug>", saved: true|false}.
    With no `saved` it toggles."""
    reader = current_reader()
    payload = request.get_json(silent=True) or {}
    place = clean_place(payload.get("place"))
    if not place:
        return jsonify(error="bad_place"), 400
    slug = str(payload.get("article") or "").strip()[:200]
    if slug and not get_article(slug):
        slug = ""

    exists = db.query_one(
        "SELECT 1 AS x FROM saved_places WHERE reader_id = :r AND place_key = :k",
        r=reader["id"], k=place["key"])
    want = payload.get("saved")
    want = (not exists) if not isinstance(want, bool) else want

    if want and not exists:
        count = db.query_one("SELECT COUNT(*) AS n FROM saved_places WHERE reader_id = :r", r=reader["id"])
        if int((count or {}).get("n", 0)) >= MAX_SAVED_PLACES:
            return jsonify(error="too_many"), 400
        try:
            db.execute(
                "INSERT INTO saved_places (id, reader_id, place_key, name, category, address, lat, lng, "
                "website, photo_url, listing_url, article_slug, created_at) "
                "VALUES (:id, :r, :k, :name, :cat, :addr, :lat, :lng, :web, :photo, :listing, :slug, :t)",
                id=db.new_id(), r=reader["id"], k=place["key"], name=place["name"], cat=place["category"],
                addr=place["address"], lat=place["lat"], lng=place["lng"], web=place["website"],
                photo=place["photo"], listing=place["listing"], slug=slug, t=db.now_iso())
        except IntegrityError:
            pass  # saved twice at once
    elif not want and exists:
        db.execute("DELETE FROM saved_places WHERE reader_id = :r AND place_key = :k",
                   r=reader["id"], k=place["key"])
    return jsonify(saved=want, keys=saved_place_keys(reader["id"]))


@readers_bp.delete("/api/places/<place_id>")
@reader_required_json
def api_places_delete(place_id):
    reader = current_reader()
    db.execute("DELETE FROM saved_places WHERE id = :id AND reader_id = :r", id=place_id, r=reader["id"])
    return jsonify(ok=True)


# ── Template context ─────────────────────────────────────────────────────

def register(app):
    """Wire the blueprint and give every template `reader`, `csrf_token`
    and `reader_state` (used by base.html's JS to know who's signed in)."""
    app.register_blueprint(readers_bp)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SECURE", bool(os.environ.get("RENDER")))
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(days=90))

    def header_user(reader):
        """Who the avatar in the site header shows: a reader (their author
        photo if their email matches an author account, otherwise their
        initial), an author who is signed in to the writers' area, or nobody
        (the default silhouette with sign in / create account)."""
        try:
            if reader:
                author = get_author_by_email(reader.get("email"))
                name = reader["display_name"]
                return {"kind": "reader", "name": name, "email": reader.get("email"),
                        "photo": (author or {}).get("photo"),
                        "initial": (name.strip()[:1] or "?").upper(),
                        "is_author": bool(session.get("author_slug"))}
            slug = session.get("author_slug")
            author = get_author(slug) if slug else None
            if author:
                name = author.get("name") or "Author"
                return {"kind": "author", "name": name, "email": author.get("email"),
                        "photo": author.get("photo"),
                        "initial": (name.strip()[:1] or "?").upper(), "is_author": True}
        except Exception as exc:  # never let the avatar break a page
            print("readers: could not build header user:", exc)
        return None

    @app.context_processor
    def inject_reader():
        reader = current_reader()
        saved = sorted(saved_slugs()) if reader else []
        return {
            "reader": reader,
            "header_user": header_user(reader),
            "csrf_token": csrf_token(),
            "saved_slugs": set(saved),
            "reader_state": {
                "signedIn": bool(reader),
                "name": reader["display_name"] if reader else None,
                "saved": saved,
                "csrf": csrf_token(),
            },
        }
