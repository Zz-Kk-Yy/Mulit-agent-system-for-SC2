# 双智能体对局 + 支撑栈（示例）
# 用法：在 multi_agent_duel 目录下分别开终端执行
# 端口：Ask API 8111，仪表盘 8100（与 mass 的 8011/8000 错开）

Write-Host "1) 终端 A: .\launch_ask_stack.ps1   # Ask API -> http://127.0.0.1:8111"
Write-Host "2) 终端 B: python swarmbrain_launch.py"
Write-Host "3) 终端 C: python serve_dashboard.py   # 仪表盘 -> http://127.0.0.1:8100/dashboard/"
Write-Host "4) 终端 D: python run_dual_deerflow_pipeline.py --match-id duel-1 --replay"
