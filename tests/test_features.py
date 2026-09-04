from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from rdklink.agent import Agent
from rdklink.models import AgentConfig
from rdklink.service import RdkLinkService


class FeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory(); cls.loop = asyncio.new_event_loop(); cls.agent = Agent(cls.root.name); cls.agent.mock_serial = True
        def run():
            asyncio.set_event_loop(cls.loop); cls.loop.run_until_complete(cls.agent.serve("127.0.0.1", 18767))
        cls.thread = threading.Thread(target=run, daemon=True); cls.thread.start(); time.sleep(.15); cls.s = RdkLinkService(AgentConfig(port=18767))
    @classmethod
    def tearDownClass(cls):
        asyncio.run_coroutine_threadsafe(cls.agent.stop(), cls.loop).result(2); cls.thread.join(2); cls.loop.close(); cls.root.cleanup()

    def test_mock_serial_pattern_and_ring_records(self):
        self.assertTrue(self.s.call("serial_open", {"port": "MOCK0", "baudrate": 115200})["ok"])
        self.s.call("serial_write", {"port": "MOCK0", "data": "STATUS"})
        result = self.s.call("serial_wait", {"port": "MOCK0", "contains": "READY", "timeout": 1, "max_lines": 10})
        self.assertTrue(any(r["text"] == "READY" for r in result["records"]))
        since = self.s.call("serial_read_since", {"port": "MOCK0", "sequence": 0, "max_records": 10})
        self.assertGreaterEqual(len(since["records"]), 2)

    def test_device_and_project_status(self):
        self.assertEqual(self.s.device_info()["model"], "RDK X5")
        self.assertFalse(self.s.project_status("missing")["exists"])

    def test_project_run_does_not_use_shell(self):
        with self.assertRaises(Exception):
            self.s.project_run("echo SAFE > escaped.txt")
        with self.assertRaises(Exception):
            self.s.project_run("cmd.exe /c echo SAFE")

    def test_project_manifest_diff(self):
        with tempfile.TemporaryDirectory() as src:
            from pathlib import Path
            Path(src, "a.txt").write_text("a", encoding="utf-8")
            self.assertEqual(self.s.project_push(src, "manifest")["uploaded"], 1)
            self.assertEqual(self.s.project_push(src, "manifest")["uploaded"], 0)

    def test_binary_file_push_and_process_output_are_bounded(self):
        with tempfile.TemporaryDirectory() as src:
            Path(src, "blob.bin").write_bytes(bytes(range(256)))
            self.assertEqual(self.s.project_push(src, "binary")["uploaded"], 1)
            remote = self.s.call("file_info", {"path": "binary/blob.bin"})
            self.assertEqual(remote["sha256"], hashlib.sha256(bytes(range(256))).hexdigest())
            Path(src, "too-large.bin").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                self.s.project_push(src, "binary")
        command = subprocess.list2cmdline([sys.executable, "-c", "import sys; print('x' * 20000); print('err', file=sys.stderr)"])
        started = self.s.project_run(command, ".")
        output = {}
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            output = self.s.call("process_output", {"pid": started["pid"], "max_lines": 1000})
            if output["state"] == "exited":
                break
            time.sleep(.05)
        self.assertLessEqual(len(output["records"]), 200)
        self.assertTrue(all(len(item["text"].encode()) <= 16_384 for item in output["records"]))
        self.assertEqual({item["stream"] for item in output["records"]}, {"stdout", "stderr"})

    def test_serial_is_port_scoped_and_bounded(self):
        self.s.call("serial_open", {"port": "MOCK0"})
        self.s.call("serial_write", {"port": "MOCK0", "data": "PING"})
        result = self.s.call("serial_wait", {"port": "MOCK0", "pattern": "PONG", "timeout": 1, "max_records": 1})
        self.assertTrue(result["matched"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["port"], "MOCK0")
        with self.assertRaises(Exception):
            self.s.call("serial_read_since", {"port": "NOT_ALLOWED", "sequence": 0})


if __name__ == "__main__": unittest.main()
