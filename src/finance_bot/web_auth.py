# 로컬 암호 해시와 만료되는 웹 세션을 안전하게 관리합니다.

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path


class LoginError(ValueError):
    pass


class WebAuth:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._sessions: dict[str, tuple[str, float]] = {}
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    @property
    def needs_setup(self) -> bool:
        return not self.path.exists()

    def login(self, password: str, *, setup: bool = False) -> tuple[str, str]:
        if not isinstance(password, str) or len(password) > 128:
            raise LoginError("请输入有效密码。")
        with self._lock:
            now = time.monotonic()
            while self._failures and self._failures[0] < now - 60:
                self._failures.popleft()
            if len(self._failures) >= 5:
                raise LoginError("尝试次数过多，请一分钟后再试。")
            if setup:
                if not self.needs_setup:
                    raise LoginError("密码已经设置，请直接登录。")
                if len(password) < 12:
                    raise LoginError("密码至少需要 12 个字符。")
                salt = secrets.token_hex(16)
                record = {"salt": salt, "iterations": 600_000, "hash": self._hash(password, salt, 600_000)}
                # Exclusive create prevents a second request from replacing the password.
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    json.dump(record, handle)
            else:
                if self.needs_setup:
                    raise LoginError("请先设置本机页面密码。")
                record = json.loads(self.path.read_text())
                candidate = self._hash(password, record["salt"], record["iterations"])
                if not hmac.compare_digest(candidate, record["hash"]):
                    self._failures.append(now)
                    raise LoginError("密码不正确，请重试。")
            self._failures.clear()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}
            if len(self._sessions) >= 64:
                self._sessions.pop(next(iter(self._sessions)))
            self._sessions[token] = (csrf, now + 12 * 60 * 60)
            return token, csrf

    def session(self, token: str) -> str | None:
        with self._lock:
            value = self._sessions.get(token)
            if value and value[1] > time.monotonic():
                return value[0]
            self._sessions.pop(token, None)
            return None

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    @staticmethod
    def _hash(password: str, salt: str, iterations: int) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
