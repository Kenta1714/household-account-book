import hmac
import click
from flask import Flask, request, Response
from app.config import configure_app
from app.extensions import db, scheduler
from app.models import Category
from werkzeug.serving import is_running_from_reloader


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
    """起動時に検証済みの認証情報を両環境で必須にする。"""
    username = app.config["APP_USERNAME"]
    password = app.config["APP_PASSWORD"]

    @app.before_request
    def require_auth():
        if request.path in ("/ping", "/favicon.ico") or \
                request.path.startswith("/static/apple-touch-icon") or \
                request.path.startswith("/static/icon-"):
            return  # スリープ対策・アイコン類は認証不要
        auth = request.authorization
        valid_username = hmac.compare_digest((auth.username or "").encode(), username.encode()) if auth else False
        valid_password = hmac.compare_digest((auth.password or "").encode(), password.encode()) if auth else False
        if not valid_username or not valid_password:
            return Response(
                "このアプリへのアクセスには認証が必要です",
                401,
                {"WWW-Authenticate": 'Basic realm="kakeibo"'},
            )


def create_app(test_config=None):
    app = Flask(__name__)
    configure_app(app)
    if test_config is not None:
        app.config.update(test_config)
    _apply_basic_auth(app)

    from app.commands import register_commands
    register_commands(app)

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
    # shell・トークン移行などの保守コマンドではバックグラウンド取込を起動しない。
    cli_context = click.get_current_context(silent=True)
    serving = cli_context is None or cli_context.find_root().invoked_subcommand == "run"
    if (
        app.config["SCHEDULER_ENABLED"]
        and serving
        and not scheduler.running
        and (not app.debug or is_running_from_reloader())
    ):
        from app.services import gmail_service as gs
        from app.services.gmail_service import scan_emails

        def scheduled_gmail_scan():
            with app.app_context():
                if gs.is_authenticated():
                    scan_emails(batch=False)

        with app.app_context():
            from app.models import Setting
            s = db.session.get(Setting, "scan_interval_minutes")
            interval_minutes = int(s.value) if s else 30

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
