"""
Content loading for the golf media site.

Articles used to live as plain dicts in a hardcoded Python list (articles.py).
Now that stories get added through the admin portal (admin.py), each article
is its own JSON file under content/articles/ -- that's what makes "saving a
story" a simple file write (committed to GitHub by github_content.py) instead
of an edit to source code. This module just reads those files back out.

Categories are still a small, fixed, hand-curated set, so they stay as a
plain dict here rather than becoming their own content type.
"""
import json
from pathlib import Path

CONTENT_DIR = Path(__file__).parent / "content" / "articles"

CATEGORIES = {
    "equipment":   {"label": "Equipment",     "emoji": "\U0001F3CC️",  "color": "#B8860B"},
    "instruction": {"label": "Instruction",   "emoji": "\U0001F3AF",        "color": "#1D6FA5"},
    "courses":     {"label": "Course Guides", "emoji": "\U0001F3DE️",  "color": "#2D6A4F"},
    "tour":        {"label": "Tour News",     "emoji": "\U0001F3C6",        "color": "#A4303F"},
    "lifestyle":   {"label": "Lifestyle",     "emoji": "⛳",            "color": "#5C6B73"},
}

# Block types the story builder (templates/admin/story_form.html) can add,
# in the order offered as "+ ..." buttons. Each key here must have matching
# render logic in templates/article.html, templates/base.html's renderSheet,
# and the block-type <template> markup in story_form.html.
BLOCK_TYPES = {
    "paragraph": "Paragraph",
    "photo": "Photo",
    "quote": "Quote",
    "subheading": "Subheading",
    "embed": "Custom embed / code",
}


def _blocks_from_legacy(article):
    """Stories written before the block-builder existed store their body as
    a plain list of paragraph strings, plus -- for the old "gallery" and
    "listicle" story types -- a separate photos/items list. This turns any
    of those shapes into the same blocks list new stories use, so
    article.html, the inline preview sheet, and the admin edit form only
    ever have to deal with one format.

    Nothing on disk is rewritten by this -- it runs again on every read --
    until the story is next saved through the builder, at which point it's
    written back out as real blocks and this stops being needed for it."""
    template = article.get("template", "standard")
    blocks = []

    for paragraph in article.get("body") or []:
        blocks.append({"type": "paragraph", "text": paragraph})

    if template == "gallery":
        for photo in article.get("photos") or []:
            blocks.append({
                "type": "photo",
                "image": photo.get("image"),
                "caption": photo.get("caption", ""),
            })
    elif template == "listicle":
        for item in article.get("items") or []:
            if item.get("heading"):
                blocks.append({"type": "subheading", "text": item["heading"]})
            if item.get("image"):
                blocks.append({"type": "photo", "image": item["image"], "caption": ""})
            for paragraph in item.get("body") or []:
                blocks.append({"type": "paragraph", "text": paragraph})

    return blocks


def _with_blocks(article):
    """Ensures an article dict has a `blocks` list, synthesizing one from
    legacy fields the first time an older story is read."""
    if article is None:
        return None
    if "blocks" not in article:
        article = dict(article)
        article["blocks"] = _blocks_from_legacy(article)
    return article


def words_in_blocks(blocks):
    words = 0
    for block in blocks:
        if block.get("type") in ("paragraph", "quote", "subheading"):
            words += len((block.get("text") or "").split())
    return words


def estimate_read_minutes(blocks):
    return max(1, round(words_in_blocks(blocks) / 200))


def get_articles():
    """Every article, newest first. Reads from disk on every call (not
    cached) -- there are only a few dozen of these at most, and reading
    fresh means a story saved through /admin shows up immediately without
    needing the dev server restarted, and in production each save is its
    own deploy anyway, which starts a fresh process."""
    articles = []
    if not CONTENT_DIR.exists():
        return articles
    for path in CONTENT_DIR.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            articles.append(_with_blocks(json.load(f)))
    articles.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    return articles


def get_article(slug):
    path = CONTENT_DIR / f"{slug}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return _with_blocks(json.load(f))


def get_related(article, limit=3):
    others = [a for a in get_articles() if a["slug"] != article["slug"]]
    same_category = [a for a in others if a["category"] == article["category"]]
    different_category = [a for a in others if a["category"] != article["category"]]
    return (same_category + different_category)[:limit]
