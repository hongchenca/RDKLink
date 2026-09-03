from __future__ import annotations

import os
import time
from typing import Any

from .protocol import request


class ServiceClient:
    """MCP、CLI 和 GUI 使用的 Windows localhost Service 客户端。"""

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or os.environ.get("RDKLINK_SERVICE_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("RDKLINK_SERVICE_PORT", "8766"))

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        timeout = 5.0
        if method == "serial_wait":
            timeout = max(timeout, min(float((params or {}).get("timeout", 10)) + 2, 125))
        return request(self.host, self.port, method, params, timeout)
