import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask.helpers import get_debug_flag
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """起動前に検出した設定エラー。メッセージに秘密情報を含めない。"""


class Config:
    DEBUG = False
    TESTING = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SCHEDULER_ENABLED = True
    BASIC_AUTH_MAX_FAILURES = 5
    BASIC_AUTH_WINDOW_SECONDS = 300
    BASIC_AUTH_LOCKOUT_SECONDS = 300


class DevelopmentConfig(Config):
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


def configure_app(app):
    """環境選択 → 設定取得 → 検証を、DB初期化より前に行う。"""
    mode = os.environ.get("APP_ENV", "").strip()
    config_classes = {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
    }
    if mode not in config_classes:
        raise ConfigError("APP_ENV に development または production を設定してください。")

    # 開発環境だけ明示的なプロジェクトの .env を読む。OS環境変数を優先する。
    if mode == "development":
        load_dotenv(PROJECT_ROOT / ".env", override=False)

    app.config.from_object(config_classes[mode])
    app.config["APP_ENV"] = mode
    required = {
        "SECRET_KEY": "FLASK_SECRET_KEY",
        "APP_USERNAME": "APP_USERNAME",
        "APP_PASSWORD": "APP_PASSWORD",
        "SQLALCHEMY_DATABASE_URI": "DATABASE_URL",
        "GMAIL_REDIRECT_URI": "GMAIL_REDIRECT_URI",
    }
    source = "CREDENTIALS_FILE" if mode == "development" else "GOOGLE_CREDENTIALS_JSON"
    missing = []
    for config_key, env_key in required.items():
        value = os.environ.get(env_key, "")
        if not value.strip():
            missing.append(env_key)
        app.config[config_key] = value
    if not os.environ.get(source, "").strip():
        missing.append(source)
    if missing:
        raise ConfigError(f"設定エラー（{mode}）: {', '.join(missing)} が未設定または空です。")

    errors = []
    if len(app.config["APP_PASSWORD"]) < 15:
        errors.append("APP_PASSWORD は15文字以上のランダムで使い回さない値にしてください")
    for key in ("BASIC_AUTH_MAX_FAILURES", "BASIC_AUTH_WINDOW_SECONDS", "BASIC_AUTH_LOCKOUT_SECONDS"):
        try:
            value = int(os.environ.get(key, app.config[key]))
            if value <= 0:
                raise ValueError
            app.config[key] = value
        except ValueError:
            errors.append(f"{key} は1以上の整数を指定してください")
    secret = app.config["SECRET_KEY"]
    if len(secret) < 32 or secret in {
        "change-me-to-random-string", "dev-secret-key", "dev-secret-key-change-in-production"
    }:
        errors.append("FLASK_SECRET_KEY は32文字以上のランダムな値にしてください")

    db_url = app.config["SQLALCHEMY_DATABASE_URI"].strip()
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    try:
        parsed_db = make_url(db_url)
        if mode == "production" and parsed_db.get_backend_name() != "postgresql":
            errors.append("本番の DATABASE_URL はPostgreSQLを指定してください")
    except (ArgumentError, ValueError):
        errors.append("DATABASE_URL の形式が不正です")

    redirect_uri = app.config["GMAIL_REDIRECT_URI"].strip()
    app.config["GMAIL_REDIRECT_URI"] = redirect_uri
    try:
        redirect = urlsplit(redirect_uri)
        valid = (
            redirect.scheme in {"http", "https"} and redirect.hostname
            and redirect.path == "/gmail/oauth/callback"
            and not redirect.query and not redirect.fragment
            and not redirect.username and not redirect.password
        )
        redirect.port  # 不正なポート指定も検出する
        if not valid:
            errors.append("GMAIL_REDIRECT_URI は /gmail/oauth/callback のHTTP(S) URLを指定してください")
        elif redirect.scheme == "http" and (
            mode == "production" or redirect.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            errors.append("GMAIL_REDIRECT_URI のHTTPは開発環境のlocalhostでのみ使用できます")
    except ValueError:
        errors.append("GMAIL_REDIRECT_URI の形式が不正です")

    # Flask CLIの --debug もFLASK_DEBUGに反映される。開発はCLIに任せる。
    if mode == "production" and get_debug_flag():
        errors.append("本番では FLASK_DEBUG を有効にできません")
    insecure = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT", "")
    # oauthlibは値が非空ならHTTPを許可する（'0'も許可になる）。
    if mode == "production" and insecure:
        errors.append("本番では OAUTHLIB_INSECURE_TRANSPORT を設定しないでください")

    try:
        if mode == "development":
            path = Path(os.environ[source]).expanduser()
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            client_config = json.loads(path.read_text(encoding="utf-8"))
            app.config["CREDENTIALS_FILE"] = str(path)
        else:
            client_config = json.loads(os.environ[source])
        client = client_config.get("web") or client_config.get("installed")
        keys = ("client_id", "client_secret", "auth_uri", "token_uri")
        if not isinstance(client, dict) or not all(
            isinstance(client.get(key), str) and client[key].strip() for key in keys
        ):
            raise ValueError
        if any(urlsplit(client[key]).scheme != "https" for key in ("auth_uri", "token_uri")):
            raise ValueError
        app.config["GOOGLE_CLIENT_CONFIG"] = client_config
    except (OSError, ValueError, TypeError, AttributeError):
        errors.append(f"{source} のOAuth認証情報を読み込めません（ファイル・JSON形式・必須項目を確認）")

    if errors:
        raise ConfigError(f"設定エラー（{mode}）: " + " / ".join(errors))

    if mode == "development":
        app.config["DEBUG"] = get_debug_flag()
    if mode == "development" and redirect.scheme == "http":
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
