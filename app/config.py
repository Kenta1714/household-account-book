import os
from dotenv import load_dotenv

load_dotenv()

# ローカル開発時のみ HTTP で OAuth2 を許可
# 本番環境では絶対に設定しないこと（HTTPS が必須）
if os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///household.db")
    # Render の PostgreSQL URL は "postgres://" で始まるが SQLAlchemy は "postgresql://" が必要
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or "dev-secret-key-change-in-production"
    SQLALCHEMY_DATABASE_URI = _get_db_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,   # 接続使用前に状態チェック→不正なトランザクションを自動リセット
    }
    # 本番環境では SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True が望ましい
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
