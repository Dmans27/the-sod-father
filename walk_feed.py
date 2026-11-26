"""
"This week" on Plan a walk: a short weekly feed of walks, written by the AI
from what people did (walk_planner's walk_events) plus the curator's own picks
and the time of year, and approved by the owner before anyone sees it.

  /admin/walk-feed         (owner) see the live feed, make a new draft, edit, publish
  walk_page()              shows the published feed on the Plan a walk start card

How a draft is made (one AI call, a few cents):
  1. Numbers from the last 7 days: places and walks that several *different*
     people planned, saved, shared or walked, and what they searched for.
     Nothing that fewer than FEED_MIN_PEOPLE people did is used, so no single
     reader's activity (or a search they typed) ends up in the feed.
  2. Candidates from the directory: the curator's own rated places and other
     well-rated places in FEED_CITY.
  3. The AI groups candidates into 3-5 short walks with a title and a line on
     why. Every stop is checked against the candidates (no invented places)
     and every walk must be compact enough to actually walk.
  4. It's saved as a draft. Nothing changes for readers until it's published.

Environment:
  FEED_CITY         the city the feed is about (default: Vienna)
  FEED_MIN_PEOPLE   how many different people make something a trend (default: 3)
"""

import hashlib
import json
import os
import re
from datetime import date
from urllib.parse import urlencode

import requests
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import ask_engine
import db
import walk_planner as wp
from admin import owner_required
from readers import _csrf_ok, csrf_token

feed_bp = Blueprint("walk_feed", __name__)

MAX_ITEMS = 5
MAX_SPREAD_M = 2500       # no two stops of a feed walk further apart than this
TAGS = ("Trending", "People asked for", "Danny's pick", "This season")
_ready = False


def _city():
    return (os.environ.get("FEED_CITY") or "Vienna").strip()[:60] or "Vienna"


def _min_people():
    try:
        return max(2, int(os.environ.get("FEED_MIN_PEOPLE", "3")))
    except ValueError:
        return 3


def _table():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS walk_feeds (
            id            TEXT PRIMARY KEY,
            status        TEXT NOT NULL,
            items_json    TEXT NOT NULL,
            note          TEXT,
            created_at    TEXT NOT NULL,
            published_at  TEXT
        )""")
    _ready = True


def _load(status):
    _table()
    row = db.query_one("SELECT id, status, items_json, note, created_at, published_at FROM walk_feeds "
                       "WHERE status = :s ORDER BY created_at DESC LIMIT 1", s=status)
    if row:
        row["items"] = json.loads(row["items_json"])
    return row


def published_feed():
    """The live feed's items for the walk page, or [] (never breaks the page)."""
    try:
        row = _load("published")
    except Exception as exc:
        print("feed: could not load:", exc)
        return []
    return row["items"] if row else []


def feed_cards():
    """published_feed() plus what the walk pop-up on the stories feed shows: the walk's
    key, its route map (the directory's /embed/route-map) and how many people walked it."""
    items = [dict(it) for it in published_feed() if isinstance(it, dict) and it.get("stops")]
    base = wp.directory_url()
    for it in items:
        slugs = ",".join(s["slug"] for s in it["stops"])
        it["key"] = "walk:" + hashlib.sha1(slugs.encode()).hexdigest()[:16]
        it["map"] = (base + "/embed/route-map?" + urlencode(
            {"s": slugs, "t": it.get("title") or "", "style": "mansfield", "mode": "story", "safe": 0})) if base else ""
    counts = wp.walked_counts([it["key"] for it in items])
    for it in items:
        it["walked"] = counts.get(it["key"], 0)
    return items


def related_articles(limit=5):
    """Stories for the walk page's "This week" drawer, as full articles (so they can open
    in the article pop-up right there). The newest featured story (the admin's "Feature
    this story" box) leads; after it, travel stories, the ones about FEED_CITY first,
    newest first among equals. Never breaks the page."""
    try:
        from content import get_articles, get_featured
        city = _city().lower()
        everything = get_articles()
        lead = (get_featured(everything) or [None])[0]   # newest first, so this is the most recent featured story
        scored = []
        for i, a in enumerate(everything):
            if lead is not None and a.get("slug") == lead.get("slug"):
                continue
            text = " ".join(str(a.get(k) or "") for k in ("title", "dek", "subcategory")).lower()
            score = (2 if a.get("category") == "travel" else 0) + (3 if city in text else 0)
            if score:
                scored.append((-score, i, a))
        scored.sort(key=lambda x: (x[0], x[1]))
        rest = [a for _, _, a in scored]
        return ([lead] + rest[:limit - 1]) if lead is not None else rest[:limit]
    except Exception as exc:
        print("feed: could not pick related reads:", exc)
        return []


def related_reads(limit=5, articles=None):
    """The drawer's "Good reads" cards: just what a card shows. A featured story is marked
    `lead` (it shows big, with its subheading); only the first one in the list can lead."""
    items = related_articles(limit) if articles is None else articles
    return [{"slug": a["slug"], "title": a.get("title") or "", "pill": a.get("pill") or "",
             "dek": a.get("dek") or "", "lead": i == 0 and bool(a.get("featured")),
             "image": a.get("cover_image") or "", "minutes": a.get("read_minutes") or 0}
            for i, a in enumerate(items)]


# ── 1 + 2: what goes into the prompt ──────────────────────────────────────

def _signals():
    """This week's trends, only where enough different people took part."""
    data = wp._insights(7)
    need = _min_people()
    return {
        "people": data["people"],
        "places": [p for p in data["places"] if p["people"] >= need][:12],
        "walks": [w for w in data["walks"] if w["people"] >= need][:6],
        "searches": [s for s in data["searches"] if s["people"] >= need][:8],
    }


def _directory_places(city):
    base, key = wp.directory_url(), (os.environ.get("WALK_PLAN_KEY") or "").strip()
    if not base or not key:
        raise wp.WalkError("The walk planner isn't set up yet, so there are no places to build a feed from.")
    out = {}
    for extra in ({}, {"min_rating": 4.4}):
        params = {"near": city, "city": city, "radius": 6000, "limit": 60, **extra}
        try:
            r = requests.get(base + "/embed/places-near?" + urlencode(params), headers={"X-Walk-Key": key}, timeout=20)
            data = r.json() if r.status_code < 300 else {}
        except (requests.RequestException, ValueError):
            data = {}
        for p in data.get("places") or []:
            if isinstance(p, dict) and wp.SLUG.match(str(p.get("slug") or "")) and p.get("lat") is not None:
                out.setdefault(p["slug"], p)
    if not out:
        raise wp.WalkError("The directory couldn't be reached, or has no places in " + city + " yet.")
    return out


def _season(today):
    m, d = today.month, today.day
    if m == 12 or (m == 11 and d >= 15):
        return "Advent: Christmas markets, punsch, warm cafés, early dark evenings"
    if m in (1, 2):
        return "deep winter and ball season: cosy indoor places, coffee houses, museums"
    if m in (3, 4, 5):
        return "spring: first terraces and gardens open, Easter markets"
    if m in (6, 7, 8):
        return "summer: outdoor seating, the Danube Canal, ice cream, long evenings"
    return "autumn: Heuriger and new wine (Sturm), chestnuts, cooler walks"


FEED_SYSTEM = """You write the weekly "This week" feed for a city walking app. The app's places are \
picked and rated by its curator, CURATOR. Make 3 to 5 short walks for readers to try this week.

Return JSON only: {"items": [{"tag": one of TAGS, "title": "...", "blurb": "...", "stops": ["slug", ...]}]}

Rules:
- Use ONLY slugs from the candidate list. Never invent a place or a fact about one.
- Each walk has 2 to 4 stops that are close together (compare their coordinates; keep each walk within about 1.5 km).
- "Trending" and "People asked for" only when the numbers below show it. If there are no numbers, don't use those tags.
- Include at least one "CURATOR's pick" walk built around places CURATOR rated or wrote about.
- Use "This season" for a walk that fits the time of year.
- title: at most 45 characters, specific and inviting (e.g. "Warm-up coffee crawl in the 7th").
- blurb: one or two sentences, at most 150 characters, friendly, no hype, no emojis. You can quote a few words of CURATOR's take.
- Don't repeat the same place in more than two walks.
- Favour places walkers rated worth the walk (4 or more footprints out of 5). Avoid places walkers rated under 3 when 3 or more rated them."""


def _prompt(city, today, places, signals):
    lines = [f"City: {city}", f"Today: {today.isoformat()} ({today.strftime('%A')}). Season: {_season(today)}.", ""]
    if signals["places"] or signals["walks"] or signals["searches"]:
        lines.append(f"What readers did in the last 7 days ({signals['people']} people):")
        for p in signals["places"]:
            lines.append(f"- place {p['slug']}: {p['people']} people; " + ", ".join(f"{n} {k}" for k, n in p["in"].items()))
        for w in signals["walks"]:
            lines.append(f"- walk {' > '.join(w['places'])}: {w['people']} people; " + ", ".join(f"{n} {k}" for k, n in w["in"].items()))
        for s in signals["searches"]:
            lines.append(f"- searched for \"{s['text'][:80]}\": {s['people']} people")
    else:
        lines.append("What readers did in the last 7 days: not enough activity yet (don't use Trending or People asked for).")
    lines += ["", "Candidates (slug | name | category | rating | lng,lat | curator | walkers: footprints out of 5 for being worth the walk):"]
    for p in places:
        bits = [p["slug"], wp._text(p.get("name"), 60), wp._text(p.get("category"), 30).replace("_", " "),
                str(p.get("rating") or "-"), f"{float(p['lng']):.4f},{float(p['lat']):.4f}"]
        mine = []
        if p.get("myRating"):
            mine.append(f"rated {p['myRating']}/5")
        if p.get("myTake"):
            mine.append("take: " + wp._text(p["myTake"], 160))
        bits.append("; ".join(mine) or "-")
        bits.append(f"{p['walkerAvg']}/5 from {p['walkerN']} walkers" if p.get("walkerN") else "-")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


# ── 3: the AI's walks, checked ────────────────────────────────────────────

def _spread(stops):
    return max((wp._metres((a["lng"], a["lat"]), (b["lng"], b["lat"])) for a in stops for b in stops), default=0)


def _clean_items(raw, places):
    curator_tag = f"{wp.CURATOR}'s pick"
    tags = {t.replace("Danny", wp.CURATOR) for t in TAGS}
    items, used = [], {}
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        stops, seen = [], set()
        for slug in it.get("stops") or []:
            slug = str(slug)
            if slug in places and slug not in seen and used.get(slug, 0) < 2:
                seen.add(slug)
                stops.append(places[slug])
        if len(stops) < 2:
            continue
        stops = stops[:4]
        if _spread(stops) > MAX_SPREAD_M:
            continue
        stops = wp.walking_order(stops, None)
        for s in stops:
            used[s["slug"]] = used.get(s["slug"], 0) + 1
        tag = str(it.get("tag") or "")
        tag = tag if tag in tags else ("This season" if tag else curator_tag)
        title = wp._text(it.get("title"), 60) or "A walk to try"
        items.append({
            "tag": tag, "title": title, "blurb": wp._text(it.get("blurb"), 200),
            "stops": [{"slug": s["slug"], "name": wp._text(s.get("name"), 80)} for s in stops],
            "link": "/walk?" + urlencode({"s": ",".join(s["slug"] for s in stops), "t": title}),
        })
        if len(items) >= MAX_ITEMS:
            break
    return items


def make_draft():
    """Build a new draft with the AI. Returns (items, note). Raises WalkError."""
    if not ask_engine.enabled():
        raise wp.WalkError("The AI isn't set up (no API key), so a feed can't be written.")
    city, today = _city(), date.today()
    places = _directory_places(city)
    try:   # what walkers rated each place (footprints.py); rated places go first so they're among the candidates
        import footprints
        footprints.attach(places.values())
    except Exception as exc:  # noqa: BLE001
        print("feed: footprints unavailable:", exc)
    candidates = sorted(places.values(), key=lambda p: (0, -p["walkerScore"]) if p.get("walkerN") else (1, 0))[:80]
    signals = _signals()
    system = FEED_SYSTEM.replace("CURATOR", wp.CURATOR).replace("TAGS", ", ".join(
        '"' + t.replace("Danny", wp.CURATOR) + '"' for t in TAGS))
    data = ask_engine._post("/chat/completions", {
        "model": os.environ.get("WALK_MODEL", "").strip() or ask_engine.chat_model(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": _prompt(city, today, candidates, signals)}],
        "temperature": 0.5, "max_tokens": 1200, "response_format": {"type": "json_object"},
    }, timeout=60)
    try:
        out = json.loads(data["choices"][0]["message"]["content"] or "{}")
    except (KeyError, IndexError, TypeError, ValueError):
        raise wp.WalkError("The AI sent back something unexpected. Try again.")
    items = _clean_items(out.get("items") if isinstance(out, dict) else None, places)
    if not items:
        raise wp.WalkError("The AI's walks didn't hold up (unknown places or too spread out). Try again.")
    used = sum(1 for k in ("places", "walks", "searches") if signals[k])
    note = (f"Built from {signals['people']} people's activity this week, your picks and the season."
            if used else "Not enough activity yet for trends: built from your picks and the season.")
    return items, note


# ── 4: the owner's page ───────────────────────────────────────────────────

@feed_bp.get("/admin/walk-feed")
@owner_required
def feed_admin():
    try:
        live, draft = _load("published"), _load("draft")
    except Exception as exc:
        print("feed: could not load:", exc)
        live = draft = None
    return render_template("admin/walk_feed.html", live=live, draft=draft, city=_city(),
                           min_people=_min_people(), csrf=csrf_token())


@feed_bp.post("/admin/walk-feed/<action>")
@owner_required
def feed_action(action):
    if not _csrf_ok():
        abort(400)
    _table()
    if action == "generate":
        try:
            items, note = make_draft()
        except (wp.WalkError, ask_engine.AskError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("walk_feed.feed_admin"))
        except Exception as exc:
            print("feed: draft failed:", exc)
            flash("Couldn't write a draft right now. Try again in a moment.", "error")
            return redirect(url_for("walk_feed.feed_admin"))
        db.execute("DELETE FROM walk_feeds WHERE status = 'draft'")
        db.execute("INSERT INTO walk_feeds (id, status, items_json, note, created_at) VALUES (:id, 'draft', :j, :n, :t)",
                   id=db.new_id(), j=json.dumps(items), n=note, t=db.now_iso())
        flash("New draft ready. Readers won't see it until you publish.", "success")
    elif action in ("save", "publish"):
        draft = _load("draft")
        if not draft:
            flash("There's no draft to save.", "error")
            return redirect(url_for("walk_feed.feed_admin"))
        items = []
        for i, it in enumerate(draft["items"]):
            if request.form.get(f"remove_{i}"):
                continue
            it["title"] = wp._text(request.form.get(f"title_{i}"), 60) or it["title"]
            it["blurb"] = wp._text(request.form.get(f"blurb_{i}"), 200)
            it["link"] = "/walk?" + urlencode({"s": ",".join(s["slug"] for s in it["stops"]), "t": it["title"]})
            items.append(it)
        if action == "save" or not items:
            db.execute("UPDATE walk_feeds SET items_json = :j WHERE id = :id", j=json.dumps(items), id=draft["id"])
            flash("Draft saved." if items else "Every walk was removed, so nothing was published.", "success" if items else "error")
        else:
            db.execute("UPDATE walk_feeds SET status = 'archived' WHERE status = 'published'")
            db.execute("UPDATE walk_feeds SET items_json = :j, status = 'published', published_at = :t WHERE id = :id",
                       j=json.dumps(items), t=db.now_iso(), id=draft["id"])
            flash("Published. It's on the Plan a walk page now.", "success")
    elif action == "unpublish":
        db.execute("UPDATE walk_feeds SET status = 'archived' WHERE status = 'published'")
        flash("Taken down. The Plan a walk page shows no feed until you publish again.", "success")
    elif action == "discard":
        db.execute("DELETE FROM walk_feeds WHERE status = 'draft'")
        flash("Draft discarded.", "success")
    else:
        abort(404)
    return redirect(url_for("walk_feed.feed_admin"))
