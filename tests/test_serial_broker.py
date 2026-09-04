from __future__ import annotations

import queue
import subprocess
import sys
import tempfile
import time

import pytest

from rdklink.agent import Agent
from rdklink.errors import SerialConfigurationConflictError
from rdklink.serial_broker import SerialBroker, SerialPortBuffer


class FakeSerial:
    instances: dict[str, "FakeSerial"] = {}

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.2, **_: object):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self.rx: queue.Queue[bytes] = queue.Queue()
        self.writes: list[bytes] = []
        self.fail_reads = False
        FakeSerial.instances[port] = self

    def read_until(self, _expected: bytes = b"\n", _size: int | None = None) -> bytes:
        if self.fail_reads:
            raise OSError("device disconnected")
        try:
            return self.rx.get(timeout=self.timeout)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.is_open = False

    def feed(self, data: bytes) -> None:
        self.rx.put(data)


def make_agent(*ports: str) -> Agent:
    root = tempfile.mkdtemp()
    agent = Agent(root, allowed_ports=list(ports))
    FakeSerial.instances.clear()
    agent.serial = SerialBroker(agent.serial_lines, agent._record, serial_factory=FakeSerial)
    return agent


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_linux_ttys_discovery_and_allowlist() -> None:
    broker = SerialBroker()
    broker._discover_linux_serial_nodes = lambda: ["/dev/ttyS0", "/dev/ttyS1", "/dev/ttyUSB0"]  # type: ignore[method-assign]
    assert broker.discover_serial_ports(["/dev/ttyS1", "/dev/ttyUSB*"]) == ["/dev/ttyS1", "/dev/ttyUSB0"]
    details = broker.discover_serial_port_details(["/dev/ttyS1"])
    assert details[0]["type"] == "onboard_uart"


def test_process_output_is_not_serial_data() -> None:
    agent = make_agent("A")
    command = subprocess.list2cmdline([sys.executable, "-c", "print('PROCESS_ONLY_TEST')"])
    started = agent.handle("run_process", {"command": command, "cwd": "."})
    wait_until(lambda: agent.handle("process_output", {"pid": started["pid"]})["state"] == "exited")
    assert any(item["text"] == "PROCESS_ONLY_TEST" for item in agent.handle("process_output", {"pid": started["pid"]})["records"])
    assert agent.handle("serial_tail", {"port": "A", "max_records": 10})["records"] == []


def test_process_stderr_not_in_serial_buffer() -> None:
    agent = make_agent("A")
    command = subprocess.list2cmdline([sys.executable, "-c", "import sys; print('PROCESS_STDERR_ONLY', file=sys.stderr)"])
    started = agent.handle("run_process", {"command": command, "cwd": "."})
    wait_until(lambda: agent.handle("process_output", {"pid": started["pid"]})["state"] == "exited")
    output = agent.handle("process_output", {"pid": started["pid"]})
    assert any(item["text"] == "PROCESS_STDERR_ONLY" and item["stream"] == "stderr" for item in output["records"])
    assert not agent.handle("serial_wait", {"port": "A", "pattern": "PROCESS_STDERR_ONLY", "timeout": 0.02})["matched"]


def test_serial_wait_does_not_match_process_output_or_other_port() -> None:
    agent = make_agent("A", "B")
    agent._record("RX", b"READY", "B")
    assert not agent.handle("serial_wait", {"port": "A", "pattern": "READY", "timeout": 0.02})["matched"]
    command = subprocess.list2cmdline([sys.executable, "-c", "print('READY')"])
    started = agent.handle("run_process", {"command": command, "cwd": "."})
    wait_until(lambda: agent.handle("process_output", {"pid": started["pid"]})["state"] == "exited")
    assert not agent.handle("serial_wait", {"port": "A", "pattern": "READY", "timeout": 0.02})["matched"]


def test_serial_buffers_are_per_port_and_wait_is_rx_only() -> None:
    agent = make_agent("A", "B")
    agent.handle("serial_open", {"port": "A", "baudrate": 115200})
    agent.handle("serial_open", {"port": "B", "baudrate": 115200})
    FakeSerial.instances["A"].feed(b"A_DATA\n")
    FakeSerial.instances["B"].feed(b"B_DATA\n")
    wait_until(lambda: len(agent.handle("serial_tail", {"port": "A"})["records"]) == 1)
    assert [r["text"] for r in agent.handle("serial_tail", {"port": "A"})["records"]] == ["A_DATA"]
    assert [r["text"] for r in agent.handle("serial_tail", {"port": "B"})["records"]] == ["B_DATA"]
    agent.handle("serial_write", {"port": "A", "data": "READY"})
    assert not agent.handle("serial_wait", {"port": "A", "pattern": "READY", "timeout": 0.05})["matched"]
    assert agent.handle("serial_wait", {"port": "A", "pattern": "READY", "timeout": 0.05, "include_tx": True})["matched"]


def test_serial_read_since_only_requested_port() -> None:
    agent = make_agent("A", "B")
    agent._record("RX", b"A1", "A")
    agent._record("RX", b"B1", "B")
    result = agent.handle("serial_read_since", {"port": "A", "sequence": 0})
    assert [item["text"] for item in result["records"]] == ["A1"]
    assert result["next_sequence"] == 1


def test_binary_serial_record_and_bounded_buffer() -> None:
    agent = make_agent("A")
    agent._record("RX", b"\xff\x00\x80\xa5", "A")
    record = agent.handle("serial_tail", {"port": "A"})["records"][0]
    assert record["hex"] == "FF 00 80 A5"
    assert record["text"] is None
    assert record["bytes"]
    buffer = SerialPortBuffer(max_records=2, max_bytes=5)
    for value in (b"1", b"22", b"333"):
        buffer.append({"_raw_bytes": value, "text": value.decode()})
    assert len(buffer.records) == 2
    assert buffer.bytes <= 5


def test_same_port_session_shared_and_conflict() -> None:
    FakeSerial.instances.clear()
    broker = SerialBroker(serial_factory=FakeSerial)
    broker.open("A", 115200)
    broker.open("A", 115200)
    assert len(FakeSerial.instances) == 1
    with pytest.raises(SerialConfigurationConflictError):
        broker.open("A", 9600)
    broker.close("A")


def test_reader_exception_allows_reopen() -> None:
    FakeSerial.instances.clear()
    broker = SerialBroker(serial_factory=FakeSerial)
    broker.open("A", 115200)
    FakeSerial.instances["A"].fail_reads = True
    wait_until(lambda: "A" not in broker.opened_ports())
    broker.open("A", 115200)
    assert "A" in broker.opened_ports()
    broker.close("A")
