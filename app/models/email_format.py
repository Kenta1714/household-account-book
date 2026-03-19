from app.extensions import db
from datetime import datetime


class EmailFormat(db.Model):
    __tablename__ = "email_formats"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), nullable=False)
    sender_email = db.Column(db.String(255), nullable=False)
    amount_prefix = db.Column(db.String(255), nullable=False)  # 金額直前のテキスト
    amount_suffix = db.Column(db.String(255), nullable=False)  # 金額直後のテキスト
    default_category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    sample_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    default_category = db.relationship("Category", foreign_keys=[default_category_id])

    def to_dict(self):
        return {
            "id": self.id,
            "company_name": self.company_name,
            "sender_email": self.sender_email,
            "amount_prefix": self.amount_prefix,
            "amount_suffix": self.amount_suffix,
            "default_category_id": self.default_category_id,
            "default_category_name": self.default_category.name if self.default_category else None,
        }
