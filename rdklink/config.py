from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class ConfigStore:
    """管理 %USERPROFILE%/.rdklink 下的 YAML 配置和日志目录。"""

    def __init__(self, base: str | None = None):
        self.base = Path(base or os.environ.get("RDKLINK_HOME", Path.home() / ".rdklink"))
        (self.base / "logs").mkdir(parents=True, exist_ok=True)

    def load(self, name: str, default: Any) -> Any:
        path = self.base / name
        if not path.exists():
            return default
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            return default if loaded is None else loaded
        except (OSError, yaml.YAMLError):
            return default

    def save(self, name: str, value: Any) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / name).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
