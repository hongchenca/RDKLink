param(
  [int]$Port = 8765,
  [string]$Root = "$PSScriptRoot\..\.rdklink-device"
)

$project = Resolve-Path "$PSScriptRoot\.."
py -3 -m rdklink.cli mock-agent --host 127.0.0.1 --port $Port --root $Root --mock
