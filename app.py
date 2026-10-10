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

from content import get_articles, get_article, get_related, CATEGORIES
from admin import admin_bp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB -- plenty for a handful of phone photos per save
app.register_blueprint(admin_bp)


@app.get("/")
def home():
    category = None
    return render_template(
        "index.html",
        articles=get_articles(),
        categories=CATEGORIES,
        active_category=category,
    )


@app.get("/category/<slug>")
def category_page(slug):
    if slug not in CATEGORIES:
        abort(404)
    filtered = [a for a in get_articles() if a["category"] == slug]
    return render_template(
        "index.html",
        articles=filtered,
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
