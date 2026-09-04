from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    timeout: float = 5.0


@dataclass
class ProcessInfo:
    pid: int
    command: str
    state: str = "running"
    output: deque[str] = field(default_factory=lambda: deque(maxlen=200))
    output_records: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=200))
    output_record_count: int = 0
    returncode: int | None = None


def result(ok: bool, **data: Any) -> dict[str, Any]:
    return {"ok": ok, **data}
