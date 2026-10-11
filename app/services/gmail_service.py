import json
import base64
import threading
from datetime import datetime

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from flask import current_app

from app.extensions import db
from app.models import Transaction, EmailFormat, ScanLog, ScanSkipDetail, Setting
from app.services.email_parser import extract_amount, extract_content, extract_card_name

_GMAIL_TOKEN_KEY = "gmail_token"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# スキャン進捗（メモリ内）
_scan_lock = threading.Lock()
_scan_progress = {
    "running": False,
    "batch": False,
    "total_found": 0,
    "added": 0,
    "done": False,
    "error": None,
    "result": None,
}


def get_scan_progress() -> dict:
    with _scan_lock:
        return dict(_scan_progress)


def _update_progress(**kwargs):
    with _scan_lock:
        _scan_progress.update(kwargs)


def get_flow(redirect_uri: str, *, state: str | None = None) -> Flow:
    """起動時に検証した環境別のOAuth認証情報を使用する。"""
    return Flow.from_client_config(
        current_app.config["GOOGLE_CLIENT_CONFIG"], scopes=SCOPES,
        redirect_uri=redirect_uri, state=state, autogenerate_code_verifier=False,
    )


def _load_token_json() -> str | None:
    """トークン未登録は正常。DB障害は呼び出し元へ伝える。"""
    row = db.session.get(Setting, _GMAIL_TOKEN_KEY)
    return row.value if row else None


def _save_token(creds: Credentials):
    """既存のDBキーを維持する。保存に失敗したらロールバックして通知する。"""
    token_json = creds.to_json()
    try:
        row = db.session.get(Setting, _GMAIL_TOKEN_KEY)
        if row:
            row.value = token_json
        else:
            db.session.add(Setting(key=_GMAIL_TOKEN_KEY, value=token_json))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _delete_token():
    """Googleから失効が確認できたDBトークンのみ削除する。"""
    try:
        row = db.session.get(Setting, _GMAIL_TOKEN_KEY)
        if row:
            db.session.delete(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def get_credentials() -> Credentials | None:
    token_json = _load_token_json()
    if not token_json:
        return None
    creds = Credentials.from_authorized_user_info(
        json.loads(token_json), SCOPES
    )
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            revoked = not exc.retryable and any(
                isinstance(arg, dict) and arg.get("error") == "invalid_grant"
                for arg in exc.args
            )
            if revoked:
                _delete_token()
                return None
            raise
        _save_token(creds)
    return creds if creds and creds.valid else None


def is_authenticated() -> bool:
    return get_credentials() is not None


def get_gmail_service():
    creds = get_credentials()
    if not creds:
        raise RuntimeError("Gmail未認証")
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload: dict) -> str:
    body = ""
    if payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
    elif payload.get("parts"):
        for part in payload["parts"]:
            mime = part.get("mimeType", "")
            if mime == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
            elif mime == "text/html" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
    return body


def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def scan_emails(batch: bool = False, trigger: str = "auto") -> dict:
    _update_progress(running=True, batch=batch, total_found=0, added=0,
                     done=False, error=None, result=None)
    try:
        result = _do_scan(batch, trigger=trigger)
    except Exception as e:
        _update_progress(running=False, done=True, error=str(e))
        raise
    _update_progress(running=False, done=True, result=result,
                     total_found=result["total_found"], added=result["added"])
    return result


def _do_scan(batch: bool, trigger: str = "auto") -> dict:
    service = get_gmail_service()
    formats = EmailFormat.query.all()
    if not formats:
        return {"total_found": 0, "added": 0, "skipped": 0,
                "skip_reasons": {"no_format": 0, "duplicate": 0, "no_amount": 0}}

    sender_query = " OR ".join(f"from:{ef.sender_email}" for ef in formats)

    if batch:
        query = sender_query
    else:
        last_log = (
            ScanLog.query.filter_by(source="gmail")
            .order_by(ScanLog.scanned_at.desc())
            .first()
        )
        if last_log:
            since_ts = int(last_log.scanned_at.timestamp())
            query = f"after:{since_ts} ({sender_query})"
        else:
            from datetime import timedelta
            since_ts = int((datetime.utcnow() - timedelta(days=90)).timestamp())
            query = f"after:{since_ts} ({sender_query})"

    total_found = 0
    added = 0
    skip_reasons = {"duplicate": 0, "no_format": 0, "no_amount": 0}
    skip_details = []

    page_token = None
    while True:
        kwargs = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.users().messages().list(**kwargs).execute()
        messages = result.get("messages", [])
        total_found += len(messages)
        _update_progress(total_found=total_found)

        for msg in messages:
            msg_id = msg["id"]
            external_id = f"gmail_{msg_id}"

            if Transaction.query.filter_by(external_id=external_id).first():
                skip_reasons["duplicate"] += 1
                skip_details.append({
                    "reason": "duplicate",
                    "sender": "", "subject": "", "date_str": "",
                    "body": None, "extra_info": external_id,
                })
                continue

            detail = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = detail.get("payload", {}).get("headers", [])
            sender = _get_header(headers, "from")
            date_str = _get_header(headers, "date")
            subject = _get_header(headers, "subject")
            body = _decode_body(detail.get("payload", {}))

            matched_format = None
            for ef in formats:
                if ef.sender_email.lower() in sender.lower():
                    matched_format = ef
                    break

            if not matched_format:
                skip_reasons["no_format"] += 1
                skip_details.append({
                    "reason": "no_format",
                    "sender": sender, "subject": subject, "date_str": date_str,
                    "body": body, "extra_info": None,
                })
                continue

            amount = extract_amount(body, matched_format)
            if not amount:
                skip_reasons["no_amount"] += 1
                skip_details.append({
                    "reason": "no_amount",
                    "sender": sender, "subject": subject, "date_str": date_str,
                    "body": body, "extra_info": matched_format.company_name,
                })
                continue

            content = extract_content(body, matched_format)
            card_name = extract_card_name(body, matched_format)
            description = content if content else (card_name or matched_format.company_name)

            try:
                tx_date = datetime.strptime(date_str[:16], "%a, %d %b %Y").date()
            except Exception:
                tx_date = datetime.utcnow().date()

            tx = Transaction(
                date=tx_date,
                amount=amount,
                type="expense",
                description=description,
                source="gmail",
                source_detail=card_name if card_name else matched_format.company_name,
                external_id=external_id,
                category_id=matched_format.default_category_id,
            )
            db.session.add(tx)
            added += 1
            _update_progress(added=added)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    db.session.commit()

    skipped = sum(skip_reasons.values())
    detail_str = (
        f"重複:{skip_reasons['duplicate']} / "
        f"フォーマット不一致:{skip_reasons['no_format']} / "
        f"金額未取得:{skip_reasons['no_amount']}"
    )
    log = ScanLog(
        source="gmail",
        trigger=trigger,
        total_found=total_found,
        added=added,
        skipped=skipped,
        detail=detail_str,
    )
    db.session.add(log)
    db.session.flush()

    ScanSkipDetail.query.filter(ScanSkipDetail.scan_log_id != log.id).delete(
        synchronize_session=False
    )
    for sd in skip_details:
        db.session.add(ScanSkipDetail(
            scan_log_id=log.id,
            reason=sd["reason"],
            sender=sd["sender"],
            subject=sd["subject"],
            date_str=sd["date_str"],
            body=sd["body"],
            extra_info=sd["extra_info"],
        ))

    db.session.commit()

    return {"total_found": total_found, "added": added, "skipped": skipped,
            "skip_reasons": skip_reasons}
