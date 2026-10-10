"""
Self-service author accounts -- separate from the /admin portal entirely.
An author signs up (behind a shared AUTHOR_SIGNUP_PASSWORD, so random
visitors can't add themselves to the site) to create a profile with a
photo and short bio, then signs in any time to update it. Danny is still
the only one who writes stories in /admin; he just picks a name from a
dropdown there and this is where that name's photo comes from.

Mirrors admin.py's patterns (same login_required-style guard, same
slugify/unique_slug approach, same story_store.save_* save flow) on
purpose, so there's only one way of doing things in this codebase.
"""
import os
import re
import secrets
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from authors import get_author, get_author_by_email, get_authors, public_fields
import story_store

author_bp = Blueprint("author_auth", __name__, url_prefix="/authors")

MIN_PASSWORD_LENGTH = 8


def author_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("author_slug"):
            return redirect(url_for("author_auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "author"


def _unique_slug(base_slug, taken_slugs):
    if base_slug not in taken_slugs:
        return base_slug
    n = 2
    while f"{base_slug}-{n}" in taken_slugs:
        n += 1
    return f"{base_slug}-{n}"


# ── Sign up ──────────────────────────────────────────────────────────────

@author_bp.get("/signup")
def signup():
    if not os.environ.get("AUTHOR_SIGNUP_PASSWORD"):
        return render_template("authors/signup.html", not_configured=True)
    return render_template("authors/signup.html", not_configured=False)


@author_bp.post("/signup")
def signup_submit():
    configured = os.environ.get("AUTHOR_SIGNUP_PASSWORD")
    if not configured:
        return render_template("authors/signup.html", not_configured=True)

    form = request.form
    errors = []

    if not secrets.compare_digest(form.get("signup_password", ""), configured):
        errors.append("That sign-up password isn't right -- check with whoever invited you.")

    name = form.get("name", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    confirm = form.get("confirm_password", "")
    bio = form.get("bio", "").strip()

    if not name:
        errors.append("Name is required.")
    if not email or "@" not in email:
        errors.append("A valid email is required.")
    elif get_author_by_email(email):
        errors.append("There's already an account with that email -- try signing in instead.")
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password needs to be at least {MIN_PASSWORD_LENGTH} characters.")
    elif password != confirm:
        errors.append("Passwords don't match.")

    if errors:
        return render_template("authors/signup.html", not_configured=False, errors=errors, form=form), 400

    existing_slugs = {a["slug"] for a in get_authors()}
    slug = _unique_slug(_slugify(name), existing_slugs)

    images = {}
    photo_path = None
    processed = story_store.process_image(request.files.get("photo"))
    if processed is not None:
        photo_path = f"static/uploads/authors/{slug}/photo.jpg"
        images[photo_path] = processed

    doc = {
        "slug": slug,
        "name": name,
        "email": email,
        "password_hash": generate_password_hash(password),
        "photo": ("/" + photo_path) if photo_path else None,
        "bio": bio,
    }

    published, message = story_store.save_author(doc, images, is_update=False)
    if published:
        flash("Account created! It takes a minute or two to go live on the deploy -- come back and sign in shortly.", "success")
    else:
        flash("Account created locally (dev mode). " + message, "warning")
    return redirect(url_for("author_auth.login"))


# ── Sign in / out ────────────────────────────────────────────────────────

@author_bp.get("/login")
def login():
    return render_template("authors/login.html")


@author_bp.post("/login")
def login_submit():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    author = get_author_by_email(email)
    if not author or not check_password_hash(author["password_hash"], password):
        flash("Wrong email or password.", "error")
        return render_template("authors/login.html"), 400

    session.clear()
    session["author_slug"] = author["slug"]
    return redirect(request.args.get("next") or url_for("author_auth.me"))


@author_bp.get("/logout")
def logout():
    session.pop("author_slug", None)
    return redirect(url_for("author_auth.login"))


# ── Profile ──────────────────────────────────────────────────────────────

@author_bp.get("/me")
@author_login_required
def me():
    author = get_author(session["author_slug"])
    if not author:
        # Signed in, but the profile hasn't landed on this running process
        # yet (the save committed to GitHub and is mid-redeploy).
        return render_template("authors/pending.html")
    return render_template("authors/me.html", author=public_fields(author), errors=[])


@author_bp.post("/me")
@author_login_required
def me_update():
    existing = get_author(session["author_slug"])
    if not existing:
        return render_template("authors/pending.html")

    form = request.form
    errors = []

    name = form.get("name", "").strip()
    bio = form.get("bio", "").strip()
    new_password = form.get("new_password", "")
    current_password = form.get("current_password", "")

    if not name:
        errors.append("Name is required.")

    if new_password:
        if not check_password_hash(existing["password_hash"], current_password):
            errors.append("Current password is wrong, so the new one wasn't set.")
        elif len(new_password) < MIN_PASSWORD_LENGTH:
            errors.append(f"New password needs to be at least {MIN_PASSWORD_LENGTH} characters.")

    if errors:
        preview = {**existing, "name": name, "bio": bio}
        return render_template("authors/me.html", author=public_fields(preview), errors=errors), 400

    images = {}
    photo_path = existing.get("photo")
    processed = story_store.process_image(request.files.get("photo"))
    if processed is not None:
        repo_path = f"static/uploads/authors/{existing['slug']}/photo.jpg"
        images[repo_path] = processed
        photo_path = "/" + repo_path

    doc = {
        **existing,
        "name": name,
        "bio": bio,
        "photo": photo_path,
    }
    if new_password and not errors:
        doc["password_hash"] = generate_password_hash(new_password)

    published, message = story_store.save_author(doc, images, is_update=True)
    flash(message, "success" if published else "warning")
    return redirect(url_for("author_auth.me"))
