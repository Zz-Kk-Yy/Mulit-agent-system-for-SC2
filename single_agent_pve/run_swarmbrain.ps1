$ErrorActionPreference = "Stop"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 进入脚本所在目录，避免从别的目录启动时报错
Set-Location -Path $PSScriptRoot

if (!(Test-Path ".\.venv\Scripts\Activate.ps1")) {
  throw "未找到 .venv。请先在项目根目录创建虚拟环境并安装依赖：python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt"
}

.\.venv\Scripts\Activate.ps1

# 你的 SC2 安装路径（按本机修改；也可在 .env 中配置 SC2PATH/SWARMBRAIN_SC2PATH）
$env:SC2PATH = "D:\StarCraft II"

# 大模型 Key/模型/接口：默认从同目录 .env 读取，无需在此重复设置。
# 若要在本脚本里强制覆盖，请取消下面三行注释并填入：
# $env:SWARMBRAIN_LLM_API_KEY = "你的真实Key"
# $env:SWARMBRAIN_LLM_MODEL = "qwen3-max"
# $env:SWARMBRAIN_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

python swarmbrain_launch.py
