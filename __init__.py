"""SchoolNet Flask application factory."""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _configure_auth_mode(app: Flask) -> None:
    """Derive auth mode after environment and optional test settings are loaded."""
    project_id = app.config.get("MANUS_PROJECT_ID")
    jwt_secret = app.config.get("MANUS_JWT_SECRET")
    if project_id and jwt_secret:
        app.config["AUTH_MODE"] = "manus"
    elif project_id or jwt_secret:
        # Do not quietly enable a local bypass when a managed deployment is incomplete.
        app.config["AUTH_MODE"] = "manus_misconfigured"
    else:
        app.config["AUTH_MODE"] = "local"


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    """Build a SchoolNet application with local, durable SQLite storage."""
    load_dotenv()
    project_root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
        static_url_path="/static",
    )

    # An unset application secret is deliberately generated at process start rather
    # than embedded in source. Configure SCHOOLNET_APP_SECRET for stable local tokens.
    configured_app_secret = os.getenv("SCHOOLNET_APP_SECRET")
    app.config.from_mapping(
        SECRET_KEY=configured_app_secret or secrets.token_urlsafe(32),
        DATABASE=str(project_root / "instance" / "schoolnet.db"),
        SCHOOLNET_PUBLISHER_KEY=os.getenv("SCHOOLNET_PUBLISHER_KEY", ""),
        SCHOOLNET_PUBLISHER_OPEN_IDS=os.getenv("SCHOOLNET_PUBLISHER_OPEN_IDS", ""),
        SCHOOLNET_APP_SECRET=configured_app_secret or "",
        SCHOOLNET_ALLOW_INSECURE_COOKIES=_as_bool(
            os.getenv("SCHOOLNET_ALLOW_INSECURE_COOKIES"), default=False
        ),
        MANUS_PROJECT_ID=os.getenv("MANUS_PROJECT_ID", ""),
        MANUS_JWT_SECRET=os.getenv("MANUS_JWT_SECRET", ""),
        MANUS_OAUTH_API_URL=os.getenv("MANUS_OAUTH_API_URL", ""),
        MANUS_OAUTH_PORTAL_URL=os.getenv("MANUS_OAUTH_PORTAL_URL", ""),
        LOCAL_TOKEN_MAX_AGE=12 * 60 * 60,
        CSRF_TOKEN_MAX_AGE=60 * 60,
        OAUTH_NONCE_MAX_AGE=10 * 60,
    )
    if test_config:
        app.config.update(test_config)

    app.config["SCHOOLNET_ALLOW_INSECURE_COOKIES"] = _as_bool(
        app.config.get("SCHOOLNET_ALLOW_INSECURE_COOKIES"), default=False
    )
    _configure_auth_mode(app)

    from .db import close_db, init_database
    from .routes import api_bp, site_bp
    from .auth import auth_bp

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_database()

    app.register_blueprint(site_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/api/") or request.path == "/auth/callback":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(404)
    def not_found(_error: Exception):
        if request.path.startswith("/api/"):
            return jsonify(error="API endpoint not found."), 404
        return render_template("index.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        if request.path.startswith("/api/"):
            return jsonify(error="This HTTP method is not supported by this API endpoint."), 405
        return error

    return app
