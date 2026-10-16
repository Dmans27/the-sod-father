"""
Tiny database layer for reader accounts, saved stories and comments.

Articles themselves still live in JSON files (content.py) -- this is only
for the data that *readers* create, which has to survive Render deploys
(the free web service's disk is wiped on every deploy, so a local file is
no good in production).

  * In production set DATABASE_URL (Render Postgres hands you one).
  * With no DATABASE_URL it falls back to a local SQLite file under
    instance/ so `python app.py` just works on your Mac. That file is
    git-ignored and would NOT survive a Render deploy -- the startup log
    warns if that fallback is ever used on Render.

SQLAlchemy Core with plain SQL text keeps the same queries working on both
databases. IDs are random hex strings and timestamps are ISO-8601 text, so
there's no database-specific column types to worry about.
"""
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

_engine = None


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Render (and Heroku) hand out "postgres://..." but SQLAlchemy
        # wants "postgresql://...". Also pin the psycopg2 driver.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url
    if os.environ.get("RENDER"):
        print("WARNING: DATABASE_URL is not set on Render -- reader accounts "
              "will be stored in a local SQLite file that is wiped on every deploy.")
    instance = Path(__file__).parent / "instance"
    instance.mkdir(exist_ok=True)
    return "sqlite:///" + str(instance / "readers.db")


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
        init_db(_engine)
    return _engine


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS readers (
        id            TEXT PRIMARY KEY,
        email         TEXT NOT NULL UNIQUE,
        display_name  TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS saved_articles (
        reader_id  TEXT NOT NULL,
        slug       TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (reader_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comments (
        id         TEXT PRIMARY KEY,
        slug       TEXT NOT NULL,
        reader_id  TEXT NOT NULL,
        body       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_comments_slug ON comments (slug, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_saved_reader ON saved_articles (reader_id, created_at)",
]


def init_db(engine):
    with engine.begin() as conn:
        for statement in SCHEMA:
            conn.execute(text(statement))


def new_id():
    return secrets.token_hex(8)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def query(sql, **params):
    """Run a SELECT and return a list of dicts."""
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def query_one(sql, **params):
    rows = query(sql, **params)
    return rows[0] if rows else None


def execute(sql, **params):
    """Run an INSERT/UPDATE/DELETE in its own transaction; returns rowcount."""
    with get_engine().begin() as conn:
        return conn.execute(text(sql), params).rowcount
