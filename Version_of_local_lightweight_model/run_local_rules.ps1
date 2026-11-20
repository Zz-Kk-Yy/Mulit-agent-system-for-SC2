$ErrorActionPreference = "Stop"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
Set-Location -Path $PSScriptRoot

if (Test-Path "..\.venv\Scripts\Activate.ps1") {
  & "..\.venv\Scripts\Activate.ps1"
} elseif (Test-Path ".\.venv\Scripts\Activate.ps1") {
  & ".\.venv\Scripts\Activate.ps1"
} else {
  throw "未找到 .venv。请在仓库根目录创建：python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt"
}

$env:PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION = "python"
$env:PYTHONIOENCODING = "utf-8"
if (-not $env:SC2PATH) { $env:SC2PATH = "E:\star_carft\StarCraft II" }
if (-not $env:SWARMBRAIN_SC2PATH) { $env:SWARMBRAIN_SC2PATH = $env:SC2PATH }

python local_rule_launch.py
