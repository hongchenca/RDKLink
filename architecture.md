# RDKLink Architecture

```text
Codex
  | MCP stdio
rdklink-mcp
  | localhost JSON-lines RPC
rdklink-service (Windows business boundary)
  | Agent RPC
rdklink-agent (RDK X5 Linux)
  |-- filesystem allowlist
  |-- process supervisor (shell=False)
  `-- shared Serial Broker + bounded ring buffer
```

The local project is the source of truth. Codex edits Windows files; `project_push` computes hashes and transfers only changed files. GUI, CLI, and MCP never open a serial port directly: they call the service, and the Agent owns the single Serial Broker connection.

Phase status:

- Phase 0: repository, packaging, logging, config, protocol and README complete.
- Phase 1: Agent health, system info and allowlisted filesystem complete.
- Phase 2: localhost Service boundary and device info complete.
- Phase 3: project add/status/manifest-style hash diff/push complete.
- Phase 4: process run/stop/output complete.
- Phase 5: serial broker list/open/close/tail/read-since/wait/write complete, with mock transport tests.
- Phase 6: MCP stdio tools complete and tested through Service to Agent.
- Phase 7: PySide6 six-tab GUI shell complete; actions call ServiceClient.

The transport is intentionally JSON-lines in this MVP so it runs on a clean Python installation. `proto/rdklink.proto` is the stable gRPC migration contract.
