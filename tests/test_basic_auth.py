import base64
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from flask import Flask

from app import _apply_basic_auth
from app.config import Config


def auth_header(username="test-user", password="test-only-long-password"):
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


class BasicAuthTests(unittest.TestCase):
    def setUp(self):
        # 認証フックのみを検証し、実際の設定・DB・スケジューラには触れない。
        self.app = self.make_app()
        self.client = self.app.test_client()
        clock = patch("app.services.basic_auth.time.monotonic", return_value=1000.0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    @staticmethod
    def make_app(**overrides):
        app = Flask(__name__)
        app.config.from_object(Config)
        app.config.update(
            TESTING=True, APP_USERNAME="test-user", APP_PASSWORD="test-only-long-password",
        )
        app.config.update(overrides)
        _apply_basic_auth(app)
        for path in ("/", "/other", "/ping", "/favicon.ico", "/static/apple-touch-icon.png", "/static/icon-192.png"):
            app.add_url_rule(path, endpoint=path, view_func=lambda: "ok", methods=["GET", "POST"])
        return app

    def fail(self, **kwargs):
        return self.client.get("/", headers=auth_header(password="wrong-password"), **kwargs)

    def lock_out(self):
        for _ in range(4):
            self.assertEqual(self.fail().status_code, 401)
        response = self.fail()
        self.assertEqual(response.status_code, 429)
        return response

    def test_initial_challenge_does_not_consume_attempts(self):
        for _ in range(10):
            response = self.client.get("/")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["WWW-Authenticate"], 'Basic realm="kakeibo"')
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/", headers=auth_header()).status_code, 200)
        self.lock_out()

    def test_lockout_headers_and_credentials_are_not_checked_during_lockout(self):
        response = self.lock_out()
        self.assertEqual(response.headers["Retry-After"], "300")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("WWW-Authenticate", response.headers)
        # 制限中に正しい資格情報を送っても、照合処理まで到達しない。
        with patch("app.services.basic_auth.hmac.compare_digest") as compare:
            self.assertEqual(self.client.get("/", headers=auth_header()).status_code, 429)
        compare.assert_not_called()
        self.assertEqual(self.client.post("/other", headers=auth_header()).status_code, 429)
        self.assertEqual(self.client.get("/").status_code, 429)

    def test_ip_username_path_and_forwarded_header_changes_cannot_bypass_limit(self):
        for i in range(5):
            response = self.client.get(
                "/" if i % 2 else "/other",
                headers={**auth_header(username=f"user-{i}"), "X-Forwarded-For": f"192.0.2.{i}"},
                environ_overrides={"REMOTE_ADDR": f"198.51.100.{i}"},
            )
            self.assertEqual(response.status_code, 401 if i < 4 else 429)
        self.assertEqual(self.client.get("/other", headers=auth_header()).status_code, 429)

    def test_successful_basic_auth_does_not_consume_or_reset_failure_budget(self):
        for _ in range(20):
            self.assertEqual(self.client.get("/", headers=auth_header()).status_code, 200)
        for _ in range(4):
            self.assertEqual(self.fail().status_code, 401)
            self.assertEqual(self.client.get("/", headers=auth_header()).status_code, 200)
        self.assertEqual(self.fail().status_code, 429)

    def test_expired_failures_do_not_cause_lockout(self):
        for _ in range(4):
            self.fail()
        self.clock.return_value = 1300.0
        self.lock_out()

    def test_lockout_expires_and_blocked_requests_do_not_extend_it(self):
        self.lock_out()
        self.clock.return_value = 1299.1
        response = self.fail()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "1")
        self.clock.return_value = 1300.0
        self.assertEqual(self.client.get("/", headers=auth_header()).status_code, 200)
        self.lock_out()

    def test_public_health_and_icons_remain_available_during_lockout(self):
        self.lock_out()
        for path in ("/ping", "/favicon.ico", "/static/apple-touch-icon.png", "/static/icon-192.png"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=auth_header(password="wrong")).status_code, 200)

    def test_malformed_and_non_basic_authorization_count_as_failures(self):
        for i, value in enumerate(("Basic !!!", "Basic", "Bearer token", "Digest username=test", "")):
            response = self.client.get("/", headers={"Authorization": value})
            self.assertEqual(response.status_code, 401 if i < 4 else 429)

    def test_wrong_username_and_password_have_same_response(self):
        username_failure = self.client.get("/", headers=auth_header(username="wrong"))
        password_failure = self.fail()
        self.assertEqual(username_failure.status_code, password_failure.status_code)
        self.assertEqual(username_failure.data, password_failure.data)
        self.assertEqual(dict(username_failure.headers), dict(password_failure.headers))

    def test_limits_are_isolated_between_app_instances(self):
        self.lock_out()
        separate = self.make_app().test_client()
        self.assertEqual(separate.get("/", headers=auth_header()).status_code, 200)

    def test_configurable_threshold_window_and_lockout(self):
        client = self.make_app(
            BASIC_AUTH_MAX_FAILURES=2, BASIC_AUTH_WINDOW_SECONDS=10, BASIC_AUTH_LOCKOUT_SECONDS=20,
        ).test_client()
        wrong = auth_header(password="wrong")
        self.assertEqual(client.get("/", headers=wrong).status_code, 401)
        self.clock.return_value = 1010.0
        self.assertEqual(client.get("/", headers=wrong).status_code, 401)
        response = client.get("/", headers=wrong)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "20")
        self.clock.return_value = 1030.0
        self.assertEqual(client.get("/", headers=auth_header()).status_code, 200)

    def test_concurrent_requests_cannot_exceed_failure_threshold(self):
        barrier = threading.Barrier(12)

        def attempt(_):
            with self.app.test_client() as client:
                barrier.wait(timeout=5)
                return client.get("/", headers=auth_header(password="wrong")).status_code

        with ThreadPoolExecutor(max_workers=12) as executor:
            statuses = list(executor.map(attempt, range(12)))
        self.assertEqual(statuses.count(401), 4)
        self.assertEqual(statuses.count(429), 8)
