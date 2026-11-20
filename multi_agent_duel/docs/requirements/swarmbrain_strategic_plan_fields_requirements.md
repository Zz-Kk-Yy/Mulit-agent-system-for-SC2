# SwarmBrain 结构化战略字段需求（小文档）

| 字段 | 内容 |
|---|---|
| 文档状态 | 已在 SwarmBrain 实现（待下一轮正式采集验证） |
| 需求版本 | `strategic_plan_fields_v0.1.0` |
| 编写日期 | 2026-07-24 |
| 目标工程 | `SwarmBrain_re`（与现有 LLM 日志改造配套） |
| 数据使用方 | 本论文工程（意图识别标签） |
| 上位日志需求 | [`swarmbrain_llm_logging_requirements.md`](./swarmbrain_llm_logging_requirements.md) |
| 实现说明 | [`../guides/strategic_plan_schema.md`](../guides/strategic_plan_schema.md) |
| 优先级 | P1：在下一轮正式采集前实现；不阻塞当前三分类 baseline |

## 0. 一句话目标

在现有 `channel ∈ {macro, building, attack}` 之外，让每次成功的 LLM 计划**显式输出闭集战略意图**，减少论文侧从命令文本反推六类标签的歧义。

---

## 1. 为什么现在要做

Pilot Top10 人工确认（2026-07-24）显示：

| 问题 | 证据 |
|---|---|
| `building` 计划常混合扩建/科技/空军 | R020–R025 全部人工标为 `ambiguous` |
| 仅靠 channel + 命令 motif 不够 | 6/10 条勾选需要结构化计划字段 |
| 规则映射只能逼近 | v0.1.1 已更积极输出 `ambiguous`，但仍非金标 |

因此：继续只靠 `macro/building/attack` 无法稳定支撑六类监督；需要 SwarmBrain 在生成计划时直接给出战略层字段。

---

## 2. 范围

### 2.1 必须做

1. 在 `llm_call_completed`（`feature_scope=label_only`）中增加结构化战略对象 `strategic_plan`；
2. 字段使用**闭集枚举**，禁止自由文本充当主标签；
3. 允许 `ambiguous` / `unknown`，不允许空字符串冒充有效意图；
4. 与现有 `channel`、`parsed_commands`、时间戳字段并存，**不删除**旧字段；
5. 解析失败或 fallback 时明确标记，不得伪造高置信战略意图。

### 2.2 不做

- 不要求一次改完全部 prompt 体系；
- 不要求改变 macro/building/attack 通道业务含义；
- 不把该字段写入模型输入（始终 `label_only`）；
- 不在本需求里实现论文侧训练代码；
- 不要求历史 pilot 日志回填（仅约束新采集）。

---

## 3. 字段定义

在每次 **成功完成** 的 `llm_call_completed` 事件中新增：

```json
{
  "strategic_plan": {
    "schema_version": "strategic_plan_v0.1.0",
    "primary_intent": "tech_climb",
    "secondary_intents": ["fast_expand"],
    "intent_confidence": 0.62,
    "intent_rationale_short": "优先解锁 Hydralisk Den，同时补二矿",
    "horizon_sec": 90,
    "source": "llm_structured",
    "parse_status": "success"
  }
}
```

### 3.1 字段表

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 固定 `strategic_plan_v0.1.0` |
| `primary_intent` | enum | 是 | 主战略意图，见 §3.2 |
| `secondary_intents` | string[] | 是 | 次要意图列表，可空数组；元素同枚举，且不得与 primary 重复 |
| `intent_confidence` | number | 是 | `[0,1]`；模型自报置信，允许校准后改写但需留 provenance |
| `intent_rationale_short` | string | 是 | ≤200 字符，短理由；仅审计，不作主标签 |
| `horizon_sec` | number\|null | 是 | 该意图面向的未来视野（秒）；未知则 `null` |
| `source` | enum | 是 | `llm_structured` / `llm_text_parsed` / `rule_fallback` |
| `parse_status` | enum | 是 | `success` / `failed` / `fallback` |

### 3.2 `primary_intent` 闭集

与论文任务规范对齐：

```text
early_rush
fast_expand
tech_climb
air_harass_prep
mid_timing_attack
defensive_macro
ambiguous
unknown
```

说明：

- 多目标且无法定主意图 → 必须用 `ambiguous`，不要硬选；
- 证据不足 → `unknown`；
- **不要**再用 `macro/building/attack` 填这个字段；那是 `channel`，不是战略意图。

### 3.3 与 `channel` 的关系

| 字段 | 含义 | 例子 |
|---|---|---|
| `channel` | 规划通道/模块 | `building` |
| `strategic_plan.primary_intent` | 该次计划的战略目标 | `tech_climb` 或 `ambiguous` |

允许：`channel=building` 且 `primary_intent=ambiguous`。  
禁止：用 `channel` 值冒充 `primary_intent`。

---

## 4. 事件级约束

### 4.1 写入位置

优先写入 `llm_events.jsonl` 的 `llm_call_completed`：

```json
{
  "event_type": "llm_call_completed",
  "llm_call_id": "...",
  "channel": "building",
  "feature_scope": "label_only",
  "fallback_used": false,
  "parse_status": "success_nonempty",
  "strategic_plan": { "...": "见 §3" }
}
```

### 4.2 失败 / fallback

| 情况 | 要求 |
|---|---|
| LLM 调用失败 | 可不写 `strategic_plan`，或写 `parse_status=failed` + `primary_intent=unknown` |
| 规则 fallback 命令 | 若写战略字段，则 `source=rule_fallback`，`intent_confidence≤0.3` |
| 结构化解析失败但有原文 | `source=llm_text_parsed` 仅作过渡；能结构化时必须用 `llm_structured` |

### 4.3 时间边界

`strategic_plan` 只描述**该次调用完成时**的计划，不得回填未来才知道的对局结果或终局意图。

---

## 5. Prompt / 输出协议建议（最小）

要求模型在回答中额外给一块可解析 JSON（可与命令列表并列），例如：

```json
{
  "strategic_plan": {
    "primary_intent": "tech_climb",
    "secondary_intents": ["fast_expand"],
    "intent_confidence": 0.6,
    "intent_rationale_short": "unlock den then take third",
    "horizon_sec": 90
  },
  "commands": ["..."]
}
```

实现方可把该块解析进日志字段；解析器需：

1. 校验枚举；
2. 截断超长 rationale；
3. 非法枚举 → `parse_status=failed`，不得静默改写成近似类。

---

## 6. 验收标准

### 6.1 功能验收

1. 新对局中，至少 95% 的成功 `llm_call_completed` 含合法 `strategic_plan`；
2. `primary_intent` 仅出现 §3.2 枚举值；
3. `feature_scope` 仍为 `label_only`；
4. 旧字段 `channel` / `declared_commands` 等仍可读；
5. 论文工程可按 `llm_call_id` 对齐到 cutoff 前最新完成计划。

### 6.2 质量验收（小样本）

采集 ≥2 局后，人工抽查 ≥20 次 `building` 调用：

- 若命令明显多目标，`primary_intent` 应为 `ambiguous` 的比例不低于人工观感；
- 不得出现“全是 tech_climb”的塌缩。

### 6.3 论文侧使用约定

- `strategic_plan.primary_intent` 可作为未来六类弱监督 / 金标候选来源；
- 在独立人工复核前，仍不得直接写入论文主结果表的六类性能；
- 模型输入仍只能来自 fog-of-war 玩家视角特征。

---

## 7. 交付物（给 SwarmBrain）

1. 更新后的事件 schema 说明（可附在原日志需求文档附录）；
2. 一例真实 `llm_call_completed` 样例（含 `strategic_plan`）；
3. 解析失败样例；
4. 校验脚本或单测：枚举非法 / 缺字段 / fallback 标记。

---

## 8. 对本论文工程的影响

实现并重新采集后，本仓库可：

1. 在 `intent_dataset` 标签中新增 `strategic_intent_label`（仍 `label_only`）；
2. 逐步替代“channel→六类规则映射”作为主监督；
3. 保留 v0.1.1 规则映射作对照基线。

在 SwarmBrain 未交付前：继续使用 `channel_to_sixclass_candidate_v0.1.1` + 人工抽查，不阻塞三分类实验。

---

## 9. 参考

- 人工确认：[`sixclass_top10_human_confirmation_20260724.md`](./sixclass_top10_human_confirmation_20260724.md)
- 当前规则映射：[`sixclass_intent_mapping_v0.1.md`](./sixclass_intent_mapping_v0.1.md)
- 既有日志需求：[`swarmbrain_llm_logging_requirements.md`](./swarmbrain_llm_logging_requirements.md)
