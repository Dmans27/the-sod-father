"""
"Plan a walk" -- readers ask, by typing or speaking, for something like
"3 highly rated coffee shops near me", and get a walking route on the map
built from the places in the directory app.

  GET  /walk                    the page (static/js/walk-planner.js)
  POST /api/walk-plan           {query, lat?, lng?, view?, circle?, previous?} -> a walk
                                view   = what the reader's map shows {lat, lng, radius}
                                circle = an area they drew {lat, lng, radius}: the walk stays inside it
                                polygon = the shape they drew with a finger [[lng, lat], ...]
                                          (circle is then the circle around it, used for the search)
  POST /api/walk-plan/voice     a short voice recording -> its text

How a walk is made (the AI never picks places from memory):
  1. The model turns the request into search settings: where (near me, a
     named place or a city), kinds of place, words to look for, minimum
     rating, how many stops, how far.
  2. The directory app finds matching listings (/embed/places-near, which
     only answers this server: both services share WALK_PLAN_KEY).
  3. Code here picks the best of them and puts them in walking order.
  4. The model writes one short line per stop, using only the facts we
     give it, and the map (/embed/route-map) shows the route.

Only signed-in readers can plan walks, with a daily limit each; every plan
and voice note is counted in the ask_usage table.

Settings (Render -> Environment):
  WALK_PLAN_KEY        the same long random value in this app and the directory app (required)
  DIRECTORY_URL        the directory app's address (default: https:// + the first MAP_EMBED_HOSTS host)
  WALK_LIMIT_READER    walks a reader can plan per day (default 15)
  WALK_DAILY_CAP       walks everyone together can plan per day (default 400)
  TRANSCRIBE_MODEL     speech-to-text model (default gpt-4o-mini-transcribe, falls back to whisper-1)
"""
import hashlib
import json
import math
import os
import re
from urllib.parse import quote, urlencode

import requests
from flask import Blueprint, abort, jsonify, render_template, request, session

import db

import ask as ask_limits
import ask_engine
from content import CATEGORIES, map_embed_hosts
from readers import _csrf_ok, current_reader

walk_bp = Blueprint("walk", __name__)

GROUPS = {
    "food": "restaurants, food, lunch, dinner, pizza, brunch, bakeries",
    "coffee": "coffee shops and cafes",
    "bars": "bars and cocktail bars",
    "wine": "wine bars",
    "rooftops": "rooftop bars and terraces",
    "shops": "shops",
    "hotels": "hotels and places to stay",
    "sights": "museums, galleries, sights, parks, churches, landmarks",
    "other": "anything else",
}
GROUP_COLORS = {"food": "#E2574C", "coffee": "#C6935A", "bars": "#B368F7", "wine": "#B4436C", "rooftops": "#5AC8E8",
                "shops": "#F5B301", "hotels": "#5B7CFA", "sights": "#E8833A", "other": "#2f9e7e"}
MAX_STOPS = 8
CURATOR = (os.environ.get("CURATOR_NAME") or "Danny").strip()[:40] or "Danny"   # whose picks the directory is
MAX_QUERY = 300
MAX_AUDIO = 4 * 1024 * 1024
AUDIO_TYPES = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/m4a": "m4a",
               "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "video/mp4": "mp4"}
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")


class WalkError(Exception):
    """Something to tell the reader in plain words."""


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def directory_url():
    url = (os.environ.get("DIRECTORY_URL") or "").strip().rstrip("/")
    if url:
        return url
    hosts = map_embed_hosts()
    if not hosts:
        return ""
    host = hosts[0]
    scheme = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"
    return f"{scheme}://{host}"


def _metres(a, b):
    r, to_r = 6371000.0, math.pi / 180
    d_lat, d_lng = (b[1] - a[1]) * to_r, (b[0] - a[0]) * to_r
    h = math.sin(d_lat / 2) ** 2 + math.cos(a[1] * to_r) * math.cos(b[1] * to_r) * math.sin(d_lng / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


# ── Limits ───────────────────────────────────────────────────────────────

def _who():
    """(usage key, daily limit) for the signed-in reader, a hotel guest (hotels.py), or (None, 0)."""
    if session.get("is_admin"):
        return "walk:admin", _int_env("WALK_LIMIT_ADMIN", 200)
    reader = current_reader()
    if reader:
        return "walk:r:" + reader["id"], _int_env("WALK_LIMIT_READER", 15)
    import guests
    g = guests.current()
    if g:   # a guest account (guests.py): its free builds are checked in _check_and_count
        return "walk:g:" + g["id"], _int_env("GUEST_VOICE_LIMIT", 12)
    hotel = _guest_hotel()
    if hotel:
        import hotels
        return "walk:h:" + hotel["slug"] + ":" + hotels.guest_key(), _int_env("HOTEL_GUEST_LIMIT", 10)
    return None, 0


def _guest_hotel():
    """The hotel a phone without an account came in through (/h/<slug>), or None."""
    if not session.get("hotel_guest"):
        return None
    import hotels
    return hotels.guest_hotel()


def _check_and_count(kind=""):
    """Raise WalkError if the reader (or everyone together) is out of walks today, else count one."""
    key, limit = _who()
    if not key:
        raise WalkError("sign_in")
    if key.startswith("walk:g:"):   # guest accounts: only while free builds are left (counted after a finished plan)
        import guests
        ok, why = guests.can_build()
        if not ok:
            raise WalkError("guest_out" if why == "guest_out" else
                            "Lots of guests are planning walks from here today. Create a free account to keep going.")
        if kind != ":voice":
            return
    try:
        if ask_limits._used(key + kind) >= limit:
            raise WalkError(f"You've planned {limit} walks today. Try again tomorrow.")
        if key.startswith("walk:h:"):   # hotel guests: also a cap per hotel and per network address
            import hotels
            hotel_all, ip = "walk:h:" + key.split(":")[2] + ":all", "walk:hip:" + hotels.ip_key()
            if ask_limits._used(hotel_all) >= _int_env("HOTEL_DAILY_CAP", 300) or ask_limits._used(ip) >= _int_env("HOTEL_IP_LIMIT", 30):
                raise WalkError("The guest guide has had a busy day. Please try again tomorrow.")
            ask_limits._count(hotel_all)
            ask_limits._count(ip)
        if ask_limits._used("walk:all") >= _int_env("WALK_DAILY_CAP", 400):
            raise WalkError("The walk planner has had a busy day. Please try again tomorrow.")
        ask_limits._count(key + kind)
        ask_limits._count("walk:all")
    except WalkError:
        raise
    except Exception as exc:  # the counters live in the database; don't block on them
        print("walk: usage counter unavailable:", exc)


# ── 1. Understanding the request ─────────────────────────────────────────

PLAN_SYSTEM = """You turn a request for a walk into search settings for a place directory (restaurants, cafes, bars, shops, hotels, sights). You never name or invent places yourself.

Return JSON only, with exactly these keys:
{"action": "plan" or "clarify",
 "reply": "if clarify: one short, friendly question; if plan: one short sentence saying what you're looking for",
 "title": "a short title for the walk, like '3 top-rated coffee shops near you' (max 50 characters)",
 "near_me": true or false,
 "map_view": true or false,
 "place": "a named area, street or landmark to walk around, or ''",
 "city": "the city, or ''",
 "groups": ["one or more of: GROUPS"],
 "keywords": ["up to 4 specific words to look for, like 'matcha', 'pastries', 'vegan'; [] if none"],
 "min_rating": a number from 0 to 5,
 "count": how many stops (1 to 8),
 "radius_m": how far from the start, in metres (300 to 20000),
 "keep": [stop numbers from the previous walk to keep, if the reader is changing it],
 "drop": [stop numbers from the previous walk to remove]}

Rules:
- "highly rated", "top rated", "best", "great" -> min_rating 4.5; "good", "well rated" -> 4.2; otherwise 0.
- Use the number of stops they ask for. With none given, use 3 for one kind of place and 4 or 5 for a mixed walk or crawl.
- "near me", "nearby", "close to me", "where I am" -> near_me true.
- map_view is about the part of the map the reader is looking at. "this area", "here", "around here", "on the map", "in view", "this part of town", "what I'm looking at" -> map_view true (only when the reader's map view is available; otherwise treat "around here" as near me).
- With no place, city or "near me" named: map_view true if the reader's map view is available, otherwise near_me true.
- If the reader drew a circle on the map, the walk always stays inside it: don't ask where, and leave place and city empty unless they name one.
- radius_m: "near me"/"nearby" 1500; "within N minutes" -> N*80; a named area 1500; a whole city 4000.
- If near_me is true, no place or city is named, there's no circle and no map view, and the reader's location is NOT available, use action "clarify" and ask where the walk should be.
- If the request has nothing to do with places to go (for example a math question), use "clarify" and say you can plan walks to places in the directory.
- If there is a previous walk and the reader asks to change it ("swap the second one", "make it 5 stops", "add a bar at the end"), keep the earlier settings unless they change them, and fill keep/drop by stop number.
"""


def understand(query, has_location, previous, has_view=False, has_circle=False, about=""):
    groups = ", ".join(f"{k} ({v})" for k, v in GROUPS.items())
    lines = [f"Reader's request: {query}",
             f"Reader's location available: {'yes' if has_location else 'no'}",
             f"Reader's map view available: {'yes' if has_view else 'no'}",
             f"Reader drew a circle on the map: {'yes' if has_circle else 'no'}"]
    if about:   # what they told us when they signed up (onboarding.planner_note)
        lines.append(about)
    if previous:
        lines.append("Previous request: " + _text(previous.get("query"), MAX_QUERY))
        stops = previous.get("stops") or []
        if stops:
            lines.append("Previous walk's stops: " + "; ".join(
                f"{i + 1}. {_text(s.get('name'), 80)} ({_text(s.get('category'), 40)})" for i, s in enumerate(stops[:MAX_STOPS])))
    data = ask_engine._post("/chat/completions", {
        "model": os.environ.get("WALK_MODEL", "").strip() or ask_engine.chat_model(),
        "messages": [{"role": "system", "content": PLAN_SYSTEM.replace("GROUPS", groups)},
                     {"role": "user", "content": "\n".join(lines)}],
        "temperature": 0.2, "max_tokens": 400, "response_format": {"type": "json_object"},
    }, timeout=30)
    try:
        plan = json.loads(data["choices"][0]["message"]["content"] or "{}")
    except (KeyError, IndexError, TypeError, ValueError):
        raise WalkError("The assistant sent back something unexpected. Please try again.")
    if not isinstance(plan, dict):
        plan = {}

    def num(key, lo, hi, default):
        try:
            v = float(plan.get(key))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v)) if not math.isnan(v) else default

    def stop_numbers(key):
        raw = plan.get(key) if isinstance(plan.get(key), list) else []
        return sorted({int(n) for n in raw if isinstance(n, (int, float)) and 1 <= int(n) <= MAX_STOPS})

    groups_out = [g for g in (plan.get("groups") or []) if isinstance(g, str) and g in GROUPS][:6]
    return {
        "action": "clarify" if plan.get("action") == "clarify" else "plan",
        "reply": _text(plan.get("reply"), 300),
        "title": _text(plan.get("title"), 60) or "Your walk",
        "near_me": bool(plan.get("near_me")),
        "map_view": bool(plan.get("map_view")) and has_view,
        "place": _text(plan.get("place"), 100),
        "city": _text(plan.get("city"), 60),
        "groups": groups_out,
        "keywords": [_text(k, 30) for k in (plan.get("keywords") or []) if isinstance(k, str) and k.strip()][:4],
        "min_rating": num("min_rating", 0, 5, 0),
        "count": int(num("count", 1, MAX_STOPS, 3)),
        "radius_m": num("radius_m", 300, 20000, 1500),
        "keep": stop_numbers("keep"),
        "drop": stop_numbers("drop"),
    }


# ── 2. Finding places (the directory app) ────────────────────────────────

def _inside(point, polygon):
    """Is [lng, lat] inside the drawn shape? (Ray casting: count how many edges a line to the east crosses.)"""
    x, y, inside = point[0], point[1], False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def find_places(settings, lat, lng, radius, min_rating, keywords, area=None):
    base, key = directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    if not base or not key:
        raise WalkError("The walk planner isn't set up yet.")
    params = {"radius": int(radius), "limit": 40}
    if settings["groups"]:
        params["groups"] = ",".join(settings["groups"])
    if keywords:
        params["q"] = " ".join(keywords)
    if min_rating:
        params["min_rating"] = min_rating
    # An area (the circle the reader drew, or the map view for "food in this
    # area") is searched exactly: its middle and its radius, nothing else.
    # Otherwise a named place wins over "near me": someone in Vienna asking for
    # "the Loop in Chicago" wants Chicago. (The directory also puts a named place
    # or city ahead of lat/lng, which it only uses to pick the right "Main Street".)
    if area:
        params.update(lat=area["lat"], lng=area["lng"], radius=int(area["radius"]))
        if area.get("polygon"):
            params["limit"] = 60          # the circle around a drawn shape holds more than the shape: fetch more, then trim
    elif settings["place"]:
        params["near"] = settings["place"]
        if settings["city"]:
            params["city"] = settings["city"]
        if lat is not None and lng is not None:
            params.update(lat=lat, lng=lng)   # helps pick the right "Main Street"
    elif settings["near_me"] and lat is not None and lng is not None:
        params.update(lat=lat, lng=lng)
    elif settings["city"]:
        params.update(near=settings["city"], city=settings["city"])
    elif lat is not None and lng is not None:
        params.update(lat=lat, lng=lng)
    else:
        return None, []
    try:
        r = requests.get(base + "/embed/places-near?" + urlencode(params), headers={"X-Walk-Key": key}, timeout=15)
    except requests.RequestException:
        raise WalkError("The directory couldn't be reached right now. Please try again in a moment.")
    if r.status_code == 403:
        raise WalkError("The walk planner isn't set up yet.")
    if r.status_code >= 300 and r.status_code != 400:
        raise WalkError("The directory couldn't be searched right now. Please try again in a moment.")
    try:
        data = r.json()
    except ValueError:
        data = {}
    center = data.get("center") if isinstance(data.get("center"), list) else None
    places = [p for p in (data.get("places") or []) if isinstance(p, dict) and SLUG.match(str(p.get("slug") or ""))]
    if area and area.get("polygon"):   # only what's inside the shape the reader drew
        places = [p for p in places if _inside((float(p["lng"]), float(p["lat"])), area["polygon"])]
    return center, places


# ── Google fallback (the directory's /embed/google-places and /embed/places-import) ─────
# Where the directory has too few places (a city we haven't covered yet), the walk
# planner and the route builder search Google live. A Google place only becomes a
# directory listing when a walker actually picks it (import_google).

GOOGLE_WORDS = {"food": "restaurants", "coffee": "coffee shops", "bars": "bars", "wine": "wine bars",
                "rooftops": "rooftop bars", "shops": "shops", "hotels": "hotels", "sights": "sights and museums",
                "other": "things to do"}


def google_places(q, lat=None, lng=None, radius=20000, limit=20, rich=False, groups=None, min_rating=0, within=False):
    """Google's places from the directory app: (places, available). Each place has placeId, and slug if
    it's already a listing. Never raises."""
    base, key = directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    if not base or not key or not q:
        return [], False
    params = {"q": q[:200], "radius": int(radius), "limit": int(limit)}
    if lat is not None and lng is not None:
        params.update(lat=round(lat, 6), lng=round(lng, 6))
    if rich:
        params["rich"] = 1
    if groups:
        params["groups"] = ",".join(groups)
    if min_rating:
        params["min_rating"] = min_rating
    if within:
        params["within"] = 1
    try:
        r = requests.get(base + "/embed/google-places?" + urlencode(params), headers={"X-Walk-Key": key}, timeout=15)
        data = r.json() if r.status_code < 300 else {}
    except (requests.RequestException, ValueError):
        return [], False
    places = [p for p in (data.get("places") or []) if isinstance(p, dict) and p.get("placeId")]
    return places, bool(data.get("available"))


def import_google(place_ids):
    """{placeId: {slug, name, category, address, lat, lng}} for the Google places a walker picked. Never raises."""
    base, key = directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    ids = [p for p in dict.fromkeys(place_ids or []) if p][:15]
    if not base or not key or not ids:
        return {}
    try:
        r = requests.post(base + "/embed/places-import", json={"placeIds": ids}, headers={"X-Walk-Key": key}, timeout=25)
        data = r.json() if r.status_code < 300 else {}
    except (requests.RequestException, ValueError):
        return {}
    out = data.get("places") if isinstance(data.get("places"), dict) else {}
    return {k: v for k, v in out.items() if isinstance(v, dict) and SLUG.match(str(v.get("slug") or ""))}


def google_fill(settings, lat, lng, radius, min_rating, keywords, area, center):
    """Google places for a walk the directory can't fill: (places shaped like the directory's, center)."""
    if area:
        clat, clng, rad = area["lat"], area["lng"], area["radius"]
    elif center:
        clat, clng, rad = center[1], center[0], radius
    elif settings["near_me"] and not settings["place"] and lat is not None and lng is not None:
        clat, clng, rad = lat, lng, radius
    else:
        clat = clng = None
        rad = radius
    where = ""
    if not area:
        if settings["place"]:
            where = " near " + settings["place"] + (", " + settings["city"] if settings["city"] else "")
        elif settings["city"] and clat is None:
            where = " in " + settings["city"]
    if keywords:
        searches = [(" ".join(keywords), list(settings["groups"]))]
    elif settings["groups"]:
        searches = [(GOOGLE_WORDS.get(g, "places"), [g]) for g in settings["groups"][:3]]
    else:   # no kind of place asked for ("plan a walk in Chicago"): whatever's good, any kind
        searches = [("best things to do", [])]
    found, seen = [], set()
    for words, groups in searches:
        places, ok = google_places(words + where, clat, clng, rad, 20, rich=True, groups=groups,
                                   min_rating=min_rating, within=bool(area))
        if not ok:
            break
        for p in places:
            if p["placeId"] in seen:
                continue
            seen.add(p["placeId"])
            p["listed"] = bool(p.get("slug"))
            p["slug"] = p.get("slug") or ("google-" + p["placeId"])[:200]
            if area and area.get("polygon") and not _inside((float(p["lng"]), float(p["lat"])), area["polygon"]):
                continue
            found.append(p)
    new_center = center
    if not new_center and found:
        new_center = [found[0]["lng"], found[0]["lat"]]
    if new_center:
        for p in found:
            p["distance"] = round(_metres((new_center[0], new_center[1]), (p["lng"], p["lat"])))
    return found, new_center


# ── 3. Picking and ordering the stops ────────────────────────────────────

def _score(p):
    rating = p.get("rating") or 3.6
    count = p.get("ratingCount") or 0
    s = rating * 2 + math.log10(1 + count) * 0.6 + (p.get("matched") or 0) * 1.5
    # Places the owner rated themselves ("Add my place") come first: being one of their
    # picks is worth a lot, and their own stars count more than Google's crowd.
    mine = p.get("myRating")
    if mine:
        s += 2.0 + (float(mine) - 3.5) * 1.5
    elif p.get("myTake"):
        s += 1.5
    # Walkers' footprints (footprints.py): already pulled towards 3.5 until enough people rated.
    if p.get("walkerScore"):
        s += (float(p["walkerScore"]) - 3.5) * 1.2
    if p.get("distance") is not None:
        s -= p["distance"] / 1000.0 * 0.7
    return s


def pick(places, count, groups, kept, dropped_slugs):
    taken = list(kept)
    have = {p["slug"] for p in taken}
    pool = [p for p in sorted(places, key=_score, reverse=True) if p["slug"] not in have and p["slug"] not in dropped_slugs]
    if len(groups) > 1:   # a mix: take turns between the kinds of place asked for
        by_group = {g: [p for p in pool if p.get("group") == g] for g in groups}
        while len(taken) < count and any(by_group.values()):
            for g in groups:
                if by_group[g] and len(taken) < count:
                    p = by_group[g].pop(0)
                    if p["slug"] not in have:
                        taken.append(p)
                        have.add(p["slug"])
        pool = [p for p in pool if p["slug"] not in have]
    for p in pool:
        if len(taken) >= count:
            break
        taken.append(p)
        have.add(p["slug"])
    return taken[:count]


def walking_order(stops, start):
    """Nearest stop first, then the nearest from there, then a quick tidy-up of crossings."""
    if len(stops) < 3 and not start:
        return stops
    left = list(stops)
    here = start or (left[0]["lng"], left[0]["lat"])
    order = []
    while left:
        nxt = min(left, key=lambda p: _metres(here, (p["lng"], p["lat"])))
        order.append(nxt)
        left.remove(nxt)
        here = (nxt["lng"], nxt["lat"])

    def length(seq):
        pts = ([start] if start else []) + [(p["lng"], p["lat"]) for p in seq]
        return sum(_metres(a, b) for a, b in zip(pts, pts[1:]))

    improved = True
    while improved:   # 2-opt: reverse any stretch that makes the walk shorter
        improved = False
        for i in range(0, len(order) - 1):
            for j in range(i + 1, len(order)):
                cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                if length(cand) + 1 < length(order):
                    order, improved = cand, True
    return order


# ── 4. One line per stop ─────────────────────────────────────────────────

WHY_SYSTEM = """You write the short notes for a walking route in a city guide. You get the reader's request and the chosen stops with their facts.
Return JSON only: {"intro": "one friendly sentence introducing the walk (max 25 words)", "why": ["one line per stop, in order"]}
Each line: at most 14 words, why this stop fits the request, using ONLY the facts given (rating, kind of place, description, distance, the curator's take and tags, what walkers rated it). When a stop has the curator's take, base its line on that, in the same spirit (don't quote it word for word). Never invent dishes, prices, opening hours or anything else not given. No emojis."""


def why_lines(query, stops, settings, start_label):
    facts = []
    for i, s in enumerate(stops):
        bits = [f"{i + 1}. {s['name']}", s.get("category") or ""]
        if s.get("rating"):
            bits.append(f"rated {s['rating']} from {s.get('ratingCount') or 'some'} reviews")
        if s.get("myTake"):
            bits.append(f"{CURATOR}'s take: " + s["myTake"][:240])
        if s.get("myRating"):
            bits.append(f"{CURATOR} rates it {s['myRating']}/5")
        if s.get("myTags"):
            bits.append("tags: " + s["myTags"][:120])
        if s.get("walkerN"):
            bits.append(f"walkers rate it {s['walkerAvg']}/5 footprints for being worth the walk ({s['walkerN']} ratings)")
        if s.get("about"):
            bits.append("about: " + s["about"][:160])
        if i == 0 and s.get("distance") is not None and start_label:
            bits.append(f"{s['distance']} m from {start_label}")
        facts.append(" | ".join(b for b in bits if b))
    try:
        data = ask_engine._post("/chat/completions", {
            "model": os.environ.get("WALK_MODEL", "").strip() or ask_engine.chat_model(),
            "messages": [{"role": "system", "content": WHY_SYSTEM},
                         {"role": "user", "content": f"Request: {query}\nStops:\n" + "\n".join(facts)}],
            "temperature": 0.4, "max_tokens": 450, "response_format": {"type": "json_object"},
        }, timeout=30)
        out = json.loads(data["choices"][0]["message"]["content"] or "{}")
        why = [_text(w, 140) for w in (out.get("why") or []) if isinstance(w, str)]
        if len(why) == len(stops):
            return _text(out.get("intro"), 220), why
    except Exception as exc:  # the walk still works without the notes
        print("walk: notes failed:", exc)
    return "", [(_text(s["myTake"], 120) if s.get("myTake") else
                 f"Rated {s['rating']}" if s.get("rating") else (s.get("category") or "").replace("_", " ").capitalize())
                for s in stops]


# ── The whole thing ──────────────────────────────────────────────────────

def plan_walk(query, lat, lng, previous, view=None, circle=None, about=""):
    settings = understand(query, lat is not None and lng is not None, previous,
                          has_view=bool(view), has_circle=bool(circle), about=about)
    # Which area to search: a drawn circle always; the map view when the reader
    # means what's on screen ("food in this area") and hasn't named a place.
    area, area_kind = None, ""
    if circle:
        area, area_kind = circle, "circle"
    elif view and settings["map_view"] and not settings["place"]:
        area, area_kind = view, "view"
    if area and settings["action"] == "clarify" and not settings["place"]:
        settings["action"] = "plan"     # we know where: no need to ask
    if settings["action"] == "clarify":
        return {"clarify": settings["reply"] or "Where should the walk be? Turn on location or name a place."}

    prev_stops = []
    for s in ((previous or {}).get("stops") or [])[:MAX_STOPS]:
        try:
            if isinstance(s, dict) and SLUG.match(str(s.get("slug") or "")):
                prev_stops.append({"slug": s["slug"], "name": _text(s.get("name"), 120), "category": _text(s.get("category"), 60),
                                   "group": s.get("group") if s.get("group") in GROUPS else "other",
                                   "lat": max(-90.0, min(90.0, float(s["lat"]))), "lng": max(-180.0, min(180.0, float(s["lng"]))),
                                   "rating": None, "ratingCount": None, "about": "", "address": _text(s.get("address"), 200)})
        except (KeyError, TypeError, ValueError):
            continue
    kept = [prev_stops[n - 1] for n in settings["keep"] if n <= len(prev_stops)]
    dropped = {prev_stops[n - 1]["slug"] for n in settings["drop"] if n <= len(prev_stops)}
    count = max(settings["count"], len(kept))

    # Search, easing off the settings a little at a time if too few places match.
    radius, min_rating, keywords = settings["radius_m"], settings["min_rating"], settings["keywords"]
    center, places, eased = None, [], []
    for attempt in range(4):
        center, places = find_places(settings, lat, lng, radius, min_rating, keywords, area)
        if center is None and not places:
            break          # the directory doesn't know this place: Google may (below)
        usable = [p for p in places if p["slug"] not in dropped]
        if len(usable) + len(kept) >= count:
            break
        if keywords:
            keywords, eased = [], eased + ["words"]
        elif min_rating > 3.9:
            min_rating, eased = round(min_rating - 0.3, 1), eased + ["rating"]
        elif radius < 8000 and not area:      # an area is a promise: never walk outside it
            radius, eased = min(20000, radius * 2), eased + ["distance"]
        else:
            break

    # Too few places in the directory here (a city it doesn't cover yet): fill up from Google.
    google_used = False
    if len([p for p in places if p["slug"] not in dropped]) + len(kept) < count:
        have = {p["slug"] for p in places}
        # Google gets the walker's own request (not the eased one): the pick prefers the best-rated anyway.
        extra, center = google_fill(settings, lat, lng, settings["radius_m"], 0, settings["keywords"], area, center)
        extra = [p for p in extra if p["slug"] not in have]
        places = places + extra
        if extra:
            google_used, eased = True, []
    if center is None and not places:
        return {"clarify": "Where should the walk be? Turn on location, or name a city or a place to start from."}

    try:   # how worth the walk walkers found each place (footprints.py) counts in the pick
        import footprints
        footprints.attach(places + kept)
    except Exception as exc:  # noqa: BLE001
        print("walk: footprints unavailable:", exc)
    stops = pick(places, count, settings["groups"], kept, dropped)
    pending = [p["placeId"] for p in stops if p.get("placeId") and not p.get("listed")]
    if pending:   # Google places the walk uses become directory listings, so the map can show them
        made = import_google(pending)
        for p in stops:
            if p.get("placeId") and not p.get("listed"):
                got = made.get(p["placeId"])
                if got:
                    p["slug"], p["listed"] = got["slug"], True
        stops = [p for p in stops if not p.get("placeId") or p.get("listed")]
    if google_used and settings["min_rating"] and any((p.get("rating") or 0) < settings["min_rating"] for p in stops):
        eased.append("rating")
    if not stops:
        where = ("inside the area you drew" if area_kind == "circle" else "in this part of the map" if area_kind == "view"
                 else "near you" if settings["near_me"] else ("around " + (settings["place"] or settings["city"])))
        return {"clarify": f"I couldn't find matching places {where} in the directory yet. Try a different kind of place or a bigger area."}

    start = (lng, lat) if settings["near_me"] and not settings["place"] and lat is not None and lng is not None else None
    if start and area and _metres(start, (area["lng"], area["lat"])) > area["radius"]:
        start = None   # the reader isn't inside the area: the walk starts at its first stop
    if start and center and _metres(start, (center[0], center[1])) > 5000:
        start = None   # the walk is somewhere else (another part of town, another city): don't start it from the reader
    stops = walking_order(stops, start)
    start_label = "you" if start else (settings["place"] or settings["city"]
                                       or {"circle": "the area the reader drew on the map", "view": "the area on the reader's map"}.get(area_kind, ""))
    if start:
        stops[0]["distance"] = round(_metres(start, (stops[0]["lng"], stops[0]["lat"])))
    intro, why = why_lines(query, stops, settings, start_label)

    note = ""
    if len(stops) < settings["count"]:
        inside = {"circle": " inside the area you drew", "view": " in this part of the map"}.get(area_kind, "" if google_used else " in the directory")
        note = f"I only found {len(stops)} that fit{inside}, so the walk is shorter."
    elif "rating" in eased:
        note = "Not enough places had the top ratings, so I included a few rated a little lower."
    elif "distance" in eased:
        note = "There weren't enough close by, so the walk goes a bit further."

    slugs = ",".join(s["slug"] for s in stops)
    title = settings["title"]
    return {
        "title": title,
        "area": area_kind,
        "intro": intro or settings["reply"],
        "note": note,
        "start": [start[0], start[1]] if start else None,
        "stops": [{
            "slug": s["slug"], "name": s["name"], "category": (s.get("category") or "").replace("_", " "),
            "group": s.get("group") or "other", "color": GROUP_COLORS.get(s.get("group"), "#2f9e7e"),
            "lat": s["lat"], "lng": s["lng"], "rating": s.get("rating"), "ratingCount": s.get("ratingCount"),
            "address": s.get("address") or "", "why": why[i] if i < len(why) else "",
        } for i, s in enumerate(stops)],
        "key": "walk:" + hashlib.sha1(slugs.encode()).hexdigest()[:16],
        "map": directory_url() + "/embed/route-map?" + urlencode({"s": slugs, "t": title, "style": "mansfield", "mode": "story"}),
        "open": "/walk?" + urlencode({"s": slugs, "t": title}),
    }


def transcribe(audio_bytes, mimetype):
    ext = AUDIO_TYPES.get((mimetype or "").split(";")[0].strip().lower())
    if not ext:
        raise WalkError("That recording format isn't supported. Try typing instead.")
    if not ask_engine.enabled():
        raise WalkError("The walk planner isn't set up yet.")
    models = [os.environ.get("TRANSCRIBE_MODEL", "").strip() or "gpt-4o-mini-transcribe", "whisper-1"]
    for i, model in enumerate(models):
        try:
            r = requests.post(ask_engine._base() + "/audio/transcriptions",
                              headers={"Authorization": f"Bearer {ask_engine.api_key()}"},
                              files={"file": (f"speech.{ext}", audio_bytes, mimetype.split(";")[0])},
                              data={"model": model}, timeout=45)
        except requests.RequestException:
            raise WalkError("Couldn't reach the speech service. Please try again or type instead.")
        if r.status_code < 300:
            try:
                return _text((r.json() or {}).get("text"), MAX_QUERY)
            except ValueError:
                raise WalkError("Couldn't understand the recording. Please try again or type instead.")
        print("walk: transcription error", r.status_code, r.text[:200])
        if i == 0 and r.status_code in (400, 404):
            continue   # try the older model
        break
    raise WalkError("Couldn't understand the recording. Please try again or type instead.")


# ── Routes ───────────────────────────────────────────────────────────────

@walk_bp.get("/walk")
def walk_page():
    return render_walk()


def render_walk(hotel=None):
    """The walk page; `hotel` (hotels.guide()) turns it into that hotel's guest guide (/h/<slug>)."""
    # "This week" drawer (walk_feed.py): the owner's published weekly walks and related stories
    from walk_feed import _city, published_feed, related_articles, related_reads
    import readers
    articles = related_articles()
    try:
        counts = readers.comment_counts()
    except Exception:
        counts = {}
    from community import community_feed   # "From walkers": walks readers posted for this city (community.py)
    import guests   # a guest account's free walks left (guests.py)
    return render_template("walk.html", directory=directory_url(), categories=CATEGORIES, active_category=None,
                           feed=published_feed(), reads=related_reads(articles=articles), reads_data=articles,
                           comment_counts=counts, feed_city=_city(), community=community_feed(current_reader(), _city()),
                           hotel=hotel, guest=None if hotel else guests.current(), guest_allowed=guests.builds_allowed())


def _guard():
    import guests
    if not current_reader() and not session.get("is_admin") and not _guest_hotel() and not guests.current():
        return jsonify(ok=False, error="sign_in"), 401
    if not _csrf_ok():
        return jsonify(ok=False, error="Your session expired. Reload the page and try again."), 403
    if not ask_engine.enabled():
        return jsonify(ok=False, error="The walk planner isn't set up yet."), 503
    return None


@walk_bp.post("/api/walk-plan")
def api_walk_plan():
    bad = _guard()
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    query = _text(body.get("query"), MAX_QUERY)
    if len(query) < 3:
        return jsonify(ok=False, error="Tell me what kind of walk you'd like."), 400

    def coord(name, lo, hi):
        try:
            v = float(body.get(name))
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi and not math.isnan(v) else None
    lat, lng = coord("lat", -90, 90), coord("lng", -180, 180)
    if lat is None or lng is None:
        lat = lng = None
    guest_hotel = None if current_reader() else _guest_hotel()
    if guest_hotel and lat is None:
        lat, lng = guest_hotel["lat"], guest_hotel["lng"]   # a hotel guest without location: "near me" is the hotel
    previous = body.get("previous") if isinstance(body.get("previous"), dict) else None

    def area(name):
        """The map view or drawn circle the page sent: {lat, lng, radius in metres}, or None."""
        a = body.get(name)
        if not isinstance(a, dict):
            return None
        try:
            la, ln, r = float(a.get("lat")), float(a.get("lng")), float(a.get("radius"))
        except (TypeError, ValueError):
            return None
        if any(math.isnan(v) for v in (la, ln, r)) or not (-90 <= la <= 90 and -180 <= ln <= 180):
            return None
        return {"lat": la, "lng": ln, "radius": max(100.0, min(20000.0, r))}

    def polygon():
        """The shape the reader drew: 4 to 400 [lng, lat] points, or None."""
        raw = body.get("polygon")
        if not isinstance(raw, list) or not 4 <= len(raw) <= 400:
            return None
        pts = []
        for pt in raw:
            try:
                x, y = float(pt[0]), float(pt[1])
            except (TypeError, ValueError, IndexError, KeyError):
                return None
            if math.isnan(x) or math.isnan(y) or not (-180 <= x <= 180 and -90 <= y <= 90):
                return None
            pts.append((x, y))
        return pts

    circle = area("circle")
    if circle:
        circle["polygon"] = polygon()
    try:
        _check_and_count()
        from onboarding import planner_note
        result = plan_walk(query, lat, lng, previous, view=area("view"), circle=circle, about=planner_note(current_reader()))
    except WalkError as exc:
        msg = str(exc)
        if msg == "guest_out":
            import guests
            return jsonify(ok=False, error="guest_out", allowed=guests.builds_allowed()), 403
        return jsonify(ok=False, error=msg), (401 if msg == "sign_in" else 429 if "today" in msg else 502)
    except ask_engine.AskError as exc:
        return jsonify(ok=False, error=str(exc).replace("Search isn't", "The walk planner isn't")), 502
    except Exception as exc:
        print("walk: unexpected failure:", exc)
        return jsonify(ok=False, error="Something went wrong planning that walk. Please try again."), 500
    # The location is only used for this one request: never stored or logged.
    if result.get("clarify"):
        kind = "no_match" if str(result["clarify"]).startswith("I couldn't find") else "unclear"
        log_event(kind, query=query)
    else:
        log_event("change" if previous else "plan", query=query, area=result.get("area"),
                  walk=result.get("key"), places=result.get("stops"))
        if guest_hotel:
            import hotels
            hotels.bump(guest_hotel["id"], "plan")
        if not current_reader() and not session.get("is_admin") and not guest_hotel:
            import guests
            if guests.current():   # a guest account: this finished plan uses one free build
                result["guest_left"] = guests.use_build()
    return jsonify(ok=True, query=query, **result)


@walk_bp.get("/api/place-search")
def place_search():
    """Directory places whose name (or kind) has these words, near the map:
    ?q=words&lat&lng. For the route builder ("add a place that isn't saved yet").
    Signed-in readers only."""
    if not current_reader() and not session.get("is_admin"):
        return jsonify(ok=False, error="sign_in"), 401
    q = _text(request.args.get("q"), 60)
    words = [w for w in re.split(r"[^\w']+", q.lower()) if len(w) >= 2][:6]
    if not words:
        return jsonify(ok=True, places=[])
    base, key = directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    if not base or not key:
        return jsonify(ok=False, error="The directory isn't set up yet."), 503
    try:
        lat, lng = float(request.args.get("lat")), float(request.args.get("lng"))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError
    except (TypeError, ValueError):
        lat, lng = 48.2082, 16.3738
    params = {"lat": lat, "lng": lng, "radius": 20000, "limit": 60, "q": " ".join(words)}
    try:
        r = requests.get(base + "/embed/places-near?" + urlencode(params), headers={"X-Walk-Key": key}, timeout=12)
        data = r.json() if r.status_code < 300 else {}
    except (requests.RequestException, ValueError):
        return jsonify(ok=False, error="The directory couldn't be searched right now."), 502
    out = []
    for p in data.get("places") or []:
        if not isinstance(p, dict) or not SLUG.match(str(p.get("slug") or "")):
            continue
        name = str(p.get("name") or "")
        hay = (name + " " + str(p.get("category") or "")).lower()
        if all(w in hay for w in words):   # every word, in the name or the kind of place
            out.append({"slug": p["slug"], "name": _text(name, 80),
                        "category": _text(str(p.get("category") or "").replace("_", " "), 40),
                        "address": _text(p.get("address"), 120), "name_hit": all(w in name.lower() for w in words)})
    out.sort(key=lambda p: not p["name_hit"])   # name matches first, then nearest (the directory's order)
    out = [{k: v for k, v in p.items() if k != "name_hit"} for p in out[:8]]
    # Few matches (a city the directory doesn't cover yet, or a place nobody added): ask Google too.
    reader = current_reader()
    if len(out) < 3 and reader and len(q) >= 3:
        gkey = "gsearch:r:" + reader["id"]
        try:
            over = ask_limits._used(gkey) >= _int_env("GOOGLE_SEARCH_LIMIT_READER", 60)
            if not over:
                ask_limits._count(gkey)
        except Exception as exc:  # noqa: BLE001
            print("walk: usage counter unavailable:", exc)
            over = False
        if not over:
            listed = {p["slug"] for p in out}
            places, _ok = google_places(q, lat, lng, 20000, 8)
            for g in places:
                if g.get("slug") and g["slug"] in listed:
                    continue
                out.append({"slug": g.get("slug") or "", "placeId": g["placeId"], "google": not g.get("slug"),
                            "name": _text(g.get("name"), 80), "category": _text(g.get("category"), 40),
                            "address": _text(g.get("address"), 120)})
    return jsonify(ok=True, places=out[:12])


@walk_bp.post("/api/place-import")
def place_import():
    """The route builder picked a Google place: make it a directory listing. {placeId} -> {slug, name, ...}."""
    reader = current_reader()
    if not reader:
        return jsonify(ok=False, error="sign_in"), 401
    if not _csrf_ok():
        return jsonify(ok=False, error="This page expired. Reload it and try again."), 403
    pid = str((request.get_json(silent=True) or {}).get("placeId") or "")
    if not re.match(r"^[A-Za-z0-9_-]{10,200}$", pid):
        return jsonify(ok=False, error="That place can't be added."), 400
    ikey = "gimport:r:" + reader["id"]
    try:
        if ask_limits._used(ikey) >= _int_env("GOOGLE_IMPORT_LIMIT_READER", 40):
            return jsonify(ok=False, error="You've added a lot of new places today. Try again tomorrow."), 429
        ask_limits._count(ikey)
    except Exception as exc:  # noqa: BLE001
        print("walk: usage counter unavailable:", exc)
    got = import_google([pid]).get(pid)
    if not got:
        return jsonify(ok=False, error="Couldn't add that place right now. Try again in a minute."), 502
    return jsonify(ok=True, place={"slug": got["slug"], "name": _text(got.get("name"), 80),
                                   "category": _text(got.get("category"), 40), "address": _text(got.get("address"), 120)})


@walk_bp.post("/api/walk-plan/voice")
def api_walk_voice():
    bad = _guard()
    if bad:
        return bad
    f = request.files.get("audio")
    if not f:
        return jsonify(ok=False, error="No recording came through. Please try again."), 400
    audio = f.read(MAX_AUDIO + 1)
    if len(audio) > MAX_AUDIO:
        return jsonify(ok=False, error="That recording is too long. Keep it under 30 seconds."), 400
    if len(audio) < 800:
        return jsonify(ok=False, error="I didn't catch that. Hold the mic a little longer."), 400
    try:
        _check_and_count(":voice")
        text = transcribe(audio, f.mimetype or "audio/webm")
    except WalkError as exc:
        msg = str(exc)
        if msg == "guest_out":
            return jsonify(ok=False, error="guest_out"), 403
        return jsonify(ok=False, error=msg), (401 if msg == "sign_in" else 429 if "today" in msg else 502)
    if not text:
        return jsonify(ok=False, error="I didn't catch that. Please try again."), 400
    return jsonify(ok=True, text=text)


# ── Shared walks ───────────────────────────────────────────────────────────
# "Share" on a walk makes a short link (/w/<code>) that remembers the stops,
# the title and the area the reader drew, so a friend opening it sees the same
# walk inside the same shape, and the link preview says what it is. Each code
# also counts how often it was opened: one of the signals for the weekly feed.

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_shares_ready = False


def _shares_table():
    global _shares_ready
    if _shares_ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS walk_shares (
            code        TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            stops_json  TEXT NOT NULL,
            area_json   TEXT,
            reader_id   TEXT,
            created_at  TEXT NOT NULL,
            opens       INTEGER NOT NULL DEFAULT 0
        )""")
    _shares_ready = True


def _clean_share(data):
    """The walk a reader wants to share, checked and trimmed, or None."""
    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()[:120] or "A walk"
    stops = []
    for s in (data.get("stops") or [])[:25]:
        if not isinstance(s, dict):
            continue
        slug = str(s.get("slug") or "")
        if not _SLUG_RE.match(slug):
            continue
        name = re.sub(r"\s+", " ", str(s.get("name") or "")).strip()[:80]
        stops.append({"slug": slug, "name": name})
    if len(stops) < 2:
        return None
    area = None
    poly = data.get("polygon")
    if isinstance(poly, list) and 4 <= len(poly) <= 400:
        try:
            pts = [[round(float(p[0]), 5), round(float(p[1]), 5)] for p in poly]
        except (TypeError, ValueError, IndexError):
            pts = []
        if pts and all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in pts):
            step = max(1, len(pts) // 80)          # keep the stored shape small
            area = pts[::step]
            if area[-1] != pts[-1]:
                area.append(pts[-1])
    return {"title": title, "stops": stops, "area": area}


def _share_code(walk):
    raw = json.dumps([walk["title"], [s["slug"] for s in walk["stops"]], walk["area"]], separators=(",", ":"))
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


@walk_bp.post("/api/walk-share")
def walk_share():
    if not current_reader() and not session.get("is_admin"):
        return jsonify(ok=False, error="sign_in"), 401
    if not _csrf_ok():
        return jsonify(ok=False, error="Your session expired. Reload the page and try again."), 403
    walk = _clean_share(request.get_json(silent=True) or {})
    if not walk:
        return jsonify(ok=False, error="This walk can't be shared yet."), 400
    code = _share_code(walk)
    try:
        _shares_table()
        if not db.query_one("SELECT code FROM walk_shares WHERE code = :c", c=code):
            reader = current_reader()
            db.execute(
                "INSERT INTO walk_shares (code, title, stops_json, area_json, reader_id, created_at, opens) "
                "VALUES (:c, :t, :s, :a, :r, :at, 0)",
                c=code, t=walk["title"], s=json.dumps(walk["stops"]),
                a=json.dumps(walk["area"]) if walk["area"] else None,
                r=reader["id"] if reader else None, at=db.now_iso())
    except Exception as exc:
        print("walk: could not save share:", exc)
        return jsonify(ok=False, error="Couldn't make a link right now. Please try again."), 500
    return jsonify(ok=True, url="/w/" + code)


@walk_bp.get("/w/<code>")
def walk_shared(code):
    if not re.match(r"^[0-9a-f]{10}$", code):
        abort(404)
    try:
        _shares_table()
        row = db.query_one("SELECT code, title, stops_json, area_json FROM walk_shares WHERE code = :c", c=code)
        if row:
            db.execute("UPDATE walk_shares SET opens = opens + 1 WHERE code = :c", c=code)
            opened = json.loads(row["stops_json"])
            slugs = ",".join(x["slug"] for x in opened)
            # Counted under the walk itself (same key as Save uses), not the link.
            log_event("share_open", walk="walk:" + hashlib.sha1(slugs.encode()).hexdigest()[:16], places=opened)
    except Exception as exc:
        print("walk: could not load share:", exc)
        row = None
    if not row:
        abort(404)
    stops = json.loads(row["stops_json"])
    names = [s["name"] for s in stops if s.get("name")]
    if len(names) > 3:
        summary = ", ".join(names[:3]) + f" and {len(names) - 3} more"
    else:
        summary = ", ".join(names)
    shared = {
        "code": row["code"],
        "title": row["title"],
        "stops": stops,
        "area": json.loads(row["area_json"]) if row["area_json"] else None,
        "description": f"A {len(stops)}-stop walk" + (f": {summary}." if summary else ".")
                       + " Open it on the map, then plan your own.",
    }
    return render_template("walk.html", directory=directory_url(), categories=CATEGORIES,
                           active_category=None, shared=shared)


# ── What readers do with walks (the signals for the weekly feed) ───────────
# One row per thing a reader did: planned a walk, asked for something the
# directory doesn't have, saved / shared / started a walk, opened a shared
# link, read about a stop. No location and no reader identity is kept: `who`
# is a one-way hash, only used to count *different* people (so one person
# can't make something "trending"). Rows older than RETENTION_DAYS are removed.

EVENT_KINDS = {"plan", "change", "unclear", "no_match", "save", "unsave", "share", "share_open", "start", "place"}
CLIENT_EVENTS = {"save", "unsave", "share", "start", "place"}   # the ones the page reports
RETENTION_DAYS = 180
_events_ready = False


def _events_table():
    global _events_ready
    if _events_ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS walk_events (
            id      TEXT PRIMARY KEY,
            kind    TEXT NOT NULL,
            day     TEXT NOT NULL,
            at      TEXT NOT NULL,
            who     TEXT,
            walk    TEXT,
            query   TEXT,
            area    TEXT,
            places  TEXT
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_walk_events_day ON walk_events (day, kind)")
    db.execute("DELETE FROM walk_events WHERE day < :d", d=_event_day(-RETENTION_DAYS))
    _events_ready = True


def _event_day(offset=0):
    from datetime import date, timedelta
    return (date.today() + timedelta(days=offset)).isoformat()


def _event_who():
    """A stand-in for the reader that can't be turned back into who they are."""
    reader = current_reader()
    raw = ("r:" + str(reader["id"])) if reader else ("s:" + str(session.get("_wid") or ""))
    if not reader and not session.get("_wid"):
        session["_wid"] = hashlib.sha1(os.urandom(16)).hexdigest()[:16]
        raw = "s:" + session["_wid"]
    secret = os.environ.get("SECRET_KEY", "") or "walk-events"
    return hashlib.sha256((secret + "|" + raw).encode()).hexdigest()[:16]


def _event_places(stops):
    out = []
    for s in (stops or [])[:25]:
        if isinstance(s, dict) and _SLUG_RE.match(str(s.get("slug") or "")):
            out.append({"slug": s["slug"], "name": re.sub(r"\s+", " ", str(s.get("name") or "")).strip()[:80]})
    return out


def log_event(kind, query=None, area=None, walk=None, places=None):
    """Record one thing a reader did. Never fails the request it's part of."""
    if kind not in EVENT_KINDS:
        return
    try:
        _events_table()
        pl = _event_places(places)
        db.execute(
            "INSERT INTO walk_events (id, kind, day, at, who, walk, query, area, places) "
            "VALUES (:id, :k, :d, :at, :w, :walk, :q, :a, :p)",
            id=db.new_id(), k=kind, d=_event_day(), at=db.now_iso(), w=_event_who(),
            walk=(str(walk)[:40] if walk else None),
            q=(re.sub(r"\s+", " ", str(query)).strip()[:200] if query else None),
            a=(str(area)[:20] if area else None), p=json.dumps(pl) if pl else None)
    except Exception as exc:
        print("walk: could not log event:", exc)


def walked_counts(keys):
    """How many different people started each walk (by its walk: key) in the last RETENTION_DAYS. Never fails."""
    keys = [k for k in dict.fromkeys(keys or []) if k and re.match(r"^walk:[0-9a-f]{16}$", k)][:40]
    if not keys:
        return {}
    try:
        _events_table()
        params = {f"k{i}": k for i, k in enumerate(keys)}
        rows = db.query(
            "SELECT walk, COUNT(DISTINCT who) AS n FROM walk_events WHERE kind = 'start' AND walk IN ("
            + ", ".join(":" + name for name in params) + ") GROUP BY walk", **params)
        return {r["walk"]: int(r["n"] or 0) for r in rows}
    except Exception as exc:
        print("walk: could not count walks:", exc)
        return {}


@walk_bp.post("/api/walk-event")
def walk_event():
    """The page reports what the reader did with a walk: {kind, walk?, places?}."""
    data = request.get_json(silent=True) or {}
    kind = str(data.get("kind") or "")
    if kind not in CLIENT_EVENTS or not _csrf_ok():
        return "", 204
    # A simple brake: at most 300 reports a day from one browser.
    today = _event_day()
    seen = session.get("_wev") or [today, 0]
    if seen[0] != today:
        seen = [today, 0]
    if seen[1] >= 300:
        return "", 204
    session["_wev"] = [today, seen[1] + 1]
    walk = str(data.get("walk") or "")
    walk = walk if re.match(r"^(walk:[0-9a-f]{16}|[0-9a-f]{10})$", walk) else None
    log_event(kind, walk=walk, places=data.get("places"))
    return "", 204


# ── Walk insights (owner only): what people plan, save and share ───────────

def _insights(days):
    _events_table()
    since = _event_day(-(days - 1))
    rows = db.query("SELECT kind, who, walk, query, places FROM walk_events WHERE day >= :d", d=since)
    totals, people = {}, set()
    searches, missing, places, walks = {}, {}, {}, {}
    for r in rows:
        k = r["kind"]
        totals[k] = totals.get(k, 0) + 1
        people.add(r["who"])
        if r["query"] and k in ("plan", "unclear", "no_match"):
            q = r["query"].lower().strip(" ?.!")
            bucket = missing if k == "no_match" else searches
            e = bucket.setdefault(q, {"text": r["query"], "n": 0, "who": set()})
            e["n"] += 1
            e["who"].add(r["who"])
        weight = {"plan": 1, "change": 1, "save": 3, "share": 3, "share_open": 2, "start": 4, "place": 2}.get(k, 0)
        if weight and r["places"]:
            for pl in json.loads(r["places"]):
                e = places.setdefault(pl["slug"], {"slug": pl["slug"], "name": pl.get("name") or pl["slug"],
                                                    "score": 0, "who": set(), "in": {}})
                if pl.get("name"):
                    e["name"] = pl["name"]
                e["score"] += weight
                e["who"].add(r["who"])
                e["in"][k] = e["in"].get(k, 0) + 1
        if r["walk"] and k in ("save", "share", "share_open", "start"):
            e = walks.setdefault(r["walk"], {"walk": r["walk"], "places": [], "score": 0, "who": set(), "in": {}})
            if r["places"] and not e["places"]:
                e["places"] = [p.get("name") or p["slug"] for p in json.loads(r["places"])]
            e["score"] += weight
            e["who"].add(r["who"])
            e["in"][k] = e["in"].get(k, 0) + 1

    def top(d, key, n=15):
        out = sorted(d.values(), key=lambda e: (-e[key], -len(e["who"])))[:n]
        for e in out:
            e["people"] = len(e.pop("who"))
        return out

    return {
        "days": days, "people": len(people), "totals": totals,
        "searches": top(searches, "n"), "missing": top(missing, "n"),
        "places": top(places, "score"), "walks": top(walks, "score", 10),
    }


def _insights_view():
    days = request.args.get("days", type=int) or 7
    days = days if days in (7, 30, 90) else 7
    return render_template("admin/walk_insights.html", data=_insights(days))


try:   # the admin app's "owner only" check; without it (tests) the page is simply off
    from admin import owner_required as _owner_required
    walk_bp.add_url_rule("/admin/walk-insights", "walk_insights", _owner_required(_insights_view))
except ImportError:
    pass
