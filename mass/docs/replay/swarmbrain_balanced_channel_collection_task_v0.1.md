# SwarmBrain 平衡 channel 数据采集任务 v0.1

## 0. 任务定位

本任务交给 SwarmBrain 工程执行，用于为论文工程补充新的、可复核的自然对局，降低 `macro / building / attack` channel 标签与时间位置之间的耦合，并改善类别不平衡。

本任务不是让模型强行输出指定标签，也不是修改旧日志。必须采集新的完整对局，并保留真实 LLM 计划、命令、时间戳和 replay。

论文侧当前正式数据 `swarmbrain_pilot_v0.2`：

| 项目 | 当前值 |
|---|---:|
| 有效对局 | 8 |
| 窗口数 | 240 |
| attack | 160 |
| building | 70 |
| macro | 10 |
| early（60–180s） | building / macro 为主 |
| mid（210–360s） | 全部 attack |
| late（390–480s） | 全部 attack |

这会使模型仅凭 `cutoff_sec` 就能猜测标签，因此必须优先修复数据分布。

## 1. 交付目标

### 1.1 数量

- 必达：新增 **12 局**完整、自然结束、可双视角解析的新 run；
- 建议：新增 **16 局**，使合并后总量达到 24 局；
- 失败、崩溃、replay 不可读、胜负矛盾的 run 不计入额度。

新 run 必须使用新的 `run_id`，不得覆盖或修改旧 run。

### 1.2 合并后的最低分布门槛

本仓接入并生成窗口后，合并正式 8 局和本批新局，至少满足：

- `macro / building / attack` 三类各不少于 **120 个窗口**；
- 每一类至少出现在 **6 局**中；
- 最大类别窗口数 / 最小类别窗口数不超过 **2.0**；
- 每个时间段 `early / mid / late` 中，三类各至少 **10 个窗口**，目标为各 **20 个以上**；
- 每个时间段中三类标签至少来自 **3 局不同 run**。

时间段定义：

```text
early: 60–180s
mid:   210–360s
late:  390–480s
```

这些是数据质量门槛，不代表必须让每局严格轮换三类计划。

## 2. 采集原则

### 必须遵守

1. 使用正常的 `pilot_dual_llm_v1` 或等价正式配置；
2. 允许 LLM 根据局势自然形成 macro、building、attack 计划；
3. 保留所有原始 LLM 调用、计划、命令、过滤和下发日志；
4. `label_only` 记录必须能按 `response_completed_game_loop <= cutoff_game_loop` 对齐；
5. 多 motif 或证据不足时记录 `ambiguous / unknown`，不得硬选类别；
6. 每局 replay 必须覆盖论文使用的 480 秒末窗。

### 禁止事项

- 禁止 prompt 硬编码“本局必须输出 macro/building/attack”；
- 禁止为了凑比例修改已生成的 `strategic_plan` 或时间戳；
- 禁止复制窗口、重复 run 或把同一局拆成多个独立游戏；
- 禁止把 `audit_only`、`command_filtered`、失败调用当作有效意图标签；
- 禁止交付 API key、Authorization header 或其他敏感信息；
- 禁止把旧失败 run 通过补丁方式恢复为新 run。

## 3. 单局硬验收

每个新 run 必须全部满足：

- `status=completed`，自然结束而非 smoke 超时；
- `runtime_result` 恰好一方 Victory、一方 Defeat；
- `runtime_result` 与 replay 胜负一致；
- validator 为 `pass` 且无双 Victory / 双 Defeat warning；
- replay 文件存在，manifest SHA-256 一致；
- `replay_final_game_loop >= 10752`，覆盖 480 秒窗口；
- 论文仓 `parse_replays.py` 双 observer 均成功；
- 双视角事件覆盖所有 cutoff，不能在 loop≈5 等异常位置断流；
- macro/building/attack 的 `label_only` 生命周期完整且时间一致；
- opening 或 audit 日志不得污染 cutoff 标签；
- 交付包中无敏感凭据。

## 4. 交付目录

请使用与旧候选集隔离的新目录，例如：

```text
strategic_plan_delivery_balanced_v0.1/
  <new_run_id>/
    run_manifest.json
    run_events.jsonl
    llm_events.jsonl
    command_events.jsonl
    errors.jsonl
    validation_summary.json
    <replay>.SC2Replay
  balanced_batch_manifest.csv
```

`balanced_batch_manifest.csv` 至少包含：

```text
run_id,status,replay_sha256,replay_final_game_loop,
runtime_result,replay_result,early_counts,mid_counts,late_counts,
macro_windows,building_windows,attack_windows,notes
```

## 5. 论文仓接入流程

收到交付后，仅处理新的 `run_id`：

```powershell
.\.venv\Scripts\python.exe .\scripts\ingest_swarmbrain_runs.py `
  --delivery-root .\strategic_plan_delivery_balanced_v0.1

.\.venv\Scripts\python.exe .\scripts\parse_replays.py `
  --sc2-path "E:\star_carft\StarCraft II" `
  --run-id <new_run_id>

.\.venv\Scripts\python.exe .\scripts\build_intent_dataset.py --force
.\.venv\Scripts\python.exe .\scripts\build_intent_feature_matrix.py
.\.venv\Scripts\python.exe .\scripts\train_intent_baseline.py
.\.venv\Scripts\python.exe .\scripts\train_causal_intent_model.py
.\.venv\Scripts\python.exe .\scripts\train_time_only_baseline.py
.\.venv\Scripts\python.exe .\scripts\report_run_bootstrap_ci.py
```

只有在以下条件全部满足后，才考虑冻结新的 `pilot_v0.3`：

1. 新数据通过 ingest、双视角 replay 解析和 intent 数据集审计；
2. 上述类别与时间段分布门槛通过；
3. Stage C 泄漏审计 `passed`；
4. time-only 模型明显低于 replay 模型，不能接近完整模型；
5. 新版本 fingerprint、run 列表和 LOGO split 被单独冻结，不覆盖 `pilot_v0.2`。

## 6. 交付回报

请同时提交：

- 新 run ID 列表；
- 每局 replay 结果与 runtime 结果；
- 每局 replay final game loop；
- early/mid/late 的三类窗口计数；
- validator 汇总；
- 失败或 quarantine run 及原因；
- 是否发现某一类别在自然对局中长期缺失。

如果自然对局无法满足某一类别，应如实记录缺口，不能通过 prompt 或手工修改标签补齐。
