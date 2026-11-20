# fixtures — 离线样例数据

无需启动 SC2 或 Redis，可将这些 JSON 当作工具返回值，用于本地编写/测试子智能体 prompt 与输出解析。

| 文件 | 对应数据源 |
|------|------------|
| `situation_current.sample.json` | `sc2_get_situation()` / Redis `situation:current` |
| `cache_sensing.sample.json` | `sc2_analysis_cache_get(..., "sensing")` |
| `cache_compute.sample.json` | `compute` |
| `cache_disposition.sample.json` | `disposition` |
| `cache_intent.sample.json` | `intent` |
| `cache_risk_id.sample.json` | `risk_id` |

字段说明见上级目录 [SC2数据与智能体输入说明.md](../SC2数据与智能体输入说明.md)。流水线如何依次写入这些 cache，见 [§2.1](../SC2数据与智能体输入说明.md#21-主流水线编排run_sc2_deerflow_pipelinepy) 与 `../run_sc2_deerflow_pipeline.py`。
