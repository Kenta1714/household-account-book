from app.extensions import db


class Setting(db.Model):
    """アプリ全体のキーバリュー設定ストア（Gmail トークン等の永続化に使用）"""
    __tablename__ = "settings"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=True)
