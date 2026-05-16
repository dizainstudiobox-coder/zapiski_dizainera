"""SQLite-хранилище: история тем, статус драфтов."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title_hint TEXT NOT NULL,
    keywords TEXT NOT NULL,
    angle TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    cover_path TEXT,
    status TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | published | rejected
    created_at TEXT NOT NULL,
    published_at TEXT,
    dzen_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_schema() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


# ---------------- topics ----------------

def upsert_topic(slug: str, title_hint: str, keywords: str, angle: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO topics (slug, title_hint, keywords, angle) "
            "VALUES (?, ?, ?, ?)",
            (slug, title_hint, keywords, angle),
        )


def pick_unused_topic() -> dict | None:
    """Берёт первую тему, которой ещё не было."""
    with conn() as c:
        row = c.execute(
            "SELECT * FROM topics WHERE used_at IS NULL "
            "ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def mark_topic_used(slug: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE topics SET used_at = ? WHERE slug = ?",
            (datetime.utcnow().isoformat(timespec="seconds"), slug),
        )


def topics_left() -> int:
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM topics WHERE used_at IS NULL"
        ).fetchone()
        return int(row["n"])


# ---------------- posts ----------------

def save_draft(topic_slug: str, title: str, body: str,
               cover_path: str | None) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO posts (topic_slug, title, body, cover_path, "
            "status, created_at) VALUES (?, ?, ?, ?, 'draft', ?)",
            (topic_slug, title, body, cover_path,
             datetime.utcnow().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def get_post(post_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        return dict(row) if row else None


def latest_pending() -> dict | None:
    """Последний драфт со статусом 'approved' (готов к публикации)."""
    with conn() as c:
        row = c.execute(
            "SELECT * FROM posts WHERE status = 'approved' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def latest_draft() -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM posts WHERE status = 'draft' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def set_status(post_id: int, status: str,
               dzen_url: str | None = None) -> None:
    with conn() as c:
        if status == "published":
            c.execute(
                "UPDATE posts SET status = ?, published_at = ?, "
                "dzen_url = ? WHERE id = ?",
                (status, datetime.utcnow().isoformat(timespec="seconds"),
                 dzen_url, post_id),
            )
        else:
            c.execute(
                "UPDATE posts SET status = ? WHERE id = ?",
                (status, post_id),
            )
