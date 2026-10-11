import json
from pathlib import Path

import click
from google.oauth2.credentials import Credentials

from app.extensions import db
from app.models import Setting


def register_commands(app):
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
