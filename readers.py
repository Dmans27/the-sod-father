"""
Reader accounts: anyone can sign up (email + password) to save stories and
comment on them. Separate from the /admin portal and from the author
accounts in author_auth.py -- those are for people who *write* here; these
are for people who *read*.

  /account/signup, /account/login, POST /account/logout, /account (saved list)
  GET/POST /api/comments/<slug>, DELETE /api/comments/<comment_id>
  POST /api/save/<slug>   (toggles saved on/off)
  (reporting, blocking and deleting an account are in safety.py)
  GET/POST /api/places, DELETE /api/places/<id>   (places to try, saved from
      the map pins inside stories -- see static/js/places.js)
  GET/POST /api/routes, DELETE /api/routes/<id>   (walking routes from "route"
      travel stories -- see static/js/map-story.js)

Signed-out visitors can still read everything and use Share. Save and
Comment send them to sign in first, then bring them back to the story --
and a pending "save" is applied automatically once they're signed in.

Storage is db.py (Postgres on Render, SQLite locally).
"""
import json
import math
from urllib.parse import quote, urlencode
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
import moderation
from authors import get_author, get_author_by_email
from content import CATEGORIES, estimate_read_minutes, get_article, map_embed_hosts

readers_bp = Blueprint("readers", __name__)

MAX_SAVED_PLACES = 500   # per reader -- a sanity cap, not a product limit
MAX_SAVED_ROUTES = 200
MAX_ROUTE_STOPS = 40
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
        rows = db.query(
            "SELECT c.slug, COUNT(*) AS n FROM comments c WHERE " + moderation.VISIBLE_SQL +
            " GROUP BY c.slug", **moderation.HIDE_PARAMS)
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


def _verified(reader):
    if not reader:
        return False
    try:
        from onboarding import is_verified, required
        return is_verified(reader) or not required()
    except Exception:  # noqa: BLE001
        return False


def _finish_auth(reader_id, welcome=False):
    """Sign the reader in, apply any pending action, and go back where they were
    (new accounts first go through the welcome screens: email code + a few questions)."""
    session.pop("reader_id", None)
    try:   # a guest account (guests.py) ends once they have a real one
        import guests
        guests.end()
    except Exception as exc:  # noqa: BLE001
        print("guests: end failed:", exc)
    session["reader_id"] = reader_id
    session.permanent = True  # stay signed in (see PERMANENT_SESSION_LIFETIME below)
    csrf_token()
    then = request.form.get("then", "")
    if then.startswith("save:"):
        slug = then[len("save:"):]
        if get_article(slug):
            _set_saved(reader_id, slug, True)
    if welcome:
        return redirect("/welcome?next=" + quote(safe_next(request.form.get("next")), safe=""))
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
    if not error and moderation.is_banned(email):
        error = "This email address can't be used to create an account."
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
    return _finish_auth(reader_id, welcome=True)


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
        if request.form.get("push_token"):   # the app sends its token so this phone stops getting pings
            import push
            push.forget(request.form.get("push_token"))
    nxt = request.form.get("next") or "/"   # e.g. the walk map's account menu sends /walk
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")


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
    routes = []
    try:
        routes = saved_routes(reader["id"])
    except Exception as exc:
        print("readers: could not load saved routes:", exc)
    return render_template(
        "account/saved.html", saved=saved, places=places, routes=routes,
        categories=CATEGORIES, active_category=None)


@readers_bp.get("/account/profile")
def profile():
    """The Account tab: who you're signed in as, a link to saved stories and
    sign out. Signed-out visitors are sent to sign in first."""
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account/profile"))
    saved_count = places_count = routes_count = 0
    try:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM saved_articles WHERE reader_id = :r", r=reader["id"])
        saved_count = int((row or {}).get("n", 0))
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM saved_places WHERE reader_id = :r", r=reader["id"])
        places_count = int((row or {}).get("n", 0))
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM saved_routes WHERE reader_id = :r", r=reader["id"])
        routes_count = int((row or {}).get("n", 0))
    except Exception as exc:
        print("readers: could not count saved stories/places:", exc)
    from newsletter import is_subscribed   # the Newsletter switch (newsletter.py)
    return render_template(
        "account/profile.html", saved_count=saved_count, places_count=places_count, routes_count=routes_count,
        newsletter_on=is_subscribed(reader.get("email")), categories=CATEGORIES, active_category=None)


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


# Plain-text copy of a story for offline reading in the iOS app (see
# static/js/native.js and capacitor-app/www/offline.html). Only text-based
# blocks are included -- photos, maps, videos and embeds need the internet
# anyway. The app renders every value with textContent, never as HTML.
_OFFLINE_TEXT_BLOCKS = {"paragraph", "subheading", "quote", "list", "tip", "takeaways", "table", "divider"}


@readers_bp.get("/api/offline/<slug>")
def api_offline_story(slug):
    article = get_article(slug)
    if not article:
        return jsonify(error="not_found"), 404
    blocks = []
    for b in article.get("blocks") or []:
        kind = b.get("type")
        if kind not in _OFFLINE_TEXT_BLOCKS:
            continue
        blocks.append({k: b.get(k) for k in
                       ("type", "text", "attribution", "style", "variant", "title", "items", "header", "rows")
                       if b.get(k) is not None})
    resp = jsonify(
        slug=article["slug"], title=article.get("title", ""), dek=article.get("dek", ""),
        author=article.get("author", ""), published_at=article.get("published_at", ""),
        pill=article.get("pill", ""), read_minutes=estimate_read_minutes(article.get("blocks") or []),
        blocks=blocks)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@readers_bp.get("/api/comments/<slug>")
def api_comments(slug):
    if not get_article(slug):
        return jsonify(error="not_found"), 404
    reader = current_reader()
    # Hidden (3+ reports, not yet reviewed) comments are left out for everyone,
    # and a signed-in reader doesn't see comments from readers they blocked.
    sql = ("SELECT c.id, c.body, c.created_at, c.reader_id, r.display_name "
           "FROM comments c JOIN readers r ON r.id = c.reader_id "
           "WHERE c.slug = :s AND " + moderation.VISIBLE_SQL)
    params = {"s": slug, **moderation.HIDE_PARAMS}
    if reader:
        sql += " AND c.reader_id NOT IN (SELECT blocked_id FROM reader_blocks WHERE reader_id = :me)"
        params["me"] = reader["id"]
    rows = db.query(sql + " ORDER BY c.created_at ASC", **params)
    is_admin = bool(session.get("is_admin"))
    return jsonify(comments=[{
        "id": r["id"],
        "name": r["display_name"],
        "body": r["body"],
        "created_at": r["created_at"],
        "can_delete": is_admin or bool(reader and reader["id"] == r["reader_id"]),
        "can_report": not (reader and reader["id"] == r["reader_id"]),
    } for r in rows])


@readers_bp.post("/api/comments/<slug>")
@reader_required_json
def api_comment_create(slug):
    if not get_article(slug):
        return jsonify(error="not_found"), 404
    from onboarding import verify_block   # comments need a verified email (onboarding.py)
    blocked = verify_block(current_reader(), "comment")
    if blocked:
        return blocked
    reader = current_reader()
    payload = request.get_json(silent=True) or {}
    body = (payload.get("body") or "").strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if not body:
        return jsonify(error="empty"), 400
    if len(body) > MAX_COMMENT_LENGTH:
        return jsonify(error="too_long", max=MAX_COMMENT_LENGTH), 400
    problem = moderation.check_comment_text(body)
    if problem:
        return jsonify(error=problem), 400

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
        "created_at": created, "can_delete": True, "can_report": False}), 201


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
    moderation.delete_comment(comment_id)
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


# ── "My places": the reader's saved places in one city, ranked ──────────
# The walk map's right-hand drawer. Places count as "in this city" when they're
# within MY_PLACES_RADIUS_M of the map's middle. A ranking starts once there are
# RANK_FROM places: the reader orders them with the arrows (kept in place_ranks);
# new saves join at the bottom. Before that the list is just "saved so far".

MY_PLACES_RADIUS_M = 30000
RANK_FROM = 3
_ranks_ready = False


def _ranks_table():
    global _ranks_ready
    if _ranks_ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS place_ranks (
            reader_id  TEXT NOT NULL,
            place_key  TEXT NOT NULL,
            pos        INTEGER NOT NULL,
            PRIMARY KEY (reader_id, place_key)
        )""")
    _ranks_ready = True


def _dist_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def _city_near(lat, lng):
    """The feed city's name when the map is over it, else None."""
    city = (os.environ.get("FEED_CITY") or "Vienna").strip()[:60] or "Vienna"
    try:
        clat, clng = [float(x) for x in (os.environ.get("FEED_CITY_CENTER") or "48.2082,16.3738").split(",")]
    except ValueError:
        clat, clng = 48.2082, 16.3738
    return city if _dist_m(lat, lng, clat, clng) <= MY_PLACES_RADIUS_M else None


def _listing_slug(url):
    m = re.search(r"/listing/([A-Za-z0-9][A-Za-z0-9_-]{0,200})/?$", url or "")
    return m.group(1) if m else ""


def my_places(reader_id, lat, lng):
    """The reader's saved places near (lat, lng): ranked order first, then newest saves."""
    _ranks_table()
    rows = db.query(
        "SELECT p.place_key, p.name, p.category, p.address, p.lat, p.lng, p.photo_url, p.listing_url, p.created_at, "
        "r.pos FROM saved_places p LEFT JOIN place_ranks r ON r.reader_id = p.reader_id AND r.place_key = p.place_key "
        "WHERE p.reader_id = :r", r=reader_id)
    near = [r for r in rows if _dist_m(lat, lng, r["lat"], r["lng"]) <= MY_PLACES_RADIUS_M]
    near.sort(key=lambda r: (r["pos"] is None, r["pos"] if r["pos"] is not None else 0, r["created_at"]))
    return [{
        "key": r["place_key"], "name": r["name"], "category": r["category"], "address": r["address"],
        "photo": r["photo_url"], "slug": _listing_slug(r["listing_url"]),
        "directions": f"https://www.google.com/maps/dir/?api=1&destination={r['lat']},{r['lng']}&travelmode=walking",
    } for r in near]


def _map_point():
    try:
        lat, lng = float(request.args.get("lat")), float(request.args.get("lng"))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng
    except (TypeError, ValueError):
        pass
    return 48.2082, 16.3738


@readers_bp.get("/api/my-places")
def api_my_places():
    """{signedIn, city, ranked, rankFrom, places: [...]} for the map's middle (?lat&lng)."""
    reader = current_reader()
    lat, lng = _map_point()
    city = _city_near(lat, lng)
    if not reader:
        return jsonify(signedIn=False, city=city, ranked=False, rankFrom=RANK_FROM, places=[])
    try:
        places = my_places(reader["id"], lat, lng)
    except Exception as exc:
        print("readers: could not load my places:", exc)
        return jsonify(error="unavailable"), 503
    return jsonify(signedIn=True, city=city, ranked=len(places) >= RANK_FROM, rankFrom=RANK_FROM,
                   name=reader["display_name"], places=places)


@readers_bp.post("/api/my-places/order")
@reader_required_json
def api_my_places_order():
    """Save the reader's ranking: {keys: [place_key, ...]} best first."""
    reader = current_reader()
    keys = (request.get_json(silent=True) or {}).get("keys")
    if not isinstance(keys, list) or not keys or len(keys) > MAX_SAVED_PLACES:
        return jsonify(error="bad_order"), 400
    mine = set(saved_place_keys(reader["id"]))
    keys = [k for k in dict.fromkeys(str(k) for k in keys) if k in mine]
    _ranks_table()
    with db.get_engine().begin() as conn:
        from sqlalchemy import text as _sql
        for i, k in enumerate(keys):
            conn.execute(_sql("DELETE FROM place_ranks WHERE reader_id = :r AND place_key = :k"), {"r": reader["id"], "k": k})
            conn.execute(_sql("INSERT INTO place_ranks (reader_id, place_key, pos) VALUES (:r, :k, :p)"),
                         {"r": reader["id"], "k": k, "p": i})
    return jsonify(ok=True)


# ── "My routes": routes a reader builds from their saved places ──────────
# ("Date night", "Show my friends around"). Each is a title and 2-15 directory
# listings in the reader's order; the walk map opens one like any walk, and it
# can be shared with the same short links as a walk.

MAX_MY_ROUTES = 50
MAX_ROUTE_STOPS = 15
_ROUTE_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_my_routes_ready = False


def _my_routes_table():
    global _my_routes_ready
    if _my_routes_ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS my_routes (
            id          TEXT PRIMARY KEY,
            reader_id   TEXT NOT NULL,
            title       TEXT NOT NULL,
            stops_json  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""")
    _my_routes_ready = True


def _route_row(r):
    return {"id": r["id"], "title": r["title"], "stops": json.loads(r["stops_json"]), "updated_at": r["updated_at"]}


@readers_bp.get("/api/my-routes")
def api_my_routes():
    reader = current_reader()
    if not reader:
        return jsonify(signedIn=False, routes=[])
    _my_routes_table()
    rows = db.query("SELECT id, title, stops_json, updated_at FROM my_routes WHERE reader_id = :r "
                    "ORDER BY updated_at DESC", r=reader["id"])
    return jsonify(signedIn=True, routes=[_route_row(r) for r in rows])


@readers_bp.post("/api/my-routes")
@reader_required_json
def api_my_routes_save():
    """Create or update a route: {id?, title, stops: [{slug, name}, ...]} in walking order."""
    reader = current_reader()
    data = request.get_json(silent=True) or {}
    title = _text(data.get("title"), 60)
    if not title:
        return jsonify(error="Give your route a name."), 400
    stops, seen = [], set()
    for st in (data.get("stops") or [])[:MAX_ROUTE_STOPS]:
        if not isinstance(st, dict):
            continue
        slug = str(st.get("slug") or "")
        if _ROUTE_SLUG_RE.match(slug) and slug not in seen:
            seen.add(slug)
            stops.append({"slug": slug, "name": _text(st.get("name"), 80)})
    if len(stops) < 2:
        return jsonify(error="Pick at least 2 places for a route."), 400
    _my_routes_table()
    now = db.now_iso()
    route_id = str(data.get("id") or "")
    if route_id:
        done = db.execute("UPDATE my_routes SET title = :t, stops_json = :s, updated_at = :u WHERE id = :id AND reader_id = :r",
                          t=title, s=json.dumps(stops), u=now, id=route_id, r=reader["id"])
        if not done:
            return jsonify(error="That route no longer exists."), 404
    else:
        count = db.query_one("SELECT COUNT(*) AS n FROM my_routes WHERE reader_id = :r", r=reader["id"])
        if int((count or {}).get("n", 0)) >= MAX_MY_ROUTES:
            return jsonify(error=f"You can keep up to {MAX_MY_ROUTES} routes. Delete one first."), 400
        route_id = db.new_id()
        db.execute("INSERT INTO my_routes (id, reader_id, title, stops_json, created_at, updated_at) "
                   "VALUES (:id, :r, :t, :s, :c, :u)", id=route_id, r=reader["id"], t=title, s=json.dumps(stops), c=now, u=now)
    return jsonify(ok=True, route={"id": route_id, "title": title, "stops": stops, "updated_at": now})


@readers_bp.delete("/api/my-routes/<route_id>")
@reader_required_json
def api_my_routes_delete(route_id):
    reader = current_reader()
    _my_routes_table()
    db.execute("DELETE FROM my_routes WHERE id = :id AND reader_id = :r", id=route_id, r=reader["id"])
    return jsonify(ok=True)


@readers_bp.delete("/api/places/<place_id>")
@reader_required_json
def api_places_delete(place_id):
    reader = current_reader()
    db.execute("DELETE FROM saved_places WHERE id = :id AND reader_id = :r", id=place_id, r=reader["id"])
    return jsonify(ok=True)


# ── Saved walking routes ─────────────────────────────────────────────────
# A "route" travel story's places, in order, saved to the reader's profile.
# The page sends the stops (the map worked them out); every field is checked
# here, and the story must exist and be a route story.

_ROUTE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_WALK_KEY = re.compile(r"^walk:[0-9a-f]{16}$")   # a walk planned with AI on /walk (walk_planner.py)
_HEX = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


def _metres(a, b):
    r, to_r = 6371000, math.pi / 180
    d_lat, d_lng = (b["lat"] - a["lat"]) * to_r, (b["lng"] - a["lng"]) * to_r
    h = math.sin(d_lat / 2) ** 2 + math.cos(a["lat"] * to_r) * math.cos(b["lat"] * to_r) * math.sin(d_lng / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(h)))


def clean_route_stops(raw):
    """The stops a story page sent, checked and trimmed; None if unusable."""
    if not isinstance(raw, list) or not (2 <= len(raw) <= MAX_ROUTE_STOPS):
        return None
    stops = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        name = _text(item.get("name"), 120)
        try:
            lat, lng = float(item.get("lat")), float(item.get("lng"))
        except (TypeError, ValueError):
            return None
        if not name or not (-90 <= lat <= 90 and -180 <= lng <= 180) or math.isnan(lat) or math.isnan(lng):
            return None
        listing = str(item.get("listing") or "").strip()
        color = str(item.get("color") or "").strip()
        stops.append({
            "name": name, "lat": lat, "lng": lng,
            "listing": listing if _ROUTE_SLUG.match(listing) else "",
            "color": color if _HEX.match(color) else "#2f9e7e",
            "category": _text(item.get("category"), 40).replace("_", " "),
        })
    return stops


def route_maps_links(stops):
    """Google Maps directions through the stops. Phones take 3 stops between
    the start and the end, so a longer route is split into parts of 5."""
    total = sum(_metres(stops[i - 1], stops[i]) for i in range(1, len(stops)))
    walk = total <= 10000
    links = []
    for start in range(0, len(stops) - 1, 4):
        part = stops[start:start + 5]
        ll = lambda s: f"{s['lat']},{s['lng']}"
        url = (f"https://www.google.com/maps/dir/?api=1&origin={ll(part[0])}&destination={ll(part[-1])}")
        if len(part) > 2:
            url += "&waypoints=" + "%7C".join(ll(s) for s in part[1:-1])
        if walk:
            url += "&travelmode=walking"
        label = (f"Stops {start + 1}\u2013{start + len(part)}" if len(stops) > 5 else "Google Maps")
        links.append({"href": url, "label": label})
    return links, total


def saved_routes(reader_id):
    rows = db.query(
        "SELECT id, article_slug, title, stops_json, created_at FROM saved_routes "
        "WHERE reader_id = :r ORDER BY created_at DESC, id", r=reader_id)
    out = []
    for r in rows:
        try:
            stops = clean_route_stops(json.loads(r["stops_json"])) or []
        except (TypeError, ValueError):
            stops = []
        if len(stops) < 2:
            continue
        links, total = route_maps_links(stops)
        km = total / 1000
        planned = bool(_WALK_KEY.match(r["article_slug"] or ""))
        article = None if planned else get_article(r["article_slug"])
        if planned:
            slugs = ",".join(s["listing"] for s in stops if s["listing"])
            open_url = "/walk?" + urlencode({"s": slugs, "t": r["title"]})
        else:
            open_url = f"/article/{r['article_slug']}?map=route"
        out.append({
            "id": r["id"], "slug": r["article_slug"],
            "title": (article or {}).get("title") or r["title"],
            "exists": bool(article) or planned,
            "planned": planned,
            "open_url": open_url,
            "link": open_url if planned else f"/article/{r['article_slug']}",
            "stops": stops,
            "distance": (f"{round(km)} km" if km >= 10 else f"{km:.1f} km"),
            "walking": total <= 10000,
            "maps": links,
        })
    return out


def _route_story(slug):
    article = get_article(slug) if slug and _ROUTE_SLUG.match(slug) else None
    return article if article and article.get("map_route") else None


@readers_bp.get("/api/routes")
def api_routes():
    """Whether this reader has saved the route in story ?article=<slug>."""
    reader = current_reader()
    if not reader:
        return jsonify(signedIn=False, saved=False)
    slug = str(request.args.get("article") or "").strip()[:200]
    row = db.query_one(
        "SELECT 1 AS x FROM saved_routes WHERE reader_id = :r AND article_slug = :s",
        r=reader["id"], s=slug) if slug else None
    return jsonify(signedIn=True, saved=bool(row))


@readers_bp.post("/api/routes")
@reader_required_json
def api_routes_save():
    """Save or unsave a story's walking route.
    Body: {article: "<slug>", stops: [...], saved: true|false}. With no `saved` it toggles."""
    reader = current_reader()
    payload = request.get_json(silent=True) or {}
    slug = str(payload.get("article") or "").strip()[:200]
    if _WALK_KEY.match(slug):   # an AI-planned walk: no story behind it, its stops must be directory listings
        article = {"title": _text(payload.get("title"), 120) or "Walk"}
        if not all(isinstance(s, dict) and _ROUTE_SLUG.match(str(s.get("listing") or "")) for s in (payload.get("stops") or [])):
            return jsonify(error="bad_route"), 400
    else:
        article = _route_story(slug)
    if not article:
        return jsonify(error="not_found"), 404
    exists = db.query_one(
        "SELECT 1 AS x FROM saved_routes WHERE reader_id = :r AND article_slug = :s",
        r=reader["id"], s=slug)
    want = payload.get("saved")
    want = (not exists) if not isinstance(want, bool) else want
    if want:
        stops = clean_route_stops(payload.get("stops"))
        if not stops:
            return jsonify(error="bad_route"), 400
        if not exists:
            count = db.query_one("SELECT COUNT(*) AS n FROM saved_routes WHERE reader_id = :r", r=reader["id"])
            if int((count or {}).get("n", 0)) >= MAX_SAVED_ROUTES:
                return jsonify(error="too_many"), 400
            try:
                db.execute(
                    "INSERT INTO saved_routes (id, reader_id, article_slug, title, stops_json, created_at) "
                    "VALUES (:id, :r, :s, :title, :stops, :t)",
                    id=db.new_id(), r=reader["id"], s=slug, title=_text(article.get("title"), 200),
                    stops=json.dumps(stops), t=db.now_iso())
            except IntegrityError:
                pass  # saved twice at once
        else:   # saved before: keep it, with the stops as they are now
            db.execute(
                "UPDATE saved_routes SET stops_json = :stops, title = :title WHERE reader_id = :r AND article_slug = :s",
                stops=json.dumps(stops), title=_text(article.get("title"), 200), r=reader["id"], s=slug)
    elif exists:
        db.execute("DELETE FROM saved_routes WHERE reader_id = :r AND article_slug = :s", r=reader["id"], s=slug)
    return jsonify(saved=want)


@readers_bp.delete("/api/routes/<route_id>")
@reader_required_json
def api_routes_delete(route_id):
    reader = current_reader()
    db.execute("DELETE FROM saved_routes WHERE id = :id AND reader_id = :r", id=route_id, r=reader["id"])
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
                "verified": _verified(reader),
                "saved": saved,
                "csrf": csrf_token(),
            },
        }
