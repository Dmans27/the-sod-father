"""
The city feed: the stories page shows one city at a time -- where you are, or any
city you search for in the bar at the top.

  GET /api/cities?q=text        up to 5 cities as you type (the directory's Mapbox geocoder)
  GET /api/city-at?lat&lng      the city at a point (the "near me" button, and the first visit)

Which city a page shows (current_city):
  1. ?city=Name&ll=lat,lng (&label=...) -- a search; remembered in the mf_city cookie
  2. the mf_city cookie -- the last city you looked at
  3. FEED_CITY (default Vienna) at FEED_CITY_CENTER

What counts as "in" a city:
  - a story whose City field (story editor) is the city, or, with no City set, whose title or
    dek names it (CITY_ALIASES: Vienna/Wien, Munich/München, ...)
  - a community walk within NEAR_M of the city's centre (community.community_feed)
  - the weekly "Walks this week" feed only belongs to FEED_CITY (walk_feed.py)
"""
import hashlib
import json
import math
import os
import re
from urllib.parse import quote, unquote, urlencode

import requests
from flask import Blueprint, jsonify, request

import walk_planner as wp

city_bp = Blueprint("city_feed", __name__)

COOKIE = "mf_city"
NEAR_M = 30000
CITY_ALIASES = {
    "vienna": ["wien"], "munich": ["münchen", "muenchen"], "prague": ["praha"], "rome": ["roma"],
    "milan": ["milano"], "florence": ["firenze"], "venice": ["venezia"], "naples": ["napoli"],
    "lisbon": ["lisboa"], "cologne": ["köln", "koeln"], "zurich": ["zürich"], "copenhagen": ["københavn"],
    "warsaw": ["warszawa"], "krakow": ["kraków"], "budapest": [], "new york": ["new york city", "nyc", "manhattan", "brooklyn"],
    "washington": ["washington dc", "washington, d.c."], "los angeles": ["la"], "san francisco": ["sf"],
}


def _dist_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def home_city():
    name = (os.environ.get("FEED_CITY") or "Vienna").strip()[:60] or "Vienna"
    try:
        lat, lng = [float(x) for x in (os.environ.get("FEED_CITY_CENTER") or "48.2082,16.3738").split(",")]
    except ValueError:
        lat, lng = 48.2082, 16.3738
    return {"name": name, "label": name, "lat": lat, "lng": lng}


def _clean_city(name, label, lat, lng):
    name = " ".join(str(name or "").split())[:80]
    label = " ".join(str(label or name).split())[:160] or name
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not name or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"name": name, "label": label, "lat": round(lat, 5), "lng": round(lng, 5)}


def _directory(params):
    base, key = wp.directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    if not base or not key:
        return []
    try:
        r = requests.get(base + "/embed/cities?" + urlencode(params), headers={"X-Walk-Key": key}, timeout=8)
        data = r.json() if r.status_code < 300 else {}
    except (requests.RequestException, ValueError):
        return []
    return [c for c in (data.get("cities") or []) if isinstance(c, dict)]


def find_cities(q):
    return [c for c in (_clean_city(x.get("name"), x.get("label"), x.get("lat"), x.get("lng")) for x in _directory({"q": q})) if c]


def city_at(lat, lng):
    found = _directory({"lat": lat, "lng": lng})
    return _clean_city(found[0].get("name"), found[0].get("label"), found[0].get("lat"), found[0].get("lng")) if found else None


def current_city():
    """(city, cookie value to set or None). Never fails: falls back to the home city."""
    args = request.args
    if args.get("city"):
        city = None
        ll = str(args.get("ll") or "").split(",")
        if len(ll) == 2:
            city = _clean_city(args.get("city"), args.get("label"), ll[0], ll[1])
        if not city:   # just a name ("/?city=Lisbon"): ask the geocoder
            hits = find_cities(str(args.get("city"))[:80])
            city = hits[0] if hits else None
        if city:
            return city, quote(json.dumps(city, separators=(",", ":")))
    raw = request.cookies.get(COOKIE)
    if raw:
        try:
            d = json.loads(unquote(raw))
            city = _clean_city(d.get("name"), d.get("label"), d.get("lat"), d.get("lng"))
            if city:
                return city, None
        except (ValueError, AttributeError):
            pass
    return home_city(), None


def is_home(city):
    home = home_city()
    return _dist_m(city["lat"], city["lng"], home["lat"], home["lng"]) <= NEAR_M


def _names(city):
    base = city["name"].lower()
    names = {base}
    for k, v in CITY_ALIASES.items():
        if base == k or base in v:
            names.add(k)
            names.update(v)
    return {n for n in names if len(n) >= 2}


def story_in_city(article, city):
    """A story belongs to the city when its Location (picked in the editor) is within NEAR_M of it,
    or its City name matches (older stories), or (no location set) its title or dek names it. Not the body: a passing mention ("flew in from Chicago") shouldn't put a story in that feed."""
    try:   # picked in the editor with its coordinates: within NEAR_M of the city's centre
        lat, lng = float(article.get("city_lat")), float(article.get("city_lng"))
        return _dist_m(lat, lng, city["lat"], city["lng"]) <= NEAR_M
    except (TypeError, ValueError):
        pass
    names = _names(city)
    field = str(article.get("city") or "").strip().lower()
    if field:
        return field in names
    hay = " ".join(str(article.get(k) or "") for k in ("title", "dek")).lower()
    return any(re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", hay) for n in names if len(n) >= 3)


# ── API: the search bar ──────────────────────────────────────────────────

def _limited():
    """A simple daily brake per visitor on geocoder lookups."""
    try:
        import ask as ask_limits
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()
        key = "geo:" + hashlib.sha256(ip.encode()).hexdigest()[:16]
        if ask_limits._used(key) >= int(os.environ.get("CITY_SEARCH_LIMIT", "400")):
            return True
        ask_limits._count(key)
    except Exception as exc:  # noqa: BLE001
        print("city: usage counter unavailable:", exc)
    return False


@city_bp.get("/api/cities")
def api_cities():
    q = " ".join(str(request.args.get("q") or "").split())[:80]
    if len(q) < 2:
        return jsonify(cities=[])
    if _limited():
        return jsonify(cities=[], error="busy"), 429
    return jsonify(cities=find_cities(q))


@city_bp.get("/api/city-at")
def api_city_at():
    try:
        lat, lng = float(request.args.get("lat")), float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify(city=None), 400
    if _limited():
        return jsonify(city=None, error="busy"), 429
    return jsonify(city=city_at(lat, lng))
