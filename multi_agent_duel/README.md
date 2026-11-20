# 多智能体博弈 · multi_agent_duel

红 / 蓝两个 SwarmBrain Bot 同图 ZvZ，仪表盘与 `mass` 同构（OODA 四环 + 行动流水线），页眉可切 **红方视角 / 蓝方视角 / 整体对比**。

## 与 mass 同时开前端时的端口

| 服务 | mass（人机） | multi_agent_duel（红蓝） |
|------|-------------|-------------------------|
| 仪表盘 | `http://127.0.0.1:8000/dashboard/` | `http://127.0.0.1:8100/dashboard/` |
| Ask API | `8011` | `8111` |

可用环境变量覆盖：`SWARMBRAIN_DUEL_DASHBOARD_PORT`、`SWARMBRAIN_DUEL_ASK_PORT`。

## 文档

| 分类 | 路径 |
|------|------|
| 部署 | [`docs/deploy/`](./docs/deploy/) |
| 指南 | [`docs/guides/`](./docs/guides/) |
| 架构 | [`docs/architecture/`](./docs/architecture/) |

## 启动（本目录）

```powershell
..\.venv\Scripts\Activate.ps1

# 终端 A：Redis + Ask API（默认 8111）
python launch_support_stack.py
# 或：python launch_support_stack.py --skip-docker

# 终端 B：红蓝对局
$env:SC2PATH = "E:\star_carft\StarCraft II"
python swarmbrain_launch.py

# 终端 C：仪表盘（默认 8100；页眉切换红/蓝/整体对比）
python serve_dashboard.py

# 终端 D（可选）：双侧流水线
python run_dual_deerflow_pipeline.py --match-id duel-1 --replay
```

## 本包特有文件

- `duel_runtime.py` — 红蓝 Agent 规格
- `sc2_redis_keys.py` — `situation:current:red|blue` 等键
- `run_dual_deerflow_pipeline.py` — 并行双侧六阶段
- `dashboard_state_red.json` / `dashboard_state_blue.json`
- `dashboard/` — 与 mass 同构的值班台 + **红/蓝/整体对比**切换

回放默认：`videos/ZvZ_DualAgents.SC2Replay`
