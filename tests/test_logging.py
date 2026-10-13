import io
import logging
import sys
import unittest

from flask import Flask

from app.logging_utils import configure_logging, log_safe_exception


class SensitiveLoggingTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.root = logging.getLogger()
        self.root.addHandler(self.handler)
        self.addCleanup(self.root.removeHandler, self.handler)
        configure_logging(self.app)

    def test_library_messages_arguments_and_cached_tracebacks_are_hidden(self):
        secret = "private-token-and-mail-body"
        for name in ("google.auth.transport.requests", "google.oauth2._client",
                     "googleapiclient.http", "requests_oauthlib.oauth2_session",
                     "oauthlib.oauth2.rfc6749", "urllib3.connectionpool",
                     "sqlalchemy.engine.Engine", "sqlalchemy.pool.impl.QueuePool"):
            with self.subTest(logger=name):
                try:
                    raise RuntimeError(secret)
                except RuntimeError:
                    record = logging.getLogger(name).makeRecord(
                        name, logging.ERROR, __file__, 0,
                        "response=%s", (secret,), sys.exc_info(),
                        sinfo=secret,
                    )
                # 他のformatterが先にキャッシュした例外文字列も除去する。
                record.exc_text = secret
                self.handler.handle(record)
        output = self.stream.getvalue()
        self.assertNotIn(secret, output)
        self.assertNotIn("Traceback", output)
        self.assertIn("sqlalchemy.engine.Engine ERROR", output)

    def test_new_child_logger_is_redacted_when_propagating_to_root(self):
        logger = logging.getLogger("googleapiclient.new_test_child")
        logger.error("private-email-body")
        self.assertNotIn("private-email-body", self.stream.getvalue())
        self.assertIn("ライブラリログの詳細を非表示", self.stream.getvalue())

    def test_configuration_is_idempotent_and_application_logs_remain_useful(self):
        filters = list(self.handler.filters)
        configure_logging(self.app)
        self.assertEqual(self.handler.filters, filters)
        log_safe_exception(self.app.logger, "Gmailスキャンに失敗しました", RuntimeError("private-token"))
        output = self.stream.getvalue()
        self.assertIn("Gmailスキャンに失敗しました (error_type=RuntimeError)", output)
        self.assertNotIn("private-token", output)


if __name__ == "__main__":
    unittest.main()
