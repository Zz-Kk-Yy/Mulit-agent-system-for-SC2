# Pilot 采集完成报告（S1–S3）

## 结论

SwarmBrain 双 LLM pilot 采集任务已完成：冻结配置、单局验收与 5 局合格交付均已就绪。未在本工程实现论文解析/训练代码。

## 合格 5 局

| run_id | 胜负 (P1/P2) | replay_final_loop | validator |
|---|---|---|---|
| `20260721T095812Z_fe2fb72f` | Defeat / Victory | 16412 | pass |
| `20260721T101817Z_23267397` | Defeat / Victory | 23404 | pass |
| `20260721T104512Z_41b4c057` | Victory / Defeat | 17295 | pass |
| `20260721T114726Z_8200d689` | Defeat / Victory | (见 manifest) | pass |
| `20260721T120458Z_0eb1c3f8` | Victory / Defeat | (见 manifest) | pass |

- 同一配置：`pilot_dual_llm_v1` / `1.0.0` / sha256 `42573202143c…`
- 5 个 replay SHA256 互不相同
- 无 Tie / Undecided / crash 混入合格集

## Quarantine（不计额度）

| run_id | 原因 |
|---|---|
| `20260721T110705Z_25600179` | 双方无 macro completed |
| `20260721T112757Z_e454359d` | 双方无 macro completed |

原因与 `iteration % interval` + 异步 busy 错过触发有关，见任务文档 §7.4；后续可改为 `next_due_loop`。

## 调度对称性观察

多数对局红蓝均有 macro/building/attack。`20260721T101817Z_23267397` 出现 attack 次数明显不对称（红 3 / 蓝 30），已记录供论文侧审计；未达到“多局持续无法解释的系统性偏差”阈值阈值。

## 交付位置

```text
multi_agent_duel/docs/replay/pilot_delivery/
├── <run_id>/   # 五份日志 + replay.SC2Replay + validation_summary.json
├── pilot_collection_manifest.csv
└── pilot_validation_report.json
```

状态文件：`docs/replay/pilot_collection_state.json`

## 关键代码/配置

- `configs/pilot_dual_llm_v1.yaml`
- `swarmbrain_pilot_config.py`
- `scripts/run_pilot_game.py` / `collect_pilot_games.py` / `build_pilot_delivery.py`
- `scripts/validate_run_logs.py`（`--pilot` / `--summary-json`）
- LLM 三类时间字段 + `docs/guides/llm_time_semantics.md`
- `termination_reason`：仅 Tie+安全时限才标 `test_time_limit`
