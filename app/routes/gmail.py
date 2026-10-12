import secrets
import hashlib
import base64

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app, abort
from app.extensions import db
from app.models import EmailFormat, Category, ScanLog, ScanSkipDetail
from app.services import gmail_service
from app.services.email_parser import preview_extraction
from app.validators import validate_email_format_form

bp = Blueprint("gmail", __name__, url_prefix="/gmail")


def _get_redirect_uri() -> str:
    return current_app.config["GMAIL_REDIRECT_URI"]


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


VALID_SCAN_INTERVALS = {15: "15分", 30: "30分", 60: "1時間", 1440: "1日"}


def _get_scan_interval_minutes() -> int:
    from app.models import Setting
    s = Setting.query.get("scan_interval_minutes")
    return int(s.value) if s else 30


@bp.route("/")
def index():
    return _render_index()


def _render_index(status=200, **context):
    authenticated = gmail_service.is_authenticated()
    formats = EmailFormat.query.order_by(EmailFormat.company_name).all()
    manual_logs = (ScanLog.query.filter_by(source="gmail", trigger="manual")
                   .order_by(ScanLog.scanned_at.desc()).limit(5).all())
    auto_logs = (ScanLog.query.filter_by(source="gmail", trigger="auto")
                 .order_by(ScanLog.scanned_at.desc()).limit(5).all())
    categories = Category.query.order_by(Category.name).all()
    scan_interval = _get_scan_interval_minutes()
    return render_template(
        "gmail/index.html",
        authenticated=authenticated,
        formats=formats,
        manual_logs=manual_logs,
        auto_logs=auto_logs,
        categories=categories,
        scan_interval=scan_interval,
        valid_scan_intervals=VALID_SCAN_INTERVALS,
        **context,
    ), status


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
    session["gmail_oauth_state"] = state
    return redirect(auth_url)


@bp.route("/oauth/callback")
def oauth_callback():
    state = session.pop("gmail_oauth_state", None)
    code_verifier = _load_and_delete_verifier()
    received_state = request.args.get("state", "")
    if (
        not state or not code_verifier or len(request.args.getlist("state")) != 1
        or not secrets.compare_digest(state.encode(), received_state.encode())
    ):
        abort(400, description="Gmail認証の確認に失敗しました。連携を最初からやり直してください。")
    if request.args.get("error") or not request.args.get("code"):
        abort(400, description="Gmail認証が完了していません。連携をやり直してください。")
    flow = gmail_service.get_flow(_get_redirect_uri(), state=state)
    # RenderのHTTPS終端後はrequest.urlがHTTPになり得る。設定済みのURLを使う。
    authorization_response = _get_redirect_uri() + "?" + request.query_string.decode("ascii")
    flow.fetch_token(
        authorization_response=authorization_response,
        code_verifier=code_verifier,
    )
    gmail_service._save_token(flow.credentials)
    flash("Gmail連携が完了しました", "success")
    return redirect(url_for("gmail.index"))


@bp.route("/scan", methods=["POST"])
def scan():
    batch = request.form.get("batch") == "1"
    try:
        result = gmail_service.scan_emails(batch=batch, trigger="manual", background=True)
    except Exception:
        current_app.logger.exception("Gmailスキャンの開始に失敗しました")
        return jsonify({"error": "スキャンを開始できませんでした。もう一度お試しください。"}), 500
    if result is None:
        return jsonify({"error": "スキャン中です"}), 409
    return jsonify(result)


@bp.route("/scan/progress")
def scan_progress():
    return jsonify(gmail_service.get_scan_progress())


@bp.route("/formats/new", methods=["POST"])
def format_new():
    data, errors = validate_email_format_form(request.form)
    if errors:
        return _render_index(
            400,
            format_form=request.form,
            format_errors=errors,
            open_format_modal=True,
        )

    ef = EmailFormat(**data)
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


@bp.route("/scan/interval", methods=["GET"])
def get_scan_interval():
    return jsonify({"minutes": _get_scan_interval_minutes()})


@bp.route("/scan/interval", methods=["POST"])
def set_scan_interval():
    from app.models import Setting
    from app.extensions import scheduler
    minutes = request.json.get("minutes")
    if minutes not in VALID_SCAN_INTERVALS:
        return jsonify({"error": "無効な値です"}), 400

    s = Setting.query.get("scan_interval_minutes")
    if s:
        s.value = str(minutes)
    else:
        db.session.add(Setting(key="scan_interval_minutes", value=str(minutes)))
    db.session.commit()

    try:
        scheduler.reschedule_job("gmail_scan", trigger="interval", minutes=minutes)
    except Exception:
        pass  # スケジューラが未起動の場合（テスト環境等）は無視

    return jsonify({"minutes": minutes, "label": VALID_SCAN_INTERVALS[minutes]})


@bp.route("/formats/preview", methods=["POST"])
def format_preview():
    body = request.json.get("body", "")
    prefix = request.json.get("prefix", "")
    suffix = request.json.get("suffix", "")
    mode = request.json.get("mode", "amount")
    result = preview_extraction(body, prefix, suffix, mode=mode)
    return jsonify(result)
