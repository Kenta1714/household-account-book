from app.extensions import db
from datetime import datetime


class ScanLog(db.Model):
    __tablename__ = "scan_logs"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(20), nullable=False)  # "gmail" or "paypay"
    trigger = db.Column(db.String(10), nullable=True)   # "manual" or "auto"
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)  # UTC。Gmailはスキャン開始時刻
    total_found = db.Column(db.Integer, default=0)
    added = db.Column(db.Integer, default=0)
    skipped = db.Column(db.Integer, default=0)
    detail = db.Column(db.Text, nullable=True)
