"""
Group maps: friends plan an outing together. One person starts a map and adds friends;
everyone on it can add, remove and reorder stops, rename it, and add their own friends.
Changes save straight away for the whole group; the walk page checks every few seconds
while a map is open, so you see each other's changes.

  GET    /api/group-maps                         your maps
  GET    /api/group-maps/<id>?rev=N              one map ({same: true} if it hasn't changed since rev N)
  POST   /api/group-maps                         {title, members: [handles], stops?: [{slug, name, category}]}
  POST   /api/group-maps/<id>                    {title}                          rename
  POST   /api/group-maps/<id>/stops              {slug, name, category}           add a stop at the end
  DELETE /api/group-maps/<id>/stops/<slug>                                         take a stop out
  POST   /api/group-maps/<id>/order              {slugs: [...]}                   new order
  POST   /api/group-maps/<id>/members            {handles: [...]}                 add your friends
  DELETE /api/group-maps/<id>/members/<handle>   the owner removes someone; your own handle = leave
  DELETE /api/group-maps/<id>                    the owner deletes it
  GET    /api/places-at?s=slug,slug              where those places are (the little map that fills in as you add stops)

Stops are directory listing slugs, like My routes (readers.py); a place found on Google is
imported first by the page (/api/place-import), then added like any other.
Only members can see or change a map. "rev" goes up by one with every change.
"""
import re

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

import db
import push
import friends as fr
from readers import current_reader, reader_required_json

group_bp = Blueprint("group_maps", __name__)

MAX_STOPS = 15
MAX_MEMBERS = 12
MAX_MAPS = 50
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_ready = False


def _tables():
    global _ready
    if _ready:
        return
    fr._tables()
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_maps (
            id          TEXT PRIMARY KEY,
            owner_id    TEXT NOT NULL,
            title       TEXT NOT NULL,
            rev         INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_map_members (
            map_id      TEXT NOT NULL,
            reader_id   TEXT NOT NULL,
            added_by    TEXT,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (map_id, reader_id)
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_map_stops (
            map_id      TEXT NOT NULL,
            slug        TEXT NOT NULL,
            name        TEXT NOT NULL,
            category    TEXT,
            position    INTEGER NOT NULL,
            added_by    TEXT,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (map_id, slug)
        )""")
    _ready = True


def _text(v, n):
    return " ".join(str(v or "").split())[:n]


def _bump(map_id):
    db.execute("UPDATE group_maps SET rev = rev + 1, updated_at = :t WHERE id = :id", t=db.now_iso(), id=map_id)


def _member(map_id, reader_id):
    return bool(db.query_one("SELECT 1 AS x FROM group_map_members WHERE map_id = :m AND reader_id = :r", m=map_id, r=reader_id))


def _load(map_id, me):
    """The map as the page sees it, or None if it doesn't exist or `me` isn't on it."""
    _tables()
    m = db.query_one("SELECT id, owner_id, title, rev, updated_at FROM group_maps WHERE id = :id", id=map_id)
    if not m or not _member(map_id, me):
        return None
    mem = db.query("SELECT g.reader_id, g.created_at, r.display_name FROM group_map_members g JOIN readers r ON r.id = g.reader_id "
                   "WHERE g.map_id = :m ORDER BY g.created_at, g.reader_id", m=map_id)
    names = {x["reader_id"]: (x["display_name"] or "Walker") for x in mem}
    members = []
    for x in mem:
        p = fr.person(x["reader_id"], x["display_name"], fr.ensure_handle(x["reader_id"], x["display_name"]))
        p["me"] = x["reader_id"] == me
        p["owner"] = x["reader_id"] == m["owner_id"]
        members.append(p)
    members.sort(key=lambda p: (not p["owner"], not p["me"], p["name"].lower()))
    stops = []
    for s in db.query("SELECT slug, name, category, added_by FROM group_map_stops WHERE map_id = :m ORDER BY position, created_at", m=map_id):
        by = s["added_by"]
        stops.append({"slug": s["slug"], "name": s["name"], "category": s["category"] or "",
                      "by": "you" if by == me else (names.get(by) or "someone").split(" ")[0]})
    return {"id": m["id"], "title": m["title"], "rev": int(m["rev"]), "updated_at": m["updated_at"],
            "owner": m["owner_id"] == me, "members": members, "stops": stops}


def maps_for(me):
    _tables()
    rows = db.query("SELECT m.id FROM group_maps m JOIN group_map_members g ON g.map_id = m.id "
                    "WHERE g.reader_id = :me ORDER BY m.updated_at DESC", me=me)
    return [x for x in (_load(r["id"], me) for r in rows) if x]


def _clean_stop(st):
    if not isinstance(st, dict):
        return None
    slug = str(st.get("slug") or "")
    if not _SLUG_RE.match(slug):
        return None
    return {"slug": slug, "name": _text(st.get("name"), 80) or slug, "category": _text(st.get("category"), 40)}


def _add_stop(map_id, st, by):
    """Adds at the end. False if the map is full; True if added or already there."""
    if db.query_one("SELECT 1 AS x FROM group_map_stops WHERE map_id = :m AND slug = :s", m=map_id, s=st["slug"]):
        return True
    row = db.query_one("SELECT COUNT(*) AS n, MAX(position) AS p FROM group_map_stops WHERE map_id = :m", m=map_id)
    if int((row or {}).get("n") or 0) >= MAX_STOPS:
        return False
    try:
        db.execute("INSERT INTO group_map_stops (map_id, slug, name, category, position, added_by, created_at) "
                   "VALUES (:m, :s, :n, :c, :p, :b, :t)", m=map_id, s=st["slug"], n=st["name"], c=st["category"],
                   p=int((row or {}).get("p") or 0) + 1, b=by, t=db.now_iso())
    except IntegrityError:
        pass
    return True


def _add_members(map_id, adder, handles):
    """Adds the adder's friends from `handles`. Returns (added names, error or None)."""
    count = int((db.query_one("SELECT COUNT(*) AS n FROM group_map_members WHERE map_id = :m", m=map_id) or {}).get("n") or 0)
    added = []
    info = db.query_one("SELECT g.title, r.display_name FROM group_maps g, readers r WHERE g.id = :m AND r.id = :b",
                        m=map_id, b=adder) or {}
    for h in list(dict.fromkeys(handles or []))[:MAX_MEMBERS]:
        t = fr.reader_by_handle(h)
        if not t or t["id"] == adder or _member(map_id, t["id"]):
            continue
        if not fr.are_friends(adder, t["id"]):
            return added, f"You can only add your friends. @{t['handle']} isn’t one yet."
        if count >= MAX_MEMBERS:
            return added, f"A map can have up to {MAX_MEMBERS} people."
        try:
            db.execute("INSERT INTO group_map_members (map_id, reader_id, added_by, created_at) VALUES (:m, :r, :b, :t)",
                       m=map_id, r=t["id"], b=adder, t=db.now_iso())
            count += 1
            added.append(t["display_name"])
            push.notify(t["id"], "You’re on a new map",
                        f"{info.get('display_name') or 'A friend'} added you to “{info.get('title') or 'a group map'}”.",
                        "/walk?group=" + map_id, key="group:" + map_id)
        except IntegrityError:
            pass
    return added, None


def _verify(reader, what):
    from onboarding import verify_block
    return verify_block(reader, what)


def _gone():
    return jsonify(error="That map isn’t there any more, or you’re no longer on it."), 404


# ── API ──────────────────────────────────────────────────────────────────

@group_bp.get("/api/group-maps")
def api_list():
    reader = current_reader()
    if not reader:
        return jsonify(signedIn=False, maps=[])
    reply = jsonify(signedIn=True, maps=maps_for(reader["id"]))
    reply.headers["Cache-Control"] = "no-store"
    return reply


@group_bp.get("/api/group-maps/<map_id>")
def api_one(map_id):
    reader = current_reader()
    if not reader:
        return jsonify(error="sign_in_required"), 401
    _tables()
    try:
        since = int(request.args.get("rev") or 0)
    except ValueError:
        since = 0
    if since:
        row = db.query_one("SELECT m.rev FROM group_maps m JOIN group_map_members g ON g.map_id = m.id "
                           "WHERE m.id = :id AND g.reader_id = :r", id=map_id, r=reader["id"])
        if row and int(row["rev"]) == since:
            reply = jsonify(same=True, rev=since)
            reply.headers["Cache-Control"] = "no-store"
            return reply
    m = _load(map_id, reader["id"])
    if not m:
        return _gone()
    reply = jsonify(map=m)
    reply.headers["Cache-Control"] = "no-store"
    return reply


@group_bp.post("/api/group-maps")
@reader_required_json
def api_create():
    reader = current_reader()
    blocked = _verify(reader, "plan maps with friends")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    title = _text(data.get("title"), 60)
    if not title:
        return jsonify(error="Give your map a name."), 400
    _tables()
    me = reader["id"]
    n = db.query_one("SELECT COUNT(*) AS n FROM group_maps WHERE owner_id = :me", me=me)
    if int((n or {}).get("n") or 0) >= MAX_MAPS:
        return jsonify(error=f"You can start up to {MAX_MAPS} group maps. Delete one first."), 400
    map_id, now = db.new_id(), db.now_iso()
    db.execute("INSERT INTO group_maps (id, owner_id, title, rev, created_at, updated_at) VALUES (:id, :o, :t, 1, :n, :n)",
               id=map_id, o=me, t=title, n=now)
    db.execute("INSERT INTO group_map_members (map_id, reader_id, added_by, created_at) VALUES (:m, :r, :r, :t)",
               m=map_id, r=me, t=now)
    for st in (data.get("stops") or [])[:MAX_STOPS]:
        st = _clean_stop(st)
        if st:
            _add_stop(map_id, st, me)
    _added, problem = _add_members(map_id, me, [str(h) for h in (data.get("members") or []) if isinstance(h, str)])
    out = _load(map_id, me)
    return jsonify(ok=True, map=out, warning=problem)


def _mine(map_id):
    """(reader, map row) for a member, else (reader, None)."""
    reader = current_reader()
    _tables()
    row = db.query_one("SELECT id, owner_id FROM group_maps WHERE id = :id", id=map_id)
    if not row or not _member(map_id, reader["id"]):
        return reader, None
    return reader, row


@group_bp.post("/api/group-maps/<map_id>")
@reader_required_json
def api_rename(map_id):
    reader, row = _mine(map_id)
    if not row:
        return _gone()
    title = _text((request.get_json(silent=True) or {}).get("title"), 60)
    if not title:
        return jsonify(error="Give your map a name."), 400
    db.execute("UPDATE group_maps SET title = :t WHERE id = :id", t=title, id=map_id)
    _bump(map_id)
    return jsonify(ok=True, map=_load(map_id, reader["id"]))


@group_bp.post("/api/group-maps/<map_id>/stops")
@reader_required_json
def api_add_stop(map_id):
    reader, row = _mine(map_id)
    if not row:
        return _gone()
    st = _clean_stop(request.get_json(silent=True) or {})
    if not st:
        return jsonify(error="That place can’t be added."), 400
    if not _add_stop(map_id, st, reader["id"]):
        return jsonify(error=f"A map can have up to {MAX_STOPS} stops."), 400
    _bump(map_id)
    return jsonify(ok=True, map=_load(map_id, reader["id"]))


@group_bp.delete("/api/group-maps/<map_id>/stops/<slug>")
@reader_required_json
def api_remove_stop(map_id, slug):
    reader, row = _mine(map_id)
    if not row:
        return _gone()
    if db.execute("DELETE FROM group_map_stops WHERE map_id = :m AND slug = :s", m=map_id, s=slug):
        _bump(map_id)
    return jsonify(ok=True, map=_load(map_id, reader["id"]))


@group_bp.post("/api/group-maps/<map_id>/order")
@reader_required_json
def api_order(map_id):
    reader, row = _mine(map_id)
    if not row:
        return _gone()
    wanted = [str(s) for s in ((request.get_json(silent=True) or {}).get("slugs") or []) if isinstance(s, str)]
    have = [r["slug"] for r in db.query("SELECT slug FROM group_map_stops WHERE map_id = :m ORDER BY position, created_at", m=map_id)]
    # The order they sent for the stops still there, then anything someone added meanwhile.
    order = [s for s in dict.fromkeys(wanted) if s in have] + [s for s in have if s not in wanted]
    for i, s in enumerate(order, 1):
        db.execute("UPDATE group_map_stops SET position = :p WHERE map_id = :m AND slug = :s", p=i, m=map_id, s=s)
    _bump(map_id)
    return jsonify(ok=True, map=_load(map_id, reader["id"]))


@group_bp.post("/api/group-maps/<map_id>/members")
@reader_required_json
def api_add_members(map_id):
    reader, row = _mine(map_id)
    if not row:
        return _gone()
    blocked = _verify(reader, "plan maps with friends")
    if blocked:
        return blocked
    handles = [str(h) for h in ((request.get_json(silent=True) or {}).get("handles") or []) if isinstance(h, str)]
    added, problem = _add_members(map_id, reader["id"], handles)
    if added:
        _bump(map_id)
    if problem and not added:
        return jsonify(error=problem), 400
    return jsonify(ok=True, map=_load(map_id, reader["id"]), warning=problem)


@group_bp.delete("/api/group-maps/<map_id>/members/<handle>")
@reader_required_json
def api_remove_member(map_id, handle):
    reader, row = _mine(map_id)
    if not row:
        return _gone()
    me = reader["id"]
    t = fr.reader_by_handle(handle)
    if not t or not _member(map_id, t["id"]):
        return jsonify(ok=True, map=_load(map_id, me))
    if t["id"] != me and row["owner_id"] != me:
        return jsonify(error="Only the person who started the map can remove people."), 403
    db.execute("DELETE FROM group_map_members WHERE map_id = :m AND reader_id = :r", m=map_id, r=t["id"])
    if t["id"] == row["owner_id"]:   # the owner left: the longest-standing member takes over, or it goes
        nxt = db.query_one("SELECT reader_id FROM group_map_members WHERE map_id = :m ORDER BY created_at, reader_id LIMIT 1", m=map_id)
        if not nxt:
            _delete(map_id)
            return jsonify(ok=True, left=True, deleted=True)
        db.execute("UPDATE group_maps SET owner_id = :o WHERE id = :id", o=nxt["reader_id"], id=map_id)
    _bump(map_id)
    if t["id"] == me:
        return jsonify(ok=True, left=True)
    return jsonify(ok=True, map=_load(map_id, me))


def _delete(map_id):
    db.execute("DELETE FROM group_map_stops WHERE map_id = :m", m=map_id)
    db.execute("DELETE FROM group_map_members WHERE map_id = :m", m=map_id)
    db.execute("DELETE FROM group_maps WHERE id = :m", m=map_id)


@group_bp.delete("/api/group-maps/<map_id>")
@reader_required_json
def api_delete(map_id):
    reader, row = _mine(map_id)
    if not row:
        return _gone()
    if row["owner_id"] != reader["id"]:
        return jsonify(error="Only the person who started the map can delete it. You can leave it instead."), 403
    _delete(map_id)
    return jsonify(ok=True, deleted=True)


_coords = {}   # slug -> {lat, lng, name} from the directory, for this process


@group_bp.get("/api/places-at")
def api_places_at():
    """Where some directory places are, for the little map that fills in as a group map or a route is built:
    ?s=slug,slug -> {places: {slug: {lat, lng, name}}}. Remembered while the server runs."""
    slugs = [x for x in dict.fromkeys(str(request.args.get("s") or "").split(",")) if _SLUG_RE.match(x)][:30]
    need = [x for x in slugs if x not in _coords]
    if need:
        try:
            from community import _directory_places
            found = _directory_places(need)
        except Exception as exc:  # noqa: BLE001 -- the directory is down: no pins this time
            print("group maps: could not look up places:", exc)
            found = None
        if found is not None:
            if len(_coords) > 5000:
                _coords.clear()
            for x in need:
                p = found.get(x)
                try:
                    _coords[x] = {"lat": float(p["lat"]), "lng": float(p["lng"]), "name": p.get("name") or x} if p else None
                except (TypeError, ValueError, KeyError):
                    _coords[x] = None
    return jsonify(places={x: _coords.get(x) for x in slugs if _coords.get(x)})


def delete_reader_data(reader_id):
    """On account deletion: leave every map (handing over or deleting ones they own)."""
    _tables()
    for r in db.query("SELECT map_id FROM group_map_members WHERE reader_id = :r", r=reader_id):
        m = r["map_id"]
        db.execute("DELETE FROM group_map_members WHERE map_id = :m AND reader_id = :r", m=m, r=reader_id)
        row = db.query_one("SELECT owner_id FROM group_maps WHERE id = :m", m=m)
        if row and row["owner_id"] == reader_id:
            nxt = db.query_one("SELECT reader_id FROM group_map_members WHERE map_id = :m ORDER BY created_at, reader_id LIMIT 1", m=m)
            if nxt:
                db.execute("UPDATE group_maps SET owner_id = :o WHERE id = :m", o=nxt["reader_id"], m=m)
            else:
                _delete(m)
    db.execute("UPDATE group_map_stops SET added_by = NULL WHERE added_by = :r", r=reader_id)
