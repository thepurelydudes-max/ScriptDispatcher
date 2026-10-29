from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

from .private_defaults import DEFAULT_COOKIE_FILE, DEFAULT_INTERNAL_BOT_KEY
from .storage import application_root, atomic_json_write, data_dir


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    in_blob, keepalive = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del keepalive


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob, keepalive = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del keepalive


class SecretStore:
    """Хранит секреты в DPAPI-контейнере текущего пользователя Windows."""

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "secrets.dat")
        self._values: dict[str, str] = {}
        self.load()
        self.bootstrap_defaults()

    def bootstrap_defaults(self) -> None:
        changed = False
        if "telegram_token" in self._values:
            self._values.pop("telegram_token", None)
            changed = True
        if not self._values.get("internal_bot_key"):
            self._values["internal_bot_key"] = DEFAULT_INTERNAL_BOT_KEY
            changed = True
        if not self._values.get("yandex_cookie"):
            cookie_path = application_root() / DEFAULT_COOKIE_FILE
            if cookie_path.exists():
                cookie = cookie_path.read_text(encoding="utf-8-sig").strip()
                if cookie:
                    self._values["yandex_cookie"] = cookie
                    changed = True
        if changed:
            self.save()

    def load(self) -> None:
        if not self.path.exists():
            self._values = {}
            return
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            payload = base64.b64decode(envelope["payload"])
            if sys.platform == "win32":
                payload = _dpapi_unprotect(payload)
            self._values = json.loads(payload.decode("utf-8"))
        except Exception:
            self._values = {}

    def save(self) -> None:
        payload = json.dumps(self._values, ensure_ascii=False).encode("utf-8")
        mode = "plain-local"
        if sys.platform == "win32":
            payload = _dpapi_protect(payload)
            mode = "windows-dpapi"
        atomic_json_write(
            self.path,
            {"mode": mode, "payload": base64.b64encode(payload).decode("ascii")},
        )
        if sys.platform != "win32":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._values[key] = value
        self.save()
