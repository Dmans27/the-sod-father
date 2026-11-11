"""
Findability: everything that helps search engines, link previews and feed
readers understand the site.

  /robots.txt     what crawlers may read, and where the sitemap is
  /sitemap.xml    every public page, with the date each story was published
  /feed.xml       RSS feed of the newest stories (readers, aggregators, IFTTT)
  /og/<name>.png  a branded 1200x630 picture used when a link is shared and
                  the story has no cover photo (so the preview is never blank)

It also gives every page (through a template variable) its canonical address
and, if you've set one up, the analytics snippet:

  GOATCOUNTER_CODE   e.g. "mansfield"  ->  https://mansfield.goatcounter.com
  PLAUSIBLE_DOMAIN   e.g. "mansfield.com"

Neither is needed; with neither set, no tracking code is added. Visits while
you are signed in to the admin are never counted.

SITE_URL (the same variable the newsletter uses) should be your real address,
e.g. https://mansfield.com once you have a domain; until then the address the
visitor used is taken, upgraded to https.
"""
import io
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from functools import lru_cache
from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, request, session

from content import CATEGORIES, get_article, get_articles

seo_bp = Blueprint("seo", __name__)

SITE_NAME = "\\\\Mansfield"
TAGLINE = "Stories on IT, cooking, travel, golf and everyday life, by Daniel Mansfield."
FEED_LIMIT = 30


def site_base():
    """The site's public address with no trailing slash."""
    configured = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    base = request.url_root.rstrip("/")
    # Render terminates https in front of the app, so Flask sees http.
    local = request.host.split(":")[0] in ("localhost", "127.0.0.1", "[::1]")
    if base.startswith("http://") and not local:
        base = "https://" + base[len("http://"):]
    return base


def absolute(path_or_url):
    if not path_or_url:
        return ""
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return site_base() + (path_or_url if path_or_url.startswith("/") else "/" + path_or_url)


def share_image(article):
    """Picture used for link previews: the cover photo, or a generated card."""
    if article.get("cover_image"):
        return absolute(article["cover_image"])
    return absolute(f"/og/{article['slug']}.png")


def _date(value):
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def analytics_snippet():
    """HTML to put before </head>, or '' when no tracker is configured or the
    visitor is the admin."""
    if session.get("is_admin"):
        return ""
    gc = os.environ.get("GOATCOUNTER_CODE", "").strip()
    if gc.replace("-", "").isalnum() and gc:
        return (f'<script data-goatcounter="https://{gc}.goatcounter.com/count" '
                f'async src="//gc.zgo.at/count.js"></script>')
    pd = os.environ.get("PLAUSIBLE_DOMAIN", "").strip()
    if pd and all(c.isalnum() or c in ".-" for c in pd):
        return f'<script defer data-domain="{pd}" src="https://plausible.io/js/script.js"></script>'
    return ""


def register(app):
    @app.context_processor
    def inject_seo():
        return {
            "site_base": site_base,
            "absolute": absolute,
            "share_image": share_image,
            "site_tagline": TAGLINE,
            "analytics_html": analytics_snippet,
        }


# ── robots.txt ───────────────────────────────────────────────────────────

@seo_bp.get("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /account/",
        "Disallow: /authors/",
        "Disallow: /api/",
        "Disallow: /newsletter/unsubscribe",
        "",
        f"Sitemap: {site_base()}/sitemap.xml",
        "",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


# ── sitemap.xml ──────────────────────────────────────────────────────────

@seo_bp.get("/sitemap.xml")
def sitemap():
    base = site_base()
    articles = get_articles()
    urls = [(base + "/", articles[0].get("published_at") if articles else None),
            (base + "/newsletter", None),
            (base + "/privacy", None), (base + "/terms", None), (base + "/contact", None)]

    for key, cat in CATEGORIES.items():
        in_cat = [a for a in articles if a["category"] == key]
        if not in_cat:
            continue  # an empty category page is thin content; leave it out
        urls.append((f"{base}/category/{key}", in_cat[0].get("published_at")))
        for sub in (cat.get("subcategories") or {}):
            in_sub = [a for a in in_cat if a.get("subcategory") == sub]
            if in_sub:
                urls.append((f"{base}/category/{key}/{sub}", in_sub[0].get("published_at")))

    for a in articles:
        urls.append((f"{base}/article/{a['slug']}", a.get("published_at")))

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in urls:
        out.append("  <url><loc>%s</loc>%s</url>" % (
            escape(loc), f"<lastmod>{escape(lastmod[:10])}</lastmod>" if lastmod else ""))
    out.append("</urlset>")
    return Response("\n".join(out) + "\n", mimetype="application/xml")


# ── feed.xml (RSS 2.0) ───────────────────────────────────────────────────

@seo_bp.get("/feed.xml")
def feed():
    base = site_base()
    articles = get_articles()[:FEED_LIMIT]
    newest = _date(articles[0].get("published_at")) if articles else None
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
           'xmlns:dc="http://purl.org/dc/elements/1.1/">',
           "<channel>",
           f"<title>{escape(SITE_NAME)}</title>",
           f"<link>{escape(base)}/</link>",
           f"<description>{escape(TAGLINE)}</description>",
           "<language>en</language>",
           f'<atom:link href="{escape(base)}/feed.xml" rel="self" type="application/rss+xml"/>']
    if newest:
        out.append(f"<lastBuildDate>{format_datetime(newest)}</lastBuildDate>")
    for a in articles:
        link = f"{base}/article/{a['slug']}"
        when = _date(a.get("published_at"))
        cat = CATEGORIES.get(a.get("category"), {}).get("label")
        out.append("<item>")
        out.append(f"<title>{escape(a.get('title', ''))}</title>")
        out.append(f"<link>{escape(link)}</link>")
        out.append(f'<guid isPermaLink="true">{escape(link)}</guid>')
        if when:
            out.append(f"<pubDate>{format_datetime(when)}</pubDate>")
        if a.get("author"):
            out.append(f"<dc:creator>{escape(a['author'])}</dc:creator>")
        if cat:
            out.append(f"<category>{escape(cat)}</category>")
        out.append(f"<description>{escape(a.get('dek', ''))}</description>")
        out.append("</item>")
    out.append("</channel></rss>")
    return Response("\n".join(out) + "\n", mimetype="application/rss+xml")


# ── generated share pictures ─────────────────────────────────────────────

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/Library/Fonts/Georgia Bold.ttf",
]


def _font(size):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)  # Pillow's built-in, always there


def _wrap(draw, text, font, width, max_lines):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return lines


@lru_cache(maxsize=128)
def _card(title, kicker):
    from PIL import Image, ImageDraw
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), "#ffffff")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 14], fill="#1b4332")
    d.rectangle([0, h - 14, w, h], fill="#1b4332")
    brand = _font(64)
    d.text((72, 56), "\\\\", font=brand, fill="#b8860b")
    d.text((72 + d.textlength("\\\\", font=brand), 56), "Mansfield", font=brand, fill="#111111")
    if kicker:
        d.text((72, 190), kicker.upper(), font=_font(28), fill="#2d6a4f")
    size = 78 if len(title) < 50 else 62
    font = _font(size)
    y = 240
    for line in _wrap(d, title, font, w - 144, 3):
        d.text((72, y), line, font=font, fill="#111111")
        y += int(size * 1.22)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@seo_bp.get("/og/<name>.png")
def og_image(name):
    if name == "default":
        title, kicker = TAGLINE, ""
    else:
        article = get_article(name)
        if not article:
            abort(404)
        title = article.get("title", "")
        kicker = CATEGORIES.get(article.get("category"), {}).get("label", "")
    resp = Response(_card(title, kicker), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp
