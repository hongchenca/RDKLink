# Codex MCP Setup

Start the Windows service and the RDK agent first. For local development:

```powershell
py -3 -m rdklink.cli mock-agent --port 8765 --mock
py -3 -m rdklink.service_server --agent-port 8765 --port 8766
```

Register the stdio server in the Codex MCP configuration. Keep paths machine-local; do not hard-code a user directory:

```json
{
  "mcpServers": {
    "rdklink": {
      "command": "py",
      "args": ["-3", "-m", "rdklink.mcp_server"],
      "env": {
        "RDKLINK_SERVICE_HOST": "127.0.0.1",
        "RDKLINK_SERVICE_PORT": "8766"
      }
    }
  }
}
```

The intended debugging sequence is:

1. `rdk_project_push` with the Windows project path.
2. `rdk_serial_open` with `baudrate: 115200`.
3. `rdk_project_run`.
4. `rdk_serial_wait` for `READY`.
5. On timeout, call `rdk_serial_tail` and `rdk_process_output`.

All reads are bounded. The MCP server exposes no shell execution tool.
