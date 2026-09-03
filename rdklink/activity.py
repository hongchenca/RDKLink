from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path


class ActivityLog:
    def __init__(self, path: Path):
        self.logger = logging.getLogger(f"rdklink.activity.{path}")
        for old_handler in self.logger.handlers:
            old_handler.close()
        self.logger.handlers.clear()
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        self.logger.addHandler(handler); self.logger.setLevel(logging.INFO)

    def record(self, source: str, operation: str, device: str, params: dict, ok: bool, duration_ms: int) -> None:
        self.logger.info(json.dumps({"timestamp": time.time(), "source": source, "operation": operation, "device": device, "params": params, "ok": ok, "duration_ms": duration_ms}, ensure_ascii=False))
