import csv
import io
from datetime import datetime
from app.extensions import db
from app.models import Transaction, ScanLog

# 取り込む取引種別
IMPORT_TYPES = {"支払い", "返金", "受け取った金額"}
# 支出扱いにする取引種別
EXPENSE_TYPES = {"支払い", "投資"}
# 収入扱いにする取引種別
INCOME_TYPES = {"返金", "受け取った金額"}


def parse_amount(value: str) -> int:
    """"-" や "1,000" などの金額文字列をintに変換する"""
    value = value.strip().strip('"')
    if value == "-" or value == "":
        return 0
    return int(value.replace(",", ""))


def import_csv(file_content: bytes, import_investment: bool = False) -> dict:
    """PayPay CSVを解析してDBに保存する。重複はスキップ。"""
    target_types = IMPORT_TYPES.copy()
    if import_investment:
        target_types.add("投資")

    text = file_content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    total_found = 0
    added = 0
    skipped = 0
    errors = []

    for row in reader:
        tx_type_str = row.get("取引内容", "").strip()
        if tx_type_str not in target_types:
            continue

        total_found += 1
        external_id = f"paypay_{row.get('取引番号', '').strip()}"

        # 重複チェック
        if Transaction.query.filter_by(external_id=external_id).first():
            skipped += 1
            continue

        try:
            date_str = row["取引日"].strip()[:10]  # "YYYY/MM/DD" だけ使う
            tx_date = datetime.strptime(date_str, "%Y/%m/%d").date()

            withdrawal = parse_amount(row.get("出金金額（円）", "-"))
            deposit = parse_amount(row.get("入金金額（円）", "-"))

            if withdrawal > 0:
                amount = withdrawal
                tx_type = "expense"
            elif deposit > 0:
                amount = deposit
                tx_type = "income"
            else:
                skipped += 1
                continue

            description = row.get("取引先", "").strip() or tx_type_str

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

        except Exception as e:
            errors.append(str(e))
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

    return {"total_found": total_found, "added": added, "skipped": skipped}
