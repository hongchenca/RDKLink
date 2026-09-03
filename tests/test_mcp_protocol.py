from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest


class McpProtocolTest(unittest.TestCase):
    def test_initialize_list_and_status(self):
        with tempfile.TemporaryDirectory() as root:
            port = "18766"
            agent = subprocess.Popen(
                [sys.executable, "-m", "rdklink.cli", "mock-agent", "--port", port, "--root", root],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=os.path.dirname(os.path.dirname(__file__)),
            )
            try:
                time.sleep(0.2)
                service = subprocess.Popen(
                    [sys.executable, "-m", "rdklink.service_server", "--agent-port", port, "--port", "18768"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=os.path.dirname(os.path.dirname(__file__)),
                )
                env = os.environ.copy()
                env.update({"RDKLINK_SERVICE_HOST": "127.0.0.1", "RDKLINK_SERVICE_PORT": "18768"})
                mcp = subprocess.Popen(
                    [sys.executable, "-m", "rdklink.mcp_server"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                    cwd=os.path.dirname(os.path.dirname(__file__)),
                    env=env,
                )
                try:
                    assert mcp.stdin and mcp.stdout
                    messages = [
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "rdk_device_info", "arguments": {}}},
                    ]
                    for message in messages:
                        mcp.stdin.write(json.dumps(message) + "\n")
                        mcp.stdin.flush()
                    responses = [json.loads(mcp.stdout.readline()) for _ in messages]
                    self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "rdklink-mcp")
                    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
                    self.assertIn("rdk_project_push", names)
                    self.assertEqual(json.loads(responses[2]["result"]["content"][0]["text"])["model"], "RDK X5")
                finally:
                    mcp.terminate()
                    mcp.wait(timeout=3)
                    if mcp.stdin: mcp.stdin.close()
                    if mcp.stdout: mcp.stdout.close()
                    service.terminate(); service.wait(timeout=3)
            finally:
                agent.terminate()
                agent.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
