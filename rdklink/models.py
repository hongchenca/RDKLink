from __future__ import annotations

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
    output: list[str] = field(default_factory=list)
    output_records: list[dict[str, str]] = field(default_factory=list)
    returncode: int | None = None


def result(ok: bool, **data: Any) -> dict[str, Any]:
    return {"ok": ok, **data}
