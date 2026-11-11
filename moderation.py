"""
Rules for keeping comments safe -- shared by readers.py (the comment API) and
safety.py (reports, blocking, contact form, account deletion, admin screens).

No Flask in here on purpose, so both of those can import it without a loop.

What "hidden" means: a comment is hidden from everyone once it has
HIDE_AFTER_REPORTS reports from different readers, until the owner looks at it
in /admin/reports and presses Keep (which writes a row to comment_moderation
and makes the comment visible again for good).
"""
import os
import re

import db

HIDE_AFTER_REPORTS = 3
MAX_LINKS_PER_COMMENT = 2

REPORT_REASONS = {
    "spam": "Spam or advertising",
    "abuse": "Harassment or hate",
    "inappropriate": "Sexual or violent content",
    "other": "Something else",
}

# SQL pieces for queries on `comments c`. Bind :hide_after (use HIDE_PARAMS).
HIDDEN_SQL = (
    "((SELECT COUNT(*) FROM comment_reports cr WHERE cr.comment_id = c.id) >= :hide_after "
    "AND NOT EXISTS (SELECT 1 FROM comment_moderation cm WHERE cm.comment_id = c.id))"
)
VISIBLE_SQL = "NOT " + HIDDEN_SQL
HIDE_PARAMS = {"hide_after": HIDE_AFTER_REPORTS}


# ── Word filter ──────────────────────────────────────────────────────────
# A small starter list of strong profanity. Add more without touching code by
# setting COMMENT_BLOCKED_TERMS on Render (comma-separated, e.g. "foo,bar").
# It matches whole words (and common endings like -ing / -s), so ordinary
# words that merely contain a listed word are not caught.

DEFAULT_BLOCKED_TERMS = (
    "fuck", "shit", "cunt", "bitch", "asshole", "bastard", "dickhead", "motherfucker",
    "bullshit", "slut", "whore",
)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_link_re = re.compile(r"(https?://|www\.|[a-z0-9-]+\.(com|net|org|io|co|xyz|ru|top|click|link)\b)", re.I)


def blocked_terms():
    extra = [t.strip().lower() for t in os.environ.get("COMMENT_BLOCKED_TERMS", "").split(",") if t.strip()]
    return list(DEFAULT_BLOCKED_TERMS) + extra


def check_comment_text(body):
    """None if the comment is fine, else a short error code:
    'blocked_words' or 'too_many_links'."""
    if len(_link_re.findall(body)) > MAX_LINKS_PER_COMMENT:
        return "too_many_links"
    plain = body.lower().translate(_LEET)
    terms = blocked_terms()
    squashed = re.sub(r"(\w)\1+", r"\1", plain)  # "fuuuuck" -> "fuck"
    for term in terms:
        word = re.escape(term) + r"(s|es|ed|ing|er|ers|y)?"
        if re.search(r"\b" + word + r"\b", plain):
            return "blocked_words"
        short = re.sub(r"(\w)\1+", r"\1", term)
        if re.search(r"\b" + re.escape(short) + r"(s|es|ed|ing|er|ers|y)?\b", squashed):
            return "blocked_words"
        # "f u c k" / "f.u.c.k": every letter on its own, split by spaces or dots.
        spaced = r"[\s._\-]+".join(re.escape(ch) for ch in term)
        if len(term) > 2 and re.search(r"(?<![a-z0-9])" + spaced + r"(?![a-z0-9])", plain):
            return "blocked_words"
    # "f*ck": a * stands in for one hidden letter.
    for token in re.findall(r"\S*\*\S*", body.lower()):
        token = token.strip(".,;:!?\"'()")
        pattern = re.compile("".join("." if ch == "*" else re.escape(ch) for ch in token))
        for term in terms:
            for suffix in ("", "s", "es", "ed", "ing", "er", "ers", "y"):
                if len(term + suffix) == len(token) and pattern.fullmatch(term + suffix):
                    return "blocked_words"
    return None


# ── Cleaning up rows ─────────────────────────────────────────────────────

def delete_comment(comment_id):
    """Remove one comment and anything that pointed at it."""
    db.execute("DELETE FROM comment_reports WHERE comment_id = :c", c=comment_id)
    db.execute("DELETE FROM comment_moderation WHERE comment_id = :c", c=comment_id)
    db.execute("DELETE FROM comments WHERE id = :c", c=comment_id)


def delete_reader(reader_id, email=None, remove_newsletter=False):
    """Erase a reader and everything of theirs: saved stories and places,
    their comments (and the reports about them), reports they made, blocks in
    both directions, their daily Ask counter -- then the account itself."""
    mine = "(SELECT id FROM comments WHERE reader_id = :r)"
    db.execute(f"DELETE FROM comment_reports WHERE comment_id IN {mine}", r=reader_id)
    db.execute(f"DELETE FROM comment_moderation WHERE comment_id IN {mine}", r=reader_id)
    db.execute("DELETE FROM comments WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM comment_reports WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM reader_blocks WHERE reader_id = :r OR blocked_id = :r", r=reader_id)
    db.execute("DELETE FROM saved_articles WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM saved_places WHERE reader_id = :r", r=reader_id)
    db.execute("DELETE FROM ask_usage WHERE key = :k", k="r:" + reader_id)
    if remove_newsletter and email:
        db.execute("DELETE FROM newsletter_subscribers WHERE email = :e", e=email)
        db.execute("DELETE FROM newsletter_unsubscribes WHERE email = :e", e=email)
    db.execute("DELETE FROM readers WHERE id = :r", r=reader_id)


def is_banned(email):
    return bool(db.query_one("SELECT 1 AS x FROM banned_emails WHERE email = :e", e=(email or "").lower()))
