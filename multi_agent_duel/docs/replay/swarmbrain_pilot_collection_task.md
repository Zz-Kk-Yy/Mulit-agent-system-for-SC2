# SwarmBrain 双 LLM Pilot 对局采集任务

## 0. 任务信息

| 字段 | 内容 |
|---|---|
| 任务状态 | 已完成（S1/S2/S3）；5 局合格已交付 |
| 任务版本 | v1.0.0 |
| 执行工程 | `E:\star_carft\SwarmBrain_re` |
| 目标 | 生成 5 局可交付给论文工程的正常双 LLM pilot 对局 |
| 执行方式 | S1 修改与测试 → S2 单局验收 → S3 补齐 5 局 |
| 上位文档 | `docs/swarmbrain_pilot_dataset_task.md` |
| S2 合格样本 | `20260721T095812Z_fe2fb72f` |
| 交付目录 | `docs/replay/pilot_delivery/` |
| 完成报告 | `docs/replay/pilot_collection_complete_20260721.md` |

本任务只负责 SwarmBrain 工程内的配置、日志、对局采集和校验。

明确不负责：

```text
ingest_swarmbrain_runs.py
parse_replays.py
align_llm_intents.py
论文前缀样本生成
论文数据划分
模型训练
```

这些工作由论文工程后续完成。不要在 SwarmBrain 工程中创建上述脚本。

---

## 1. 已通过基线

以下能力已通过前期 smoke test，修改时不得回归：

- 每局具有唯一 `run_id`；
- JSONL 的 `event_id` 唯一；
- opening 三次且具有 `opening_index=1/2/3`；
- shared opening 使用 `agent_side=shared` 和 `feature_scope=audit_only`；
- LLM started/completed/failed 可以通过 `llm_call_id` 配对；
- 红方固定 player 1，蓝方固定 player 2；
- parsed commands 与 proposed commands 数量一致；
- `source=llm` 的命令具有有效 `llm_call_id`；
- command proposed/accepted/filtered/dispatched/succeeded/skipped 可追溯；
- 正常结束时 replay 路径、大小、SHA256 可验证；
- replay header 的 build、data version 和 final loop 可解析；
- 崩溃时不会写虚假的 `replay_saved`；
- 崩溃时 active LLM 调用和 pending commands 可以正确收尾；
- 日志不包含 API key、Authorization 或 Bearer token。

以下 run 只作为测试 fixture，不属于本任务的 5 局 pilot：

```text
20260721T090738Z_ea95b14e
20260721T091921Z_506cb5c2
```

---

## 2. S1：冻结正式采集配置

### 2.1 目标

从“快速覆盖日志通道的 smoke 配置”切换为“正常双 LLM 对局配置”。

### 2.2 Pilot 禁止使用的 smoke 设置

以下设置仅用于此前的日志覆盖测试：

```text
SWARMBRAIN_ATTACK_EVERY=40
SWARMBRAIN_ATTACK_MIN_ARMY=0
SWARMBRAIN_MAX_GAME_TIME_SEC=420
```

pilot 不得通过以下方式人为制造数据：

- 无兵力也触发 attack planner；
- 极短间隔高频调用 attack；
- 到 420 秒强制 Tie；
- 为覆盖 channel 而改变正常策略；
- 把 `Undecided` replay 当成正常胜负数据。

### 2.3 建立版本化配置

建议新增：

```text
configs/pilot_dual_llm_v1.yaml
```

配置中冻结：

```text
realtime / non-realtime
macro planner 正常间隔
building planner 正常间隔
attack planner 正常间隔
attack 正常最低兵力条件
最大安全运行时间
模型 provider
模型名称
temperature/top_p 等非敏感参数
prompt template version
地图
双方种族
随机种子或种子生成策略
fallback 策略版本
```

具体值使用当前 SwarmBrain 的正常策略默认值或已经确认的生产值，不自行发明新的策略参数。

### 2.4 Manifest 配置字段

每个 run 的 `run_manifest.json` 必须记录：

```text
config_name
config_version
config_sha256
runtime_config
model_provider
model_name
prompt_template_version
random_seed
code_git_commit
```

无法获得时不要无解释地写 `null`，应使用：

```json
{
  "value": null,
  "unavailable_reason": "明确原因"
}
```

不得保存 API key 或完整 `.env`。

### 2.5 正常结束定义

合格 pilot 应优先产生：

```text
Victory / Defeat
```

以下结果不计入 5 局合格额度：

```text
run_crashed
startup_failed
replay 保存失败
replay header 解析失败
test_time_limit 导致的 Tie
replay result = Undecided
异常短局
任一方全程 fallback
```

允许保留最大安全时限防止进程无限运行，但触发安全时限的局进入 quarantine。

### 2.6 S1 配置验收

- [ ] 已建立 `pilot_dual_llm_v1` 配置；
- [ ] 不再使用 `ATTACK_MIN_ARMY=0`；
- [ ] 不再使用高频 attack smoke 周期；
- [ ] 不以短时限 Tie 作为正常结果；
- [ ] manifest 保存配置名称、版本和 SHA256；
- [ ] 随机种子或生成规则可追溯；
- [ ] 模型和 prompt 版本可追溯；
- [ ] 配置和日志不包含认证信息。

---

## 3. S1：明确异步 LLM 时间字段

### 3.1 背景

异步任务可能基于较早的游戏状态生成 prompt，但在稍晚时间才真正发送请求和获得回答。不能只用一个含义不明确的 `game_loop` 表示全部时间。

### 3.2 推荐字段

对非 opening LLM 调用保存：

```text
observation_game_loop
observation_game_time_sec
request_started_game_loop
request_started_game_time_sec
response_completed_game_loop
response_completed_game_time_sec
```

含义：

| 字段 | 含义 |
|---|---|
| observation | 构造 prompt 输入状态快照的时间 |
| request started | LLM 请求实际开始发送的时间 |
| response completed | 回答完成并可被程序使用的时间 |

### 3.3 兼容现有 schema

如果本轮不修改字段名，至少必须冻结以下语义并写入 schema 文档：

```text
llm_call_started.game_loop
    = observation_game_loop

llm_call_completed.started_game_loop
    = observation_game_loop

llm_call_completed.game_loop
    = response_completed_game_loop
```

如果请求因队列等待而延迟，建议额外记录实际 `request_started_game_loop`。

### 3.4 时间约束

- 同一调用的 response completed loop 不得早于 observation loop；
- completed/failed 必须继承同一调用的 agent/player/channel；
- opening 使用 `phase=pregame`，game loop 保持 `null`；
- 不要求异步 JSONL 文件中的所有事件按 game loop 绝对递增；
- 但每个字段的含义必须明确且可通过 `llm_call_id` 重建。

### 3.5 S1 时间验收

- [ ] observation 与 response completed 时间可区分；
- [ ] request 延迟可以记录或明确说明；
- [ ] completed loop 不早于 observation loop；
- [ ] completed/failed 时间字段完整；
- [ ] opening 仍保持 pregame 语义；
- [ ] validator 按 `llm_call_id` 校验，不依赖文件行顺序。

---

## 4. S1：完善日志校验器

目标脚本：

```text
scripts/validate_run_logs.py
```

### 4.1 文件与 ID

- [ ] 五个规定文件存在；
- [ ] JSON/JSONL 均可解析；
- [ ] `run_id` 一致；
- [ ] `event_id` 存在且唯一；
- [ ] schema version 一致。

### 4.2 LLM 调用

- [ ] started 有且只有一个 completed 或 failed；
- [ ] agent/player/race/channel 在同一调用中不变化；
- [ ] prompt 与 raw response 非空；
- [ ] prompt SHA256 正确；
- [ ] 非 opening 时间字段完整；
- [ ] parsed commands 与 proposed commands 数量一致；
- [ ] fallback 与正常 LLM 输出可区分。

### 4.3 Opening

- [ ] opening 恰好三次；
- [ ] `opening_index=1/2/3`；
- [ ] `phase=pregame`；
- [ ] `agent_id=shared_opening` 时 `agent_side=shared`；
- [ ] shared opening 使用 `audit_only`；
- [ ] opening game loop/time 为 null。

### 4.4 Command

- [ ] `source=llm` 必须具有有效 `llm_call_id`；
- [ ] dispatched 必须有相同 command ID 的 proposed 和 accepted；
- [ ] 正常结束后 accepted 必须有 dispatched/failed/skipped；
- [ ] dispatched 必须有 succeeded/failed/skipped 或明确仅能确认 dispatched；
- [ ] orphan command 为 0；
- [ ] 过滤原因不能全部使用模糊的 `filtered`。

### 4.5 Replay 与 Manifest

- [ ] `status=completed` 时 replay 文件存在；
- [ ] replay 文件大小一致；
- [ ] SHA256 一致；
- [ ] replay header 可以解析；
- [ ] base build/data version 一致；
- [ ] replay final loop 与 manifest 一致；
- [ ] `last_observed_game_loop` 与 replay final loop 分字段保存；
- [ ] 保存失败时使用 `replay_save_failed`，禁止写 `replay_saved`；
- [ ] runtime result 与 replay result 分开保存；
- [ ] `termination_reason` 存在。

### 4.6 敏感信息

- [ ] 不包含 API key；
- [ ] 不包含 Authorization/Bearer header；
- [ ] 不包含 `.env` 原始内容；
- [ ] 错误堆栈经过脱敏。

### 4.7 校验器输出

输出必须包括：

```text
run_id
validation_status
error_count
warning_count
failed_rules
replay_sha256
base_build
data_version
replay_final_game_loop
各 channel 调用数
fallback/failed 数量
command 生命周期统计
```

任何阻断规则失败时返回非零退出码。

---

## 5. S1：代码修改与本地测试交付

S1 只完成代码和配置，不立即批量采集 5 局。

### 5.1 允许修改

根据实际工程结构，允许修改：

```text
configs/pilot_dual_llm_v1.yaml
swarmbrain_run_logger.py
swarmbrain_launch.py
scripts/validate_run_logs.py
相关 tests
```

### 5.2 不允许修改

- 不改变论文工程文件；
- 不创建 `parse_replays.py`；
- 不创建数据集划分代码；
- 不创建模型训练代码；
- 不删除历史 fixture；
- 不修改历史 JSONL 伪造通过结果。

### 5.3 S1 测试

至少执行：

- logger 单元测试；
- manifest 原子更新测试；
- LLM 三类时间测试；
- command provenance 测试；
- replay header 校验测试；
- 敏感信息扫描测试；
- expected crash 校验测试。

### 5.4 S1 完成定义

- [ ] 配置已冻结；
- [ ] 时间语义已冻结；
- [ ] validator 已更新；
- [ ] 自动测试通过；
- [ ] 没有开始批量采集；
- [ ] 输出修改文件列表和测试结果。

S1 完成后停止，先进行 S2 单局验收。

---

## 6. S2：采集并验收 1 局正常 Pilot

### 6.1 运行要求

使用：

```text
pilot_dual_llm_v1
```

只运行 1 局，不立即连续运行 5 局。

### 6.2 必需产物

```text
logs/runs/<run_id>/
├── run_manifest.json
├── run_events.jsonl
├── llm_events.jsonl
├── command_events.jsonl
└── errors.jsonl

videos/<包含run_id的文件名>.SC2Replay
```

### 6.3 单局合格条件

- `status=completed`；
- 正常 Victory/Defeat；
- 不是 test time limit Tie；
- replay result 不是 Undecided；
- replay 路径存在；
- SHA256 与文件一致；
- replay header 与 manifest 一致；
- base build/data version 可用；
- player 身份零错配；
- 双方产生正常 macro/building；
- 所有 LLM 调用闭环；
- parsed/proposed 一致；
- orphan command 为 0；
- accepted command 有合理后续；
- fallback/failed 已明确标记；
- 无敏感信息；
- validator 退出码为 0。

### 6.4 单局快速人工核对

人工确认：

- manifest 的配置是 pilot 而非 smoke；
- attack 没有在零兵力条件下被强制高频调用；
- observation 和 response completed 时间含义正确；
- 红蓝双方调用频率没有明显的身份串线；
- replay 能正常打开或静态解析；
- 结束原因与胜负合理。

### 6.5 S2 失败处理

如果任一阻断条件失败：

- 该局进入 quarantine；
- 保留全部原始文件；
- 记录失败规则；
- 停止后续 4 局采集；
- 返回 S1 修复并重新运行单局。

### 6.6 S2 完成定义

- [ ] 1 局正常 pilot validator 通过；
- [ ] replay 和日志可一一关联；
- [ ] 结果为有效胜负；
- [ ] 人工快速核对通过；
- [ ] 明确允许进入 S3。

---

## 7. S3：补齐至 5 局合格 Pilot

只有 S2 通过后才能执行。

### 7.1 采集数量

继续采集，直到累计：

```text
5 局合格正常对局
```

失败/quarantine 对局不计入 5 局。

### 7.2 每局必须记录

```text
run_id
采集时间
config name/version/SHA256
random seed
地图和双方种族
模型和 prompt 版本
replay path/SHA256
base build/data version
replay final loop/duration
runtime result/replay result
termination reason
各 channel 调用数
fallback/failed 数量
command 统计
validator 结果
是否合格
排除原因
```

### 7.3 去重要求

- 5 个 run ID 必须不同；
- 5 个 replay SHA256 必须不同；
- replay 文件名必须包含 run ID 或等价唯一标识；
- 已有文件禁止静默覆盖；
- 相同 SHA256 视为重复样本，不计入 5 局。

### 7.4 调度与公平性审计

每局比较：

```text
red vs blue macro 次数
red vs blue building 次数
red vs blue attack 次数
red vs blue LLM latency
red vs blue command accepted/success rate
```

如果多局持续出现无法解释的严重不对称，停止采集并检查调度。

需要重点排查：

```text
iteration % interval == 0
```

与异步任务 busy 状态组合后是否导致错过触发点。必要时改为基于 `next_due_loop` 的调度，并记录 `planner_trigger_skipped`。

### 7.5 S3 完成定义

- [ ] 累计 5 局合格正常对局；
- [ ] 5 局全部使用同一冻结配置版本；
- [ ] 5 个 replay SHA256 均不同；
- [ ] 所有 validator 退出码为 0；
- [ ] 没有 Tie/Undecided/crash 混入合格集；
- [ ] 所有 quarantine 样本有原因；
- [ ] 没有身份、时间或 command provenance 回归；
- [ ] 已生成采集清单。

---

## 8. 向论文工程交付的内容

SwarmBrain 只负责交付，不负责在论文工程中处理。

### 8.1 每局交付

```text
<run_id>/
├── run_manifest.json
├── run_events.jsonl
├── llm_events.jsonl
├── command_events.jsonl
├── errors.jsonl
└── replay.SC2Replay
```

如果 replay 不复制进 run 目录，必须保证 manifest 中路径有效，并单独提供 replay 文件。

### 8.2 汇总清单

建议输出：

```text
pilot_collection_manifest.csv
```

至少包含：

```text
run_id
config_name
config_version
config_sha256
random_seed
model_name
prompt_template_version
replay_file_name
replay_sha256
base_build
data_version
map_name_runtime
map_name_replay
replay_final_game_loop
replay_duration_sec
player_1_race
player_2_race
runtime_result_p1
runtime_result_p2
replay_result_p1
replay_result_p2
termination_reason
llm_call_count
fallback_count
validation_status
```

### 8.3 校验输出

同时提供每局 validator 输出，或汇总：

```text
pilot_validation_report.json
```

### 8.4 禁止交付

- `.env`；
- API key；
- Authorization/Bearer header；
- 未脱敏请求日志；
- 被手工修改过的历史 JSONL；
- 未说明原因的失败样本。

---

## 9. 最终执行清单

### S1：修改与测试

1. [ ] 建立 `pilot_dual_llm_v1` 配置；
2. [ ] 恢复正常 planner 周期和 attack 条件；
3. [ ] 冻结 observation/request/completed 时间语义；
4. [ ] 更新 `validate_run_logs.py`；
5. [ ] 完成自动测试；
6. [ ] 输出修改和测试报告；
7. [ ] 停止并等待 S2。

### S2：单局验收

8. [ ] 运行 1 局正常 pilot；
9. [ ] 验证 replay SHA256/header；
10. [ ] 运行 validator；
11. [ ] 快速人工核对；
12. [ ] 通过后明确允许 S3。

### S3：补齐 5 局

13. [ ] 继续采集并逐局校验；
14. [ ] 累计 5 局合格对局；
15. [ ] 检查 replay/run 去重；
16. [ ] 检查红蓝调度对称性；
17. [ ] 生成 collection manifest；
18. [ ] 生成 validation report；
19. [ ] 将 5 局 run、replay 和报告交付论文工程；
20. [ ] 停止，不在 SwarmBrain 中实现论文解析和训练代码。

---

## 10. 完成定义

本任务完成必须同时满足：

1. 已冻结非 smoke 的双 LLM pilot 配置；
2. 异步 LLM 时间语义明确且可重建；
3. validator 覆盖身份、时间、command、replay 和敏感信息；
4. 已先通过 1 局单局验收；
5. 已累计 5 局正常 Victory/Defeat 对局；
6. 5 局日志和 replay 全部校验通过；
7. 5 个 replay SHA256 均不同；
8. replay build、data version、final loop 和时长可追溯；
9. 不存在身份错配、orphan command 或未解释的 accepted command；
10. 不存在 Tie/Undecided/crash 混入合格集；
11. 红蓝调度不存在未解释的系统性偏差；
12. 已生成采集清单和校验报告；
13. 已向论文工程交付 5 局数据；
14. 未在 SwarmBrain 工程中越界实现论文数据解析或模型训练。

完成后，SwarmBrain 阶段停止。后续由论文工程执行数据导入、双视角 replay 解析、LLM 标签对齐和 pilot 数据审计。
