"""Frontend page and JSON API endpoints for SchoolNet."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, render_template, request

from .auth import csrf_required_and_valid, get_auth_context, session_payload
from .db import get_db, get_post, list_events, list_posts, list_profiles

site_bp = Blueprint("site", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")

CATEGORIES = {"Academics", "Clubs", "Sports", "General"}
CLIENT_ID_PATTERN = re.compile(r"^[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$")


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _error(message: str, status: int):
    return jsonify(error=message), status


def _body() -> dict[str, Any] | None:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


def _publisher_context():
    context = get_auth_context()
    if not context["authenticated"]:
        return None, _error("Authentication is required to publish.", 401)
    if not context["can_publish"]:
        return None, _error("This account is not permitted to publish.", 403)
    if not csrf_required_and_valid(context):
        return None, _error("A valid X-CSRF-Token is required for this cookie-authenticated request.", 403)
    return context, None


def _validate_post_input(body: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    profile_id = body.get("profile_id")
    category = body.get("category")
    title = body.get("title")
    content = body.get("content")
    if not (isinstance(profile_id, str) and isinstance(category, str)
            and isinstance(title, str) and isinstance(content, str)):
        return None, "profile_id, category, title, and content must be strings."
    normalized = {
        "profile_id": profile_id.strip(),
        "category": category.strip(),
        "title": title.strip(),
        "content": content.strip(),
    }
    if normalized["category"] not in CATEGORIES:
        return None, "category must be Academics, Clubs, Sports, or General."
    if not 3 <= len(normalized["title"]) <= 120:
        return None, "title must be between 3 and 120 characters after trimming."
    if not 10 <= len(normalized["content"]) <= 3000:
        return None, "content must be between 10 and 3000 characters after trimming."
    exists = get_db().execute(
        "SELECT 1 FROM profiles WHERE id = ?", (normalized["profile_id"],)
    ).fetchone()
    if not exists:
        return None, "profile_id must be an approved SchoolNet profile."
    return normalized, None


@site_bp.get("/")
def index():
    return render_template("index.html")


@site_bp.get("/manus-routes.json")
def manus_routes():
    return jsonify(routes=[{"path": "/", "title": "SchoolNet | Community feed"}])


@site_bp.get("/health")
def health():
    get_db().execute("SELECT 1").fetchone()
    return jsonify(status="ok")


@api_bp.get("/bootstrap")
def bootstrap():
    context = get_auth_context()
    return jsonify(
        profiles=list_profiles(),
        posts=list_posts(request.headers.get("X-Client-ID", "")),
        events=list_events(),
        session=session_payload(context),
        stats={"members": 248, "staff": 3},
    )


@api_bp.route("/posts", methods=["GET", "POST"])
def posts():
    if request.method == "GET":
        return jsonify(posts=list_posts(request.headers.get("X-Client-ID", "")))

    _context, auth_error = _publisher_context()
    if auth_error:
        return auth_error
    body = _body()
    if body is None:
        return _error("Expected a JSON object request body.", 400)
    payload, validation_error = _validate_post_input(body)
    if payload is None:
        return _error(validation_error or "Invalid announcement.", 400)

    db = get_db()
    with db:
        cursor = db.execute(
            """
            INSERT INTO posts (profile_id, category, title, content, created_at, pinned, image)
            VALUES (?, ?, ?, ?, ?, 0, NULL)
            """,
            (
                payload["profile_id"],
                payload["category"],
                payload["title"],
                payload["content"],
                _iso_now(),
            ),
        )
    assert cursor.lastrowid is not None
    post = get_post(cursor.lastrowid)
    return jsonify(post=post), 201


@api_bp.route("/posts/<int:post_id>/comments", methods=["GET", "POST"])
def comments(post_id: int):
    if not get_post(post_id):
        return _error("Post not found.", 404)
    db = get_db()
    if request.method == "GET":
        rows = db.execute(
            """
            SELECT id, name, content, created_at FROM comments
            WHERE post_id = ? ORDER BY id ASC
            """,
            (post_id,),
        ).fetchall()
        return jsonify(comments=[dict(row) for row in rows])

    context, auth_error = _publisher_context()
    if auth_error:
        return auth_error
    assert context is not None
    body = _body()
    content = body.get("content") if body else None
    if not isinstance(content, str):
        return _error("content must be a string.", 400)
    content = content.strip()
    if not 1 <= len(content) <= 500:
        return _error("content must be between 1 and 500 characters after trimming.", 400)

    created_at = _iso_now()
    with db:
        cursor = db.execute(
            """
            INSERT INTO comments (post_id, name, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (post_id, context["name"], content, created_at),
        )
        # The displayed comment count is derived from the same committed database
        # transaction rather than supplied by a client.
        db.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,)).fetchone()
    return jsonify(
        comment={
            "id": cursor.lastrowid,
            "name": context["name"],
            "content": content,
            "created_at": created_at,
        }
    ), 201


@api_bp.post("/posts/<int:post_id>/like")
def like(post_id: int):
    if not get_post(post_id):
        return _error("Post not found.", 404)
    client_id = request.headers.get("X-Client-ID", "").strip()
    if len(client_id) > 80 or not CLIENT_ID_PATTERN.fullmatch(client_id):
        return _error("X-Client-ID must be a UUID-like client identifier up to 80 characters.", 400)

    db = get_db()
    with db:
        deleted = db.execute(
            "DELETE FROM post_likes WHERE post_id = ? AND client_id = ?", (post_id, client_id)
        )
        if deleted.rowcount:
            liked = False
        else:
            db.execute(
                "INSERT INTO post_likes (post_id, client_id, created_at) VALUES (?, ?, ?)",
                (post_id, client_id, _iso_now()),
            )
            liked = True
        likes = db.execute(
            "SELECT COUNT(*) AS count FROM post_likes WHERE post_id = ?", (post_id,)
        ).fetchone()["count"]
    return jsonify(likes=likes, liked=liked)
