# 单一智能体 · PvE（single_agent_pve）

单个 SwarmBrain Bot 对抗星际 II 内置电脑（默认 Terran Medium）。可改为人机对战。

## 文档

| 分类 | 路径 |
|------|------|
| 部署 | [`docs/deploy/`](./docs/deploy/) |
| 使用指南 | [`docs/guides/`](./docs/guides/) |
| 需求说明 | [`docs/requirements/`](./docs/requirements/) |

## 启动（本目录）

```powershell
# 使用仓库根 .venv 亦可
..\.venv\Scripts\Activate.ps1

# 终端 A（可选）：Redis + Ask API（需 Docker）
python launch_support_stack.py
# 或 Redis 已启动：python launch_support_stack.py --skip-docker

# 终端 B（必开）：对局
$env:SC2PATH = "E:\star_carft\StarCraft II"
python swarmbrain_launch.py

# 终端 C（可选）：仪表盘
python serve_dashboard.py
# http://127.0.0.1:8000/dashboard/
```

## 本包特有文件

- `swarmbrain_launch.py` — Bot vs `Computer`（非红蓝互打）
- `dashboard/` — 单视角仪表盘（无红蓝对战条）
- `dashboard_state.json` — 运行时态势

回放默认：`videos/ZvT_Medium.SC2Replay`
