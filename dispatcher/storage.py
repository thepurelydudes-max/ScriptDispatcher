from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import AppConfig


APP_DIR_NAME = "data"


def application_root() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    result = application_root() / APP_DIR_NAME
    result.mkdir(parents=True, exist_ok=True)
    return result


def config_path() -> Path:
    return data_dir() / "config.json"


def logs_dir() -> Path:
    result = data_dir() / "logs"
    result.mkdir(parents=True, exist_ok=True)
    return result


def temp_dir() -> Path:
    result = data_dir() / "temp"
    result.mkdir(parents=True, exist_ok=True)
    return result


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_config(default_factory) -> AppConfig:
    path = config_path()
    defaults = default_factory()
    if not path.exists():
        config = defaults
        save_config(config)
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = AppConfig.from_dict(raw)
        changed = False
        for key, value in defaults.settings.items():
            if key not in config.settings:
                config.settings[key] = value
                changed = True
        obsolete_settings = (
            "telegram_chat_id",
            "telegram_max_retries",
            "telegram_delay",
            "telegram_album_pause",
        )
        for key in obsolete_settings:
            if key in config.settings:
                config.settings.pop(key, None)
                changed = True
        for task in config.tasks:
            if "telegram_enabled" in task.params:
                task.params.setdefault(
                    "internal_bot_enabled",
                    bool(task.params.get("telegram_enabled", True)),
                )
                task.params.pop("telegram_enabled", None)
                changed = True
        if changed:
            save_config(config)
        return config
    except Exception:
        broken = path.with_name(f"config.broken.{int(path.stat().st_mtime)}.json")
        path.replace(broken)
        config = defaults
        save_config(config)
        return config


def save_config(config: AppConfig) -> None:
    atomic_json_write(config_path(), config.to_dict())
