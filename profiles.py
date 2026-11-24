"""
Profiles: the walks you finished and the cities you've been to, for your friends to see.

  /u/<handle>  (friends.py) shows the profile: a map of their cities, recent walks and favourite
  places (4-5 footprints, footprints.py). Friends see it; so does anyone with the link when the
  owner turns on "Public profile". Everyone else gets the Add friend page.

  POST   /api/walk-history                 {title, stops: [{slug, name}], walk?, finished?, reached?}
                                           the walk page sends this when a walk ends: all stops reached,
                                           or at least 2 of them
  POST   /api/walk-history/<id>            {hidden}   hide it from your profile (or show it again)
  DELETE /api/walk-history/<id>
  POST   /api/my-cities                    {name, label, lat, lng}   add a city by hand (the city search)
  DELETE /api/my-cities?key=...            take one off (a city from your walks stays off)
  POST   /api/profile/visibility           {public}

A walk's city comes from its first stop: the directory gives the stop's position
(/embed/places-by-slug) and the geocoder names the city there (city_feed.city_at).
Only the walk's title, stops and city are kept -- never the route someone actually walked.
Tables are created on first use.
"""
import json
import re

from flask import jsonify, request

import db
from friends import friends_bp
from readers import current_reader, reader_required_json

MAX_HISTORY = 500
RECENT = 20
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_ready = False


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS walk_history (
            id          TEXT PRIMARY KEY,
            reader_id   TEXT NOT NULL,
            title       TEXT NOT NULL,
            stops_json  TEXT NOT NULL,
            walk_key    TEXT,
            city_key    TEXT,
            city_name   TEXT,
            city_label  TEXT,
            finished    INTEGER NOT NULL DEFAULT 0,
            hidden      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_walk_history_reader ON walk_history (reader_id, created_at)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS reader_cities (
            reader_id   TEXT NOT NULL,
            city_key    TEXT NOT NULL,
            name        TEXT NOT NULL,
            label       TEXT NOT NULL,
            lat         REAL NOT NULL,
            lng         REAL NOT NULL,
            source      TEXT NOT NULL,
            hidden      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (reader_id, city_key)
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS profile_settings (
            reader_id   TEXT PRIMARY KEY,
            public      INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT NOT NULL
        )""")
    _ready = True


def _text(v, n):
    return " ".join(str(v or "").split())[:n]


def _city_key(label):
    return _text(label, 160).lower()


def is_public(reader_id):
    _tables()
    row = db.query_one("SELECT public FROM profile_settings WHERE reader_id = :r", r=reader_id)
    return bool(row and int(row["public"] or 0))


def _remember_city(reader_id, city, source):
    """Adds the city to their list (a hidden one stays hidden unless they add it by hand)."""
    key = _city_key(city["label"] or city["name"])
    row = db.query_one("SELECT source, hidden FROM reader_cities WHERE reader_id = :r AND city_key = :k", r=reader_id, k=key)
    if row:
        if source == "manual" and int(row["hidden"] or 0):
            db.execute("UPDATE reader_cities SET hidden = 0, source = 'manual' WHERE reader_id = :r AND city_key = :k", r=reader_id, k=key)
        return key
    try:
        db.execute("INSERT INTO reader_cities (reader_id, city_key, name, label, lat, lng, source, hidden, created_at) "
                   "VALUES (:r, :k, :n, :l, :lat, :lng, :s, 0, :t)", r=reader_id, k=key, n=city["name"], l=city["label"] or city["name"],
                   lat=float(city["lat"]), lng=float(city["lng"]), s=source, t=db.now_iso())
    except Exception:  # noqa: BLE001 -- added by a request running at the same time
        pass
    return key


def _where(slugs):
    """The city of the walk's first stop the directory knows, or None. Never fails."""
    try:
        import city_feed
        from community import _directory_places
        places = _directory_places(slugs[:25])
        for s in slugs:
            p = places.get(s)
            if p and p.get("lat") is not None and p.get("lng") is not None:
                return city_feed.city_at(float(p["lat"]), float(p["lng"]))
    except Exception as exc:  # noqa: BLE001
        print("profiles: could not place the walk:", exc)
    return None


# ── What a profile shows ─────────────────────────────────────────────────

def _ago(iso):
    from community import _ago as ago
    return ago(iso)


def profile_data(reader_id, owner=False):
    """{walks, cities, places, counts, public} for /u/<handle>. The owner also sees hidden walks and cities."""
    _tables()
    hid = "" if owner else " AND hidden = 0"
    rows = db.query("SELECT id, title, stops_json, city_key, city_label, city_name, finished, hidden, created_at FROM walk_history "
                    "WHERE reader_id = :r" + hid + " ORDER BY created_at DESC LIMIT :n", r=reader_id, n=RECENT)
    walks = []
    for w in rows:
        stops = json.loads(w["stops_json"])
        walks.append({"id": w["id"], "title": w["title"], "stops": stops, "city": w["city_name"] or "",
                      "where": w["city_label"] or "", "ago": _ago(w["created_at"]), "finished": bool(int(w["finished"] or 0)),
                      "hidden": bool(int(w["hidden"] or 0)),
                      "open": "/walk?" + "s=" + ",".join(s["slug"] for s in stops) + "&t=" + _quote(w["title"])})
    per_city = {r["city_key"]: int(r["n"]) for r in db.query(
        "SELECT city_key, COUNT(*) AS n FROM walk_history WHERE reader_id = :r AND hidden = 0 AND city_key IS NOT NULL GROUP BY city_key", r=reader_id)}
    total = db.query_one("SELECT COUNT(*) AS n FROM walk_history WHERE reader_id = :r AND hidden = 0", r=reader_id)
    crow = db.query("SELECT city_key, name, label, lat, lng, source, hidden FROM reader_cities WHERE reader_id = :r" + hid +
                    " ORDER BY created_at DESC", r=reader_id)
    cities = [{"key": c["city_key"], "name": c["name"], "label": c["label"], "lat": float(c["lat"]), "lng": float(c["lng"]),
               "where": ", ".join(x for x in c["label"].split(", ")[1:] if x.lower() != c["name"].lower()),
               "walks": per_city.get(c["city_key"], 0), "mine": c["source"] == "manual", "hidden": bool(int(c["hidden"] or 0))}
              for c in crow]
    return {"walks": walks, "cities": cities, "places": favourite_places(reader_id),
            "counts": {"walks": int((total or {}).get("n") or 0), "cities": sum(1 for c in cities if not c["hidden"])},
            "public": is_public(reader_id)}


def _quote(s):
    from urllib.parse import quote
    return quote(s, safe="")


def favourite_places(reader_id, limit=8):
    """Places they gave 4 or 5 footprints, best and newest first. Never fails."""
    try:
        import footprints
        footprints._tables()
        rows = db.query("SELECT slug, name, steps FROM place_footprints WHERE reader_id = :r AND steps >= 4 "
                        "ORDER BY steps DESC, updated_at DESC LIMIT :n", r=reader_id, n=limit)
    except Exception as exc:  # noqa: BLE001
        print("profiles: could not read footprints:", exc)
        return []
    out = []
    for r in rows:
        name = r["name"] or r["slug"].replace("-", " ").title()
        out.append({"slug": r["slug"], "name": name, "steps": int(r["steps"]),
                    "open": "/walk?go=" + _quote(r["slug"]) + "&t=" + _quote(name)})
    return out


# ── API ──────────────────────────────────────────────────────────────────

@friends_bp.post("/api/walk-history")
@reader_required_json
def api_log_walk():
    reader = current_reader()
    data = request.get_json(silent=True) or {}
    stops, seen = [], set()
    for st in (data.get("stops") or [])[:25]:
        if isinstance(st, dict) and _SLUG_RE.match(str(st.get("slug") or "")) and st["slug"] not in seen:
            seen.add(st["slug"])
            stops.append({"slug": st["slug"], "name": _text(st.get("name"), 80) or st["slug"]})
    try:
        reached = int(data.get("reached") or 0)
    except (TypeError, ValueError):
        reached = 0
    finished = bool(data.get("finished"))
    if len(stops) < 2 or not (finished or reached >= 2):
        return jsonify(ok=False, error="Not a finished walk."), 400
    title = _text(data.get("title"), 120) or "A walk"
    walk = str(data.get("walk") or "")
    walk = walk if re.match(r"^walk:[0-9a-f]{16}$", walk) else None
    _tables()
    me = reader["id"]
    stops_json = json.dumps(stops)
    # The same walk ended again soon after (stopped, then carried on): one entry.
    recent = db.query_one("SELECT id, finished FROM walk_history WHERE reader_id = :r AND stops_json = :s AND created_at >= :since "
                          "ORDER BY created_at DESC LIMIT 1", r=me, s=stops_json, since=_hours_ago(6))
    if recent:
        if finished and not int(recent["finished"] or 0):
            db.execute("UPDATE walk_history SET finished = 1 WHERE id = :id", id=recent["id"])
        return jsonify(ok=True, id=recent["id"], again=True)
    count = db.query_one("SELECT COUNT(*) AS n FROM walk_history WHERE reader_id = :r", r=me)
    if int((count or {}).get("n") or 0) >= MAX_HISTORY:   # keep the newest
        old = db.query_one("SELECT id FROM walk_history WHERE reader_id = :r ORDER BY created_at LIMIT 1", r=me)
        if old:
            db.execute("DELETE FROM walk_history WHERE id = :id", id=old["id"])
    city = _where([s["slug"] for s in stops])
    key = _remember_city(me, city, "walk") if city else None
    wid = db.new_id()
    db.execute("INSERT INTO walk_history (id, reader_id, title, stops_json, walk_key, city_key, city_name, city_label, finished, hidden, created_at) "
               "VALUES (:id, :r, :t, :s, :w, :k, :cn, :cl, :f, 0, :at)", id=wid, r=me, t=title, s=stops_json, w=walk, k=key,
               cn=(city or {}).get("name"), cl=(city or {}).get("label"), f=1 if finished else 0, at=db.now_iso())
    return jsonify(ok=True, id=wid, city=(city or {}).get("name"))


def _hours_ago(h):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


@friends_bp.post("/api/walk-history/<walk_id>")
@reader_required_json
def api_hide_walk(walk_id):
    reader = current_reader()
    _tables()
    hidden = 1 if (request.get_json(silent=True) or {}).get("hidden") else 0
    db.execute("UPDATE walk_history SET hidden = :h WHERE id = :id AND reader_id = :r", h=hidden, id=walk_id, r=reader["id"])
    return jsonify(ok=True, hidden=bool(hidden))


@friends_bp.delete("/api/walk-history/<walk_id>")
@reader_required_json
def api_delete_walk(walk_id):
    reader = current_reader()
    _tables()
    db.execute("DELETE FROM walk_history WHERE id = :id AND reader_id = :r", id=walk_id, r=reader["id"])
    return jsonify(ok=True)


@friends_bp.post("/api/my-cities")
@reader_required_json
def api_add_city():
    reader = current_reader()
    import city_feed
    d = request.get_json(silent=True) or {}
    city = city_feed._clean_city(d.get("name"), d.get("label"), d.get("lat"), d.get("lng"))
    if not city:
        return jsonify(error="Pick a city from the list."), 400
    _tables()
    n = db.query_one("SELECT COUNT(*) AS n FROM reader_cities WHERE reader_id = :r", r=reader["id"])
    if int((n or {}).get("n") or 0) >= 300:
        return jsonify(error="That's a lot of cities! Remove some first."), 400
    key = _remember_city(reader["id"], city, "manual")
    return jsonify(ok=True, key=key)


@friends_bp.delete("/api/my-cities")
@reader_required_json
def api_remove_city():
    reader = current_reader()
    _tables()
    key = _city_key(request.args.get("key"))
    row = db.query_one("SELECT source FROM reader_cities WHERE reader_id = :r AND city_key = :k", r=reader["id"], k=key)
    if row and row["source"] == "walk":   # it would come back with the next walk there: keep it, hidden
        db.execute("UPDATE reader_cities SET hidden = 1 WHERE reader_id = :r AND city_key = :k", r=reader["id"], k=key)
    elif row:
        db.execute("DELETE FROM reader_cities WHERE reader_id = :r AND city_key = :k", r=reader["id"], k=key)
    return jsonify(ok=True)


@friends_bp.post("/api/profile/visibility")
@reader_required_json
def api_visibility():
    reader = current_reader()
    _tables()
    public = 1 if (request.get_json(silent=True) or {}).get("public") else 0
    if not db.execute("UPDATE profile_settings SET public = :p, updated_at = :t WHERE reader_id = :r", p=public, t=db.now_iso(), r=reader["id"]):
        db.execute("INSERT INTO profile_settings (reader_id, public, updated_at) VALUES (:r, :p, :t)", r=reader["id"], p=public, t=db.now_iso())
    return jsonify(ok=True, public=bool(public))


def delete_reader_data(reader_id):
    _tables()
    db.execute("DELETE FROM walk_history WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM reader_cities WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM profile_settings WHERE reader_id = :r", r=reader_id)
