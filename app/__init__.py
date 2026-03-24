import os
from flask import Flask, request, Response
from app.config import Config
from app.extensions import db, scheduler
from app.models import Category


DEFAULT_CATEGORIES = [
    ("食費", "expense", "#e15759"),
    ("日用品", "expense", "#f28e2b"),
    ("交通費", "expense", "#4e79a7"),
    ("光熱費", "expense", "#76b7b2"),
    ("通信費", "expense", "#b07aa1"),
    ("娯楽", "expense", "#ff9da7"),
    ("医療", "expense", "#59a14f"),
    ("衣服", "expense", "#edc948"),
    ("その他支出", "expense", "#adb5bd"),
    ("給与", "income", "#59a14f"),
    ("副収入", "income", "#4e79a7"),
    ("その他収入", "income", "#76b7b2"),
]


def _apply_basic_auth(app):
    """APP_USERNAME / APP_PASSWORD が設定されている場合のみ Basic 認証を有効化"""
    username = os.environ.get("APP_USERNAME", "")
    password = os.environ.get("APP_PASSWORD", "")
    if not username or not password:
        return  # ローカル開発時はスキップ

    @app.before_request
    def require_auth():
        if request.path in ("/ping", "/favicon.ico") or \
                request.path.startswith("/static/apple-touch-icon") or \
                request.path.startswith("/static/icon-"):
            return  # スリープ対策・アイコン類は認証不要
        auth = request.authorization
        if not auth or auth.username != username or auth.password != password:
            return Response(
                "このアプリへのアクセスには認証が必要です",
                401,
                {"WWW-Authenticate": 'Basic realm="kakeibo"'},
            )


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    _apply_basic_auth(app)

    db.init_app(app)

    from app.routes import dashboard, transactions, categories, gmail, paypay
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(transactions.bp)
    app.register_blueprint(categories.bp)
    app.register_blueprint(gmail.bp)
    app.register_blueprint(paypay.bp)

    @app.route("/ping")
    def ping():
        return "ok", 200

    @app.route("/favicon.ico")
    def favicon():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "apple-touch-icon.png",
                                   mimetype="image/png")

    with app.app_context():
        db.create_all()
        _migrate_db()
        _seed_categories()

    # Gmail定期スキャン（間隔はDBのscan_interval_minutes設定値、デフォルト30分）
    # Flaskのdebugリローダーが起動する子プロセスではスケジューラを起動しない
    if not scheduler.running and os.environ.get("WERKZEUG_RUN_MAIN") != "false":
        from app.services import gmail_service as gs
        from app.services.gmail_service import scan_emails

        def scheduled_gmail_scan():
            with app.app_context():
                if gs.is_authenticated():
                    scan_emails(batch=False)

        try:
            with app.app_context():
                from app.models import Setting
                s = Setting.query.get("scan_interval_minutes")
                interval_minutes = int(s.value) if s else 30
        except Exception:
            interval_minutes = 30

        scheduler.add_job(scheduled_gmail_scan, "interval", minutes=interval_minutes, id="gmail_scan")
        scheduler.start()

    return app


def _migrate_db():
    """既存DBに新カラムを追加する簡易マイグレーション（PostgreSQL / SQLite 両対応）"""
    # PostgreSQL では "IF NOT EXISTS" が使える（9.6+）
    # SQLite は IF NOT EXISTS に非対応なので try/except でエラーを無視する
    is_postgres = db.engine.url.get_backend_name() == "postgresql"
    if is_postgres:
        migrations = [
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS source_detail VARCHAR(200)",
            "ALTER TABLE email_formats ADD COLUMN IF NOT EXISTS card_name_mode VARCHAR(20)",
            "ALTER TABLE email_formats ADD COLUMN IF NOT EXISTS card_name_prefix VARCHAR(255)",
            "ALTER TABLE email_formats ADD COLUMN IF NOT EXISTS card_name_suffix VARCHAR(255)",
            "ALTER TABLE email_formats ADD COLUMN IF NOT EXISTS card_name_keywords TEXT",
            "ALTER TABLE scan_logs ADD COLUMN IF NOT EXISTS trigger VARCHAR(10)",
        ]
    else:
        migrations = [
            "ALTER TABLE transactions ADD COLUMN source_detail VARCHAR(200)",
            "ALTER TABLE email_formats ADD COLUMN card_name_mode VARCHAR(20)",
            "ALTER TABLE email_formats ADD COLUMN card_name_prefix VARCHAR(255)",
            "ALTER TABLE email_formats ADD COLUMN card_name_suffix VARCHAR(255)",
            "ALTER TABLE email_formats ADD COLUMN card_name_keywords TEXT",
            "ALTER TABLE scan_logs ADD COLUMN trigger VARCHAR(10)",
        ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()
    db.session.remove()  # マイグレーション後のセッションを確実にクリーンにする


def _seed_categories():
    if Category.query.count() == 0:
        for name, type_, color in DEFAULT_CATEGORIES:
            db.session.add(Category(name=name, type=type_, color=color, is_default=True))
        db.session.commit()
