# balanced_channel_collection_v0.1 交付回报

| 字段 | 内容 |
|---|---|
| 任务 | `swarmbrain_balanced_channel_collection_task_v0.1` |
| 配置 | `pilot_dual_llm_v1` |
| 合格局数 | **12**（必达） |
| Quarantine | 16 |
| 交付目录 | `docs/replay/strategic_plan_delivery_balanced_v0.1/` |
| 清单 | `balanced_batch_manifest.csv` / `balanced_batch_report.json` |

## 1. 新 run ID 列表

- `20260727T053433Z_e32679e5`
- `20260727T074148Z_ed48f9c5`
- `20260727T125458Z_ff4cb329`
- `20260727T132849Z_5d1cc263`
- `20260727T184900Z_4dc725b3`
- `20260727T190105Z_63411f3b`
- `20260727T194536Z_a7e29b4c`
- `20260727T195836Z_f99da222`
- `20260727T223412Z_b244d8b5`
- `20260727T232251Z_53a15527`
- `20260727T234016Z_7e316595`
- `20260728T043933Z_2d4d7810`

## 2. 每局结果与 loop

| run_id | runtime | replay | final_loop | dur_s | validator | macro | building | attack |
|---|---|---|---:|---:|---|---:|---:|---:|
| `20260727T053433Z_e32679e5` | `{"player_1": "Victory", "player_2": "Defeat"}` | `{"player_1": "Win", "player_2": "Loss"}` | 15385 | 686.8 | pass | 6 | 13 | 21 |
| `20260727T074148Z_ed48f9c5` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 23584 | 1052.9 | pass | 4 | 7 | 31 |
| `20260727T125458Z_ff4cb329` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 28063 | 1252.8 | pass | 3 | 22 | 59 |
| `20260727T132849Z_5d1cc263` | `{"player_1": "Victory", "player_2": "Defeat"}` | `{"player_1": "Win", "player_2": "Loss"}` | 17336 | 773.9 | pass | 2 | 6 | 25 |
| `20260727T184900Z_4dc725b3` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 11979 | 534.8 | pass | 6 | 10 | 9 |
| `20260727T190105Z_63411f3b` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 26872 | 1199.6 | pass | 6 | 11 | 41 |
| `20260727T194536Z_a7e29b4c` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 13920 | 621.4 | pass | 2 | 4 | 20 |
| `20260727T195836Z_f99da222` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 18212 | 813.0 | pass | 5 | 10 | 28 |
| `20260727T223412Z_b244d8b5` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 44955 | 2006.9 | pass | 7 | 20 | 90 |
| `20260727T232251Z_53a15527` | `{"player_1": "Victory", "player_2": "Defeat"}` | `{"player_1": "Win", "player_2": "Loss"}` | 17104 | 763.6 | pass | 3 | 11 | 22 |
| `20260727T234016Z_7e316595` | `{"player_1": "Defeat", "player_2": "Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 21620 | 965.2 | pass | 6 | 14 | 33 |
| `20260728T043933Z_2d4d7810` | `{"player_1": "Victory", "player_2": "Defeat"}` | `{"player_1": "Win", "player_2": "Loss"}` | 16736 | 747.1 | pass | 6 | 13 | 22 |

## 3. early / mid / late 三类调用计数（SwarmBrain 侧近似）

按 `label_only` 完成调用的 `game_time_sec` 落入论文时段统计（**不是**论文仓最终窗口数）：

| bucket | macro | building | attack |
|---|---:|---:|---:|
| early | 2 | 15 | 0 |
| mid | 0 | 0 | 53 |
| late | 1 | 2 | 37 |

全时段合计调用：macro=56, building=141, attack=401

### 自然分布缺口（如实记录）

- mid（210–360s）在本批自然对局中 **全部为 attack**（macro/building=0）。
- late（390–480s）几乎全是 attack；仅偶然出现 macro=1、building=2。
- early 有少量 building/macro；更多 building/macro 落在 60s 前或时段间隙。
- **未**通过 prompt 硬编码或事后改标签补齐；论文侧窗口门槛需 ingest 后再审计。

## 4. Quarantine（不计入额度）

原因汇总：

- `test_time_limit`: 11
- `validator_or_audit_failed`: 3
- `runtime_not_exact_win_loss`: 2

主要失败模式：`max_game_time_sec=2700` → `test_time_limit` → 双边 Tie/Undecided（按任务拒绝）。

## 5. 验收门禁（本批均满足）

- status=completed，自然结束（非 smoke）
- runtime 恰好一方 Victory / 一方 Defeat，且与 replay 一致
- `replay_final_game_loop >= 10752`（≥480s）
- validator pass + strategic_plan audit pass
- 新 run_id，未覆盖旧 `strategic_plan_delivery`

## 6. Quarantine 明细表（可追溯）

| run_id | status | quarantine_reason | termination_reason | runtime_result | replay_result | replay_final_game_loop | validator_status | failed_rules | notes |
|---|---|---|---|---|---|---:|---|---|---|
| `20260727T055433Z_7d24b100` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T081310Z_2dde1b69` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T094458Z_b0fef1a8` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T111103Z_862114d5` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T134914Z_6c3c9644` | quarantined | `runtime_not_exact_win_loss:{'player_1': 'Result.Victory', 'player_2': 'Result.Victory'}` |  | `{"player_1": "Result.Victory", "player_2": "Result.Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` |  |  | `[]` |  |
| `20260727T135542Z_069ce6ed` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T151507Z_4e076d2d` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T161646Z_862fb2b4` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T173428Z_c99d3b46` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260727T192647Z_f75b1b20` | quarantined | `validator_or_audit_failed` |  | `{"player_1": "Result.Victory", "player_2": "Result.Defeat"}` | `{"player_1": "Win", "player_2": "Loss"}` | 18919 | fail | `["pilot missing red/macro completed calls", "pilot missing blue/macro completed calls"]` |  |
| `20260727T201741Z_a455fc8c` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Tie", "player_2": "Tie"}` |  |  | `[]` |  |
| `20260727T215832Z_b346896e` | quarantined | `validator_or_audit_failed` |  | `{"player_1": "Result.Defeat", "player_2": "Result.Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 32580 | fail | `["pilot missing red/macro completed calls", "pilot missing blue/macro completed calls"]` |  |
| `20260728T000345Z_7ba83357` | quarantined | `runtime_not_exact_win_loss:{'player_1': 'Result.Victory', 'player_2': 'Result.Tie'}` |  | `{"player_1": "Result.Victory", "player_2": "Result.Tie"}` | `{"player_1": "Win", "player_2": "Loss"}` |  |  | `[]` |  |
| `20260728T014457Z_2f965848` | quarantined | `validator_or_audit_failed` |  | `{"player_1": "Result.Defeat", "player_2": "Result.Victory"}` | `{"player_1": "Loss", "player_2": "Win"}` | 17670 | fail | `["pilot missing red/macro completed calls"]` |  |
| `20260728T020307Z_6b02b1d5` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |
| `20260728T032256Z_f2e4e778` | quarantined | `test_time_limit:test_time_limit` | test_time_limit | `{"player_1": "Result.Tie", "player_2": "Result.Tie"}` | `{"player_1": "Undecided", "player_2": "Undecided"}` |  |  | `[]` |  |

说明：quarantine 仅出现在本回报中，不进入 `pilot_collection_manifest.csv`。
