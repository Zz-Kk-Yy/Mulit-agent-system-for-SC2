# SwarmBrain 下一轮数据采集任务：pilot v0.3 补采与平衡

## 1. 任务目标

为论文工程重新采集可完整解析的自然对局，修复以下两个问题：

1. 上一批存在 4 局 replay 的 player-view 实际覆盖仅到 game loop 4/5，无法用于论文数据集；
2. 当前候选数据中 `macro / building / attack` 与 early/mid/late 时间段高度耦合，模型可以仅凭时间位置猜测标签。

本任务不是要求强行生成指定标签，也不是修改旧日志，而是采集新的完整对局，并提高不同阶段中自然战略行为的覆盖。

## 2. 必须重新采集的 4 局

以下旧 run 已被论文工程排除：

| 旧 run_id | replay header 末 loop | player-view 实际覆盖 |
|---|---:|---:|
| `20260727T074148Z_ed48f9c5` | 23584 | 5 |
| `20260727T125458Z_ff4cb329` | 28063 | 4 |
| `20260727T132849Z_5d1cc263` | 17336 | 5 |
| `20260727T223412Z_b244d8b5` | 44955 | 4 |

处理要求：

- 旧 run 不得修补或重新交付；
- 每次重采必须生成全新的 `run_id`；
- replay、日志、manifest 和校验报告必须来自同一次新对局；
- 4 局只是最低补采数量。若无法满足第 3 节的数据覆盖目标，应继续采集。

## 3. 优先补齐的数据覆盖

论文工程当前 16 局候选集的窗口分布为：

| 时间段 | macro | building | attack |
|---|---:|---:|---:|
| early（60–180 秒） | 28 | 132 | 0 |
| mid（210–360 秒） | 0 | 0 | 192 |
| late（390–480 秒） | 0 | 2 | 126 |

下一轮应优先增加以下五个缺失单元：

1. `early × attack`
2. `mid × macro`
3. `mid × building`
4. `late × macro`
5. `late × building`

合并论文正式 8 局和全部新增有效局后，最低目标为：

- `macro / building / attack` 各不少于 120 个窗口；
- 每一类至少来自 6 个不同 run；
- 最大类别窗口数与最小类别窗口数之比不超过 2.0；
- early/mid/late 每个时间段内，三类各不少于 10 个窗口；
- 每个“时间段 × 类别”单元至少来自 3 个不同 run；
- 建议每个“时间段 × 类别”单元达到 20 个以上窗口。

这些是论文侧最终验收目标，不要求单局严格轮换三种 channel。

## 4. 采集方式

### 4.1 必须遵守

- 使用正常的 LLM 与 LLM 对战配置；
- 两方均按照真实可见局势自然决策；
- 保留完整的 LLM 调用、计划、命令、过滤、下发、执行和时间戳日志；
- `macro / building / attack` 必须记录真实 channel，不得事后修改；
- `llm_call_completed.response_completed_game_loop` 必须是真实完成时间；
- `strategic_plan` 继续使用闭集字段，并保持 `feature_scope=label_only`；
- opening 和系统审计记录继续使用 `audit_only`，不得参与窗口标签；
- 每局至少覆盖到游戏时间 480 秒，除非在 480 秒前自然结束且 replay 能完整解析到真实终局；
- 对局必须自然结束，且恰好一方 Victory、一方 Defeat。

### 4.2 可以采用的自然覆盖方法

可以通过提高局势多样性来增加缺失单元，例如：

- 使用多种合法开局或策略模板，但不预先指定最终 channel 标签；
- 调整 LLM 查询频率，使 early、mid、late 均有有效决策记录；
- 保持 macro、building 和 attack 模块在各阶段均可被局势触发；
- 避免进入中期后永久只调用 attack；
- 避免进入后期后停止 macro 和 building 决策；
- 保留“不攻击”“继续运营”“补经济”“补科技或建筑”等真实决策，即使命令最终被过滤或未下发；
- 若某次 LLM 决策无有效命令，应如实记录，不得伪造成有效标签。

## 5. 禁止事项

- 禁止在 prompt 中写“本局必须输出 macro/building/attack”；
- 禁止按目标比例轮流写入 channel；
- 禁止事后修改 LLM 输出、channel、时间戳或战略标签；
- 禁止复制窗口或将同一局伪装成多个 run；
- 禁止通过延长时间戳制造 late 样本；
- 禁止把 opening、失败调用、`command_filtered` 或 `audit_only` 记录作为有效窗口标签；
- 禁止重复使用旧 replay 并更换 `run_id`；
- 禁止交付 API key、Authorization header 或其他敏感信息。

## 6. replay 交付前硬验收

SwarmBrain 必须在交付前实际启动 StarCraft II，对每个 replay 分别执行玩家 1 和玩家 2 视角解析。

每局必须同时满足：

- replay 文件存在且 SHA-256 与 manifest 一致；
- replay build 与论文环境兼容；
- replay header 中的 final game loop 合理；
- 玩家 1 视角可正常启动并持续解析；
- 玩家 2 视角可正常启动并持续解析；
- 两个视角的实际覆盖均达到 replay 真实终局；
- 对于超过 480 秒的对局，两个视角均至少覆盖到 game loop 10752；
- 不得出现 header 显示数万 loop、player-view 实际只到 loop 4/5 的情况；
- runtime 胜负与 replay 胜负一致；
- validator 和 replay smoke 均为 `pass`。

建议在 SwarmBrain 侧为每局保存以下 smoke 结果：

```json
{
  "run_id": "<new_run_id>",
  "replay_final_game_loop": 0,
  "observer_1_last_game_loop": 0,
  "observer_2_last_game_loop": 0,
  "observer_1_status": "pass",
  "observer_2_status": "pass",
  "covers_480_sec": true,
  "runtime_replay_result_consistent": true
}
```

任一 observer 失败时，该 run 必须进入 quarantine，不能放入正式交付清单。

## 7. 单局交付内容

每个新 run 至少包含：

```text
<new_run_id>/
  run_manifest.json
  run_events.jsonl
  llm_events.jsonl
  command_events.jsonl
  errors.jsonl
  validation_summary.json
  replay_smoke_summary.json
  replay.SC2Replay
```

其中：

- 所有文件中的 `run_id` 必须一致；
- replay 文件名可以不同，但 manifest 中必须使用交付包内的相对路径；
- 所有 JSONL 必须逐行可解析；
- `errors.jsonl` 无错误时可以为空文件，但必须存在；
- 原始日志不得因为生成汇总报告而删除。

## 8. 批次汇总文件

交付根目录应增加：

```text
balanced_batch_manifest.csv
balanced_batch_report.json
balanced_batch_report.md
```

`balanced_batch_manifest.csv` 至少包含：

```text
run_id
status
replay_sha256
replay_final_game_loop
observer_1_last_game_loop
observer_2_last_game_loop
runtime_result
replay_result
early_macro
early_building
early_attack
mid_macro
mid_building
mid_attack
late_macro
late_building
late_attack
validator_status
replay_smoke_status
notes
```

`balanced_batch_report.md` 应明确汇报：

- 总采集局数；
- pass 局数；
- quarantine 局数及原因；
- 新 run ID 列表；
- 双视角 replay 覆盖结果；
- 每个“时间段 × 类别”单元的窗口数和 run 数；
- 是否达到本任务的最低覆盖门槛；
- 某一缺失类别是否在自然对局中长期无法出现。

## 9. 论文工程接收判定

论文工程收到数据后将重新执行：

```text
ingest
→ 双视角 parse_replays
→ 构建隔离候选数据集
→ 检查类别与时间分布
→ baseline / time-only / causal temporal
→ 按 run bootstrap
→ Stage C 泄漏审计
```

只有以下条件全部满足，才会冻结 `pilot_v0.3`：

1. 所有计入额度的 run 均通过 ingest 和双视角 replay 解析；
2. 类别总量、类别比例和“时间段 × 类别”覆盖达到第 3 节门槛；
3. Stage C 信息泄漏审计通过；
4. replay 特征模型能够稳定超过 time-only 对照；
5. 按 run bootstrap 的结果支持该差异；
6. 新版本使用独立 fingerprint 和 split pin，不覆盖 `pilot_v0.2`。

若门槛仍未通过，论文工程会保留本批数据作为候选诊断集，但不会将其作为论文主结果。

