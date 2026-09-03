# RDKLink

RDKLink 是 Windows 本地控制平面，通过 MCP、CLI 和 PySide6 GUI 管理 RDK X5。Windows 本地项目是唯一源码真源：Codex 修改本地文件，再调用 `rdk_project_push` 同步到设备。

## Architecture

```text
Codex -> rdklink-mcp (stdio) -> rdklink-service (localhost) -> rdklink-agent (RDK X5)
GUI/CLI ------------------------^                         -> Serial Broker
```

完整边界和阶段状态见 [architecture.md](architecture.md)。

## Windows Installation

```powershell
py -3 -m pip install -e .
py -3 -m pip install -e ".[gui,serial]"
```

配置保存在 `%USERPROFILE%\.rdklink\`：`config.yaml`、`devices.yaml`、`projects.yaml` 和 `logs/`。不要把凭据写进项目仓库。

## RDK X5 Installation

将该工程或 `rdklink` 包部署到 X5，使用 Python 3 安装，然后启动：

```bash
python3 -m pip install .
rdklink-agent --host 0.0.0.0 --port 8765 --root /userdata/projects
```

使用 `config/agent.yaml` 作为 allowlist 策略参考。Agent 默认无 shell 执行工具，文件操作只允许 `--root` 或额外 `--allowed-path` 范围。

## Mock Quick Start

终端 1：

```powershell
rdklink-agent --host 127.0.0.1 --port 8765 --root .rdklink-device --mock
```

终端 2：

```powershell
rdklink-service --agent-host 127.0.0.1 --agent-port 8765 --port 8766
```

终端 3：

```powershell
rdklink-cli device info
rdklink-cli serial list
rdklink-cli serial write MOCK0 STATUS
rdklink-cli serial wait MOCK0 READY --timeout 10
```

Mock `MOCK0` 会生成 heartbeat，并支持 `PING -> PONG`、`STATUS -> READY`。

## Projects

添加项目由 Windows Service 持久化：

```powershell
rdklink-cli project push robot --local-path C:\test\robot --remote-path robot
rdklink-cli project run robot --remote-path robot --command "python3 main.py"
```

首次同步会传输全部文件；未改动时 `uploaded: 0`；修改一个文件后 `uploaded: 1`。同步输出含 `bytes`、`duration_ms`、`changed` 和 `removed`。

## Serial

安装实体串口依赖：

```powershell
py -3 -m pip install -r requirements-serial.txt
```

所有 GUI、MCP 和 CLI 串口操作都经过 RDK Agent 的同一个 Serial Broker。串口读取均为有界环形缓存，支持 `tail`、`read_since` 和模式等待。

## GUI

```powershell
rdklink-gui
```

需要可选依赖：`py -3 -m pip install -e ".[gui]"`。GUI 包含 Devices、Projects、Serial、Processes、Activity、Settings，所有按钮通过 Windows Service 调用业务功能。

## Codex MCP

见 [docs/codex-mcp.md](docs/codex-mcp.md)。MCP 提供有界的设备、项目、文件、进程与串口工具；不提供 unrestricted root shell。

## Tests

```powershell
py -3 -m pytest
py -3 -m compileall -q rdklink tests
```

覆盖 Agent/Service/MCP 链路、路径越界、项目 manifest diff、进程输出、Mock Serial 的 PONG/READY、串口 sequence ring buffer。

## Troubleshooting

- `DeviceOfflineError`: 确认 Agent IP/端口，以及 `RDKLINK_AGENT_HOST`/`RDKLINK_AGENT_PORT`。
- MCP 不能连接：先确认 `rdklink-service` 在 `127.0.0.1:8766`，再检查 `RDKLINK_SERVICE_PORT`。
- 实体串口失败：安装 `pyserial`，并将端口加入 Agent allowlist。
- GUI 未启动：安装 `rdklink[gui]`。

## Security

读取、进程和串口操作均有大小/记录上限。远程路径使用 allowlist，串口使用 allowlist。项目命令以 `shell=False` 启动，MCP 不暴露任意 shell、任意文件删除、重启、关机或固件升级。
