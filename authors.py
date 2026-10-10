"""
Author account storage -- mirrors content.py's pattern exactly, just for
authors instead of articles: each one is its own JSON file under
content/authors/, read fresh on every call. Signing up or editing a profile
writes through story_store.py's save_record() (same GitHub-commit path
stories use), so author data survives Render restarts the same way.
"""
import json
from pathlib import Path

AUTHORS_DIR = Path(__file__).parent / "content" / "authors"


def get_authors():
    """Every author, alphabetical by name. Not sensitive data is included
    here (name, photo, bio) -- password_hash is also present in the file,
    callers that render this to a page must leave it out."""
    authors = []
    if not AUTHORS_DIR.exists():
        return authors
    for path in AUTHORS_DIR.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            authors.append(json.load(f))
    authors.sort(key=lambda a: a.get("name", "").lower())
    return authors


def get_author(slug):
    path = AUTHORS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_author_by_email(email):
    email = (email or "").strip().lower()
    if not email:
        return None
    for author in get_authors():
        if author.get("email", "").strip().lower() == email:
            return author
    return None


def public_fields(author):
    """Strips the password hash before an author record goes anywhere
    near a template or a form prefill."""
    if not author:
        return None
    return {k: v for k, v in author.items() if k != "password_hash"}
