"""Create the initial schema and adopt existing databases.

Revision ID: 20261012_0001
Revises:
Create Date: 2026-10-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20261012_0001"
down_revision = None
branch_labels = None
depends_on = None


def _table_names():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name):
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _add_column_if_missing(table_name, column):
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def upgrade():
    tables = _table_names()

    if "categories" not in tables:
        op.create_table(
            "categories",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("type", sa.String(length=10), nullable=False),
            sa.Column("color", sa.String(length=7), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "scan_logs" not in tables:
        op.create_table(
            "scan_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False),
            sa.Column("trigger", sa.String(length=10), nullable=True),
            sa.Column("scanned_at", sa.DateTime(), nullable=True),
            sa.Column("total_found", sa.Integer(), nullable=True),
            sa.Column("added", sa.Integer(), nullable=True),
            sa.Column("skipped", sa.Integer(), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        _add_column_if_missing(
            "scan_logs", sa.Column("trigger", sa.String(length=10), nullable=True),
        )

    if "settings" not in tables:
        op.create_table(
            "settings",
            sa.Column("key", sa.String(length=100), nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("key"),
        )

    if "transactions" not in tables:
        op.create_table(
            "transactions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(length=10), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=False),
            sa.Column("memo", sa.Text(), nullable=True),
            sa.Column("category_id", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(length=20), nullable=True),
            sa.Column("source_detail", sa.String(length=200), nullable=True),
            sa.Column("external_id", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["category_id"], ["categories.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("external_id"),
        )
    else:
        _add_column_if_missing(
            "transactions",
            sa.Column("source_detail", sa.String(length=200), nullable=True),
        )

    if "email_formats" not in tables:
        op.create_table(
            "email_formats",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("company_name", sa.String(length=100), nullable=False),
            sa.Column("sender_email", sa.String(length=255), nullable=False),
            sa.Column("amount_prefix", sa.String(length=255), nullable=False),
            sa.Column("amount_suffix", sa.String(length=255), nullable=False),
            sa.Column("content_prefix", sa.String(length=255), nullable=True),
            sa.Column("content_suffix", sa.String(length=255), nullable=True),
            sa.Column("card_name_mode", sa.String(length=20), nullable=True),
            sa.Column("card_name_prefix", sa.String(length=255), nullable=True),
            sa.Column("card_name_suffix", sa.String(length=255), nullable=True),
            sa.Column("card_name_keywords", sa.Text(), nullable=True),
            sa.Column("default_category_id", sa.Integer(), nullable=True),
            sa.Column("sample_text", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["default_category_id"], ["categories.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        for column in (
            sa.Column("card_name_mode", sa.String(length=20), nullable=True),
            sa.Column("card_name_prefix", sa.String(length=255), nullable=True),
            sa.Column("card_name_suffix", sa.String(length=255), nullable=True),
            sa.Column("card_name_keywords", sa.Text(), nullable=True),
        ):
            _add_column_if_missing("email_formats", column)

    if "scan_skip_details" not in tables:
        op.create_table(
            "scan_skip_details",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("scan_log_id", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=20), nullable=False),
            sa.Column("sender", sa.String(length=300), nullable=True),
            sa.Column("subject", sa.String(length=500), nullable=True),
            sa.Column("date_str", sa.String(length=100), nullable=True),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("extra_info", sa.String(length=300), nullable=True),
            sa.ForeignKeyConstraint(["scan_log_id"], ["scan_logs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    raise RuntimeError(
        "初回マイグレーションは既存DBを引き継ぐため、downgradeできません。"
    )
