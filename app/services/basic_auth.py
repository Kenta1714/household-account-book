import hmac
import math
import threading
import time
from collections import deque


class BasicAuthGuard:
    """単一アカウントの認証失敗を、アプリ全体でスレッドセーフに制限する。"""

    def __init__(self, username, password, *, max_failures, window_seconds, lockout_seconds):
        self._username = username.encode()
        self._password = password.encode()
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._lockout_seconds = lockout_seconds
        self._failures = deque(maxlen=max_failures)
        self._locked_until = 0
        self._lock = threading.Lock()

    def authenticate(self, auth, *, attempted):
        """(認証成功, 再試行までの秒数)を返す。制限中は認証情報を検証しない。"""
        # 判定・認証・失敗記録を一緒にロックし、同時リクエストによる突破を防ぐ。
        with self._lock:
            now = time.monotonic()
            if now < self._locked_until:
                return False, math.ceil(self._locked_until - now)
            if self._locked_until:
                self._failures.clear()
                self._locked_until = 0
            while self._failures and self._failures[0] <= now - self._window_seconds:
                self._failures.popleft()

            valid_username = valid_password = False
            if auth is not None and auth.type == "basic":
                valid_username = hmac.compare_digest(
                    (auth.username or "").encode(), self._username,
                )
                valid_password = hmac.compare_digest(
                    (auth.password or "").encode(), self._password,
                )
            if valid_username and valid_password:
                # Basic認証は毎回送られるため、成功で他の失敗履歴を消さない。
                return True, None

            # 初回のBasic認証チャレンジは失敗に含めない。不正なヘッダーは数える。
            if attempted:
                self._failures.append(now)
                if len(self._failures) >= self._max_failures:
                    self._locked_until = now + self._lockout_seconds
                    return False, self._lockout_seconds
            return False, None
