# 单终端：Docker Redis + Ask API。.env / 环境变量中配置 SWARMBRAIN_LLM_API_KEY。
# Redis 态势同步已在 swarmbrain_launch.py 后台线程执行；仪表盘请自行 python serve_dashboard.py（如需网页）。
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$venvPy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Write-Host "未找到 .venv，请先创建虚拟环境并 pip install -r requirements.txt" -ForegroundColor Red
  exit 1
}

$extra = $args
& $venvPy "$here\launch_support_stack.py" @extra
