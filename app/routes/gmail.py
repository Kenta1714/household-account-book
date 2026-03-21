import os
import secrets
import hashlib
import base64

# 本番環境では GMAIL_REDIRECT_URI に Render の URL を設定する
_DEFAULT_REDIRECT_URI = "http://localhost:8080/gmail/oauth/callback"

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from app.extensions import db
from app.models import EmailFormat, Category, ScanLog, ScanSkipDetail
from app.services import gmail_service
from app.services.email_parser import preview_extraction

bp = Blueprint("gmail", __name__, url_prefix="/gmail")


def _get_redirect_uri() -> str:
    return os.environ.get("GMAIL_REDIRECT_URI", _DEFAULT_REDIRECT_URI)


def _pkce_pair():
    verifier = secrets.token_urlsafe(96)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _save_verifier(verifier: str):
    session["pkce_verifier"] = verifier


def _load_and_delete_verifier() -> str | None:
    return session.pop("pkce_verifier", None)


@bp.route("/")
def index():
    authenticated = gmail_service.is_authenticated()
    formats = EmailFormat.query.order_by(EmailFormat.company_name).all()
    logs = ScanLog.query.filter_by(source="gmail").order_by(ScanLog.scanned_at.desc()).limit(10).all()
    categories = Category.query.order_by(Category.name).all()
    return render_template("gmail/index.html",
                           authenticated=authenticated,
                           formats=formats,
                           logs=logs,
                           categories=categories)


@bp.route("/auth")
def auth():
    verifier, challenge = _pkce_pair()
    _save_verifier(verifier)
    flow = gmail_service.get_flow(_get_redirect_uri())
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    return redirect(auth_url)


@bp.route("/oauth/callback")
def oauth_callback():
    flow = gmail_service.get_flow(_get_redirect_uri())
    code_verifier = _load_and_delete_verifier()
    flow.fetch_token(
        authorization_response=request.url,
        code_verifier=code_verifier,
    )
    gmail_service._save_token(flow.credentials)
    flash("Gmail連携が完了しました", "success")
    return redirect(url_for("gmail.index"))


@bp.route("/scan", methods=["POST"])
def scan():
    batch = request.form.get("batch") == "1"
    progress = gmail_service.get_scan_progress()
    if progress["running"]:
        return jsonify({"error": "スキャン中です"}), 409

    from threading import Thread
    from flask import current_app
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            gmail_service.scan_emails(batch=batch)

    Thread(target=run, daemon=True).start()
    return jsonify({"started": True})


@bp.route("/scan/progress")
def scan_progress():
    return jsonify(gmail_service.get_scan_progress())


@bp.route("/formats/new", methods=["POST"])
def format_new():
    ef = EmailFormat(
        company_name=request.form["company_name"],
        sender_email=request.form["sender_email"],
        amount_prefix=request.form["amount_prefix"],
        amount_suffix=request.form["amount_suffix"],
        content_prefix=request.form.get("content_prefix") or None,
        content_suffix=request.form.get("content_suffix") or None,
        default_category_id=request.form.get("default_category_id") or None,
        sample_text=request.form.get("sample_text"),
    )
    db.session.add(ef)
    db.session.commit()
    flash(f"「{ef.company_name}」のフォーマットを登録しました", "success")
    return redirect(url_for("gmail.index"))


@bp.route("/formats/<int:fmt_id>/delete", methods=["POST"])
def format_delete(fmt_id):
    ef = EmailFormat.query.get_or_404(fmt_id)
    db.session.delete(ef)
    db.session.commit()
    flash("フォーマットを削除しました", "success")
    return redirect(url_for("gmail.index"))


@bp.route("/scan/skip_details")
def scan_skip_details():
    latest_log = (
        ScanLog.query.filter_by(source="gmail")
        .order_by(ScanLog.scanned_at.desc())
        .first()
    )
    if not latest_log:
        return jsonify([])
    details = ScanSkipDetail.query.filter_by(scan_log_id=latest_log.id).all()
    reason_labels = {
        "duplicate": "重複（インポート済み）",
        "no_format": "送信元フォーマット未登録",
        "no_amount": "金額抽出失敗",
    }
    return jsonify([{
        "reason": d.reason,
        "reason_label": reason_labels.get(d.reason, d.reason),
        "sender": d.sender or "",
        "subject": d.subject or "",
        "date_str": d.date_str or "",
        "body": d.body or "",
        "extra_info": d.extra_info or "",
    } for d in details])


@bp.route("/formats/preview", methods=["POST"])
def format_preview():
    body = request.json.get("body", "")
    prefix = request.json.get("prefix", "")
    suffix = request.json.get("suffix", "")
    mode = request.json.get("mode", "amount")
    result = preview_extraction(body, prefix, suffix, mode=mode)
    return jsonify(result)
