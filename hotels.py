"""
Hotel links: a guest guide built on the walk map, one link per hotel.

  GET  /h/<slug>                     the walk page as that hotel's guide (walk.html with `hotel`):
                                     the map opens at the hotel, the Guide drawer leads with the hotel's
                                     favourite places and walks from the door, and guests can ask the
                                     AI planner without an account (a daily limit per phone).
  GET  /admin/hotels                 the owner's list: links, QR codes, guest numbers; add or edit a hotel
  POST /admin/hotels                 save a hotel (new or edited)
  POST /admin/hotels/demo            make the demo hotel, filled from the directory around it
  POST /admin/hotels/<id>/fill       fill a hotel's favourites and walks from the directory around it
  POST /admin/hotels/<id>/toggle     pause / switch on a hotel's link

Opening a hotel link marks the visit (session["hotel_guest"]) so walk_planner.py lets that phone plan
walks without signing in: HOTEL_GUEST_LIMIT walks a day per phone (default 10), HOTEL_DAILY_CAP per hotel
(default 300) and HOTEL_IP_LIMIT per network address (default 30), on top of the planner's own
WALK_DAILY_CAP. Saving and sharing walks still need an account. Guests' locations are never stored; only
counts are kept (hotel_stats: guests, opens and walks planned per day). Tables are created on first use.
"""
import hashlib
import json
import math
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

import db
from admin import owner_required
from readers import _csrf_ok, csrf_token

hotels_bp = Blueprint("hotels", __name__)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
_PLACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
MAX_PICKS = 12
MAX_WALKS = 6
MAX_STOPS = 6
DEMO = {"slug": "demo", "name": "Hotel Demo Wien", "address": "Wiedner Hauptstraße 12, 1040 Wien", "city": "Vienna",
        "lat": 48.1952, "lng": 16.3681,
        "welcome": "Our favourite places within a few minutes' walk, and walks that start right at our door. "
                   "Ask for anything nearby, in any language."}
_ready = False


def _int_env(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS hotels (
            id          TEXT PRIMARY KEY,
            slug        TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            address     TEXT,
            city        TEXT,
            lat         REAL NOT NULL,
            lng         REAL NOT NULL,
            welcome     TEXT,
            picks       TEXT NOT NULL DEFAULT '[]',
            walks       TEXT NOT NULL DEFAULT '[]',
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS hotel_stats (
            hotel_id  TEXT NOT NULL,
            day       TEXT NOT NULL,
            kind      TEXT NOT NULL,
            n         INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (hotel_id, day, kind)
        )""")
    _ready = True


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _row(h):
    if not h:
        return None
    h = dict(h)
    for k in ("picks", "walks"):
        try:
            h[k] = json.loads(h.get(k) or "[]")
        except ValueError:
            h[k] = []
    h["active"] = bool(int(h.get("active") or 0))
    return h


def get_hotel(slug, active_only=True):
    _tables()
    h = _row(db.query_one("SELECT * FROM hotels WHERE slug = :s", s=str(slug or "")))
    return h if h and (h["active"] or not active_only) else None


def guest_hotel():
    """The hotel whose link this phone came in through (and is still switched on), or None."""
    slug = session.get("hotel_guest")
    if not slug:
        return None
    try:
        return get_hotel(slug)
    except Exception as exc:  # noqa: BLE001 -- never break the planner over this
        print("hotels: could not look up the guest's hotel:", exc)
        return None


def guest_key():
    """A random id for this phone's session (no account), used only for the daily limit."""
    if not session.get("guest_id"):
        session["guest_id"] = secrets.token_hex(8)
    return session["guest_id"]


def ip_key():
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    return hashlib.sha256(("hotel-ip:" + ip).encode()).hexdigest()[:16]


def bump(hotel_id, kind):
    """Count one guest event for today (guest, open, plan). Never raises."""
    try:
        _tables()
        day = _today()
        if not db.execute("UPDATE hotel_stats SET n = n + 1 WHERE hotel_id = :h AND day = :d AND kind = :k", h=hotel_id, d=day, k=kind):
            try:
                db.execute("INSERT INTO hotel_stats (hotel_id, day, kind, n) VALUES (:h, :d, :k, 1)", h=hotel_id, d=day, k=kind)
            except IntegrityError:
                db.execute("UPDATE hotel_stats SET n = n + 1 WHERE hotel_id = :h AND day = :d AND kind = :k", h=hotel_id, d=day, k=kind)
    except Exception as exc:  # noqa: BLE001
        print("hotels: could not count:", exc)


def stats(hotel_id, days=30):
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    out = {"guest": 0, "open": 0, "plan": 0}
    for r in db.query("SELECT kind, SUM(n) AS n FROM hotel_stats WHERE hotel_id = :h AND day >= :d GROUP BY kind", h=hotel_id, d=since):
        out[r["kind"]] = int(r["n"] or 0)
    return out


def _metres(a_lat, a_lng, b_lat, b_lng):
    dx = (b_lng - a_lng) * 111320 * math.cos(math.radians((a_lat + b_lat) / 2))
    dy = (b_lat - a_lat) * 110540
    return math.hypot(dx, dy)


def _walk_min(m):
    return max(1, round(m * 1.3 / 80))   # streets aren't straight: about 1.3x the straight line at 80 m a minute


def guide(hotel):
    """What the page shows for a hotel: its favourites with walking minutes, and its walks with links."""
    home = "/h/" + hotel["slug"]
    picks = []
    for p in hotel["picks"]:
        if p.get("lat") is None:
            continue
        mins = _walk_min(_metres(hotel["lat"], hotel["lng"], p["lat"], p["lng"]))
        picks.append({**p, "mins": mins,
                      "link": home + "?" + urlencode({"go": p["slug"], "t": p.get("name") or ""})})
    walks = []
    for w in hotel["walks"]:
        stops = w.get("stops") or []
        if len(stops) < 2:
            continue
        walks.append({"title": w.get("title") or "A walk from the door", "stops": stops,
                      "link": home + "?" + urlencode({"s": ",".join(s["slug"] for s in stops), "t": w.get("title") or ""})})
    return {"slug": hotel["slug"], "name": hotel["name"], "address": hotel.get("address") or "",
            "city": hotel.get("city") or "", "lat": hotel["lat"], "lng": hotel["lng"],
            "welcome": hotel.get("welcome") or "", "home": home, "picks": picks, "walks": walks}


# ── The guest page ───────────────────────────────────────────────────────

@hotels_bp.get("/h/<slug>")
def hotel_page(slug):
    hotel = get_hotel(slug)
    if not hotel:
        abort(404)
    session.permanent = True
    session["hotel_guest"] = hotel["slug"]
    guest_key()
    seen = session.get("hotel_seen") or ""
    if seen != hotel["slug"] + ":" + _today():   # one "guest" a day per phone, every open counted too
        session["hotel_seen"] = hotel["slug"] + ":" + _today()
        bump(hotel["id"], "guest")
    bump(hotel["id"], "open")
    from walk_planner import render_walk
    return render_walk(hotel=guide(hotel))


# ── Filling a hotel from the directory ───────────────────────────────────

def _near(lat, lng, **params):
    import requests
    import walk_planner as wp
    base, key = wp.directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    if not base or not key:
        raise ValueError("The directory isn't connected (DIRECTORY_URL / WALK_PLAN_KEY).")
    q = {"lat": lat, "lng": lng, "radius": params.pop("radius", 900), "limit": params.pop("limit", 40), **params}
    r = requests.get(base + "/embed/places-near?" + urlencode(q), headers={"X-Walk-Key": key}, timeout=20)
    data = r.json() if r.status_code < 300 else {}
    return [p for p in (data.get("places") or []) if isinstance(p, dict) and _PLACE.match(str(p.get("slug") or ""))]


def _stop(p):
    return {"slug": p["slug"], "name": p.get("name") or "", "category": p.get("category") or "",
            "lat": float(p["lat"]), "lng": float(p["lng"])}


def _in_walking_order(lat, lng, stops):
    """Nearest first from the door, then each next stop the nearest to the last."""
    left, out, at = list(stops), [], (lat, lng)
    while left:
        nxt = min(left, key=lambda s: _metres(at[0], at[1], s["lat"], s["lng"]))
        out.append(nxt)
        left.remove(nxt)
        at = (nxt["lat"], nxt["lng"])
    return out


def fill_from_directory(hotel):
    """Favourites: the best-rated places within about 10 minutes, a mix of kinds. Walks: coffee,
    food & drink and sights from the door, where there are enough of each."""
    lat, lng = hotel["lat"], hotel["lng"]
    places = _near(lat, lng, radius=900, min_rating=4.3, limit=60)
    places.sort(key=lambda p: (-(p.get("rating") or 0), p.get("distance") or 0))
    picks, per_group = [], {}
    for p in places:
        g = p.get("group") or "other"
        if g == "hotels" or per_group.get(g, 0) >= 3:
            continue
        per_group[g] = per_group.get(g, 0) + 1
        picks.append(_stop(p))
        if len(picks) >= 8:
            break
    walks = []
    for title, groups in (("Coffee near the hotel", "coffee"), ("Food & drink nearby", "food,bars,wine"),
                          ("Sights on foot", "sights")):
        found = _near(lat, lng, radius=1200, groups=groups, min_rating=4.2, limit=20)
        found.sort(key=lambda p: (-(p.get("rating") or 0), p.get("distance") or 0))
        stops = [_stop(p) for p in found[:3]]
        if len(stops) >= 2:
            walks.append({"title": title, "stops": _in_walking_order(lat, lng, stops)})
    return picks, walks


# ── Admin ────────────────────────────────────────────────────────────────

def _site():
    return (os.environ.get("SITE_URL") or request.host_url).rstrip("/")


@hotels_bp.get("/admin/hotels")
@owner_required
def admin_list():
    _tables()
    import friends
    hotels = [_row(h) for h in db.query("SELECT * FROM hotels ORDER BY created_at DESC")]
    for h in hotels:
        h["url"] = _site() + "/h/" + h["slug"]
        h["qr"] = friends.qr_svg(h["url"])
        h["stats"] = stats(h["id"])
        h["picks_text"] = "\n".join(p["slug"] + (" | " + p["note"] if p.get("note") else "") for p in h["picks"])
        h["walks_text"] = "\n".join(w["title"] + ": " + ", ".join(s["slug"] for s in w["stops"]) for w in h["walks"])
    edit = next((h for h in hotels if h["id"] == request.args.get("edit")), None)
    return render_template("admin/hotels.html", hotels=hotels, edit=edit, csrf=csrf_token(),
                           guest_limit=_int_env("HOTEL_GUEST_LIMIT", 10))


def _lookup(slugs):
    """{slug: place} from the directory for these listing slugs."""
    if not slugs:
        return {}
    from community import _directory_places
    return _directory_places(slugs)


@hotels_bp.post("/admin/hotels")
@owner_required
def admin_save():
    _tables()
    if not _csrf_ok():
        abort(403)
    f = request.form
    hid = (f.get("id") or "").strip()
    name = " ".join(str(f.get("name") or "").split())[:120]
    slug = str(f.get("slug") or "").strip().lower()
    try:
        lat, lng = float(f.get("lat")), float(f.get("lng"))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError
    except (TypeError, ValueError):
        flash("The hotel needs its map position (latitude and longitude).", "warning")
        return redirect(url_for("hotels.admin_list", edit=hid or None))
    if not name or not _SLUG.match(slug):
        flash("Give the hotel a name and a link name (lowercase letters, numbers and dashes).", "warning")
        return redirect(url_for("hotels.admin_list", edit=hid or None))
    # Favourites: one listing slug per line, optionally "slug | a short note".
    pick_lines = [ln.split("|", 1) for ln in str(f.get("picks") or "").splitlines() if ln.strip()][:MAX_PICKS]
    pick_slugs = [p[0].strip() for p in pick_lines if _PLACE.match(p[0].strip())]
    notes = {p[0].strip(): " ".join(p[1].split())[:140] for p in pick_lines if len(p) > 1}
    # Walks: one per line, "Title: slug, slug, slug".
    walk_lines = []
    for ln in str(f.get("walks") or "").splitlines():
        if ":" not in ln:
            continue
        title, rest = ln.split(":", 1)
        slugs = [s.strip() for s in rest.split(",") if _PLACE.match(s.strip())][:MAX_STOPS]
        if title.strip() and len(slugs) >= 2:
            walk_lines.append((" ".join(title.split())[:80], slugs))
    walk_lines = walk_lines[:MAX_WALKS]
    try:
        found = _lookup(list(dict.fromkeys(pick_slugs + [s for _, ss in walk_lines for s in ss])))
    except Exception as exc:  # noqa: BLE001
        flash(f"Couldn't check the places with the directory: {exc}", "warning")
        return redirect(url_for("hotels.admin_list", edit=hid or None))
    missing = [s for s in dict.fromkeys(pick_slugs + [s for _, ss in walk_lines for s in ss]) if s not in found]
    picks = [{**_stop(found[s]), **({"note": notes[s]} if notes.get(s) else {})} for s in pick_slugs if s in found]
    walks = [{"title": t, "stops": [_stop(found[s]) for s in ss if s in found]} for t, ss in walk_lines]
    walks = [w for w in walks if len(w["stops"]) >= 2]
    row = {"name": name, "slug": slug, "address": " ".join(str(f.get("address") or "").split())[:200],
           "city": " ".join(str(f.get("city") or "").split())[:80], "lat": lat, "lng": lng,
           "welcome": str(f.get("welcome") or "").strip()[:400], "picks": json.dumps(picks), "walks": json.dumps(walks),
           "now": _now()}
    try:
        if hid:
            db.execute("UPDATE hotels SET name=:name, slug=:slug, address=:address, city=:city, lat=:lat, lng=:lng, "
                       "welcome=:welcome, picks=:picks, walks=:walks, updated_at=:now WHERE id=:id", id=hid, **row)
        else:
            hid = secrets.token_hex(8)
            db.execute("INSERT INTO hotels (id, slug, name, address, city, lat, lng, welcome, picks, walks, active, created_at, updated_at) "
                       "VALUES (:id, :slug, :name, :address, :city, :lat, :lng, :welcome, :picks, :walks, 1, :now, :now)", id=hid, **row)
    except IntegrityError:
        flash("Another hotel already uses that link name. Pick a different one.", "warning")
        return redirect(url_for("hotels.admin_list", edit=hid or None))
    flash("Saved." + (" These weren't found in the directory and were left out: " + ", ".join(missing) if missing else ""),
          "warning" if missing else "success")
    return redirect(url_for("hotels.admin_list"))


@hotels_bp.post("/admin/hotels/demo")
@owner_required
def admin_demo():
    _tables()
    if not _csrf_ok():
        abort(403)
    if db.query_one("SELECT 1 AS x FROM hotels WHERE slug = :s", s=DEMO["slug"]):
        flash("The demo hotel is already there.", "warning")
        return redirect(url_for("hotels.admin_list"))
    try:
        picks, walks = fill_from_directory(DEMO)
    except Exception as exc:  # noqa: BLE001
        print("hotels: demo fill failed:", exc)
        picks, walks = [], []
        flash("The demo hotel was made, but its places couldn't be filled from the directory. Try 'Fill from the directory'.", "warning")
    now = _now()
    db.execute("INSERT INTO hotels (id, slug, name, address, city, lat, lng, welcome, picks, walks, active, created_at, updated_at) "
               "VALUES (:id, :slug, :name, :address, :city, :lat, :lng, :welcome, :picks, :walks, 1, :now, :now)",
               id=secrets.token_hex(8), slug=DEMO["slug"], name=DEMO["name"], address=DEMO["address"], city=DEMO["city"],
               lat=DEMO["lat"], lng=DEMO["lng"], welcome=DEMO["welcome"], picks=json.dumps(picks), walks=json.dumps(walks), now=now)
    if picks:
        flash(f"Demo hotel ready: {len(picks)} favourites and {len(walks)} walks from the door.", "success")
    return redirect(url_for("hotels.admin_list"))


@hotels_bp.post("/admin/hotels/<hid>/fill")
@owner_required
def admin_fill(hid):
    _tables()
    if not _csrf_ok():
        abort(403)
    h = _row(db.query_one("SELECT * FROM hotels WHERE id = :id", id=hid))
    if not h:
        abort(404)
    try:
        picks, walks = fill_from_directory(h)
    except Exception as exc:  # noqa: BLE001
        flash(f"Couldn't reach the directory: {exc}", "warning")
        return redirect(url_for("hotels.admin_list"))
    db.execute("UPDATE hotels SET picks = :p, walks = :w, updated_at = :now WHERE id = :id",
               p=json.dumps(picks), w=json.dumps(walks), now=_now(), id=hid)
    flash(f"Filled from the directory: {len(picks)} favourites and {len(walks)} walks. Edit them below if you like.", "success")
    return redirect(url_for("hotels.admin_list", edit=hid))


@hotels_bp.post("/admin/hotels/<hid>/toggle")
@owner_required
def admin_toggle(hid):
    _tables()
    if not _csrf_ok():
        abort(403)
    db.execute("UPDATE hotels SET active = 1 - active, updated_at = :now WHERE id = :id", now=_now(), id=hid)
    return redirect(url_for("hotels.admin_list"))
