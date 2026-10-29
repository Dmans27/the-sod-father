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
    "it":        {"label": "IT",        "emoji": "\U0001F4BB",       "color": "#1D6FA5"},
    "cooking":   {"label": "Cooking",   "emoji": "\U0001F373",       "color": "#C2571A"},
    "travel":    {"label": "Travel",    "emoji": "✈️",      "color": "#0F766E"},
    "golf":      {"label": "Golf",      "emoji": "⛳",           "color": "#2D6A4F"},
    "lifestyle": {"label": "Lifestyle", "emoji": "\U0001F33F",       "color": "#5C6B73"},
}

# Stories written under the old golf-only categories are treated as "golf"
# when they are read, so nothing has to be edited by hand (and a story
# published straight to GitHub with an old category can't break the site).
# Saving a story from the admin portal writes the new category.
LEGACY_CATEGORIES = {
    "equipment": "golf",
    "instruction": "golf",
    "courses": "golf",
    "tour": "golf",
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
    "list": "List",
    "tip": "Pro tip box",
    "takeaways": "Key takeaways",
    "table": "Table",
    "divider": "Divider",
    "gallery": "Gallery",
    "video": "Video (YouTube / Vimeo link)",
    "link": "Link card",
    "compare": "Before / after slider",
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
    legacy = LEGACY_CATEGORIES.get(article.get("category"))
    if legacy:
        article = dict(article)
        article["category"] = legacy
    if "blocks" not in article:
        article = dict(article)
        article["blocks"] = _blocks_from_legacy(article)
    return article


def words_in_blocks(blocks):
    """Words a reader actually reads, for the read-time estimate. Counts
    prose plus list items, tip text and takeaways; tables, captions and
    media don't add reading time."""
    words = 0
    for block in blocks:
        kind = block.get("type")
        if kind in ("paragraph", "quote", "subheading", "tip"):
            words += len((block.get("text") or "").split())
        elif kind in ("list", "takeaways"):
            for item in block.get("items") or []:
                words += len(str(item).split())
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


MAX_FEATURED = 3


def get_featured(articles):
    """The homepage's hero stories -- picked by hand in the admin portal
    (the "Feature this story" checkbox) rather than any ranking. `articles`
    is expected already sorted newest-first (get_articles() does that), so
    if more than MAX_FEATURED are checked, the newest ones win and older
    checked boxes simply stop having an effect rather than erroring."""
    featured = [a for a in articles if a.get("featured")]
    return featured[:MAX_FEATURED]
