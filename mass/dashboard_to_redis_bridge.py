from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import redis
except ImportError as exc:
    raise SystemExit("redis package not installed. Run: pip install redis>=5.0.0") from exc


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = ROOT_DIR / "dashboard_state.json"

from sc2_redis_keys import game_status_key, layer_key, situation_key

KEY_SPEED_CONFIG = "game:speed_config"


def _redis_client() -> redis.Redis:
    kwargs: dict[str, Any] = {
        "host": os.getenv("REDIS_HOST", "localhost"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "db": int(os.getenv("REDIS_DB", "0")),
        "decode_responses": True,
    }
    password = os.getenv("REDIS_PASSWORD", "")
    if password:
        kwargs["password"] = password
    return redis.Redis(**kwargs)


def _read_json(path: Path, attempts: int = 5, delay_seconds: float = 0.05) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


def _parse_game_seconds(game: dict[str, Any], frame: dict[str, Any] | None) -> float | None:
    if frame and isinstance(frame.get("time_seconds"), (int, float)):
        return float(frame["time_seconds"])
    formatted = str(game.get("timeFormatted") or "")
    if formatted.startswith("t=") and ":" in formatted:
        minutes, seconds = formatted[2:].split(":", 1)
        try:
            return int(minutes) * 60 + int(seconds)
        except ValueError:
            return None
    return None


def _detail_to_unit(detail: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "tag": f"{detail.get('kind', 'unit')}:{detail.get('name', 'UNKNOWN')}:{detail.get('location', '')}:{index}",
        "type": detail.get("name", "UNKNOWN"),
        "count": detail.get("count", 0),
        "location": detail.get("location", ""),
        "position": {"label": detail.get("location", "")},
        "action": detail.get("action", ""),
        "kind": detail.get("kind", "unit"),
    }


def _composition_to_units(items: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        units.append(
            {
                "tag": f"{prefix}:{item.get('name', 'UNKNOWN')}:{index}",
                "type": item.get("name", "UNKNOWN"),
                "count": item.get("count", 0),
            }
        )
    return units


def build_situation(state: dict[str, Any], *, side: str = "") -> dict[str, Any]:
    game = state.get("game") or {}
    frame = state.get("latestFeatureFrame")
    if not isinstance(frame, dict):
        frame = None

    own_details = game.get("ownForceDetails") or []
    enemy_details = game.get("enemyVisibleDetails") or []
    own_units = [_detail_to_unit(item, i) for i, item in enumerate(own_details) if item.get("kind") == "unit"]
    own_structures = [_detail_to_unit(item, i) for i, item in enumerate(own_details) if item.get("kind") != "unit"]
    enemy_units = [_detail_to_unit(item, i) for i, item in enumerate(enemy_details)]
    if not enemy_units:
        enemy_units = _composition_to_units(game.get("enemyVisibleComposition") or [], "enemy")

    raw = {
        "game_loop": frame.get("game_loop") if frame else game.get("iteration", 0),
        "game_time": _parse_game_seconds(game, frame),
        "time_formatted": game.get("timeFormatted") or (frame.get("time_formatted") if frame else ""),
        "iteration": game.get("iteration", 0),
        "updated_at": state.get("updatedAt"),
        "player_id": 2 if (side or "").lower() == "blue" else 1,
        "source": "dashboard_state",
    }
    unit = {
        "own_units": own_units,
        "own_structures": own_structures,
        "enemy_units": enemy_units,
        "own_unit_composition": game.get("ownUnitComposition") or [],
        "enemy_visible_composition": game.get("enemyVisibleComposition") or [],
        "army_count": game.get("armyCount", 0),
        "own_hp_total": game.get("ownHpTotal", 0),
    }
    economy = {
        "minerals": game.get("minerals", 0),
        "vespene": game.get("vespene", 0),
        "supply_used": game.get("supplyUsed", 0),
        "supply_cap": game.get("supplyCap", 0),
        "workers": game.get("workerCount", 0),
        "pending_command_count": game.get("pendingCommandCount", 0),
    }
    map_layer = {
        "map_name": game.get("mapName", ""),
        "map_size": None,
        "start_location": game.get("startLocation", ""),
        "bases": state.get("minimapOverlay") or [],
        "game_stage": game.get("gameStage", ""),
        "feature_session": game.get("featureSession", ""),
    }
    frame = state.get("latestFeatureFrame") if isinstance(state.get("latestFeatureFrame"), dict) else {}
    frame_vision = frame.get("vision") if isinstance(frame.get("vision"), dict) else {}
    vision = {
        "visible_tiles_pct": frame_vision.get("visible_tiles_pct"),
        "fog_of_war": frame_vision.get("fog_of_war_pct"),
        "enemy_base_visible": bool(game.get("enemyStructuresVisible", 0)),
        "enemy_units_visible": game.get("enemyUnitsVisible", 0),
        "enemy_structures_visible": game.get("enemyStructuresVisible", 0),
    }
    return {
        "raw": raw,
        "unit": unit,
        "economy": economy,
        "map": map_layer,
        "vision": vision,
        "messages": state.get("messages") or {},
        "command_sequence": state.get("commandSequence") or [],
        "agent_workbench": state.get("agentWorkbench") or {},
    }


def build_game_status(state: dict[str, Any]) -> dict[str, Any]:
    game = state.get("game") or {}
    frame = state.get("latestFeatureFrame")
    if not isinstance(frame, dict):
        frame = None
    status = state.get("status") or "idle"
    updated_at = state.get("updatedAt")
    return {
        "status": status,
        "game_id": updated_at or datetime.now(timezone.utc).isoformat(),
        "map_name": game.get("mapName", ""),
        "result": None,
        "game_stage": game.get("gameStage", ""),
        "iteration": game.get("iteration", 0),
        "game_loop": frame.get("game_loop") if frame else game.get("iteration", 0),
        "time_formatted": game.get("timeFormatted") or (frame.get("time_formatted") if frame else ""),
        "updated_at": updated_at,
        "source": "dashboard_state",
    }


def run_persistent_mirror_to_redis(
    state_path: Path,
    *,
    side: str = "",
    poll_interval_s: float = 0.5,
    ttl_seconds: int = 30,
    read_attempts: int = 5,
    once: bool = False,
    log_prefix: str = "",
) -> None:
    """阻塞运行：监控 `dashboard_state.json`（或给定路径），将态势同步到 Redis。"""
    state_path = state_path.resolve()
    prefix = f"{log_prefix} " if log_prefix else ""

    while True:
        try:
            client = _redis_client()
            client.ping()
            break
        except Exception as exc:
            print(f"{prefix}Redis 不可用，{poll_interval_s:.1f}s 后重试: {exc}", flush=True)
            time.sleep(max(0.2, poll_interval_s))

    last_mtime = 0.0
    last_sync_at = 0.0
    refresh_interval = max(1.0, min(ttl_seconds / 3, 10.0))
    while True:
        if not state_path.exists():
            print(f"{prefix}等待态势文件 {state_path.name} …", flush=True)
            time.sleep(poll_interval_s)
            if once:
                return
            continue

        now = time.monotonic()
        current_mtime = state_path.stat().st_mtime
        should_refresh_ttl = now - last_sync_at >= refresh_interval
        if once or current_mtime != last_mtime or should_refresh_ttl:
            try:
                status = sync_once(
                    client,
                    state_path,
                    ttl_seconds=ttl_seconds,
                    read_attempts=read_attempts,
                    side=side,
                )
            except (OSError, json.JSONDecodeError) as exc:
                if once:
                    raise
                print(f"{prefix}跳过同步（读取失败）: {exc}", flush=True)
                time.sleep(poll_interval_s)
                continue
            last_mtime = current_mtime
            last_sync_at = now
            print(
                f"{prefix}已同步 {state_path.name}: status={status.get('status')} "
                f"iteration={status.get('iteration')} loop={status.get('game_loop')}",
                flush=True,
            )
        if once:
            break
        time.sleep(poll_interval_s)


def sync_once(
    client: redis.Redis,
    state_path: Path,
    ttl_seconds: int = 10,
    read_attempts: int = 5,
    side: str = "",
) -> dict[str, Any]:
    state = _read_json(state_path, attempts=read_attempts)
    situation = build_situation(state, side=side)
    status = build_game_status(state)
    speed_config = {"speed": 1, "paused": False}

    sit_key = situation_key(side)
    stat_key = game_status_key(side)
    pipe = client.pipeline()
    pipe.set(sit_key, json.dumps(situation, ensure_ascii=False), ex=ttl_seconds)
    pipe.set(stat_key, json.dumps(status, ensure_ascii=False), ex=ttl_seconds)
    pipe.set(KEY_SPEED_CONFIG, json.dumps(speed_config, ensure_ascii=False), ex=ttl_seconds)
    if side == "red":
        pipe.set(situation_key(""), json.dumps(situation, ensure_ascii=False), ex=ttl_seconds)
        pipe.set(game_status_key(""), json.dumps(status, ensure_ascii=False), ex=ttl_seconds)
    for layer_name in ("unit", "economy", "map", "vision"):
        pipe.set(
            layer_key(layer_name, side),
            json.dumps(situation[layer_name], ensure_ascii=False),
            ex=ttl_seconds,
        )
    pipe.execute()
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Mirror dashboard_state.json into Redis for the SC2 MCP server.")
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="Path to dashboard_state.json.")
    parser.add_argument("--interval", type=float, default=0.5, help="Polling interval in seconds.")
    parser.add_argument("--ttl", type=int, default=30, help="Redis key TTL in seconds.")
    parser.add_argument("--read-retries", type=int, default=5, help="Retries when dashboard_state.json is being rewritten.")
    parser.add_argument("--once", action="store_true", help="Sync once and exit.")
    args = parser.parse_args()

    state_path = Path(args.state).resolve()
    if args.once and not state_path.exists():
        raise SystemExit(f"State file not found: {state_path}")
    try:
        run_persistent_mirror_to_redis(
            state_path,
            poll_interval_s=args.interval,
            ttl_seconds=args.ttl,
            read_attempts=args.read_retries,
            once=args.once,
        )
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
