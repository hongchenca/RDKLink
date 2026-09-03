from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from .models import AgentConfig
from .protocol import request
from .config import ConfigStore
from .activity import ActivityLog


class RdkLinkService:
    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig(
            host=os.environ.get("RDKLINK_AGENT_HOST", "127.0.0.1"),
            port=int(os.environ.get("RDKLINK_AGENT_PORT", "8765")),
        )
        self.config_store = ConfigStore()
        self.activity = ActivityLog(self.config_store.base / "logs" / "rdklink-service.log")

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        timeout = self.config.timeout
        if method == "serial_wait":
            timeout = max(timeout, min(float((params or {}).get("timeout", 10)) + 2, 125))
        summary = {k: (f"<{len(str(v))} chars>" if k == "content" else v) for k, v in (params or {}).items()}
        try:
            value = request(self.config.host, self.config.port, method, params, timeout)
            self.activity.record(os.environ.get("RDKLINK_SOURCE", "SYSTEM"), method, self.config.host, summary, value.get("ok", True), int((time.monotonic() - started) * 1000))
            return value
        except Exception:
            self.activity.record(os.environ.get("RDKLINK_SOURCE", "SYSTEM"), method, self.config.host, summary, False, int((time.monotonic() - started) * 1000))
            raise

    def status(self) -> dict[str, Any]:
        return self.call("ping")

    def device_list(self) -> dict[str, Any]:
        return {"ok": True, "devices": [self.status()]}

    def device_info(self) -> dict[str, Any]:
        return self.call("device_info")

    def project_list(self) -> dict[str, Any]:
        return {"ok": True, "projects": self.config_store.load("projects.yaml", [])}

    def project_add(self, name: str, local_path: str, remote_path: str, run_command: str = "python3 main.py") -> dict[str, Any]:
        projects = self.config_store.load("projects.yaml", [])
        item = {"name": name, "local_path": str(Path(local_path).resolve()), "remote_path": remote_path, "run_command": run_command}
        projects = [p for p in projects if p.get("name") != name]
        projects.append(item)
        self.config_store.save("projects.yaml", projects)
        return {"ok": True, "project": item}

    def project_status(self, project: str = "project") -> dict[str, Any]:
        return self.call("project_status", {"path": project})

    def project_push(self, local_path: str, remote_path: str = "project", delete: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        root = Path(local_path).resolve()
        if not root.is_dir():
            raise ValueError(f"local_path is not a directory: {root}")
        ignored = {".git", ".venv", "__pycache__", "node_modules"}
        local_files = {}
        for path in root.rglob("*"):
            if not path.is_file() or any(part in ignored for part in path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            data = path.read_bytes()
            local_files[rel] = (hashlib.sha256(data).hexdigest(), data)
        remote_files = set(self.call("list_files", {"path": remote_path}).get("files", []))
        changed = []
        uploaded_bytes = 0
        for rel, (digest, data) in local_files.items():
            target = f"{remote_path.rstrip('/')}/{rel}"
            current = self.call("read_file", {"path": target}) if target in remote_files else None
            if not current or current.get("sha256") != digest:
                self.call("write_file", {"path": target, "content": data.decode("utf-8")})
                changed.append(rel)
                uploaded_bytes += len(data)
        removed = []
        if delete:
            for target in sorted(remote_files):
                prefix = remote_path.rstrip("/") + "/"
                rel = target[len(prefix):] if target.startswith(prefix) else None
                if rel and rel not in local_files:
                    self.call("delete_file", {"path": target})
                    removed.append(rel)
        return {"ok": True, "changed": changed, "removed": removed, "files": len(local_files), "uploaded": len(changed), "bytes": uploaded_bytes, "duration_ms": int((time.monotonic() - started) * 1000)}

    def project_run(self, command: str, cwd: str = "project") -> dict[str, Any]:
        return self.call("run_process", {"command": command, "cwd": cwd})

    def project_stop(self, pid: int) -> dict[str, Any]:
        return self.call("stop_process", {"pid": pid})

    def serial_tail(self, max_lines: int = 100, port: str | None = None, baudrate: int = 115200) -> dict[str, Any]:
        return self.call("serial_tail", {"max_lines": max_lines, "port": port, "baudrate": baudrate})

    def serial_wait(self, contains: str, timeout: float = 10, max_lines: int = 100, port: str | None = None, baudrate: int = 115200) -> dict[str, Any]:
        return self.call("serial_wait", {"contains": contains, "timeout": timeout, "max_lines": max_lines, "port": port, "baudrate": baudrate})
