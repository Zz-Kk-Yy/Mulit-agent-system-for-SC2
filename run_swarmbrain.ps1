$ErrorActionPreference = "Stop"
Write-Host @"
SwarmBrain 根目录仅作入口。请选择：

  .\run_single_agent_pve.ps1     # 单智能体 vs 电脑
  .\run_multi_agent_duel.ps1     # 红蓝双智能体互打

文档见 docs\README.md
"@
exit 2
