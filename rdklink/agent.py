from __future__ import annotations

import argparse
import asyncio
import base64
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
from collections import OrderedDict, deque
from pathlib import Path

from .models import ProcessInfo, result
from .errors import SerialConfigurationConflictError
from .serial_broker import SerialBroker, SerialPortBuffer

MAX_RPC_LINE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_FILE_READ_BYTES = 256 * 1024
MAX_LIST_FILES = 2000
MAX_LIST_BYTES = 512 * 1024
MAX_PROCESS_LINE_CHARS = 16 * 1024
MAX_PROCESS_RECORDS_RESPONSE = 200
MAX_SERIAL_RECORDS_RESPONSE = 300
MAX_SERIAL_TEXT_BYTES = 4096
MAX_SERIAL_WAIT_SECONDS = 120


class Agent:
    """RDK X5 上的最小文件、进程和串口代理。"""

    def __init__(self, root: str, allowed_paths: list[str] | None = None, allowed_ports: list[str] | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_paths = [self.root] + [Path(x).resolve() for x in (allowed_paths or [])]
        self._process_lock = threading.Lock()
        self._serial_lock = threading.RLock()
        self._serial_condition = threading.Condition(self._serial_lock)
        self.processes: dict[int, tuple[subprocess.Popen[str], ProcessInfo]] = {}
        self.recent_processes: OrderedDict[int, ProcessInfo] = OrderedDict()
        self._reader_threads: dict[int, tuple[threading.Thread, threading.Thread]] = {}
        self.allowed_ports = allowed_ports or ["/dev/ttyS1", "/dev/ttyUSB*", "/dev/ttyACM*", "COM*", "MOCK0"]
        # Serial observations are isolated by port. These legacy attributes are
        # retained for callers that introspect Agent, but API reads use buffers.
        self.serial_lines: deque[str] = deque(maxlen=1000)
        self.serial_records: deque[dict] = deque(maxlen=1000)
        self.serial_buffers: dict[str, SerialPortBuffer] = {}
        self.serial_sequences: dict[str, int] = {}
        self.mock_serial = False
        self._mock_serial_configs: dict[str, int] = {}
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

    @staticmethod
    def _bounded_text(text: str, limit: int) -> str:
        data = text.encode("utf-8", errors="replace")
        if len(data) <= limit:
            return text
        marker = b" [truncated]"
        return (data[:limit - len(marker)].decode("utf-8", errors="ignore") + marker.decode("ascii"))

    @staticmethod
    def _split_command(command: str) -> list[str]:
        argv = shlex.split(command, posix=os.name != "nt")
        if os.name == "nt":
            argv = [token[1:-1] if len(token) >= 2 and token.startswith('"') and token.endswith('"') else token for token in argv]
        return argv

    def _read_process_stream(self, stream: object, info: ProcessInfo, name: str) -> None:
        """后台持续读取单个输出流，保留 stdout/stderr 的来源信息。"""
        while line := stream.readline(MAX_PROCESS_LINE_CHARS + 1):  # type: ignore[union-attr]
            text = self._bounded_text(line.rstrip("\r\n"), MAX_PROCESS_LINE_CHARS)
            with self._process_lock:
                info.output.append(text)
                info.output_records.append({"text": text, "stream": name})
                info.output_record_count += 1

    def _watch_process(self, proc: subprocess.Popen[str], info: ProcessInfo) -> None:
        proc.wait()
        with self._process_lock:
            readers = self._reader_threads.get(info.pid, ())
        for reader in readers:
            reader.join(timeout=2.0)
        with self._process_lock:
            info.returncode = proc.returncode
            info.state = "exited"
            self.processes.pop(info.pid, None)
            self._reader_threads.pop(info.pid, None)
            self.recent_processes[info.pid] = info
            self.recent_processes.move_to_end(info.pid)
            while len(self.recent_processes) > 200:
                self.recent_processes.popitem(last=False)

    def _record(self, direction: str, data: str | bytes, port: str = "") -> None:
        """Append a physical/mock serial observation to exactly one port buffer."""
        raw = data if isinstance(data, bytes) else data.encode("utf-8", errors="replace")
        raw = raw[:MAX_SERIAL_TEXT_BYTES]
        try:
            text: str | None = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        with self._serial_condition:
            sequence = self.serial_sequences.get(port, 0) + 1
            self.serial_sequences[port] = sequence
            record = {
                "sequence": sequence,
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "direction": direction,
                "port": port,
                "bytes": base64.b64encode(raw).decode("ascii"),
                "bytes_encoding": "base64",
                "hex": raw.hex(" ").upper(),
                "text": text,
                "_raw_bytes": raw,
            }
            buffer = self.serial_buffers.setdefault(port, SerialPortBuffer(max_records=1000, max_bytes=4 * 1024 * 1024))
            buffer.append(record)
            # Keep old diagnostic views bounded without using them for matching.
            self.serial_lines.append(f"[{port}] {text if text is not None else record['hex']}")
            self.serial_records.append(self._public_serial_record(record))
            self._serial_condition.notify_all()

    @staticmethod
    def _public_serial_record(record: dict) -> dict:
        return {key: value for key, value in record.items() if key != "_raw_bytes"}

    @property
    def serial_sequence(self) -> int:
        """Compatibility view; API cursors are per-port in ``serial_sequences``."""
        with self._serial_lock:
            return max(self.serial_sequences.values(), default=0)

    def _serial_records_for(self, port: str | None) -> list[dict]:
        with self._serial_lock:
            if port:
                buffer = self.serial_buffers.get(port)
                records = buffer.snapshot() if buffer else []
            else:
                records = [record for buffer in self.serial_buffers.values() for record in buffer.snapshot()]
        records.sort(key=lambda item: item["timestamp"])
        return records

    def _list_files(self, base: Path) -> list[str]:
        files: list[str] = []
        encoded_bytes = 0
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            label = str(path.relative_to(self.root)).replace("\\", "/") if self.root in path.parents else str(path).replace("\\", "/")
            encoded_bytes += len(label.encode("utf-8"))
            if len(files) >= MAX_LIST_FILES or encoded_bytes > MAX_LIST_BYTES:
                raise ValueError("file listing exceeds bounded response limits")
            files.append(label)
        return sorted(files)

    def _file_info(self, path: Path) -> dict:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
        digest = hashlib.sha256()
        actual_size = 0
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                actual_size += len(chunk)
                if actual_size > MAX_FILE_BYTES:
                    raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
                digest.update(chunk)
        return result(True, sha256=digest.hexdigest(), bytes=actual_size)

    def handle(self, method: str, p: dict) -> dict:
        if method == "ping":
            return result(True, device_id=self.device_id, model="RDK X5", state="online", root=str(self.root))
        if method == "device_info":
            return result(True, device=self.device_id, hostname=os.environ.get("HOSTNAME", "rdk-x5"), model="RDK X5", cpu="Horizon X5", ram_mb=0, temperature_c=None, uptime_s=None)
        if method == "project_status":
            base = self.safe_path(p.get("path", "project"))
            label = str(base.relative_to(self.root)).replace("\\", "/") if self.root in base.parents or base == self.root else str(base).replace("\\", "/")
            return result(True, path=label, exists=base.exists(), files=len(self._list_files(base)) if base.exists() else 0)
        if method == "list_files":
            base = self.safe_path(p.get("path", "."))
            return result(True, files=self._list_files(base))
        if method == "read_file":
            path = self.safe_path(p["path"])
            limit = max(1, min(int(p.get("max_bytes", MAX_FILE_READ_BYTES)), MAX_FILE_READ_BYTES))
            size = path.stat().st_size
            with path.open("rb") as source:
                data = source.read(limit)
            return result(True, content=data.decode("utf-8", errors="replace"), bytes=size, truncated=size > limit)
        if method == "file_info":
            return self._file_info(self.safe_path(p["path"]))
        if method == "write_file":
            path = self.safe_path(p["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if "content_base64" in p:
                try:
                    data = base64.b64decode(p["content_base64"], validate=True)
                except (ValueError, TypeError) as exc:
                    raise ValueError("content_base64 is invalid") from exc
            else:
                data = p.get("content", "").encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
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
            if not isinstance(command, str) or len(command) > 8192:
                raise ValueError("command must be a string of at most 8192 characters")
            argv = self._split_command(command)
            if not argv:
                raise ValueError("command must not be empty")
            executable_stem = Path(argv[0]).stem.lower()
            if executable_stem in {"sh", "bash", "dash", "zsh", "cmd", "powershell", "pwsh"}:
                raise ValueError("shell interpreters are not permitted")
            proc = subprocess.Popen(argv, cwd=cwd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            info = ProcessInfo(proc.pid, p["command"])
            assert proc.stdout is not None and proc.stderr is not None
            stdout_reader = threading.Thread(target=self._read_process_stream, args=(proc.stdout, info, "stdout"), daemon=True)
            stderr_reader = threading.Thread(target=self._read_process_stream, args=(proc.stderr, info, "stderr"), daemon=True)
            with self._process_lock:
                self.processes[proc.pid] = (proc, info)
                self.recent_processes[proc.pid] = info
                self.recent_processes.move_to_end(proc.pid)
                self._reader_threads[proc.pid] = (stdout_reader, stderr_reader)
                while len(self.recent_processes) > 200:
                    self.recent_processes.popitem(last=False)
            stdout_reader.start()
            stderr_reader.start()
            threading.Thread(target=self._watch_process, args=(proc, info), daemon=True).start()
            return result(True, pid=proc.pid, state=info.state)
        if method == "stop_process":
            pid = int(p["pid"])
            with self._process_lock:
                item = self.processes.get(pid)
            if not item:
                return result(False, message="process not found")
            proc, info = item
            proc.terminate()
            info.state = "stopping"
            return result(True, pid=pid, state=info.state)
        if method == "process_output":
            pid = int(p["pid"])
            with self._process_lock:
                item = self.processes.get(pid)
                info = item[1] if item else self.recent_processes.get(pid)
                if not info: return result(False, message="process not found", code="process_not_found")
                limit = max(1, min(int(p.get("max_lines", 200)), MAX_PROCESS_RECORDS_RESPONSE))
                records = list(info.output_records)[-limit:]
                state = info.state
                command = info.command
                truncated = info.output_record_count > len(records)
            return result(True, pid=pid, state=state, command=command, records=records, truncated=truncated)
        if method == "serial_list":
            opened = self.serial.opened_ports()
            details = self.serial.discover_serial_port_details(self.allowed_ports)
            ports = []
            for detail in details:
                path = detail["path"]
                session = self.serial.sessions().get(path)
                ports.append({
                    "port": path,
                    "path": path,
                    "name": detail["name"],
                    "type": detail["type"],
                    "description": detail["description"],
                    "available": detail.get("available", True),
                    "open": path in opened,
                    "baudrate": session.baudrate if session else 115200,
                })
            known = {item["port"] for item in ports}
            for path in sorted(opened - known):
                session = self.serial.sessions().get(path)
                ports.append({"port": path, "path": path, "name": Path(path).name, "type": "serial", "description": "Open serial port", "available": True, "open": True, "baudrate": session.baudrate if session else 115200})
            if self.mock_serial:
                baudrate = self._mock_serial_configs.get("MOCK0", 115200)
                ports.append({"port": "MOCK0", "path": "MOCK0", "name": "MOCK0", "type": "mock", "description": "RDKLink mock serial", "available": True, "open": "MOCK0" in self._mock_serial_configs, "baudrate": baudrate})
            return result(True, ports=ports, details=ports)
        if method == "serial_open":
            port = p["port"]
            if not any(fnmatch.fnmatch(port, pattern) for pattern in self.allowed_ports):
                raise PermissionError(f"serial port is not allowlisted: {port}")
            baudrate = int(p.get("baudrate", 115200))
            if self.mock_serial and port == "MOCK0":
                previous = self._mock_serial_configs.get(port)
                if previous is not None and previous != baudrate:
                    raise SerialConfigurationConflictError(f"serial port {port} is already open with baudrate={previous}")
                if previous is None:
                    self._mock_serial_configs[port] = baudrate
                    self._record("RX", "MCU HEARTBEAT 1", port)
                return result(True, port=port, baudrate=baudrate, mock=True, reused=previous is not None)
            reused = port in self.serial.opened_ports()
            self.serial.open(port, baudrate, bytesize=int(p.get("bytesize", 8)), parity=str(p.get("parity", "N")), stopbits=float(p.get("stopbits", 1)))
            return result(True, port=port, baudrate=baudrate, reused=reused)
        if method == "serial_close":
            port = p["port"]
            if not any(fnmatch.fnmatch(port, pattern) for pattern in self.allowed_ports):
                raise PermissionError(f"serial port is not allowlisted: {port}")
            if self.mock_serial and port == "MOCK0":
                self._mock_serial_configs.pop(port, None)
            else:
                self.serial.close(port)
            return result(True, port=port)
        if method == "serial_read_since":
            port = p.get("port")
            if port and not any(fnmatch.fnmatch(port, pattern) for pattern in self.allowed_ports):
                raise PermissionError(f"serial port is not allowlisted: {port}")
            seq = int(p.get("sequence", 0)); limit = max(1, min(int(p.get("max_records", 300)), MAX_SERIAL_RECORDS_RESPONSE))
            scoped = self._serial_records_for(port)
            records = [x for x in scoped if x["sequence"] > seq]
            oldest = scoped[0]["sequence"] if scoped else None
            next_sequence = max((int(x["sequence"]) for x in scoped), default=0)
            return result(True, records=[self._public_serial_record(x) for x in records[-limit:]], truncated=len(records) > limit or (oldest is not None and seq < oldest - 1), next_sequence=next_sequence)
        if method == "serial_write":
            port = p["port"]; data = p.get("data", "")
            if len(data.encode("utf-8")) > 4096: raise ValueError("serial write exceeds 4096-byte limit")
            if not any(fnmatch.fnmatch(port, pattern) for pattern in self.allowed_ports): raise PermissionError(f"serial port is not allowlisted: {port}")
            if self.mock_serial and port == "MOCK0":
                self._record("TX", data, port)
                if data.strip().upper() == "PING": self._record("RX", "PONG", port)
                if data.strip().upper() == "STATUS": self._record("RX", "READY", port)
            else:
                self.serial.write(port, data)
            return result(True, port=port, bytes=len(data.encode()))
        if method in {"serial_tail", "serial_wait"}:
            port = p.get("port")
            if port and not any(fnmatch.fnmatch(port, pattern) for pattern in self.allowed_ports):
                raise PermissionError(f"serial port is not allowlisted: {port}")
            if self.mock_serial and port == "MOCK0":
                requested_baudrate = int(p.get("baudrate", 115200))
                previous_baudrate = self._mock_serial_configs.get(port)
                if previous_baudrate is not None and previous_baudrate != requested_baudrate:
                    raise SerialConfigurationConflictError(f"serial port {port} is already open with baudrate={previous_baudrate}")
                if previous_baudrate is None:
                    self._mock_serial_configs[port] = requested_baudrate
                self._mock_heartbeat()
            if port and not (self.mock_serial and port == "MOCK0"):
                self.serial.open(port, int(p.get("baudrate", 115200)))
            max_records = max(1, min(int(p.get("max_records", p.get("max_lines", 100))), MAX_SERIAL_RECORDS_RESPONSE))
            contains = p.get("contains", p.get("pattern", ""))
            include_tx = bool(p.get("include_tx", False))
            records = self._serial_records_for(port)
            matched = not contains or any((x["direction"] == "RX" or include_tx) and x.get("text") is not None and contains in x["text"] for x in records)
            if method == "serial_wait" and contains:
                timeout = max(0, min(float(p.get("timeout", 5)), MAX_SERIAL_WAIT_SECONDS))
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and not matched:
                    with self._serial_condition:
                        self._serial_condition.wait(timeout=max(0.0, min(0.05, deadline - time.monotonic())))
                    records = self._serial_records_for(port)
                    matched = any((x["direction"] == "RX" or include_tx) and x.get("text") is not None and contains in x["text"] for x in records)
            total_records = len(records)
            records = records[-max_records:]
            public = [self._public_serial_record(x) for x in records]
            return result(True, lines=[x["text"] for x in public if x.get("text") is not None], records=public, matched=matched, truncated=total_records > max_records)
        raise ValueError(f"unknown method: {method}")

    def _mock_heartbeat(self) -> None:
        now = int(time.monotonic())
        last = getattr(self, "_mock_last_heartbeat", -1)
        if now != last:
            self._mock_last_heartbeat = now
            self._mock_heartbeat_count = getattr(self, "_mock_heartbeat_count", 0) + 1
            self._record("RX", f"MCU HEARTBEAT {self._mock_heartbeat_count}", "MOCK0")

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    import json
                    req = json.loads(line)
                    output = await asyncio.to_thread(self.handle, req["method"], req.get("params", {}))
                    response = {"id": req.get("id"), "result": output}
                except Exception as exc:
                    request_id = req.get("id") if isinstance(req, dict) else None
                    response = {"id": request_id, "error": {"code": getattr(exc, "code", exc.__class__.__name__), "message": str(exc)}}
                import json
                writer.write((json.dumps(response, ensure_ascii=True) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve(self, host: str, port: int) -> None:
        self._shutdown = asyncio.Event()
        self._server = await asyncio.start_server(self.client, host, port, limit=MAX_RPC_LINE_BYTES)
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
