# SwarmBrain · 自博弈子反思副本

本目录是 `single_agent_pve/` 的副本，**只改了会出指令的 Overmind 提示词**。

| 目录 | 提示词 | 用途 |
|---|---|---|
| `single_agent_pve/` | **未改** | 继续打基线对照局 |
| `single_agent_pve_selfplay/` | 子反思四步 | 本对照臂 |

决策流程（每轮可见）：

1. **A 我方草案** — 先给出本窗口方案  
2. **B 预测对手下一步** — 根据可见情报预测人族下一手  
3. **C 反思纠正** — 对照预测修正草案  
4. **D 收敛指令** — 仍输出 `(单位, 地点)->(动作)->(目标)`  

未改：`overmind_brain_initial` / `overmind_brain_initial2`（兵力目标与 True/False 倾向）、地图标签、指令解析、launch 执行层。

窗口：普通窗口 1280x720（非全屏）。恢复全屏可设 `SWARMBRAIN_FULLSCREEN=1`。

单局测试：

```powershell
..\.venv\Scripts\python.exe -u .\scripts\run_baseline_winrate_experiment.py --games 1 --difficulty Medium --arm selfplay_reflect_test --skip-smoke
```

10 局窗口化：

```powershell
..\.venv\Scripts\python.exe -u .\scripts\run_baseline_winrate_experiment.py --games 10 --difficulty Medium --arm selfplay_reflect --skip-smoke
```
