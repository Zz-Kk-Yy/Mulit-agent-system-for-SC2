# strategic_plan 验收与采集清单（2026-07-24）

## 交付物核对

| 交付物 | 路径 | 状态 |
|---|---|---|
| Schema 说明 | `docs/guides/strategic_plan_schema.md` | 已齐 |
| 成功样例 | `docs/examples/strategic_plan_success_*.json` | 已齐 |
| 解析失败样例 | `docs/examples/strategic_plan_failed_*.json` | 已齐 |
| 单测 | `tests/test_run_logger.py`（含非法枚举、format 安全、多 motif→ambiguous） | 通过 |
| 校验脚本 | `scripts/validate_run_logs.py`（含 strategic_plan 规则） | 已齐 |
| 抽查审计 | `scripts/audit_strategic_plan.py` | 已齐 |

## 烟测验收标准与结果

| 标准 | 结果 |
|---|---|
| 成功 `llm_call_completed`（ingame label_only）≥95% 含合法 `strategic_plan` | **通过**（现行 7 局均 100% coverage） |
| `primary_intent` 仅闭集 | **通过** |
| `feature_scope=label_only` | **通过** |
| `channel` 仍存在且不为意图值 | **通过** |
| building 抽查：不塌缩为全 `tech_climb`；多目标宜见 `ambiguous` | **功能门通过**（tech_climb=0）；质量门见下 |

### 烟测局

| run_id | 角色 | 结果 |
|---|---|---|
| `20260724T031633Z_63139e59` | 烟测1 | quarantine（prompt `.format` KeyError，已修） |
| `20260724T042407Z_623bc227` | 烟测2 / 正式包第1局 | PASS，coverage 56/56 |

## 正式包（7 局，2026-07-26 替换后）

交付目录：`docs/replay/strategic_plan_delivery/`

配置：`pilot_dual_llm_v1`。门禁（已写入 validator/collector）：**禁止 runtime 双 Victory**；**replay 本仓可解析且 duration≥480s**；保留 `strategic_plan`。

### 保留局

| run_id | coverage | dur | runtime |
|---|---|---|---|
| `20260724T042407Z_623bc227` | 56/56 | 905s | Defeat/Victory |
| `20260724T051235Z_5df2f7dc` | 43/43 | 841s | Victory/Defeat |
| `20260725T112524Z_60644f11` | 44/44 | 1591s | Victory/Defeat |
| `20260725T125059Z_e90359f4` | 70/70 | 948s | Defeat/Victory |

### 替换局（替代失败 id）

| 新 run_id | 替代 | coverage | dur | runtime | strategic 摘要 |
|---|---|---|---|---|---|
| `20260726T085811Z_96bbed30` | `045540Z_0e0d77b2` | 35/35 | 718s | Defeat/Victory | mid_timing×7 + ambiguous×11 |
| `20260726T091810Z_4e86be4b` | `065446Z_f43d3182` | 56/56 | 1385s | Victory/Defeat | mid_timing×19 + ambiguous×16 |
| `20260726T125512Z_73cd1a56` | `110418Z_42749965` | 41/41 | 1313s | Victory/Defeat | mid_timing×10 + ambiguous×9 |

### 已剔除失败 id

| run_id | 原因 |
|---|---|
| `20260724T045540Z_0e0d77b2` | runtime 双 Victory |
| `20260724T065446Z_f43d3182` | 双 Victory + replay 273s&lt;480 |
| `20260725T110418Z_42749965` | runtime 双 Victory |

### 稀缺四类（tech_climb / air_harass_prep / defensive_macro / mid_timing_attack）

- 替换 3 局合计：`mid_timing_attack=36`；其余三类本轮未自然出现 → 多目标处标 `ambiguous`，**未造假**。

### Quarantine（含历史）

| run_id | 原因 |
|---|---|
| `20260724T031633Z_63139e59` | prompt_format_KeyError |
| `20260724T053451Z_248e2b24` | crashed_sc2_gone_mid_attack |
| `20260724T064101Z_effac1f5` | crashed_sc2_killed_mid_game |
| 上述 3 个双 Victory / &lt;480s | 已替换 |

## 质量说明

前几局在 motif 调和上线前采集，building 偏 `fast_expand`。上线后：

1. prompt 强调多目标 → `ambiguous`；鼓励稀缺四类仅在证据充分时使用
2. `reconcile_intent_with_commands()`：多 motif 混杂时改为 `ambiguous`（不静默改写为具体类）

## 交付物路径速查

- 包：`docs/replay/strategic_plan_delivery/`（含各局 `run_manifest.json` / `llm_events.jsonl` / replay）
- 清单：`pilot_collection_manifest.csv`、`pilot_validation_report.json`
- 状态：`docs/replay/strategic_plan_collection_state.json`
