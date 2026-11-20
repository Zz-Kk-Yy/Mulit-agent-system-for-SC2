#!/usr/bin/env python3
"""
Sequential SC2 「DeerFlow 风格」六阶段子 Agent 编排（不经官方 deer-flow Wheel：见 README / 仓库内 deerflow/ 存根）。

【本目录副本说明】
  此文件与 SwarmBrain 项目根目录 `run_sc2_deerflow_pipeline.py` 内容一致，随 new_agents 包下发，
  便于阅读编排逻辑（SC2_SUBAGENT_ORDER、thread_id 注入、工具绑定等）。
  **实际运行**请在项目根目录执行（需同级的 sc2_redis_tools.py、swarmbrain_llm_shared.py、.env、Redis 等）：
    cd <SwarmBrain 根目录>
    python run_sc2_deerflow_pipeline.py --thread-id my-run-1

依赖：Redis 中已有 `situation:current`（如已运行 swarmbrain_launch + 对局），以及 `swarmbrain_llm_shared` 所配 LLM；本仓库已含 `deerflow/` 存根以加载 `sc2_agents.py`（官方 harness 未发 PyPI）。

用法:
  python run_sc2_deerflow_pipeline.py
  python run_sc2_deerflow_pipeline.py --replay --thread-id my-run-1
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from sc2_agents import ALL_SC2_SUBAGENTS
from sc2_redis_tools import SC2_TOOL_COROS
from swarmbrain_llm_shared import get_swarmbrain_llm_credentials

SC2_SUBAGENT_ORDER = [
    "sc2-sensing",
    "sc2-compute",
    "sc2-disposition",
    "sc2-intent",
    "sc2-risk-id",
    "sc2-risk-response",
]

SC2_TOOL_DOCS: dict[str, str] = {
    "sc2_get_situation": "Read full SC2 situation JSON from Redis.",
    "sc2_get_layer": "Read one layer: unit|economy|map|vision.",
    "sc2_send_commands": "Queue bot commands (live mode / approved only).",
    "sc2_get_game_status": "Redis game/bot status JSON.",
    "sc2_unit_capabilities": "Static unit stats; pass unit_names or omit for full table.",
    "sc2_analysis_cache_get": "Read prior stage JSON for thread_id.",
    "sc2_analysis_cache_set": "Persist this stage JSON output to Redis.",
    "sc2_get_trend": "Approximate deltas vs previous trend anchor snapshot.",
    "sc2_replay_list": "List cached replays.",
    "sc2_replay_load": "Request loading a replay.",
    "sc2_replay_seek": "Seek replay time.",
    "sc2_replay_timeline": "Replay keyframes.",
    "sc2_replay_status": "Replay status.",
}


def _build_stage_tools(cfg_name: str, tool_names: list[str] | None, replay_mode: bool) -> list[StructuredTool]:
    names = list(tool_names or [])
    if replay_mode and cfg_name == "sc2-risk-response":
        names = [n for n in names if n != "sc2_send_commands"]
    tools: list[StructuredTool] = []
    for n in names:
        coro = SC2_TOOL_COROS.get(n)
        if coro is None:
            raise KeyError(f"Unknown tool '{n}' for stage {cfg_name}")
        doc = SC2_TOOL_DOCS.get(n) or (inspect.getdoc(coro) or n)
        tools.append(StructuredTool.from_function(coroutine=coro, name=n, description=doc))
    return tools


async def _invoke_sc2_tool(name: str, raw: dict | None) -> str:
    fn = SC2_TOOL_COROS[name]
    sig = inspect.signature(fn)
    args = dict(raw or {})
    kwargs = {k: v for k, v in args.items() if k in sig.parameters}
    return await fn(**kwargs)


async def run_stage(
    *,
    cfg,
    thread_id: str,
    mode: str,
    model: ChatOpenAI,
    replay_mode: bool,
) -> str:
    tools = _build_stage_tools(cfg.name, cfg.tools, replay_mode=replay_mode)
    if not tools:
        raise RuntimeError(f"Stage {cfg.name} has no tools bound.")

    bound = model.bind_tools(tools)
    user_block = (
        f"thread_id={thread_id}\n"
        f"mode={mode}\n\n"
        "Execute your stage now. Follow your system prompt: read upstream via sc2_analysis_cache_get, "
        "then write your final JSON with sc2_analysis_cache_set. End with a single JSON object in assistant text."
    )
    messages = [SystemMessage(content=cfg.system_prompt), HumanMessage(content=user_block)]

    last_text = ""
    for _ in range(cfg.max_turns):
        msg = await bound.ainvoke(messages)
        messages.append(msg)
        if isinstance(msg, AIMessage) and msg.content:
            last_text = str(msg.content)
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tid = tc.get("id") or ""
                name = tc.get("name", "")
                args = tc.get("args") or tc.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                try:
                    out = await _invoke_sc2_tool(name, args if isinstance(args, dict) else {})
                except Exception as e:
                    out = json.dumps({"error": str(e), "tool": name}, ensure_ascii=False)
                messages.append(ToolMessage(content=out, tool_call_id=tid))
            continue

        # no tool calls — treat as final
        break

    return last_text


def _stage_agent_key(stage_name: str) -> str:
    """Map SubagentConfig.name -> sc2_analysis_cache_set agent_name."""
    mapping = {
        "sc2-sensing": "sensing",
        "sc2-compute": "compute",
        "sc2-disposition": "disposition",
        "sc2-intent": "intent",
        "sc2-risk-id": "risk_id",
        "sc2-risk-response": "risk_response",
    }
    return mapping.get(stage_name, stage_name.replace("sc2-", "").replace("-", "_"))


def _extract_json_dict(text: str) -> dict | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        parsed = json.loads(s)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Best-effort: grab first JSON object block from free text.
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    blob = m.group(0)
    try:
        parsed = json.loads(blob)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


async def _async_main(thread_id: str, replay_mode: bool) -> None:
    p = get_swarmbrain_llm_credentials()
    model = ChatOpenAI(
        model=p["model"],
        api_key=p["api_key"],
        base_url=p["base_url"],
        temperature=float(p["temperature"]),
    )
    analysis_mode = "replay" if replay_mode else "live"

    print(f"[pipeline] thread_id={thread_id} mode={analysis_mode}", flush=True)
    for key in SC2_SUBAGENT_ORDER:
        cfg = ALL_SC2_SUBAGENTS[key]
        print(f"[pipeline] --- stage {cfg.name} ---", flush=True)
        try:
            stage_text = await asyncio.wait_for(
                run_stage(
                    cfg=cfg,
                    thread_id=thread_id,
                    mode=analysis_mode,
                    model=model,
                    replay_mode=replay_mode,
                ),
                timeout=float(cfg.timeout_seconds),
            )
            # Hardening: if model forgot sc2_analysis_cache_set, force-write parsable JSON.
            agent_key = _stage_agent_key(cfg.name)
            existing = await SC2_TOOL_COROS["sc2_analysis_cache_get"](thread_id, agent_key)
            try:
                existing_obj = json.loads(existing)
            except Exception:
                existing_obj = {}
            if not isinstance(existing_obj, dict) or bool(existing_obj.get("error")):
                stage_obj = _extract_json_dict(stage_text)
                if isinstance(stage_obj, dict) and stage_obj:
                    payload = json.dumps(stage_obj, ensure_ascii=False)
                    await SC2_TOOL_COROS["sc2_analysis_cache_set"](
                        thread_id=thread_id,
                        agent_name=agent_key,
                        payload=payload,
                    )
                    print(f"[pipeline] cache backfill -> {agent_key}", flush=True)
                else:
                    print(f"[pipeline] warning: no parsable JSON to backfill for {agent_key}", flush=True)
        except asyncio.TimeoutError:
            print(f"[pipeline] TIMEOUT after {cfg.timeout_seconds}s — {cfg.name}", file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run 6-stage SC2 analysis pipeline (Redis + LangChain tools).")
    ap.add_argument("--thread-id", default="", help="Cache key prefix; default random UUID.")
    ap.add_argument(
        "--replay",
        action="store_true",
        help="Replay analysis: do not expose sc2_send_commands in risk-response stage.",
    )
    args = ap.parse_args()
    tid = args.thread_id.strip() or str(uuid.uuid4())
    asyncio.run(_async_main(tid, replay_mode=bool(args.replay)))
    print(f"[pipeline] done. Inspect Redis keys sc2:analysis:{tid}:*", flush=True)


if __name__ == "__main__":
    main()
