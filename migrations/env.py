from logging.config import fileConfig

from alembic import context

from app import create_app
from app.extensions import db
import app.models  # noqa: F401 - 全モデルをmetadataへ登録する


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.metadata


def _configure_context(**kwargs):
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline():
    app = create_app({"SCHEDULER_ENABLED": False})
    _configure_context(
        url=app.config["SQLALCHEMY_DATABASE_URI"],
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection):
    _configure_context(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_with_connection(supplied_connection)
        return

    app = create_app({"SCHEDULER_ENABLED": False})
    with app.app_context():
        with db.engine.connect() as connection:
            _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
