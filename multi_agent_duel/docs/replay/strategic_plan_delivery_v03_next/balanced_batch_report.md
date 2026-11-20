# pilot v0.3 next-collection 交付回报

- 总采集合格局数: **12**
- quarantine 局数: **25**
- 配置族: `pilot_dual_llm_v03_cover_v1`
- 配置版本: `1.0.0`×2, `1.1.0`×2, `1.3.0`×8
- 旧排除 run 未重交: `ed48f9c5`, `ff4cb329`, `5d1cc263`, `b244d8b5`
- 论文导入契约文件: `pilot_collection_manifest.csv`, `pilot_validation_report.json`
- 每局 `run_manifest.json` 中 `replay.path` 为包内相对路径 `<run_id>/replay.SC2Replay`

## 新 run ID

- `20260728T100517Z_76a595a7` (`config_version=1.0.0`)
- `20260728T103138Z_50913692` (`config_version=1.0.0`)
- `20260728T151048Z_f5252b4c` (`config_version=1.1.0`)
- `20260728T162956Z_37e8faf3` (`config_version=1.1.0`)
- `20260729T091036Z_31b03c0d` (`config_version=1.3.0`)
- `20260729T110353Z_e10b8b84` (`config_version=1.3.0`)
- `20260729T115335Z_ab2b7f1c` (`config_version=1.3.0`)
- `20260729T121200Z_34c74784` (`config_version=1.3.0`)
- `20260729T123144Z_36bdc633` (`config_version=1.3.0`)
- `20260729T134023Z_4b3757f9` (`config_version=1.3.0`)
- `20260729T141228Z_68b54282` (`config_version=1.3.0`)
- `20260729T153436Z_b8aaa23a` (`config_version=1.3.0`)

## 双视角 replay 覆盖

| run_id | header_loop | o1 | o2 | smoke | config_version |
|---|---:|---:|---:|---|---|
| `20260728T100517Z_76a595a7` | 16584 | 16584 | 16584 | pass | 1.0.0 |
| `20260728T103138Z_50913692` | 52400 | 52400 | 52400 | pass | 1.0.0 |
| `20260728T151048Z_f5252b4c` | 14690 | 14690 | 14690 | pass | 1.1.0 |
| `20260728T162956Z_37e8faf3` | 18682 | 18682 | 18682 | pass | 1.1.0 |
| `20260729T091036Z_31b03c0d` | 19768 | 19768 | 19768 | pass | 1.3.0 |
| `20260729T110353Z_e10b8b84` | 20116 | 20116 | 20116 | pass | 1.3.0 |
| `20260729T115335Z_ab2b7f1c` | 18101 | 18101 | 18101 | pass | 1.3.0 |
| `20260729T121200Z_34c74784` | 17206 | 17206 | 17206 | pass | 1.3.0 |
| `20260729T123144Z_36bdc633` | 17392 | 17392 | 17392 | pass | 1.3.0 |
| `20260729T134023Z_4b3757f9` | 21716 | 21716 | 21716 | pass | 1.3.0 |
| `20260729T141228Z_68b54282` | 26549 | 26549 | 26549 | pass | 1.3.0 |
| `20260729T153436Z_b8aaa23a` | 18650 | 18650 | 18650 | pass | 1.3.0 |

## 时间段 × 类别（SwarmBrain 侧 label_only 调用近似）

| bucket | macro | building | attack |
|---|---:|---:|---:|
| early | 4 | 20 | 8 |
| mid | 1 | 5 | 80 |
| late | 0 | 4 | 63 |

### 每单元 run 覆盖数

| bucket | macro_runs | building_runs | attack_runs |
|---|---:|---:|---:|
| early | 3 | 10 | 4 |
| mid | 1 | 2 | 12 |
| late | 0 | 2 | 12 |

## 覆盖门槛自评（相对本批，非论文合并后最终值）

- 本批仍缺失单元: `late×macro`
- 已如实记录；未通过 prompt/改标签伪造。
- 将继续用 `config_version=1.2.0` 自然补采 mid/late macro 与 building。

## Quarantine 原因

- `test_time_limit`: 17
- `replay_smoke_failed`: 3
- `runtime_not_exact_win_loss`: 2
- `validator_or_audit_failed`: 1
- `status=running`: 1
- `status=crashed`: 1

