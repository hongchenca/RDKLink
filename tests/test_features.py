from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest

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

    def test_project_manifest_diff(self):
        with tempfile.TemporaryDirectory() as src:
            from pathlib import Path
            Path(src, "a.txt").write_text("a", encoding="utf-8")
            self.assertEqual(self.s.project_push(src, "manifest")["uploaded"], 1)
            self.assertEqual(self.s.project_push(src, "manifest")["uploaded"], 0)


if __name__ == "__main__": unittest.main()
