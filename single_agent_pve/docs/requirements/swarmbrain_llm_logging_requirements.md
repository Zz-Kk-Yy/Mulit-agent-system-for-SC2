# SwarmBrain 对局级 LLM 计划与执行日志改造需求

## 0. 文档信息

| 字段 | 内容 |
|---|---|
| 文档状态 | 待实现 |
| 需求版本 | v1.0.0 |
| 编写日期 | 2026-07-21 |
| 目标工程 | `E:\star_carft\SwarmBrain_re` |
| 数据使用方 | “心智增强的动态多智能体对抗环境对手意图识别”论文工程 |
| 优先级 | P0：完成后再采集新的论文对局 |

本文档用于指导 SwarmBrain 增加逐局、追加式、可验证的 LLM 日志。实现后的日志应能回答：

1. 某条 LLM 计划属于哪一局、哪一方、哪个游戏时间点；
2. LLM 当时看到了什么输入，返回了什么原始回答；
3. 原始回答被解析成了哪些高层命令；
4. 哪些命令被接受、过滤、下发、执行成功或失败；
5. 该局最终生成了哪个 replay，文件哈希是什么；
6. 是否发生 LLM 调用失败、超时、重试或 fallback；
7. 日志是否可被论文工程稳定解析，并与 replay 时间轴对齐。

---

## 1. 背景与现状

当前 SwarmBrain 已存在以下记录：

- `logs/swarmbrain_game.out.log` 中有部分 prompt、LLM 回答和解析命令；
- `dashboard_state.json` 中有宏观、建筑、进攻通道的最后一次消息；
- DeerFlow 流水线的部分结果写入 Redis；
- 对局结束后可能生成 `.SC2Replay`。

但这些记录不能稳定形成“对局—时间点—LLM 计划—执行动作—replay”的完整关联，主要问题包括：

- 没有贯穿整局的唯一 `run_id`；
- Dashboard 状态会覆盖旧消息，只是快照，不是历史记录；
- 控制台日志缺少统一 schema 和稳定的现实时间戳；
- replay 文件名或哈希没有写入 LLM 日志；
- LLM 建议、过滤后命令和实际执行动作没有分层记录；
- LLM 调用失败后的 fallback 容易被误当成真实 LLM 意图；
- Redis 数据不能作为唯一持久化来源。

因此，需要增加独立于 Dashboard、控制台输出和 Redis 的磁盘持久化日志。

---

## 2. 改造目标

### 2.1 必须实现

1. 每次启动一局游戏时生成唯一 `run_id`；
2. 每局使用独立目录保存日志，禁止不同对局共用或覆盖同一文件；
3. 每次 LLM 调用至少记录开始、成功或失败状态；
4. 保存调用时的游戏时间、原始 prompt、原始 response、解析结果和错误信息；
5. 保存 LLM 建议经过过滤、下发和执行后的状态变化；
6. 在对局结束后把 replay 路径和 SHA256 写入该局 manifest；
7. 支持单智能体 PVE 和红蓝双智能体模式；
8. 进程异常退出时，已写入的 JSONL 行仍然有效且可以恢复；
9. 日志不得包含 API key、Authorization header 或 `.env` 内容；
10. 提供自动校验脚本和最小测试。

### 2.2 不属于本次需求

- 不修改现有 LLM 决策逻辑；
- 不修改宏观、建筑、进攻命令的业务含义；
- 不要求更换模型或提示词；
- 不用日志替代 replay；
- 不把 Dashboard 改造成完整实验管理系统；
- 不要求将日志直接上传到外部数据库或云服务；
- 不把受控方的 LLM 计划错误地当成另一方的真实意图。

---

## 3. 术语与信息边界

### 3.1 术语

| 术语 | 定义 |
|---|---|
| run | 一次完整或中途终止的 SC2 对局进程 |
| run_id | 标识该局的全局唯一 ID |
| llm_call_id | 标识一次 LLM 请求的唯一 ID |
| command_batch_id | 一次 LLM 回答解析得到的一批命令的唯一 ID |
| command_id | 一条高层命令的唯一 ID |
| channel | LLM 决策类型，如 `macro`、`building`、`attack` |
| game_loop | SC2 原始游戏循环编号，作为首要时间基准 |
| wall_time_utc | 现实世界 UTC 时间，仅用于审计和文件关联 |
| proposed command | LLM 原始回答中提出的命令 |
| accepted command | 经过语法、单位类型和规则过滤后接受的命令 |
| dispatched command | 已尝试转换并下发给 SC2 API 的动作 |
| executed command | 经程序确认已成功下发或执行的动作 |
| fallback | LLM 失败或输出不可用时使用的默认/规则策略 |

### 3.2 论文使用边界

SwarmBrain 控制虫族时，其 LLM 日志表达的是虫族代理自己的计划。该日志：

- 可作为“从对手视角预测该虫族代理意图”时的标签来源之一；
- 必须标记为 `feature_scope: "label_only"`；
- 不得作为同一时间点意图识别模型的输入特征；
- 不能直接作为另一方（例如人族）的意图真值；
- LLM 的自然语言回答是“计划声明”，不等同于实际执行；
- fallback、失败调用和未执行建议必须与正常 LLM 计划分开。

---

## 4. 输出目录与文件约定

### 4.1 日志根目录

增加环境变量：

```text
SWARMBRAIN_RUN_LOG_DIR
```

默认值：

```text
<SwarmBrain根目录>/logs/runs
```

### 4.2 每局目录

建议 `run_id` 格式：

```text
YYYYMMDDTHHMMSSZ_<uuid前8位>
```

示例：

```text
logs/runs/20260721T120000Z_a13f9c42/
├── run_manifest.json
├── run_events.jsonl
├── llm_events.jsonl
├── command_events.jsonl
└── errors.jsonl
```

要求：

- 目录必须在游戏启动、首次 LLM 调用之前创建；
- 所有文件必须使用 UTF-8；
- JSONL 每行必须是独立合法 JSON；
- JSONL 只能追加，禁止重写历史行；
- `run_manifest.json` 可以在结束时原子更新；
- 不同进程、红蓝双方和并行对局不得发生目录冲突。

---

## 5. 通用字段

所有 JSONL 事件必须包含以下字段：

```json
{
  "schema_version": "1.0.0",
  "event_type": "llm_call_completed",
  "event_id": "uuid",
  "run_id": "20260721T120000Z_a13f9c42",
  "agent_id": "player_1",
  "agent_side": "red",
  "player_id": 1,
  "race": "Zerg",
  "wall_time_utc": "2026-07-21T12:00:00.123Z",
  "game_loop": 3584,
  "game_time_sec": 160.0,
  "game_time_formatted": "02:40",
  "feature_scope": "label_only"
}
```

字段要求：

- `schema_version`：固定为本需求对应版本；
- `event_id`：每条事件唯一；
- `run_id`：同一局所有文件一致；
- `agent_id`：单智能体也必须填写；
- `agent_side`：允许 `single`、`red`、`blue`；
- `player_id`：尽量使用 SC2 replay/player 编号；尚未获得时允许 `null`，结束前应在 manifest 中补齐；
- `game_loop`：直接读取 SC2 状态，不得仅由现实时间推算；
- `game_time_sec`：记录程序当时使用的游戏秒数；
- `wall_time_utc`：ISO 8601 UTC，必须带毫秒和 `Z`；
- `feature_scope`：LLM 计划相关事件固定为 `label_only`，运行审计事件可使用 `audit_only`。

禁止使用玩家昵称或 replay 文件名充当唯一 ID。

---

## 6. `run_manifest.json` 需求

### 6.1 对局启动时写入

```json
{
  "schema_version": "1.0.0",
  "run_id": "20260721T120000Z_a13f9c42",
  "status": "running",
  "started_at_utc": "2026-07-21T12:00:00.123Z",
  "ended_at_utc": null,
  "mode": "single_agent_pve",
  "map_name": "AutomatonLE",
  "sc2_version": null,
  "base_build": null,
  "data_version": null,
  "random_seed": null,
  "code_git_commit": null,
  "agents": [],
  "replay": null,
  "result": null
}
```

### 6.2 对局结束时补齐

至少补充：

```json
{
  "status": "completed",
  "ended_at_utc": "2026-07-21T12:14:12.456Z",
  "final_game_loop": 19040,
  "final_game_time_sec": 850.0,
  "result": {
    "player_1": "Defeat",
    "player_2": "Victory"
  },
  "replay": {
    "path": "E:/.../example.SC2Replay",
    "file_name": "example.SC2Replay",
    "size_bytes": 123456,
    "sha256": "完整64位SHA256",
    "saved_at_utc": "2026-07-21T12:14:13.000Z"
  }
}
```

要求：

- SHA256 必须在 replay 文件关闭并完成写入后计算；
- 必须记录实际 replay 路径，不能只记录预期路径；
- 不允许只靠文件修改时间推断 replay 归属；
- 若 replay 保存失败，`replay` 仍需记录 `save_status: "failed"` 和错误原因；
- 正常结束为 `completed`，程序异常为 `crashed`，用户取消为 `aborted`，游戏启动失败为 `startup_failed`；
- manifest 更新必须采用“临时文件写入成功后替换正式文件”的原子方式；
- 建议记录非敏感运行配置、模型名称、代码 Git commit 和配置文件哈希。

---

## 7. `run_events.jsonl` 需求

用于记录对局生命周期，至少支持：

```text
run_started
game_connected
player_initialized
replay_save_requested
replay_saved
run_completed
run_aborted
run_crashed
```

示例：

```json
{"schema_version":"1.0.0","event_type":"run_started","event_id":"...","run_id":"...","wall_time_utc":"...","game_loop":null,"feature_scope":"audit_only"}
```

异常退出无法写入最终事件时，下一次启动可根据 `status: running` 判断上一局可能崩溃，但不得自动把它标记为正常完成。

---

## 8. `llm_events.jsonl` 需求

### 8.1 事件类型

每次调用使用同一个 `llm_call_id`，至少记录以下事件：

```text
llm_call_started
llm_call_completed
llm_call_failed
llm_fallback_selected
```

调用成功时应有：

```text
llm_call_started -> llm_call_completed
```

调用失败并回退时应有：

```text
llm_call_started -> llm_call_failed -> llm_fallback_selected
```

### 8.2 `llm_call_started`

```json
{
  "schema_version": "1.0.0",
  "event_type": "llm_call_started",
  "event_id": "uuid",
  "run_id": "...",
  "llm_call_id": "uuid",
  "agent_id": "player_1",
  "agent_side": "single",
  "player_id": 1,
  "race": "Zerg",
  "channel": "macro",
  "wall_time_utc": "2026-07-21T12:02:40.000Z",
  "game_loop": 3584,
  "game_time_sec": 160.0,
  "game_time_formatted": "02:40",
  "model_provider": "openai_compatible",
  "model_name": "model-name",
  "prompt_template_version": "macro_v1",
  "prompt_sha256": "...",
  "prompt": "完整原始prompt",
  "input_state": {},
  "previous_commands": [],
  "feature_scope": "label_only"
}
```

`channel` 至少支持：

```text
opening
macro
building
attack
intent
risk
other
```

`input_state` 建议保存当时真正传给 planner 的结构化输入，包括：

- 己方单位与建筑摘要；
- 已观察敌方单位与建筑摘要；
- 当前科技、资源、人口和阶段；
- 当前/上一批命令；
- 可见性或信息来源说明；
- planner 实际使用的其他上下文。

不得在日志中补入调用发生时尚不可见、但后来从 replay 获得的信息。

### 8.3 `llm_call_completed`

```json
{
  "schema_version": "1.0.0",
  "event_type": "llm_call_completed",
  "event_id": "uuid",
  "run_id": "...",
  "llm_call_id": "...",
  "agent_id": "player_1",
  "channel": "macro",
  "wall_time_utc": "2026-07-21T12:02:42.500Z",
  "game_loop": 3630,
  "game_time_sec": 162.1,
  "game_time_formatted": "02:42",
  "raw_response": "完整原始LLM回答",
  "finish_reason": "stop",
  "latency_ms": 2500,
  "token_usage": {
    "prompt_tokens": 1000,
    "completion_tokens": 500,
    "total_tokens": 1500
  },
  "parsed_commands": [],
  "parse_status": "success",
  "parse_error": null,
  "fallback_used": false,
  "feature_scope": "label_only"
}
```

要求：

- `raw_response` 必须保存，不能只保存解析后的命令；
- `parsed_commands` 必须保存解析时的原始顺序；
- 无法解析时使用 `parse_status: "failed"`，不能伪造空计划；
- 模型不提供 token usage 时允许为 `null`；
- request ID、response ID 可记录，但不得包含认证信息；
- `game_loop` 同时记录开始和完成时的值，避免长调用造成时间歧义。

### 8.4 失败与 fallback

失败事件示例：

```json
{
  "event_type": "llm_call_failed",
  "run_id": "...",
  "llm_call_id": "...",
  "channel": "attack",
  "error_type": "AuthenticationError",
  "error_message": "已脱敏的错误摘要",
  "retry_count": 0,
  "fallback_used": true
}
```

fallback 事件必须保存：

- 触发原因；
- fallback 策略名称和版本；
- fallback 产生的命令；
- `is_llm_generated: false`；
- 对应失败的 `llm_call_id`。

API key、请求 header、完整异常对象和环境变量不得写入错误日志。

---

## 9. `command_events.jsonl` 需求

### 9.1 目的

必须区分“LLM 说了什么”和“游戏里实际做了什么”。一条命令可能经历：

```text
proposed -> accepted/filtered -> dispatched -> succeeded/failed/skipped
```

### 9.2 事件类型

```text
command_proposed
command_accepted
command_filtered
command_dispatched
command_succeeded
command_failed
command_skipped
```

### 9.3 字段

```json
{
  "schema_version": "1.0.0",
  "event_type": "command_accepted",
  "event_id": "uuid",
  "run_id": "...",
  "llm_call_id": "...",
  "command_batch_id": "...",
  "command_id": "...",
  "agent_id": "player_1",
  "channel": "building",
  "wall_time_utc": "...",
  "game_loop": 4000,
  "game_time_sec": 178.6,
  "raw_command": "(Drone, A1)->(Build)->(Spawning Pool, A2)",
  "parsed_command": {
    "actor": "Drone",
    "actor_location": "A1",
    "action": "Build",
    "target": "Spawning Pool",
    "target_location": "A2"
  },
  "source": "llm",
  "status": "accepted",
  "reason_code": null,
  "reason_detail": null,
  "feature_scope": "label_only"
}
```

要求：

- 每条命令必须能通过 `llm_call_id` 回到原始 LLM 回答；
- 规则或 fallback 命令必须使用 `source: "rule"` 或 `source: "fallback"`；
- 被过滤命令必须记录过滤原因，不能直接丢弃；
- 下发时尽量记录实际 unit tag、ability ID、目标坐标等执行信息；
- 执行失败必须记录失败原因，例如资源不足、单位不存在、科技未解锁、位置无效；
- 如果当前框架无法确认游戏动作最终完成，使用 `command_dispatched`，不得错误标记为 `command_succeeded`。

建议的 `reason_code`：

```text
invalid_syntax
unsupported_unit
unsupported_action
invalid_target
unit_not_found
insufficient_resources
tech_unavailable
location_invalid
duplicate_command
superseded
llm_failed
execution_exception
unknown
```

---

## 10. `errors.jsonl` 需求

用于保存不适合放入 LLM 或命令事件的运行异常，例如：

- Dashboard 写入失败；
- replay 保存失败；
- JSON 序列化失败；
- SC2 连接中断；
- 日志目录不可写；
- 后台任务异常。

至少包含：

```text
run_id, event_id, wall_time_utc, game_loop,
component, error_type, sanitized_message,
recoverable, action_taken
```

日志写入自身失败时必须向标准错误输出明确告警，但不能导致游戏主循环因普通日志异常而崩溃。

---

## 11. 写入可靠性与安全要求

### 11.1 JSONL 写入

- 每条事件通过统一 logger 写入；
- 使用线程锁，避免异步 LLM 任务交叉破坏 JSON 行；
- 一次构造完整 JSON 字符串，再执行一次追加写入；
- 每行末尾添加 `\n`；
- 每次关键事件后 `flush()`；
- `llm_call_started`、失败事件、replay 保存事件和结束事件建议同时执行 `os.fsync()`；
- 单条不可序列化字段应转换为安全字符串或显式置为 `null`，不得破坏整份文件；
- 日志组件故障不得静默吞掉。

### 11.2 脱敏

必须过滤以下内容：

```text
api_key
authorization
bearer token
cookie
password
secret
.env 原文
```

允许记录：

- 模型提供商名称；
- 模型名称；
- 非敏感采样参数；
- token usage；
- 已脱敏错误类型与摘要。

### 11.3 兼容性

- 日志失败不得改变原有游戏策略和时序；
- 现有 Dashboard 更新应继续工作；
- 现有控制台日志可以保留，但不得作为唯一持久化来源；
- Redis 可以继续使用，但论文数据不能只存在 Redis；
- 新 logger 应尽量不依赖大型第三方库。

---

## 12. 建议的代码接入位置

以下是建议，不要求完全照搬文件结构，但最终行为必须满足本需求。

### 12.1 新增独立日志模块

建议新增：

```text
swarmbrain_run_logger.py
```

建议接口：

```python
class SwarmBrainRunLogger:
    def __init__(self, log_root, run_context): ...
    def write_run_event(self, event_type, **payload): ...
    def start_llm_call(self, channel, prompt, input_state, **meta) -> str: ...
    def complete_llm_call(self, llm_call_id, response, parsed_commands, **meta): ...
    def fail_llm_call(self, llm_call_id, error, **meta): ...
    def write_command_event(self, event_type, command_id, **payload): ...
    def attach_replay(self, replay_path, result, **meta): ...
    def finalize(self, status, **meta): ...
```

时间、UUID、JSON 序列化、SHA256、脱敏、flush 和原子 manifest 更新应集中实现，不要散落在业务代码中。

### 12.2 `swarmbrain_launch.py`

需要在以下生命周期接入：

1. 游戏启动前创建 run logger；
2. 玩家/地图信息可用后更新 manifest；
3. `overmindbrain_iter` 的宏观规划调用前后；
4. building planner 调用前后；
5. attack planner 调用前后；
6. `filter_commands` 前后；
7. 命令转换和 SC2 动作下发处；
8. `on_end`、正常退出、异常退出处；
9. replay 保存完成后计算并写入 SHA256。

现有 `_record_llm_message()` 只更新 Dashboard，可继续保留，但不能作为唯一日志入口。LLM 日志必须在原始 prompt 尚未丢失的位置写入。

### 12.3 `swarmbrain.py`

底层模型调用层建议返回或暴露：

- 模型名称；
- 开始/结束时间；
- latency；
- finish reason；
- token usage；
- request/response ID；
- 异常类型；
- 重试次数。

调用层不应重复生成新的 `run_id`。业务层创建的 `llm_call_id` 应贯穿底层调用。

### 12.4 双智能体模式

- 红蓝双方共享同一个对局 `run_id`；
- 使用不同 `agent_id`、`agent_side` 和 `player_id`；
- 可以写入同一组 JSONL，但必须通过线程锁保证安全；
- 也可以按 agent 分文件，但 manifest 必须明确列出文件路径；
- 两方的 `llm_call_id` 和 `command_id` 必须全局唯一；
- 一方 LLM 失败不得把另一方事件标成 fallback。

---

## 13. 实现顺序

按以下顺序开发和验证：

1. 实现 `SwarmBrainRunLogger` 和安全 JSONL 追加写入；
2. 实现 run 目录和初始 `run_manifest.json`；
3. 接入单智能体宏观、建筑、进攻三类 LLM 调用；
4. 接入解析、过滤和命令下发事件；
5. 接入正常结束、异常退出和 replay 哈希；
6. 接入 LLM 失败、重试和 fallback；
7. 接入红蓝双智能体模式；
8. 编写日志校验脚本和自动测试；
9. 运行一局 3–5 分钟测试对局；
10. 用生成的 replay 和日志完成一次关联验收；
11. 验收通过后再开始论文用批量对局采集。

---

## 14. 校验脚本要求

建议新增：

```text
scripts/validate_run_logs.py
```

命令示例：

```powershell
python scripts/validate_run_logs.py --run-dir "logs/runs/<run_id>"
```

至少检查：

- 所有 JSON/JSONL 可以解析；
- 所有事件的 `run_id` 一致；
- `event_id` 不重复；
- `llm_call_id` 的开始与完成/失败可以配对；
- completed 与 failed 不同时出现在同一调用的最终状态；
- fallback 必须关联失败调用；
- LLM 产生的命令可以关联到 `llm_call_id`；
- command 状态转换合法；
- game loop 在单个 agent 的事件序列中不逆序；
- manifest 中的 replay 存在；
- 实际 replay SHA256 与 manifest 一致；
- 日志中不存在常见密钥模式；
- 已完成对局不存在 `status: running`；
- `feature_scope` 字段符合要求。

校验结果应输出明确的通过/失败数量，并以非零退出码表示失败。

---

## 15. 验收测试

### 15.1 正常单智能体对局

运行一局至少触发宏观、建筑和进攻规划的对局，验收：

- [ ] 只生成一个唯一 run 目录；
- [ ] manifest 从 `running` 更新为 `completed`；
- [ ] 三类 LLM 调用都存在 start 与 completed 事件；
- [ ] prompt、raw response、解析命令均非空；
- [ ] 每条解析命令都有 command 事件；
- [ ] replay 路径存在且 SHA256 正确；
- [ ] replay 时长与最终 game loop 基本一致；
- [ ] Dashboard 原功能正常。

### 15.2 LLM 调用失败

使用 mock 触发一次超时或认证失败，验收：

- [ ] 写入 `llm_call_failed`；
- [ ] 错误信息已脱敏；
- [ ] 如果选择 fallback，存在 `llm_fallback_selected`；
- [ ] fallback 命令标记 `is_llm_generated: false`；
- [ ] 程序按照原有策略继续或安全退出；
- [ ] 失败调用不会被统计为正常 LLM 计划。

### 15.3 中途终止

在游戏进行中终止进程，验收：

- [ ] 已写入 JSONL 的每一行仍可解析；
- [ ] 不存在半行 JSON；
- [ ] manifest 保持 `running` 或经恢复逻辑标记为 `crashed`；
- [ ] 不得被错误标记为 `completed`。

### 15.4 双智能体对局

- [ ] 红蓝双方使用同一个 `run_id`；
- [ ] 每条事件能区分 red/blue；
- [ ] 并发写入没有 JSON 行交叉损坏；
- [ ] 一方失败不会污染另一方状态；
- [ ] replay manifest 包含双方玩家、种族和结果。

### 15.5 敏感信息扫描

- [ ] 日志中没有 API key；
- [ ] 日志中没有 Authorization/Bearer header；
- [ ] 日志中没有 `.env` 原始内容；
- [ ] 异常堆栈中没有请求认证信息。

---

## 16. 最终交付物

实现完成后，SwarmBrain 工程应至少提供：

```text
swarmbrain_run_logger.py
scripts/validate_run_logs.py
tests/test_run_logger.py
tests/test_run_log_validation.py
logs/runs/<测试run_id>/          # 本地验收样本，不一定提交 Git
```

同时提交一份简短说明，列出：

- 修改的文件；
- 日志根目录配置方法；
- 如何运行校验脚本；
- 一次正常对局的 run 目录；
- 一次 LLM 失败测试结果；
- replay SHA256 校验结果；
- 尚未实现或降级的字段。

---

## 17. 完成定义（Definition of Done）

只有同时满足以下条件，才认为本需求完成：

1. 一局中每次 LLM 调用均可追溯到原始 prompt、原始 response 和游戏时间；
2. 每条 LLM 命令均可区分 proposed、filtered、dispatched 和最终可确认状态；
3. LLM 失败与 fallback 不会被误标成正常 LLM 意图；
4. 日志与最终 replay 通过同一 `run_id` 和 replay SHA256 稳定关联；
5. 进程中断后已写日志仍可解析；
6. 单智能体和双智能体模式均不会覆盖其他对局日志；
7. 自动校验脚本全部通过；
8. 日志不包含密钥和认证信息；
9. 论文工程能够在不读取 Dashboard 或 Redis 的情况下独立解析这些文件；
10. 完成一局真实 smoke test，并人工核对至少三个 LLM 时间点与 replay 时间轴一致。

达到上述完成定义后，再使用新版 SwarmBrain 采集论文所需的新对局。
