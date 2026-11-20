# SwarmBrain balanced v0.1 交付审核与修正任务

| 字段 | 内容 |
|---|---|
| 文档日期 | 2026-07-28 |
| 审核对象 | `strategic_plan_delivery_balanced_v0.1/` |
| 当前有效交付 | 12 局 completed run |
| Quarantine 回报 | 16 局：test_time_limit×11、胜负异常×2、validator/audit×3 |
| 当前结论 | 单局硬质量通过；交付格式不兼容；平衡门槛未通过；暂不冻结 `pilot_v0.3` |
| 修正原则 | 保留原始 run 和日志，不修改历史事实，不覆盖正式 `pilot_v0.2` |

## 1. 当前审核结论

### 1.1 已通过项目

论文工程对现有 12 个 run 做了只读检查，结果如下：

- 12 个唯一 `run_id`，全部 `status=completed`；
- 12/12 validator 为 `pass`；
- warning 总数为 0；
- 全部为 `termination_reason=natural_end`；
- runtime 与 replay 胜负语义一致；
- 最小 `replay_final_game_loop=11979`，均覆盖 480 秒末窗；
- 12 个 replay 的实际 SHA-256 全部与 manifest 一致；
- 每局均包含 replay、run manifest、四类 JSONL 日志和 validation summary；
- 交付目录只包含 12 个有效 run，16 个 quarantine 未混入有效交付。

因此，这 12 局不需要重新运行对局，也不得修改既有日志、时间戳、结果或 replay。

### 1.2 当前阻塞项目

论文仓现有 `ingest_swarmbrain_runs.py` 要求交付根目录包含：

```text
pilot_collection_manifest.csv
pilot_validation_report.json
```

并要求每个 run 目录中的 replay 统一命名为：

```text
replay.SC2Replay
```

当前交付使用的是：

```text
balanced_batch_manifest.csv
balanced_batch_report.json
<原始名称>.SC2Replay
```

因此当前执行：

```powershell
.\.venv\Scripts\python.exe .\scripts\ingest_swarmbrain_runs.py `
  --delivery-root .\strategic_plan_delivery_balanced_v0.1 `
  --dry-run
```

会因缺少 `pilot_collection_manifest.csv` 而失败，尚不能正式导入。

## 2. 交付格式修正

推荐生成一个新的规范化交付目录，不修改原目录：

```text
strategic_plan_delivery_balanced_v0.1_normalized/
  pilot_collection_manifest.csv
  pilot_validation_report.json
  balanced_batch_manifest.csv
  balanced_batch_report.json
  balanced_channel_delivery_report_v0.1.md
  <run_id>/
    run_manifest.json
    run_events.jsonl
    llm_events.jsonl
    command_events.jsonl
    errors.jsonl
    validation_summary.json
    replay.SC2Replay
```

### 2.1 `pilot_collection_manifest.csv`

必须一行对应一个有效 run，仅包含当前 12 局，不得包含 quarantine。

列结构必须与论文仓既有 pilot 交付兼容：

```text
run_id,config_name,config_version,config_sha256,random_seed,
model_name,prompt_template_version,replay_file_name,replay_sha256,
base_build,data_version,map_name_runtime,map_name_replay,
replay_final_game_loop,replay_duration_sec,player_1_race,player_2_race,
runtime_result_p1,runtime_result_p2,replay_result_p1,replay_result_p2,
termination_reason,llm_call_count,fallback_count,validation_status
```

其中：

- `replay_file_name` 固定写为 `replay.SC2Replay`；
- `validation_status` 必须为 `pass`；
- `replay_sha256` 必须与原 replay 内容一致；
- 不得因为重命名而重新编码或修改 replay；
- 所有字段必须从 run manifest、validation summary 或 replay 解析结果中复制，不得手工猜测。

### 2.2 `pilot_validation_report.json`

必须为 JSON 数组，每个对象对应一个有效 run，并与：

- `pilot_collection_manifest.csv`
- `run_manifest.json`
- `validation_summary.json`

具有完全相同的 `run_id` 集合。

每项至少保留：

```json
{
  "run_id": "<run_id>",
  "validation_status": "pass",
  "replay_sha256": "<sha256>",
  "base_build": 96516,
  "data_version": "<data_version>"
}
```

可以直接在 validation summary 基础上补齐，不要重新生成不同口径的胜负或 replay 信息。

### 2.3 replay 规范化

把每个有效 run 的原始 replay 原样复制到规范化目录，并重命名为：

```text
replay.SC2Replay
```

修正后必须再次核验：

```text
原 replay SHA-256 == replay.SC2Replay SHA-256
```

禁止移动或删除原始 replay；禁止覆盖原始交付目录。

## 3. 补齐 quarantine 回报

用户回报的 quarantine 为 16 局：

| 原因 | 数量 |
|---|---:|
| test_time_limit | 11 |
| 胜负异常 | 2 |
| validator/audit | 3 |
| 合计 | 16 |

但论文工程当前未找到：

```text
balanced_channel_delivery_report_v0.1.md
```

请把该文件加入规范化交付根目录，并至少列出：

```text
run_id,status,quarantine_reason,termination_reason,
runtime_result,replay_result,replay_final_game_loop,
validator_status,failed_rules,notes
```

要求：

- 16 个 quarantine 的 `run_id` 必须全部可追溯；
- quarantine 只能出现在回报中，不能出现在有效 manifest；
- `test_time_limit` 不得包装为 `natural_end`；
- 胜负异常 run 不得通过手工改结果恢复；
- validator/audit 失败必须保留原始 failed rules；
- 不要求把 quarantine 的大体积 replay 和日志复制进有效交付目录。

## 4. 当前平衡性审核

### 4.1 delivery 自带 channel 总量

当前 `balanced_batch_manifest.csv` 声明：

| channel | 数量 |
|---|---:|
| macro | 56 |
| building | 141 |
| attack | 401 |

最大类别与最小类别比例约为：

```text
401 / 56 ≈ 7.16
```

高于任务要求的 `≤2.0`。

这些数值更接近 LLM 调用级统计，不等同于论文侧最终 15-cutoff 窗口标签，因此不能直接作为最终验收结果；但它表明当前交付仍明显偏向 `attack`。

### 4.2 delivery 自带时间桶统计

聚合当前 `balanced_batch_report.json`：

| 时间段 | macro | building | attack |
|---|---:|---:|---:|
| early | 2 | 15 | 0 |
| mid | 0 | 0 | 53 |
| late | 1 | 2 | 37 |

仍表现为：

```text
early → building/macro
mid/late → attack
```

因此当前批次尚未证明标签与时间位置的耦合已经降低。

## 5. 后续新采集修正方向

现有 12 局保留并进入候选接入，不需要废弃。后续补采应重点增加：

- early 阶段自然发生的 `attack`；
- mid/late 阶段自然发生的 `macro`；
- mid/late 阶段自然发生的 `building`；
- 同一 channel 在多个不同 run、不同时间段中的自然实例。

仍然禁止：

- prompt 强制指定本局 channel；
- 每局按顺序轮换 macro/building/attack；
- 修改既有计划或时间戳来制造平衡；
- 复制同一 run 或窗口；
- 将失败调用、audit-only 或 filtered command 当作有效标签。

如果自然对局无法产生某个“类别×时间段”组合，应记录缺口，不得伪造。

## 6. 论文侧后续验收顺序

规范化交付完成后，论文工程按以下顺序执行：

### 6.1 dry-run

```powershell
.\.venv\Scripts\python.exe .\scripts\ingest_swarmbrain_runs.py `
  --delivery-root .\strategic_plan_delivery_balanced_v0.1_normalized `
  --dry-run
```

验收条件：

- 12 个 run 全部通过；
- 无 replay hash、base build、data version 或 run ID 冲突；
- quarantine 不在声明集合中。

### 6.2 正式 ingest 与双视角 replay 解析

```powershell
.\.venv\Scripts\python.exe .\scripts\ingest_swarmbrain_runs.py `
  --delivery-root .\strategic_plan_delivery_balanced_v0.1_normalized

.\.venv\Scripts\python.exe .\scripts\parse_replays.py `
  --sc2-path "E:\star_carft\StarCraft II" `
  --run-id <new_run_id>
```

12 局必须分别完成玩家 1、玩家 2 双视角解析，并覆盖 480 秒末窗。

### 6.3 构建 20 局候选数据集

把正式 `pilot_v0.2` 的 8 局与本批 12 局合并为：

```text
pilot_v0.3_candidate
```

不得直接覆盖：

```text
data/interim/pilot_v0.2/
data/splits/pilot_v0.2/
```

### 6.4 科学质量验收

使用论文侧实际窗口标签重新计算：

- macro/building/attack 总量；
- 每类出现的 run 数；
- early/mid/late 的类别矩阵；
- 最大类/最小类比例；
- time-only 对照；
- 因果时间序列模型；
- 按 run bootstrap 置信区间；
- Stage C 泄漏审计。

只有全部满足以下条件时才考虑冻结 `pilot_v0.3`：

1. 三类各不少于 120 个论文窗口；
2. 每类至少来自 6 个不同 run；
3. 最大类/最小类比例不超过 2.0；
4. early/mid/late 中三类均有跨 run 覆盖；
5. time-only 明显低于 replay/因果模型；
6. 因果模型的按 run 配对改进具有稳定证据；
7. Stage C 泄漏审计通过；
8. 单独记录新 fingerprint、run 列表和 split version。

## 7. 本轮修正完成标准

- [ ] 新建 `strategic_plan_delivery_balanced_v0.1_normalized/`；
- [ ] 补齐 `pilot_collection_manifest.csv`；
- [ ] 补齐 `pilot_validation_report.json`；
- [ ] 每局包含内容不变的 `replay.SC2Replay`；
- [ ] 加入 `balanced_channel_delivery_report_v0.1.md`；
- [ ] quarantine 16 局清单与原因可追溯；
- [ ] 原始 `strategic_plan_delivery_balanced_v0.1/` 保持不变；
- [ ] 论文侧 ingest dry-run 通过 12/12；
- [ ] 当前批次只进入候选集，不直接冻结 `pilot_v0.3`。

---

**最终结论：** 当前 12 局是合格的候选 run，主要问题是交付协议不兼容和科学分布仍不平衡。先做无损格式规范化并接入候选集，再依据论文侧窗口统计决定需要补采哪些“类别×时间段”组合。
