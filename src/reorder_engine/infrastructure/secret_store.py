from __future__ import annotations

import threading
import json
from pathlib import Path
import os
import tempfile

from reorder_engine.application.errors import EngineError


def _password_values(values: list[str]) -> tuple[str, ...]:
    # One UTF-8 line per archive password. Preserve spaces, duplicates and #.
    if len(values) > 10000 or any(not isinstance(x, str) or len(x) > 4096 or
                                 any(c in x for c in "\x00\r\n") for x in values):
        raise EngineError("INVALID_PASSWORDS", "密码需每行一个，最多 10000 条，每条不超过 4096 字符。")
    passwords = tuple(x for x in values if x != "")
    # The desktop bridge has a 1 MiB frame limit. Escaped control characters can
    # expand beyond the UTF-8 file size; leave room for the settings DTO envelope.
    try:
        encoded_size = len(json.dumps(passwords, ensure_ascii=False).encode("utf-8"))
    except UnicodeError as exc:
        raise EngineError("INVALID_PASSWORDS", "密码需要有效的 UTF-8 文字。") from exc
    if encoded_size > 800 * 1024:
        raise EngineError("INVALID_PASSWORDS", "密码列表过大，请缩小到可在界面中编辑的范围。")
    return passwords


def parse_password_text(text: str) -> tuple[str, ...]:
    # Only CRLF/LF are record separators; preserve other Unicode characters.
    return _password_values(text.replace("\r\n", "\n").split("\n"))


class SecretStore:
    """Legacy in-memory test adapter; archive passwords are public plain text.

    Desktop production uses PasswordFile below. The legacy name and constructor
    remain compatible with injected adapters; no credential backend is used.
    """

    def __init__(self, *, persistent: bool = True):
        self._lock = threading.RLock()
        self._passwords: tuple[str, ...] = ()
        self.mode = "session"

    def load(self) -> tuple[str, ...]:
        with self._lock:
            return self._passwords

    def set_extra_secrets(self, values) -> None:
        pass  # Compatibility only: public archive passwords are never masked.

    def replace(self, values: list[str]) -> dict:
        with self._lock:
            self._passwords = _password_values(values)
            return self.info()

    def info(self) -> dict:
        values = self.load()
        return {"count": len(values), "storage": self.mode, "path": "", "values": list(values)}

    def redact(self, text: str) -> str:
        return text


class PasswordFile(SecretStore):
    """A single editable UTF-8 library, seeded only when it does not exist.

    Read from disk on every load so external edits take effect. An empty file is
    an intentional empty library. No default values are appended after seeding.
    """

    MAX_BYTES = 512 * 1024

    def __init__(self, path: Path, *, initial: tuple[str, ...] = ()):
        super().__init__(persistent=False)
        self.path = path
        self.mode = "plaintext"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._check_path()
        seed = self._encode(list(initial))
        try:
            with self.path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(seed)
        except FileExistsError:
            pass  # Keep all existing user edits, including an empty library.
        except OSError as exc:
            raise EngineError("PASSWORD_FILE_FAILED", "无法创建密码词库文件，请检查数据目录权限。") from exc

    def _check_path(self) -> None:
        is_junction = getattr(self.path, "is_junction", lambda: False)
        if self.path.is_symlink() or is_junction() or (self.path.exists() and not self.path.is_file()):
            raise EngineError("INVALID_PASSWORD_FILE", "密码词库必须是普通 UTF-8 文件。")

    def _encode(self, values: list[str]) -> str:
        values = _password_values(values)
        text = "\n".join(values) + ("\n" if values else "")
        if len(text.encode("utf-8")) > self.MAX_BYTES:
            raise EngineError("INVALID_PASSWORD_FILE", "密码词库不能超过 512 KiB。")
        return text

    def load(self) -> tuple[str, ...]:
        with self._lock:
            self._check_path()
            try:
                with self.path.open("rb") as stream:
                    data = stream.read(self.MAX_BYTES + 1)
                if len(data) > self.MAX_BYTES:
                    raise EngineError("INVALID_PASSWORD_FILE", "密码词库不能超过 512 KiB。")
                return parse_password_text(data.decode("utf-8-sig"))
            except (OSError, UnicodeError) as exc:
                raise EngineError("PASSWORD_FILE_FAILED", "无法读取密码词库，请检查文件及 UTF-8 编码。") from exc

    def replace(self, values: list[str]) -> dict:
        text = self._encode(values)
        with self._lock:
            self._check_path()
            temporary = None
            try:
                fd, name = tempfile.mkstemp(prefix=".passwords-", suffix=".tmp", dir=self.path.parent)
                temporary = Path(name)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._check_path()
                os.replace(temporary, self.path)
            except OSError as exc:
                raise EngineError("PASSWORD_FILE_FAILED", "密码词库保存失败，原文件未替换。") from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return self.info()

    def info(self) -> dict:
        result = super().info()
        result["path"] = str(self.path)
        return result
