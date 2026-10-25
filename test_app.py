from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import jwt

from schoolnet import create_app


class SchoolNetApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="schoolnet-test-"))
        self.database = self.tempdir / "schoolnet.db"
        self.config = {
            "TESTING": True,
            "DATABASE": str(self.database),
            "SCHOOLNET_PUBLISHER_KEY": "unit-test-editor-key",
            "SCHOOLNET_APP_SECRET": "unit-test-application-secret",
            "SECRET_KEY": "unit-test-application-secret",
            "MANUS_PROJECT_ID": "",
            "MANUS_JWT_SECRET": "",
            "MANUS_OAUTH_API_URL": "",
            "SCHOOLNET_ALLOW_INSECURE_COOKIES": True,
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def publisher_headers(self):
        response = self.client.post("/api/auth/local", json={"key": "unit-test-editor-key"})
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.get_json()['token']}"}

    def valid_post(self):
        return {
            "profile_id": "abebe",
            "category": "General",
            "title": "A carefully tested update",
            "content": "This fictional community update contains enough detail for validation.",
        }

    def test_seed_bootstrap_is_complete_and_idempotent(self):
        payload = self.client.get("/api/bootstrap").get_json()
        self.assertEqual([profile["id"] for profile in payload["profiles"]], ["abebe", "sara", "dan"])
        self.assertEqual([(p["name"], p["role"]) for p in payload["profiles"]], [
            ("Mr. Abebe", "Physics Teacher"),
            ("Ms. Sara", "Science Club Coordinator"),
            ("Coach Dan", "Sports Director"),
        ])
        self.assertEqual(len(payload["posts"]), 4)
        self.assertEqual(len(payload["events"]), 3)
        self.assertTrue(payload["posts"][0]["pinned"])
        self.assertEqual(payload["posts"][0]["title"], "A little preparation. A lot of possibility.")
        self.assertEqual(payload["stats"], {"members": 248, "staff": 3})
        self.assertFalse(payload["session"]["authenticated"])
        self.assertEqual(payload["session"]["mode"], "local")

        second_app = create_app(self.config)
        second_payload = second_app.test_client().get("/api/posts").get_json()
        self.assertEqual(len(second_payload["posts"]), 4)

    def test_valid_and_invalid_posts(self):
        headers = self.publisher_headers()
        created = self.client.post("/api/posts", json=self.valid_post(), headers=headers)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["post"]["profile_id"], "abebe")
        self.assertFalse(created.get_json()["post"]["pinned"])

        invalid = self.valid_post()
        invalid["title"] = " x "
        response = self.client.post("/api/posts", json=invalid, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_anonymous_user_cannot_publish_or_comment(self):
        post_response = self.client.post("/api/posts", json=self.valid_post())
        self.assertEqual(post_response.status_code, 401)
        comment_response = self.client.post("/api/posts/1/comments", json={"content": "A response"})
        self.assertEqual(comment_response.status_code, 401)

    def test_comments_use_authenticated_session_name(self):
        headers = self.publisher_headers()
        response = self.client.post(
            "/api/posts/1/comments",
            json={"content": "  Thank you for the revision reminder.  ", "name": "Ignored name"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 201)
        comment = response.get_json()["comment"]
        self.assertEqual(comment["name"], "School editor")
        self.assertEqual(comment["content"], "Thank you for the revision reminder.")

        comments = self.client.get("/api/posts/1/comments").get_json()["comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["name"], "School editor")
        post = next(item for item in self.client.get("/api/posts").get_json()["posts"] if item["id"] == 1)
        self.assertEqual(post["comments"], 1)

    def test_anonymous_likes_toggle_per_browser_client(self):
        headers = {"X-Client-ID": "8f3f3e7c-2e87-4a8b-9806-26b235673963"}
        first = self.client.post("/api/posts/1/like", headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json(), {"liked": True, "likes": 1})
        feed = self.client.get("/api/posts", headers=headers).get_json()["posts"]
        self.assertTrue(next(post for post in feed if post["id"] == 1)["liked"])
        another_feed = self.client.get("/api/posts").get_json()["posts"]
        self.assertFalse(next(post for post in another_feed if post["id"] == 1)["liked"])
        second = self.client.post("/api/posts/1/like", headers=headers)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json(), {"liked": False, "likes": 0})

    def test_database_persists_new_post_across_app_instances(self):
        headers = self.publisher_headers()
        self.assertEqual(self.client.post("/api/posts", json=self.valid_post(), headers=headers).status_code, 201)
        reopened = create_app(self.config).test_client()
        self.assertEqual(len(reopened.get("/api/posts").get_json()["posts"]), 5)

    def test_invalid_local_auth_is_rejected(self):
        response = self.client.post("/api/auth/local", json={"key": "incorrect-key"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())

        missing = self.client.post("/api/auth/local", json={})
        self.assertEqual(missing.status_code, 401)

    def test_page_assets_manifest_and_health(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SchoolNet", response.data)
        for path in ["/static/js/app.js", "/static/css/styles.css", "/static/images/logo.svg", "/static/images/science-lab.jpg"]:
            asset_response = self.client.get(path)
            self.assertEqual(asset_response.status_code, 200)
            asset_response.close()
        self.assertEqual(self.client.get("/health").get_json(), {"status": "ok"})
        self.assertEqual(self.client.get("/manus-routes.json").get_json(), {"routes": [{"path": "/", "title": "SchoolNet | Community feed"}]})
        self.assertEqual(self.client.get("/api/unknown").status_code, 404)
        self.assertEqual(self.client.post("/api/bootstrap").status_code, 405)
        self.assertIn("error", self.client.post("/api/bootstrap").get_json())

    def managed_client(self):
        config = {**self.config, "MANUS_PROJECT_ID": "test-project", "MANUS_JWT_SECRET": "unit-test-jwt-secret-at-least-32-chars",
                  "MANUS_OAUTH_API_URL": "https://api.example.com", "MANUS_OAUTH_PORTAL_URL": "https://portal.example.com"}
        return create_app(config).test_client(), config

    def test_managed_session_and_csrf(self):
        client, config = self.managed_client()
        claims = {"appId": "test-project", "openId": "staff-test", "name": "Test Editor", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        token = jwt.encode(claims, config["MANUS_JWT_SECRET"], algorithm="HS256")
        client.set_cookie("webdev_app_session", token)
        session = client.get("/api/bootstrap").get_json()["session"]
        self.assertTrue(session["can_publish"])
        self.assertEqual(client.post("/api/posts", json=self.valid_post()).status_code, 403)
        self.assertEqual(client.post("/api/posts", json=self.valid_post(), headers={"X-CSRF-Token": session["csrf_token"]}).status_code, 201)
        self.assertEqual(client.post("/api/auth/local", json={"key": "unit-test-editor-key"}).status_code, 403)
        claims["appId"] = "wrong-project"
        client.set_cookie("webdev_app_session", jwt.encode(claims, config["MANUS_JWT_SECRET"], algorithm="HS256"))
        self.assertFalse(client.get("/api/bootstrap").get_json()["session"]["authenticated"])

    def test_oauth_uses_browser_portal(self):
        client, _config = self.managed_client()
        response = client.get("/api/auth/login-url?origin=https://school.example.com")
        self.assertEqual(response.status_code, 200)
        url = urlparse(response.get_json()["login_url"])
        self.assertEqual(url.netloc, "portal.example.com")
        self.assertEqual(url.path, "/app-auth")
        self.assertEqual(parse_qs(url.query)["redirectUri"], ["https://school.example.com/auth/callback"])
        self.assertEqual(client.get("/api/auth/login-url?origin=http://evil.example.com").status_code, 400)
        failed = client.get("/auth/callback?code=bogus&state=bogus")
        self.assertEqual(failed.status_code, 302)
        self.assertEqual(failed.headers["Location"], "/?auth_error=1")

if __name__ == "__main__":
    unittest.main(verbosity=2)
