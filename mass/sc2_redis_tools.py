"""
Async Redis-backed SC2 tool implementations shared by MCP and the DeerFlow-style pipeline.

Kept synchronous with mcp_server/sc2_mcp_server.py semantics; MCP layer is a thin FastMCP wrapper.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_MCP_DIR = _ROOT / "mcp_server"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

try:
    import redis.asyncio as aioredis
except ImportError as e:
    raise ImportError("redis package required. pip install redis>=5.0.0") from e

from unit_stats import UNIT_STATS, get_unit_stat  # noqa: E402

from sc2_redis_keys import commands_key, game_status_key, layer_key, situation_key, trend_anchor_key


# Redis keys — keep aligned with mcp_server/sc2_mcp_server.py / bot bridge
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

KEY_SPEED_CONFIG = "game:speed_config"

KEY_ANALYSIS_PREFIX = "sc2:analysis:"
KEY_REPLAY_STATUS = "replay:status"
KEY_REPLAY_TIMELINE = "replay:timeline"

VALID_CMD_TYPES = {"attack_move", "attack", "move", "retreat", "build", "train", "annotation"}


def _make_redis() -> aioredis.Redis:
    kwargs = {"host": REDIS_HOST, "port": REDIS_PORT, "db": REDIS_DB, "decode_responses": True}
    if REDIS_PASSWORD:
        kwargs["password"] = REDIS_PASSWORD
    return aioredis.Redis(**kwargs)


def _econ(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot.get("economy") or {}


def _unit(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot.get("unit") or {}


def _vision(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot.get("vision") or {}


async def sc2_get_situation() -> str:
    r = _make_redis()
    try:
        data = await r.get(situation_key())
        if not data:
            return json.dumps(
                {
                    "error": "No situation data available.",
                    "hint": "Start SwarmBrain / bot so situation:current is populated in Redis.",
                },
                indent=2,
            )
        return data
    finally:
        await r.aclose()


async def sc2_get_layer(layer_name: str) -> str:
    valid_layers = {"unit", "economy", "map", "vision"}
    if layer_name not in valid_layers:
        return json.dumps(
            {"error": f"Invalid layer '{layer_name}'. Valid: {sorted(valid_layers)}"},
            indent=2,
        )

    r = _make_redis()
    try:
        ly_key = layer_key(layer_name)
        data = await r.get(ly_key)
        if data:
            return data

        full = await r.get(situation_key())
        if full:
            parsed = json.loads(full)
            layer_data = parsed.get(layer_name)
            if layer_data:
                return json.dumps(layer_data, indent=2)

        return json.dumps(
            {"error": f"No data for layer '{layer_name}'. Is the game running?"},
            indent=2,
        )
    finally:
        await r.aclose()


async def sc2_send_commands(commands: list[dict[str, Any]]) -> str:
    if not commands:
        return json.dumps({"error": "No commands provided."}, indent=2)

    if len(commands) > 50:
        return json.dumps(
            {"error": f"Too many commands: {len(commands)}. Maximum is 50 per call."},
            indent=2,
        )

    for i, cmd in enumerate(commands):
        cmd_type = cmd.get("type", "")
        if cmd_type not in VALID_CMD_TYPES:
            return json.dumps(
                {
                    "error": f"Command #{i} has invalid type '{cmd_type}'. Valid: {sorted(VALID_CMD_TYPES)}",
                    "command": cmd,
                },
                indent=2,
            )

    r = _make_redis()
    try:
        for cmd in commands:
            await r.rpush(commands_key(), json.dumps(cmd))

        return json.dumps(
            {
                "status": "ok",
                "commands_queued": len(commands),
                "message": f"{len(commands)} command(s) queued. Bot executes on next step (~500ms).",
            },
            indent=2,
        )
    finally:
        await r.aclose()


async def sc2_get_game_status() -> str:
    r = _make_redis()
    try:
        status_raw = await r.get(game_status_key())
        game_status = json.loads(status_raw) if status_raw else {"status": "idle"}

        speed_raw = await r.get(KEY_SPEED_CONFIG)
        speed_config = json.loads(speed_raw) if speed_raw else {"speed": 1, "paused": False}

        situation_exists = await r.exists(situation_key())

        result = {
            **game_status,
            "speed": speed_config.get("speed", 1),
            "paused": speed_config.get("paused", False),
            "bot_running": bool(situation_exists),
        }

        return json.dumps(result, indent=2)
    finally:
        await r.aclose()


async def sc2_unit_capabilities(unit_names: list[str] | None = None) -> str:
    if not unit_names:
        return json.dumps(UNIT_STATS, indent=2, ensure_ascii=False)
    result = {name: get_unit_stat(name) for name in unit_names}
    return json.dumps(result, indent=2, ensure_ascii=False)


async def sc2_analysis_cache_get(thread_id: str, agent_name: str | None = None) -> str:
    if not thread_id:
        return json.dumps({"error": "thread_id is required."}, indent=2)
    r = _make_redis()
    try:
        if agent_name:
            data = await r.get(f"{KEY_ANALYSIS_PREFIX}{thread_id}:{agent_name}")
            if not data:
                return json.dumps({"error": f"No data for {agent_name}."}, indent=2)
            return data
        pattern = f"{KEY_ANALYSIS_PREFIX}{thread_id}:*"
        keys = await r.keys(pattern)
        if not keys:
            return json.dumps({}, indent=2)
        values = await r.mget(keys)
        out: dict[str, Any] = {}
        prefix_len = len(f"{KEY_ANALYSIS_PREFIX}{thread_id}:")
        for k, v in zip(keys, values):
            if v is None or str(k).endswith(":_order"):
                continue
            try:
                out[k[prefix_len:]] = json.loads(v)
            except json.JSONDecodeError:
                out[k[prefix_len:]] = v
        return json.dumps(out, indent=2, ensure_ascii=False)
    finally:
        await r.aclose()


async def sc2_analysis_cache_set(thread_id: str, agent_name: str, payload: str) -> str:
    if not (thread_id and agent_name and payload):
        return json.dumps({"error": "thread_id, agent_name, payload required."}, indent=2)
    try:
        json.loads(payload)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"payload must be valid JSON: {e}"}, indent=2)

    r = _make_redis()
    try:
        key = f"{KEY_ANALYSIS_PREFIX}{thread_id}:{agent_name}"
        await r.set(key, payload, ex=3600)
        await r.rpush(f"{KEY_ANALYSIS_PREFIX}{thread_id}:_order", agent_name)
        await r.expire(f"{KEY_ANALYSIS_PREFIX}{thread_id}:_order", 3600)
        return json.dumps({"status": "ok", "key": key, "bytes": len(payload)}, indent=2)
    finally:
        await r.aclose()


async def sc2_get_trend() -> str:
    """Approximate supply/army deltas vs the snapshot from the previous call (same Redis anchor).

    No wall-clock ~30s history is stored today; callers should treat this as sequential delta.
    """
    r = _make_redis()
    try:
        cur_raw = await r.get(situation_key())
        if not cur_raw:
            return json.dumps({"error": "No situation data for trend baseline."}, indent=2)

        cur = json.loads(cur_raw)
        prev_raw = await r.get(trend_anchor_key())
        ec = _econ(cur)
        uc = _unit(cur)
        vc = _vision(cur)
        own_supply = int(ec.get("supply_used") or 0)
        own_army = int(uc.get("army_count") or 0)
        enemy_vis = int(vc.get("enemy_units_visible") or 0)

        deltas = {
            "own_supply_delta": 0,
            "enemy_supply_delta": 0,
            "own_army_value_delta": 0,
            "enemy_army_value_delta": 0,
        }
        note = (
            "Deltas vs previous pipeline anchor in Redis (situation:trend_anchor), not calibrated to 30s wall time."
        )
        if prev_raw:
            prev = json.loads(prev_raw)
            pe = _econ(prev)
            pu = _unit(prev)
            pv = _vision(prev)
            deltas["own_supply_delta"] = own_supply - int(pe.get("supply_used") or 0)
            # proxy: visible enemy units as coarse "enemy pressure"
            deltas["enemy_supply_delta"] = enemy_vis - int(pv.get("enemy_units_visible") or 0)
            deltas["own_army_value_delta"] = own_army - int(pu.get("army_count") or 0)
            deltas["enemy_army_value_delta"] = deltas["enemy_supply_delta"]

        await r.set(trend_anchor_key(), cur_raw, ex=3600)
        return json.dumps({"trend_30s_approx": deltas, "note": note}, indent=2, ensure_ascii=False)
    finally:
        await r.aclose()


async def sc2_replay_list() -> str:
    r = _make_redis()
    try:
        data = await r.get("replay:catalog")
        if not data:
            return json.dumps(
                {
                    "error": "Replay catalog not ready.",
                    "hint": "Populate via backend replay API when available.",
                },
                indent=2,
            )
        return data
    finally:
        await r.aclose()


async def sc2_replay_load(replay_id: str, observed_player: int = 1) -> str:
    r = _make_redis()
    try:
        req = {"replay_id": replay_id, "observed_player": observed_player}
        await r.set("replay:load_request", json.dumps(req), ex=60)
        return json.dumps({"status": "requested", "request": req}, indent=2)
    finally:
        await r.aclose()


async def sc2_replay_seek(game_loop: int) -> str:
    r = _make_redis()
    try:
        await r.set("replay:seek_request", json.dumps({"game_loop": int(game_loop)}), ex=60)
        return json.dumps({"status": "requested", "game_loop": int(game_loop)}, indent=2)
    finally:
        await r.aclose()


async def sc2_replay_timeline() -> str:
    r = _make_redis()
    try:
        data = await r.get(KEY_REPLAY_TIMELINE)
        if not data:
            return json.dumps({"error": "No timeline. Load a replay first."}, indent=2)
        return data
    finally:
        await r.aclose()


async def sc2_replay_status() -> str:
    r = _make_redis()
    try:
        data = await r.get(KEY_REPLAY_STATUS)
        if not data:
            return json.dumps({"status": "idle"}, indent=2)
        return data
    finally:
        await r.aclose()


# All coroutines addressable by tool name for the LangChain binder
SC2_TOOL_COROS: dict[str, Any] = {
    "sc2_get_situation": sc2_get_situation,
    "sc2_get_layer": sc2_get_layer,
    "sc2_send_commands": sc2_send_commands,
    "sc2_get_game_status": sc2_get_game_status,
    "sc2_unit_capabilities": sc2_unit_capabilities,
    "sc2_analysis_cache_get": sc2_analysis_cache_get,
    "sc2_analysis_cache_set": sc2_analysis_cache_set,
    "sc2_get_trend": sc2_get_trend,
    "sc2_replay_list": sc2_replay_list,
    "sc2_replay_load": sc2_replay_load,
    "sc2_replay_seek": sc2_replay_seek,
    "sc2_replay_timeline": sc2_replay_timeline,
    "sc2_replay_status": sc2_replay_status,
}
