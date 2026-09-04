from __future__ import annotations

import fnmatch
import glob
import inspect
import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import SerialConfigurationConflictError

logger = logging.getLogger(__name__)


class SerialPortBuffer:
    """Bounded, port-local record storage used by the agent serial API."""

    def __init__(self, max_records: int = 1000, max_bytes: int = 1024 * 1024):
        self.max_records = max(1, max_records)
        self.max_bytes = max(1, max_bytes)
        self.records: deque[dict[str, Any]] = deque()
        self.bytes = 0

    @staticmethod
    def _record_size(record: dict[str, Any]) -> int:
        raw = record.get("_raw_bytes")
        if isinstance(raw, bytes):
            return len(raw)
        return len(str(record.get("text") or "").encode("utf-8", errors="replace"))

    def append(self, record: dict[str, Any]) -> None:
        size = self._record_size(record)
        if size > self.max_bytes:
            return
        self.records.append(record)
        self.bytes += size
        while len(self.records) > self.max_records or self.bytes > self.max_bytes:
            removed = self.records.popleft()
            self.bytes -= self._record_size(removed)

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self.records]


@dataclass
class SerialSession:
    port: str
    baudrate: int
    bytesize: int
    parity: str
    stopbits: float
    connection: object
    stop_event: threading.Event
    opened_at: str
    reader_thread: threading.Thread | None = None
    connected: bool = True
    last_rx: str | None = None
    last_tx: str | None = None
    last_error: str | None = None

    @property
    def config(self) -> tuple[int, int, str, float]:
        return self.baudrate, self.bytesize, self.parity, self.stopbits


class SerialBroker:
    """Own one physical serial session per port and continuously read its bytes."""

    MAX_LINE_BYTES = 4096

    def __init__(
        self,
        cache: deque[str] | None = None,
        record: Callable[..., None] | None = None,
        *,
        serial_factory: Callable[..., object] | None = None,
        max_records: int = 1000,
        max_bytes: int = 1024 * 1024,
    ):
        # ``cache`` remains for compatibility with older callers. Agent records
        # are delivered through ``record`` and are not mixed with process output.
        self.cache = cache if cache is not None else deque(maxlen=max_records)
        self.record = record
        self.max_records = max(1, max_records)
        self.max_bytes = max(1, max_bytes)
        self._serial_factory = serial_factory
        self._connections: dict[str, object] = {}
        self._sessions: dict[str, SerialSession] = {}
        self.buffers: dict[str, SerialPortBuffer] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _allowed(port: str, allowed_ports: Sequence[str] | None) -> bool:
        return not allowed_ports or any(fnmatch.fnmatch(port, pattern) for pattern in allowed_ports)

    def _discover_linux_serial_nodes(self) -> list[str]:
        """Discover only known Linux serial device-node families."""
        paths: set[str] = set()
        for pattern in ("/dev/ttyS*", "/dev/ttyUSB*", "/dev/ttyACM*"):
            paths.update(glob.glob(pattern))
        return sorted(paths)

    def discover_serial_port_details(self, allowed_ports: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Return pyserial ports plus bounded Linux node discovery.

        Device nodes are limited to ttyS/ttyUSB/ttyACM and filtered before they
        leave the agent. pyserial metadata wins when both methods find a path.
        """
        metadata: dict[str, dict[str, Any]] = {}
        try:
            from serial.tools import list_ports  # type: ignore[import-not-found]

            entries: Iterable[object] = list_ports.comports()
        except (ImportError, OSError):
            entries = ()
        for item in entries:
            path = str(getattr(item, "device", item) or "")
            if not path or not self._allowed(path, allowed_ports):
                continue
            name = Path(path).name
            if name.startswith("ttyS"):
                port_type = "onboard_uart"
            elif name.startswith(("ttyUSB", "ttyACM")) or path.upper().startswith("COM"):
                port_type = "usb_serial"
            else:
                port_type = "serial"
            metadata[path] = {
                "path": path,
                "name": name,
                "type": port_type,
                "description": str(getattr(item, "description", "") or "Serial port"),
                "available": True,
            }
            for key in ("vid", "pid", "serial_number", "manufacturer", "product"):
                value = getattr(item, key, None)
                if value is not None:
                    metadata[path][key] = value

        for path in self._discover_linux_serial_nodes():
            if not self._allowed(path, allowed_ports):
                continue
            if path not in metadata:
                name = Path(path).name
                metadata[path] = {
                    "path": path,
                    "name": name,
                    "type": "onboard_uart" if name.startswith("ttyS") else "usb_serial",
                    "description": "Linux UART" if name.startswith("ttyS") else "Linux USB serial",
                    "available": Path(path).exists(),
                }

        return [metadata[path] for path in sorted(metadata)]

    def discover_serial_ports(self, allowed_ports: Sequence[str] | None = None) -> list[str]:
        """Return only allowlisted serial device paths."""
        return [item["path"] for item in self.discover_serial_port_details(allowed_ports)]

    def list_ports(self, allowed_ports: Sequence[str] | None = None) -> list[str]:
        """Compatibility alias for serial discovery."""
        return self.discover_serial_ports(allowed_ports)

    def _make_connection(self, port: str, baudrate: int, bytesize: int, parity: str, stopbits: float) -> object:
        factory = self._serial_factory
        if factory is None:
            try:
                import serial  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("serial support requires pyserial: pip install pyserial") from exc
            factory = serial.Serial
        kwargs = {"baudrate": baudrate, "timeout": 0.2, "bytesize": bytesize, "parity": parity, "stopbits": stopbits}
        try:
            return factory(port, **kwargs)
        except TypeError:
            # Keep simple fake transports and older adapters usable in tests.
            return factory(port, baudrate=baudrate, timeout=0.2)

    def open(self, port: str, baudrate: int, *, bytesize: int = 8, parity: str = "N", stopbits: float = 1) -> None:
        if baudrate <= 0 or baudrate > 4_000_000:
            raise ValueError("baudrate must be between 1 and 4000000")
        requested = (int(baudrate), int(bytesize), str(parity).upper(), float(stopbits))
        with self._lock:
            existing = self._sessions.get(port)
            if existing and existing.connected:
                if existing.config != requested:
                    raise SerialConfigurationConflictError(
                        f"serial port {port} is already open with baudrate={existing.baudrate}, "
                        f"bytesize={existing.bytesize}, parity={existing.parity}, stopbits={existing.stopbits}"
                    )
                return
            conn = self._make_connection(port, *requested)
            session = SerialSession(
                port=port,
                baudrate=requested[0],
                bytesize=requested[1],
                parity=requested[2],
                stopbits=requested[3],
                connection=conn,
                stop_event=threading.Event(),
                opened_at=datetime.now(timezone.utc).isoformat(),
            )
            self._sessions[port] = session
            self._connections[port] = conn
            thread = threading.Thread(target=self._reader, args=(session,), daemon=True, name=f"rdklink-serial-{port}")
            session.reader_thread = thread
            thread.start()

    def _reader(self, session: SerialSession) -> None:
        conn = session.connection
        try:
            while not session.stop_event.is_set() and getattr(conn, "is_open", True):
                read_until = getattr(conn, "read_until", None)
                if callable(read_until):
                    raw = read_until(b"\n", self.MAX_LINE_BYTES + 1)
                else:
                    readline = getattr(conn, "readline", None)
                    if callable(readline):
                        try:
                            raw = readline(self.MAX_LINE_BYTES + 1)
                        except TypeError:
                            raw = readline()
                    else:
                        raw = conn.read(self.MAX_LINE_BYTES + 1)  # type: ignore[attr-defined]
                if raw:
                    if not isinstance(raw, bytes):
                        raw = bytes(raw)
                    raw = raw[: self.MAX_LINE_BYTES].rstrip(b"\r\n")
                    if raw:
                        session.last_rx = datetime.now(timezone.utc).isoformat()
                        self._emit("RX", raw, session.port)
        except Exception as exc:
            session.connected = False
            session.last_error = str(exc)
            logger.warning("serial reader disconnected port=%s: %s", session.port, exc)
        finally:
            with self._lock:
                current = self._sessions.get(session.port)
                if current is session:
                    self._sessions.pop(session.port, None)
                    self._connections.pop(session.port, None)
            try:
                conn.close()
            except Exception as exc:
                logger.debug("serial close failed port=%s: %s", session.port, exc)

    def _emit(self, direction: str, raw: bytes, port: str) -> None:
        with self._lock:
            self.buffers.setdefault(port, SerialPortBuffer(self.max_records, self.max_bytes)).append(
                {"direction": direction, "port": port, "timestamp": datetime.now(timezone.utc).isoformat(), "_raw_bytes": raw}
            )
        if self.record is None:
            text = raw.decode("utf-8", errors="replace")
            self.cache.append(f"[{port}] {text}")
            return
        try:
            parameters = inspect.signature(self.record).parameters
            accepts_port = len(parameters) >= 3 or any(p.kind == p.VAR_POSITIONAL for p in parameters.values())
        except (TypeError, ValueError):
            accepts_port = True
        if accepts_port:
            self.record(direction, raw, port)
        else:
            self.record(direction, raw)

    def close(self, port: str) -> None:
        with self._lock:
            session = self._sessions.pop(port, None)
            self._connections.pop(port, None)
        if session:
            session.stop_event.set()
            try:
                session.connection.close()
            except Exception as exc:
                logger.debug("serial close failed port=%s: %s", port, exc)

    def opened_ports(self) -> set[str]:
        with self._lock:
            return {port for port, session in self._sessions.items() if session.connected}

    def sessions(self) -> dict[str, SerialSession]:
        with self._lock:
            return dict(self._sessions)

    def write(self, port: str, data: str | bytes) -> int:
        with self._lock:
            session = self._sessions.get(port)
        if not session or not session.connected:
            raise RuntimeError(f"serial port is not open: {port}")
        raw = data if isinstance(data, bytes) else data.encode("utf-8")
        written = session.connection.write(raw)  # type: ignore[attr-defined]
        session.last_tx = datetime.now(timezone.utc).isoformat()
        self._emit("TX", raw, port)
        return int(written if written is not None else len(raw))
