from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from .service_client import ServiceClient


def schema(properties: dict, required: list[str] | None = None) -> dict:
    value = {"type": "object", "properties": properties, "additionalProperties": False}
    if required: value["required"] = required
    return value


TOOLS = [
    {"name": "rdk_device_list", "description": "List configured RDK devices and their current online state. Use this before selecting a device.", "inputSchema": schema({})},
    {"name": "rdk_device_info", "description": "Read hardware and runtime information for the connected RDK X5 device.", "inputSchema": schema({})},
    {"name": "rdk_project_list", "description": "List projects configured in the Windows RDKLink workspace.", "inputSchema": schema({})},
    {"name": "rdk_project_status", "description": "Check whether a project exists on the device and report its remote file count.", "inputSchema": schema({"project": {"type": "string", "default": "project", "description": "Remote project directory."}})},
    {"name": "rdk_project_push", "description": "Incrementally upload a local project to the RDK device. Only changed files are transferred; output is bounded.", "inputSchema": schema({"local_path": {"type": "string", "description": "Windows local project directory."}, "remote_path": {"type": "string", "default": "project", "description": "Allowed remote project directory."}, "delete": {"type": "boolean", "default": False, "description": "Also delete remote files absent locally."}}, ["local_path"])},
    {"name": "rdk_project_run", "description": "Start a project command on the RDK device. Use the returned pid with output or stop.", "inputSchema": schema({"command": {"type": "string"}, "cwd": {"type": "string", "default": "project"}}, ["command"])},
    {"name": "rdk_project_stop", "description": "Stop a previously started RDK project process by pid.", "inputSchema": schema({"pid": {"type": "integer"}}, ["pid"])},
    {"name": "rdk_process_output", "description": "Read bounded stdout/stderr records for a running or recently running process.", "inputSchema": schema({"pid": {"type": "integer"}, "max_lines": {"type": "integer", "default": 200, "minimum": 1, "maximum": 1000}}, ["pid"])},
    {"name": "rdk_remote_file_read", "description": "Read one allowed remote file for diagnosis. Paths outside the filesystem allowlist are rejected. Content is bounded.", "inputSchema": schema({"path": {"type": "string"}, "max_bytes": {"type": "integer", "default": 262144, "minimum": 1, "maximum": 4194304}}, ["path"])},
    {"name": "rdk_remote_file_tail", "description": "Read the last bounded lines of an allowed remote text file without consuming it.", "inputSchema": schema({"path": {"type": "string"}, "max_lines": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000}}, ["path"])},
    {"name": "rdk_serial_list", "description": "List serial ports visible to the RDK agent and whether RDKLink currently owns them.", "inputSchema": schema({})},
    {"name": "rdk_serial_open", "description": "Open one allowlisted serial port through the shared Serial Broker. GUI and MCP share this connection.", "inputSchema": schema({"port": {"type": "string"}, "baudrate": {"type": "integer", "default": 115200, "minimum": 1, "maximum": 4000000}}, ["port"])},
    {"name": "rdk_serial_close", "description": "Close a serial port owned by the shared Serial Broker.", "inputSchema": schema({"port": {"type": "string"}}, ["port"])},
    {"name": "rdk_serial_tail", "description": "Read bounded recent serial traffic. Use for UART diagnosis; this does not consume the buffer.", "inputSchema": schema({"port": {"type": "string"}, "baudrate": {"type": "integer", "default": 115200}, "max_records": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000}}, ["port"])},
    {"name": "rdk_serial_read_since", "description": "Read serial records after a sequence number for loss-aware polling. Response includes next_sequence and truncation.", "inputSchema": schema({"port": {"type": "string"}, "sequence": {"type": "integer", "default": 0}, "max_records": {"type": "integer", "default": 300, "maximum": 1000}}, ["port"])},
    {"name": "rdk_serial_wait", "description": "Wait up to timeout seconds for serial text to contain pattern. On timeout inspect tail and process output.", "inputSchema": schema({"port": {"type": "string"}, "pattern": {"type": "string"}, "baudrate": {"type": "integer", "default": 115200}, "timeout": {"type": "number", "default": 10, "maximum": 120}, "max_records": {"type": "integer", "default": 100, "maximum": 1000}}, ["port", "pattern"])},
    {"name": "rdk_serial_write", "description": "Write bounded text to an already-open serial port. This mutates device state.", "inputSchema": schema({"port": {"type": "string"}, "data": {"type": "string", "maxLength": 4096}}, ["port", "data"])},
]


def dispatch(s: ServiceClient, name: str, a: dict) -> dict:
    mapping = {"rdk_device_list": "device_list", "rdk_device_info": "device_info", "rdk_project_list": "project_list", "rdk_project_status": "project_status", "rdk_project_push": "project_push", "rdk_project_run": "project_run", "rdk_project_stop": "project_stop", "rdk_process_output": "process_output", "rdk_serial_list": "serial_list", "rdk_serial_open": "serial_open", "rdk_serial_close": "serial_close", "rdk_serial_read_since": "serial_read_since", "rdk_serial_write": "serial_write"}
    if name in mapping:
        params = dict(a)
        if name == "rdk_project_status": params = {"project": a.get("project", "project")}
        return s.call(mapping[name], params)
    if name == "rdk_remote_file_read": return s.call("remote_file_read", {"path": a["path"], "max_bytes": a.get("max_bytes", 262144)})
    if name == "rdk_remote_file_tail":
        data = s.call("remote_file_read", {"path": a["path"], "max_bytes": 4 * 1024 * 1024}); lines = data.get("content", "").splitlines(); limit = max(1, min(a.get("max_lines", 100), 1000))
        return {"ok": True, "path": a["path"], "lines": lines[-limit:], "truncated": len(lines) > limit}
    if name == "rdk_serial_tail": return s.call("serial_tail", {"port": a["port"], "baudrate": a.get("baudrate", 115200), "max_records": a.get("max_records", 100)})
    if name == "rdk_serial_wait": return s.call("serial_wait", {"port": a["port"], "pattern": a["pattern"], "timeout": a.get("timeout", 10), "max_records": a.get("max_records", 100)})
    raise ValueError(f"unknown tool: {name}")


def main() -> None:
    import os
    log_dir = Path(os.environ.get("RDKLINK_LOG_DIR", Path.home() / ".rdklink" / "logs")); log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.handlers.RotatingFileHandler(log_dir / "rdklink-mcp.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
    os.environ["RDKLINK_SOURCE"] = "MCP"
    service = ServiceClient()
    for line in sys.stdin:
        if not line.strip(): continue
        req = {}
        try:
            req = json.loads(line); method = req.get("method")
            if method == "initialize": out = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "rdklink-mcp", "version": "0.2.0"}}
            elif method == "notifications/initialized": continue
            elif method == "tools/list": out = {"tools": TOOLS}
            elif method == "tools/call":
                p = req.get("params", {}); value = dispatch(service, p["name"], p.get("arguments", {})); out = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value, "isError": not value.get("ok", True)}
            else: raise ValueError(f"unsupported MCP method: {method}")
            print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": out}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32000, "message": str(exc), "type": exc.__class__.__name__}}, ensure_ascii=False), flush=True)


if __name__ == "__main__": main()
