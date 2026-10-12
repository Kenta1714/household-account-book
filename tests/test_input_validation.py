import base64
import io
import json
import os
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import Category, EmailFormat, ScanLog, Transaction
from tests.helpers import migrate_database, seed_categories


CLIENT_CONFIG = {
    "web": {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        credentials = root / "credentials.json"
        credentials.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
        environment = patch.dict(os.environ, {
            "APP_ENV": "development",
            "FLASK_SECRET_KEY": "test-only-secret-key-with-at-least-32-characters",
            "APP_USERNAME": "test-user",
            "APP_PASSWORD": "test-password",
            "DATABASE_URL": "sqlite:///" + str(root / "household.db"),
            "GMAIL_REDIRECT_URI": "http://localhost:8080/gmail/oauth/callback",
            "CREDENTIALS_FILE": str(credentials),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        dotenv = patch("app.config.load_dotenv")
        dotenv.start()
        self.addCleanup(dotenv.stop)

        self.app = create_app({"TESTING": True, "SCHEDULER_ENABLED": False})
        migrate_database(self.app)
        seed_categories(self.app)
        self.client = self.app.test_client()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(b"test-user:test-password").decode()
        }
        self.addCleanup(self._dispose_app)
        # 入力検証テストも実際の画面から取得したCSRFトークンで送信する。
        response = self.client.get("/transactions/new", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)">',
            response.get_data(as_text=True),
        ).group(1)
        self.headers["X-CSRFToken"] = token

    def _dispose_app(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def _categories(self):
        with self.app.app_context():
            expense = Category.query.filter_by(type="expense").first()
            income = Category.query.filter_by(type="income").first()
            return expense.id, income.id

    @staticmethod
    def _transaction_form(**changes):
        form = {
            "date": "2026-10-12",
            "amount": "1500",
            "type": "expense",
            "description": "昼食",
            "memo": "",
            "category_id": "",
        }
        form.update(changes)
        return form

    @staticmethod
    def _email_format_form(**changes):
        form = {
            "company_name": "テストカード",
            "sender_email": "notice@example.com",
            "amount_prefix": "ご利用金額:",
            "amount_suffix": "円",
            "content_prefix": "",
            "content_suffix": "",
            "card_name_mode": "",
            "card_name_prefix": "",
            "card_name_suffix": "",
            "card_name_keywords": "",
            "default_category_id": "",
            "sample_text": "",
        }
        form.update(changes)
        return form

    def test_transaction_create_rejects_invalid_values_without_saving(self):
        _, income_id = self._categories()
        cases = {
            "invalid date": {"date": "2026-02-31"},
            "non numeric amount": {"amount": "千円"},
            "zero amount": {"amount": "0"},
            "negative amount": {"amount": "-100"},
            "amount over maximum": {"amount": "1000000000"},
            "unknown type": {"type": "other"},
            "blank description": {"description": "   "},
            "long description": {"description": "x" * 256},
            "non numeric category": {"category_id": "abc"},
            "missing category": {"category_id": "999999"},
            "category type mismatch": {"category_id": str(income_id)},
            "long memo": {"memo": "x" * 5001},
        }

        for name, changes in cases.items():
            with self.subTest(name=name):
                response = self.client.post(
                    "/transactions/new",
                    data=self._transaction_form(**changes),
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 400)
                with self.app.app_context():
                    self.assertEqual(Transaction.query.count(), 0)

    def test_transaction_create_accepts_clean_valid_data(self):
        expense_id, _ = self._categories()
        response = self.client.post(
            "/transactions/new",
            data=self._transaction_form(
                description="  昼食  ", category_id=str(expense_id)
            ),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            tx = Transaction.query.one()
            self.assertEqual(tx.amount, 1500)
            self.assertEqual(tx.description, "昼食")
            self.assertEqual(tx.category_id, expense_id)

    def test_transaction_edit_rejects_invalid_data_without_mutating_record(self):
        with self.app.app_context():
            tx = Transaction(
                date=date(2026, 10, 1),
                amount=500,
                type="expense",
                description="変更前",
                source="manual",
            )
            db.session.add(tx)
            db.session.commit()
            tx_id = tx.id

        response = self.client.post(
            f"/transactions/{tx_id}/edit",
            data=self._transaction_form(amount="-1", description="変更後"),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            tx = db.session.get(Transaction, tx_id)
            self.assertEqual(tx.amount, 500)
            self.assertEqual(tx.description, "変更前")

    def test_category_create_rejects_invalid_values_without_saving(self):
        with self.app.app_context():
            original_count = Category.query.count()
        cases = (
            {"name": " ", "type": "expense", "color": "#4e79a7"},
            {"name": "x" * 101, "type": "expense", "color": "#4e79a7"},
            {"name": "不正", "type": "other", "color": "#4e79a7"},
            {"name": "不正", "type": "expense", "color": "red"},
        )
        for form in cases:
            with self.subTest(form=form):
                response = self.client.post("/categories/new", data=form, headers=self.headers)
                self.assertEqual(response.status_code, 400)
                with self.app.app_context():
                    self.assertEqual(Category.query.count(), original_count)

    def test_email_format_rejects_invalid_values_without_saving(self):
        _, income_id = self._categories()
        cases = {
            "bad email": {"sender_email": "not-an-email"},
            "long company": {"company_name": "x" * 101},
            "missing amount marker": {"amount_prefix": ""},
            "unpaired content markers": {"content_prefix": "店名:"},
            "unknown card mode": {"card_name_mode": "other"},
            "missing extract marker": {
                "card_name_mode": "extract", "card_name_prefix": "カード:",
            },
            "missing keywords": {"card_name_mode": "keyword"},
            "missing category": {"default_category_id": "999999"},
            "income category": {"default_category_id": str(income_id)},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                response = self.client.post(
                    "/gmail/formats/new",
                    data=self._email_format_form(**changes),
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 400)
                with self.app.app_context():
                    self.assertEqual(EmailFormat.query.count(), 0)

    def test_email_format_accepts_existing_expense_category(self):
        expense_id, _ = self._categories()
        response = self.client.post(
            "/gmail/formats/new",
            data=self._email_format_form(default_category_id=str(expense_id)),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            email_format = EmailFormat.query.one()
            self.assertEqual(email_format.sender_email, "notice@example.com")
            self.assertEqual(email_format.default_category_id, expense_id)

    def _upload_csv(self, text: str):
        return self.client.post(
            "/paypay/upload",
            data={"csv_file": (io.BytesIO(text.encode("utf-8-sig")), "paypay.csv")},
            headers=self.headers,
            content_type="multipart/form-data",
        )

    def test_paypay_log_failure_rolls_back_and_hides_internal_error(self):
        csv_text = (
            "取引番号,取引内容,取引日,出金金額（円）,入金金額（円）\n"
            "A1,支払い,2026/10/12,100,-\n"
        )

        def fail_log_insert(mapper, connection, target):
            raise RuntimeError("internal database detail")

        event.listen(ScanLog, "before_insert", fail_log_insert)
        try:
            with self.app.app_context(), self.assertLogs(self.app.logger, level="ERROR") as logs:
                response = self._upload_csv(csv_text)
                self.assertEqual(Transaction.query.count(), 0)
                self.assertEqual(ScanLog.query.count(), 0)
        finally:
            event.remove(ScanLog, "before_insert", fail_log_insert)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json, {
            "error": "CSVの取り込み中に予期しないエラーが発生しました",
        })
        self.assertIn("internal database detail", "\n".join(logs.output))
        response = self._upload_csv(csv_text)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["added"], 1)
        with self.app.app_context():
            self.assertEqual(Transaction.query.count(), 1)
            self.assertEqual(ScanLog.query.one().added, 1)

    def test_paypay_csv_rejects_missing_required_column_before_saving(self):
        response = self._upload_csv(
            "取引内容,取引日,出金金額（円）,入金金額（円）\n"
            "支払い,2026/10/12,100,-\n"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("取引番号", response.get_json()["error"])
        with self.app.app_context():
            self.assertEqual(Transaction.query.count(), 0)
            self.assertEqual(ScanLog.query.filter_by(source="paypay").count(), 0)

    def test_paypay_csv_skips_empty_transaction_number_without_creating_paypay_id(self):
        response = self._upload_csv(
            "取引番号,取引内容,取引日,出金金額（円）,入金金額（円）,取引先\n"
            ",支払い,2026/10/12,100,-,テスト店\n"
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertIn("取引番号が空です", result["errors"][0])
        with self.app.app_context():
            self.assertEqual(Transaction.query.count(), 0)
            self.assertIsNone(Transaction.query.filter_by(external_id="paypay_").first())

    def test_paypay_csv_imports_distinct_ids_and_reports_duplicate_and_bad_rows(self):
        response = self._upload_csv(
            "取引番号,取引内容,取引日,出金金額（円）,入金金額（円）,取引先\n"
            "A1,支払い,2026/10/12,100,-,店A\n"
            "A2,返金,2026/10/13,-,200,店B\n"
            "A1,支払い,2026/10/14,300,-,店C\n"
            "A3,支払い,不正な日付,400,-,店D\n"
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["total_found"], 4)
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(len(result["errors"]), 2)
        with self.app.app_context():
            self.assertEqual(Transaction.query.count(), 2)
            self.assertEqual(
                {tx.external_id for tx in Transaction.query.all()},
                {"paypay_A1", "paypay_A2"},
            )


if __name__ == "__main__":
    unittest.main()
