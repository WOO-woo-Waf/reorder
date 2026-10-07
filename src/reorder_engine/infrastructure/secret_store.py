from __future__ import annotations

import json
import threading

from reorder_engine.application.errors import EngineError


class SecretStore:
    """Use OS credentials when available; never fall back to plaintext files."""

    SERVICE = "reorder-engine-desktop"
    ACCOUNT = "archive-passwords"

    def __init__(self, *, persistent: bool = True):
        self._lock = threading.RLock()
        self._passwords: tuple[str, ...] = ()
        self.mode = "session"
        self._backend = None
        if not persistent:
            return
        try:
            import keyring
            backend = keyring.get_keyring()
            allowed = {"keyring.backends.Windows", "keyring.backends.macOS",
                       "keyring.backends.SecretService", "keyring.backends.libsecret"}
            if backend.priority > 0 and type(backend).__module__ in allowed:
                raw = backend.get_password(self.SERVICE, self.ACCOUNT)
                if raw is not None:
                    values = json.loads(raw)
                    if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
                        raise ValueError("Invalid password store")
                    self._passwords = tuple(values)
                self._backend = backend
                self.mode = "system"
        except Exception:
            self._backend = None

    def load(self) -> tuple[str, ...]:
        with self._lock:
            return self._passwords

    def replace(self, values: list[str]) -> dict:
        passwords = tuple(dict.fromkeys(x for x in values if x))
        if any(len(x) > 4096 or "\x00" in x for x in passwords):
            raise EngineError("INVALID_PASSWORDS", "密码长度或格式不合法。")
        with self._lock:
            if self._backend:
                try:
                    self._backend.set_password(self.SERVICE, self.ACCOUNT, json.dumps(passwords, ensure_ascii=False))
                except Exception as exc:
                    raise EngineError("SECRET_STORE_FAILED", "系统凭据保存失败；原密码集未替换。") from exc
            self._passwords = passwords
            return {"count": len(passwords), "storage": self.mode}

    def redact(self, text: str) -> str:
        for password in sorted(self.load(), key=len, reverse=True):
            text = text.replace(password, "[密码已隐藏]")
        return text
