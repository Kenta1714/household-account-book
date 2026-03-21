from flask import Flask
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


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    from app.routes import dashboard, transactions, categories, gmail, paypay
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(transactions.bp)
    app.register_blueprint(categories.bp)
    app.register_blueprint(gmail.bp)
    app.register_blueprint(paypay.bp)

    with app.app_context():
        db.create_all()
        _migrate_db()
        _seed_categories()

    # Gmail定期スキャン（30分ごと）
    # Flaskのdebugリローダーが起動する子プロセスではスケジューラを起動しない
    import os
    if not scheduler.running and os.environ.get("WERKZEUG_RUN_MAIN") != "false":
        from app.services import gmail_service as gs
        from app.services.gmail_service import scan_emails

        def scheduled_gmail_scan():
            with app.app_context():
                if gs.is_authenticated():
                    scan_emails(batch=False)

        scheduler.add_job(scheduled_gmail_scan, "interval", minutes=30, id="gmail_scan")
        scheduler.start()

    return app


def _migrate_db():
    """既存DBに新カラムを追加する（SQLite用の簡易マイグレーション）"""
    migrations = [
        "ALTER TABLE transactions ADD COLUMN source_detail VARCHAR(200)",
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()


def _seed_categories():
    if Category.query.count() == 0:
        for name, type_, color in DEFAULT_CATEGORIES:
            db.session.add(Category(name=name, type=type_, color=color, is_default=True))
        db.session.commit()
