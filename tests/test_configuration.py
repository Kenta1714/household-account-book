import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask
from flask.cli import FlaskGroup
from click.testing import CliRunner
from google.auth.exceptions import RefreshError, TransportError
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.config import ConfigError, configure_app
from app.extensions import db
from app.models import Category, Setting, Transaction
from app.services import gmail_service
from tests.helpers import check_database_schema, migrate_database, seed_categories


CLIENT_CONFIG = {
    "web": {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}
TOKEN = json.dumps({
    "token": "test-access-token", "refresh_token": "test-refresh-token",
    "client_id": "test-client-id", "client_secret": "test-client-secret",
    "token_uri": "https://oauth2.googleapis.com/token",
})


class IsolatedEnvironment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        credentials = self.root / "credentials.json"
        credentials.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
        self.env = {
            "APP_ENV": "development",
            "FLASK_SECRET_KEY": "test-only-secret-key-with-at-least-32-characters",
            "APP_USERNAME": "test-user",
            "APP_PASSWORD": "test-password",
            "DATABASE_URL": "sqlite:///" + str(self.root / "household.db"),
            "GMAIL_REDIRECT_URI": "http://localhost:8080/gmail/oauth/callback",
            "CREDENTIALS_FILE": str(credentials),
        }
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Never read the user's .env or credentials.
        self.dotenv = patch("app.config.load_dotenv")
        self.load_dotenv = self.dotenv.start()
        self.addCleanup(self.dotenv.stop)

    def production(self):
        os.environ.update({
            "APP_ENV": "production",
            "DATABASE_URL": "postgres://test-user:test-password@localhost/test-db",
            "GMAIL_REDIRECT_URI": "https://test.example/gmail/oauth/callback",
            "GOOGLE_CREDENTIALS_JSON": json.dumps(CLIENT_CONFIG),
        })
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)

    def make_app(self, *, prepare_database=True, **overrides):
        # Explicit test overrides only; all required environment settings are still validated.
        app = create_app({"TESTING": True, "SCHEDULER_ENABLED": False, **overrides})
        self.addCleanup(self.dispose, app)
        if prepare_database:
            migrate_database(app)
            seed_categories(app)
        return app

    @staticmethod
    def dispose(app):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


class ConfigurationTests(IsolatedEnvironment):
    def test_missing_environment_fails_before_database_initialization(self):
        os.environ.pop("APP_ENV")
        with patch("app.db.init_app") as init_db, self.assertRaises(ConfigError):
            create_app()
        init_db.assert_not_called()

    def test_unknown_environment_is_rejected(self):
        os.environ["APP_ENV"] = "typo"
        with self.assertRaises(ConfigError):
            configure_app(Flask(__name__))

    def test_missing_and_blank_values_are_reported_without_secrets(self):
        os.environ.pop("APP_USERNAME")
        os.environ["APP_PASSWORD"] = "  "
        with patch("app.db.init_app") as init_db, self.assertRaises(ConfigError) as error:
            create_app()
        message = str(error.exception)
        self.assertIn("APP_USERNAME", message)
        self.assertIn("APP_PASSWORD", message)
        self.assertNotIn(self.env["FLASK_SECRET_KEY"], message)
        init_db.assert_not_called()

    def test_required_values_have_no_defaults(self):
        for key in ("FLASK_SECRET_KEY", "DATABASE_URL", "GMAIL_REDIRECT_URI", "CREDENTIALS_FILE"):
            with self.subTest(key=key), patch.dict(os.environ):
                os.environ.pop(key)
                with self.assertRaises(ConfigError):
                    configure_app(Flask(__name__))

    def test_development_loads_dotenv_without_overriding_environment(self):
        app = Flask(__name__)
        configure_app(app)
        self.load_dotenv.assert_called_once()
        self.assertFalse(self.load_dotenv.call_args.kwargs["override"])
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(os.environ["OAUTHLIB_INSECURE_TRANSPORT"], "1")

    def test_production_uses_render_environment_and_secure_cookies(self):
        self.production()
        # A bad local file must be irrelevant in production.
        os.environ["CREDENTIALS_FILE"] = str(self.root / "missing.json")
        app = Flask(__name__)
        configure_app(app)
        self.load_dotenv.assert_not_called()
        self.assertFalse(app.debug)
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql://"))

    def test_production_does_not_fall_back_to_local_credentials(self):
        self.production()
        os.environ.pop("GOOGLE_CREDENTIALS_JSON")
        with self.assertRaises(ConfigError):
            configure_app(Flask(__name__))

    def test_production_rejects_insecure_settings(self):
        self.production()
        cases = {
            "FLASK_DEBUG": "1", "OAUTHLIB_INSECURE_TRANSPORT": "0",
            "GMAIL_REDIRECT_URI": "http://test.example/gmail/oauth/callback",
            "DATABASE_URL": "sqlite:///household.db",
        }
        for key, value in cases.items():
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ConfigError):
                    configure_app(Flask(__name__))

    def test_production_rejects_debug_values_that_flask_treats_as_true(self):
        self.production()
        for value in ("1", "true", " 0 ", " "):
            with self.subTest(value=value), patch.dict(os.environ, {"FLASK_DEBUG": value}):
                with self.assertRaises(ConfigError):
                    configure_app(Flask(__name__))

    def test_development_rejects_external_http_callback(self):
        os.environ["GMAIL_REDIRECT_URI"] = "http://external.example/gmail/oauth/callback"
        with self.assertRaises(ConfigError):
            configure_app(Flask(__name__))

    def test_malformed_settings_are_sanitized(self):
        cases = {
            "FLASK_SECRET_KEY": "dev-secret-key-change-in-production",
            "DATABASE_URL": "not-a-db-url-with-secret",
            "GMAIL_REDIRECT_URI": "http://localhost:bad/gmail/oauth/callback",
            "CREDENTIALS_FILE": str(self.root / "missing-secret.json"),
        }
        for key, value in cases.items():
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ConfigError) as error:
                    configure_app(Flask(__name__))
                self.assertNotIn(value, str(error.exception))
        self.production()
        for value in ("{broken-secret", "[]", "{}", '{"web": {"client_secret": "secret"}}'):
            with self.subTest(value=value), patch.dict(os.environ, {"GOOGLE_CREDENTIALS_JSON": value}):
                with self.assertRaises(ConfigError) as error:
                    configure_app(Flask(__name__))
                self.assertNotIn(value, str(error.exception))


class ApplicationTests(IsolatedEnvironment):
    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.client = self.app.test_client()
        self.headers = {"Authorization": "Basic " + base64.b64encode(b"test-user:test-password").decode()}

    def store_token(self):
        db.session.add(Setting(key="gmail_token", value=TOKEN))
        db.session.commit()

    def test_create_app_does_not_create_or_migrate_tables(self):
        separate_database = self.root / "startup-only.db"
        app = self.make_app(
            prepare_database=False,
            SQLALCHEMY_DATABASE_URI="sqlite:///" + str(separate_database),
        )
        with app.app_context():
            self.assertEqual(inspect(db.engine).get_table_names(), [])

    def test_seed_categories_is_idempotent(self):
        result = self.app.test_cli_runner().invoke(args=["seed-categories"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("0件", result.output)
        with self.app.app_context():
            self.assertEqual(Category.query.count(), 12)

    def test_migration_schema_matches_models(self):
        check_database_schema(self.app)

    def test_initial_migration_preserves_legacy_database_data(self):
        legacy_database = self.root / "legacy.db"
        app = self.make_app(
            prepare_database=False,
            SQLALCHEMY_DATABASE_URI="sqlite:///" + str(legacy_database),
        )
        with app.app_context(), db.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE categories ("
                "id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, "
                "type VARCHAR(10) NOT NULL, color VARCHAR(7), "
                "is_default BOOLEAN, created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE transactions ("
                "id INTEGER PRIMARY KEY, date DATE NOT NULL, amount INTEGER NOT NULL, "
                "type VARCHAR(10) NOT NULL, description VARCHAR(255) NOT NULL, "
                "memo TEXT, category_id INTEGER, source VARCHAR(20), "
                "external_id VARCHAR(255) UNIQUE, created_at DATETIME, "
                "FOREIGN KEY(category_id) REFERENCES categories(id))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE settings (key VARCHAR(100) PRIMARY KEY, value TEXT)"
            )
            connection.exec_driver_sql(
                "INSERT INTO categories (id, name, type) VALUES (1, '既存カテゴリ', 'expense')"
            )
            connection.exec_driver_sql(
                "INSERT INTO transactions "
                "(id, date, amount, type, description, category_id) "
                "VALUES (1, '2026-10-12', 500, 'expense', '既存取引', 1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO settings (key, value) VALUES ('gmail_token', 'existing-token')"
            )

        migrate_database(app)

        with app.app_context():
            columns = {column["name"] for column in inspect(db.engine).get_columns("transactions")}
            self.assertIn("source_detail", columns)
            self.assertEqual(db.session.get(Transaction, 1).description, "既存取引")
            self.assertEqual(db.session.get(Setting, "gmail_token").value, "existing-token")

    def test_basic_auth_is_required_and_ping_remains_public(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.get("/", headers={"Authorization": "Basic YmFkOmJhZA=="}).status_code, 401)
        self.assertEqual(self.client.get("/", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/ping").status_code, 200)

    def test_category_edit_data_is_not_embedded_in_inline_javascript(self):
        with self.app.app_context():
            db.session.add(Category(name="O'Reilly", type="expense", color="#4e79a7"))
            db.session.commit()

        response = self.client.get("/categories/", headers=self.headers)
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-category-name="O&#39;Reilly"', html)
        self.assertNotIn('onclick="openEditModal', html)
        self.assertIn("button.dataset.categoryName", html)

    def test_gmail_scan_result_uses_validated_text_content(self):
        response = self.client.get("/gmail/", headers=self.headers)
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Number.isSafeInteger(count)", html)
        self.assertIn("message.textContent", html)
        self.assertNotIn("div.innerHTML", html)

    def test_existing_transactions_settings_and_token_survive_restart(self):
        with self.app.app_context():
            category = Category.query.first()
            tx = Transaction(date=date(2026, 1, 1), amount=1234, type="expense",
                             description="preserve me", memo="original memo", category_id=category.id)
            db.session.add(tx)
            db.session.add(Setting(key="scan_interval_minutes", value="60"))
            self.store_token()
            tx_id = tx.id
            original = tx.to_dict()
            category_count = Category.query.count()
        restarted = self.make_app()
        with restarted.app_context():
            self.assertEqual(db.session.get(Transaction, tx_id).to_dict(), original)
            self.assertEqual(db.session.get(Transaction, tx_id).memo, "original memo")
            self.assertEqual(Category.query.count(), category_count)
            self.assertEqual(db.session.get(Setting, "scan_interval_minutes").value, "60")
            self.assertEqual(gmail_service._load_token_json(), TOKEN)

    def test_missing_token_is_normal(self):
        with self.app.app_context():
            self.assertIsNone(gmail_service.get_credentials())

    def test_database_failure_does_not_read_old_token_file(self):
        old_file = self.root / "token.json"
        old_file.write_text(TOKEN)
        with patch.dict(os.environ, {"TOKEN_FILE_PATH": str(old_file)}), self.app.app_context():
            with patch.object(db.session, "get", side_effect=SQLAlchemyError("DB unavailable")):
                with self.assertRaises(SQLAlchemyError):
                    gmail_service._load_token_json()
        self.assertEqual(old_file.read_text(), TOKEN)

    def test_failed_token_save_rolls_back_without_touching_file(self):
        old_file = self.root / "token.json"
        old_file.write_text(TOKEN)
        with self.app.app_context():
            self.store_token()
            creds = MagicMock()
            creds.to_json.return_value = '{"token":"new"}'
            with patch.dict(os.environ, {"TOKEN_FILE_PATH": str(old_file)}):
                with patch.object(db.session, "commit", side_effect=SQLAlchemyError("DB unavailable")):
                    with self.assertRaises(SQLAlchemyError):
                        gmail_service._save_token(creds)
            self.assertEqual(gmail_service._load_token_json(), TOKEN)
        self.assertEqual(old_file.read_text(), TOKEN)

    def test_refresh_failure_preserves_token_unless_revoked(self):
        errors = [
            TransportError("network unavailable"),
            RefreshError("server_error", {"error": "server_error"}, retryable=True),
            RefreshError("invalid_client", {"error": "invalid_client"}),
        ]
        with self.app.app_context():
            self.store_token()
            for error in errors:
                creds = MagicMock(expired=True, refresh_token="test-refresh", valid=True)
                creds.refresh.side_effect = error
                with self.subTest(error=type(error).__name__), patch.object(
                    gmail_service.Credentials, "from_authorized_user_info", return_value=creds
                ):
                    with self.assertRaises(type(error)):
                        gmail_service.get_credentials()
                    self.assertEqual(gmail_service._load_token_json(), TOKEN)
            creds.refresh.side_effect = RefreshError("revoked", {"error": "invalid_grant"})
            with patch.object(gmail_service.Credentials, "from_authorized_user_info", return_value=creds):
                self.assertIsNone(gmail_service.get_credentials())
            self.assertIsNone(gmail_service._load_token_json())

    def test_refresh_save_failure_does_not_delete_old_token(self):
        with self.app.app_context():
            self.store_token()
            creds = MagicMock(expired=True, refresh_token="test-refresh", valid=True)
            with patch.object(gmail_service.Credentials, "from_authorized_user_info", return_value=creds):
                with patch.object(gmail_service, "_save_token", side_effect=SQLAlchemyError("save failed")):
                    with self.assertRaises(SQLAlchemyError):
                        gmail_service.get_credentials()
            self.assertEqual(gmail_service._load_token_json(), TOKEN)

    def test_legacy_token_import_preserves_file_and_never_overwrites_db(self):
        old_file = self.root / "token.json"
        old_file.write_text(TOKEN)
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["import-gmail-token", "--file", str(old_file)])
        self.assertEqual(result.exit_code, 0, result.output)
        old_file.write_text(TOKEN.replace("test-access-token", "different-access-token"))
        result = runner.invoke(args=["import-gmail-token", "--file", str(old_file)])
        self.assertNotEqual(result.exit_code, 0)
        with self.app.app_context():
            self.assertEqual(gmail_service._load_token_json(), TOKEN)
        self.assertIn("different-access-token", old_file.read_text())
        self.assertNotIn("test-refresh-token", result.output)

    def test_malformed_legacy_token_is_not_imported(self):
        old_file = self.root / "token.json"
        old_file.write_text('{"secret": "malformed-test-secret"}')
        result = self.app.test_cli_runner().invoke(args=["import-gmail-token", "--file", str(old_file)])
        self.assertNotEqual(result.exit_code, 0)
        self.assertNotIn("malformed-test-secret", result.output)
        with self.app.app_context():
            self.assertIsNone(gmail_service._load_token_json())

    def start_oauth(self, flow):
        flow.authorization_url.return_value = ("https://accounts.google.com/oauth", "test-state")
        with patch.object(gmail_service, "get_flow", return_value=flow):
            response = self.client.get("/gmail/auth", headers=self.headers)
        self.assertEqual(response.status_code, 302)
        return response

    def test_oauth_invalid_state_never_exchanges_or_overwrites_token(self):
        with self.app.app_context():
            self.store_token()
        flow = MagicMock()
        for query in ("code=test", "state=wrong&code=test", "state=test-state&state=wrong&code=test"):
            self.start_oauth(flow)
            with patch.object(gmail_service, "get_flow", return_value=flow) as get_flow:
                response = self.client.get("/gmail/oauth/callback?" + query, headers=self.headers)
            self.assertEqual(response.status_code, 400)
            get_flow.assert_not_called()
        flow.fetch_token.assert_not_called()
        with self.app.app_context():
            self.assertEqual(gmail_service._load_token_json(), TOKEN)

    def test_real_google_flow_uses_saved_pkce_and_state_without_network(self):
        from urllib.parse import parse_qs, urlsplit

        response = self.client.get("/gmail/auth", headers=self.headers)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.location).query)
        with self.client.session_transaction() as session:
            state = session["gmail_oauth_state"]
            verifier = session["pkce_verifier"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["state"], [state])
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        with self.app.app_context():
            flow = gmail_service.get_flow(self.env["GMAIL_REDIRECT_URI"], state=state)
            self.assertEqual(flow.oauth2session._state, state)

    def test_oauth_valid_state_is_consumed_and_uses_configured_callback_url(self):
        flow = MagicMock()
        flow.credentials.to_json.return_value = TOKEN
        self.start_oauth(flow)
        with patch.object(gmail_service, "get_flow", return_value=flow) as get_flow:
            response = self.client.get("/gmail/oauth/callback?state=test-state&code=test", headers=self.headers)
        self.assertEqual(response.status_code, 302)
        get_flow.assert_called_once_with(self.env["GMAIL_REDIRECT_URI"], state="test-state")
        kwargs = flow.fetch_token.call_args.kwargs
        self.assertEqual(kwargs["authorization_response"], self.env["GMAIL_REDIRECT_URI"] + "?state=test-state&code=test")
        self.assertTrue(kwargs["code_verifier"])
        replay = self.client.get("/gmail/oauth/callback?state=test-state&code=test", headers=self.headers)
        self.assertEqual(replay.status_code, 400)
        with self.app.app_context():
            self.assertEqual(gmail_service._load_token_json(), TOKEN)

    def test_production_callback_uses_https_behind_proxy_and_secure_cookie(self):
        self.production()
        prod_app = self.make_app(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        client = prod_app.test_client()
        flow = MagicMock()
        flow.authorization_url.return_value = ("https://accounts.google.com/oauth", "test-state")
        flow.credentials.to_json.return_value = TOKEN
        with patch.object(gmail_service, "get_flow", return_value=flow):
            response = client.get("/gmail/auth", base_url="https://test.example", headers=self.headers)
            self.assertIn("Secure", response.headers["Set-Cookie"])
            # HTTPS is terminated at the proxy, so the Flask request URL may be HTTP.
            response = client.get("/gmail/oauth/callback?state=test-state&code=test",
                                  base_url="http://test.example", headers=self.headers)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(flow.fetch_token.call_args.kwargs["authorization_response"].startswith("https://test.example/"))


class SchedulerTests(IsolatedEnvironment):
    def test_scheduled_scan_skips_when_another_scan_is_running(self):
        self.make_app()
        with patch("app.scheduler") as scheduler:
            scheduler.running = False
            self.make_app(SCHEDULER_ENABLED=True)
        scheduled_scan = scheduler.add_job.call_args.args[0]
        with patch.dict(gmail_service._scan_progress, {"running": True, "added": 5}), \
                patch.object(gmail_service, "is_authenticated", return_value=True), \
                patch.object(gmail_service, "_do_scan") as scan:
            progress = gmail_service.get_scan_progress()
            scheduled_scan()
            scan.assert_not_called()
            self.assertEqual(gmail_service.get_scan_progress(), progress)

    def test_flask_cli_maintenance_does_not_start_scheduler(self):
        group = FlaskGroup(create_app=create_app, load_dotenv=False)
        with patch("app.scheduler") as scheduler:
            scheduler.running = False
            result = CliRunner().invoke(group, ["routes"])
        self.assertEqual(result.exit_code, 0, result.output)
        scheduler.start.assert_not_called()

    def test_debug_reloader_parent_does_not_start_scheduler(self):
        os.environ["FLASK_DEBUG"] = "1"
        with patch("app.scheduler") as scheduler:
            scheduler.running = False
            self.make_app(SCHEDULER_ENABLED=True)
        scheduler.start.assert_not_called()

    def test_debug_reloader_child_starts_scheduler_once(self):
        os.environ.update({"FLASK_DEBUG": "1", "WERKZEUG_RUN_MAIN": "true"})
        self.make_app()
        with patch("app.scheduler") as scheduler:
            scheduler.running = False
            self.make_app(SCHEDULER_ENABLED=True)
        scheduler.start.assert_called_once()
        scheduler.add_job.assert_called_once()


if __name__ == "__main__":
    unittest.main()
