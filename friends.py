"""
Friends: everyone has an @handle; you find people by handle or name, send a request,
and they accept. Friends can plan group maps together (group_maps.py).

  GET  /account/friends                the Friends page (search, requests, friends, invite link + QR)
  GET  /u/<handle>                     someone's invite page: "Add Danny" (the link and QR point here)
  GET  /u/<handle>/qr.svg              the QR code for that page
  GET  /api/friends                    {me, friends, incoming, outgoing}
  GET  /api/friends/search?q=          up to 12 people, by @handle or name
  GET  /api/friends/handle-check?h=    is this handle free?
  POST /api/friends/handle    {handle}             change your handle
  POST /api/friends/request   {handle}             send a request (accepts theirs if they asked you first)
  POST /api/friends/respond   {handle, accept}     accept or decline a request
  POST /api/friends/remove    {handle}             unfriend, or take back a request
  POST /api/friends/block     {handle}             block (same reader_blocks as comments) and unfriend

People are only ever identified by handle in the browser -- never their id or email.
Blocks either way hide people from search, requests and friend lists.
Handles are made on first need from the display name (danny, danny2, ...) and can be changed.
Tables are created on first use.
"""
import hashlib
import io
import os
import re
import secrets
import unicodedata

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

import db
from content import CATEGORIES
from readers import current_reader, reader_required_json

friends_bp = Blueprint("friends", __name__)

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_.]{1,18}[a-z0-9_]$")   # 3-20 characters
RESERVED = {"admin", "administrator", "mansfield", "support", "help", "root", "me", "api", "walk", "walks", "account",
            "settings", "friends", "staff", "official", "team", "null", "undefined", "owner", "mod", "moderator"}
MAX_PENDING_OUT = 100
SEARCH_LIMIT = int(os.environ.get("FRIEND_SEARCH_LIMIT", "300"))   # per reader per day
_ready = False


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS reader_handles (
            reader_id   TEXT PRIMARY KEY,
            handle      TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS friendships (
            a_id          TEXT NOT NULL,
            b_id          TEXT NOT NULL,
            status        TEXT NOT NULL,
            requested_by  TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            PRIMARY KEY (a_id, b_id)
        )""")
    _ready = True


def _pair(x, y):
    return (x, y) if x < y else (y, x)


# ── Handles ──────────────────────────────────────────────────────────────

def clean_handle(raw):
    h = str(raw or "").strip().lstrip("@").lower()
    return h if HANDLE_RE.match(h) and h not in RESERVED and ".." not in h else ""


def _base_handle(name):
    ascii_name = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]", "", ascii_name.lower())[:15]
    if len(base) < 3 or base in RESERVED:
        base = (base + "walker")[:15]
    return base


def handle_of(reader_id):
    _tables()
    row = db.query_one("SELECT handle FROM reader_handles WHERE reader_id = :r", r=reader_id)
    return row["handle"] if row else None


def ensure_handle(reader_id, name=None):
    """The reader's handle, made from their name the first time it's needed."""
    have = handle_of(reader_id)
    if have:
        return have
    if name is None:
        row = db.query_one("SELECT display_name FROM readers WHERE id = :r", r=reader_id)
        name = (row or {}).get("display_name") or ""
    base = _base_handle(name)
    tries = [base] + [f"{base}{n}" for n in range(2, 40)] + [base + secrets.token_hex(3) for _ in range(5)]
    now = db.now_iso()
    for h in tries:
        if not clean_handle(h):
            continue
        try:
            db.execute("INSERT INTO reader_handles (reader_id, handle, created_at, updated_at) VALUES (:r, :h, :t, :t)",
                       r=reader_id, h=h, t=now)
            return h
        except IntegrityError:
            again = handle_of(reader_id)   # made by a request running at the same time
            if again:
                return again
    raise RuntimeError("could not make a handle")


def handle_free(handle, reader_id=None):
    row = db.query_one("SELECT reader_id FROM reader_handles WHERE handle = :h", h=handle)
    return not row or row["reader_id"] == reader_id


def reader_by_handle(handle):
    h = clean_handle(handle)
    if not h:
        return None
    _tables()
    return db.query_one("SELECT r.id, r.display_name, h.handle FROM reader_handles h JOIN readers r ON r.id = h.reader_id "
                        "WHERE h.handle = :h", h=h)


# ── Who's who ────────────────────────────────────────────────────────────

def blocked_between(x, y):
    return bool(db.query_one("SELECT 1 AS x FROM reader_blocks WHERE (reader_id = :x AND blocked_id = :y) "
                             "OR (reader_id = :y AND blocked_id = :x)", x=x, y=y))


def _blocked_ids(me):
    rows = db.query("SELECT blocked_id AS id FROM reader_blocks WHERE reader_id = :me "
                    "UNION SELECT reader_id AS id FROM reader_blocks WHERE blocked_id = :me", me=me)
    return {r["id"] for r in rows}


def _row(me, other):
    a, b = _pair(me, other)
    return db.query_one("SELECT status, requested_by FROM friendships WHERE a_id = :a AND b_id = :b", a=a, b=b)


def state_with(me, other):
    """'self', 'friends', 'sent' (I asked), 'received' (they asked) or 'none'."""
    if me == other:
        return "self"
    row = _row(me, other)
    if not row:
        return "none"
    if row["status"] == "accepted":
        return "friends"
    return "sent" if row["requested_by"] == me else "received"


def are_friends(x, y):
    _tables()
    return state_with(x, y) == "friends" and not blocked_between(x, y)


def friend_ids(me):
    _tables()
    rows = db.query("SELECT a_id, b_id FROM friendships WHERE status = 'accepted' AND (a_id = :me OR b_id = :me)", me=me)
    blocked = _blocked_ids(me)
    return [i for i in (r["b_id"] if r["a_id"] == me else r["a_id"] for r in rows) if i not in blocked]


def person(reader_id, name, handle, state=None):
    name = (name or "").strip() or "Walker"
    out = {"handle": handle, "name": name, "initial": name[:1].upper()}
    if state:
        out["state"] = state
    return out


def _people(ids, state=None):
    ids = list(ids)
    if not ids:
        return []
    for i in ids:   # everyone listed needs a handle to be addressed by
        ensure_handle(i)
    params = {f"i{n}": v for n, v in enumerate(ids)}
    rows = db.query("SELECT r.id, r.display_name, h.handle FROM readers r JOIN reader_handles h ON h.reader_id = r.id "
                    "WHERE r.id IN (" + ", ".join(":" + k for k in params) + ")", **params)
    out = [person(r["id"], r["display_name"], r["handle"], state) for r in rows]
    out.sort(key=lambda p: p["name"].lower())
    return out


def overview(me):
    _tables()
    blocked = _blocked_ids(me)
    rows = db.query("SELECT a_id, b_id, status, requested_by FROM friendships WHERE a_id = :me OR b_id = :me", me=me)
    friends, incoming, outgoing = [], [], []
    for r in rows:
        other = r["b_id"] if r["a_id"] == me else r["a_id"]
        if other in blocked:
            continue
        if r["status"] == "accepted":
            friends.append(other)
        elif r["requested_by"] == me:
            outgoing.append(other)
        else:
            incoming.append(other)
    return {"friends": _people(friends, "friends"), "incoming": _people(incoming, "received"),
            "outgoing": _people(outgoing, "sent")}


def incoming_count(me):
    """Requests waiting for this reader (the dot on Account). Never fails."""
    try:
        _tables()
        blocked = _blocked_ids(me)
        rows = db.query("SELECT a_id, b_id FROM friendships WHERE status = 'pending' AND requested_by != :me "
                        "AND (a_id = :me OR b_id = :me)", me=me)
        return sum(1 for r in rows if (r["b_id"] if r["a_id"] == me else r["a_id"]) not in blocked)
    except Exception as exc:  # noqa: BLE001
        print("friends: could not count requests:", exc)
        return 0


def _me_card(reader):
    return {"handle": ensure_handle(reader["id"], reader["display_name"]), "name": reader["display_name"],
            "initial": (reader["display_name"] or "?")[:1].upper()}


def _verify(reader, what):
    from onboarding import verify_block   # adding friends needs a verified email (onboarding.py)
    return verify_block(reader, what)


def _limited(reader_id):
    try:
        import ask as ask_limits
        key = "fs:" + hashlib.sha256(reader_id.encode()).hexdigest()[:16]
        if ask_limits._used(key) >= SEARCH_LIMIT:
            return True
        ask_limits._count(key)
    except Exception as exc:  # noqa: BLE001
        print("friends: usage counter unavailable:", exc)
    return False


# ── Pages ────────────────────────────────────────────────────────────────

def qr_svg(url):
    """An inline SVG QR code for the URL, or None if segno isn't installed."""
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=1, border=2, dark="#15171a", light="#ffffff",
                                    xmldecl=False, svgns=True, omitsize=True)
    return buf.getvalue().decode()


def _site():
    return (os.environ.get("SITE_URL") or request.host_url).rstrip("/")


@friends_bp.get("/account/friends")
def page():
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account/friends"))
    me = _me_card(reader)
    try:
        from group_maps import maps_for
        maps = maps_for(reader["id"])
    except Exception as exc:  # noqa: BLE001
        print("friends: could not load group maps:", exc)
        maps = []
    return render_template("account/friends.html", me=me, data=overview(reader["id"]), maps=maps,
                           invite_url=_site() + "/u/" + me["handle"], has_qr=qr_svg("x") is not None,
                           categories=CATEGORIES, active_category=None)


@friends_bp.get("/u/<handle>")
def invite_page(handle):
    target = reader_by_handle(handle)
    reader = current_reader()
    if not target or (reader and blocked_between(reader["id"], target["id"])):
        return render_template("account/friend_invite.html", target=None, state=None,
                               categories=CATEGORIES, active_category=None), 404
    state = state_with(reader["id"], target["id"]) if reader else None
    return render_template("account/friend_invite.html", state=state,
                           target=person(target["id"], target["display_name"], target["handle"]),
                           categories=CATEGORIES, active_category=None)


@friends_bp.get("/u/<handle>/qr.svg")
def invite_qr(handle):
    target = reader_by_handle(handle)
    svg = qr_svg(_site() + "/u/" + target["handle"]) if target else None
    if not svg:
        abort(404)
    return svg, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=3600"}


# ── API ──────────────────────────────────────────────────────────────────

def _json_reader():
    reader = current_reader()
    if not reader:
        return None, (jsonify(error="sign_in_required"), 401)
    return reader, None


@friends_bp.get("/api/friends")
def api_overview():
    reader, err = _json_reader()
    if err:
        return err
    out = overview(reader["id"])
    out["me"] = _me_card(reader)
    reply = jsonify(out)
    reply.headers["Cache-Control"] = "no-store"
    return reply


def _like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@friends_bp.get("/api/friends/search")
def api_search():
    reader, err = _json_reader()
    if err:
        return err
    q = " ".join(str(request.args.get("q") or "").split())[:40]
    bare = q.lstrip("@").lower()
    if len(bare) < 2:
        return jsonify(people=[])
    if _limited(reader["id"]):
        return jsonify(people=[], error="Too many searches today. Try again tomorrow."), 429
    _tables()
    me = reader["id"]
    rows = db.query(
        "SELECT r.id, r.display_name, h.handle FROM readers r LEFT JOIN reader_handles h ON h.reader_id = r.id "
        "WHERE r.id != :me AND (h.handle LIKE :pre ESCAPE '\\' OR LOWER(r.display_name) LIKE :any ESCAPE '\\') "
        "ORDER BY r.display_name LIMIT 40",
        me=me, pre=_like(bare) + "%", any="%" + _like(bare) + "%")
    blocked = _blocked_ids(me)
    found = []
    for r in rows:
        if r["id"] in blocked:
            continue
        handle = r["handle"] or ensure_handle(r["id"], r["display_name"])
        rank = 0 if handle == bare else 1 if handle.startswith(bare) else 2 if (r["display_name"] or "").lower().startswith(bare) else 3
        found.append((rank, (r["display_name"] or "").lower(), person(r["id"], r["display_name"], handle, state_with(me, r["id"]))))
    found.sort(key=lambda x: (x[0], x[1]))
    return jsonify(people=[p for _, _, p in found[:12]])


@friends_bp.get("/api/friends/handle-check")
def api_handle_check():
    reader, err = _json_reader()
    if err:
        return err
    raw = str(request.args.get("h") or "").strip().lstrip("@").lower()
    h = clean_handle(raw)
    if not h:
        return jsonify(ok=False, error=_handle_error(raw))
    _tables()
    if not handle_free(h, reader["id"]):
        return jsonify(ok=False, error=f"@{h} is taken.")
    return jsonify(ok=True, handle=h)


def _handle_error(raw):
    if len(raw) < 3 or len(raw) > 20:
        return "Use 3 to 20 characters."
    if raw.lstrip("@").lower() in RESERVED:
        return "That one's reserved. Try another."
    return "Use letters, numbers, dots and underscores."


@friends_bp.post("/api/friends/handle")
@reader_required_json
def api_set_handle():
    reader = current_reader()
    raw = str((request.get_json(silent=True) or {}).get("handle") or "").strip().lstrip("@").lower()
    h = clean_handle(raw)
    if not h:
        return jsonify(error=_handle_error(raw)), 400
    _tables()
    current = ensure_handle(reader["id"], reader["display_name"])
    if h == current:
        return jsonify(ok=True, handle=h)
    try:
        db.execute("UPDATE reader_handles SET handle = :h, updated_at = :t WHERE reader_id = :r",
                   h=h, t=db.now_iso(), r=reader["id"])
    except IntegrityError:
        return jsonify(error=f"@{h} is taken."), 409
    return jsonify(ok=True, handle=h)


def _target(data, me):
    t = reader_by_handle(data.get("handle"))
    if not t or t["id"] == me or blocked_between(me, t["id"]):
        return None
    return t


@friends_bp.post("/api/friends/request")
@reader_required_json
def api_request():
    reader = current_reader()
    blocked = _verify(reader, "add friends")
    if blocked:
        return blocked
    me = reader["id"]
    t = _target(request.get_json(silent=True) or {}, me)
    if not t:
        return jsonify(error="Couldn’t find that person."), 404
    _tables()
    state = state_with(me, t["id"])
    if state in ("friends", "sent"):
        return jsonify(ok=True, state=state)
    a, b = _pair(me, t["id"])
    now = db.now_iso()
    if state == "received":   # they asked first: this accepts it
        db.execute("UPDATE friendships SET status = 'accepted', updated_at = :t WHERE a_id = :a AND b_id = :b", t=now, a=a, b=b)
        return jsonify(ok=True, state="friends")
    pending = db.query_one("SELECT COUNT(*) AS n FROM friendships WHERE status = 'pending' AND requested_by = :me", me=me)
    if int((pending or {}).get("n", 0)) >= MAX_PENDING_OUT:
        return jsonify(error="You have a lot of requests waiting. Take some back first."), 400
    try:
        db.execute("INSERT INTO friendships (a_id, b_id, status, requested_by, created_at, updated_at) "
                   "VALUES (:a, :b, 'pending', :me, :t, :t)", a=a, b=b, me=me, t=now)
    except IntegrityError:
        pass   # sent twice at once
    return jsonify(ok=True, state=state_with(me, t["id"]))


@friends_bp.post("/api/friends/respond")
@reader_required_json
def api_respond():
    reader = current_reader()
    data = request.get_json(silent=True) or {}
    me = reader["id"]
    t = _target(data, me)
    if not t or state_with(me, t["id"]) != "received":
        return jsonify(error="That request isn’t there any more."), 404
    a, b = _pair(me, t["id"])
    if data.get("accept"):
        blocked = _verify(reader, "add friends")
        if blocked:
            return blocked
        db.execute("UPDATE friendships SET status = 'accepted', updated_at = :t WHERE a_id = :a AND b_id = :b",
                   t=db.now_iso(), a=a, b=b)
        return jsonify(ok=True, state="friends")
    db.execute("DELETE FROM friendships WHERE a_id = :a AND b_id = :b", a=a, b=b)
    return jsonify(ok=True, state="none")


@friends_bp.post("/api/friends/remove")
@reader_required_json
def api_remove():
    reader = current_reader()
    t = reader_by_handle((request.get_json(silent=True) or {}).get("handle"))
    if t and t["id"] != reader["id"]:
        _tables()
        a, b = _pair(reader["id"], t["id"])
        db.execute("DELETE FROM friendships WHERE a_id = :a AND b_id = :b", a=a, b=b)
    return jsonify(ok=True, state="none")


@friends_bp.post("/api/friends/block")
@reader_required_json
def api_block():
    reader = current_reader()
    t = reader_by_handle((request.get_json(silent=True) or {}).get("handle"))
    if not t or t["id"] == reader["id"]:
        return jsonify(error="Couldn’t find that person."), 404
    _tables()
    a, b = _pair(reader["id"], t["id"])
    db.execute("DELETE FROM friendships WHERE a_id = :a AND b_id = :b", a=a, b=b)
    try:
        db.execute("INSERT INTO reader_blocks (reader_id, blocked_id, created_at) VALUES (:r, :b, :t)",
                   r=reader["id"], b=t["id"], t=db.now_iso())
    except IntegrityError:
        pass
    return jsonify(ok=True, state="blocked")


def delete_reader_data(reader_id):
    _tables()
    db.execute("DELETE FROM friendships WHERE a_id = :r OR b_id = :r", r=reader_id)
    db.execute("DELETE FROM reader_handles WHERE reader_id = :r", r=reader_id)


@friends_bp.app_template_global()
def my_handle():
    """The signed-in reader's @handle for templates ('' when signed out). Never fails."""
    reader = current_reader()
    if not reader:
        return ""
    try:
        return ensure_handle(reader["id"], reader["display_name"])
    except Exception as exc:  # noqa: BLE001
        print("friends: no handle:", exc)
        return ""


@friends_bp.app_context_processor
def _friend_alerts():
    reader = current_reader()
    return {"friend_requests": incoming_count(reader["id"]) if reader else 0}
