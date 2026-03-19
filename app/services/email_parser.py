import re
from app.models import EmailFormat


def build_pattern(prefix: str, suffix: str) -> str:
    """前後テキストからregexパターンを生成する"""
    p = re.escape(prefix)
    s = re.escape(suffix)
    return rf"{p}\s*[¥￥]?\s*([\d,]+)\s*{s}"


def extract_amount(body: str, email_format: EmailFormat) -> int | None:
    """メール本文から金額を抽出する。見つからなければNoneを返す"""
    pattern = build_pattern(email_format.amount_prefix, email_format.amount_suffix)
    match = re.search(pattern, body)
    if match:
        amount_str = match.group(1).replace(",", "")
        return int(amount_str)
    return None


def preview_extraction(body: str, prefix: str, suffix: str) -> dict:
    """フォーマット登録時のプレビュー用。抽出結果を返す"""
    pattern = build_pattern(prefix, suffix)
    match = re.search(pattern, body)
    if match:
        amount_str = match.group(1).replace(",", "")
        return {"success": True, "amount": int(amount_str), "matched": match.group(0)}
    return {"success": False, "amount": None, "matched": None}
