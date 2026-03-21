from app.extensions import db
from datetime import datetime


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Integer, nullable=False)  # 円単位
    type = db.Column(db.String(10), nullable=False)  # "income" or "expense"
    description = db.Column(db.String(255), nullable=False)
    memo = db.Column(db.Text, nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    source = db.Column(db.String(20), default="manual")  # "manual" / "gmail" / "paypay"
    source_detail = db.Column(db.String(200), nullable=True)  # Gmail: 会社名
    external_id = db.Column(db.String(255), nullable=True, unique=True)  # 重複防止
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "amount": self.amount,
            "type": self.type,
            "description": self.description,
            "memo": self.memo,
            "category_id": self.category_id,
            "category_name": self.category.name if self.category else None,
            "category_color": self.category.color if self.category else None,
            "source": self.source,
        }
