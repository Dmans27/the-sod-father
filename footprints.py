"""
Footprints: walkers rate places from 1 to 5 footprints -- how worth the walk a place is.

  GET  /api/footprints?s=slug,slug     {signedIn, mine: {slug: steps}, stats: {slug: {avg, n}}}
  POST /api/footprints                 {slug, steps 1-5 (0 takes your rating back), visited?, walk?, name?}
  GET  /admin/footprints               the owner's leaderboard of places walkers rated
  POST /admin/footprints/summary       an AI summary of the leaderboard (kept, newest shown)

Anyone signed in can rate, from the walk's list of stops or from the "You're at stop 2"
card when they reach a stop (the map sends mansfield:rate). A rating from someone who
reached the stop on a walk is marked visited and counts double (VISITED_WEIGHT).

What the numbers are:
  avg    the plain weighted average (shown to everyone: "4.6 · 3 walkers")
  score  the same pulled towards PRIOR_MEAN until there are enough ratings (a Bayesian
         average), so one 5-footprint rating can't top the leaderboard. Used for ranking,
         by the walk planner (walk_planner._score) and the weekly feed (walk_feed._prompt).

Places are the directory's listing slugs. Tables are created on first use.
"""
import os
import re

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import ask_engine
import db
from admin import owner_required
from readers import _csrf_ok, csrf_token, current_reader, reader_required_json

footprints_bp = Blueprint("footprints", __name__)

VISITED_WEIGHT = 2
PRIOR_MEAN = 3.5
PRIOR_N = 3
MAX_SLUGS = 40
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
_ready = False


def _tables():
    global _ready
    if _ready:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS place_footprints (
            slug        TEXT NOT NULL,
            reader_id   TEXT NOT NULL,
            steps       INTEGER NOT NULL,
            visited     INTEGER NOT NULL DEFAULT 0,
            walk_key    TEXT,
            name        TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (slug, reader_id)
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS footprint_summaries (
            id          TEXT PRIMARY KEY,
            body        TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )""")
    _ready = True


def _clean_slugs(raw):
    out = []
    for s in raw:
        s = str(s or "").strip()
        if _SLUG_RE.match(s) and s not in out:
            out.append(s)
    return out[:MAX_SLUGS]


def _numbers(total_w, sum_w, n, visited):
    avg = sum_w / total_w if total_w else 0
    score = (PRIOR_N * PRIOR_MEAN + sum_w) / (PRIOR_N + total_w)
    return {"avg": round(avg, 1), "n": int(n), "visited": int(visited), "score": round(score, 2)}


_AGG = (f"SUM(CASE WHEN visited = 1 THEN {VISITED_WEIGHT} ELSE 1 END) AS tw, "
        f"SUM(steps * CASE WHEN visited = 1 THEN {VISITED_WEIGHT} ELSE 1 END) AS sw, "
        "COUNT(*) AS n, SUM(visited) AS v")


def stats(slugs):
    """{slug: {avg, n, visited, score}} for the slugs that have ratings. Never fails."""
    slugs = _clean_slugs(slugs)
    if not slugs:
        return {}
    try:
        _tables()
        params = {f"s{i}": s for i, s in enumerate(slugs)}
        rows = db.query(f"SELECT slug, {_AGG} FROM place_footprints WHERE slug IN ("
                        + ", ".join(":" + k for k in params) + ") GROUP BY slug", **params)
        return {r["slug"]: _numbers(float(r["tw"] or 0), float(r["sw"] or 0), r["n"] or 0, r["v"] or 0) for r in rows}
    except Exception as exc:  # noqa: BLE001
        print("footprints: could not read ratings:", exc)
        return {}


def attach(places):
    """Add walkerAvg / walkerN / walkerScore to directory places (dicts with a slug), in place."""
    places = list(places or [])
    found = {}
    for i in range(0, len(places), MAX_SLUGS):
        found.update(stats([p.get("slug") for p in places[i:i + MAX_SLUGS]]))
    for p in places:
        st = found.get(p.get("slug"))
        if st:
            p["walkerAvg"], p["walkerN"], p["walkerScore"] = st["avg"], st["n"], st["score"]
    return places


def leaderboard(limit=200):
    _tables()
    rows = db.query(f"SELECT slug, MAX(name) AS name, MAX(updated_at) AS last, {_AGG} FROM place_footprints GROUP BY slug")
    out = []
    for r in rows:
        item = _numbers(float(r["tw"] or 0), float(r["sw"] or 0), r["n"] or 0, r["v"] or 0)
        item.update(slug=r["slug"], name=r["name"] or r["slug"], last=(r["last"] or "")[:10])
        out.append(item)
    out.sort(key=lambda x: (-x["score"], -x["n"]))
    return out[:limit]


# ── API ──────────────────────────────────────────────────────────────────

@footprints_bp.get("/api/footprints")
def api_get():
    slugs = _clean_slugs((request.args.get("s") or "").split(","))
    reader = current_reader()
    mine = {}
    if reader and slugs:
        try:
            _tables()
            params = {f"s{i}": s for i, s in enumerate(slugs)}
            rows = db.query("SELECT slug, steps FROM place_footprints WHERE reader_id = :r AND slug IN ("
                            + ", ".join(":" + k for k in params) + ")", r=reader["id"], **params)
            mine = {r["slug"]: int(r["steps"]) for r in rows}
        except Exception as exc:  # noqa: BLE001
            print("footprints: could not read the reader's ratings:", exc)
    public = {k: {"avg": v["avg"], "n": v["n"]} for k, v in stats(slugs).items()}
    reply = jsonify(signedIn=bool(reader), mine=mine, stats=public)
    reply.headers["Cache-Control"] = "no-store"
    return reply


@footprints_bp.post("/api/footprints")
@reader_required_json
def api_rate():
    reader = current_reader()
    data = request.get_json(silent=True) or {}
    slug = str(data.get("slug") or "")
    if not _SLUG_RE.match(slug):
        return jsonify(error="bad_place"), 400
    try:
        steps = int(data.get("steps"))
    except (TypeError, ValueError):
        return jsonify(error="bad_steps"), 400
    if not 0 <= steps <= 5:
        return jsonify(error="bad_steps"), 400
    _tables()
    if steps == 0:
        db.execute("DELETE FROM place_footprints WHERE slug = :s AND reader_id = :r", s=slug, r=reader["id"])
    else:
        visited = 1 if data.get("visited") else 0
        walk = str(data.get("walk") or "")
        walk = walk if re.match(r"^walk:[0-9a-f]{16}$", walk) else None
        name = " ".join(str(data.get("name") or "").split())[:120] or None
        now = db.now_iso()
        done = db.execute(
            "UPDATE place_footprints SET steps = :st, visited = CASE WHEN visited = 1 THEN 1 ELSE :v END, "
            "walk_key = COALESCE(:w, walk_key), name = COALESCE(:nm, name), updated_at = :t "
            "WHERE slug = :s AND reader_id = :r", st=steps, v=visited, w=walk, nm=name, t=now, s=slug, r=reader["id"])
        if not done:
            db.execute("INSERT INTO place_footprints (slug, reader_id, steps, visited, walk_key, name, created_at, updated_at) "
                       "VALUES (:s, :r, :st, :v, :w, :nm, :t, :t)", s=slug, r=reader["id"], st=steps, v=visited, w=walk, nm=name, t=now)
    st = stats([slug]).get(slug)
    return jsonify(ok=True, slug=slug, steps=steps, stats={"avg": st["avg"], "n": st["n"]} if st else None)


def delete_reader_ratings(reader_id):
    _tables()
    db.execute("DELETE FROM place_footprints WHERE reader_id = :r", r=reader_id)


# ── The owner's leaderboard ──────────────────────────────────────────────

SUMMARY_SYSTEM = """You help the owner of a city walking app understand how walkers rate places.
Walkers give each place 1 to 5 footprints: how worth the walk it is. "score" is the average pulled
towards 3.5 until a place has enough ratings (use it for ranking); "avg" is the plain average; "n" is
how many walkers rated it; "visited" is how many of them rated it after actually walking there.

Write a short report in plain text (no markdown tables, no emojis), with these parts:
Worth the walk: the 5 strongest places, one line each, saying why from the numbers.
Hidden gems: high averages but few ratings, worth featuring so more people try them.
Not loved: places walkers rated low (avg under 3 with at least 3 ratings), if any.
For next week: one or two concrete ideas for the weekly walk feed built around these places.
Use ONLY the numbers given. Never invent facts about the places. Keep it under 250 words."""


@footprints_bp.get("/admin/footprints")
@owner_required
def admin_board():
    board = leaderboard()
    latest = db.query_one("SELECT body, created_at FROM footprint_summaries ORDER BY created_at DESC LIMIT 1")
    total = sum(p["n"] for p in board)
    return render_template("admin/footprints.html", board=board, latest=latest, total=total,
                           ai=ask_engine.enabled(), csrf=csrf_token(), visited_weight=VISITED_WEIGHT)


@footprints_bp.post("/admin/footprints/summary")
@owner_required
def admin_summary():
    if not _csrf_ok():
        abort(400)
    board = leaderboard()
    if not board:
        flash("No ratings yet, so there's nothing to summarise.", "warning")
        return redirect(url_for("footprints.admin_board"))
    if not ask_engine.enabled():
        flash("The AI isn't set up (OPENAI_API_KEY), so it can't write a summary.", "error")
        return redirect(url_for("footprints.admin_board"))
    lines = [f"{p['name']} | score {p['score']} | avg {p['avg']} | n {p['n']} | visited {p['visited']}" for p in board[:60]]
    try:
        data = ask_engine._post("/chat/completions", {
            "model": os.environ.get("WALK_MODEL", "").strip() or ask_engine.chat_model(),
            "messages": [{"role": "system", "content": SUMMARY_SYSTEM},
                         {"role": "user", "content": "Places walkers rated (best first):\n" + "\n".join(lines)}],
            "temperature": 0.3, "max_tokens": 700,
        }, timeout=45)
        body = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:  # noqa: BLE001
        print("footprints: summary failed:", exc)
        flash("The AI couldn't write a summary just now. Try again in a minute.", "error")
        return redirect(url_for("footprints.admin_board"))
    if body:
        db.execute("INSERT INTO footprint_summaries (id, body, created_at) VALUES (:id, :b, :t)",
                   id=db.new_id(), b=body[:6000], t=db.now_iso())
        flash("New summary written.", "success")
    return redirect(url_for("footprints.admin_board"))
