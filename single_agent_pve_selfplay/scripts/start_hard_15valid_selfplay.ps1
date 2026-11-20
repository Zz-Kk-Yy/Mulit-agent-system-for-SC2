# 自博弈 × 人机 Hard（窗口「艰难」）15 场有效局。
# 检查：.\start_hard_15valid_selfplay.ps1
# 开打：.\start_hard_15valid_selfplay.ps1 -Run
param(
    [switch]$Run,
    [switch]$Check,
    [string]$ExpDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Repo = Split-Path -Parent $Root
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Script = Join-Path $PSScriptRoot "run_hard_3valid_selfplay_experiment.py"

if (-not (Test-Path $Python)) { throw "missing $Python" }
if ($Root -notmatch 'single_agent_pve_selfplay$') { throw "this launcher is not in single_agent_pve_selfplay" }

$env:SWARMBRAIN_BOARD_GG_NOT_WIN = "1"
$env:SWARMBRAIN_MANUAL_QUIT = "0"
$env:SWARMBRAIN_MATCH_TIME_LIMIT_SEC = "1800"
$env:SWARMBRAIN_DISABLE_FEATURE_LAYER = "1"
$env:SWARMBRAIN_DIFFICULTY = "Hard"
$env:SC2PATH = if ($env:SC2PATH) { $env:SC2PATH } else { "E:\star_carft\StarCraft II" }

Set-Location $Root
$forward = @(
    "--valid-games", "15",
    "--max-attempts", "30",
    "--arm", "baseline_selfplay_reflect_hard_valid15_noboard"
)
if ($ExpDir) { $forward += @("--exp-dir", $ExpDir) }
if ($Run) {
    & $Python -u $Script --run @forward
    exit $LASTEXITCODE
}
& $Python -u $Script --check @forward
exit $LASTEXITCODE
