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

# Known story layouts, offered as a picker in the admin form. Each key here
# must have matching markup in templates/article.html.
TEMPLATES = {
    "standard": "Standard article",
    "gallery": "Photo gallery",
    "listicle": "Listicle / ranked list",
}


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
            articles.append(json.load(f))
    articles.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    return articles


def get_article(slug):
    path = CONTENT_DIR / f"{slug}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_related(article, limit=3):
    others = [a for a in get_articles() if a["slug"] != article["slug"]]
    same_category = [a for a in others if a["category"] == article["category"]]
    different_category = [a for a in others if a["category"] != article["category"]]
    return (same_category + different_category)[:limit]
