# LLM 对局日志使用说明

实现了 `docs/requirements/swarmbrain_llm_logging_requirements.md` 中的磁盘 JSONL 日志。

## 输出位置

默认：

```text
<模式包>/logs/runs/<run_id>/
  run_manifest.json
  run_events.jsonl
  llm_events.jsonl
  command_events.jsonl
  errors.jsonl
```

可通过环境变量覆盖根目录：

```powershell
$env:SWARMBRAIN_RUN_LOG_DIR = "E:\data\swarmbrain_runs"
```

`run_id` 格式：`YYYYMMDDTHHMMSSZ_<uuid8>`。

## 已接入内容

- 开局三次：`opening`（`game_loop` 可为 `null`）
- 对局中：`macro` / `building` / `attack`
- 命令：`proposed` / `accepted|filtered` / `dispatched`
- 对局结束：replay 路径 + SHA256 写入 manifest
- 双智能体：红蓝共享同一 `run_id`，用 `agent_side` 区分

## 校验

```powershell
cd multi_agent_duel   # 或 single_agent_pve
python scripts\validate_run_logs.py --run-dir "logs\runs\<run_id>"
python -m unittest tests.test_run_logger -v
```

## 相关文件

- `swarmbrain_run_logger.py`
- `swarmbrain_run_log_hooks.py`
- `swarmbrain.py`（底层 invoke 记录 prompt/response）
- `swarmbrain_launch.py`（生命周期与命令流水线）
