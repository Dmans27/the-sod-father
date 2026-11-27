"""
The community walk feed: readers post walks they built (My routes) or walked to
the end, for other walkers in the same city. A post has a title, a short note,
an optional photo, and likes.

  POST   /api/community/walks                 post a walk (multipart form: title, note, source, stops, photo?)
  DELETE /api/community/walks/<id>            take your own post down
  POST   /api/community/walks/<id>/like       like / unlike  -> {liked, likes}
  POST   /api/community/walks/<id>/report     report it (JSON {reason})
  POST   /api/community/walks/<id>/block      stop seeing this person's walks (and comments)
  GET    /community/photo/<id>.jpg            a post's photo
  GET    /admin/community                     the owner's review screen (+ keep / remove / ban)

Posts go live straight away. Anyone signed in can report one: the reporter stops
seeing it at once, and at HIDE_AFTER_REPORTS reports from different people it's
hidden from everyone until the owner presses Keep or Remove in /admin/community.
Blocks are the same reader_blocks rows the comments use (safety.py), so blocking
someone hides both their comments and their walks. Titles and notes go through
the comment word filter (moderation.py).

Where a walk is: the directory checks every stop (/embed/places-by-slug, walk
key only), so a post can only contain real, published places. Its city is the
feed city (FEED_CITY) when the stops are within 30 km of FEED_CITY_CENTER, else
the stops' own city -- each city's feed only shows its own walks.

Photos are resized on the reader's phone, then again here (max PHOTO_WIDTH px,
JPEG), and kept in the database (community_photos), not in the GitHub repo: a
photo must never redeploy the site.

Tables are created on first use (CREATE TABLE IF NOT EXISTS) -- nothing to set up.
"""
import hashlib
import io
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

import db
import moderation
import walk_planner as wp
from admin import owner_required
from readers import _city_near, _csrf_ok, current_reader, reader_required_json

community_bp = Blueprint("community", __name__)

MAX_TITLE = 60
MAX_NOTE = 280
MAX_STOPS = 15
POSTS_PER_DAY = 5
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
PHOTO_WIDTH = 1280
PHOTO_QUALITY = 80
HIDE_AFTER_REPORTS = 2
FEED_DAYS = 120
FEED_LIMIT = 12
SOURCES = {"route", "walk"}
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")

VIENNA = ["", "Innere Stadt", "Leopoldstadt", "Landstraße", "Wieden", "Margareten", "Mariahilf", "Neubau",
          "Josefstadt", "Alsergrund", "Favoriten", "Simmering", "Meidling", "Hietzing", "Penzing", "Rudolfsheim-Fünfhaus",
          "Ottakring", "Hernals", "Währing", "Döbling", "Brigittenau", "Floridsdorf", "Donaustadt", "Liesing"]

_ready = False


def _tables():
    global _ready
    if _ready:
        return
    blob = "BYTEA" if db.get_engine().dialect.name == "postgresql" else "BLOB"
    db.execute("""
        CREATE TABLE IF NOT EXISTS community_walks (
            id          TEXT PRIMARY KEY,
            reader_id   TEXT NOT NULL,
            title       TEXT NOT NULL,
            note        TEXT,
            stops_json  TEXT NOT NULL,
            walk_key    TEXT NOT NULL,
            city        TEXT,
            district    TEXT,
            lat         REAL,
            lng         REAL,
            source      TEXT,
            has_photo   INTEGER NOT NULL DEFAULT 0,
            status      TEXT NOT NULL DEFAULT 'live',
            cleared     INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_community_walks_city ON community_walks (city, status, created_at)")
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS community_photos (
            walk_id     TEXT PRIMARY KEY,
            data        {blob} NOT NULL,
            created_at  TEXT NOT NULL
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS community_likes (
            walk_id     TEXT NOT NULL,
            reader_id   TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (walk_id, reader_id)
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS community_reports (
            walk_id     TEXT NOT NULL,
            reader_id   TEXT NOT NULL,
            reason      TEXT,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (walk_id, reader_id)
        )""")
    _ready = True


# ── Small helpers ────────────────────────────────────────────────────────

def feed_city():
    return (os.environ.get("FEED_CITY") or "Vienna").strip()[:60] or "Vienna"


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _note(value):
    """Keep line breaks (at most one blank line), squash other whitespace."""
    lines = [" ".join(line.split()) for line in str(value or "").replace("\r", "").split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out[:MAX_NOTE]


def _walk_key(slugs):
    return "walk:" + hashlib.sha1(",".join(slugs).encode()).hexdigest()[:16]


def _districts(addresses):
    seen = []
    for a in addresses:
        m = re.search(r"\b1(0[1-9]|1\d|2[0-3])0\b", str(a or ""))
        n = int(m.group(1)) if m else 0
        if n and n not in seen:
            seen.append(n)
    if not seen:
        return ""

    def ordinal(n):
        return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    if len(seen) == 1:
        return f"{VIENNA[seen[0]]} · {ordinal(seen[0])} district"
    parts = [f"{VIENNA[n]} ({ordinal(n)})" for n in seen[:3]]
    return ", ".join(parts[:-1]) + " & " + parts[-1]


def _ago(iso):
    try:
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return ""
    secs = max(0, (datetime.now(timezone.utc) - then).total_seconds())
    if secs < 3600:
        return "just now" if secs < 120 else f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    days = int(secs // 86400)
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} wk ago"
    return then.strftime("%b %Y")


def _photo_bytes(file_storage):
    """Resized JPEG bytes, None when no file was chosen; ValueError for a file that isn't a photo."""
    if not file_storage or not file_storage.filename:
        return None
    from PIL import Image, ImageOps
    raw = file_storage.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("too_big")
    try:
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except Exception as exc:  # noqa: BLE001 -- any decoding problem means "not a photo we can use"
        raise ValueError("not_a_photo") from exc
    if image.width > PHOTO_WIDTH:
        image = image.resize((PHOTO_WIDTH, round(image.height * PHOTO_WIDTH / image.width)), Image.LANCZOS)
    if image.height > PHOTO_WIDTH * 1.5:   # very tall: keep the middle
        top = (image.height - int(PHOTO_WIDTH * 1.5)) // 2
        image = image.crop((0, top, image.width, top + int(PHOTO_WIDTH * 1.5)))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=PHOTO_QUALITY, optimize=True)
    return buf.getvalue()


def _directory_places(slugs):
    """{slug: place} for the slugs the directory knows. Raises wp.WalkError when it can't be reached."""
    base, key = wp.directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    if not base or not key:
        raise wp.WalkError("The directory isn't connected, so walks can't be posted right now.")
    try:
        r = requests.get(base + "/embed/places-by-slug?" + urlencode({"s": ",".join(slugs)}),
                         headers={"X-Walk-Key": key}, timeout=12)
        data = r.json() if r.status_code < 300 else None
    except (requests.RequestException, ValueError):
        data = None
    if not isinstance(data, dict):
        raise wp.WalkError("Couldn't check the places right now. Try again in a minute.")
    return {p["slug"]: p for p in data.get("places") or [] if isinstance(p, dict) and p.get("slug")}


def _hidden(row):
    return int(row.get("reports") or 0) >= HIDE_AFTER_REPORTS and not int(row.get("cleared") or 0)


def _card(row, viewer_id=None, liked=False, walked=0):
    stops = json.loads(row["stops_json"])
    slugs = ",".join(s["slug"] for s in stops)
    base = wp.directory_url()
    name = row.get("display_name") or "A walker"
    return {
        "id": row["id"],
        "title": row["title"],
        "note": row.get("note") or "",
        "blurb": row.get("note") or "",
        "by": name,
        "tag": "Shared by " + name,
        "ago": _ago(row.get("created_at")),
        "district": row.get("district") or "",
        "photo": f"/community/photo/{row['id']}.jpg" if int(row.get("has_photo") or 0) else "",
        "likes": int(row.get("likes") or 0),
        "liked": bool(liked),
        "mine": bool(viewer_id and viewer_id == row["reader_id"]),
        "walked": walked,
        "stops": stops,
        "link": "/walk?" + urlencode({"s": slugs, "t": row["title"]}),
        "map": (base + "/embed/route-map?" + urlencode(
            {"s": slugs, "t": row["title"], "style": "mansfield", "mode": "story", "safe": 0})) if base else "",
        "source": row.get("source") or "",
    }


_SELECT = (
    "SELECT w.id, w.reader_id, w.title, w.note, w.stops_json, w.walk_key, w.city, w.district, w.has_photo, "
    "w.cleared, w.source, w.created_at, r.display_name, "
    "(SELECT COUNT(*) FROM community_likes l WHERE l.walk_id = w.id) AS likes, "
    "(SELECT COUNT(*) FROM community_reports x WHERE x.walk_id = w.id) AS reports "
    "FROM community_walks w JOIN readers r ON r.id = w.reader_id ")


# ── The feed ─────────────────────────────────────────────────────────────

NEAR_M = 30000   # a walk belongs to a city when its middle is this close to the city's centre


def community_feed(viewer=None, city=None, limit=FEED_LIMIT, center=None):
    """The city's community walks for this viewer: newest and most liked first, without
    hidden posts, posts the viewer reported, or posts by people they blocked. Never fails.
    center=(lat, lng): walks within NEAR_M of that point (the city feed); else by city name."""
    city = city or feed_city()
    viewer_id = viewer["id"] if viewer else None
    try:
        _tables()
        since = (datetime.now(timezone.utc) - timedelta(days=FEED_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if center:
            clat, clng = float(center[0]), float(center[1])
            dlat = NEAR_M / 111320.0
            dlng = NEAR_M / (111320.0 * max(0.2, math.cos(math.radians(clat))))
            rows = db.query(_SELECT + "WHERE w.status = 'live' AND w.created_at >= :since "
                            "AND w.lat BETWEEN :la AND :lb AND w.lng BETWEEN :na AND :nb "
                            "ORDER BY w.created_at DESC LIMIT 200", since=since,
                            la=clat - dlat, lb=clat + dlat, na=clng - dlng, nb=clng + dlng)
        else:
            rows = db.query(_SELECT + "WHERE w.status = 'live' AND w.city = :city AND w.created_at >= :since "
                            "ORDER BY w.created_at DESC LIMIT 200", city=city, since=since)
        blocked, reported, liked = set(), set(), set()
        if viewer_id:
            blocked = {r["blocked_id"] for r in db.query("SELECT blocked_id FROM reader_blocks WHERE reader_id = :r", r=viewer_id)}
            reported = {r["walk_id"] for r in db.query("SELECT walk_id FROM community_reports WHERE reader_id = :r", r=viewer_id)}
            liked = {r["walk_id"] for r in db.query("SELECT walk_id FROM community_likes WHERE reader_id = :r", r=viewer_id)}
        now = datetime.now(timezone.utc)

        def score(r):
            try:
                hours = (now - datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
            except ValueError:
                hours = 1000
            return (int(r["likes"] or 0) + 1.5) / math.pow(hours + 6, 0.8)
        rows = [r for r in rows if not _hidden(r) and r["id"] not in reported and r["reader_id"] not in blocked]
        rows.sort(key=score, reverse=True)
        rows = rows[:limit]
        walked = wp.walked_counts([r["walk_key"] for r in rows])
        return [_card(r, viewer_id, r["id"] in liked, walked.get(r["walk_key"], 0)) for r in rows]
    except Exception as exc:  # noqa: BLE001 -- the page must render without the community feed
        print("community: could not load the feed:", exc)
        return []


# ── Posting ──────────────────────────────────────────────────────────────

@community_bp.post("/api/community/walks")
@reader_required_json
def post_walk():
    reader = current_reader()
    from onboarding import verify_block   # posting needs a verified email (onboarding.py)
    blocked = verify_block(reader, "post walks")
    if blocked:
        return blocked
    _tables()
    form = request.form
    title = _text(form.get("title"), MAX_TITLE)
    note = _note(form.get("note"))
    if len(title) < 2:
        return jsonify(error="Give your walk a name."), 400
    if moderation.check_comment_text(title + "\n" + note):
        return jsonify(error="Please keep it friendly, with no more than a couple of links."), 400
    source = form.get("source") if form.get("source") in SOURCES else "route"

    try:
        raw = json.loads(form.get("stops") or "[]")
    except ValueError:
        raw = []
    slugs = []
    for st in raw if isinstance(raw, list) else []:
        slug = str((st.get("slug") if isinstance(st, dict) else st) or "")
        if _SLUG_RE.match(slug) and slug not in slugs:
            slugs.append(slug)
    slugs = slugs[:MAX_STOPS]
    if len(slugs) < 2:
        return jsonify(error="A walk needs at least 2 stops."), 400

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    posted = db.query_one("SELECT COUNT(*) AS n FROM community_walks WHERE reader_id = :r AND created_at >= :d",
                          r=reader["id"], d=today)
    if int((posted or {}).get("n") or 0) >= POSTS_PER_DAY:
        return jsonify(error=f"You can post {POSTS_PER_DAY} walks a day. Try again tomorrow."), 429

    try:
        known = _directory_places(slugs)
    except wp.WalkError as exc:
        return jsonify(error=str(exc)), 503
    places = [known[s] for s in slugs if s in known]
    if len(places) < 2:
        return jsonify(error="Some of these places aren't in the directory any more, so this walk can't be posted."), 400
    stops = [{"slug": p["slug"], "name": _text(p.get("name"), 80)} for p in places]
    key = _walk_key([p["slug"] for p in places])
    if db.query_one("SELECT id FROM community_walks WHERE reader_id = :r AND walk_key = :k AND status = 'live'",
                    r=reader["id"], k=key):
        return jsonify(error="You've already posted this walk."), 409

    try:
        photo = _photo_bytes(request.files.get("photo"))
    except ValueError as exc:
        msg = "That photo is too big (12 MB at most)." if str(exc) == "too_big" else "That file isn't a photo we can use."
        return jsonify(error=msg), 400

    lat = sum(p["lat"] for p in places) / len(places)
    lng = sum(p["lng"] for p in places) / len(places)
    cities = Counter(_text(p.get("city"), 60) for p in places if p.get("city"))
    city = _city_near(lat, lng) or (cities.most_common(1)[0][0] if cities else "")
    walk_id = db.new_id()
    now = db.now_iso()
    db.execute(
        "INSERT INTO community_walks (id, reader_id, title, note, stops_json, walk_key, city, district, lat, lng, "
        "source, has_photo, status, cleared, created_at) VALUES (:id, :r, :t, :n, :s, :k, :c, :d, :la, :ln, :src, :ph, 'live', 0, :at)",
        id=walk_id, r=reader["id"], t=title, n=note, s=json.dumps(stops), k=key, c=city,
        d=_districts([p.get("address") for p in places]), la=lat, ln=lng, src=source, ph=1 if photo else 0, at=now)
    if photo:
        db.execute("INSERT INTO community_photos (walk_id, data, created_at) VALUES (:w, :d, :t)", w=walk_id, d=photo, t=now)
    wp.log_event("share", walk=key, places=stops)
    row = db.query_one(_SELECT + "WHERE w.id = :id", id=walk_id)
    return jsonify(ok=True, post=_card(row, reader["id"]), city=city)


@community_bp.delete("/api/community/walks/<walk_id>")
@reader_required_json
def delete_walk(walk_id):
    reader = current_reader()
    _tables()
    row = db.query_one("SELECT reader_id FROM community_walks WHERE id = :id", id=walk_id)
    if not row or row["reader_id"] != reader["id"]:
        return jsonify(error="not_found"), 404
    _remove(walk_id)
    return jsonify(ok=True)


def _remove(walk_id):
    db.execute("UPDATE community_walks SET status = 'removed', has_photo = 0 WHERE id = :id", id=walk_id)
    db.execute("DELETE FROM community_photos WHERE walk_id = :id", id=walk_id)


def _live(walk_id):
    _tables()
    return db.query_one("SELECT id, reader_id FROM community_walks WHERE id = :id AND status = 'live'", id=walk_id)


def _owner_page(reader_id):
    """Their own profile page, where the walk they posted is listed."""
    try:
        import friends
        h = friends.handle_of(reader_id)
        return "/u/" + h if h else "/walk"
    except Exception:  # noqa: BLE001
        return "/walk"


@community_bp.post("/api/community/walks/<walk_id>/like")
@reader_required_json
def like_walk(walk_id):
    reader = current_reader()
    row = _live(walk_id)
    if not row:
        return jsonify(error="not_found"), 404
    if row["reader_id"] == reader["id"]:
        return jsonify(error="That's your own walk."), 400
    done = db.execute("DELETE FROM community_likes WHERE walk_id = :w AND reader_id = :r", w=walk_id, r=reader["id"])
    liked = not done
    if liked:
        try:
            db.execute("INSERT INTO community_likes (walk_id, reader_id, created_at) VALUES (:w, :r, :t)",
                       w=walk_id, r=reader["id"], t=db.now_iso())
        except IntegrityError:
            pass
        else:
            import push
            w = db.query_one("SELECT title FROM community_walks WHERE id = :w", w=walk_id) or {}
            push.notify(row["reader_id"], "Someone liked your walk",
                        f"{reader['display_name']} liked “{w.get('title') or 'your walk'}”.",
                        _owner_page(row["reader_id"]), key=f"like:{walk_id}:{reader['id']}")
    n = db.query_one("SELECT COUNT(*) AS n FROM community_likes WHERE walk_id = :w", w=walk_id)
    return jsonify(ok=True, liked=liked, likes=int((n or {}).get("n") or 0))


@community_bp.post("/api/community/walks/<walk_id>/report")
@reader_required_json
def report_walk(walk_id):
    reader = current_reader()
    row = _live(walk_id)
    if not row:
        return jsonify(error="not_found"), 404
    if row["reader_id"] == reader["id"]:
        return jsonify(error="That's your own walk."), 400
    reason = (request.get_json(silent=True) or {}).get("reason")
    if reason not in moderation.REPORT_REASONS:
        reason = "other"
    try:
        db.execute("INSERT INTO community_reports (walk_id, reader_id, reason, created_at) VALUES (:w, :r, :why, :t)",
                   w=walk_id, r=reader["id"], why=reason, t=db.now_iso())
    except IntegrityError:
        pass  # already reported by this reader -- counts once
    return jsonify(ok=True)


@community_bp.post("/api/community/walks/<walk_id>/block")
@reader_required_json
def block_walker(walk_id):
    reader = current_reader()
    _tables()
    row = db.query_one("SELECT reader_id FROM community_walks WHERE id = :id", id=walk_id)
    if not row:
        return jsonify(error="not_found"), 404
    if row["reader_id"] == reader["id"]:
        return jsonify(error="That's your own walk."), 400
    try:
        db.execute("INSERT INTO reader_blocks (reader_id, blocked_id, created_at) VALUES (:r, :b, :t)",
                   r=reader["id"], b=row["reader_id"], t=db.now_iso())
    except IntegrityError:
        pass
    return jsonify(ok=True)


@community_bp.get("/community/photo/<walk_id>.jpg")
def walk_photo(walk_id):
    _tables()
    row = db.query_one("SELECT p.data FROM community_photos p JOIN community_walks w ON w.id = p.walk_id "
                       "WHERE p.walk_id = :id AND w.status = 'live'", id=walk_id)
    if not row:
        abort(404)
    reply = Response(bytes(row["data"]), mimetype="image/jpeg")
    reply.headers["Cache-Control"] = "public, max-age=86400"
    return reply


def delete_reader_posts(reader_id):
    """Everything community of one reader (account deletion / ban): their posts with
    their photos, likes and reports, and the likes and reports they made."""
    _tables()
    mine = "(SELECT id FROM community_walks WHERE reader_id = :r)"
    db.execute(f"DELETE FROM community_photos WHERE walk_id IN {mine}", r=reader_id)
    db.execute(f"DELETE FROM community_likes WHERE walk_id IN {mine}", r=reader_id)
    db.execute(f"DELETE FROM community_reports WHERE walk_id IN {mine}", r=reader_id)
    db.execute("DELETE FROM community_likes WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM community_reports WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM community_walks WHERE reader_id = :r", r=reader_id)


# ── The owner's review screen ────────────────────────────────────────────

@community_bp.get("/admin/community")
@owner_required
def community_admin():
    _tables()
    rows = db.query(_SELECT.replace("r.display_name, ", "r.display_name, r.email, ")
                    + "WHERE w.status = 'live' ORDER BY w.created_at DESC LIMIT 300")
    reasons = {}
    for rr in db.query("SELECT walk_id, reason FROM community_reports ORDER BY created_at"):
        reasons.setdefault(rr["walk_id"], []).append(moderation.REPORT_REASONS.get(rr["reason"], rr["reason"]))
    items = []
    for r in rows:
        card = _card(r)
        card.update(email=r["email"], city=r["city"], reports=int(r["reports"] or 0), hidden=_hidden(r),
                    cleared=bool(int(r["cleared"] or 0)), reasons=reasons.get(r["id"], []),
                    created=(r["created_at"] or "")[:16].replace("T", " "))
        items.append(card)
    reported = [i for i in items if i["reports"] and not i["cleared"]]
    reported.sort(key=lambda i: -i["reports"])
    from readers import csrf_token
    return render_template("admin/community.html", reported=reported, recent=items[:60],
                           hide_after=HIDE_AFTER_REPORTS, csrf=csrf_token())


@community_bp.post("/admin/community/<walk_id>/<action>")
@owner_required
def community_admin_action(walk_id, action):
    if not _csrf_ok():
        abort(400)
    _tables()
    row = db.query_one("SELECT w.reader_id, r.email FROM community_walks w JOIN readers r ON r.id = w.reader_id "
                       "WHERE w.id = :id", id=walk_id)
    if not row:
        flash("That walk no longer exists.", "error")
    elif action == "keep":
        db.execute("UPDATE community_walks SET cleared = 1 WHERE id = :id", id=walk_id)
        flash("Kept. The walk is visible again and further reports won't hide it.", "success")
    elif action == "remove":
        _remove(walk_id)
        flash("Walk removed from the feed.", "success")
    elif action == "ban":
        try:
            db.execute("INSERT INTO banned_emails (email, created_at) VALUES (:e, :t)",
                       e=row["email"].lower(), t=db.now_iso())
        except IntegrityError:
            pass
        moderation.delete_reader(row["reader_id"], email=row["email"], remove_newsletter=False)
        flash("Walker banned: their account and posts were removed and that email can't sign up again.", "success")
    else:
        abort(404)
    return redirect(url_for("community.community_admin"))
