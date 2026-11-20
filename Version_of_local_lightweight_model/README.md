# 本地轻量模型联调 · SwarmBrain

从 [`single_agent_pve/`](../single_agent_pve/) 复制的可运行框架，用来接服务器上的 `Qwen3-4B-Thinking`。联调事实和完成标准见同目录 [`本地智能体任务_20261101.md`](./本地智能体任务_20261101.md)。

未带过来的运行时产物：`.env`、`experiments/`、`logs/`、`videos/`、`__pycache__`、`dashboard_state.json`。密钥放本目录 `.env`（从 `.env.example` 复制），不要提交。

不调用模型的一局用规则入口。地图、难度、出生方向和开局都在 [`configs/match.json`](./configs/match.json)：虫族对人族内置 AI，难度 Medium，出生方向 `auto`，开局后按实际出生点匹配配置里的矿点标签。

本地规则只做四件事：农民采矿、人口不够补王虫、血池后女王注卵、敌军进入防御半径才补刺蛇并迎击。`plan.actions` 保持为空，表示维持这套计划。

```powershell
cd Version_of_local_lightweight_model
.\run_local_rules.ps1
```

打完后看 `outputs/local_rule/<game_id>/`：`local_record.json` 有地图校验值、出生方向和失败点，`observation_sample.json` 是局面样本。回放在 `videos/local_rule_zvt_medium.SC2Replay`。

第一次联调只发一条已保存的 Observation，不打开新对局：

```powershell
$env:PYTHONIOENCODING = "utf-8"
python local_joint_debug.py
```

脚本先访问 `http://127.0.0.1:8000/v1/models`。看不到 `swarmbrain-qwen3-4b` 就停。看得到才发送 `max_tokens=2048` 的一条补全。判定和完整响应写到 `outputs/joint_debug/<request_id>/joint_debug_record.json`。`reasoning_content` 只留档。

原来的 `swarmbrain_launch.py` 仍会在开局调用 DashScope `qwen3-max`，这条线和规则入口分开。

```powershell
.\run_swarmbrain.ps1
```

SC2 默认路径：`E:\star_carft\StarCraft II`。

仪表盘 `serve_dashboard.py` 默认监听 `127.0.0.1:8000`。服务器模型经 SSH 隧道也占用本机 `127.0.0.1:8000`。联调时不要把仪表盘绑在 8000。
