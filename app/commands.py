import json
from pathlib import Path

import click
from google.oauth2.credentials import Credentials

from app.extensions import db
from app.models import Category, Setting


DEFAULT_CATEGORIES = (
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
)


def register_commands(app):
    @app.cli.command("seed-categories")
    def seed_categories():
        """不足している初期カテゴリを登録する。既存カテゴリは変更しない。"""
        existing = {
            (category.name, category.type)
            for category in db.session.execute(db.select(Category)).scalars()
        }
        added = 0
        for name, type_, color in DEFAULT_CATEGORIES:
            if (name, type_) in existing:
                continue
            db.session.add(Category(
                name=name, type=type_, color=color, is_default=True,
            ))
            added += 1
        db.session.commit()
        click.echo(f"初期カテゴリを{added}件登録しました。")

    @app.cli.command("import-gmail-token")
    @click.option("--file", "token_file", required=True,
                  type=click.Path(exists=True, dir_okay=False, path_type=Path))
    def import_gmail_token(token_file):
        """旧トークンファイルをDBへ取り込む。既存DBトークンや元ファイルは変更しない。"""
        from app.services.gmail_service import SCOPES, _GMAIL_TOKEN_KEY

        if db.session.get(Setting, _GMAIL_TOKEN_KEY) is not None:
            raise click.ClickException("DBにGmailトークンが既に存在します。上書きせず終了しました。")
        try:
            token_json = token_file.read_text(encoding="utf-8")
            Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        except (OSError, ValueError, TypeError, AttributeError):
            raise click.ClickException("トークンファイルの形式を確認してください。") from None
        try:
            db.session.add(Setting(key=_GMAIL_TOKEN_KEY, value=token_json))
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise click.ClickException("DBへの取り込みに失敗しました。元ファイルは変更していません。") from None
        click.echo("GmailトークンをDBへ取り込みました。元ファイルは変更していません。")
