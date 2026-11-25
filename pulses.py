"""
Pulses: short videos on the map that disappear after 8 hours.

Anyone signed in (with a verified email) posts one or more clips from where they are; each clip is cut to its
first 8 seconds and can tag a place. A post shows as a pulsing circle in the middle of the DISTRICT it was posted
from (Neubau, Wieden...; a neighbourhood or the city where there are no districts) -- never the exact spot, and
we never store where the person stood. Everyone sees every live pulse. Tap one and the page plays that
district's clips, newest first. After 8 hours a post and its videos are deleted (the sweep below).

  GET    /api/pulses?lat&lng           live pulses near there: [{id, lat, lng, count, thumb, label}]  (one per district)
  GET    /api/pulses/<pulse id>         that district's clips: [{post, video, poster, place, by, mine, ago, left}]
  POST   /api/pulses/where {lat, lng}   the district you'd post to ({name, label})
  POST   /api/pulses/sign               signed upload settings: the page uploads straight to Cloudinary
  POST   /api/pulses {lat, lng, clips: [{public_id, place_slug?, place_name?}]}   post them (up to 10)
  DELETE /api/pulses/post/<id>          take your own post down now
  POST   /api/pulses/post/<id>/report   two reports hide a post until the owner looks at it
  POST   /api/pulses/post/<id>/block    block the person who posted it (same reader_blocks as comments)
  GET    /admin/pulses                  the owner's list of live posts, with Delete

Videos live on Cloudinary (CLOUDINARY_URL = cloudinary://<api key>:<api secret>@<cloud name>). The page uploads
each file straight there with a signature from /api/pulses/sign, into a folder only that reader can get a
signature for; Cloudinary makes the 8-second, phone-sized copy (the "eager" transformation) that the app plays.
Expired posts are deleted here and on Cloudinary by sweep(), which runs at most every few minutes while people
use the app. Tables are created on first use.
"""
import hashlib
import hmac
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import requests
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import db
from admin import owner_required
from readers import _csrf_ok, csrf_token, current_reader, reader_required_json

pulses_bp = Blueprint("pulses", __name__)

LIFETIME_HOURS = 8
CLIP_SECONDS = 8
MAX_CLIPS_PER_POST = 10
MAX_CLIPS_PER_DAY = int(os.environ.get("PULSE_DAILY_CLIPS", "30"))
HIDE_AFTER_REPORTS = 2
NEAR_KM = 60
SWEEP_EVERY = 300          # seconds
# The copy the app plays: the first 8 seconds, at most 720x1280, compressed for phones.
PLAY = f"so_0,eo_{CLIP_SECONDS},c_limit,w_720,h_1280,q_auto"
POSTER = "so_0,c_fill,w_240,h_240,q_auto"
_ready = False
_last_sweep = 0.0
_sweep_lock = threading.Lock()


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS pulse_posts (
            id            TEXT PRIMARY KEY,
            reader_id     TEXT NOT NULL,
            district_key  TEXT NOT NULL,
            district      TEXT NOT NULL,
            label         TEXT,
            lat           REAL NOT NULL,
            lng           REAL NOT NULL,
            reports       INTEGER NOT NULL DEFAULT 0,
            cleared       INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            expires_at    TEXT NOT NULL
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_pulse_posts_expires ON pulse_posts (expires_at)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS pulse_clips (
            id          TEXT PRIMARY KEY,
            post_id     TEXT NOT NULL,
            public_id   TEXT NOT NULL,
            position    INTEGER NOT NULL,
            place_slug  TEXT,
            place_name  TEXT
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS pulse_reports (
            post_id     TEXT NOT NULL,
            reader_id   TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (post_id, reader_id)
        )""")
    _ready = True


# ── Cloudinary ───────────────────────────────────────────────────────────

def cloudinary():
    """{cloud, key, secret} from CLOUDINARY_URL (or the three CLOUDINARY_* settings), or None."""
    url = (os.environ.get("CLOUDINARY_URL") or "").strip()
    if url.startswith("cloudinary://"):
        u = urlparse(url)
        if u.username and u.password and u.hostname:
            return {"cloud": u.hostname, "key": unquote(u.username), "secret": unquote(u.password)}
    cloud, key, secret = (os.environ.get(k, "").strip() for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    return {"cloud": cloud, "key": key, "secret": secret} if cloud and key and secret else None


def enabled():
    return cloudinary() is not None


def _api_base():
    return (os.environ.get("CLOUDINARY_API_BASE") or "https://api.cloudinary.com").rstrip("/")


def _res_base():
    return (os.environ.get("CLOUDINARY_RES_BASE") or "https://res.cloudinary.com").rstrip("/")


def _sign(params, secret):
    base = "&".join(f"{k}={params[k]}" for k in sorted(params)) + secret
    return hashlib.sha1(base.encode()).hexdigest()


def _folder(reader_id):
    """A folder only this reader gets upload signatures for: a clip's public id proves who uploaded it."""
    secret = (os.environ.get("SECRET_KEY") or "pulses").encode()
    return "pulses/" + hmac.new(secret, reader_id.encode(), hashlib.sha256).hexdigest()[:20]


def video_url(public_id):
    c = cloudinary()
    return f"{_res_base()}/{c['cloud']}/video/upload/{PLAY}/{public_id}.mp4" if c else ""


def poster_url(public_id):
    c = cloudinary()
    return f"{_res_base()}/{c['cloud']}/video/upload/{POSTER}/{public_id}.jpg" if c else ""


def _destroy(public_id):
    """Delete a video on Cloudinary (with the 8-second copy). Never fails."""
    c = cloudinary()
    if not c:
        return
    params = {"public_id": public_id, "invalidate": "true", "timestamp": str(int(time.time()))}
    try:
        requests.post(f"{_api_base()}/v1_1/{c['cloud']}/video/destroy",
                      data=dict(params, api_key=c["key"], signature=_sign(params, c["secret"])), timeout=10)
    except requests.RequestException as exc:
        print("pulses: could not delete a video on Cloudinary:", exc)


# ── Time and the sweep ───────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _delete_posts(ids):
    for pid in ids:
        clips = db.query("SELECT public_id FROM pulse_clips WHERE post_id = :p", p=pid)
        db.execute("DELETE FROM pulse_clips WHERE post_id = :p", p=pid)
        db.execute("DELETE FROM pulse_reports WHERE post_id = :p", p=pid)
        db.execute("DELETE FROM pulse_posts WHERE id = :p", p=pid)
        for c in clips:
            _destroy(c["public_id"])


def sweep(force=False):
    """Delete posts past their 8 hours (here and on Cloudinary). At most every SWEEP_EVERY seconds; in the background."""
    global _last_sweep
    if not force and time.time() - _last_sweep < SWEEP_EVERY:
        return
    if not _sweep_lock.acquire(blocking=False):
        return
    _last_sweep = time.time()

    def run():
        try:
            _tables()
            rows = db.query("SELECT id FROM pulse_posts WHERE expires_at <= :t", t=_iso(_now()))
            _delete_posts([r["id"] for r in rows])
        except Exception as exc:  # noqa: BLE001
            print("pulses: sweep failed:", exc)
        finally:
            _sweep_lock.release()
    if force:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


# ── Where ────────────────────────────────────────────────────────────────

def _district(lat, lng):
    """{key, name, label, lat, lng}: the district at a point and its centre, from the directory's geocoder."""
    import city_feed
    found = city_feed._directory({"lat": lat, "lng": lng, "level": "district"})
    if not found:
        return None
    d = city_feed._clean_city(found[0].get("name"), found[0].get("label"), found[0].get("lat"), found[0].get("lng"))
    if not d:
        return None
    d["key"] = hashlib.sha1(d["label"].lower().encode()).hexdigest()[:16]
    return d


def _point(data):
    try:
        lat, lng = float(data.get("lat")), float(data.get("lng"))
    except (TypeError, ValueError):
        return None
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def _ago(iso):
    from community import _ago as ago
    return ago(iso)


def _left(expires):
    end = _parse(expires)
    if not end:
        return ""
    mins = max(0, int((end - _now()).total_seconds() // 60))
    return f"{math.ceil(mins / 60)} h left" if mins >= 60 else f"{max(1, mins)} min left"


def _visible_sql(viewer_id):
    sql = " AND p.expires_at > :now AND (p.reports < :hide OR p.cleared = 1)"
    if viewer_id:
        sql += (" AND p.reader_id NOT IN (SELECT blocked_id FROM reader_blocks WHERE reader_id = :me)"
                " AND p.reader_id NOT IN (SELECT reader_id FROM reader_blocks WHERE blocked_id = :me)")
    return sql


# ── API ──────────────────────────────────────────────────────────────────

def _no_store(reply):
    reply.headers["Cache-Control"] = "no-store"
    return reply


@pulses_bp.get("/api/pulses")
def api_map():
    sweep()
    if not enabled():
        return _no_store(jsonify(pulses=[], enabled=False))
    at = _point(request.args)
    _tables()
    reader = current_reader()
    me = reader["id"] if reader else ""
    params = {"now": _iso(_now()), "hide": HIDE_AFTER_REPORTS, "me": me}
    where = "WHERE 1 = 1" + _visible_sql(me)
    if at:   # a box around where the map is: about NEAR_KM each way
        dlat, dlng = NEAR_KM / 111.0, NEAR_KM / max(20.0, 111.0 * abs(math.cos(math.radians(at[0]))))
        where += " AND p.lat BETWEEN :la0 AND :la1 AND p.lng BETWEEN :ln0 AND :ln1"
        params.update(la0=at[0] - dlat, la1=at[0] + dlat, ln0=at[1] - dlng, ln1=at[1] + dlng)
    rows = db.query("SELECT p.id, p.district_key, p.district, p.lat, p.lng, p.created_at, "
                    "(SELECT COUNT(*) FROM pulse_clips c WHERE c.post_id = p.id) AS clips, "
                    "(SELECT c.public_id FROM pulse_clips c WHERE c.post_id = p.id ORDER BY c.position LIMIT 1) AS first "
                    "FROM pulse_posts p " + where + " ORDER BY p.created_at DESC LIMIT 500", **params)
    groups = {}
    for r in rows:   # newest first, so each district's thumbnail is its newest clip
        g = groups.get(r["district_key"])
        if not g:
            g = groups[r["district_key"]] = {"id": r["district_key"], "lat": float(r["lat"]), "lng": float(r["lng"]),
                                             "label": r["district"], "count": 0, "thumb": poster_url(r["first"]) if r["first"] else ""}
        g["count"] += int(r["clips"] or 0)
    return _no_store(jsonify(pulses=list(groups.values()), enabled=True))


@pulses_bp.get("/api/pulses/<pulse_id>")
def api_open(pulse_id):
    if not re.match(r"^[0-9a-f]{16}$", pulse_id):
        return jsonify(error="not_found"), 404
    _tables()
    reader = current_reader()
    me = reader["id"] if reader else ""
    rows = db.query("SELECT p.id, p.reader_id, p.district, p.created_at, p.expires_at, r.display_name FROM pulse_posts p "
                    "JOIN readers r ON r.id = p.reader_id WHERE p.district_key = :k" + _visible_sql(me) +
                    " ORDER BY p.created_at DESC LIMIT 50", k=pulse_id, now=_iso(_now()), hide=HIDE_AFTER_REPORTS, me=me)
    clips = []
    for p in rows:
        for c in db.query("SELECT public_id, place_slug, place_name FROM pulse_clips WHERE post_id = :p ORDER BY position", p=p["id"]):
            clips.append({"post": p["id"], "video": video_url(c["public_id"]), "poster": poster_url(c["public_id"]),
                          "place": {"slug": c["place_slug"], "name": c["place_name"]} if c["place_slug"] else None,
                          "by": (p["display_name"] or "A walker").split(" ")[0], "mine": p["reader_id"] == me,
                          "ago": _ago(p["created_at"]), "left": _left(p["expires_at"]), "district": p["district"]})
    if not clips:
        return _no_store(jsonify(error="These videos have disappeared.", clips=[])), 404
    return _no_store(jsonify(clips=clips, district=rows[0]["district"]))


@pulses_bp.post("/api/pulses/where")
@reader_required_json
def api_where():
    at = _point(request.get_json(silent=True) or {})
    if not at:
        return jsonify(error="We need your location to post."), 400
    d = _district(*at)
    if not d:
        return jsonify(error="Couldn’t work out where you are. Try again in a moment."), 503
    return jsonify(ok=True, name=d["name"], label=d["label"])


def _verify(reader):
    from onboarding import verify_block
    return verify_block(reader, "post videos")


def _clips_today(reader_id):
    since = _iso(_now() - timedelta(hours=24))
    row = db.query_one("SELECT COUNT(*) AS n FROM pulse_clips c JOIN pulse_posts p ON p.id = c.post_id "
                       "WHERE p.reader_id = :r AND p.created_at >= :s", r=reader_id, s=since)
    return int((row or {}).get("n") or 0)


@pulses_bp.post("/api/pulses/sign")
@reader_required_json
def api_sign():
    reader = current_reader()
    blocked = _verify(reader)
    if blocked:
        return blocked
    c = cloudinary()
    if not c:
        return jsonify(error="Video posting isn’t switched on yet."), 503
    _tables()
    if _clips_today(reader["id"]) >= MAX_CLIPS_PER_DAY:
        return jsonify(error=f"You’ve posted {MAX_CLIPS_PER_DAY} clips today. Try again tomorrow."), 429
    params = {"folder": _folder(reader["id"]), "timestamp": str(int(time.time())),
              "eager": PLAY + "/mp4|" + POSTER + "/jpg", "eager_async": "true", "tags": "pulse"}
    return jsonify(ok=True, url=f"{_api_base()}/v1_1/{c['cloud']}/video/upload", api_key=c["key"],
                   params=dict(params, signature=_sign(params, c["secret"])), max_clips=MAX_CLIPS_PER_POST)


@pulses_bp.post("/api/pulses")
@reader_required_json
def api_post():
    reader = current_reader()
    blocked = _verify(reader)
    if blocked:
        return blocked
    if not enabled():
        return jsonify(error="Video posting isn’t switched on yet."), 503
    data = request.get_json(silent=True) or {}
    at = _point(data)
    if not at:
        return jsonify(error="We need your location to post."), 400
    folder = _folder(reader["id"]) + "/"
    clips = []
    for c in (data.get("clips") or [])[:MAX_CLIPS_PER_POST]:
        if not isinstance(c, dict):
            continue
        pid = str(c.get("public_id") or "")
        if not pid.startswith(folder) or not re.match(r"^[A-Za-z0-9_/-]{1,200}$", pid):
            continue   # only clips this reader uploaded with our signature
        slug = str(c.get("place_slug") or "")
        slug = slug if re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$", slug) else ""
        clips.append({"public_id": pid, "slug": slug or None,
                      "name": (" ".join(str(c.get("place_name") or "").split())[:80] or None) if slug else None})
    if not clips:
        return jsonify(error="Add at least one video."), 400
    _tables()
    if _clips_today(reader["id"]) + len(clips) > MAX_CLIPS_PER_DAY:
        return jsonify(error=f"That’s more than {MAX_CLIPS_PER_DAY} clips today. Post fewer, or try tomorrow."), 429
    d = _district(*at)
    if not d:
        return jsonify(error="Couldn’t work out where you are. Try again in a moment."), 503
    now = _now()
    post_id = db.new_id()
    db.execute("INSERT INTO pulse_posts (id, reader_id, district_key, district, label, lat, lng, reports, cleared, created_at, expires_at) "
               "VALUES (:id, :r, :k, :d, :l, :lat, :lng, 0, 0, :c, :e)", id=post_id, r=reader["id"], k=d["key"], d=d["name"],
               l=d["label"], lat=d["lat"], lng=d["lng"], c=_iso(now), e=_iso(now + timedelta(hours=LIFETIME_HOURS)))
    for i, c in enumerate(clips):
        db.execute("INSERT INTO pulse_clips (id, post_id, public_id, position, place_slug, place_name) VALUES (:id, :p, :pid, :pos, :s, :n)",
                   id=db.new_id(), p=post_id, pid=c["public_id"], pos=i, s=c["slug"], n=c["name"])
    return jsonify(ok=True, id=post_id, pulse=d["key"], district=d["name"], clips=len(clips))


def _post_row(post_id):
    _tables()
    return db.query_one("SELECT id, reader_id FROM pulse_posts WHERE id = :id", id=post_id)


@pulses_bp.delete("/api/pulses/post/<post_id>")
@reader_required_json
def api_delete(post_id):
    reader = current_reader()
    row = _post_row(post_id)
    if row and row["reader_id"] == reader["id"]:
        _delete_posts([post_id])
    return jsonify(ok=True)


@pulses_bp.post("/api/pulses/post/<post_id>/report")
@reader_required_json
def api_report(post_id):
    reader = current_reader()
    row = _post_row(post_id)
    if not row:
        return jsonify(ok=True)
    try:
        db.execute("INSERT INTO pulse_reports (post_id, reader_id, created_at) VALUES (:p, :r, :t)", p=post_id, r=reader["id"], t=db.now_iso())
        db.execute("UPDATE pulse_posts SET reports = reports + 1 WHERE id = :p", p=post_id)
    except Exception:  # noqa: BLE001 -- reported already: counts once
        pass
    return jsonify(ok=True)


@pulses_bp.post("/api/pulses/post/<post_id>/block")
@reader_required_json
def api_block(post_id):
    reader = current_reader()
    row = _post_row(post_id)
    if not row or row["reader_id"] == reader["id"]:
        return jsonify(error="That’s your own video."), 400
    try:
        db.execute("INSERT INTO reader_blocks (reader_id, blocked_id, created_at) VALUES (:r, :b, :t)",
                   r=reader["id"], b=row["reader_id"], t=db.now_iso())
    except Exception:  # noqa: BLE001
        pass
    return jsonify(ok=True)


def delete_reader_posts(reader_id):
    _tables()
    _delete_posts([r["id"] for r in db.query("SELECT id FROM pulse_posts WHERE reader_id = :r", r=reader_id)])
    db.execute("DELETE FROM pulse_reports WHERE reader_id = :r", r=reader_id)


# ── The owner's list ─────────────────────────────────────────────────────

@pulses_bp.get("/admin/pulses")
@owner_required
def admin_list():
    sweep()
    _tables()
    posts = db.query("SELECT p.id, p.district, p.label, p.reports, p.cleared, p.created_at, p.expires_at, r.display_name, r.email "
                     "FROM pulse_posts p JOIN readers r ON r.id = p.reader_id WHERE p.expires_at > :now ORDER BY p.created_at DESC",
                     now=_iso(_now()))
    for p in posts:
        p["clips"] = [{"video": video_url(c["public_id"]), "poster": poster_url(c["public_id"]), "place": c["place_name"]}
                      for c in db.query("SELECT public_id, place_name FROM pulse_clips WHERE post_id = :p ORDER BY position", p=p["id"])]
        p["ago"], p["left"] = _ago(p["created_at"]), _left(p["expires_at"])
        p["hidden"] = int(p["reports"] or 0) >= HIDE_AFTER_REPORTS and not int(p["cleared"] or 0)
    return render_template("admin/pulses.html", posts=posts, enabled=enabled(), csrf=csrf_token())


@pulses_bp.post("/admin/pulses/<post_id>/<action>")
@owner_required
def admin_act(post_id, action):
    if not _csrf_ok():
        abort(400)
    _tables()
    if action == "delete":
        _delete_posts([post_id])
        flash("Deleted.", "success")
    elif action == "clear":
        db.execute("UPDATE pulse_posts SET cleared = 1 WHERE id = :p", p=post_id)
        flash("It's back on the map.", "success")
    return redirect(url_for("pulses.admin_list"))
