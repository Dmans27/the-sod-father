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
from datetime import date, datetime
from functools import wraps
from urllib.parse import parse_qs, urlparse

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for,
)

from content import (BLOCK_TYPES, CATEGORIES, LOCAL_HOSTNAMES, estimate_read_minutes, get_article,
                     get_articles, get_featured, is_draft, map_embed_hosts)
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
    everything = get_articles(include_drafts=True)
    drafts = [x for x in everything if is_draft(x)]
    published = [x for x in everything if not is_draft(x)]
    featured = get_featured(published)
    return render_template(
        "admin/dashboard.html",
        drafts=drafts,
        articles=published,
        categories=CATEGORIES,
        featured_slugs={x["slug"] for x in featured},
    )


def _subscribers():
    import db
    return db.query(
        "SELECT s.email, s.name, s.profession, s.source, s.created_at, "
        "CASE WHEN u.email IS NULL THEN 0 ELSE 1 END AS unsubscribed "
        "FROM newsletter_subscribers s "
        "LEFT JOIN newsletter_unsubscribes u ON u.email = s.email "
        "ORDER BY s.created_at DESC")


@admin_bp.get("/subscribers")
@login_required
def subscribers_list():
    """Everyone who signed up for the newsletter (footer, /newsletter page or
    the pop-up). Read-only; use the CSV download to move them to an email tool."""
    error = None
    try:
        subscribers = _subscribers()
    except Exception as exc:  # database unreachable: say so instead of a 500
        subscribers, error = [], str(exc)
    return render_template("admin/subscribers.html", subscribers=subscribers, error=error)


def _csv_cell(value):
    """Stops a spreadsheet from running a name like '=HYPERLINK(...)' as a
    formula when the CSV is opened in Excel/Numbers/Sheets."""
    value = "" if value is None else str(value)
    if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        value = "'" + value
    return value


@admin_bp.get("/subscribers.csv")
@login_required
def subscribers_csv():
    import csv
    import io
    from flask import Response
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "name", "profession", "source", "signed_up_at", "unsubscribed"])
    for row in _subscribers():
        writer.writerow([_csv_cell(row[k]) for k in ("email", "name", "profession", "source", "created_at")]
                        + ["yes" if row["unsubscribed"] else ""])
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=column-newsletter-subscribers.csv"})


# ── Newsletter issues: write, preview, test, send ───────────────────────
# Sending logic lives in newsletter_send.py; this is just the admin screens.

def _newsletter_status():
    """What the editor page should warn about before anyone presses send."""
    import newsletter_send as ns
    active = 0
    try:
        active = len(ns.subscribers())
    except Exception:
        pass
    return {
        "has_key": bool(ns.api_key()),
        "from": ns.from_address(),
        "test_sender": ns.is_test_sender(),
        "has_address": bool(ns.postal_address()),
        "active": active,
    }


def _issue_or_404(issue_id):
    import newsletter_send as ns
    issue = ns.get_issue(issue_id)
    if not issue:
        abort(404)
    return issue


def _issue_form_page(issue, errors=None, status=200):
    import newsletter_send as ns
    from readers import csrf_token
    stories = get_articles()
    delivered, failed, todo = (0, 0, [])
    if issue and issue.get("id"):
        try:
            delivered, failed, todo = ns.progress(issue["id"])
        except Exception:
            pass
    return render_template(
        "admin/newsletter_form.html", issue=issue, stories=stories, errors=errors or [],
        status=_newsletter_status(), delivered=delivered, failed=failed, waiting=len(todo),
        csrf=csrf_token()), status


@admin_bp.get("/newsletter")
@login_required
def newsletter_list():
    import newsletter_send as ns
    error = None
    try:
        issues = ns.list_issues()
    except Exception as exc:
        issues, error = [], str(exc)
    return render_template("admin/newsletter_list.html", issues=issues, error=error,
                           status=_newsletter_status())


@admin_bp.get("/newsletter/new")
@login_required
def newsletter_new():
    return _issue_form_page({"id": None, "subject": "", "preview": "", "intro": "", "outro": "",
                             "slugs": [], "status": "draft"})


@admin_bp.post("/newsletter/new")
@login_required
def newsletter_create():
    import newsletter_send as ns
    issue, errors = ns.clean_issue_form(request.form)
    if errors:
        return _issue_form_page(dict(issue, id=None, status="draft"), errors, 400)
    issue_id = ns.save_issue(issue)
    flash("Draft saved.", "success")
    return redirect(url_for("admin.newsletter_edit", issue_id=issue_id))


@admin_bp.get("/newsletter/<issue_id>")
@login_required
def newsletter_edit(issue_id):
    return _issue_form_page(_issue_or_404(issue_id))


@admin_bp.post("/newsletter/<issue_id>")
@login_required
def newsletter_update(issue_id):
    import newsletter_send as ns
    current = _issue_or_404(issue_id)
    if current["status"] == "sent":
        flash("This issue has already been sent, so it can't be edited. Start a new issue instead.", "warning")
        return redirect(url_for("admin.newsletter_edit", issue_id=issue_id))
    issue, errors = ns.clean_issue_form(request.form)
    if errors:
        return _issue_form_page(dict(issue, id=issue_id, status=current["status"]), errors, 400)
    ns.save_issue(issue, issue_id)
    flash("Draft saved.", "success")
    return redirect(url_for("admin.newsletter_edit", issue_id=issue_id))


@admin_bp.post("/newsletter/<issue_id>/delete")
@login_required
def newsletter_delete(issue_id):
    import newsletter_send as ns
    from readers import _csrf_ok
    issue = _issue_or_404(issue_id)
    if issue["status"] == "sent":
        flash("Sent issues are kept as a record and can't be deleted.", "warning")
    elif _csrf_ok():
        ns.delete_issue(issue_id)
        flash("Draft deleted.", "success")
    return redirect(url_for("admin.newsletter_list"))


@admin_bp.get("/newsletter/<issue_id>/preview")
@login_required
def newsletter_preview(issue_id):
    import newsletter_send as ns
    html, _text = ns.render_issue(_issue_or_404(issue_id), name="Alex Reader")
    return html


@admin_bp.post("/newsletter/<issue_id>/test")
@login_required
def newsletter_test(issue_id):
    import newsletter_send as ns
    from readers import EMAIL_RE, _csrf_ok
    issue = _issue_or_404(issue_id)
    to = (request.form.get("test_email") or "").strip().lower()
    if not _csrf_ok():
        flash("Your session expired -- please try again.", "error")
    elif not ns.api_key():
        flash("RESEND_API_KEY isn't set on the server, so nothing can be sent yet.", "warning")
    elif not EMAIL_RE.match(to):
        flash("Enter a valid email address to send the test to.", "error")
    else:
        html, text = ns.render_issue(issue, email=to, name="")
        try:
            ok, err = ns.send_email(to, "[TEST] " + issue["subject"], html, text)
        except ns.QuotaReached as exc:
            ok, err = False, str(exc)
        if ok:
            flash(f"Test email sent to {to}. It can take a minute to arrive -- check spam too.", "success")
        else:
            flash(f"The test email failed: {err}", "error")
    return redirect(url_for("admin.newsletter_edit", issue_id=issue_id))


@admin_bp.post("/newsletter/<issue_id>/send")
@login_required
def newsletter_send_chunk(issue_id):
    """Called repeatedly by the editor page's JavaScript; each call emails a
    batch (about 18 seconds' worth) and reports progress as JSON."""
    import newsletter_send as ns
    from readers import _csrf_ok
    issue = _issue_or_404(issue_id)
    if not _csrf_ok():
        return jsonify(ok=False, error="Your session expired -- reload the page and try again."), 400
    if not ns.api_key():
        return jsonify(ok=False, error="RESEND_API_KEY isn't set on the server."), 400
    if ns.is_test_sender():
        return jsonify(ok=False, error=(
            "Sending is still on Resend's test sender, which only delivers to your own address. "
            "Verify a domain in Resend and set NEWSLETTER_FROM first.")), 400
    if issue["status"] == "sent" and request.form.get("retry") != "1":
        return jsonify(ok=False, error="This issue has already been sent."), 400
    if not ns.subscribers():
        return jsonify(ok=False, error="There are no active subscribers yet."), 400
    try:
        result = ns.send_chunk(issue, retry_failed=request.form.get("retry") == "1")
    except Exception as exc:
        current_app.logger.exception("Newsletter send failed")
        return jsonify(ok=False, error=str(exc)), 500
    return jsonify(ok=True, **result)


@admin_bp.get("/search-index")
@login_required
def search_index():
    """Status of the "Ask" search: is it switched on, and how much of the
    site is indexed. Rebuild is only needed after changing the embedding model."""
    import ask_engine
    from readers import csrf_token
    status, error = {}, None
    try:
        status = ask_engine.index_status()
    except Exception as exc:  # database unreachable: say so instead of a 500
        error = str(exc)
    return render_template("admin/search_index.html", enabled=ask_engine.enabled(),
                           status=status, error=error, csrf=csrf_token(),
                           embed_model=ask_engine.embed_model(), chat_model=ask_engine.chat_model())


@admin_bp.post("/search-index/rebuild")
@login_required
def search_index_rebuild():
    import ask_engine
    from readers import _csrf_ok
    if not _csrf_ok():
        flash("Your session expired. Try again.", "warning")
    elif not ask_engine.enabled():
        flash("Add OPENAI_API_KEY in Render first, then rebuild.", "warning")
    else:
        try:
            r = ask_engine.sync_index(force=True)
            flash(f"Rebuilt: {r['articles']} articles, {r['chunks']} pieces indexed.", "success")
        except Exception as exc:
            flash(f"Couldn't rebuild the index: {exc}", "warning")
    return redirect(url_for("admin.search_index"))


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


MAP_STYLES = ("light", "dark", "streets")
MAP_HEIGHTS = {"short": 300, "medium": 420, "tall": 560}
_MAP_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")


def parse_map_url(raw, style="light"):
    """Turns a pasted ai-app map link into {kind, ident, embed_url, open_url},
    or None if it isn't a map from an allowed host (content.map_embed_hosts).

    Accepts the ordinary share links -- /lists/<slug> for one list and
    /u/<id>/map for a whole profile -- as well as the /embed/... forms.
    Like parse_video_url, the iframe address is always rebuilt here from the
    extracted slug or id, never copied from what was pasted."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    netloc = (parsed.netloc or "").lower()
    if not netloc or "@" in netloc or netloc not in map_embed_hosts():
        return None
    # https everywhere; plain http only for localhost, for trying it out.
    scheme = "http" if (parsed.scheme == "http" and (parsed.hostname or "") in LOCAL_HOSTNAMES) else "https"
    base = f"{scheme}://{netloc}"
    style = style if style in MAP_STYLES else "light"
    path = parsed.path.rstrip("/")

    m = re.match(r"^/(?:lists|embed/list)/([^/]+)$", path)
    if m and _MAP_SLUG.match(m.group(1)):
        slug = m.group(1)
        return {"kind": "list", "ident": slug,
                "embed_url": f"{base}/embed/list/{slug}?style={style}",
                "open_url": f"{base}/lists/{slug}"}

    m = re.match(r"^/(?:u/(\d{1,9})/map|embed/u/(\d{1,9}))$", path)
    if m:
        user_id = m.group(1) or m.group(2)
        return {"kind": "profile", "ident": user_id,
                "embed_url": f"{base}/embed/u/{user_id}?style={style}",
                "open_url": f"{base}/u/{user_id}/map"}
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

        elif btype == "map":
            raw_url = form.get(f"block_url_{bid}", "").strip()
            if raw_url:
                style = form.get(f"block_map_style_{bid}", "light")
                height = form.get(f"block_map_height_{bid}", "medium")
                style = style if style in MAP_STYLES else "light"
                height = height if height in MAP_HEIGHTS else "medium"
                parsed = parse_map_url(raw_url, style)
                if parsed is None:
                    errors.append(
                        f"The map link “{raw_url}” isn't a map I can embed. Paste a list link "
                        "(…/lists/your-list) or a profile map link (…/u/12/map) from your app "
                        f"({', '.join(map_embed_hosts())}).")
                else:
                    blocks.append({
                        "type": "map", "kind": parsed["kind"], "url": raw_url,
                        "embed_url": parsed["embed_url"], "open_url": parsed["open_url"],
                        "style": style, "height": height,
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


def _build_doc_from_form(form, files, slug, existing, draft=False):
    """Returns (doc, images, errors, warnings). images is {repo_path: bytes} for only
    the photos newly uploaded this save (cover + any block photos) -- a
    photo block left untouched re-saves whatever image path it already
    had, so editing a story without touching a given photo doesn't lose
    it.

    draft=True is the "Save draft" path: nothing is required (a half-written
    story is the whole point), so what would be an error when publishing --
    a missing title, author or date, a block with a problem -- is either
    filled in with a placeholder or skipped and reported back as a warning."""
    errors = []
    warnings = []
    title = form.get("title", "").strip()
    dek = form.get("dek", "").strip()
    category = form.get("category", "")
    subcategory = form.get("subcategory", "").strip()
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

    if draft:
        title = title or "Untitled draft"
        published_at = published_at or date.today().isoformat()
        if category not in CATEGORIES:
            category = ""
        if not category or subcategory not in (CATEGORIES[category].get("subcategories") or {}):
            subcategory = ""
    else:
        if not title:
            errors.append("Title is required.")
        if category not in CATEGORIES:
            errors.append("Pick a valid category.")
        elif subcategory and subcategory not in (CATEGORIES[category].get("subcategories") or {}):
            errors.append("Pick a subcategory that belongs to the chosen category.")
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
    if draft:
        warnings.extend(block_errors)
    else:
        errors.extend(block_errors)
        if not blocks:
            errors.append("Add at least one block to the story (a paragraph, a photo, a quote...).")

    doc = {
        "slug": slug,
        "title": title,
        "dek": dek,
        "category": category,
        "subcategory": subcategory,
        "author": author_name,
        "author_slug": author_slug or None,
        "author_photo": author_photo,
        "author_position": author_position,
        "published_at": published_at,
        "cover_image": cover_image,
        "featured": form.get("featured") == "1",
        "status": "draft" if draft else "published",
        "blocks": blocks,
        "read_minutes": estimate_read_minutes(blocks),
    }

    return doc, images, errors, warnings


# ── Story routes ─────────────────────────────────────────────────────────

def _wants_draft():
    return request.form.get("action") == "draft"


def _slug_from_hint():
    """The editor remembers a story's slug (hidden draft_slug field) once it
    has been saved as a draft, so saving again updates that same draft instead
    of creating a second one. Returns (slug, existing_story) -- slug is ""
    when there is no usable hint. A hint that points at a *published* story is
    ignored, so this can never overwrite one by accident."""
    raw = request.form.get("draft_slug", "").strip()
    if not raw:
        return "", None
    slug = slugify(raw)
    prior = get_article(slug, include_drafts=True)
    if prior is not None and not is_draft(prior):
        return "", None
    return slug, prior


def _render_form(mode, article, errors):
    return render_template(
        "admin/story_form.html",
        mode=mode,
        article=article,
        categories=CATEGORIES,
        authors=get_authors(),
        block_types=BLOCK_TYPES,
        story_choices=_story_choices(),
        errors=errors,
    )


def _draft_message(committed):
    if committed:
        return ("Draft saved to GitHub \u2014 Render redeploys (usually 1-2 min). "
                "It stays off the site until you publish it.")
    return ("Draft saved locally (dev mode, no GITHUB_TOKEN). "
            "It stays off the site until you publish it.")


def _save_error_text(exc):
    """A readable message for a failed save, shown above the editor so the
    story text the writer typed is not lost behind a bare 500 page."""
    return ("Your story was NOT saved: %s. Nothing you typed has been lost -- "
            "it is still in this form (and autosaved in this browser). "
            "If this mentions GitHub or a token, the GITHUB_TOKEN on Render "
            "has probably expired and needs replacing." % exc)


def _flash_save(published, message, warnings, draft):
    flash(_draft_message(published) if draft else message,
          "success" if published else "warning")
    for w in warnings:
        flash("Skipped in the draft: " + w, "warning")


@admin_bp.get("/story/new")
@login_required
def story_new():
    return _render_form("new", None, [])


@admin_bp.post("/story/new")
@login_required
def story_create():
    draft = _wants_draft()
    slug, existing = _slug_from_hint()
    if not slug:
        title = request.form.get("title", "").strip()
        taken = {x["slug"] for x in get_articles(include_drafts=True)}
        slug = unique_slug(slugify(title or ("untitled-draft" if draft else "story")), taken)

    doc, images, errors, warnings = _build_doc_from_form(
        request.form, request.files, slug, existing=existing, draft=draft)

    if errors:
        return _render_form("new", doc, errors), 400

    try:
        published, message = story_store.save_story(doc, images, is_update=existing is not None)
    except Exception as exc:  # GitHub rejected the commit, token expired, etc.
        current_app.logger.exception("Saving story %s failed", slug)
        return _render_form("new", doc, [_save_error_text(exc)]), 502
    _flash_save(published, message, warnings, draft)
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/story/draft")
@login_required
def story_save_draft():
    """The editor's "Save draft" button (and nothing else) posts here from
    JavaScript, so the page stays open and keeps working. Saves with the same
    rules as a draft from the normal form, and answers with JSON."""
    slug, existing = _slug_from_hint()
    if not slug:
        title = request.form.get("title", "").strip()
        taken = {x["slug"] for x in get_articles(include_drafts=True)}
        slug = unique_slug(slugify(title or "untitled-draft"), taken)

    doc, images, _errors, warnings = _build_doc_from_form(
        request.form, request.files, slug, existing=existing, draft=True)
    try:
        published, message = story_store.save_story(doc, images, is_update=existing is not None)
    except Exception as exc:  # GitHub down / token problem: tell the editor, don't 500
        return jsonify(ok=False, error=str(exc)), 502
    return jsonify(
        ok=True,
        slug=slug,
        title=doc["title"],
        message=_draft_message(published),
        warnings=warnings,
        saved_at=datetime.now().strftime("%H:%M:%S"),
        edit_url=url_for("admin.story_edit", slug=slug),
    )


@admin_bp.get("/story/<slug>/edit")
@login_required
def story_edit(slug):
    article = get_article(slug, include_drafts=True)
    if not article:
        abort(404)
    return _render_form("edit", article, [])


@admin_bp.post("/story/<slug>/edit")
@login_required
def story_update(slug):
    existing = get_article(slug, include_drafts=True)
    if not existing:
        abort(404)

    draft = _wants_draft()
    doc, images, errors, warnings = _build_doc_from_form(
        request.form, request.files, slug, existing=existing, draft=draft)

    if errors:
        return _render_form("edit", doc, errors), 400

    try:
        published, message = story_store.save_story(doc, images, is_update=True)
    except Exception as exc:  # GitHub rejected the commit, token expired, etc.
        current_app.logger.exception("Saving story %s failed", slug)
        return _render_form("edit", doc, [_save_error_text(exc)]), 502
    _flash_save(published, message, warnings, draft)
    return redirect(url_for("admin.dashboard"))
