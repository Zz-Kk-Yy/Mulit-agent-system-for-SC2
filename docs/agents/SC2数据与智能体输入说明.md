# SC2 可获取数据与智能体输入说明

> **面向对象**：领取 `new_agents` 包的同学，**无需本地安装星际争霸 II 或跑通全栈**，只要理解本文的数据契约，即可设计子智能体并与主流水线对接。  
> **配套文档**：[标准智能体模板接入.md](./标准智能体模板接入.md)（如何写代码注册智能体）

---

## 0. 30 秒理解：你的智能体吃什么、吐什么

| 层级 | 存储位置 | 谁写入 | 典型消费者 |
|------|----------|--------|------------|
| **原始态势** | Redis `situation:current` | 对局进程（`swarmbrain_launch`） | 仅 **sensing** 阶段应直接读取 |
| **分层快照** | Redis `situation:layer:{unit\|economy\|map\|vision}` | 同上（或由全量拆分） | sensing、disposition 按需补空间细节 |
| **流水线产物** | Redis `sc2:analysis:{thread_id}:{阶段键}` | 各子智能体 `cache_set` | **compute 及之后所有阶段** |
| **编排上下文** | 首条用户消息文本 | 主流水线 `run_sc2_deerflow_pipeline.py` | **所有阶段**（`thread_id`、`mode`） |

**黄金规则**：

1. **不要重复拉原始态势**：上游阶段已写入 cache 的，你只读 `sc2_analysis_cache_get`，除非你是 sensing 或需要补坐标。  
2. **`input_sources` 填缓存键**：`sensing`、`compute`、`disposition`、`intent`、`risk_id`、`risk_response`（**不要**写 `sc2-sensing`）。  
3. **输出必须是可 `json.loads` 的 JSON 字符串**，并通过 `sc2_analysis_cache_set` 写入；键名 = `agent_name` 的 `-` 后缀（如 `sc2-counter` → `counter`）。

---

## 1. 数据三层架构（对接视角）

```mermaid
flowchart TB
  subgraph L1 [Layer 1 原始态势 仅 sensing 宜直接读]
    SIT[situation:current]
    LYR[situation:layer:*]
    STAT[game:status]
    CAP[sc2_unit_capabilities 静态表]
  end
  subgraph L2 [Layer 2 流水线缓存 按 thread_id]
    C1[sensing]
    C2[compute]
    C3[disposition]
    C4[intent]
    C5[risk_id]
    C6[risk_response]
    CX[你新增的 counter 等]
  end
  subgraph L3 [Layer 3 编排注入]
    TID[thread_id]
    MOD[mode=replay|live]
  end
  SIT --> C1
  C1 --> C2 --> C3 --> C4 --> C5 --> C6
  TID --> C1
  MOD --> C6
```

**不部署游戏时**：用本文第 10 节的 **fixtures 样例 JSON** 当作 `cache_get` 的返回值，在本地单测你的 prompt / 解析逻辑即可。

---

## 2. 编排层注入（每个智能体必收）

主流水线在每个阶段开头发送等价于下面的用户消息（**不是 JSON 文件**，是 LLM 的 HumanMessage 文本）：

```text
thread_id=<uuid 或指定 id>
mode=replay

Execute your stage now. ...
```

| 字段 | 含义 | 你的智能体如何使用 |
|------|------|-------------------|
| `thread_id` | 本次分析会话 ID，Redis 缓存命名空间 | 所有 `sc2_analysis_cache_get/set` 的第一个参数 |
| `mode` | `replay`：只出建议，禁止下发操作；`live`：risk-response 可在人工确认后发命令 | 若你做「处置类」智能体，必须在输出 JSON 里带 `mode` 并遵守 |

上述 `thread_id` / `mode` 由 **`run_sc2_deerflow_pipeline.py`** 在每一阶段调用 LLM 前写入用户消息；详见下文 §2.1。

---

### 2.1 主流水线编排（`run_sc2_deerflow_pipeline.py`）

本目录附有该脚本的**只读副本**（与仓库根目录同源），用于理解「谁在什么时候调用你的智能体」。**不必通读实现**；对接时记住下表即可。

#### 它做什么

| 职责 | 说明 |
|------|------|
| 顺序调度 | 按 `SC2_SUBAGENT_ORDER` 依次执行 `ALL_SC2_SUBAGENTS` 中的每个 `SubagentConfig` |
| 工具绑定 | 将 `cfg.tools` 中的 MCP 工具名绑定为 LangChain `StructuredTool`，供该阶段 LLM 调用 |
| 上下文注入 | 每阶段发送固定格式的 HumanMessage（含 `thread_id`、`mode`），见 §2 |
| 超时与兜底 | 每阶段 `asyncio.wait_for(..., timeout=cfg.timeout_seconds)`；若模型未 `cache_set`，尝试从回复文本解析 JSON 并 **backfill** 写入 Redis |
| 注册名 → 缓存键 | `_stage_agent_key()`：如 `sc2-risk-id` → Redis 键后缀 `risk_id`；**新智能体** `sc2-counter` → `counter`（与模板规则一致） |

#### 默认执行顺序（合并新阶段前）

```text
sc2-sensing → sc2-compute → sc2-disposition → sc2-intent → sc2-risk-id → sc2-risk-response
```

对应 Redis：`sc2:analysis:{thread_id}:sensing` … `:risk_response`。

#### 与 `--replay` / `mode` 的关系

| 启动方式 | `mode` 注入值 | 对工具的影响 |
|----------|---------------|--------------|
| 默认（无 `--replay`） | `live` | `sc2-risk-response` 可绑定 `sc2_send_commands` |
| `python run_sc2_deerflow_pipeline.py --replay` | `replay` | **risk-response 阶段不再暴露** `sc2_send_commands` |

仪表盘「刷新分析」由 Ask API 子进程拉起**根目录**同名脚本时，通常带 `replay` 语义（只分析、不下发操作）。

#### 谁需要关心这个文件

| 角色 | 是否需要 |
|------|----------|
| 只写单个子智能体、用 fixtures 自测 | **了解** `thread_id` 与顺序即可，不必本地跑流水线 |
| 要把新阶段并入全栈 / 仪表盘 | **必须** 与集成同学改根目录 `SC2_SUBAGENT_ORDER`（见 [标准智能体模板接入.md §4.1](./标准智能体模板接入.md#41-并入主流水线sc2_subagent_order)） |
| 联调 Redis 产物 | 跑完后检查 `sc2:analysis:{thread_id}:*` |

#### 常用命令（在 SwarmBrain **项目根目录**）

```powershell
# 指定 thread_id，便于与仪表盘/同学对齐
python run_sc2_deerflow_pipeline.py --thread-id my-run-1

# 复盘/联调模式（risk-response 不发命令工具）
python run_sc2_deerflow_pipeline.py --replay --thread-id my-run-1
```

**前置**：Redis 已有 `situation:current`（对局或 bridge 在写）、`.env` 中 LLM Key 有效。本目录副本**不能单独运行**（缺少 `sc2_redis_tools.py` 等），请在完整工程中执行。

#### 另一入口：Ask API

仪表盘点击 **「刷新分析」** 时，`ask_api_server.py` 会在项目根启动同一条流水线（子进程），结果仍写入相同 Redis 键；你提交的子智能体合并进主仓库后，也会经该路径执行。

---

## 3. Layer 1：原始态势 `situation:current`

**工具**：`sc2_get_situation()` → 返回一整份 JSON 字符串。  
**Redis 键**：`situation:current`（TTL 约 10–30s，对局运行时持续刷新）。

### 3.1 顶层结构

| 顶层键 | 类型 | 含义 |
|--------|------|------|
| `raw` | object | 时间、回合、玩家、数据源 |
| `unit` | object | 单位/建筑列表与编成 |
| `economy` | object | 矿气、补给、工人 |
| `map` | object | 地图名、出生点、阶段 |
| `vision` | object | 迷雾、可见敌情 |
| `messages` | object | 方案生成智能体对话等（可选） |
| `command_sequence` | array | 已下发/待执行指令摘要（可选） |
| `agent_workbench` | object | 工作台状态（可选） |

### 3.2 `raw` — 时间与元数据

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `game_loop` | int | SC2 逻辑帧 | `1234` |
| `game_time` | float \| null | 秒（若可解析） | `320.5` |
| `time_formatted` | string | 展示用时间 | `t=5:20` |
| `iteration` | int | 对局迭代计数 | `100` |
| `updated_at` | string | ISO 时间戳 | |
| `player_id` | int | 观察视角玩家，常为 `1` | |
| `source` | string | 如 `dashboard_state` | |

### 3.3 `unit` — 兵力与可见单位（核心）

| 字段 | 类型 | 说明 |
|------|------|------|
| `own_units` | array | 己方单位明细（见下表「单位条目」） |
| `own_structures` | array | 己方建筑 |
| `enemy_units` | array | **战争迷雾内可见**的敌方单位 |
| `own_unit_composition` | array | 己方编成统计 `[{name, count}, ...]` |
| `enemy_visible_composition` | array | 敌方可见编成统计 |
| `army_count` | int | 己方军队数量（聚合） |
| `own_hp_total` | int | 己方 HP 总和（若有） |

**单位条目**（`own_units` / `enemy_units` 每项）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `tag` | string | 合成唯一键，如 `unit:Marine:A3:0` |
| `type` | string | 单位类型名，与 `sc2_unit_capabilities` 键一致，如 `Marine`、`Hydralisk` |
| `count` | int | 数量（编成聚合时可能 >1） |
| `location` | string | **小地图网格标签**，如 `A3`、`B7`（不是像素坐标） |
| `position` | object | `{ "label": "A3" }` |
| `action` | string | 当前动作摘要（若有） |
| `kind` | string | `unit` 或建筑类型 |

> **注意**：早期流水线 JSON 里 sensing 用 `own.units` 按 **类型计数**；Redis 全量态势用 **明细列表 + 位置标签**。你写新智能体时，以 **cache 中 sensing 阶段输出** 为准；若自己调 `sc2_get_situation`，需兼容列表字段。

### 3.4 `economy` — 经济

| 字段 | 类型 | 说明 |
|------|------|------|
| `minerals` | int | 矿物 |
| `vespene` | int | 气体 |
| `supply_used` | int | 已用补给 |
| `supply_cap` | int | 补给上限 |
| `workers` | int | 工人数量 |
| `pending_command_count` | int | 待处理指令数 |

### 3.5 `map` — 地图与阶段

| 字段 | 类型 | 说明 |
|------|------|------|
| `map_name` | string | 如 `AutomatonLE` |
| `map_size` | null \| array | 地图尺寸（常为 null） |
| `start_location` | string | 出生点标签 |
| `bases` | array | 小地图覆盖/基地相关（来自 minimap） |
| `game_stage` | string | `early` / `mid` / `late`（前期/中期/后期） |
| `feature_session` | string | 特征层会话 id（可选） |

### 3.6 `vision` — 视野与迷雾

| 字段 | 类型 | 说明 |
|------|------|------|
| `visible_tiles_pct` | float \| null | 可见格子占比 |
| `fog_of_war` | float \| null | 迷雾占比 |
| `enemy_base_visible` | bool | 是否看到敌方基地 |
| `enemy_units_visible` | int | 可见敌方单位数 |
| `enemy_structures_visible` | int | 可见敌方建筑数 |

### 3.7 分层读取 `sc2_get_layer(layer_name)`

| layer_name | 对应 `situation:current` 子树 | 适用场景 |
|------------|------------------------------|----------|
| `unit` | `unit` | 单位位置、编成、集群分析 |
| `economy` | `economy` | 经济决策、爆兵节奏 |
| `map` | `map` | 地图/阶段/出生点 |
| `vision` | `vision` | 迷雾、侦察需求 |

---

## 4. Layer 1 补充：静态能力、趋势、游戏状态

### 4.1 `sc2_unit_capabilities(unit_names?)`

返回 **静态平衡表**（与当前帧无关），键为 `type` 名（如 `Marine`）。

| 字段 | 说明 |
|------|------|
| `hp`, `shield`, `armor` | 生存 |
| `supply`, `minerals`, `vespene`, `build_time` | 经济与人口 |
| `ground_range`, `air_range` | 射程 |
| `dps_ground`, `dps_air` | 输出 |
| `speed`, `sight` | 机动与视野 |
| `is_air` | 是否空中单位 |
| `role` | `worker` / `infantry` / `ranged` / `heavy` / `anti_air` / `harass` / … |

**谁需要**：`compute`（算战力）、`counter`（克制）、`risk-id`（克制系数）等。

### 4.2 `sc2_get_trend()`

返回相对**上一次调用锚点**的近似增量（非严格 30 秒墙钟）：

```json
{
  "trend_30s_approx": {
    "own_supply_delta": 0,
    "enemy_supply_delta": 0,
    "own_army_value_delta": 0,
    "enemy_army_value_delta": 0
  },
  "note": "..."
}
```

**谁需要**：`compute`（写入 `trend_30s`）、`intent`（判断 rush/膨胀）。

### 4.3 `sc2_get_game_status()`

| 字段 | 说明 |
|------|------|
| `status` | `running` / `idle` 等 |
| `map_name`, `game_stage`, `iteration`, `game_loop` | 与态势一致 |
| `bot_running` | Redis 是否有 `situation:current` |
| `speed`, `paused` | 游戏速度 |

### 4.4 `sc2_send_commands(commands)`（慎用）

单条 command 结构：

| 字段 | 类型 | 合法值 |
|------|------|--------|
| `type` | string | `attack_move`, `attack`, `move`, `retreat`, `build`, `train`, `annotation` |
| `unit_tags` | int[] | 单位 tag（live 模式） |
| `target` | object | `{ "x": int, "y": int }` 地图坐标 |
| `unit_type` | string \| null | 训练/建造类型 |
| `priority` | string | `urgent` / `high` / `normal` |

通常仅 **risk-response** 阶段在 `mode=live` 且人工确认后使用。

---

## 5. Layer 2：流水线缓存（各阶段输入输出）

Redis 键格式：`sc2:analysis:{thread_id}:{agent_name}`  
读取：`sc2_analysis_cache_get(thread_id)` → 返回**所有已写入阶段**的对象；或 `sc2_analysis_cache_get(thread_id, agent_name="sensing")` 读单个。

### 5.1 总览表（必读）

| 阶段 | 注册名 | 缓存键 | 建议读取的上游 (`input_sources`) | 额外工具 | 输出供谁使用 |
|------|--------|--------|----------------------------------|----------|--------------|
| 态势感知 | `sc2-sensing` | `sensing` | **无**（读原始态势） | `sc2_get_situation`, `sc2_get_layer` | compute, disposition, 所有下游 |
| 态势计算 | `sc2-compute` | `compute` | `sensing` | `sc2_unit_capabilities`, `sc2_get_trend` | disposition, intent, counter… |
| 兵力布势 | `sc2-disposition` | `disposition` | `sensing`, `compute` | `sc2_get_layer("unit")` | intent, risk-id |
| 意图识别 | `sc2-intent` | `intent` | `sensing`, `compute`, `disposition` | （通常仅 cache） | risk-id, risk-response |
| 风险识别 | `sc2-risk-id` | `risk_id` | `sensing`, `compute`, `disposition`, `intent` | （通常仅 cache） | risk-response |
| 风险处置 | `sc2-risk-response` | `risk_response` | `intent`, `risk_id`（+ 按需上游） | `sc2_send_commands`, `sc2_get_game_status` | 仪表盘 / 执行层 |
| **示例扩展** | `sc2-counter` | `counter` | `sensing`, `compute` | `sc2_unit_capabilities` | disposition, plan… |

---

### 5.2 `sensing` 输出 schema（事实层）

**输入**：`situation:current` + 可选 `sc2_get_layer`。

**输出 JSON 字段**（写入 `agent_name="sensing"`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `game_loop` | int | |
| `game_time` | string | `mm:ss` |
| `player_id` | int | |
| `own.units` | object | `{ "<UnitType>": count }` |
| `own.buildings` | object | 建筑计数 |
| `own.supply_used`, `supply_cap`, `minerals`, `vespene`, `workers`, `bases` | number | |
| `enemy.units`, `enemy.buildings` | object | **仅可见部分** |
| `enemy.visible_supply`, `bases_seen` | int | |
| `map.name`, `map.size`, `map.start_location` | | |
| `vision.visible_tiles_pct`, `enemy_base_visible` | | |

---

### 5.3 `compute` 输出 schema（量化层）

**输入**：`sensing` 全文。

| 字段 | 类型 | 说明 |
|------|------|------|
| `own.army_value` | int | 矿+1.5×气 估算战力 |
| `own.dps_ground`, `own.dps_air` | float | |
| `own.avg_ground_range`, `own.air_ratio` | float | |
| `own.supply`, `own.eco_income_est` | | |
| `enemy.*` | | 对称字段（基于可见敌军） |
| `ratios.army_value`, `ratios.supply`, … | float | 敌我比 |
| `trend_30s.*_delta` | int | 趋势增量 |

---

### 5.4 `disposition` 输出 schema（空间层）

**输入**：`sensing` + `compute`；可选 `sc2_get_layer("unit")` 补坐标。

| 字段 | 说明 |
|------|------|
| `own_clusters[]` | `center[x,y]`, `unit_count`, `dps_*`, `composition`, `role`（main_army/defense/harass/expansion_guard） |
| `enemy_clusters[]` | 同上 + `confidence` |
| `frontline` | 双方前线点、距离、无人区 |
| `weak_points[]` | `owner`, `location`, `reason` |

> 集群 `center` 为地图坐标；与 sensing 的 `location` 网格标签可能并存，下游需知悉两种表达。

---

### 5.5 `intent` 输出 schema（研判层）

**输入**：建议读 **全部已有 cache**（至少 sensing + compute + disposition）。

| 字段 | 说明 |
|------|------|
| `primary_strategy` | `rush` \| `timing_attack` \| `macro` \| `harass` \| `all_in` \| `tech_switch` \| `defensive` \| `unknown` |
| `confidence` | float |
| `evidence` | string[] 总证据 |
| `intent_dimensions` | **必填四维**：`attack` / `defense` / `detect` / `support`，各含 `description_cn`, `level`（high/medium/low） |
| `evidence_by_dimension` | 四维证据数组（键不可缺） |
| `expected_attack_routes[]` | 方向、概率、ETA、涉及单位 |
| `tech_switch_risk` | 转型预判 |
| `timing_window` | 关键时间窗 |

**业务对齐（仪表盘）**：前端「意图识别」Tab 高亮敌方单位角色，与你的 `intent_dimensions` / 敌军 composition 应一致。

---

### 5.6 `risk_id` 输出 schema（风险层）

**输入**：`sensing`, `compute`, `disposition`, `intent`。

| 字段 | 说明 |
|------|------|
| `risks[]` | `rank`, `target`, `location`, `threat_score`, `threat_type`, `time_to_impact_sec`, `recommended_counters`, `reasoning` |
| `overall_risk_level` | `critical` \| `high` \| `medium` \| `low` |

`threat_type` 示例：`army_push`, `harass`, `tech`, `economy_denial`, `air_raid`。

**业务对齐**：仪表盘「风险分析」四维：**进攻 / 防御 / 探测 / 保障** — 你的 `threat_type` 与 `target` 应能映射到这四类。

---

### 5.7 `risk_response` 输出 schema（处置层）

**输入**：至少 `intent` + `risk_id`；复杂处置可再拉 `disposition`。

| 字段 | 说明 |
|------|------|
| `mode` | `replay` \| `live` |
| `plan_summary` | 中文摘要 |
| `commands[]` | 见 4.4 |
| `force_adjustments[]` | `produce` \| `tech_up` \| `expand` \| `defend` + 单位/建筑 + 数量 |
| `hitl_required`, `hitl_prompt` | 人工确认提示 |

---

## 6. 业务语义词典（与前端/方案模块对齐）

### 6.1 能力四维（兵力布势、任务划分、风险）

| 键 | 中文 | 典型数据依据 |
|----|------|--------------|
| `attack` / 打击 | 进攻、主力推进 | 敌方 offense 集群、高 dps、前线距离 |
| `defense` / 防御 | 防守、固守 | 己方 defense 集群、薄弱点 |
| `detect` / 探测 | 侦察、视野 | `vision.*`, 可见敌军比例、Observer/Overseer |
| `support` / 保障 | 经济、支援、扩张 | workers、基地数、补给、矿物气体 |

### 6.2 单位角色 `role`（capabilities 表 + 集群 role）

| role | 含义 |
|------|------|
| `worker` | 农民 |
| `infantry` / `ranged` | 地面主力 |
| `heavy` / `siege` | 重甲/攻城 |
| `anti_air` | 对空 |
| `harass` | 骚扰 |
| `caster` | 施法 |
| `scout` / `detector` | 侦察/反隐 |

### 6.3 游戏阶段 `game_stage`

| 值 | 含义 |
|----|------|
| `early` | 前期（约 0–4 分钟） |
| `mid` | 中期 |
| `late` | 后期 |

---

## 7. 设计新智能体：输入选型决策树

```
你的智能体要回答什么问题？
│
├─ 「当前事实是什么？」→ 不要新建，用 sensing；或 input_sources=[] + sc2_get_situation（不推荐重复）
├─ 「敌我战力对比？」→ 读 compute（不要自己重算 DPS，除非新算法）
├─ 「兵在哪、前线在哪？」→ 读 disposition（必要时 sc2_get_layer unit）
├─ 「敌人想干什么？」→ 读 intent（+ 上游）
├─ 「最大威胁是什么？」→ 读 risk_id
├─ 「具体怎么打/做什么？」→ 读 risk_response 或在其后新增阶段
└─ 「克制/产兵/科技建议？」→ 读 sensing + compute + sc2_unit_capabilities（见 sc2-counter 案例）
```

**填写 `input_sources` 时**：只列你 **prompt 里会用到** 的键，越少越好，降低 token 与误读。

---

## 8. 离线开发：不跑游戏如何自测

### 8.1 使用 fixtures 样例

目录：`new_agents/fixtures/`（与本文档同包下发）

| 文件 | 模拟 |
|------|------|
| `situation_current.sample.json` | `sc2_get_situation()` |
| `cache_sensing.sample.json` | `cache_get` → `sensing` |
| `cache_compute.sample.json` | `cache_get` → `compute` |
| `cache_disposition.sample.json` | `cache_get` → `disposition` |
| `cache_intent.sample.json` | `cache_get` → `intent` |
| `cache_risk_id.sample.json` | `cache_get` → `risk_id` |

本地测试伪代码：

```python
import json
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
upstream = {
    "sensing": json.loads((FIX / "cache_sensing.sample.json").read_text(encoding="utf-8")),
    "compute": json.loads((FIX / "cache_compute.sample.json").read_text(encoding="utf-8")),
}
# 将 upstream 塞进你的 prompt，检查输出 JSON 是否符合 output_json_schema
```

### 8.2 仅对接主流水线时

由集成同学提供：

1. 一次真实对局跑完后的 `sc2_analysis_cache_get(thread_id)` 导出 JSON；或  
2. Redis 只读访问 + 固定 `thread_id`。

你仍只需遵守：**读指定 `input_sources` → 输出 JSON → `cache_set`**。

### 8.3 输出自检清单

- [ ] 输出可被 `json.loads`，无 markdown 包裹  
- [ ] 字段名与 `output_json_schema` 一致  
- [ ] 未捏造 `thread_id`  
- [ ] 未重复请求 sensing 已包含的原始计数  
- [ ] 中文描述字段（如 `description_cn`）若目标阶段需要则齐全  

---

## 9. MCP / 工具参数速查

| 工具 | 参数 | 返回 |
|------|------|------|
| `sc2_get_situation` | 无 | §3 全量态势 |
| `sc2_get_layer` | `layer_name`: unit/economy/map/vision | 对应子树 JSON |
| `sc2_unit_capabilities` | `unit_names`: string[] 可选 | 单位静态属性表 |
| `sc2_get_trend` | 无 | §4.2 |
| `sc2_get_game_status` | 无 | §4.3 |
| `sc2_analysis_cache_get` | `thread_id`, `agent_name` 可选 | 单阶段或全部 cache |
| `sc2_analysis_cache_set` | `thread_id`, `agent_name`, `payload` 字符串 | 成功/失败 |
| `sc2_send_commands` | `commands`: array | 队列结果 |

---

## 10. 与仪表盘字段的对应（帮助理解业务）

| 仪表盘模块 | 主要消费的 cache 键 | 你还需知的原始字段 |
|------------|---------------------|-------------------|
| 态势感知 S1/S2 | `sensing`, `compute` | `vision`, `economy` |
| 兵力布势 | `disposition`, `compute` | 四维打击/探测/防御/保障 |
| 意图识别 | `intent` | `enemy_clusters`, `intent_dimensions` |
| 风险分析 | `risk_id`, `risk_response` | 风险四维映射 |
| 杀伤链/关键节点 | 多为前端 + LLM 派生 | `disposition`, `intent` |
| 作战方案 | `command_sequence`, workbench | `risk_response` |

---

## 11. 文件索引

| 文件 | 说明 |
|------|------|
| `SC2数据与智能体输入说明.md` | 本文档 |
| `标准智能体模板接入.md` | 模板 API、注册与并入流水线 |
| `run_sc2_deerflow_pipeline.py` | 主流水线编排脚本（副本，供阅读；运行请在项目根） |
| `create_new_agent.py` | 工厂函数 |
| `sc2_agents.py` | 六阶段手写配置（完整 prompt/schema） |
| `sc2_counter.py` | 扩展阶段示例 |
| `fixtures/*.sample.json` | 离线样例数据 |

---

**维护说明**：若主流水线增删阶段或 Redis 键变更，以仓库根目录 `run_sc2_deerflow_pipeline.py`、`sc2_agents.py`、`dashboard_to_redis_bridge.py`、`sc2_redis_tools.py` 为准，并同步更新本目录副本与 fixtures。
