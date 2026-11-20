# SwarmBrain LLM 时间字段语义（pilot 冻结）

## 兼容 schema（1.0.0）

非 opening 调用：

| 事件字段 | 语义 |
|---|---|
| `llm_call_started.game_loop` | `observation_game_loop`（构造 prompt 时的观测时钟） |
| `llm_call_started.observation_game_loop` | 同上（显式别名） |
| `llm_call_started.request_started_game_loop` | HTTP 请求实际发出时的游戏时钟（可能因队列延迟晚于 observation） |
| `llm_call_completed.started_game_loop` | 继承 observation |
| `llm_call_completed.observation_game_loop` | 同上 |
| `llm_call_completed.game_loop` | `response_completed_game_loop`（回答可用时的游戏时钟） |
| `llm_call_completed.response_completed_game_loop` | 同上 |

约束：

- `response_completed_game_loop >= observation_game_loop`（同一 `llm_call_id`）
- opening：`phase=pregame`，game loop / time 为 `null`
- 不要求 JSONL 文件行按 game_loop 全局递增；按 `llm_call_id` / `command_id` 重建
