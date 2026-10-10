"""
The Sod Father -- a small, self-contained golf media site built around one
idea: the "cards" from a map-discovery app (Dony) work just as well as
article previews when there's no map involved. Each card here opens a full
article instead of a place's detail panel.

No traditional database -- articles are JSON files under content/articles/
(see content.py), written either by hand or through the /admin portal
(admin.py), which commits them straight to GitHub so they survive Render's
free-tier restarts and deploy the normal way.

Run it:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5050
"""
from flask import Flask, render_template, abort
import os

from content import get_articles, get_article, get_featured, get_related, CATEGORIES
from admin import admin_bp
from author_auth import author_bp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB -- plenty for a handful of phone photos per save
app.register_blueprint(admin_bp)
app.register_blueprint(author_bp)


@app.get("/")
def home():
    # The hero story (if one's been picked via the admin portal's "Feature
    # this story" checkbox) gets pulled out of the grid and rendered up top
    # instead -- `articles` is what the grid loops over, `all_articles` is
    # the full list including the hero, used for the inline-sheet JS data
    # so clicking the hero opens the same popup the grid cards do.
    all_articles = get_articles()
    featured = get_featured(all_articles)
    grid_articles = [a for a in all_articles if not featured or a["slug"] != featured["slug"]]
    return render_template(
        "index.html",
        articles=grid_articles,
        all_articles=all_articles,
        featured=featured,
        categories=CATEGORIES,
        active_category=None,
    )


@app.get("/category/<slug>")
def category_page(slug):
    if slug not in CATEGORIES:
        abort(404)
    # Category pages stay a plain grid -- no hero story there, same as a
    # section front usually looks different from a homepage.
    filtered = [a for a in get_articles() if a["category"] == slug]
    return render_template(
        "index.html",
        articles=filtered,
        all_articles=filtered,
        featured=None,
        categories=CATEGORIES,
        active_category=slug,
    )


@app.get("/article/<slug>")
def article_page(slug):
    article = get_article(slug)
    if not article:
        abort(404)
    related = get_related(article)
    return render_template(
        "article.html",
        article=article,
        related=related,
        categories=CATEGORIES,
    )


@app.errorhandler(404)
def not_found(e):
    # base.html's nav loops over `categories` unconditionally, so every page
    # that extends it -- this one included -- needs to pass it, not just the
    # routes that show a category-filtered list.
    return render_template("404.html", categories=CATEGORIES, active_category=None), 404


if __name__ == "__main__":
    app.run(debug=True, port=5050)
