import re
from app.models import EmailFormat


def build_amount_pattern(prefix: str, suffix: str) -> str:
    p = re.escape(prefix)
    s = re.escape(suffix)
    return rf"{p}\s*[¥￥]?\s*([\d,]+)\s*{s}"


def build_content_pattern(prefix: str, suffix: str) -> str:
    p = re.escape(prefix)
    s = re.escape(suffix)
    return rf"{p}\s*(.+?)\s*{s}"


def extract_amount(body: str, email_format: EmailFormat) -> int | None:
    pattern = build_amount_pattern(email_format.amount_prefix, email_format.amount_suffix)
    match = re.search(pattern, body)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def extract_content(body: str, email_format: EmailFormat) -> str | None:
    """ご利用内容を抽出する。フォーマット未設定の場合はNoneを返す"""
    if not email_format.content_prefix or not email_format.content_suffix:
        return None
    pattern = build_content_pattern(email_format.content_prefix, email_format.content_suffix)
    match = re.search(pattern, body)
    if match:
        return match.group(1).strip()
    return None


def preview_extraction(body: str, prefix: str, suffix: str, mode: str = "amount") -> dict:
    """フォーマット登録時のプレビュー用"""
    if mode == "content":
        pattern = build_content_pattern(prefix, suffix)
        match = re.search(pattern, body)
        if match:
            return {"success": True, "value": match.group(1).strip(), "matched": match.group(0)}
        return {"success": False, "value": None, "matched": None}
    else:
        pattern = build_amount_pattern(prefix, suffix)
        match = re.search(pattern, body)
        if match:
            amount_str = match.group(1).replace(",", "")
            return {"success": True, "value": int(amount_str), "matched": match.group(0)}
        return {"success": False, "value": None, "matched": None}
