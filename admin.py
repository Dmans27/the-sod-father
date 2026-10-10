"""
The admin portal: a password-gated set of /admin routes for writing and
editing stories without touching code. A save builds the same JSON shape
content.py reads, resizes any uploaded photos (story_store.py), and commits
everything straight to GitHub (github_content.py) -- which is also what
keeps content alive across Render restarts, since the free tier has no
persistent disk.

Auth is intentionally simple: one shared password (ADMIN_PASSWORD, set as a
Render env var) protects the whole blueprint via a signed session cookie.
That's enough for a single-writer site; it would need real accounts if
other people start writing stories too.
"""
import os
import re
import secrets
from functools import wraps

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)

from content import BLOCK_TYPES, CATEGORIES, estimate_read_minutes, get_article, get_articles
from authors import get_author, get_authors
import story_store

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ── Auth ──────────────────────────────────────────────────────────────────

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@admin_bp.get("/login")
def login():
    if not os.environ.get("ADMIN_PASSWORD"):
        return render_template("admin/login.html", not_configured=True)
    return render_template("admin/login.html", not_configured=False)


@admin_bp.post("/login")
def login_submit():
    configured = os.environ.get("ADMIN_PASSWORD")
    if not configured:
        return render_template("admin/login.html", not_configured=True)

    submitted = request.form.get("password", "")
    if secrets.compare_digest(submitted, configured):
        session.clear()
        session["is_admin"] = True
        return redirect(request.args.get("next") or url_for("admin.dashboard"))

    return render_template("admin/login.html", not_configured=False, error="Wrong password.")


@admin_bp.get("/logout")
def logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin.login"))


# ── Dashboard ────────────────────────────────────────────────────────────

@admin_bp.get("/")
@login_required
def dashboard():
    return render_template(
        "admin/dashboard.html",
        articles=get_articles(),
        categories=CATEGORIES,
    )


@admin_bp.get("/authors")
@login_required
def authors_list():
    # Read-only -- authors manage their own profiles at /authors/me. This
    # is just so Danny can see at a glance who's signed up (and share the
    # sign-up link/password with them) without digging through the repo.
    return render_template("admin/authors.html", authors=get_authors())


# ── Story form helpers ──────────────────────────────────────────────────

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "story"


def unique_slug(base_slug, taken_slugs):
    if base_slug not in taken_slugs:
        return base_slug
    n = 2
    while f"{base_slug}-{n}" in taken_slugs:
        n += 1
    return f"{base_slug}-{n}"


def _image_repo_path(slug, field_name, original_filename):
    ext = ".jpg"  # story_store.process_image always re-encodes as JPEG
    safe_field = re.sub(r"[^a-z0-9_-]+", "-", field_name.lower())
    return f"static/uploads/{slug}/{safe_field}{ext}"


def _build_blocks_from_form(form, files, slug):
    """The story builder (story_form.html) lets Danny add paragraph, photo,
    quote, subheading, and custom-embed blocks in any order and drag them
    around -- the page keeps a hidden `block_order` field in sync with
    on-screen order, listing each block's id. A block's id is only ever a
    fragment of its field names (block_type_<id>, block_text_<id>, ...),
    generated client-side and never stored once saved; this just walks that
    order and pulls each block's type-specific fields back out.

    Returns (blocks, images). images is {repo_path: bytes} for any newly
    uploaded photo blocks, same shape save_record() expects for the cover
    photo."""
    order = [bid for bid in form.get("block_order", "").split(",") if bid.strip()]
    blocks = []
    images = {}

    for bid in order:
        btype = form.get(f"block_type_{bid}", "")

        if btype == "paragraph":
            text = form.get(f"block_text_{bid}", "").strip()
            if text:
                blocks.append({"type": "paragraph", "text": text})

        elif btype == "subheading":
            text = form.get(f"block_text_{bid}", "").strip()
            if text:
                blocks.append({"type": "subheading", "text": text})

        elif btype == "quote":
            text = form.get(f"block_text_{bid}", "").strip()
            attribution = form.get(f"block_attribution_{bid}", "").strip()
            if text:
                blocks.append({"type": "quote", "text": text, "attribution": attribution})

        elif btype == "embed":
            html = form.get(f"block_text_{bid}", "").strip()
            if html:
                blocks.append({"type": "embed", "html": html})

        elif btype == "photo":
            caption = form.get(f"block_caption_{bid}", "").strip()
            existing_image = form.get(f"block_existing_image_{bid}", "").strip() or None
            field_name = f"block_photo_{bid}"
            processed = story_store.process_image(files.get(field_name))
            if processed is not None:
                path = f"static/uploads/{slug}/block-{bid}.jpg"
                images[path] = processed
                image = "/" + path
            else:
                image = existing_image
            if image:
                blocks.append({"type": "photo", "image": image, "caption": caption})

    return blocks, images


def _build_doc_from_form(form, files, slug, existing):
    """Returns (doc, images, errors). images is {repo_path: bytes} for only
    the photos newly uploaded this save (cover + any block photos) -- a
    photo block left untouched re-saves whatever image path it already
    had, so editing a story without touching a given photo doesn't lose
    it."""
    errors = []
    title = form.get("title", "").strip()
    dek = form.get("dek", "").strip()
    category = form.get("category", "")
    published_at = form.get("published_at", "").strip()

    # The author field is a dropdown of registered authors (so their photo
    # comes along automatically) with an "Other" option that reveals a
    # plain text field -- for a one-off guest byline, or just because
    # Danny hasn't set everyone up with an account yet. Resolved here,
    # server-side, rather than trusted from hidden form fields, so there's
    # no way to submit someone else's photo under a different name.
    author_slug = form.get("author_slug", "").strip()
    author_name = ""
    author_photo = None
    if author_slug and author_slug != "__other__":
        author_record = get_author(author_slug)
        if author_record:
            author_name = author_record["name"]
            author_photo = author_record.get("photo")
        else:
            author_slug = ""
    else:
        author_slug = ""
        author_name = form.get("author_custom", "").strip()

    if not title:
        errors.append("Title is required.")
    if category not in CATEGORIES:
        errors.append("Pick a valid category.")
    if not author_name:
        errors.append("Author is required.")
    if not published_at:
        errors.append("Published date is required.")

    images = {}

    def handle_image(field_name, keep_existing_path):
        processed = story_store.process_image(files.get(field_name))
        if processed is not None:
            path = _image_repo_path(slug, field_name, files[field_name].filename)
            images[path] = processed
            return "/" + path
        return keep_existing_path

    existing_cover = existing.get("cover_image") if existing else None
    cover_image = handle_image("cover_image", existing_cover)

    blocks, block_images = _build_blocks_from_form(form, files, slug)
    images.update(block_images)

    if not blocks:
        errors.append("Add at least one block to the story (a paragraph, a photo, a quote...).")

    doc = {
        "slug": slug,
        "title": title,
        "dek": dek,
        "category": category,
        "author": author_name,
        "author_slug": author_slug or None,
        "author_photo": author_photo,
        "published_at": published_at,
        "cover_image": cover_image,
        "blocks": blocks,
        "read_minutes": estimate_read_minutes(blocks),
    }

    return doc, images, errors


# ── Story routes ─────────────────────────────────────────────────────────

@admin_bp.get("/story/new")
@login_required
def story_new():
    return render_template(
        "admin/story_form.html",
        mode="new",
        article=None,
        categories=CATEGORIES,
        authors=get_authors(),
        block_types=BLOCK_TYPES,
        errors=[],
    )


@admin_bp.post("/story/new")
@login_required
def story_create():
    title = request.form.get("title", "").strip()
    existing_slugs = {a["slug"] for a in get_articles()}
    slug = unique_slug(slugify(title or "story"), existing_slugs)

    doc, images, errors = _build_doc_from_form(request.form, request.files, slug, existing=None)

    if errors:
        return render_template(
            "admin/story_form.html",
            mode="new",
            article=doc,
            categories=CATEGORIES,
            authors=get_authors(),
            block_types=BLOCK_TYPES,
            errors=errors,
        ), 400

    published, message = story_store.save_story(doc, images, is_update=False)
    flash(message, "success" if published else "warning")
    return redirect(url_for("admin.dashboard"))


@admin_bp.get("/story/<slug>/edit")
@login_required
def story_edit(slug):
    article = get_article(slug)
    if not article:
        abort(404)
    return render_template(
        "admin/story_form.html",
        mode="edit",
        article=article,
        categories=CATEGORIES,
        authors=get_authors(),
        block_types=BLOCK_TYPES,
        errors=[],
    )


@admin_bp.post("/story/<slug>/edit")
@login_required
def story_update(slug):
    existing = get_article(slug)
    if not existing:
        abort(404)

    doc, images, errors = _build_doc_from_form(request.form, request.files, slug, existing=existing)

    if errors:
        return render_template(
            "admin/story_form.html",
            mode="edit",
            article=doc,
            categories=CATEGORIES,
            authors=get_authors(),
            block_types=BLOCK_TYPES,
            errors=errors,
        ), 400

    published, message = story_store.save_story(doc, images, is_update=True)
    flash(message, "success" if published else "warning")
    return redirect(url_for("admin.dashboard"))
