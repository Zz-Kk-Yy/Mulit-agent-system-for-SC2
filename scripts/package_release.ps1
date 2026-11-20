# 打包 single_agent_pve 到 dist\ 并输出 zip 到 docs\packaging\
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $repoRoot) { $repoRoot = (Get-Location).Path }
Set-Location -LiteralPath $repoRoot

$py = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
    $py = "python"
}

& $py (Join-Path $PSScriptRoot "build_package.py")
