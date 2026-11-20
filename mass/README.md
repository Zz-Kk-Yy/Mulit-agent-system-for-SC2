# MASS · 多智能体自博弈系统（人机入口）

单个 SwarmBrain Bot 对抗星际争霸 II 内置电脑（默认虫族 vs 人族 Medium），对齐 `single_agent_pve` 的启动方式。仪表盘按我方单视角展示。

## 启动（本目录）

```powershell
..\.venv\Scripts\Activate.ps1

# 终端 A：Redis + Ask API
python launch_support_stack.py
# 或：python launch_support_stack.py --skip-docker

# 终端 B：人机对局（SwarmBrain vs 内置电脑）
$env:SC2PATH = "E:\star_carft\StarCraft II"
python swarmbrain_launch.py

# 终端 C：仪表盘
python serve_dashboard.py
# http://127.0.0.1:8000/dashboard/
```

启动后终端会打印类似：

`[pve] 启动人机对局：SwarmBrain(Zerg) vs Computer(Terran, Medium)`

## 可选环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SWARMBRAIN_PVE_OPPONENT` | `computer` | `computer`：对内置电脑；`human`：人类玩家上手对 SwarmBrain |
| `SWARMBRAIN_PVE_RACE` | `Terran` | 内置电脑种族 |
| `SWARMBRAIN_PVE_DIFFICULTY` | `Medium` | 内置电脑难度 |
| `SWARMBRAIN_PVE_HUMAN_RACE` | `Terran` | 人类玩家种族（仅 `OPPONENT=human`） |

人类上手示例：

```powershell
$env:SWARMBRAIN_PVE_OPPONENT = "human"
$env:SWARMBRAIN_PVE_HUMAN_RACE = "Terran"
python swarmbrain_launch.py
```

## 说明

- `swarmbrain_launch.py` 只启动一个 SwarmBrain，对手是 `Computer`（或可选 `Human`），不再开红蓝双 Bot。
- 态势写入 `dashboard_state_red.json`，仪表盘按我方读取。
- 红蓝自博弈仍在仓库的 `multi_agent_duel/`。
