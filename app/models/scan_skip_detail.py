from app.extensions import db


class ScanSkipDetail(db.Model):
    __tablename__ = "scan_skip_details"

    id = db.Column(db.Integer, primary_key=True)
    scan_log_id = db.Column(db.Integer, db.ForeignKey("scan_logs.id"), nullable=False)
    reason = db.Column(db.String(20), nullable=False)  # duplicate / no_format / no_amount / invalid_date
    sender = db.Column(db.String(300))
    subject = db.Column(db.String(500))
    date_str = db.Column(db.String(100))
    body = db.Column(db.Text)         # メール本文（duplicateは None）
    extra_info = db.Column(db.String(300))  # フォーマット名など補足情報

    scan_log = db.relationship("ScanLog", backref="skip_details")
