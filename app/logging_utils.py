import logging


_SENSITIVE_LOGGERS = (
    "google.auth", "google.oauth2", "google_auth_httplib2",
    "google_auth_oauthlib", "googleapiclient", "httplib2",
    "oauthlib", "requests_oauthlib", "urllib3", "sqlalchemy",
)


def _is_sensitive_logger(name):
    return any(name == prefix or name.startswith(prefix + ".")
               for prefix in _SENSITIVE_LOGGERS)


class SensitiveLibraryLogFilter(logging.Filter):
    """ライブラリの任意の本文・引数・例外を出力前に除去する。"""

    def filter(self, record):
        if _is_sensitive_logger(record.name):
            record.msg = "ライブラリログの詳細を非表示にしました"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def configure_logging(app):
    # 親loggerのfilterは子へ継承されないため、出力handlerにも設定する。
    loggers = [logging.getLogger(), app.logger]
    loggers.extend(
        logger for name, logger in logging.Logger.manager.loggerDict.items()
        if _is_sensitive_logger(name) and isinstance(logger, logging.Logger)
    )
    for logger in loggers:
        for handler in logger.handlers:
            if not any(isinstance(f, SensitiveLibraryLogFilter) for f in handler.filters):
                handler.addFilter(SensitiveLibraryLogFilter())


def log_safe_exception(logger, message, exc):
    """固定の処理名と例外型だけを記録し、例外本文・tracebackを渡さない。"""
    logger.error("%s (error_type=%s)", message, type(exc).__name__, exc_info=False)
