"""
The Sod Father -- a small, self-contained golf media site built around one
idea: the "cards" from a map-discovery app (Dony) work just as well as
article previews when there's no map involved. Each card here opens a full
article instead of a place's detail panel.

No database, no auth, no payments -- just Flask + an in-memory article list
(see articles.py) so this runs with nothing to configure. Swap articles.py
for a real database later without touching these routes or templates.

Run it:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5050
"""
from flask import Flask, render_template, abort

from articles import ARTICLES, CATEGORIES, get_article, get_related

app = Flask(__name__)


@app.get("/")
def home():
    category = None
    return render_template(
        "index.html",
        articles=ARTICLES,
        categories=CATEGORIES,
        active_category=category,
    )


@app.get("/category/<slug>")
def category_page(slug):
    if slug not in CATEGORIES:
        abort(404)
    filtered = [a for a in ARTICLES if a["category"] == slug]
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
