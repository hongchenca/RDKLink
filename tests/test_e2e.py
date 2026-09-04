from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import subprocess
import sys
import unittest
from pathlib import Path

from rdklink.agent import Agent
from rdklink.models import AgentConfig
from rdklink.service import RdkLinkService


class E2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = tempfile.TemporaryDirectory()
        cls.loop = asyncio.new_event_loop()
        cls.agent = Agent(cls.device.name)
        def serve() -> None:
            asyncio.set_event_loop(cls.loop)
            cls.loop.run_until_complete(cls.agent.serve("127.0.0.1", 18765))
        cls.thread = threading.Thread(target=serve, daemon=True)
        cls.thread.start(); time.sleep(0.15)
        cls.service = RdkLinkService(AgentConfig(port=18765))

    @classmethod
    def tearDownClass(cls):
        asyncio.run_coroutine_threadsafe(cls.agent.stop(), cls.loop).result(timeout=2)
        cls.thread.join(timeout=2)
        cls.loop.close()
        cls.device.cleanup()

    def test_push_run_and_tail(self):
        with tempfile.TemporaryDirectory() as src:
            Path(src, "main.py").write_text("print('X5_READY')", encoding="utf-8")
            pushed = self.service.project_push(src, "project")
            self.assertEqual(pushed["changed"], ["main.py"])
            self.assertEqual(pushed["uploaded"], 1)
            self.assertEqual(self.service.project_push(src, "project")["uploaded"], 0)
            Path(src, "main.py").write_text("print('X5_READY_2')", encoding="utf-8")
            self.assertEqual(self.service.project_push(src, "project")["uploaded"], 1)
        started = self.service.project_run(subprocess.list2cmdline([sys.executable, "main.py"]))
        self.assertTrue(started["ok"])
        output = {}
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            output = self.service.call("process_output", {"pid": started["pid"], "max_lines": 10})
            if any(record["text"] == "X5_READY_2" and record["stream"] == "stdout" for record in output["records"]):
                break
            time.sleep(0.05)
        self.assertTrue(any(record["text"] == "X5_READY_2" and record["stream"] == "stdout" for record in output["records"]), output)

    def test_path_escape_rejected(self):
        with self.assertRaises(Exception):
            self.service.call("read_file", {"path": "../secret"})


if __name__ == "__main__":
    unittest.main()
