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
from urllib.parse import parse_qs, urlparse

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)

from content import BLOCK_TYPES, CATEGORIES, estimate_read_minutes, get_article, get_articles, get_featured
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
    articles = get_articles()
    featured = get_featured(articles)
    return render_template(
        "admin/dashboard.html",
        articles=articles,
        categories=CATEGORIES,
        featured_slugs={a["slug"] for a in featured},
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


# ── Parsing helpers for the newer block types ───────────────────────────

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_url(raw):
    """Turns a pasted YouTube or Vimeo link into (provider, video_id,
    embed_url), or None if it isn't one we recognise. The embed URL is
    always built here from the extracted ID -- never copied from what was
    pasted -- so only these two players can ever end up in an iframe."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]

    video_id = None
    if host == "youtube.com":
        if parsed.path == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [None])[0]
        else:
            m = re.match(r"^/(?:embed|shorts|live|v)/([^/?#]+)", parsed.path)
            video_id = m.group(1) if m else None
    elif host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    if video_id and _YOUTUBE_ID.match(video_id):
        return ("youtube", video_id, f"https://www.youtube-nocookie.com/embed/{video_id}")

    if host in ("vimeo.com", "player.vimeo.com"):
        ids = re.findall(r"/(\d{6,12})(?=/|$)", parsed.path)
        if ids:
            return ("vimeo", ids[-1], f"https://player.vimeo.com/video/{ids[-1]}")
    return None


def parse_list_items(text):
    """One item per line; a leading bullet or "1." the writer typed is stripped."""
    items = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", "", line).strip()
        if line:
            items.append(line)
    return items


def parse_table(text):
    """One row per line, columns separated by | -- or tabs, so a range
    pasted straight from Excel / Google Sheets works too. Rows are padded
    to the same width."""
    rows = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            cells = line.split("\t")
        else:
            line = line.strip()
            if line.startswith("|"):
                line = line[1:]
            if line.endswith("|"):
                line = line[:-1]
            cells = line.split("|")
        cells = [c.strip() for c in cells]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
            continue  # a markdown "---|---" divider row
        rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def _story_choices():
    """[{slug, title}] for the link card's "one of my stories" picker."""
    return [{"slug": a["slug"], "title": a["title"]} for a in get_articles()]


def _build_blocks_from_form(form, files, slug):
    """The story builder (story_form.html) lets Danny add blocks of many
    kinds in any order and drag them around -- the page keeps a hidden
    `block_order` field in sync with on-screen order, listing each block's
    id. A block's id is only ever a fragment of its field names
    (block_type_<id>, block_text_<id>, ...), generated client-side and
    never stored once saved; this just walks that order and pulls each
    block's type-specific fields back out.

    Returns (blocks, images, errors). images is {repo_path: bytes} for any
    newly uploaded photos, same shape save_record() expects for the cover
    photo. errors is a list of human-readable problems (e.g. a video link
    that isn't YouTube/Vimeo) shown at the top of the form."""
    order = [bid for bid in form.get("block_order", "").split(",") if bid.strip()]
    blocks = []
    images = {}
    errors = []

    def image_for(field_name, existing_field, repo_path):
        """A newly uploaded file wins; otherwise keep the image path the
        block already had (so editing a story doesn't lose its photos)."""
        processed = story_store.process_image(files.get(field_name))
        if processed is not None:
            images[repo_path] = processed
            return "/" + repo_path
        return form.get(existing_field, "").strip() or None

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
            image = image_for(f"block_photo_{bid}", f"block_existing_image_{bid}",
                              f"static/uploads/{slug}/block-{bid}.jpg")
            if image:
                blocks.append({"type": "photo", "image": image, "caption": caption})

        elif btype == "list":
            items = parse_list_items(form.get(f"block_text_{bid}", ""))
            style = "numbered" if form.get(f"block_style_{bid}") == "numbered" else "bulleted"
            if items:
                blocks.append({"type": "list", "style": style, "items": items})

        elif btype == "tip":
            text = form.get(f"block_text_{bid}", "").strip()
            variant = "warning" if form.get(f"block_variant_{bid}") == "warning" else "tip"
            if text:
                blocks.append({"type": "tip", "variant": variant, "text": text})

        elif btype == "takeaways":
            items = parse_list_items(form.get(f"block_text_{bid}", ""))
            title = form.get(f"block_title_{bid}", "").strip() or "Key takeaways"
            if items:
                blocks.append({"type": "takeaways", "title": title, "items": items})

        elif btype == "table":
            rows = parse_table(form.get(f"block_text_{bid}", ""))
            if rows:
                has_header = form.get(f"block_header_{bid}") == "1" and len(rows) > 1
                blocks.append({
                    "type": "table",
                    "header": rows[0] if has_header else None,
                    "rows": rows[1:] if has_header else rows,
                })

        elif btype == "divider":
            blocks.append({"type": "divider"})

        elif btype == "gallery":
            photos = []
            row_ids = [r for r in form.get(f"block_gallery_order_{bid}", "").split(",") if r.strip()]
            for rid in row_ids:
                image = image_for(
                    f"block_gallery_photo_{bid}_{rid}", f"block_gallery_existing_{bid}_{rid}",
                    f"static/uploads/{slug}/gallery-{bid}-{rid}.jpg")
                if image:
                    photos.append({
                        "image": image,
                        "caption": form.get(f"block_gallery_caption_{bid}_{rid}", "").strip(),
                    })
            if photos:
                blocks.append({"type": "gallery", "photos": photos})

        elif btype == "video":
            raw_url = form.get(f"block_url_{bid}", "").strip()
            if raw_url:
                parsed = parse_video_url(raw_url)
                if parsed is None:
                    errors.append(f"The video link \u201c{raw_url}\u201d isn't a YouTube or Vimeo link I can embed.")
                else:
                    provider, video_id, embed_url = parsed
                    blocks.append({
                        "type": "video", "provider": provider, "video_id": video_id,
                        "url": raw_url, "embed_url": embed_url,
                        "caption": form.get(f"block_caption_{bid}", "").strip(),
                    })

        elif btype == "link":
            kind = form.get(f"block_link_kind_{bid}", "story")
            label = form.get(f"block_link_label_{bid}", "").strip()
            if kind == "story":
                target = get_article(form.get(f"block_link_story_{bid}", "").strip())
                if target:
                    blocks.append({
                        "type": "link", "kind": "story",
                        "href": f"/article/{target['slug']}",
                        "title": target["title"],
                        "description": target.get("dek", ""),
                        "image": target.get("cover_image"),
                        "label": label or "Read the story",
                        "host": None,
                    })
            else:
                url = form.get(f"block_link_url_{bid}", "").strip()
                title = form.get(f"block_link_title_{bid}", "").strip()
                if url:
                    if "://" not in url:
                        url = "https://" + url
                    parsed_url = urlparse(url)
                    if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
                        errors.append(f"The link \u201c{url}\u201d must be a normal web address (https://...).")
                    else:
                        host = parsed_url.hostname
                        blocks.append({
                            "type": "link", "kind": "web", "href": url,
                            "title": title or host,
                            "description": form.get(f"block_link_desc_{bid}", "").strip(),
                            "image": None,
                            "label": label or "Visit site",
                            "host": host[4:] if host.startswith("www.") else host,
                        })

        elif btype == "compare":
            before = image_for(f"block_before_{bid}", f"block_existing_before_{bid}",
                               f"static/uploads/{slug}/compare-{bid}-before.jpg")
            after = image_for(f"block_after_{bid}", f"block_existing_after_{bid}",
                              f"static/uploads/{slug}/compare-{bid}-after.jpg")
            if before and after:
                blocks.append({
                    "type": "compare", "before": before, "after": after,
                    "before_label": form.get(f"block_before_label_{bid}", "").strip() or "Before",
                    "after_label": form.get(f"block_after_label_{bid}", "").strip() or "After",
                    "caption": form.get(f"block_caption_{bid}", "").strip(),
                })
            elif before or after:
                errors.append("A before / after slider needs two photos \u2014 one is missing.")

    return blocks, images, errors


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
    author_position = None
    if author_slug and author_slug != "__other__":
        author_record = get_author(author_slug)
        if author_record:
            author_name = author_record["name"]
            author_photo = author_record.get("photo")
            author_position = author_record.get("position")
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
    # The "Remove cover photo" checkbox only matters when no new photo was
    # uploaded this save -- a fresh upload always wins, same as before.
    # (A FileStorage with no file chosen is still a truthy object, so this
    # checks .filename rather than the object itself -- same test
    # story_store.process_image uses.)
    new_cover_file = files.get("cover_image")
    has_new_cover = bool(new_cover_file and new_cover_file.filename)
    if form.get("remove_cover_image") == "1" and not has_new_cover:
        existing_cover = None
    cover_image = handle_image("cover_image", existing_cover)

    blocks, block_images, block_errors = _build_blocks_from_form(form, files, slug)
    images.update(block_images)
    errors.extend(block_errors)

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
        "author_position": author_position,
        "published_at": published_at,
        "cover_image": cover_image,
        "featured": form.get("featured") == "1",
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
        story_choices=_story_choices(),
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
        story_choices=_story_choices(),
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
        story_choices=_story_choices(),
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
        story_choices=_story_choices(),
            errors=errors,
        ), 400

    published, message = story_store.save_story(doc, images, is_update=True)
    flash(message, "success" if published else "warning")
    return redirect(url_for("admin.dashboard"))
