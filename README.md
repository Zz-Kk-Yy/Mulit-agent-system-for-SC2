# Multi-agent System

星际争霸 II LLM 智能体工程。**对局代码只在模式目录中**，根目录仅保留入口脚本、文档与工具。

| 路径 | 用途 |
|------|------|
| [`single_agent_pve/`](./single_agent_pve/) | **单一智能体** vs 电脑/人类（PvE），自包含可运行 |
| [`multi_agent_duel/`](./multi_agent_duel/) | **红蓝双智能体** 互打 + 双侧 DeerFlow，自包含可运行 |
| [`Version_of_local_lightweight_model/`](./Version_of_local_lightweight_model/) | 不调用模型的规则骨架，以及服务器 Qwen3-4B 联调说明 |
| [`docs/`](./docs/) | 文档（architecture / deploy / guides / packaging / agents） |
| [`new_agents/`](./new_agents/) | 子智能体模板与数据契约 |
| [`scripts/`](./scripts/) | 整理 / 打包脚本 |
| [`dist/`](./dist/) | 历史部署快照 |

## 启动

```powershell
# 建议在仓库根创建一次 venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 单智能体
.\run_single_agent_pve.ps1
# 或：cd single_agent_pve; python swarmbrain_launch.py

# 多智能体
.\run_multi_agent_duel.ps1
# 或：cd multi_agent_duel; python swarmbrain_launch.py
```

支撑栈（Redis / Ask API）与仪表盘请在**对应模式目录**内启动，例如：

```powershell
cd single_agent_pve
python launch_support_stack.py --skip-docker
python serve_dashboard.py
```

## 说明

根目录不再放置与模式包重复的 `swarmbrain.py`、`dashboard/`、`mcp_server/` 等运行时文件；请始终进入模式目录开发与运行。
