"""SQLite persistence helpers for SchoolNet."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from flask import current_app, g


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    bio TEXT NOT NULL,
    initials TEXT NOT NULL,
    color TEXT NOT NULL,
    department TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL REFERENCES profiles(id),
    category TEXT NOT NULL CHECK (category IN ('Academics', 'Clubs', 'Sports', 'General')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    image TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    date TEXT NOT NULL,
    date_label TEXT NOT NULL,
    month TEXT NOT NULL,
    day TEXT NOT NULL,
    time TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_likes (
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (post_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id, id);
CREATE INDEX IF NOT EXISTS idx_likes_post_id ON post_likes(post_id);
"""


POST_SELECT = """
SELECT
    p.id,
    p.profile_id,
    p.category,
    p.title,
    p.content,
    p.created_at,
    p.pinned,
    p.image,
    (SELECT COUNT(*) FROM post_likes AS pl WHERE pl.post_id = p.id) AS likes,
    (SELECT COUNT(*) FROM comments AS c WHERE c.post_id = p.id) AS comments,
    EXISTS(SELECT 1 FROM post_likes AS mine WHERE mine.post_id = p.id AND mine.client_id = ?) AS liked
FROM posts AS p
"""


def get_db() -> sqlite3.Connection:
    """Return the request-scoped SQLite connection."""
    if "db" not in g:
        database_path = Path(current_app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(database_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        g.db = connection
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_database() -> None:
    """Create tables then seed once, preserving existing local content."""
    connection = get_db()
    connection.executescript(SCHEMA)
    connection.commit()
    from .seed import seed_database

    seed_database(connection)


def row_to_post(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "category": row["category"],
        "title": row["title"],
        "content": row["content"],
        "created_at": row["created_at"],
        "pinned": bool(row["pinned"]),
        "image": row["image"],
        "likes": row["likes"],
        "comments": row["comments"],
        "liked": bool(row["liked"]),
    }


def get_post(post_id: int, client_id: str = "") -> dict[str, Any] | None:
    row = get_db().execute(POST_SELECT + " WHERE p.id = ?", (client_id, post_id)).fetchone()
    return row_to_post(row) if row else None


def list_posts(client_id: str = "") -> list[dict[str, Any]]:
    rows = get_db().execute(POST_SELECT + " ORDER BY p.created_at DESC, p.id DESC", (client_id,)).fetchall()
    return [row_to_post(row) for row in rows]


def list_profiles() -> list[dict[str, Any]]:
    rows = get_db().execute(
        "SELECT id, name, role, bio, initials, color, department FROM profiles ORDER BY rowid"
    ).fetchall()
    return [dict(row) for row in rows]


def list_events() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT id, title, category, date, date_label, month, day, time, location, description
        FROM events ORDER BY date ASC, id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]
