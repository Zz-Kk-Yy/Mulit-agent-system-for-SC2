# strategic_plan_delivery_v03_next 接入结果（2026-07-29）

## 结论

`strategic_plan_delivery_v03_next` 中的 4 局已完成论文工程正式导入、论文侧 StarCraft II 双视角解析和20局隔离候选数据集构建。

4局工程接入全部通过，但合并后的类别平衡和“类别 × 时间段”覆盖仍未达到 `pilot_v0.3` 冻结门槛。因此：

- 4局可以保留为有效候选数据；
- 不冻结 `pilot_v0.3`；
- 不生成新的正式 split pin；
- 不替换正式默认 `pilot_v0.2`；
- 按既定门槛停止，不继续构建特征矩阵或重跑模型。

## 1. 正式导入

论文侧正式导入结果：

```json
{
  "status": "ok",
  "mode": "import",
  "validated_count": 4,
  "failure_count": 0,
  "imported_count": 4,
  "index_row_count": 31
}
```

导入的 run：

- `20260728T100517Z_76a595a7`
- `20260728T103138Z_50913692`
- `20260728T151048Z_f5252b4c`
- `20260728T162956Z_37e8faf3`

## 2. 论文侧双视角 replay 解析

4局均完成玩家1和玩家2视角解析，共8个 observer 方向。

| run_id | observer 1 | observer 2 | 末端覆盖 | 结论 |
|---|---|---|---:|---|
| `20260728T100517Z_76a595a7` | ok | ok | 16576 | 通过 |
| `20260728T103138Z_50913692` | ok | ok | 52394 | 通过 |
| `20260728T151048Z_f5252b4c` | ok | ok | 14672 | 通过 |
| `20260728T162956Z_37e8faf3` | ok | ok | 18682 | 通过 |

所有视角均满足：

- fog of war 开启；
- `unknown_unit_type_count=0`；
- replay SHA-256 与导入索引一致；
- player-view 覆盖全部论文 cutoff；
- 双方视角分别独立生成事件流；
- `parse_status.json` 为 `status=ok`。

首次批处理在全部4局完成原子发布后触及20分钟外层命令时限；随后幂等复跑确认4局全部为 `skipped`、`failed_count=0`。因此该外层超时不是 replay 解析失败。

## 3. 20局隔离候选集

候选集由上一版16局候选集与本批新增有效4局组成：

| 项目 | 值 |
|---|---:|
| run | 20 |
| observer 方向 | 40 |
| 总窗口 | 600 |
| 有效标签 | 592 |
| unknown | 8 |
| fingerprint | `224e2dbe17422c54b6fd2a31cbebfe38ce21000199cf2967a42723a1686db670` |

路径：

```text
data/interim/pilot_v0.3_next_candidate/intent_dataset/
```

8个 unknown 全部来自 `20260728T151048Z_f5252b4c` 的60–150秒窗口，原因为：

```text
no_valid_completed_plan_before_cutoff
```

这是因果标签边界下的合法 unknown，不应使用未来 LLM 调用回填。

## 4. 正式窗口分布

### 总类别

| 类别 | 窗口 | run 覆盖 | 门槛 | 结果 |
|---|---:|---:|---|---|
| macro | 42 | 10 | ≥120窗口、≥6 run | 窗口失败 |
| building | 162 | 20 | ≥120窗口、≥6 run | 通过 |
| attack | 388 | 20 | ≥120窗口、≥6 run | 通过 |

最大类别/最小类别为：

```text
388 / 42 = 9.24
```

门槛要求不超过 `2.0`，因此失败。

### 时间段 × 类别

| 时间段 | macro | building | attack |
|---|---:|---:|---:|
| early（60–180s） | 36（10 run） | 149（19 run） | 7（4 run） |
| mid（210–360s） | 6（1 run） | 7（2 run） | 227（20 run） |
| late（390–480s） | 0（0 run） | 6（3 run） | 154（20 run） |

门槛要求每个单元至少10个窗口且来自至少3个 run。当前失败单元：

- `early × attack`：7窗口；
- `mid × macro`：6窗口、1 run；
- `mid × building`：7窗口、2 run；
- `late × macro`：0窗口、0 run；
- `late × building`：6窗口。

## 5. 工程决策

本批4局解决了上一批 replay 覆盖异常问题，并为 early-attack、mid-macro、mid-building、late-building 提供了少量有效样本，但不足以解除总体时间耦合。

下一轮仍应优先自然补采：

1. `late × macro`；
2. `mid × macro`；
3. `mid × building`；
4. `late × building`；
5. `early × attack`。

只有正式窗口分布先通过门槛，才重新执行特征矩阵、time-only、causal temporal、bootstrap CI 和 Stage C；在此之前不升级 GRU/TCN。

