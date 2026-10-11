import os

# 直接実行は開発専用。Gunicornからのimportではデバッグを有効にしない。
if __name__ == "__main__":
    if os.environ.get("APP_ENV") != "development":
        raise RuntimeError("直接実行は APP_ENV=development のみ対応します。本番はGunicornを使用してください。")
    os.environ.setdefault("FLASK_DEBUG", "1")

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(port=8080, load_dotenv=False)
