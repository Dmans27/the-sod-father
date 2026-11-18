"""
"Plan a walk" -- readers ask, by typing or speaking, for something like
"3 highly rated coffee shops near me", and get a walking route on the map
built from the places in the directory app.

  GET  /walk                    the page (static/js/walk-planner.js)
  POST /api/walk-plan           {query, lat?, lng?, previous?} -> a walk
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
from flask import Blueprint, jsonify, render_template, request, session

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
    """(usage key, daily limit) for the signed-in reader, or (None, 0)."""
    if session.get("is_admin"):
        return "walk:admin", _int_env("WALK_LIMIT_ADMIN", 200)
    reader = current_reader()
    if not reader:
        return None, 0
    return "walk:r:" + reader["id"], _int_env("WALK_LIMIT_READER", 15)


def _check_and_count(kind=""):
    """Raise WalkError if the reader (or everyone together) is out of walks today, else count one."""
    key, limit = _who()
    if not key:
        raise WalkError("sign_in")
    try:
        if ask_limits._used(key + kind) >= limit:
            raise WalkError(f"You've planned {limit} walks today. Try again tomorrow.")
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
- "near me", "nearby", "around here" -> near_me true. With no place or city named, near_me is true.
- radius_m: "near me"/"nearby" 1500; "within N minutes" -> N*80; a named area 1500; a whole city 4000.
- If near_me is true, no place or city is named, and the reader's location is NOT available, use action "clarify" and ask where the walk should be.
- If the request has nothing to do with places to go (for example a math question), use "clarify" and say you can plan walks to places in the directory.
- If there is a previous walk and the reader asks to change it ("swap the second one", "make it 5 stops", "add a bar at the end"), keep the earlier settings unless they change them, and fill keep/drop by stop number.
"""


def understand(query, has_location, previous):
    groups = ", ".join(f"{k} ({v})" for k, v in GROUPS.items())
    lines = [f"Reader's request: {query}",
             f"Reader's location available: {'yes' if has_location else 'no'}"]
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

def find_places(settings, lat, lng, radius, min_rating, keywords):
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
    if settings["near_me"] and lat is not None and lng is not None:
        params.update(lat=lat, lng=lng)
    elif settings["place"]:
        params["near"] = settings["place"]
        if settings["city"]:
            params["city"] = settings["city"]
        if lat is not None and lng is not None:
            params.update(lat=lat, lng=lng)   # helps pick the right "Main Street"
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
    return center, places


# ── 3. Picking and ordering the stops ────────────────────────────────────

def _score(p):
    rating = p.get("rating") or 3.6
    count = p.get("ratingCount") or 0
    s = rating * 2 + math.log10(1 + count) * 0.6 + (p.get("matched") or 0) * 1.5
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
Each line: at most 14 words, why this stop fits the request, using ONLY the facts given (rating, kind of place, description, distance). Never invent dishes, prices, opening hours or anything else not given. No emojis."""


def why_lines(query, stops, settings, start_label):
    facts = []
    for i, s in enumerate(stops):
        bits = [f"{i + 1}. {s['name']}", s.get("category") or ""]
        if s.get("rating"):
            bits.append(f"rated {s['rating']} from {s.get('ratingCount') or 'some'} reviews")
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
    return "", [(f"Rated {s['rating']}" if s.get("rating") else (s.get("category") or "").replace("_", " ").capitalize())
                for s in stops]


# ── The whole thing ──────────────────────────────────────────────────────

def plan_walk(query, lat, lng, previous):
    settings = understand(query, lat is not None and lng is not None, previous)
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
        center, places = find_places(settings, lat, lng, radius, min_rating, keywords)
        if center is None and not places:
            return {"clarify": "Where should the walk be? Turn on location, or name a city or a place to start from."}
        usable = [p for p in places if p["slug"] not in dropped]
        if len(usable) + len(kept) >= count:
            break
        if keywords:
            keywords, eased = [], eased + ["words"]
        elif min_rating > 3.9:
            min_rating, eased = round(min_rating - 0.3, 1), eased + ["rating"]
        elif radius < 8000:
            radius, eased = min(20000, radius * 2), eased + ["distance"]
        else:
            break

    stops = pick(places, count, settings["groups"], kept, dropped)
    if not stops:
        where = "near you" if settings["near_me"] else ("around " + (settings["place"] or settings["city"]))
        return {"clarify": f"I couldn't find matching places {where} in the directory yet. Try a different kind of place or a bigger area."}

    start = (lng, lat) if settings["near_me"] and lat is not None and lng is not None else None
    stops = walking_order(stops, start)
    start_label = "you" if start else (settings["place"] or settings["city"] or "")
    if start:
        stops[0]["distance"] = round(_metres(start, (stops[0]["lng"], stops[0]["lat"])))
    intro, why = why_lines(query, stops, settings, start_label)

    note = ""
    if len(stops) < settings["count"]:
        note = f"I only found {len(stops)} that fit in the directory, so the walk is shorter."
    elif "rating" in eased:
        note = "Not enough places had the top ratings, so I included a few rated a little lower."
    elif "distance" in eased:
        note = "There weren't enough close by, so the walk goes a bit further."

    slugs = ",".join(s["slug"] for s in stops)
    title = settings["title"]
    return {
        "title": title,
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
    return render_template("walk.html", directory=directory_url(), categories=CATEGORIES, active_category=None)


def _guard():
    if not current_reader() and not session.get("is_admin"):
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
    previous = body.get("previous") if isinstance(body.get("previous"), dict) else None
    try:
        _check_and_count()
        result = plan_walk(query, lat, lng, previous)
    except WalkError as exc:
        msg = str(exc)
        return jsonify(ok=False, error=msg), (401 if msg == "sign_in" else 429 if "today" in msg else 502)
    except ask_engine.AskError as exc:
        return jsonify(ok=False, error=str(exc).replace("Search isn't", "The walk planner isn't")), 502
    except Exception as exc:
        print("walk: unexpected failure:", exc)
        return jsonify(ok=False, error="Something went wrong planning that walk. Please try again."), 500
    # The location is only used for this one request: never stored or logged.
    return jsonify(ok=True, query=query, **result)


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
        return jsonify(ok=False, error=msg), (401 if msg == "sign_in" else 429 if "today" in msg else 502)
    if not text:
        return jsonify(ok=False, error="I didn't catch that. Please try again."), 400
    return jsonify(ok=True, text=text)
