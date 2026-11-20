# SwarmBrain 响应：v03_next 接入结果后的补采任务（2026-07-29）

依据：`strategic_plan_delivery_v03_next_intake_20260729.md`

## 论文侧结论（已接受）

- 4 局正式导入、双视角解析、20 局候选集均通过；
- **不冻结** `pilot_v0.3`，不替换 `pilot_v0.2`；
- 4 局保留为有效候选。

## SwarmBrain 本轮动作

1. 保留既有 4 局合格 run，不修改历史日志与 replay；
2. 使用 `pilot_dual_llm_v03_cover_v1` **config_version=1.3.0** 继续自然补采；
3. 优先观察正式窗口缺口（不强制 prompt 标签）：
   - `late × macro`
   - `mid × macro`
   - `mid × building`
   - `late × building`
   - `early × attack`
4. 门禁不变：自然胜负、replay≥480s、validator pass、双视角 smoke pass；
5. 新合格局并入 `strategic_plan_delivery_v03_next/`，重建 `pilot_*` 契约文件；
6. 分布仍未达冻结门槛时如实报告，不伪造标签。

## 配置要点（1.3.0）

| 参数 | 值 | 意图 |
|---|---:|---|
| macro_every | 45 | 提高 mid/late macro 触发机会 |
| building_every | 25 | 提高 mid/late building 触发机会 |
| attack_every | 150 | 避免 mid/late 永久只打 attack |
| attack_min_army | 12 | 允许 early 在小军队时自然出现 attack |
| max_game_time_sec | 1200 | 超时进 quarantine，不计额度 |

目标先补到合格 **12** 局（含原 4 局），再按论文窗口统计决定是否继续。
