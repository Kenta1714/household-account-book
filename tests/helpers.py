from pathlib import Path

from alembic import command
from alembic.config import Config

from app.extensions import db
from app.logging_utils import configure_logging


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def migrate_database(app):
    """テストDBを本番と同じAlembicマイグレーションで作成する。"""
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with app.app_context():
        with db.engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.upgrade(alembic_config, "head")
    # テストではアプリ生成後にAlembicがhandlerを置換し既存loggerを無効化する。
    # 本番と同様、マイグレーション後のアプリ用ログ設定に戻す。
    app.logger.disabled = False
    configure_logging(app)


def check_database_schema(app):
    """モデルとマイグレーション後のDBに差分がないことを検証する。"""
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with app.app_context():
        with db.engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.check(alembic_config)


def seed_categories(app):
    """初期カテゴリCLIを実行し、失敗したテストを明確に停止する。"""
    result = app.test_cli_runner().invoke(args=["seed-categories"])
    if result.exit_code != 0:
        raise AssertionError(result.output) from result.exception
