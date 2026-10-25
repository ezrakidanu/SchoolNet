"""Authentication helpers for managed Manus sessions and explicit local publishing."""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt
import requests
from flask import Blueprint, current_app, jsonify, redirect, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


auth_bp = Blueprint("auth", __name__)
OAUTH_NONCE_COOKIE = "schoolnet_oauth_nonce"
SESSION_COOKIE = "webdev_app_session"


def _manus_environment() -> bool:
    return current_app.config["AUTH_MODE"] in {"manus", "manus_misconfigured"}


def _empty_context() -> dict[str, Any]:
    return {
        "authenticated": False,
        "can_publish": False,
        "name": None,
        "open_id": None,
        "source": None,
        "mode": "manus" if _manus_environment() else "local",
    }


def _allowlisted(open_id: str) -> bool:
    configured = current_app.config.get("SCHOOLNET_PUBLISHER_OPEN_IDS", "")
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    return not allowed or open_id in allowed


def _decode_manus_jwt(token: str) -> dict[str, Any] | None:
    if current_app.config["AUTH_MODE"] != "manus":
        return None
    try:
        claims = jwt.decode(
            token,
            current_app.config["MANUS_JWT_SECRET"],
            algorithms=["HS256"],
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError:
        return None
    open_id = claims.get("openId")
    if claims.get("appId") != current_app.config["MANUS_PROJECT_ID"]:
        return None
    if not isinstance(open_id, str) or not open_id.strip():
        return None
    raw_name = claims.get("name") or claims.get("displayName") or "School editor"
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "School editor"
    return {
        "authenticated": True,
        "can_publish": _allowlisted(open_id),
        "name": name[:120],
        "open_id": open_id,
        "mode": "manus",
    }


def _local_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="schoolnet-local-bearer-v1")


def _decode_local_token(token: str) -> dict[str, Any] | None:
    if current_app.config["AUTH_MODE"] != "local":
        return None
    try:
        payload = _local_serializer().loads(
            token, max_age=current_app.config["LOCAL_TOKEN_MAX_AGE"]
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "schoolnet-local-publisher":
        return None
    return {
        "authenticated": True,
        "can_publish": True,
        "name": "School editor",
        "open_id": None,
        "source": "bearer",
        "mode": "local",
    }


def get_auth_context() -> dict[str, Any]:
    """Read a valid bearer first, then a managed session cookie when applicable."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if _manus_environment():
            context = _decode_manus_jwt(token)
        else:
            context = _decode_local_token(token)
        if context:
            context["source"] = "bearer"
            return context
        # A supplied malformed bearer should not be masked by an ambient cookie.
        return _empty_context()

    if current_app.config["AUTH_MODE"] == "manus":
        context = _decode_manus_jwt(request.cookies.get(SESSION_COOKIE, ""))
        if context:
            context["source"] = "cookie"
            return context
    return _empty_context()


def _csrf_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["MANUS_JWT_SECRET"], salt="schoolnet-csrf-v1")


def csrf_token_for(context: dict[str, Any]) -> str | None:
    if not context.get("authenticated") or context.get("mode") != "manus":
        return None
    return _csrf_serializer().dumps({"openId": context["open_id"]})


def csrf_is_valid(token: str | None, context: dict[str, Any]) -> bool:
    if not token or context.get("mode") != "manus" or not context.get("open_id"):
        return False
    try:
        payload = _csrf_serializer().loads(
            token, max_age=current_app.config["CSRF_TOKEN_MAX_AGE"]
        )
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(payload, dict) and hmac.compare_digest(
        str(payload.get("openId", "")), str(context["open_id"])
    )


def session_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "authenticated": bool(context["authenticated"]),
        "can_publish": bool(context["can_publish"]),
        "name": context["name"],
        "mode": context["mode"],
        "csrf_token": csrf_token_for(context),
    }


def csrf_required_and_valid(context: dict[str, Any]) -> bool:
    """Only cookie-authenticated writes need CSRF; explicit bearer is non-cookie auth."""
    if context.get("source") != "cookie":
        return True
    return csrf_is_valid(request.headers.get("X-CSRF-Token"), context)


def _cookie_settings() -> dict[str, Any]:
    insecure_local = bool(current_app.config["SCHOOLNET_ALLOW_INSECURE_COOKIES"])
    return {
        "secure": not insecure_local,
        "httponly": True,
        "samesite": "Lax" if insecure_local else "None",
        "path": "/",
    }


def _nonce_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="schoolnet-oauth-nonce-v1")


def _encode_state(payload: dict[str, str]) -> str:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_state(state: str) -> dict[str, Any] | None:
    try:
        padded = state + "=" * (-len(state) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _validated_origin(origin: str) -> str | None:
    try:
        parsed = urlparse(origin)
    except ValueError:
        return None
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        return None
    if parsed.params or parsed.query or parsed.fragment or not parsed.hostname:
        return None
    hostname = parsed.hostname.lower()
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "https" or (parsed.scheme == "http" and loopback):
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def _auth_error(_message: str, _status: int):
    # Callback failures always return to our own fixed page, never a supplied URL.
    response = redirect("/?auth_error=1")
    response.delete_cookie(OAUTH_NONCE_COOKIE, path="/")
    return response


def _configured_oauth() -> bool:
    return bool(
        current_app.config["AUTH_MODE"] == "manus"
        and current_app.config.get("MANUS_OAUTH_API_URL")
        and current_app.config.get("MANUS_OAUTH_PORTAL_URL")
    )


@auth_bp.get("/api/auth/login-url")
def login_url():
    if not _configured_oauth():
        return jsonify(error="Manus OAuth is not configured for this deployment."), 503
    origin = _validated_origin(request.args.get("origin", ""))
    if not origin:
        return jsonify(error="A valid HTTPS origin or local loopback origin is required."), 400

    nonce = secrets.token_urlsafe(24)
    redirect_uri = f"{origin}/auth/callback"
    state = _encode_state({"redirectUri": redirect_uri, "nonce": nonce})
    oauth_base = current_app.config["MANUS_OAUTH_PORTAL_URL"].rstrip("/")
    login = f"{oauth_base}/app-auth?" + urlencode(
        {
            "appId": current_app.config["MANUS_PROJECT_ID"],
            "redirectUri": redirect_uri,
            "responseType": "code",
            "state": state,
        }
    )
    response = jsonify(login_url=login)
    signed_nonce = _nonce_serializer().dumps(nonce)
    response.set_cookie(
        OAUTH_NONCE_COOKIE,
        signed_nonce,
        max_age=current_app.config["OAUTH_NONCE_MAX_AGE"],
        **_cookie_settings(),
    )
    return response


@auth_bp.get("/auth/callback")
def auth_callback():
    if not _configured_oauth():
        return _auth_error("Manus OAuth is not configured for this deployment.", 503)
    state = _decode_state(request.args.get("state", ""))
    code = request.args.get("code", "")
    cookie_value = request.cookies.get(OAUTH_NONCE_COOKIE, "")
    if not state or not code or not cookie_value:
        return _auth_error("Invalid or expired authentication state.", 400)
    try:
        cookie_nonce = _nonce_serializer().loads(
            cookie_value, max_age=current_app.config["OAUTH_NONCE_MAX_AGE"]
        )
    except (BadSignature, SignatureExpired):
        return _auth_error("Invalid or expired authentication state.", 400)

    redirect_uri = state.get("redirectUri")
    state_nonce = state.get("nonce")
    valid_redirect = isinstance(redirect_uri, str) and _validated_origin(
        redirect_uri.rsplit("/auth/callback", 1)[0]
    )
    if (
        not valid_redirect
        or not isinstance(state_nonce, str)
        or not hmac.compare_digest(state_nonce, str(cookie_nonce))
        or redirect_uri != f"{valid_redirect}/auth/callback"
    ):
        return _auth_error("Invalid or expired authentication state.", 400)

    base = current_app.config["MANUS_OAUTH_API_URL"].rstrip("/")
    try:
        exchange_response = requests.post(
            f"{base}/webdev.v1.WebDevAuthPublicService/ExchangeToken",
            json={
                "clientId": current_app.config["MANUS_PROJECT_ID"],
                "grantType": "authorization_code",
                "code": code,
                "redirectUri": redirect_uri,
            },
            timeout=10,
        )
        exchange_response.raise_for_status()
        exchange_data = exchange_response.json()
        if not isinstance(exchange_data, dict):
            raise ValueError("OAuth exchange returned an invalid response")
        access_token = exchange_data.get("accessToken") or exchange_data.get("access_token")
        if not access_token and isinstance(exchange_data.get("data"), dict):
            access_token = exchange_data["data"].get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("OAuth exchange did not return an access token")

        user_response = requests.post(
            f"{base}/webdev.v1.WebDevAuthPublicService/GetUserInfo",
            json={"accessToken": access_token},
            timeout=10,
        )
        user_response.raise_for_status()
        user_data = user_response.json()
        if not isinstance(user_data, dict):
            raise ValueError("OAuth user response was invalid")
        user = user_data.get("user") if isinstance(user_data.get("user"), dict) else user_data
        if isinstance(user_data.get("data"), dict):
            user = user_data["data"].get("user", user_data["data"])
        open_id = user.get("openId") if isinstance(user, dict) else None
        raw_name = (user.get("name") or user.get("displayName")) if isinstance(user, dict) else None
        if not isinstance(open_id, str) or not open_id.strip():
            raise ValueError("OAuth user response did not return an openId")
    except (requests.RequestException, ValueError, TypeError):
        return _auth_error("Unable to complete Manus authentication. Please try again.", 502)

    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "School editor"
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "appId": current_app.config["MANUS_PROJECT_ID"],
            "openId": open_id,
            "name": name[:120],
            "iat": now,
            "exp": now + timedelta(hours=12),
        },
        current_app.config["MANUS_JWT_SECRET"],
        algorithm="HS256",
    )
    response = redirect("/")
    response.set_cookie(SESSION_COOKIE, token, max_age=12 * 60 * 60, **_cookie_settings())
    response.delete_cookie(OAUTH_NONCE_COOKIE, path="/")
    return response


@auth_bp.post("/api/auth/local")
def local_auth():
    if current_app.config["AUTH_MODE"] != "local":
        return jsonify(error="Local authentication is unavailable when Manus auth is configured."), 403
    configured_key = current_app.config.get("SCHOOLNET_PUBLISHER_KEY", "")
    if not configured_key:
        return jsonify(error="Local publishing is not configured."), 403
    body = request.get_json(silent=True)
    supplied_key = body.get("key") if isinstance(body, dict) else None
    if not isinstance(supplied_key, str) or not hmac.compare_digest(supplied_key, configured_key):
        return jsonify(error="Invalid publisher key."), 401

    token = _local_serializer().dumps({"kind": "schoolnet-local-publisher"})
    context = {
        "authenticated": True,
        "can_publish": True,
        "name": "School editor",
        "open_id": None,
        "source": "bearer",
        "mode": "local",
    }
    return jsonify(token=token, session=session_payload(context))


@auth_bp.post("/api/auth/logout")
def logout():
    response = jsonify(ok=True)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
