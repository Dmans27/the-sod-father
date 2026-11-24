"""
Newsletter signup for \\\\Mansfield.

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
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, render_template, request
from PIL import Image, ImageOps
from sqlalchemy.exc import IntegrityError

import db
import newsletter_send
from content import CATEGORIES, get_article
from readers import EMAIL_RE, _csrf_ok, current_reader, reader_required_json

newsletter_bp = Blueprint("newsletter", __name__)

PROJECT_ROOT = Path(__file__).parent
UPLOADS_DIR = (PROJECT_ROOT / "static" / "uploads").resolve()

MAX_NAME = 80
MAX_PROFESSION = 80
SOURCES = {"popup", "footer", "page", "account"}


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


# ── The Newsletter switch on the account page ───────────────────────────
# On: if the reader's email is already on the list it's simply switched back on;
# if not, the page asks for their name and profession first (need: "details").
# Off: the same as the unsubscribe link in an email.

def is_subscribed(email):
    email = (email or "").strip().lower()
    if not email:
        return False
    try:
        on = db.query_one("SELECT 1 AS x FROM newsletter_subscribers WHERE email = :e", e=email)
        off = db.query_one("SELECT 1 AS x FROM newsletter_unsubscribes WHERE email = :e", e=email)
    except Exception as exc:
        print("newsletter: could not check subscription:", exc)
        return False
    return bool(on) and not off


@newsletter_bp.post("/api/newsletter/me")
@reader_required_json
def newsletter_me():
    reader = current_reader()
    email = (reader.get("email") or "").strip().lower()
    data = request.get_json(silent=True) or {}
    try:
        if not data.get("on"):
            try:
                db.execute("INSERT INTO newsletter_unsubscribes (email, created_at) VALUES (:e, :t)", e=email, t=db.now_iso())
            except IntegrityError:
                pass   # already off
            return jsonify(ok=True, on=False, message="You won't get the newsletter any more.")
        known = db.query_one("SELECT 1 AS x FROM newsletter_subscribers WHERE email = :e", e=email)
        if not known:
            name = _clean(data.get("name"), MAX_NAME)
            profession = _clean(data.get("profession"), MAX_PROFESSION)
            if not name or not profession:   # not on the list yet: ask for the details first
                return jsonify(ok=False, need="details", name=_clean(reader.get("display_name"), MAX_NAME))
            try:
                db.execute("INSERT INTO newsletter_subscribers (id, email, name, profession, source, created_at) "
                           "VALUES (:id, :email, :name, :profession, 'account', :t)",
                           id=db.new_id(), email=email, name=name, profession=profession, t=db.now_iso())
            except IntegrityError:
                pass
        db.execute("DELETE FROM newsletter_unsubscribes WHERE email = :e", e=email)
    except Exception as exc:
        print("newsletter: could not change the subscription:", exc)
        return jsonify(ok=False, error="Something went wrong. Please try again in a moment."), 500
    return jsonify(ok=True, on=True, message="You're on the list. New stories will come to " + email + ".")


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


# ── Cover photos for the email ───────────────────────────────────────────
# Phone photos come in every shape (and tall ones make an email very long),
# so emails use these evenly cropped versions instead of the originals.
# Only an existing story's own cover file can be requested, never a path.

IMAGE_VARIANTS = {"lead": (1360, 700), "thumb": (560, 420)}
_image_cache = {}


@newsletter_bp.get("/newsletter/img/<slug>/<variant>.jpg")
def newsletter_image(slug, variant):
    size = IMAGE_VARIANTS.get(variant)
    article = get_article(slug)
    cover = (article or {}).get("cover_image") or ""
    if not size or not cover.startswith("/static/uploads/"):
        abort(404)
    path = (PROJECT_ROOT / cover.lstrip("/")).resolve()
    if UPLOADS_DIR not in path.parents or not path.is_file():
        abort(404)

    key = (str(path), variant, int(path.stat().st_mtime))
    data = _image_cache.get(key)
    if data is None:
        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                img = ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.4))
            import io
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            data = buf.getvalue()
        except Exception as exc:
            print("newsletter: could not crop cover:", exc)
            abort(404)
        if len(_image_cache) > 40:
            _image_cache.clear()
        _image_cache[key] = data
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
