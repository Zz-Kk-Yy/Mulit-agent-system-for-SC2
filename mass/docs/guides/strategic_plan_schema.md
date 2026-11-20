# SwarmBrain 日志附录：strategic_plan（strategic_plan_v0.1.0）

上位需求：[`swarmbrain_strategic_plan_fields_requirements.md`](../requirements/swarmbrain_strategic_plan_fields_requirements.md)

## 写入位置

`llm_events.jsonl` → `event_type=llm_call_completed`，且：

- `feature_scope=label_only`
- `channel ∈ {macro, building, attack}`

opening / `audit_only` 不强制写入。

## 字段

见需求 §3。`schema_version` 固定为 `strategic_plan_v0.1.0`。

`primary_intent` 闭集：

```text
early_rush | fast_expand | tech_climb | air_harass_prep
mid_timing_attack | defensive_macro | ambiguous | unknown
```

禁止用 `macro` / `building` / `attack` 冒充 `primary_intent`。

## 解析规则（实现）

1. 从 raw response 提取 JSON（含 fenced ```json``` 或含 `"strategic_plan"` 的对象）
2. 校验枚举；非法枚举 → `parse_status=failed` + `primary_intent=unknown`（不静默改写）
3. `fallback_used=true` → `source=rule_fallback`，`intent_confidence≤0.3`
4. 缺块 → `failed` / `unknown`，不得伪造高置信意图

## 样例

- 成功：[`examples/strategic_plan_success_llm_call_completed.json`](../examples/strategic_plan_success_llm_call_completed.json)
- 解析失败：[`examples/strategic_plan_failed_llm_call_completed.json`](../examples/strategic_plan_failed_llm_call_completed.json)
