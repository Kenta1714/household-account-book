import csv
import io
from datetime import datetime
from app.extensions import db
from app.models import Transaction, ScanLog
from app.validators import MAX_TRANSACTION_AMOUNT

# 取り込む取引種別
IMPORT_TYPES = {"支払い", "返金", "受け取った金額"}
# 支出扱いにする取引種別
EXPENSE_TYPES = {"支払い", "投資"}
# 収入扱いにする取引種別
INCOME_TYPES = {"返金", "受け取った金額"}

REQUIRED_COLUMNS = {
    "取引番号",
    "取引内容",
    "取引日",
    "出金金額（円）",
    "入金金額（円）",
}


class CsvValidationError(ValueError):
    """CSVファイル全体を取り込めない入力エラー。"""


def parse_amount(value: str) -> int:
    """"-" や "1,000" などの金額文字列をintに変換する。"""
    if not isinstance(value, str):
        raise ValueError("金額が文字列ではありません")
    value = value.strip().strip('"')
    if value == "-" or value == "":
        return 0
    try:
        amount = int(value.replace(",", ""))
    except ValueError as exc:
        raise ValueError("金額の形式が不正です") from exc
    if not 0 <= amount <= MAX_TRANSACTION_AMOUNT:
        raise ValueError(f"金額は0円以上{MAX_TRANSACTION_AMOUNT:,}円以下にしてください")
    return amount


def import_csv(file_content: bytes, import_investment: bool = False) -> dict:
    """PayPay CSVを解析してDBに保存する。重複はスキップ。"""
    target_types = IMPORT_TYPES.copy()
    if import_investment:
        target_types.add("投資")

    try:
        text = file_content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvValidationError("CSVファイルをUTF-8で読み込めません") from exc

    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    missing_columns = REQUIRED_COLUMNS - headers
    if missing_columns:
        missing = "、".join(sorted(missing_columns))
        raise CsvValidationError(f"CSVに必須列がありません: {missing}")

    total_found = 0
    added = 0
    skipped = 0
    errors = []
    seen_external_ids = set()

    for line_number, row in enumerate(reader, start=2):
        tx_type_str = (row.get("取引内容") or "").strip()
        if tx_type_str not in target_types:
            continue

        total_found += 1
        transaction_number = (row.get("取引番号") or "").strip()
        if not transaction_number:
            errors.append(f"{line_number}行目: 取引番号が空です")
            skipped += 1
            continue
        if len(transaction_number) > 248:
            errors.append(f"{line_number}行目: 取引番号が長すぎます")
            skipped += 1
            continue

        external_id = f"paypay_{transaction_number}"

        if external_id in seen_external_ids:
            errors.append(f"{line_number}行目: CSV内で取引番号が重複しています")
            skipped += 1
            continue
        seen_external_ids.add(external_id)

        # 重複チェック
        if Transaction.query.filter_by(external_id=external_id).first():
            skipped += 1
            continue

        try:
            date_str = (row.get("取引日") or "").strip()[:10]  # "YYYY/MM/DD" だけ使う
            tx_date = datetime.strptime(date_str, "%Y/%m/%d").date()

            withdrawal = parse_amount(row.get("出金金額（円）") or "-")
            deposit = parse_amount(row.get("入金金額（円）") or "-")

            if withdrawal > 0:
                amount = withdrawal
                tx_type = "expense"
            elif deposit > 0:
                amount = deposit
                tx_type = "income"
            else:
                skipped += 1
                continue

            description = (row.get("取引先") or "").strip() or tx_type_str
            if len(description) > 255:
                raise ValueError("取引先は255文字以内で入力してください")

            tx = Transaction(
                date=tx_date,
                amount=amount,
                type=tx_type,
                description=description,
                source="paypay",
                external_id=external_id,
            )
            db.session.add(tx)
            added += 1

        except (TypeError, ValueError) as exc:
            errors.append(f"{line_number}行目: {exc}")
            skipped += 1

    db.session.commit()

    log = ScanLog(
        source="paypay",
        total_found=total_found,
        added=added,
        skipped=skipped,
        detail="; ".join(errors) if errors else None,
    )
    db.session.add(log)
    db.session.commit()

    return {
        "total_found": total_found,
        "added": added,
        "skipped": skipped,
        "errors": errors,
    }
