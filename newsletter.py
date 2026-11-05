"""
Newsletter signup for /Column.

Collects a name, profession and email from the footer form, the /newsletter
page and the timed pop-up, and stores them in the same database as reader
accounts (db.py, table newsletter_subscribers). Nothing is emailed from here
yet -- the admin portal (/admin/subscribers) lists everyone and exports a CSV
that can be imported into whatever tool does the sending later.

One endpoint does the work: POST /newsletter/subscribe. The pop-up and the
forms call it with JavaScript (and get JSON back); with JavaScript off it
still works as a normal form post and shows the /newsletter page with a
message.
"""
from flask import Blueprint, jsonify, render_template, request
from sqlalchemy.exc import IntegrityError

import db
import newsletter_send
from content import CATEGORIES
from readers import EMAIL_RE, _csrf_ok

newsletter_bp = Blueprint("newsletter", __name__)

MAX_NAME = 80
MAX_PROFESSION = 80
SOURCES = {"popup", "footer", "page"}


def _clean(value, limit):
    return " ".join((value or "").split())[:limit]


def _wants_json():
    # The pop-up and forms ask for JSON explicitly (fetch with an Accept header).
    return "application/json" in (request.headers.get("Accept") or "")


@newsletter_bp.get("/newsletter")
def newsletter_page():
    return render_template("newsletter.html", form={}, error=None, joined=False,
                           categories=CATEGORIES, active_category=None)


@newsletter_bp.post("/newsletter/subscribe")
def subscribe():
    as_json = _wants_json()

    def respond(ok, message, status=200, form=None):
        if as_json:
            return jsonify(ok=ok, message=message), status
        return render_template("newsletter.html", form=form or {},
                               error=None if ok else message, joined=ok,
                               message=message, categories=CATEGORIES,
                               active_category=None), status

    if not _csrf_ok():
        return respond(False, "Your session expired -- please reload the page and try again.", 400)

    # Honeypot: a hidden field real visitors never fill in. Bots do. Pretend
    # it worked so they don't learn anything, and store nothing.
    if request.form.get("website"):
        return respond(True, "You're on the list. Thanks for signing up!")

    name = _clean(request.form.get("name"), MAX_NAME)
    profession = _clean(request.form.get("profession"), MAX_PROFESSION)
    email = (request.form.get("email") or "").strip().lower()
    source = request.form.get("source") or "page"
    if source not in SOURCES:
        source = "page"
    form = {"name": name, "profession": profession, "email": email}

    if not email or not EMAIL_RE.match(email) or len(email) > 254:
        return respond(False, "That doesn't look like a valid email address.", 400, form)

    try:
        db.execute(
            "INSERT INTO newsletter_subscribers (id, email, name, profession, source, created_at) "
            "VALUES (:id, :email, :name, :profession, :source, :t)",
            id=db.new_id(), email=email, name=name, profession=profession,
            source=source, t=db.now_iso())
    except IntegrityError:
        # Already subscribed. Say the same thing as a brand-new signup so this
        # form can't be used to check whether someone's email is on the list.
        pass
    except Exception as exc:  # database trouble: tell the reader, don't 500
        print("newsletter: could not save subscriber:", exc)
        return respond(False, "Something went wrong saving that. Please try again in a moment.", 500, form)

    # Signing up again after unsubscribing is an explicit "yes, send me mail".
    try:
        db.execute("DELETE FROM newsletter_unsubscribes WHERE email = :e", e=email)
    except Exception as exc:
        print("newsletter: could not clear unsubscribe:", exc)

    return respond(True, "You're on the list. Thanks for signing up!")


# ── Unsubscribe ──────────────────────────────────────────────────────────
# The link in every email carries the address and a signature made with the
# server's secret key (newsletter_send.unsub_token), so it can't be forged.
# GET only shows a confirm button -- link scanners in mail apps open links
# without anyone clicking, and must not unsubscribe people. The POST does the
# work; mail apps' one-click "Unsubscribe" button also POSTs to this URL
# (List-Unsubscribe-Post header), which is why it has no CSRF check: the
# signature is the protection.

def _unsub_page(email, state):
    return render_template("newsletter_unsubscribe.html", email=email, state=state,
                           token=request.args.get("t", ""), categories=CATEGORIES, active_category=None)


@newsletter_bp.get("/newsletter/unsubscribe")
def unsubscribe_page():
    email = (request.args.get("e") or "").strip().lower()
    if not newsletter_send.token_ok(email, request.args.get("t")):
        return _unsub_page("", "invalid"), 400
    return _unsub_page(email, "confirm")


@newsletter_bp.post("/newsletter/unsubscribe")
def unsubscribe_submit():
    email = (request.args.get("e") or request.form.get("e") or "").strip().lower()
    token = request.args.get("t") or request.form.get("t")
    if not newsletter_send.token_ok(email, token):
        return _unsub_page("", "invalid"), 400
    try:
        db.execute("INSERT INTO newsletter_unsubscribes (email, created_at) VALUES (:e, :t)",
                   e=email, t=db.now_iso())
    except IntegrityError:
        pass  # already unsubscribed
    except Exception as exc:
        print("newsletter: could not unsubscribe:", exc)
        return _unsub_page(email, "error"), 500
    return _unsub_page(email, "done")
