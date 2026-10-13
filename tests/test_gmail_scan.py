import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from flask import Flask, current_app
from sqlalchemy.exc import StatementError

from app.routes.gmail import bp
from app.services import gmail_service as gs


RESULT = {"total_found": 3, "added": 2, "skipped": 1}
SECRETS = ("private-access-token", "private-refresh-token", "private-client-secret",
           "非公開のメール本文")


def sensitive_error():
    return StatementError(
        "database failed", "INSERT INTO setting (value) VALUES (?)", SECRETS,
        RuntimeError(" ".join(SECRETS)),
    )


class GmailScanTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        progress = patch.dict(gs._scan_progress, {
            "running": False, "batch": False, "total_found": 0,
            "added": 0, "done": False, "error": None, "result": None,
        }, clear=True)
        progress.start()
        self.addCleanup(progress.stop)

    def test_completed_scan_releases_reservation_and_records_result(self):
        with patch.object(gs, "_do_scan", return_value=RESULT) as scan:
            self.assertEqual(gs.scan_emails(batch=True, trigger="manual"), RESULT)
        scan.assert_called_once_with(True, trigger="manual")
        self.assertEqual(gs.get_scan_progress(), {
            "running": False, "batch": True, "total_found": 3,
            "added": 2, "done": True, "error": None, "result": RESULT,
        })

    def test_failed_scan_releases_reservation_and_allows_retry(self):
        with patch.object(gs, "_do_scan", side_effect=RuntimeError("scan failed")):
            with self.assertRaisesRegex(RuntimeError, "scan failed"):
                gs.scan_emails()
        progress = gs.get_scan_progress()
        self.assertFalse(progress["running"])
        self.assertTrue(progress["done"])
        self.assertEqual(progress["error"], gs.SCAN_ERROR_MESSAGE)
        with patch.object(gs, "_do_scan", return_value=RESULT):
            self.assertEqual(gs.scan_emails(), RESULT)
        self.assertIsNone(gs.get_scan_progress()["error"])

    def test_background_scan_reserves_before_thread_runs_and_uses_app_context(self):
        def do_scan(batch, trigger):
            self.assertIs(current_app._get_current_object(), self.app)
            return RESULT

        with patch.object(gs.threading, "Thread") as thread, \
                patch.object(gs, "_do_scan", side_effect=do_scan) as scan:
            with self.app.app_context():
                self.assertEqual(
                    gs.scan_emails(batch=True, trigger="manual", background=True),
                    {"started": True},
                )
                reserved = gs.get_scan_progress()
                self.assertTrue(reserved["running"])
                self.assertIsNone(gs.scan_emails(background=True))
            self.assertIsNone(gs.scan_emails())
            self.assertEqual(gs.get_scan_progress(), reserved)
            scan.assert_not_called()
            thread.return_value.start.assert_called_once()
            # HTTP応答後にスレッドが動き始める状況を再現する。
            kwargs = thread.call_args.kwargs
            kwargs["target"](**kwargs["kwargs"])
        scan.assert_called_once_with(True, trigger="manual")
        self.assertFalse(gs.get_scan_progress()["running"])
        self.assertEqual(gs.get_scan_progress()["result"], RESULT)

    def test_background_scan_failure_releases_reservation(self):
        with patch.object(gs.threading, "Thread") as thread:
            with self.app.app_context():
                gs.scan_emails(trigger="manual", background=True)
            kwargs = thread.call_args.kwargs
        with patch.object(gs, "_do_scan", side_effect=sensitive_error()), \
                self.assertLogs(self.app.logger, level="ERROR") as logs:
            kwargs["target"](**kwargs["kwargs"])
        self.assertFalse(gs.get_scan_progress()["running"])
        progress = self.app.test_client().get("/gmail/scan/progress")
        self.assertEqual(progress.json["error"], gs.SCAN_ERROR_MESSAGE)
        output = str(progress.json) + "\n".join(logs.output)
        for secret in (*SECRETS, "INSERT INTO", "Traceback"):
            self.assertNotIn(secret, output)
        self.assertIn("StatementError", "\n".join(logs.output))
        self.assertFalse(logs.records[0].exc_info)
        with patch.object(gs, "_do_scan", return_value=RESULT):
            self.assertEqual(gs.scan_emails(), RESULT)

    def test_thread_creation_or_start_failure_releases_reservation(self):
        for stage in ("create", "start"):
            with self.subTest(stage=stage), patch.object(gs.threading, "Thread") as thread:
                if stage == "create":
                    thread.side_effect = RuntimeError("thread failed")
                else:
                    thread.return_value.start.side_effect = RuntimeError("thread failed")
                with self.app.app_context(), self.assertRaisesRegex(RuntimeError, "thread failed"):
                    gs.scan_emails(background=True)
            self.assertFalse(gs.get_scan_progress()["running"])
            self.assertEqual(gs.get_scan_progress()["error"], gs.SCAN_ERROR_MESSAGE)
            with patch.object(gs, "_do_scan", return_value=RESULT):
                self.assertEqual(gs.scan_emails(), RESULT)

    def test_simultaneous_manual_and_auto_calls_run_only_one_scan(self):
        count = 8
        barrier = threading.Barrier(count + 1)
        entered = threading.Event()
        release = threading.Event()
        all_rejected = threading.Event()
        rejected_lock = threading.Lock()
        rejected = 0

        def do_scan(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release scan")
            return RESULT

        def request_scan(index):
            nonlocal rejected
            barrier.wait(timeout=5)
            result = gs.scan_emails(
                batch=bool(index % 2), trigger="manual" if index % 2 else "auto",
            )
            if result is None:
                with rejected_lock:
                    rejected += 1
                    if rejected == count - 1:
                        all_rejected.set()
            return result

        with patch.object(gs, "_do_scan", side_effect=do_scan) as scan, \
                ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(request_scan, index) for index in range(count)]
            try:
                barrier.wait(timeout=5)
                self.assertTrue(entered.wait(timeout=5))
                self.assertTrue(all_rejected.wait(timeout=5))
                self.assertTrue(gs.get_scan_progress()["running"])
                scan.assert_called_once()
            finally:
                release.set()
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(results.count(None), count - 1)
        self.assertEqual(results.count(RESULT), 1)
        self.assertFalse(gs.get_scan_progress()["running"])

    def test_manual_requests_are_rejected_until_reserved_scan_finishes(self):
        client = self.app.test_client()
        with patch.object(gs.threading, "Thread") as thread, \
                patch.object(gs, "_do_scan", return_value=RESULT) as scan:
            first = client.post("/gmail/scan", data={"batch": "1"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.get_json(), {"started": True})
            reserved = client.get("/gmail/scan/progress").get_json()
            self.assertTrue(reserved["running"])
            self.assertTrue(reserved["batch"])
            for batch in ("0", "1"):
                response = client.post("/gmail/scan", data={"batch": batch})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json(), {"error": "スキャン中です"})
            self.assertIsNone(gs.scan_emails(trigger="auto"))
            self.assertEqual(gs.get_scan_progress(), reserved)
            thread.return_value.start.assert_called_once()
            scan.assert_not_called()
            kwargs = thread.call_args.kwargs
            kwargs["target"](**kwargs["kwargs"])
            self.assertFalse(gs.get_scan_progress()["running"])
            retry = client.post("/gmail/scan")
            self.assertEqual(retry.status_code, 200)
            kwargs = thread.call_args.kwargs
            kwargs["target"](**kwargs["kwargs"])

    def test_manual_request_during_auto_scan_does_not_change_progress(self):
        def do_scan(batch, trigger):
            gs._update_progress(total_found=5, added=1)
            reserved = gs.get_scan_progress()
            response = self.app.test_client().post("/gmail/scan", data={"batch": "1"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(gs.get_scan_progress(), reserved)
            return RESULT

        with patch.object(gs, "_do_scan", side_effect=do_scan) as scan, \
                patch.object(gs.threading, "Thread") as thread:
            self.assertEqual(gs.scan_emails(trigger="auto"), RESULT)
        scan.assert_called_once()
        thread.assert_not_called()

    def test_manual_thread_start_failure_returns_json_and_allows_retry(self):
        client = self.app.test_client()
        with patch.object(gs.threading, "Thread") as thread:
            thread.return_value.start.side_effect = sensitive_error()
            with self.assertLogs(self.app.logger, level="ERROR") as logs:
                response = client.post("/gmail/scan")
        self.assertEqual(response.status_code, 500)
        self.assertIn("スキャンを開始できませんでした", response.get_json()["error"])
        self.assertFalse(gs.get_scan_progress()["running"])
        progress = client.get("/gmail/scan/progress")
        self.assertEqual(progress.json["error"], gs.SCAN_ERROR_MESSAGE)
        output = str(response.json) + str(progress.json) + "\n".join(logs.output)
        for secret in (*SECRETS, "INSERT INTO", "Traceback"):
            self.assertNotIn(secret, output)
        self.assertFalse(logs.records[0].exc_info)
        with patch.object(gs, "_do_scan", return_value=RESULT):
            self.assertEqual(gs.scan_emails(), RESULT)

    def test_real_background_thread_does_not_pass_failure_to_excepthook(self):
        threads = []
        thread_type = threading.Thread

        def make_thread(**kwargs):
            thread = thread_type(**kwargs)
            threads.append(thread)
            return thread

        with patch.object(gs.threading, "Thread", side_effect=make_thread), \
                patch.object(gs, "_do_scan", side_effect=sensitive_error()), \
                patch.object(threading, "excepthook") as hook, \
                self.assertLogs(self.app.logger, level="ERROR") as logs:
            response = self.app.test_client().post("/gmail/scan")
            self.assertEqual(response.status_code, 200)
            threads[0].join(timeout=5)
            self.assertFalse(threads[0].is_alive())
        hook.assert_not_called()
        self.assertEqual(gs.get_scan_progress()["error"], gs.SCAN_ERROR_MESSAGE)
        for secret in (*SECRETS, "INSERT INTO", "Traceback"):
            self.assertNotIn(secret, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
