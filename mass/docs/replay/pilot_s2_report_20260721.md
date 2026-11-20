# Pilot S2 单局验收报告

## 结论

S2 **通过**。允许进入 S3。

| 字段 | 值 |
|---|---|
| run_id | `20260721T095812Z_fe2fb72f` |
| config | `pilot_dual_llm_v1` / `1.0.0` |
| config_sha256 | `42573202143c8c593592b3f7b9c8e295b9afb83aab3c96b8e28a09df0ee74b60` |
| status | completed |
| termination_reason | natural_end（曾被误标为 test_time_limit，已按胜负结果纠正；launch 已修） |
| runtime_result | P1 Defeat / P2 Victory |
| replay_result | P1 Loss / P2 Win |
| replay_final_game_loop | 16412（≈12.2 min） |
| attack_min_army | 16（非 smoke） |
| validator | pass=29 fail=0 |

## 人工核对

- [x] manifest 为 pilot 配置而非 smoke
- [x] attack 非零兵力强制高频
- [x] observation / response_completed 可区分
- [x] 红蓝均有 macro/building/attack
- [x] replay 可解析，胜负合理

## 修复备注

`swarmbrain_launch.py`：仅当结果为 Tie（且配置了安全时限）时标记 `test_time_limit`；出现 Victory/Defeat 记为 `natural_end`。
