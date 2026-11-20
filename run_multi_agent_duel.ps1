# Launch multi-agent duel from repo root
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "multi_agent_duel")
if (Test-Path "..\.venv\Scripts\Activate.ps1") { & "..\.venv\Scripts\Activate.ps1" }
elseif (Test-Path ".\.venv\Scripts\Activate.ps1") { & ".\.venv\Scripts\Activate.ps1" }
if (-not $env:SC2PATH) { $env:SC2PATH = "E:\star_carft\StarCraft II" }
if (-not $env:SWARMBRAIN_SC2PATH) { $env:SWARMBRAIN_SC2PATH = $env:SC2PATH }
python swarmbrain_launch.py
