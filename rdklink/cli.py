from __future__ import annotations

import argparse
import asyncio
import json

from .agent import Agent
from .service_client import ServiceClient


def main() -> None:
    parser = argparse.ArgumentParser(prog="rdklink-cli")
    sub = parser.add_subparsers(dest="group", required=True)
    mock = sub.add_parser("mock-agent"); mock.add_argument("--host", default="127.0.0.1"); mock.add_argument("--port", type=int, default=8765); mock.add_argument("--root", default=".rdklink-device"); mock.add_argument("--mock", action="store_true")
    dev = sub.add_parser("device"); dev.add_argument("action", choices=["list", "info"])
    project = sub.add_parser("project"); project.add_argument("action", choices=["list", "status", "push", "run"]); project.add_argument("name", nargs="?"); project.add_argument("--local-path"); project.add_argument("--remote-path", default="project"); project.add_argument("--command", default="python3 main.py")
    serial = sub.add_parser("serial"); serial.add_argument("action", choices=["list", "tail", "write", "wait"]); serial.add_argument("port", nargs="?"); serial.add_argument("value", nargs="?"); serial.add_argument("--baudrate", type=int, default=115200); serial.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    if args.group == "mock-agent":
        agent = Agent(args.root); agent.mock_serial = args.mock; asyncio.run(agent.serve(args.host, args.port)); return
    s = ServiceClient()
    if args.group == "device": out = s.call("device_list") if args.action == "list" else s.call("device_info")
    elif args.group == "project":
        if args.action == "list": out = s.call("project_list")
        elif args.action == "status": out = s.call("project_status", {"project": args.name or "project"})
        elif args.action == "push": out = s.call("project_push", {"local_path": args.local_path or ".", "remote_path": args.remote_path})
        else: out = s.call("project_run", {"command": args.command, "cwd": args.remote_path})
    elif args.action == "list": out = s.call("serial_list", {})
    elif args.action == "tail": out = s.call("serial_tail", {"port": args.port, "baudrate": args.baudrate})
    elif args.action == "write": out = s.call("serial_write", {"port": args.port, "data": args.value or ""})
    else: out = s.call("serial_wait", {"port": args.port, "pattern": args.value or "", "timeout": args.timeout, "baudrate": args.baudrate})
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
