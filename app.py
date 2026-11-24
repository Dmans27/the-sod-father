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
from textlinks import inline_links
from flask import Flask, render_template, abort, make_response, request
import os

from content import (get_articles, get_article, get_featured, get_related, CATEGORIES,
                     is_map_embed_url, map_embed_hosts, map_links, place_embed_url)
from admin import admin_bp
from author_auth import author_bp
from newsletter import newsletter_bp
from ask import ask_bp
from walk_planner import walk_bp
from walk_feed import feed_bp, feed_cards
from community import community_bp, feed_city
from footprints import footprints_bp
from friends import friends_bp
from group_maps import group_bp
import city_feed
from city_feed import city_bp
from onboarding import onboarding_bp
import safety
from seo import seo_bp, register as register_seo
import readers

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64MB -- a handful of phone photos plus a few short place videos (12MB each) per save
app.register_blueprint(admin_bp)
app.register_blueprint(author_bp)
app.register_blueprint(newsletter_bp)  # newsletter signup (see newsletter.py)
app.register_blueprint(ask_bp)  # "Ask" chat answering from the articles (see ask.py)
app.register_blueprint(walk_bp)  # "Plan a walk": AI walking routes from the directory (see walk_planner.py)
app.register_blueprint(feed_bp)  # "This week": the weekly AI walk feed, approved by the owner (see walk_feed.py)
app.register_blueprint(community_bp)  # walks readers post for other walkers in their city (see community.py)
app.register_blueprint(onboarding_bp)  # email code + the welcome questions after sign-up (see onboarding.py)
app.register_blueprint(city_bp)  # the city search bar on the stories page (see city_feed.py)
app.register_blueprint(footprints_bp)  # walkers rate places 1-5 footprints: worth the walk? (see footprints.py)
app.register_blueprint(friends_bp)  # @handles, friend requests, invite links (see friends.py)
app.register_blueprint(group_bp)  # maps friends plan together (see group_maps.py)
app.register_blueprint(seo_bp)  # robots.txt, sitemap.xml, feed.xml, share images (see seo.py)
register_seo(app)  # canonical addresses + optional analytics for every page
# Interactive map blocks (see content.py): templates use these two to check
# that a map's address is on the allowed list before putting it in an iframe.
app.jinja_env.filters["inline_links"] = inline_links  # [words](https://...) in story text (see textlinks.py)
app.jinja_env.globals.update(map_embed_ok=is_map_embed_url, map_embed_hosts=map_embed_hosts,
                             place_embed_url=place_embed_url, map_links=map_links)
readers.register(app)  # reader accounts: sign up, save, comment (see readers.py)
safety.register(app)  # privacy/terms/contact pages, report + block, delete account, admin review screens (see safety.py)


@app.get("/")
def home():
    # The city feed (city_feed.py): one city at a time -- where you are, or the
    # city you searched for in the bar at the top. Its stories come first (the
    # ones you marked "Feature this story" lead), then its walks, then every
    # other story under "More stories". `all_articles` is the full set, used
    # for the inline-sheet JS data so any card opens the same popup.
    city, cookie = city_feed.current_city()
    all_articles = get_articles()
    featured_slugs = {a["slug"] for a in get_featured(all_articles)}
    in_city = [a for a in all_articles if city_feed.story_in_city(a, city)]
    in_city.sort(key=lambda a: a["slug"] not in featured_slugs)   # featured first, then newest (stable)
    city_slugs = {a["slug"] for a in in_city}
    home_city = city_feed.is_home(city)
    reply = make_response(render_template(
        "index.html",
        city=city,
        featured=in_city[:2],                       # hero cards
        city_articles=in_city[2:],                  # the rest of the city's stories
        articles=[a for a in all_articles if a["slug"] not in city_slugs],   # "More stories"
        all_articles=all_articles,
        categories=CATEGORIES,
        active_category=None,
        comment_counts=readers.comment_counts(),
        walk_feed=feed_cards() if home_city else [],   # "Walks this week" is the home city's (walk_feed.py)
        feed_city=city["name"],
    ))
    if cookie:   # remember the city you searched for
        reply.set_cookie(city_feed.COOKIE, cookie, max_age=365 * 86400, samesite="Lax", secure=request.is_secure)
    return reply


@app.get("/category/<slug>")
@app.get("/category/<slug>/<sub>")
def category_page(slug, sub=None):
    if slug not in CATEGORIES:
        abort(404)
    # A subcategory page (/category/golf/equipment) only exists for the
    # subcategories listed under that category in content.py.
    if sub is not None and sub not in (CATEGORIES[slug].get("subcategories") or {}):
        abort(404)
    # Category pages stay a plain list -- no hero stories there, same as a
    # section front usually looks different from a homepage.
    filtered = [
        a for a in get_articles()
        if a["category"] == slug and (sub is None or a.get("subcategory") == sub)
    ]
    return render_template(
        "index.html",
        articles=filtered,
        all_articles=filtered,
        featured=[],
        categories=CATEGORIES,
        active_category=slug,
        active_sub=sub,
        comment_counts=readers.comment_counts(),
        walk_feed=feed_cards() if slug == "travel" else [],   # walks fit the Travel section too
        feed_city=feed_city(),
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
        comment_counts=readers.comment_counts(),
    )


@app.errorhandler(404)
def not_found(e):
    # base.html's nav loops over `categories` unconditionally, so every page
    # that extends it -- this one included -- needs to pass it, not just the
    # routes that show a category-filtered list.
    return render_template("404.html", categories=CATEGORIES, active_category=None), 404


if __name__ == "__main__":
    app.run(debug=True, port=5050)
