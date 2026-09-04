from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

from .config import ConfigStore
from .models import AgentConfig
from .service import RdkLinkService

MAX_SERVICE_REQUEST_BYTES = 64 * 1024


class LocalServiceServer:
    """Windows 本地服务：GUI、CLI 和 MCP 的唯一业务入口。"""

    def __init__(self, core: RdkLinkService):
        self.core = core
        self._shutdown = asyncio.Event()

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "health": return {"ok": True, "service": "rdklink-service"}
        if method == "device_list": return self.core.device_list()
        if method == "device_info": return self.core.device_info()
        if method == "project_list": return self.core.project_list()
        if method == "project_add": return self.core.project_add(params["name"], params["local_path"], params["remote_path"], params.get("run_command", "python3 main.py"))
        if method == "project_status": return self.core.project_status(params.get("project", "project"))
        if method == "project_push": return self.core.project_push(params["local_path"], params.get("remote_path", "project"), params.get("delete", False))
        if method == "project_run": return self.core.project_run(params["command"], params.get("cwd", "project"))
        if method == "project_stop": return self.core.project_stop(int(params["pid"]))
        if method == "process_output": return self.core.call("process_output", params)
        if method == "remote_file_read": return self.core.call("read_file", params)
        if method in {"serial_list", "serial_open", "serial_close", "serial_read_since", "serial_write"}: return self.core.call(method, params)
        if method == "serial_tail": return self.core.serial_tail(params.get("max_records", params.get("max_lines", 100)), params.get("port"), params.get("baudrate", 115200))
        if method == "serial_wait": return self.core.serial_wait(params["pattern"], params.get("timeout", 10), params.get("max_records", params.get("max_lines", 100)), params.get("port"), params.get("baudrate", 115200), include_tx=bool(params.get("include_tx", False)))
        raise ValueError(f"unknown service method: {method}")

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                request: dict[str, Any] = {}
                try:
                    request = json.loads(line); output = await asyncio.to_thread(self.dispatch, request["method"], request.get("params", {})); response = {"id": request.get("id"), "result": output}
                except Exception as exc:
                    request_id = request.get("id") if isinstance(request, dict) else None
                    response = {"id": request_id, "error": {"code": getattr(exc, "code", exc.__class__.__name__), "message": str(exc)}}
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")); await writer.drain()
        finally:
            writer.close(); await writer.wait_closed()

    async def serve(self, host: str, port: int) -> None:
        server = await asyncio.start_server(self.client, host, port, limit=MAX_SERVICE_REQUEST_BYTES)
        async with server:
            await self._shutdown.wait()

    async def stop(self) -> None:
        self._shutdown.set()


def main() -> None:
    parser = argparse.ArgumentParser(prog="rdklink-service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("RDKLINK_SERVICE_PORT", "8766")))
    parser.add_argument("--agent-host", default=os.environ.get("RDKLINK_AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--agent-port", type=int, default=int(os.environ.get("RDKLINK_AGENT_PORT", "8765")))
    args = parser.parse_args()
    store = ConfigStore(); log = store.base / "logs" / "rdklink-service.log"; logging.basicConfig(level=logging.INFO, handlers=[logging.handlers.RotatingFileHandler(log, maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
    asyncio.run(LocalServiceServer(RdkLinkService(AgentConfig(args.agent_host, args.agent_port))).serve(args.host, args.port))


if __name__ == "__main__": main()
