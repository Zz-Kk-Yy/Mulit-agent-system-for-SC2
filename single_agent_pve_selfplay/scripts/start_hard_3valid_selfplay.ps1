# 自博弈 × 人机 Hard（窗口「艰难」）3 场有效局。
# 默认只做检查。真正开打：.\start_hard_3valid_selfplay.ps1 -Run
param(
    [switch]$Run,
    [switch]$Check
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
$env:SWARMBRAIN_MATCH_TIME_LIMIT_SEC = "1260"
$env:SWARMBRAIN_DISABLE_FEATURE_LAYER = "1"
$env:SWARMBRAIN_DIFFICULTY = "Hard"
$env:SC2PATH = if ($env:SC2PATH) { $env:SC2PATH } else { "E:\star_carft\StarCraft II" }

Set-Location $Root
if ($Run) {
    & $Python -u $Script --run
    exit $LASTEXITCODE
}
& $Python -u $Script --check
exit $LASTEXITCODE
