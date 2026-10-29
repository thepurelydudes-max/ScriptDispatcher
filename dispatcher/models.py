from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ScheduleConfig:
    mode: str = "manual"  # manual | times | interval
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    times: list[str] = field(default_factory=list)
    interval_minutes: int = 60
    window_start: str = "08:00"
    window_end: str = "19:00"
    catch_up: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ScheduleConfig":
        raw = raw or {}
        return cls(
            mode=str(raw.get("mode", "manual")),
            weekdays=[int(x) for x in raw.get("weekdays", [0, 1, 2, 3, 4])],
            times=[str(x) for x in raw.get("times", [])],
            interval_minutes=max(1, int(raw.get("interval_minutes", 60))),
            window_start=str(raw.get("window_start", "08:00")),
            window_end=str(raw.get("window_end", "19:00")),
            catch_up=bool(raw.get("catch_up", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskDefinition:
    id: str
    name: str
    module: str
    workdir: str = ""
    enabled: bool = False
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskDefinition":
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name", raw["id"])),
            module=str(raw.get("module", "external")),
            workdir=str(raw.get("workdir", "")),
            enabled=bool(raw.get("enabled", False)),
            schedule=ScheduleConfig.from_dict(raw.get("schedule")),
            params=dict(raw.get("params") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schedule"] = self.schedule.to_dict()
        return result


@dataclass(slots=True)
class AppConfig:
    version: int = 1
    settings: dict[str, Any] = field(default_factory=dict)
    tasks: list[TaskDefinition] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        return cls(
            version=int(raw.get("version", 1)),
            settings=dict(raw.get("settings") or {}),
            tasks=[TaskDefinition.from_dict(x) for x in raw.get("tasks", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "settings": self.settings,
            "tasks": [task.to_dict() for task in self.tasks],
        }
