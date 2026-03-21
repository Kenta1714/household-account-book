import os
from dotenv import load_dotenv

load_dotenv()

# ローカル開発時のみHTTPでOAuth2を許可（本番では設定しない）
if os.environ.get("OAUTHLIB_INSECURE_TRANSPORT"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///household.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
