"""Launch one Zerg vs Medium Terran game without calling a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import traceback
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
os.environ.setdefault("SC2PATH", os.getenv("SWARMBRAIN_SC2PATH", r"E:\star_carft\StarCraft II"))

from sc2 import maps
from sc2 import main as sc2_main
from sc2.data import Difficulty, Race, Result
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.sc2process import SC2Process as OrigSC2Process

from local_joint_debug import BASE_URL, read_models
from local_model_live import match_runs_realtime
from local_rule_bot import LocalRuleBot
from local_rule_config import PACKAGE_ROOT, MatchConfig, load_match_config
from local_rule_plan import model_actions_from_record


def main() -> int:
    parser = argparse.ArgumentParser(description="Play one no-model Zerg game from configs/match.json")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--model-record", type=Path, default=None)
    parser.add_argument("--call-model", action="store_true")
    parser.add_argument("--replay", type=Path, default=None)
    args = parser.parse_args()
    config = load_match_config(args.config)
    game_id = "local-rule-" + datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = PACKAGE_ROOT / "outputs" / "local_rule" / game_id
    output_dir.mkdir(parents=True, exist_ok=True)
    sc2_path = Path(os.environ["SC2PATH"])
    map_file = find_map_file(sc2_path, config.map_name)
    model_actions = []
    model_record_path = None
    if args.model_record is not None:
        model_record_path = args.model_record.resolve()
        model_actions = model_actions_from_record(json.loads(model_record_path.read_text(encoding="utf-8")))
    replay_path = args.replay.resolve() if args.replay is not None else replay_destination(config)
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "game_id": game_id,
        "code_path": str(PACKAGE_ROOT),
        "swarmbrain_commit": None,
        "branch": None,
        "git_note": "当前 SwarmBrain_re 目录不是 git 仓库，还对不上上游 commit。",
        "model_called": bool(args.call_model),
        "model_actions_source": None if model_record_path is None else str(model_record_path),
        "model_actions": model_actions,
        "sc2_path": str(sc2_path),
        "sc2_version": read_sc2_version(sc2_path),
        "map_name": config.map_name,
        "map_path": str(map_file),
        "map_sha256": sha256_file(map_file),
        "difficulty": config.difficulty,
        "own_race": config.own_race,
        "opponent": config.opponent,
        "opponent_race": config.opponent_race,
        "spawn_direction_config": config.spawn_direction,
        "spawn_direction_resolved": None,
        "opening": config.opening.__dict__,
        "realtime": match_runs_realtime(args.call_model, config.realtime),
        "match_time_limit_sec": config.match_time_limit_sec,
        "replay": str(replay_path),
        "match_completed": False,
        "result": None,
        "failure_point": None,
        "observation_sample": None,
    }
    write_json(output_dir / "local_record.json", record)
    print(
        f"[local-rule] {config.own_race} vs {config.opponent_race} {config.difficulty} "
        f"map={config.map_name} model_called={str(bool(args.call_model)).lower()} "
        f"realtime={str(record['realtime']).lower()}",
        flush=True,
    )
    print(f"[local-rule] map_sha256={record['map_sha256']}", flush=True)
    print(f"[local-rule] record={output_dir / 'local_record.json'}", flush=True)

    print(f"[local-rule] model_actions={json.dumps(model_actions, ensure_ascii=False)}", flush=True)
    if args.call_model:
        model_check = read_models(BASE_URL)
        if not model_check["accepted"]:
            record["match_completed"] = False
            record["result"] = "model_unavailable"
            record["failure_point"] = "未看到模型 swarmbrain-qwen3-4b，已停止，没有开局"
            record["model_check"] = model_check
            write_json(output_dir / "local_record.json", record)
            print(record["failure_point"], flush=True)
            return 2
        print(f"[local-rule] model={BASE_URL} accepted={model_check['model_ids']}", flush=True)
    bot = LocalRuleBot(config, game_id, output_dir, model_actions=model_actions, call_model=args.call_model)
    result = None
    try:
        window = install_windowed_sc2()
        record["window"] = window
        result = run_game(
            maps.get(config.map_name),
            [
                Bot(Race[config.own_race], bot),
                Computer(Race[config.opponent_race], Difficulty[config.difficulty]),
            ],
            realtime=record["realtime"],
            save_replay_as=str(replay_path),
            game_time_limit=config.match_time_limit_sec,
        )
        record["match_completed"] = True
        record["result"] = result_name(result)
    except Exception as exc:
        record["match_completed"] = False
        record["result"] = "crashed"
        record["failure_point"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()
        print(record["traceback"], flush=True)
    finally:
        fill_game_record(record, bot, result, replay_path)
        write_json(output_dir / "local_record.json", record)
        if bot.latest_observation is not None:
            write_json(output_dir / "observation_final.json", bot.latest_observation)
        print(
            f"[local-rule] completed={record['match_completed']} result={record['result']} "
            f"failure={record['failure_point']}",
            flush=True,
        )
    return 0 if record["match_completed"] else 1


def fill_game_record(record: dict, bot: LocalRuleBot, result, replay_path: Path) -> None:
    if bot.spawn:
        record["spawn_direction_resolved"] = bot.spawn["side"]
        record["spawn"] = bot.spawn
    if bot.enemy_spawns:
        record["enemy_start_labels"] = bot.enemy_spawns
    if bot.sample_path:
        record["observation_sample"] = str(bot.sample_path)
    elif bot.latest_observation is not None and bot.output_dir:
        sample = bot.output_dir / "observation_sample.json"
        write_json(sample, bot.latest_observation)
        record["observation_sample"] = str(sample)
    record["model_request_count"] = bot.model_request_count
    record["model_executed_count"] = bot.model_executed_count
    record["model_request_paths"] = list(bot.model_request_paths)
    record["game_time_s"] = None if bot.latest_observation is None else bot.latest_observation["game"]["game_time_s"]
    record["replay_exists"] = replay_path.is_file()
    record["failure_hints"] = list(bot.failure_hints)
    if record.get("failure_point"):
        return
    record["failure_point"] = describe_failure(record.get("result"), bot, replay_path)


def describe_failure(result_name_value, bot: LocalRuleBot, replay_path: Path) -> str | None:
    if result_name_value == "Victory":
        return None
    hints = "；".join(bot.failure_hints)
    if result_name_value == "Defeat":
        if bot.call_model:
            calls = f"对局中发出 {bot.model_request_count} 条模型请求，执行 {bot.model_executed_count} 条。"
            return f"{calls}{hints}" if hints else calls
        return hints or "内置 AI 获胜。规则骨架没有调用模型。"
    if result_name_value == "Tie":
        limit = bot.config.match_time_limit_sec
        base = f"打到配置时限 {limit}s，对局记为平局"
        return f"{base}。{hints}" if hints else base
    if result_name_value == "crashed":
        return "对局进程异常退出"
    if not replay_path.is_file():
        return hints or "对局没有留下回放"
    return hints or None


def result_name(result) -> str:
    if isinstance(result, Result):
        return result.name
    return str(result)


def find_map_file(sc2_path: Path, map_name: str) -> Path:
    maps_dir = sc2_path / "Maps"
    direct = maps_dir / f"{map_name}.SC2Map"
    if direct.is_file():
        return direct
    for path in maps_dir.glob(f"*/*{map_name}.SC2Map"):
        return path
    raise FileNotFoundError(f"找不到地图 {map_name}，已检查 {maps_dir}")


def read_sc2_version(sc2_path: Path) -> str | None:
    info = sc2_path / ".build.info"
    if not info.is_file():
        return None
    lines = [line for line in info.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    names = [item.split("!")[0].strip() for item in lines[0].split("|")]
    values = lines[1].split("|")
    if "Version" not in names:
        return None
    return values[names.index("Version")] or None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replay_destination(config: MatchConfig) -> Path:
    path = Path(config.replay_rel)
    if path.is_absolute():
        return path
    return (PACKAGE_ROOT / path).resolve()


def install_windowed_sc2() -> dict:
    width = int(os.getenv("SWARMBRAIN_WINDOW_WIDTH", "1280"))
    height = int(os.getenv("SWARMBRAIN_WINDOW_HEIGHT", "720"))
    pos_x = int(os.getenv("SWARMBRAIN_WINDOW_X", "80"))
    pos_y = int(os.getenv("SWARMBRAIN_WINDOW_Y", "80"))

    class WindowedSC2Process(OrigSC2Process):
        def __init__(self, *args, **kwargs):
            kwargs["fullscreen"] = False
            kwargs["resolution"] = (width, height)
            kwargs["placement"] = (pos_x, pos_y)
            super().__init__(*args, **kwargs)

    sc2_main.SC2Process = WindowedSC2Process
    return {"fullscreen": False, "window_width": width, "window_height": height, "window_x": pos_x, "window_y": pos_y}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
