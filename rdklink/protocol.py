from __future__ import annotations

import json
import socket
from typing import Any
from .errors import DeviceOfflineError, InvalidPathError, PermissionDeniedError, ProcessNotFoundError


class ProtocolError(RuntimeError):
    pass


def request(host: str, port: int, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    payload = {"id": 1, "method": method, "params": params or {}}
    try:
        sock_ctx = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise DeviceOfflineError(f"cannot connect to agent {host}:{port}: {exc}") from exc
    with sock_ctx as sock:
        sock.settimeout(timeout)
        sock.sendall((json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"))
        buf = bytearray()
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
        if not buf:
            raise ProtocolError("agent closed connection without a response")
    response = json.loads(bytes(buf).split(b"\n", 1)[0].decode("utf-8"))
    if response.get("error"):
        error = response["error"]
        error_type = {"permission_denied": PermissionDeniedError, "invalid_path": InvalidPathError, "process_not_found": ProcessNotFoundError}.get(error.get("code"), ProtocolError)
        raise error_type(error.get("message", "agent error"))
    return response.get("result", response)
