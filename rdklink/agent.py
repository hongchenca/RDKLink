from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import logging
import logging.handlers
import os
import fnmatch
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .models import ProcessInfo, result
from .serial_broker import SerialBroker


class Agent:
    """RDK X5 上的最小文件、进程和串口代理。"""

    def __init__(self, root: str, allowed_paths: list[str] | None = None, allowed_ports: list[str] | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_paths = [self.root] + [Path(x).resolve() for x in (allowed_paths or [])]
        self.processes: dict[int, tuple[subprocess.Popen[str], ProcessInfo]] = {}
        self.recent_processes: dict[int, ProcessInfo] = {}
        self.allowed_ports = allowed_ports or ["/dev/ttyS1", "/dev/ttyUSB*", "/dev/ttyACM*", "MOCK0"]
        self.serial_lines: deque[str] = deque(maxlen=2000)
        self.serial_records: deque[dict] = deque(maxlen=4000)
        self.serial_sequence = 0
        self.mock_serial = False
        self.serial = SerialBroker(self.serial_lines, self._record)
        self.device_id = os.environ.get("RDKLINK_DEVICE_ID", "rdk-x5-local")
        self._server: asyncio.AbstractServer | None = None
        self._shutdown: asyncio.Event | None = None

    def safe_path(self, raw: str) -> Path:
        requested = Path(raw)
        candidate = (requested if requested.is_absolute() else self.root / raw).resolve()
        if not any(candidate == allowed or allowed in candidate.parents for allowed in self.allowed_paths):
            raise ValueError("path escapes agent root")
        return candidate

    def _read_process_stream(self, stream: object, info: ProcessInfo, name: str) -> None:
        """后台持续读取单个输出流，保留 stdout/stderr 的来源信息。"""
        for line in stream:  # type: ignore[union-attr]
            text = line.rstrip("\r\n")
            info.output.append(text)
            info.output_records.append({"text": text, "stream": name})
            self.serial_lines.append(text)
            self._record("RX", text)

    def _watch_process(self, proc: subprocess.Popen[str], info: ProcessInfo) -> None:
        proc.wait()
        info.returncode = proc.returncode
        info.state = "exited"
        self.recent_processes[info.pid] = info

    def _record(self, direction: str, text: str) -> None:
        self.serial_sequence += 1
        self.serial_records.append({"sequence": self.serial_sequence, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "direction": direction, "hex": text.encode().hex(" "), "text": text})

    def handle(self, method: str, p: dict) -> dict:
        if method == "ping":
            return result(True, device_id=self.device_id, model="RDK X5", state="online", root=str(self.root))
        if method == "device_info":
            return result(True, device=self.device_id, hostname=os.environ.get("HOSTNAME", "rdk-x5"), model="RDK X5", cpu="Horizon X5", ram_mb=0, temperature_c=None, uptime_s=None)
        if method == "project_status":
            base = self.safe_path(p.get("path", "project"))
            label = str(base.relative_to(self.root)).replace("\\", "/") if self.root in base.parents or base == self.root else str(base).replace("\\", "/")
            return result(True, path=label, exists=base.exists(), files=sum(1 for x in base.rglob("*") if x.is_file()) if base.exists() else 0)
        if method == "list_files":
            base = self.safe_path(p.get("path", "."))
            files = [str(x.relative_to(self.root)).replace("\\", "/") if self.root in x.parents else str(x).replace("\\", "/") for x in base.rglob("*") if x.is_file()]
            return result(True, files=sorted(files))
        if method == "read_file":
            path = self.safe_path(p["path"])
            data = path.read_bytes()
            limit = max(1, min(int(p.get("max_bytes", 256 * 1024)), 4 * 1024 * 1024))
            return result(True, content=data[:limit].decode("utf-8", errors="replace"), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), truncated=len(data) > limit)
        if method == "write_file":
            path = self.safe_path(p["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            data = p.get("content", "").encode("utf-8")
            path.write_bytes(data)
            return result(True, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        if method == "delete_file":
            path = self.safe_path(p["path"])
            if path.exists():
                path.unlink()
            return result(True)
        if method == "run_process":
            cwd = self.safe_path(p.get("cwd", "."))
            command = p["command"]
            argv = shlex.split(command, posix=os.name != "nt")
            if not argv:
                raise ValueError("command must not be empty")
            proc = subprocess.Popen(argv, cwd=cwd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            info = ProcessInfo(proc.pid, p["command"])
            self.processes[proc.pid] = (proc, info)
            self.recent_processes[proc.pid] = info
            assert proc.stdout is not None and proc.stderr is not None
            threading.Thread(target=self._read_process_stream, args=(proc.stdout, info, "stdout"), daemon=True).start()
            threading.Thread(target=self._read_process_stream, args=(proc.stderr, info, "stderr"), daemon=True).start()
            threading.Thread(target=self._watch_process, args=(proc, info), daemon=True).start()
            return result(True, pid=proc.pid, state=info.state)
        if method == "stop_process":
            pid = int(p["pid"])
            item = self.processes.get(pid)
            if not item:
                return result(False, message="process not found")
            proc, info = item
            proc.terminate()
            info.state = "stopping"
            return result(True, pid=pid, state=info.state)
        if method == "process_output":
            pid = int(p["pid"]); item = self.processes.get(pid)
            info = item[1] if item else self.recent_processes.get(pid)
            if not info: return result(False, message="process not found", code="process_not_found")
            limit = max(1, min(int(p.get("max_lines", 200)), 1000))
            return result(True, pid=pid, state=info.state, command=info.command, records=info.output_records[-limit:], truncated=len(info.output_records) > limit)
        if method == "serial_list":
            opened = set(self.serial._connections)
            ports = [{"port": x, "open": x in opened, "baudrate": 115200} for x in self.serial.list_ports()]
            ports.extend({"port": x, "open": True, "baudrate": 115200} for x in opened if x not in {p["port"] for p in ports})
            if self.mock_serial: ports.append({"port": "MOCK0", "open": False, "baudrate": 115200})
            return result(True, ports=ports)
        if method == "serial_open":
            if not any(fnmatch.fnmatch(p["port"], pattern) for pattern in self.allowed_ports):
                raise PermissionError(f"serial port is not allowlisted: {p['port']}")
            if self.mock_serial and p["port"] == "MOCK0":
                self._record("RX", "MCU HEARTBEAT 1")
                return result(True, port="MOCK0", baudrate=int(p.get("baudrate", 115200)), mock=True)
            self.serial.open(p["port"], int(p.get("baudrate", 115200)))
            return result(True, port=p["port"], baudrate=int(p.get("baudrate", 115200)))
        if method == "serial_close":
            self.serial.close(p["port"]); return result(True, port=p["port"])
        if method == "serial_read_since":
            seq = int(p.get("sequence", 0)); limit = max(1, min(int(p.get("max_records", 300)), 1000))
            records = [x for x in self.serial_records if x["sequence"] > seq]
            return result(True, records=records[-limit:], truncated=len(records) > limit, next_sequence=self.serial_sequence)
        if method == "serial_write":
            port = p["port"]; data = p.get("data", "")
            if len(data.encode("utf-8")) > 4096: raise ValueError("serial write exceeds 4096-byte limit")
            if not any(fnmatch.fnmatch(port, pattern) for pattern in self.allowed_ports): raise PermissionError(f"serial port is not allowlisted: {port}")
            if self.mock_serial and port == "MOCK0":
                self._record("TX", data)
                if data.strip().upper() == "PING": self._record("RX", "PONG")
                if data.strip().upper() == "STATUS": self._record("RX", "READY")
            else: self.serial.write(port, data)
            return result(True, port=port, bytes=len(data.encode()))
        if method in {"serial_tail", "serial_wait"}:
            port = p.get("port")
            if port and not any(fnmatch.fnmatch(port, pattern) for pattern in self.allowed_ports):
                raise PermissionError(f"serial port is not allowlisted: {port}")
            if self.mock_serial and port == "MOCK0":
                self._mock_heartbeat()
            if port and not (self.mock_serial and port == "MOCK0"):
                self.serial.open(port, int(p.get("baudrate", 115200)))
            max_lines = max(1, min(int(p.get("max_lines", 100)), 1000))
            lines = list(self.serial_lines)[-max_lines:]
            if method == "serial_wait" and p.get("contains"):
                deadline = time.monotonic() + float(p.get("timeout", 5))
                while time.monotonic() < deadline and not any(p["contains"] in x for x in lines):
                    time.sleep(0.05)
                    lines = list(self.serial_lines)[-max_lines:]
            records = list(self.serial_records)[-max_lines:]
            return result(True, lines=lines, records=records, truncated=len(self.serial_records) > max_lines)
        raise ValueError(f"unknown method: {method}")

    def _mock_heartbeat(self) -> None:
        now = int(time.monotonic())
        last = getattr(self, "_mock_last_heartbeat", -1)
        if now != last:
            self._mock_last_heartbeat = now
            self._mock_heartbeat_count = getattr(self, "_mock_heartbeat_count", 0) + 1
            self._record("RX", f"MCU HEARTBEAT {self._mock_heartbeat_count}")

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    import json
                    req = json.loads(line)
                    output = await asyncio.to_thread(self.handle, req["method"], req.get("params", {}))
                    response = {"id": req.get("id"), "result": output}
                except Exception as exc:
                    response = {"id": req.get("id") if 'req' in locals() else None, "error": {"code": getattr(exc, "code", exc.__class__.__name__), "message": str(exc)}}
                import json
                writer.write((json.dumps(response, ensure_ascii=True) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve(self, host: str, port: int) -> None:
        self._shutdown = asyncio.Event()
        self._server = await asyncio.start_server(self.client, host, port)
        await self._shutdown.wait()
        self._server.close()
        await self._server.wait_closed()

    async def stop(self) -> None:
        """停止监听器，供测试或受控服务关闭流程调用。"""
        if self._shutdown:
            self._shutdown.set()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", default="/opt/rdklink/workspace")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--allowed-path", action="append", default=[])
    args = parser.parse_args()
    log_dir = Path(os.environ.get("RDKLINK_LOG_DIR", Path.home() / ".rdklink" / "logs")); log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / "rdklink-agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    agent = Agent(args.root, args.allowed_path); agent.mock_serial = args.mock
    asyncio.run(agent.serve(args.host, args.port))


if __name__ == "__main__":
    main()
