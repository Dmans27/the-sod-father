"""
Safety and trust pages the App Store (and plain good manners) require:

  GET  /privacy, /terms                  the two legal pages
  GET/POST /contact                      contact form -> saved for the owner (no email address is published)
  GET/POST /account/delete               delete your account and everything attached to it
  GET  /account/blocked                  readers you have blocked
  POST /account/blocked/<id>/unblock
  POST /api/comments/<id>/report         report a comment (hidden after 3 reports until reviewed)
  POST /api/comments/<id>/block          stop seeing one reader's comments

  /admin/reports, /admin/messages        the owner's review screens (admin password required)

The rules themselves (how many reports hide a comment, the word filter, what
deleting an account removes) live in moderation.py.
"""
import time

from flask import (Blueprint, g, jsonify, redirect, render_template, request,
                   session, url_for, flash, abort)
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash

import db
import moderation
from admin import login_required
from content import CATEGORIES, get_article
from readers import (EMAIL_RE, _csrf_ok, current_reader, reader_required_json)

safety_bp = Blueprint("safety", __name__)
review_bp = Blueprint("review", __name__, url_prefix="/admin")

CONTACT_MAX_NAME = 80
CONTACT_MAX_MESSAGE = 2000
CONTACT_MIN_MESSAGE = 10
CONTACT_PER_HOUR = 5
OPERATOR = "Daniel Mansfield"
LEGAL_UPDATED = "November 2026"

# Per-process brake on the contact form (same idea as the login throttle in readers.py).
_contact_hits = {}


def _page(template, **ctx):
    ctx.setdefault("categories", CATEGORIES)
    ctx.setdefault("active_category", None)
    return render_template(template, **ctx)


def _client_ip():
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or request.remote_addr or "?"


def _clean(value, limit):
    return " ".join((value or "").split())[:limit]


# ── Legal pages ──────────────────────────────────────────────────────────

@safety_bp.get("/privacy")
def privacy():
    return _page("legal/privacy.html", updated=LEGAL_UPDATED, operator=OPERATOR)


@safety_bp.get("/terms")
def terms():
    return _page("legal/terms.html", updated=LEGAL_UPDATED, operator=OPERATOR,
                 hide_after=moderation.HIDE_AFTER_REPORTS)


# ── Contact form ─────────────────────────────────────────────────────────

@safety_bp.get("/contact")
def contact():
    reader = current_reader()
    form = {"name": reader["display_name"], "email": reader["email"]} if reader else {}
    return _page("contact.html", form=form, sent=bool(request.args.get("sent")))


@safety_bp.post("/contact")
def contact_submit():
    if not _csrf_ok():
        return _page("contact.html", form={}, error="Your session expired. Please reload the page and try again."), 400

    # Honeypot: hidden field real people never fill in. Pretend it worked.
    if request.form.get("website"):
        return redirect(url_for("safety.contact", sent=1))

    name = _clean(request.form.get("name"), CONTACT_MAX_NAME)
    email = (request.form.get("email") or "").strip().lower()
    message = (request.form.get("message") or "").strip()
    form = {"name": name, "email": email, "message": message}

    error = None
    if not name:
        error = "Please tell us your name."
    elif not EMAIL_RE.match(email) or len(email) > 254:
        error = "That doesn't look like a valid email address, so we couldn't reply to it."
    elif len(message) < CONTACT_MIN_MESSAGE:
        error = "Please write a little more so we know how to help."
    elif len(message) > CONTACT_MAX_MESSAGE:
        error = f"That's too long ({CONTACT_MAX_MESSAGE:,} characters max)."
    if error:
        return _page("contact.html", form=form, error=error), 400

    ip = _client_ip()
    now = time.time()
    hits = [t for t in _contact_hits.get(ip, []) if now - t < 3600]
    if len(hits) >= CONTACT_PER_HOUR:
        return _page("contact.html", form=form,
                     error="You've sent a few messages already. Please try again later."), 429
    hits.append(now)
    _contact_hits[ip] = hits

    try:
        db.execute(
            "INSERT INTO contact_messages (id, name, email, message, status, created_at) "
            "VALUES (:id, :n, :e, :m, 'new', :t)",
            id=db.new_id(), n=name, e=email, m=message, t=db.now_iso())
    except Exception as exc:
        print("safety: could not save contact message:", exc)
        return _page("contact.html", form=form,
                     error="Something went wrong sending that. Please try again in a moment."), 500
    return redirect(url_for("safety.contact", sent=1))


# ── Delete my account ────────────────────────────────────────────────────

@safety_bp.get("/account/delete")
def delete_account_page():
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account/delete"))
    return _page("account/delete.html")


@safety_bp.post("/account/delete")
def delete_account_submit():
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account/delete"))
    if not _csrf_ok():
        return _page("account/delete.html", error="Your session expired. Please try again."), 400

    row = db.query_one("SELECT password_hash FROM readers WHERE id = :id", id=reader["id"])
    if not row or not check_password_hash(row["password_hash"], request.form.get("password") or ""):
        return _page("account/delete.html", error="That password doesn't match."), 400
    if request.form.get("confirm") != "yes":
        return _page("account/delete.html", error="Tick the box to confirm you want to delete your account."), 400

    try:
        moderation.delete_reader(
            reader["id"], email=reader["email"],
            remove_newsletter=request.form.get("newsletter") == "yes")
    except Exception as exc:
        print("safety: could not delete account:", exc)
        return _page("account/delete.html",
                     error="Something went wrong and your account was not fully deleted. Please try again."), 500

    session.pop("reader_id", None)
    session.pop("csrf", None)
    g.reader = None  # the page below should render signed out
    return _page("account/deleted.html")


# ── Blocked readers ──────────────────────────────────────────────────────

@safety_bp.get("/account/blocked")
def blocked_page():
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account/blocked"))
    rows = db.query(
        "SELECT b.blocked_id AS id, r.display_name AS name, b.created_at "
        "FROM reader_blocks b JOIN readers r ON r.id = b.blocked_id "
        "WHERE b.reader_id = :r ORDER BY b.created_at DESC", r=reader["id"])
    return _page("account/blocked.html", blocked=rows)


@safety_bp.post("/account/blocked/<blocked_id>/unblock")
def unblock(blocked_id):
    reader = current_reader()
    if not reader:
        return redirect(url_for("readers.login", next="/account/blocked"))
    if _csrf_ok():
        db.execute("DELETE FROM reader_blocks WHERE reader_id = :r AND blocked_id = :b",
                   r=reader["id"], b=blocked_id)
    return redirect(url_for("safety.blocked_page"))


# ── JSON API: report + block ─────────────────────────────────────────────

def _comment(comment_id):
    return db.query_one("SELECT id, reader_id, slug FROM comments WHERE id = :id", id=comment_id)


@safety_bp.post("/api/comments/<comment_id>/report")
@reader_required_json
def api_report(comment_id):
    reader = current_reader()
    comment = _comment(comment_id)
    if not comment:
        return jsonify(error="not_found"), 404
    if comment["reader_id"] == reader["id"]:
        return jsonify(error="own_comment"), 400
    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason")
    if reason not in moderation.REPORT_REASONS:
        reason = "other"
    try:
        db.execute(
            "INSERT INTO comment_reports (id, comment_id, reader_id, reason, created_at) "
            "VALUES (:id, :c, :r, :why, :t)",
            id=db.new_id(), c=comment_id, r=reader["id"], why=reason, t=db.now_iso())
    except IntegrityError:
        pass  # already reported by this reader -- counts once
    hidden = bool(db.query_one(
        "SELECT 1 AS x FROM comments c WHERE c.id = :id AND " + moderation.HIDDEN_SQL,
        id=comment_id, **moderation.HIDE_PARAMS))
    return jsonify(ok=True, hidden=hidden)


@safety_bp.post("/api/comments/<comment_id>/block")
@reader_required_json
def api_block(comment_id):
    reader = current_reader()
    comment = _comment(comment_id)
    if not comment:
        return jsonify(error="not_found"), 404
    if comment["reader_id"] == reader["id"]:
        return jsonify(error="own_comment"), 400
    try:
        db.execute(
            "INSERT INTO reader_blocks (reader_id, blocked_id, created_at) VALUES (:r, :b, :t)",
            r=reader["id"], b=comment["reader_id"], t=db.now_iso())
    except IntegrityError:
        pass
    ids = db.query("SELECT id FROM comments WHERE reader_id = :b", b=comment["reader_id"])
    return jsonify(ok=True, hidden_ids=[r["id"] for r in ids])


# ── Owner's review screens (admin password) ──────────────────────────────

@review_bp.get("/reports")
@login_required
def reports():
    rows = db.query(
        "SELECT c.id, c.slug, c.body, c.created_at, c.reader_id, r.display_name, r.email, "
        "(SELECT COUNT(*) FROM comment_reports x WHERE x.comment_id = c.id) AS n "
        "FROM comments c JOIN readers r ON r.id = c.reader_id "
        "WHERE EXISTS (SELECT 1 FROM comment_reports x WHERE x.comment_id = c.id) "
        "AND NOT EXISTS (SELECT 1 FROM comment_moderation m WHERE m.comment_id = c.id) "
        "ORDER BY n DESC, c.created_at DESC")
    reasons = {}
    for rr in db.query("SELECT comment_id, reason FROM comment_reports ORDER BY created_at"):
        reasons.setdefault(rr["comment_id"], []).append(rr["reason"])
    items = []
    for r in rows:
        article = get_article(r["slug"])
        items.append({
            **r,
            "n": int(r["n"]),
            "hidden": int(r["n"]) >= moderation.HIDE_AFTER_REPORTS,
            "title": (article or {}).get("title") or r["slug"],
            "reasons": [moderation.REPORT_REASONS.get(x, x) for x in reasons.get(r["id"], [])],
        })
    return render_template("admin/reports.html", items=items, hide_after=moderation.HIDE_AFTER_REPORTS)


@review_bp.post("/reports/<comment_id>/<action>")
@login_required
def reports_action(comment_id, action):
    if not _csrf_ok():
        abort(400)
    row = db.query_one("SELECT reader_id FROM comments WHERE id = :id", id=comment_id)
    if not row:
        flash("That comment no longer exists.", "error")
    elif action == "keep":
        try:
            db.execute("INSERT INTO comment_moderation (comment_id, status, created_at) VALUES (:c, 'cleared', :t)",
                       c=comment_id, t=db.now_iso())
        except IntegrityError:
            pass
        flash("Kept. The comment is visible again and further reports won't hide it.", "success")
    elif action == "delete":
        moderation.delete_comment(comment_id)
        flash("Comment deleted.", "success")
    elif action == "ban":
        reader = db.query_one("SELECT email FROM readers WHERE id = :id", id=row["reader_id"])
        if reader:
            try:
                db.execute("INSERT INTO banned_emails (email, created_at) VALUES (:e, :t)",
                           e=reader["email"].lower(), t=db.now_iso())
            except IntegrityError:
                pass
            moderation.delete_reader(row["reader_id"], email=reader["email"], remove_newsletter=False)
        flash("Reader banned: their account and comments were removed and that email can't sign up again.", "success")
    else:
        abort(404)
    return redirect(url_for("review.reports"))


@review_bp.get("/messages")
@login_required
def messages():
    rows = db.query("SELECT id, name, email, message, status, created_at FROM contact_messages "
                    "ORDER BY CASE WHEN status = 'new' THEN 0 ELSE 1 END, created_at DESC")
    return render_template("admin/messages.html", items=rows,
                           new_count=sum(1 for r in rows if r["status"] == "new"))


@review_bp.post("/messages/<message_id>/<action>")
@login_required
def messages_action(message_id, action):
    if not _csrf_ok():
        abort(400)
    if action == "done":
        db.execute("UPDATE contact_messages SET status = 'done' WHERE id = :id", id=message_id)
    elif action == "reopen":
        db.execute("UPDATE contact_messages SET status = 'new' WHERE id = :id", id=message_id)
    elif action == "delete":
        db.execute("DELETE FROM contact_messages WHERE id = :id", id=message_id)
    else:
        abort(404)
    return redirect(url_for("review.messages"))


def register(app):
    app.register_blueprint(safety_bp)
    app.register_blueprint(review_bp)
