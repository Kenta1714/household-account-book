import base64
import os
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flask import Flask
from sqlalchemy import event

from app.extensions import db
from app.models import EmailFormat, ScanLog, ScanSkipDetail, Transaction
from app.routes.gmail import bp
from app.services import gmail_service as gs
from tests.helpers import migrate_database


STARTED_AT = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)


class GmailImportTests(unittest.TestCase):
    def setUp(self):
        # 実際の設定・認証情報・家計簿DBを使わず、一時DBと疑似Gmailで検証する。
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        migrate_database(self.app)
        # Alembicのログ設定で、前のテストが作成したロガーが無効化される場合がある。
        self.app.logger.disabled = False
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self._dispose_app)
        db.session.add(EmailFormat(
            company_name="テストカード", sender_email="notice@example.com",
            amount_prefix="ご利用金額:", amount_suffix="円",
        ))
        db.session.commit()

        self.service = MagicMock()
        self.messages = self.service.users.return_value.messages.return_value
        service_patch = patch.object(gs, "get_gmail_service", return_value=self.service)
        service_patch.start()
        self.addCleanup(service_patch.stop)
        clock_patch = patch.object(gs, "datetime", wraps=datetime)
        self.clock = clock_patch.start()
        self.clock.now.return_value = STARTED_AT
        self.addCleanup(clock_patch.stop)
        progress_patch = patch.dict(gs._scan_progress, {
            "running": False, "batch": False, "total_found": 0,
            "added": 0, "done": False, "error": None, "result": None,
        }, clear=True)
        progress_patch.start()
        self.addCleanup(progress_patch.stop)

    def _dispose_app(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def _set_messages(self, dates):
        self.messages.list.return_value.execute.side_effect = None
        self.messages.list.return_value.execute.return_value = {
            "messages": [{"id": msg_id} for msg_id in dates],
        }

        def get_message(*, userId, id, format):
            headers = [
                {"name": "From", "value": "notice@example.com"},
                {"name": "Subject", "value": "カード利用通知"},
            ]
            if dates[id] is not None:
                headers.append({"name": "Date", "value": dates[id]})
            request = MagicMock()
            request.execute.return_value = {"payload": {
                "headers": headers,
                "body": {"data": base64.urlsafe_b64encode(
                    "ご利用金額:1,500円".encode()
                ).decode()},
            }}
            return request

        self.messages.get.side_effect = get_message

    def _previous_scan(self, scanned_at):
        log = ScanLog(source="gmail", scanned_at=scanned_at)
        db.session.add(log)
        db.session.commit()
        return log.id

    def test_valid_mail_dates_keep_the_date_in_the_mail_timezone(self):
        self._set_messages({
            "single_digit": "Mon, 5 Oct 2026 12:34:56 +0900",
            "double_digit": "Mon, 05 Oct 2026 12:34:56 +0900",
            "no_weekday": "5 Oct 2026 12:34:56 GMT",
            "unknown_timezone": "Mon, 5 Oct 2026 12:34:56 -0000",
            "different_timezone": "Sun, 11 Oct 2026 23:30:00 -0500",
        })
        result = gs.scan_emails(batch=True)
        self.assertEqual(result["added"], 5)
        self.assertEqual(result["skipped"], 0)
        for msg_id in ("single_digit", "double_digit", "no_weekday", "unknown_timezone"):
            tx = Transaction.query.filter_by(external_id=f"gmail_{msg_id}").one()
            self.assertEqual(tx.date, date(2026, 10, 5))
            self.assertEqual(tx.amount, 1500)
        tx = Transaction.query.filter_by(external_id="gmail_different_timezone").one()
        self.assertEqual(tx.date, date(2026, 10, 11))

    def test_invalid_or_missing_dates_are_recorded_and_other_mail_is_imported(self):
        self._set_messages({
            "missing": None,
            "empty": "",
            "malformed": "not a date",
            "invalid_day": "Tue, 31 Feb 2026 12:34:56 +0900",
            "invalid_hour": "Mon, 5 Oct 2026 25:34:56 +0900",
            "invalid_timezone": "Mon, 5 Oct 2026 12:34:56 +2500",
            "valid": "Mon, 5 Oct 2026 12:34:56 +0900",
        })
        with self.assertLogs(self.app.logger, level="WARNING") as logs:
            result = gs.scan_emails(batch=True, trigger="manual")
        self.assertEqual(result["total_found"], 7)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["skipped"], 6)
        self.assertEqual(result["skip_reasons"]["invalid_date"], 6)
        self.assertEqual(len(logs.output), 6)
        self.assertEqual(Transaction.query.one().date, date(2026, 10, 5))
        self.assertIn("日付解析失敗:6", ScanLog.query.one().detail)
        details = ScanSkipDetail.query.filter_by(reason="invalid_date").all()
        self.assertEqual(len(details), 6)
        self.assertTrue(all(d.extra_info for d in details))
        response = self.app.test_client().get("/gmail/scan/skip_details")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(d["reason_label"] == "日付解析失敗" for d in response.json))
        self.assertIn("not a date", [d["date_str"] for d in response.json])

    def test_log_uses_scan_start_even_when_processing_takes_time(self):
        self._set_messages({"valid": "Mon, 5 Oct 2026 12:34:56 +0900"})
        get_message = self.messages.get.side_effect

        def slow_get(**kwargs):
            self.clock.now.return_value = STARTED_AT + timedelta(minutes=20)
            return get_message(**kwargs)

        self.messages.get.side_effect = slow_get
        gs.scan_emails()
        self.assertEqual(ScanLog.query.one().scanned_at, STARTED_AT.replace(tzinfo=None))

    def test_incremental_scan_overlaps_by_five_minutes_and_ignores_paypay_logs(self):
        previous_start = STARTED_AT - timedelta(hours=1)
        self._previous_scan(previous_start.replace(tzinfo=None))
        db.session.add(ScanLog(source="paypay", scanned_at=STARTED_AT.replace(tzinfo=None)))
        db.session.commit()
        self._set_messages({})
        gs.scan_emails()
        cutoff = int((previous_start - timedelta(minutes=5)).timestamp())
        self.messages.list.assert_called_once_with(
            userId="me", q=f"after:{cutoff} (from:notice@example.com)", maxResults=500,
        )

    @unittest.skipUnless(hasattr(time, "tzset"), "tzsetが必要")
    def test_search_cutoff_is_utc_even_on_a_japanese_timezone_server(self):
        self._set_messages({})
        try:
            with patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
                time.tzset()
                gs.scan_emails()
                first_cutoff = int((STARTED_AT - timedelta(days=90)).timestamp())
                self.assertEqual(
                    self.messages.list.call_args.kwargs["q"],
                    f"after:{first_cutoff} (from:notice@example.com)",
                )
                gs.scan_emails()
                next_cutoff = int((STARTED_AT - timedelta(minutes=5)).timestamp())
                self.assertEqual(
                    self.messages.list.call_args.kwargs["q"],
                    f"after:{next_cutoff} (from:notice@example.com)",
                )
        finally:
            time.tzset()

    def test_mail_arriving_during_scan_is_imported_next_time_without_duplicates(self):
        self._set_messages({"existing": "Mon, 5 Oct 2026 12:34:56 +0900"})

        def slow_search():
            self.clock.now.return_value = STARTED_AT + timedelta(minutes=20)
            return {"messages": [{"id": "existing"}]}

        self.messages.list.return_value.execute.side_effect = slow_search
        gs.scan_emails()
        self.clock.now.return_value = STARTED_AT + timedelta(minutes=30)
        self._set_messages({
            "existing": "Mon, 5 Oct 2026 12:34:56 +0900",
            "arrived_during_scan": "Mon, 12 Oct 2026 10:10:00 +0000",
        })
        result = gs.scan_emails()
        cutoff = int((STARTED_AT - timedelta(minutes=5)).timestamp())
        self.assertEqual(
            self.messages.list.call_args.kwargs["q"],
            f"after:{cutoff} (from:notice@example.com)",
        )
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["skip_reasons"]["duplicate"], 1)
        self.assertEqual(Transaction.query.count(), 2)
        self.assertEqual(self.messages.get.call_count, 2)

    def test_full_scan_has_no_date_filter_and_keeps_duplicate_detection(self):
        self._previous_scan(STARTED_AT.replace(tzinfo=None))
        self._set_messages({"old": "Mon, 5 Oct 2020 12:34:56 +0900"})
        gs.scan_emails(batch=True)
        result = gs.scan_emails(batch=True)
        self.assertEqual(self.messages.list.call_args.kwargs["q"], "from:notice@example.com")
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["skip_reasons"]["duplicate"], 1)
        self.assertEqual(Transaction.query.count(), 1)

    def test_failed_pagination_keeps_previous_checkpoint_for_retry(self):
        previous_start = STARTED_AT - timedelta(hours=1)
        previous_id = self._previous_scan(previous_start.replace(tzinfo=None))
        self._set_messages({
            "first": "Mon, 5 Oct 2026 12:34:56 +0900",
            "second": "Tue, 6 Oct 2026 12:34:56 +0900",
        })
        self.messages.list.return_value.execute.side_effect = [
            {"messages": [{"id": "first"}], "nextPageToken": "page-2"},
            RuntimeError("Gmail通信失敗"),
        ]
        with self.assertRaisesRegex(RuntimeError, "Gmail通信失敗"):
            gs.scan_emails()
        self.assertEqual(Transaction.query.count(), 0)
        self.assertEqual(ScanLog.query.one().id, previous_id)
        self.assertEqual(ScanLog.query.one().scanned_at, previous_start.replace(tzinfo=None))

        self.messages.list.return_value.execute.side_effect = [
            {"messages": [{"id": "first"}], "nextPageToken": "page-2"},
            {"messages": [{"id": "second"}]},
        ]
        result = gs.scan_emails()
        self.assertEqual(result["added"], 2)
        self.assertEqual(Transaction.query.count(), 2)
        self.assertEqual(self.messages.list.call_args.kwargs["pageToken"], "page-2")
        cutoff = int((previous_start - timedelta(minutes=5)).timestamp())
        self.assertEqual(
            self.messages.list.call_args.kwargs["q"],
            f"after:{cutoff} (from:notice@example.com)",
        )

    def test_no_formats_does_not_advance_checkpoint(self):
        EmailFormat.query.delete()
        db.session.commit()
        result = gs.scan_emails()
        self.assertEqual(result["total_found"], 0)
        self.assertEqual(result["skip_reasons"]["invalid_date"], 0)
        self.assertEqual(ScanLog.query.count(), 0)
        self.messages.list.assert_not_called()

    def test_log_save_failure_rolls_back_transactions_and_allows_retry(self):
        previous_id = self._previous_scan((STARTED_AT - timedelta(hours=1)).replace(tzinfo=None))
        self._set_messages({"valid": "Mon, 5 Oct 2026 12:34:56 +0900"})

        def fail_log_insert(mapper, connection, target):
            raise RuntimeError("ログ保存失敗")

        event.listen(ScanLog, "before_insert", fail_log_insert)
        try:
            with self.assertRaisesRegex(RuntimeError, "ログ保存失敗"):
                gs.scan_emails()
        finally:
            event.remove(ScanLog, "before_insert", fail_log_insert)
        self.assertEqual(ScanLog.query.one().id, previous_id)
        self.assertEqual(Transaction.query.count(), 0)
        result = gs.scan_emails()
        self.assertEqual(result["added"], 1)
        self.assertEqual(Transaction.query.count(), 1)

    def test_detail_save_failure_restores_previous_details_and_transactions(self):
        previous_id = self._previous_scan((STARTED_AT - timedelta(hours=1)).replace(tzinfo=None))
        db.session.add(ScanSkipDetail(scan_log_id=previous_id, reason="no_amount"))
        db.session.commit()
        self._set_messages({
            "valid": "Mon, 5 Oct 2026 12:34:56 +0900",
            "invalid": "not a date",
        })

        def fail_detail_insert(mapper, connection, target):
            raise RuntimeError("詳細保存失敗")

        event.listen(ScanSkipDetail, "before_insert", fail_detail_insert)
        try:
            with self.assertRaisesRegex(RuntimeError, "詳細保存失敗"):
                gs.scan_emails()
        finally:
            event.remove(ScanSkipDetail, "before_insert", fail_detail_insert)
        self.assertEqual(Transaction.query.count(), 0)
        self.assertEqual(ScanLog.query.one().id, previous_id)
        self.assertEqual(ScanSkipDetail.query.one().reason, "no_amount")


if __name__ == "__main__":
    unittest.main()
