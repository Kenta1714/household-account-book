import base64
import io
import time
from datetime import date
from html.parser import HTMLParser
from unittest.mock import patch

from app.extensions import db
from app.models import Category, EmailFormat, Setting, Transaction
from tests.test_configuration import IsolatedEnvironment


class FormParser(HTMLParser):
    """レンダリングされたフォームとmetaタグからトークンを取得する。"""

    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current_form = None
        self.token = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") == "csrf-token":
            self.token = attrs.get("content")
        elif tag == "form":
            self.current_form = {**attrs, "tokens": []}
            self.forms.append(self.current_form)
        elif tag == "input" and self.current_form is not None:
            if attrs.get("name") == "csrf_token":
                self.current_form["tokens"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


class CsrfTests(IsolatedEnvironment):
    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.client = self.app.test_client()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(b"test-user:test-only-long-password").decode()
        }
        with self.app.app_context():
            category = Category.query.filter_by(type="expense").first()
            self.cat_id = category.id
            tx = Transaction(
                date=date(2026, 10, 12), amount=500, type="expense",
                description="保護する取引", source="manual", category_id=self.cat_id,
            )
            email_format = EmailFormat(
                company_name="テストカード", sender_email="notice@example.com",
                amount_prefix="金額:", amount_suffix="円",
            )
            db.session.add_all([tx, email_format])
            db.session.commit()
            self.tx_id = tx.id
            self.fmt_id = email_format.id

    def page(self, path, client=None):
        response = (client or self.client).get(path, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        parsed = FormParser(response.get_data(as_text=True))
        self.assertTrue(parsed.token)
        return parsed

    def form_token(self, path, action=None):
        parsed = self.page(path)
        forms = [form for form in parsed.forms if form.get("method", "get").lower() == "post"]
        if action is not None:
            forms = [form for form in forms if form.get("action") == action]
        self.assertTrue(forms)
        self.assertEqual(len(forms[0]["tokens"]), 1)
        return forms[0]["tokens"][0]["value"]

    def test_every_rendered_post_form_contains_a_valid_token(self):
        # 連携済み表示にして、手動スキャンのフォームも確認する。
        with patch("app.services.gmail_service.is_authenticated", return_value=True):
            for path in (
                "/transactions/?year=2026&month=10", "/transactions/new",
                f"/transactions/{self.tx_id}/edit", "/categories/", "/gmail/", "/paypay/",
            ):
                with self.subTest(path=path):
                    parsed = self.page(path)
                    post_forms = [form for form in parsed.forms if form.get("method") == "post"]
                    self.assertTrue(post_forms)
                    for form in post_forms:
                        self.assertEqual(len(form["tokens"]), 1)
                        self.assertEqual(form["tokens"][0]["type"], "hidden")
                        self.assertEqual(form["tokens"][0]["value"], parsed.token)

    def test_all_post_routes_reject_missing_or_invalid_tokens_before_side_effects(self):
        self.page("/transactions/new")
        adapter = self.app.url_map.bind("localhost")
        ids = {"tx_id": self.tx_id, "cat_id": self.cat_id, "fmt_id": self.fmt_id}
        with patch("threading.Thread.start") as start, patch("app.routes.paypay.import_csv") as import_csv:
            for rule in self.app.url_map.iter_rules():
                if "POST" not in rule.methods:
                    continue
                path = adapter.build(rule.endpoint, {arg: ids[arg] for arg in rule.arguments})
                for token in (None, "tampered-token"):
                    with self.subTest(path=path, token=token):
                        data = {} if token is None else {"csrf_token": token}
                        response = self.client.post(
                            path, data=data,
                            headers={**self.headers, "Accept": "application/json"},
                        )
                        self.assertEqual(response.status_code, 400)
                        self.assertIn("再読み込み", response.get_json()["error"])
            start.assert_not_called()
            import_csv.assert_not_called()
        with self.app.app_context():
            self.assertEqual(Transaction.query.count(), 1)
            self.assertEqual(db.session.get(Transaction, self.tx_id).amount, 500)
            self.assertEqual(Category.query.count(), 12)
            self.assertEqual(EmailFormat.query.count(), 1)
            self.assertIsNone(db.session.get(Setting, "scan_interval_minutes"))

    def test_delete_with_form_token_succeeds(self):
        path = f"/transactions/{self.tx_id}/delete"
        token = self.form_token("/transactions/?year=2026&month=10", action=path)
        response = self.client.post(path, data={"csrf_token": token}, headers=self.headers)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Transaction, self.tx_id))

    def test_bulk_delete_with_form_token_succeeds(self):
        path = "/transactions/bulk_delete"
        token = self.form_token("/transactions/?year=2026&month=10", action=path)
        response = self.client.post(
            path, data={"csrf_token": token, "date_from": "2026-10-12"}, headers=self.headers,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Transaction.query.count(), 0)

    def test_transaction_edit_with_form_token_succeeds(self):
        path = f"/transactions/{self.tx_id}/edit"
        response = self.client.post(path, headers=self.headers, data={
            "csrf_token": self.form_token(path), "date": "2026-10-12", "amount": "750",
            "type": "expense", "description": "変更後", "category_id": str(self.cat_id),
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Transaction, self.tx_id).amount, 750)

    def test_category_create_edit_delete_with_form_tokens_succeed(self):
        token = self.form_token("/categories/", action="/categories/new")
        response = self.client.post("/categories/new", headers=self.headers, data={
            "csrf_token": token, "name": "追加カテゴリ", "type": "expense", "color": "#4e79a7",
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            category_id = Category.query.filter_by(name="追加カテゴリ").one().id
        token = self.form_token("/categories/")
        response = self.client.post(f"/categories/{category_id}/edit", headers=self.headers, data={
            "csrf_token": token, "name": "変更カテゴリ", "type": "expense", "color": "#4e79a7",
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Category, category_id).name, "変更カテゴリ")
        path = f"/categories/{category_id}/delete"
        token = self.form_token("/categories/", action=path)
        response = self.client.post(path, headers=self.headers, data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Category, category_id))

    def test_email_format_delete_with_form_token_succeeds(self):
        path = f"/gmail/formats/{self.fmt_id}/delete"
        token = self.form_token("/gmail/", action=path)
        response = self.client.post(path, headers=self.headers, data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(EmailFormat, self.fmt_id))

    def test_json_setting_update_requires_valid_header_token(self):
        token = self.page("/gmail/").token
        for headers in (self.headers, {**self.headers, "X-CSRFToken": "invalid"}):
            response = self.client.post("/gmail/scan/interval", json={"minutes": 60}, headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertIn("再読み込み", response.get_json()["error"])
        response = self.client.post(
            "/gmail/scan/interval", json={"minutes": 60},
            headers={**self.headers, "X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.session.get(Setting, "scan_interval_minutes").value, "60")

    def test_preview_with_header_token_succeeds(self):
        token = self.page("/gmail/").token
        response = self.client.post(
            "/gmail/formats/preview", json={"body": "金額:1500円", "prefix": "金額:", "suffix": "円"},
            headers={**self.headers, "X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

    def test_scan_with_header_token_starts_background_job(self):
        token = self.page("/gmail/").token
        with patch("app.services.gmail_service.scan_emails", return_value={"started": True}) as scan:
            response = self.client.post(
                "/gmail/scan", data={"batch": "0"},
                headers={**self.headers, "Accept": "application/json", "X-CSRFToken": token},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["started"])
        scan.assert_called_once_with(batch=False, trigger="manual", background=True)

    def test_multipart_upload_with_hidden_token_imports_csv(self):
        token = self.form_token("/paypay/", action="/paypay/upload")
        csv = (
            "取引番号,取引内容,取引日,出金金額（円）,入金金額（円）,取引先\n"
            "CSRF1,支払い,2026/10/12,100,-,テスト店\n"
        )
        response = self.client.post("/paypay/upload", headers=self.headers, data={
            "csrf_token": token, "csv_file": (io.BytesIO(csv.encode("utf-8-sig")), "paypay.csv"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["added"], 1)
        with self.app.app_context():
            self.assertIsNotNone(Transaction.query.filter_by(external_id="paypay_CSRF1").first())

    def test_token_from_another_session_is_rejected(self):
        other_client = self.app.test_client()
        token = self.page("/transactions/new", client=other_client).token
        self.page("/transactions/new")
        response = self.client.post(
            f"/transactions/{self.tx_id}/delete", data={"csrf_token": token}, headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Transaction, self.tx_id))

    def test_expired_token_is_rejected_without_deleting(self):
        with patch("itsdangerous.TimestampSigner.get_timestamp", return_value=int(time.time()) - 7200):
            token = self.page("/transactions/new").token
        response = self.client.post(
            f"/transactions/{self.tx_id}/delete", data={"csrf_token": token}, headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("再読み込み", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Transaction, self.tx_id))

    def test_authentication_is_checked_before_csrf(self):
        response = self.client.post(f"/transactions/{self.tx_id}/delete")
        self.assertEqual(response.status_code, 401)
        self.assertIn("WWW-Authenticate", response.headers)

    def test_read_only_requests_work_without_a_token(self):
        for path in ("/ping", "/transactions/bulk_delete_count", "/gmail/scan/interval"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self.headers).status_code, 200)
