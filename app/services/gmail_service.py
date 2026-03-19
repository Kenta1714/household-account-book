import os
import base64
import json
import re
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.extensions import db
from app.models import Transaction, EmailFormat, ScanLog
from app.services.email_parser import extract_amount

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def get_flow(redirect_uri: str) -> Flow:
    return Flow.from_client_secrets_file(
        CREDENTIALS_FILE, scopes=SCOPES, redirect_uri=redirect_uri
    )


def get_credentials() -> Credentials | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
    return creds if creds and creds.valid else None


def _save_token(creds: Credentials):
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())


def is_authenticated() -> bool:
    return get_credentials() is not None


def get_gmail_service():
    creds = get_credentials()
    if not creds:
        raise RuntimeError("Gmail未認証")
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload: dict) -> str:
    """メールのpayloadから本文テキストを取得する"""
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


def scan_emails(batch: bool = False) -> dict:
    """
    Gmailをスキャンして登録済みフォーマットに一致するメールを取り込む。
    batch=True のとき全件スキャン、False のとき未読のみ。
    """
    service = get_gmail_service()
    formats = EmailFormat.query.all()
    if not formats:
        return {"total_found": 0, "added": 0, "skipped": 0}

    # 送信元メールアドレスでOR検索クエリを組み立てる
    sender_query = " OR ".join(f"from:{ef.sender_email}" for ef in formats)
    query = sender_query if batch else f"is:unread ({sender_query})"

    total_found = 0
    added = 0
    skipped = 0
    errors = []

    page_token = None
    while True:
        kwargs = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.users().messages().list(**kwargs).execute()
        messages = result.get("messages", [])
        total_found += len(messages)

        for msg in messages:
            msg_id = msg["id"]
            external_id = f"gmail_{msg_id}"

            if Transaction.query.filter_by(external_id=external_id).first():
                skipped += 1
                continue

            detail = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = detail.get("payload", {}).get("headers", [])
            sender = _get_header(headers, "from")
            date_str = _get_header(headers, "date")
            body = _decode_body(detail.get("payload", {}))

            matched_format = None
            for ef in formats:
                if ef.sender_email.lower() in sender.lower():
                    matched_format = ef
                    break

            if not matched_format:
                skipped += 1
                continue

            amount = extract_amount(body, matched_format)
            if not amount:
                skipped += 1
                continue

            try:
                tx_date = datetime.strptime(date_str[:16], "%a, %d %b %Y").date()
            except Exception:
                tx_date = datetime.utcnow().date()

            tx = Transaction(
                date=tx_date,
                amount=amount,
                type="expense",
                description=matched_format.company_name,
                source="gmail",
                external_id=external_id,
                category_id=matched_format.default_category_id,
            )
            db.session.add(tx)
            added += 1

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    db.session.commit()

    log = ScanLog(
        source="gmail",
        total_found=total_found,
        added=added,
        skipped=skipped,
        detail="; ".join(errors) if errors else None,
    )
    db.session.add(log)
    db.session.commit()

    return {"total_found": total_found, "added": added, "skipped": skipped}
