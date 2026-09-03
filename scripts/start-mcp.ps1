param(
  [string]$Host = "127.0.0.1",
  [int]$Port = 8765
)

$env:RDKLINK_AGENT_HOST = $Host
$env:RDKLINK_AGENT_PORT = "$Port"
py -3 -m rdklink.mcp_server

