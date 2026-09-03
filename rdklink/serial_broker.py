from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable


class SerialBroker:
    """设备侧串口读取器，按端口和波特率复用连接并写入共享缓存。"""

    def __init__(self, cache: deque[str], record: Callable[[str, str], None] | None = None):
        self.cache = cache
        self.record = record
        self._connections: dict[str, object] = {}
        self._lock = threading.Lock()

    def open(self, port: str, baudrate: int) -> None:
        """打开串口并启动读取线程；需要在 Agent 环境安装 pyserial。"""
        if baudrate <= 0 or baudrate > 4_000_000:
            raise ValueError("baudrate must be between 1 and 4000000")
        with self._lock:
            if port in self._connections:
                return
            try:
                import serial  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("serial support requires pyserial: pip install pyserial") from exc
            conn = serial.Serial(port, baudrate=baudrate, timeout=0.2)
            self._connections[port] = conn
            threading.Thread(target=self._reader, args=(port, conn), daemon=True).start()

    def _reader(self, port: str, conn: object) -> None:
        """持续读取一个已打开串口的字节流，断开后清理连接状态。"""
        try:
            while getattr(conn, "is_open", False):
                raw = conn.readline()
                if raw:
                    text = raw.decode("utf-8", errors="replace").rstrip()
                    self.cache.append(f"[{port}] {text}")
                    if self.record:
                        self.record("RX", text)
        finally:
            with self._lock:
                self._connections.pop(port, None)
            try:
                conn.close()
            except Exception:
                pass

    def close(self, port: str) -> None:
        with self._lock:
            conn = self._connections.pop(port, None)
        if conn:
            conn.close()

    def write(self, port: str, data: str) -> None:
        with self._lock:
            conn = self._connections.get(port)
        if not conn:
            raise RuntimeError(f"serial port is not open: {port}")
        conn.write(data.encode("utf-8"))

    def list_ports(self) -> list[str]:
        try:
            from serial.tools import list_ports  # type: ignore[import-not-found]
        except ImportError:
            return []
        return [item.device for item in list_ports.comports()]
