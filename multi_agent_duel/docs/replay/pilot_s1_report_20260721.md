# Pilot S1 完成报告（2026-07-21）

## 状态

S1（配置冻结 / 时间语义 / validator / 测试）已完成。按任务文档，下一步为 **S2 单局验收**，不得在未通过 S2 前宣称 5 局合格。

## 修改文件

| 文件 | 说明 |
|---|---|
| `configs/pilot_dual_llm_v1.yaml` | 冻结正式采集配置（禁止 smoke 参数） |
| `swarmbrain_pilot_config.py` | 加载/应用配置、SHA256、manifest 字段 |
| `swarmbrain_run_logger.py` | observation / request_started / response_completed 时间字段 |
| `swarmbrain.py` | invoke 层写入三类时间 |
| `swarmbrain_run_log_hooks.py` | begin 时冻结 observation |
| `swarmbrain_launch.py` | `SWARMBRAIN_PILOT=1` 时加载 pilot 配置 |
| `scripts/validate_run_logs.py` | `--pilot`、`--summary-json`、时间语义与汇总输出 |
| `scripts/run_pilot_game.py` | 单局 pilot 启动器 |
| `scripts/build_pilot_delivery.py` | 交付目录 / CSV / validation report |
| `docs/guides/llm_time_semantics.md` | 时间语义冻结说明 |
| `tests/test_run_logger.py` | 时间字段与 smoke 拒绝测试 |

## 冻结配置摘要

```text
config_name=pilot_dual_llm_v1
macro_every=120
building_every=60
attack_every=150
attack_min_army=16
realtime=false
max_game_time_sec=2700   # 仅安全上限；触发则 quarantine，不计合格
```

## 测试结果

```text
python -m unittest tests.test_run_logger -v
-> 6 tests OK
```

## 启动 S2

```powershell
$env:SWARMBRAIN_PILOT = "1"
python scripts/run_pilot_game.py
# 结束后
python scripts/validate_run_logs.py --run-dir logs/runs/<run_id> --expect-status completed --pilot --summary-json docs/replay/pilot_s2_<run_id>.json
```
