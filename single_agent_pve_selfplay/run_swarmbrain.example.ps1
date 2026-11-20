$ErrorActionPreference = "Stop"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
Set-Location -Path $PSScriptRoot

if (!(Test-Path ".\.venv\Scripts\Activate.ps1")) {
    throw "未找到 .venv。请先执行: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt"
}

.\.venv\Scripts\Activate.ps1

# 星际 II 安装路径（按本机修改）
$env:SC2PATH = "C:\Program Files (x86)\StarCraft II"

# 大模型 API Key（必填；勿将填好 Key 的脚本提交到公开仓库）
$env:SWARMBRAIN_LLM_API_KEY = "请在此处填写你的 Key"

# 可选
# $env:SWARMBRAIN_LLM_MODEL = "qwen3-max"
# $env:SWARMBRAIN_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

python swarmbrain_launch.py
