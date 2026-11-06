"""
"Ask" -- the chat screen that answers questions from the published articles.

The engine is in ask_engine.py; this file is the two web endpoints the chat
screen (static/js/ask.js) talks to, plus the limits that keep it from running
up the OpenAI bill:

  * visitors who aren't signed in get ASK_LIMIT_ANON questions a day (default 5),
    counted per network address;
  * signed-in readers get ASK_LIMIT_READER (default 20);
  * the admin gets ASK_LIMIT_ADMIN (default 200);
  * everyone together is held to ASK_DAILY_CAP questions a day (default 300).
    The per-person limits can be dodged (changing network address); this one
    cannot, so it is the real guard on cost.
Days roll over at midnight UTC.
"""
import hashlib
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session
from sqlalchemy.exc import IntegrityError

import ask_engine
import db
from content import get_articles
from readers import _csrf_ok, current_reader

ask_bp = Blueprint("ask", __name__)

MAX_QUESTION = 500


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _who():
    """(usage key, daily limit) for whoever is asking."""
    if session.get("is_admin"):
        return "admin", _int_env("ASK_LIMIT_ADMIN", 200)
    reader = current_reader()
    if reader:
        return "r:" + reader["id"], _int_env("ASK_LIMIT_READER", 20)
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = forwarded or request.remote_addr or "unknown"
    digest = hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]
    return "ip:" + digest, _int_env("ASK_LIMIT_ANON", 5)


def _used(key):
    row = db.query_one("SELECT n FROM ask_usage WHERE day = :d AND key = :k", d=_today(), k=key)
    return row["n"] if row else 0


def _count(key):
    day = _today()
    if db.execute("UPDATE ask_usage SET n = n + 1 WHERE day = :d AND key = :k", d=day, k=key):
        return
    try:
        db.execute("INSERT INTO ask_usage (day, key, n) VALUES (:d, :k, 1)", d=day, k=key)
    except IntegrityError:  # another request created the row a moment ago
        db.execute("UPDATE ask_usage SET n = n + 1 WHERE day = :d AND key = :k", d=day, k=key)


def _remaining(key, limit):
    return max(0, limit - _used(key))


@ask_bp.get("/api/ask/status")
def ask_status():
    key, limit = _who()
    titles = [a["title"] for a in get_articles()[:3]]
    return jsonify(
        enabled=ask_engine.enabled(),
        suggestions=[f"What is “{t}” about?" for t in titles],
        remaining=_remaining(key, limit), limit=limit)


@ask_bp.post("/api/ask")
def ask():
    if not _csrf_ok():
        return jsonify(ok=False, error="Your session expired. Reload the page and try again."), 400
    if not ask_engine.enabled():
        return jsonify(ok=False, error="Search isn't switched on yet. Check back soon."), 503

    body = request.get_json(silent=True) or {}
    question = " ".join(str(body.get("question") or "").split())
    if not question:
        return jsonify(ok=False, error="Type a question first."), 400
    if len(question) > MAX_QUESTION:
        return jsonify(ok=False, error=f"Please keep questions under {MAX_QUESTION} characters."), 400

    key, limit = _who()
    if _used(key) >= limit:
        msg = "You've used today's questions. Come back tomorrow"
        msg += ", or sign in for more." if key.startswith("ip:") else "."
        return jsonify(ok=False, remaining=0, limit=limit, error=msg), 429
    if _used("__global__") >= _int_env("ASK_DAILY_CAP", 300):
        return jsonify(ok=False, error="Search has reached its limit for today. Please try again tomorrow."), 429

    _count(key)
    _count("__global__")
    try:
        result = ask_engine.answer(question, body.get("history"))
    except ask_engine.AskError as exc:
        return jsonify(ok=False, error=str(exc), remaining=_remaining(key, limit), limit=limit), 502
    except Exception as exc:
        print("ask: unexpected failure:", exc)
        return jsonify(ok=False, error="Something went wrong. Please try again.",
                       remaining=_remaining(key, limit), limit=limit), 500

    return jsonify(ok=True, remaining=_remaining(key, limit), limit=limit, **result)
