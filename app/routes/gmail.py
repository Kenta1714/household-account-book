from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from app.extensions import db
from app.models import EmailFormat, Category, ScanLog
from app.services import gmail_service
from app.services.email_parser import preview_extraction

bp = Blueprint("gmail", __name__, url_prefix="/gmail")


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
    redirect_uri = url_for("gmail.oauth_callback", _external=True)
    flow = gmail_service.get_flow(redirect_uri)
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    session["oauth_state"] = state
    return redirect(auth_url)


@bp.route("/oauth/callback")
def oauth_callback():
    redirect_uri = url_for("gmail.oauth_callback", _external=True)
    flow = gmail_service.get_flow(redirect_uri)
    flow.fetch_token(authorization_response=request.url)
    gmail_service._save_token(flow.credentials)
    flash("Gmail連携が完了しました", "success")
    return redirect(url_for("gmail.index"))


@bp.route("/scan", methods=["POST"])
def scan():
    batch = request.form.get("batch") == "1"
    try:
        result = gmail_service.scan_emails(batch=batch)
        flash(f"スキャン完了: {result['added']}件追加, {result['skipped']}件スキップ", "success")
    except Exception as e:
        flash(f"エラー: {e}", "danger")
    return redirect(url_for("gmail.index"))


@bp.route("/formats/new", methods=["POST"])
def format_new():
    ef = EmailFormat(
        company_name=request.form["company_name"],
        sender_email=request.form["sender_email"],
        amount_prefix=request.form["amount_prefix"],
        amount_suffix=request.form["amount_suffix"],
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


@bp.route("/formats/preview", methods=["POST"])
def format_preview():
    body = request.json.get("body", "")
    prefix = request.json.get("prefix", "")
    suffix = request.json.get("suffix", "")
    result = preview_extraction(body, prefix, suffix)
    return jsonify(result)
