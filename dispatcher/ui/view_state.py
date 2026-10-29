"""Small, Qt-free persistence for the window's optional presentation state."""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

from .. import storage


_MAX_FILE_BYTES = 512 * 1024
_MAX_BINARY_BYTES = 64 * 1024
_MAX_BASE64_LENGTH = 4 * ((_MAX_BINARY_BYTES + 2) // 3)
_DEFAULT_STATE = {
    "version": 1,
    "geometry": "",
    "header": "",
    "splitter": "",
    "sort_enabled": False,
    "sort_column": 0,
    "sort_order": 0,
}


def _valid_base64(value: object) -> bool:
    if not isinstance(value, str) or len(value) > _MAX_BASE64_LENGTH:
        return False
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeError, binascii.Error, ValueError):
        return False
    return (
        len(decoded) <= _MAX_BINARY_BYTES
        and base64.b64encode(decoded).decode("ascii") == value
    )


def _normalise(raw: object) -> dict:
    result = _DEFAULT_STATE.copy()
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        return result
    for key in ("geometry", "header", "splitter"):
        value = raw.get(key)
        if _valid_base64(value):
            result[key] = value
    if type(raw.get("sort_enabled")) is bool:
        result["sort_enabled"] = raw["sort_enabled"]
    for key in ("sort_column", "sort_order"):
        if type(raw.get(key)) is int:
            result[key] = raw[key]
    return result


def load_view_state(path: Path | None = None) -> dict:
    """Return usable state without changing a missing, unreadable or bad file.

    Widget restore methods and sort ranges are checked by the Qt consumer.
    Reads are bounded before JSON decoding; unknown schema versions are ignored.
    """
    try:
        target = path if path is not None else storage.data_dir() / "ui_state.json"
        with target.open("rb") as stream:
            content = stream.read(_MAX_FILE_BYTES + 1)
        if len(content) > _MAX_FILE_BYTES:
            return _DEFAULT_STATE.copy()
        return _normalise(json.loads(content.decode("utf-8")))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return _DEFAULT_STATE.copy()


def save_view_state(state: dict, path: Path | None = None) -> None:
    """Atomically save only supported view fields; storage errors reach the caller."""
    target = path if path is not None else storage.data_dir() / "ui_state.json"
    raw = {"version": 1, **state}
    storage.atomic_json_write(target, _normalise(raw))
