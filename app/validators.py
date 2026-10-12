import re
from datetime import datetime
from email.utils import parseaddr

from app.extensions import db
from app.models import Category


ALLOWED_TRANSACTION_TYPES = {"income", "expense"}
ALLOWED_CARD_NAME_MODES = {"extract", "keyword"}
MAX_TRANSACTION_AMOUNT = 999_999_999
MAX_MEMO_LENGTH = 5_000
MAX_LONG_TEXT_LENGTH = 20_000

_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _raw_text(form, field: str) -> str:
    """フォーム値を安全に文字列として取り出す。"""
    value = form.get(field, "")
    return value if isinstance(value, str) else ""


def _required_text(form, field: str, label: str, max_length: int, errors: dict) -> str:
    value = _raw_text(form, field).strip()
    if not value:
        errors[field] = f"{label}を入力してください"
    elif len(value) > max_length:
        errors[field] = f"{label}は{max_length}文字以内で入力してください"
    return value


def _optional_text(form, field: str, label: str, max_length: int, errors: dict) -> str | None:
    value = _raw_text(form, field)
    if not value.strip():
        return None
    if len(value) > max_length:
        errors[field] = f"{label}は{max_length}文字以内で入力してください"
    return value


def _required_verbatim_text(
    form, field: str, label: str, max_length: int, errors: dict
) -> str:
    """前後の空白にも意味がある抽出文字列を、加工せずに検証する。"""
    value = _raw_text(form, field)
    if not value.strip():
        errors[field] = f"{label}を入力してください"
    elif len(value) > max_length:
        errors[field] = f"{label}は{max_length}文字以内で入力してください"
    return value


def _category_id(form, field: str, errors: dict) -> tuple[int | None, Category | None]:
    value = _raw_text(form, field).strip()
    if not value:
        return None, None
    try:
        category_id = int(value)
    except ValueError:
        errors[field] = "カテゴリの指定が不正です"
        return None, None
    if category_id <= 0:
        errors[field] = "カテゴリの指定が不正です"
        return None, None

    category = db.session.get(Category, category_id)
    if category is None:
        errors[field] = "指定されたカテゴリは存在しません"
        return None, None
    return category_id, category


def validate_transaction_form(form) -> tuple[dict, dict]:
    """取引フォームを検証し、保存用データと項目別エラーを返す。"""
    data = {}
    errors = {}

    date_text = _raw_text(form, "date").strip()
    try:
        data["date"] = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        errors["date"] = "正しい日付を入力してください"

    amount_text = _raw_text(form, "amount").strip()
    try:
        amount = int(amount_text)
    except ValueError:
        errors["amount"] = "金額は整数で入力してください"
    else:
        if not 1 <= amount <= MAX_TRANSACTION_AMOUNT:
            errors["amount"] = f"金額は1円以上{MAX_TRANSACTION_AMOUNT:,}円以下にしてください"
        else:
            data["amount"] = amount

    tx_type = _raw_text(form, "type").strip()
    if tx_type not in ALLOWED_TRANSACTION_TYPES:
        errors["type"] = "種別は収入または支出を選択してください"
    else:
        data["type"] = tx_type

    description = _required_text(form, "description", "内容", 255, errors)
    if "description" not in errors:
        data["description"] = description

    memo = _optional_text(form, "memo", "メモ", MAX_MEMO_LENGTH, errors)
    if "memo" not in errors:
        data["memo"] = memo

    category_id, category = _category_id(form, "category_id", errors)
    if category is not None and tx_type in ALLOWED_TRANSACTION_TYPES and category.type != tx_type:
        errors["category_id"] = "取引種別とカテゴリ種別が一致しません"
    if "category_id" not in errors:
        data["category_id"] = category_id

    return data, errors


def validate_category_form(form, default_color: str = "#6c757d") -> tuple[dict, dict]:
    """カテゴリフォームを検証する。"""
    errors = {}
    name = _required_text(form, "name", "カテゴリ名", 100, errors)

    category_type = _raw_text(form, "type").strip()
    if category_type not in ALLOWED_TRANSACTION_TYPES:
        errors["type"] = "種別は収入または支出を選択してください"

    color = _raw_text(form, "color").strip() or default_color
    if not _HEX_COLOR_PATTERN.fullmatch(color):
        errors["color"] = "色の指定が不正です"

    return {"name": name, "type": category_type, "color": color}, errors


def validate_email_format_form(form) -> tuple[dict, dict]:
    """Gmailのメールフォーマット登録値を検証する。"""
    errors = {}
    data = {
        "company_name": _required_text(form, "company_name", "カード名", 100, errors),
        "sender_email": _required_text(
            form, "sender_email", "送信元メールアドレス", 255, errors
        ).lower(),
        "amount_prefix": _required_verbatim_text(
            form, "amount_prefix", "金額直前のテキスト", 255, errors
        ),
        "amount_suffix": _required_verbatim_text(
            form, "amount_suffix", "金額直後のテキスト", 255, errors
        ),
        "content_prefix": _optional_text(
            form, "content_prefix", "内容直前のテキスト", 255, errors
        ),
        "content_suffix": _optional_text(
            form, "content_suffix", "内容直後のテキスト", 255, errors
        ),
        "sample_text": _optional_text(
            form, "sample_text", "サンプルメール本文", MAX_LONG_TEXT_LENGTH, errors
        ),
    }

    sender_email = data["sender_email"]
    _, parsed_email = parseaddr(sender_email)
    if (
        "sender_email" not in errors
        and (parsed_email != sender_email or not _EMAIL_PATTERN.fullmatch(sender_email))
    ):
        errors["sender_email"] = "正しいメールアドレスを入力してください"

    if bool(data["content_prefix"]) != bool(data["content_suffix"]):
        errors["content"] = "内容の前後テキストは両方入力するか、両方空欄にしてください"

    mode = _raw_text(form, "card_name_mode").strip() or None
    if mode is not None and mode not in ALLOWED_CARD_NAME_MODES:
        errors["card_name_mode"] = "カード名の抽出方法が不正です"
    data["card_name_mode"] = mode

    if mode == "extract":
        data["card_name_prefix"] = _required_verbatim_text(
            form, "card_name_prefix", "カード名直前のテキスト", 255, errors
        )
        data["card_name_suffix"] = _required_verbatim_text(
            form, "card_name_suffix", "カード名直後のテキスト", 255, errors
        )
        data["card_name_keywords"] = None
    elif mode == "keyword":
        data["card_name_prefix"] = None
        data["card_name_suffix"] = None
        data["card_name_keywords"] = _required_text(
            form, "card_name_keywords", "キーワード", 2_000, errors
        )
    else:
        data["card_name_prefix"] = None
        data["card_name_suffix"] = None
        data["card_name_keywords"] = None

    category_id, category = _category_id(form, "default_category_id", errors)
    if category is not None and category.type != "expense":
        errors["default_category_id"] = "Gmail取込には支出カテゴリを選択してください"
    data["default_category_id"] = category_id

    return data, errors
