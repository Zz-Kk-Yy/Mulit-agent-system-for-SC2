#!/usr/bin/env python3
"""Baseline unmodified-prompt PvE win-rate experiment.

Uses the latest mass/.env LLM credentials (qwen3.7-flash / DashScope).
Does not change Overmind prompts. Plays SwarmBrain vs Terran Medium.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PVE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PVE_ROOT.parent
MASS_ENV = REPO_ROOT / "mass" / ".env"
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
LAUNCH = PVE_ROOT / "swarmbrain_launch.py"
SC2_PATH_DEFAULT = r"E:\star_carft\StarCraft II"
SC2_PROCESS_NAMES = (
    "SC2_x64",
    "SC2Switcher_x64",
    "SC2",
    "StarCraft II",
    "SC2Switcher",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_dotenv_map(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        from dotenv import dotenv_values
    except ImportError:
        dotenv_values = None
    if dotenv_values is not None:
        raw = dotenv_values(path) or {}
        for key, value in raw.items():
            if key and isinstance(value, str) and value.strip():
                values[str(key)] = value.strip()
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def apply_mass_api_to_pve_env() -> dict[str, str]:
    mass = load_dotenv_map(MASS_ENV)
    wanted = (
        "SWARMBRAIN_LLM_API_KEY",
        "SWARMBRAIN_LLM_MODEL",
        "SWARMBRAIN_LLM_BASE_URL",
        "SWARMBRAIN_LLM_TEMPERATURE",
    )
    copied = {key: mass[key] for key in wanted if mass.get(key)}
    pve_env_path = PVE_ROOT / ".env"
    existing = load_dotenv_map(pve_env_path)
    merged = dict(existing)
    merged.update(copied)
    merged.setdefault("SC2PATH", os.getenv("SC2PATH", SC2_PATH_DEFAULT))
    merged.setdefault("SWARMBRAIN_SC2PATH", merged["SC2PATH"])
    lines = [
        "# SwarmBrain 运行所需个人配置（含真实 API Key，请勿提交到公开仓库）",
        "",
        f"SWARMBRAIN_LLM_API_KEY={merged.get('SWARMBRAIN_LLM_API_KEY', '')}",
        f"SWARMBRAIN_LLM_MODEL={merged.get('SWARMBRAIN_LLM_MODEL', 'qwen3.7-flash')}",
        f"SWARMBRAIN_LLM_BASE_URL={merged.get('SWARMBRAIN_LLM_BASE_URL', '')}",
        f"SWARMBRAIN_LLM_TEMPERATURE={merged.get('SWARMBRAIN_LLM_TEMPERATURE', '0.35')}",
        "",
        f"SC2PATH={merged.get('SC2PATH', SC2_PATH_DEFAULT)}",
        f"SWARMBRAIN_SC2PATH={merged.get('SWARMBRAIN_SC2PATH', merged.get('SC2PATH', SC2_PATH_DEFAULT))}",
        "SWARMBRAIN_GAME_EMBED_URL=/game-view/",
        "SWARMBRAIN_ASK_HEURISTIC=1",
        "",
    ]
    pve_env_path.write_text("\n".join(lines), encoding="utf-8")
    return copied


def child_env(mass_api: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    sc2 = env.get("SC2PATH") or env.get("SWARMBRAIN_SC2PATH") or SC2_PATH_DEFAULT
    env["SC2PATH"] = sc2
    env["SWARMBRAIN_SC2PATH"] = sc2
    env.update(mass_api)
    env.update(extra)
    env.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def kill_sc2() -> None:
    for name in SC2_PROCESS_NAMES:
        subprocess.run(
            ["taskkill", "/F", "/IM", f"{name}.exe"],
            capture_output=True,
            text=True,
            check=False,
        )


LOG_RESULT_RE = re.compile(
    r"Result for player 1 - Bot SwarmBrain\(Zerg\): (Victory|Defeat|Tie)",
    re.IGNORECASE,
)
GAME_TIME_RE = re.compile(r"game time:\s*(\d{1,2}:\d{2})", re.IGNORECASE)


def game_clock_to_sec(text: str | None) -> float | None:
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(text).strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def last_game_time_from_log(log_file: Path) -> tuple[str | None, float | None]:
    if not log_file.is_file():
        return None, None
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    matches = GAME_TIME_RE.findall(text)
    if not matches:
        return None, None
    clock = str(matches[-1])
    return clock, game_clock_to_sec(clock)


def recover_outcome_from_log(log_file: Path) -> str | None:
    if not log_file.is_file():
        return None
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = LOG_RESULT_RE.findall(text)
    if not matches:
        return None
    return str(matches[-1]).capitalize()


def apply_result_overrides(games: list[dict[str, Any]], exp_dir: Path | None = None) -> None:
    for game in games:
        try:
            idx = int(game.get("game_index"))
        except (TypeError, ValueError):
            idx = None
        path = Path(str(game.get("result_file") or ""))
        if not path.is_file() and exp_dir is not None and idx is not None:
            path = exp_dir / f"game_{idx:02d}" / "result.json"
        if not path.is_file():
            continue
        try:
            sidecar = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        reason = str(sidecar.get("claim_reason") or "")
        outcome = sidecar.get("outcome")
        claimed = bool(sidecar.get("claimed"))
        user_ok = reason.startswith("user_confirmed") or reason.startswith("human_review")
        if outcome not in {"Victory", "Defeat", "Tie"}:
            continue
        if not (claimed or user_ok):
            continue
        game["outcome"] = outcome
        game["claim_reason"] = reason or game.get("claim_reason")
        game["result_file"] = str(path)
        game["count_in_winrate"] = True
        status = str(game.get("run_status") or "completed")
        if user_ok and "user_override" not in status:
            game["run_status"] = f"{status}+user_override"


def summarize(games: list[dict[str, Any]], exp_dir: Path | None = None) -> dict[str, Any]:
    apply_result_overrides(games, exp_dir)
    excluded = [
        g
        for g in games
        if g.get("count_in_winrate") is False or g.get("contaminated") or g.get("outcome") in {"user_quit"}
    ]
    counted = [
        g
        for g in games
        if g.get("outcome") in {"Victory", "Defeat", "Tie"}
        and g.get("count_in_winrate", True) is not False
        and not g.get("contaminated")
    ]
    wins = sum(1 for g in counted if g.get("outcome") == "Victory")
    losses = sum(1 for g in counted if g.get("outcome") == "Defeat")
    ties = sum(1 for g in counted if g.get("outcome") == "Tie")
    crashes = sum(1 for g in games if g.get("outcome") in {"crashed", "startup_failed", "timeout"})
    n = len(counted)
    return {
        "completed_games": n,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "crashes_or_timeouts": crashes,
        "excluded_user_quit_or_contaminated": len(excluded),
        "win_rate": (wins / n) if n else None,
        "win_rate_including_crashes_as_loss": (wins / len(games)) if games else None,
        "total_wall_sec": round(sum(float(g.get("elapsed_sec") or 0) for g in games), 1),
        "total_match_sec": round(
            sum(float(g.get("match_duration_sec") or 0) for g in games if g.get("match_duration_sec") is not None),
            1,
        ),
        "note": "到点未结束且对手未GG记平局；我方GG记失败。崩溃或中途手动退出不计入。",
        "score": f"{wins}-{losses}-{ties}",
    }


VALID_OUTCOMES = {"Victory", "Defeat", "Tie"}
INVALID_OUTCOMES = {
    "user_quit",
    "time_limit",
    "exit",
    "crashed",
    "startup_failed",
    "timeout",
    "unknown",
    "aborted",
}


def is_valid_game(game: dict[str, Any]) -> bool:
    if game.get("count_in_winrate", True) is False or game.get("contaminated"):
        return False
    if game.get("outcome") not in VALID_OUTCOMES:
        return False
    reason = str(game.get("claim_reason") or "")
    if os.getenv("SWARMBRAIN_BOARD_GG_NOT_WIN", "0").strip().lower() in {"1", "true", "yes", "on"}:
        if game.get("outcome") == "Victory":
            ok = (
                reason
                in {
                    "engine_observation",
                    "engine_on_end",
                    "human_review_opponent_gg",
                    "time_limit_opponent_gg",
                    "engine_defeat_after_enemy_gg",
                    "on_end_after_enemy_gg_chat",
                }
                or reason.startswith("enemy_gg_chat")
            )
            if not ok:
                return False
    return True


def valid_score(games: list[dict[str, Any]], exp_dir: Path | None = None) -> tuple[int, int, int, int]:
    apply_result_overrides(games, exp_dir)
    counted = [g for g in games if is_valid_game(g)]
    wins = sum(1 for g in counted if g.get("outcome") == "Victory")
    losses = sum(1 for g in counted if g.get("outcome") == "Defeat")
    ties = sum(1 for g in counted if g.get("outcome") == "Tie")
    return wins, losses, ties, len(counted)


def smoke_llm(env: dict[str, str]) -> dict[str, Any]:
    code = r"""
import os, time
from swarmbrain_llm_shared import get_swarmbrain_llm_credentials
from langchain_openai import ChatOpenAI
p = get_swarmbrain_llm_credentials()
print(f"model={p['model']}")
print(f"base={p['base_url']}")
print(f"key_len={len(str(p['api_key']))}")
llm = ChatOpenAI(model=p['model'], api_key=p['api_key'], base_url=p['base_url'], temperature=0)
t0 = time.time()
msg = llm.invoke("Reply with exactly: OK")
print(f"reply={str(getattr(msg,'content',msg))[:120]}")
print(f"latency_sec={time.time()-t0:.2f}")
"""
    proc = subprocess.run(
        [str(PYTHON), "-c", code],
        cwd=str(PVE_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"LLM smoke failed: {proc.stderr[-2000:] or proc.stdout[-2000:]}")
    payload = {"ok": True, "stdout": proc.stdout.strip()}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            payload[key.strip()] = value.strip()
    return payload


def load_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_one_game(
    *,
    game_index: int,
    exp_dir: Path,
    mass_api: dict[str, str],
    timeout_sec: int,
    realtime: bool,
    arm: str,
    difficulty: str,
) -> dict[str, Any]:
    game_dir = exp_dir / f"game_{game_index:02d}"
    game_dir.mkdir(parents=True, exist_ok=True)
    result_file = game_dir / "result.json"
    replay_file = game_dir / "replay.SC2Replay"
    log_file = game_dir / "launch.log"
    started = time.time()
    started_at = utc_now()
    env = child_env(
        mass_api,
        {
            "SWARMBRAIN_EXPERIMENT_ARM": arm,
            "SWARMBRAIN_EXPERIMENT_GAME_INDEX": str(game_index),
            "SWARMBRAIN_EXPERIMENT_RESULT_FILE": str(result_file),
            "SWARMBRAIN_REPLAY_REL": str(replay_file),
            "SWARMBRAIN_REALTIME": "1" if realtime else "0",
            "SWARMBRAIN_DIFFICULTY": difficulty,
            "SWARMBRAIN_DISABLE_REDIS_BRIDGE": "1",
            "SWARMBRAIN_SKIP_REPLAY": "1",
            "SWARMBRAIN_EXPERIMENT_QUIET": "1",
            "SWARMBRAIN_DISABLE_FEATURE_LAYER": "1",
            "SWARMBRAIN_FULLSCREEN": "0",
            "SWARMBRAIN_WINDOW_WIDTH": os.getenv("SWARMBRAIN_WINDOW_WIDTH", "1280"),
            "SWARMBRAIN_WINDOW_HEIGHT": os.getenv("SWARMBRAIN_WINDOW_HEIGHT", "720"),
            "SWARMBRAIN_WINDOW_X": os.getenv("SWARMBRAIN_WINDOW_X", "80"),
            "SWARMBRAIN_WINDOW_Y": os.getenv("SWARMBRAIN_WINDOW_Y", "80"),
            "SWARMBRAIN_MATCH_TIME_LIMIT_SEC": os.getenv("SWARMBRAIN_MATCH_TIME_LIMIT_SEC", "1260"),
            "SWARMBRAIN_BOARD_GG_NOT_WIN": os.getenv("SWARMBRAIN_BOARD_GG_NOT_WIN", "0"),
        },
    )
    heartbeat = exp_dir / "heartbeat.json"
    write_json(
        heartbeat,
        {
            "phase": "game_running",
            "game_index": game_index,
            "started_at": utc_now(),
            "log_file": str(log_file),
        },
    )
    print(f"[exp] start game {game_index} difficulty={difficulty} realtime={realtime}", flush=True)
    with log_file.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            [str(PYTHON), str(LAUNCH)],
            cwd=str(PVE_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        timed_out = False
        stale_exit = False
        returncode = None
        deadline = time.time() + timeout_sec
        result_seen_at: float | None = None
        try:
            while True:
                ret = proc.poll()
                if ret is not None:
                    returncode = ret
                    break
                if result_file.is_file() and result_seen_at is None:
                    result_seen_at = time.time()
                    print(f"[exp] game {game_index} result file seen, waiting for process exit", flush=True)
                if result_seen_at is not None and (time.time() - result_seen_at) > 45:
                    print(f"[exp] game {game_index} launch hung after result; killing child", flush=True)
                    proc.kill()
                    stale_exit = True
                    try:
                        returncode = proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        returncode = proc.returncode
                    break
                if time.time() >= deadline:
                    timed_out = True
                    proc.kill()
                    try:
                        returncode = proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        returncode = proc.returncode
                    break
                time.sleep(2)
        finally:
            kill_sc2()
    elapsed = round(time.time() - started, 1)
    sidecar: dict[str, Any] = {}
    if result_file.is_file():
        try:
            sidecar = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sidecar = {"outcome": "unknown", "error": "result.json parse failed"}
    if timed_out and not sidecar:
        outcome = "timeout"
        run_status = "timeout"
    else:
        outcome = str(sidecar.get("outcome") or ("crashed" if returncode else "unknown"))
        run_status = str(sidecar.get("run_status") or ("completed" if returncode == 0 else "crashed"))
        if stale_exit:
            run_status = f"{run_status}+stale_killed"
    if sidecar.get("outcome") == "user_quit" or sidecar.get("claim_reason") == "user_quit":
        outcome = "user_quit"
        run_status = f"{run_status}+user_quit"
    elif sidecar.get("outcome") in {"exit", "time_limit"} or sidecar.get("claim_reason") in {
        "time_limit_exit",
        "time_limit_25min",
        "time_limit_draw",
    }:
        outcome = "Tie"
        run_status = f"{run_status}+draw"
    elif sidecar.get("claimed") and sidecar.get("outcome") in {"Victory", "Defeat", "Tie"}:
        outcome = str(sidecar["outcome"])
        run_status = f"{run_status}+claimed"
    elif outcome in {"crashed", "unknown", "startup_failed"}:
        recovered = recover_outcome_from_log(log_file)
        if recovered in {"Victory", "Tie", "Defeat"}:
            outcome = recovered
            run_status = f"{run_status}+recovered_from_log"
            sidecar["outcome"] = recovered
            sidecar["claim_reason"] = sidecar.get("claim_reason") or f"recovered_from_log_{recovered.lower()}"
            sidecar["claimed"] = True
            sidecar["recovered_from_log"] = True
            write_json(result_file, sidecar)
    log_clock, log_clock_sec = last_game_time_from_log(log_file)
    match_clock = sidecar.get("game_time") or log_clock
    match_sec = sidecar.get("game_time_sec")
    if match_sec is None:
        match_sec = game_clock_to_sec(str(match_clock) if match_clock else None) or log_clock_sec
    record = {
        "game_index": game_index,
        "outcome": outcome,
        "run_status": run_status,
        "claim_reason": sidecar.get("claim_reason") or sidecar.get("outcome_reason"),
        "difficulty": sidecar.get("difficulty") or difficulty,
        "returncode": returncode,
        "started_at_utc": started_at,
        "ended_at_utc": utc_now(),
        "elapsed_sec": elapsed,
        "match_duration": match_clock,
        "match_duration_sec": match_sec,
        "result_file": str(result_file) if result_file.is_file() else None,
        "replay_file": str(replay_file) if replay_file.is_file() else None,
        "log_file": str(log_file),
        "run_id": sidecar.get("run_id"),
        "run_dir": sidecar.get("run_dir"),
    }
    print(
        f"[exp] game {game_index} outcome={outcome} wall={elapsed}s match={match_clock} returncode={returncode}",
        flush=True,
    )
    return record


def write_final_report(
    *,
    exp_dir: Path,
    protocol: dict[str, Any],
    games: list[dict[str, Any]],
    stats: dict[str, Any],
) -> None:
    report = {
        "title": "SwarmBrain PvE 实验结果",
        "generated_at_utc": utc_now(),
        "experiment_dir": str(exp_dir),
        "protocol": protocol,
        "stats": stats,
        "games": games,
    }
    write_json(exp_dir / "final_report.json", report)
    lines = [
        "SwarmBrain PvE 实验执行报告",
        f"目录: {exp_dir}",
        f"对手: {protocol.get('opponent')}",
        f"地图: {protocol.get('map')}",
        f"模型: {protocol.get('model')}",
        f"计划有效局: {protocol.get('valid_games_planned') or protocol.get('games_planned')}",
        f"比分 我方胜利-对方胜利-平局: {stats.get('wins')}-{stats.get('losses')}-{stats.get('ties')}",
        f"胜率: {stats.get('win_rate')}",
        f"总墙钟秒: {stats.get('total_wall_sec')}",
        f"总对局秒: {stats.get('total_match_sec')}",
        "",
        "局号\t胜负\t墙钟秒\t对局时长\t结束原因\t日志",
    ]
    for game in games:
        lines.append(
            "\t".join(
                [
                    str(game.get("game_index")),
                    str(game.get("outcome")),
                    str(game.get("elapsed_sec")),
                    str(game.get("match_duration") or game.get("match_duration_sec") or ""),
                    str(game.get("claim_reason") or game.get("run_status") or ""),
                    str(game.get("log_file") or ""),
                ]
            )
        )
    (exp_dir / "final_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run unmodified-prompt PvE baseline games.")
    parser.add_argument("--games", type=int, default=5, help="Max attempts if --valid-games is 0. Default: 5")
    parser.add_argument(
        "--valid-games",
        type=int,
        default=0,
        help="Keep playing until this many valid Victory/Defeat/Tie games. 0 = use --games.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="Safety cap on launched games when using --valid-games. Default: valid-games + 10.",
    )
    parser.add_argument("--timeout-sec", type=int, default=3600, help="Per-game wall timeout.")
    parser.add_argument(
        "--difficulty",
        default="Medium",
        help="Computer difficulty: Medium / MediumHard / Hard. 中文：中等=Medium，困难(窗口)=MediumHard，艰难=Hard。本脚本传 Hard 时为人机 Hard。",
    )
    parser.add_argument("--arm", default="baseline_unmodified", help="Experiment arm name.")
    parser.add_argument(
        "--realtime",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="SC2 realtime (default True so the window accepts camera/unit clicks). Use --no-realtime to pause while the LLM thinks.",
    )
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--exp-dir", default="", help="Resume/write into an existing experiment directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not PYTHON.is_file():
        raise SystemExit(f"missing venv python: {PYTHON}")
    if not MASS_ENV.is_file():
        raise SystemExit(f"missing mass env: {MASS_ENV}")
    mass_api = apply_mass_api_to_pve_env()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    arm = str(args.arm)
    difficulty = str(args.difficulty)
    prefix = arm if arm.startswith("baseline_") else f"baseline_{arm}"
    exp_dir = Path(args.exp_dir) if args.exp_dir else (PVE_ROOT / "experiments" / f"{prefix}_{stamp}")
    exp_dir.mkdir(parents=True, exist_ok=True)
    summary_path = exp_dir / "summary.json"
    games_path = exp_dir / "games.jsonl"
    existing = load_summary(summary_path)
    games: list[dict[str, Any]] = list(existing.get("games") or [])
    apply_result_overrides(games, exp_dir)
    done_indexes = {int(g["game_index"]) for g in games if g.get("game_index")}
    valid_target = int(args.valid_games or 0)
    if valid_target > 0:
        max_attempts = int(args.max_attempts or 0) or (valid_target + 10)
    else:
        max_attempts = int(args.games)
    for sidecar_path in sorted(exp_dir.glob("game_*/result.json")):
        try:
            game_index = int(sidecar_path.parent.name.split("_")[-1])
        except ValueError:
            continue
        if game_index in done_indexes:
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        record = {
            "game_index": game_index,
            "outcome": sidecar.get("outcome") or "unknown",
            "run_status": sidecar.get("run_status") or "completed",
            "returncode": None,
            "elapsed_sec": None,
            "result_file": str(sidecar_path),
            "replay_file": sidecar.get("replay"),
            "log_file": str(exp_dir / f"game_{game_index:02d}" / "launch.log"),
            "run_id": sidecar.get("run_id"),
            "run_dir": sidecar.get("run_dir"),
            "ended_at_utc": sidecar.get("ended_at_utc") or utc_now(),
            "resumed_from_sidecar": True,
        }
        games.append(record)
        done_indexes.add(game_index)
        append_jsonl(games_path, record)
        print(f"[exp] resume game {game_index} from sidecar outcome={record['outcome']}", flush=True)
    try:
        match_limit_sec = max(60, int(float(os.getenv("SWARMBRAIN_MATCH_TIME_LIMIT_SEC", "1260"))))
    except ValueError:
        match_limit_sec = 1260
    match_limit_clock = f"{match_limit_sec // 60:02d}:{match_limit_sec % 60:02d}"
    protocol = {
        "arm": arm,
        "prompts_modified": True,
        "prompt_variant": "selfplay_reflect",
        "opponent": f"Computer Terran {difficulty}",
        "difficulty": difficulty,
        "map": "AutomatonLE",
        "model": mass_api.get("SWARMBRAIN_LLM_MODEL"),
        "base_url": mass_api.get("SWARMBRAIN_LLM_BASE_URL"),
        "api_source": str(MASS_ENV),
        "realtime": bool(args.realtime),
        "games_planned": valid_target or args.games,
        "valid_games_planned": valid_target or None,
        "max_attempts": max_attempts,
        "timeout_sec": args.timeout_sec,
        "match_time_limit_sec": match_limit_sec,
        "match_time_limit_clock": match_limit_clock,
        "created_at_utc": existing.get("protocol", {}).get("created_at_utc") or utc_now(),
        "note": (
            "决策提示词为子反思四步（A/B/C/D）；对手难度由 SWARMBRAIN_DIFFICULTY 指定。"
            "SC2 以普通可交互窗口启动（displayMode=0，默认 1280x720）。默认 realtime=True，可点小地图切视角、框选控兵。"
            f"对局满 {match_limit_clock} 仍未结束则自动退出：对手已 GG 记胜，否则记平局。"
            "我方 GG/投降记失败。"
            + (
                "有效胜只认：对手聊天 GG、引擎结算 Victory、或你现场确认对手 GG。"
                "盘面主矿/军队看不见就关窗，不记有效胜。"
                if os.getenv("SWARMBRAIN_BOARD_GG_NOT_WIN", "0").strip().lower()
                in {"1", "true", "yes", "on"}
                else ""
            )
        ),
    }
    write_json(exp_dir / "protocol.json", protocol)
    smoke = existing.get("llm_smoke")
    env = child_env(mass_api, {"SWARMBRAIN_DISABLE_REDIS_BRIDGE": "1"})
    if not args.skip_smoke and not smoke:
        print("[exp] LLM smoke test...", flush=True)
        smoke = smoke_llm(env)
        print(f"[exp] LLM smoke ok model={smoke.get('model')} latency={smoke.get('latency_sec')}", flush=True)
    summary = {
        "protocol": protocol,
        "llm_smoke": smoke,
        "games": games,
        "stats": summarize(games, exp_dir),
        "updated_at_utc": utc_now(),
        "status": "running",
    }
    write_json(summary_path, summary)
    print(f"[exp] dir={exp_dir}", flush=True)
    game_index = 1
    while True:
        apply_result_overrides(games, exp_dir)
        wins, losses, ties, valid_n = valid_score(games, exp_dir)
        if valid_target > 0 and valid_n >= valid_target:
            print(f"[exp] reached {valid_n} valid games, score {wins}-{losses}-{ties}", flush=True)
            break
        if game_index > max_attempts:
            print(
                f"[exp] stop at max attempts={max_attempts} with valid={valid_n} score {wins}-{losses}-{ties}",
                flush=True,
            )
            break
        if game_index in done_indexes:
            print(f"[exp] skip completed game {game_index}", flush=True)
            game_index += 1
            continue
        record = run_one_game(
            game_index=game_index,
            exp_dir=exp_dir,
            mass_api=mass_api,
            timeout_sec=args.timeout_sec,
            realtime=bool(args.realtime),
            arm=arm,
            difficulty=difficulty,
        )
        if not is_valid_game(record):
            record["count_in_winrate"] = False
        games.append(record)
        append_jsonl(games_path, record)
        summary["games"] = games
        summary["stats"] = summarize(games, exp_dir)
        summary["updated_at_utc"] = utc_now()
        summary["status"] = "running"
        write_json(summary_path, summary)
        wins, losses, ties, valid_n = valid_score(games, exp_dir)
        write_json(
            exp_dir / "heartbeat.json",
            {
                "phase": "between_games",
                "last_finished_game": game_index,
                "valid_games": valid_n,
                "valid_target": valid_target or max_attempts,
                "score": f"{wins}-{losses}-{ties}",
                "stats": summary["stats"],
                "updated_at_utc": utc_now(),
            },
        )
        print(
            f"[exp] score {wins}-{losses}-{ties} valid={valid_n}/{valid_target or max_attempts} "
            f"launched={len(games)}",
            flush=True,
        )
        game_index += 1
        time.sleep(20)
        kill_sc2()
        time.sleep(3)
    summary["status"] = "completed"
    summary["updated_at_utc"] = utc_now()
    write_json(summary_path, summary)
    write_final_report(exp_dir=exp_dir, protocol=protocol, games=games, stats=summary["stats"])
    stats = summary["stats"]
    print(
        f"[exp] done score={stats.get('wins')}-{stats.get('losses')}-{stats.get('ties')} "
        f"win_rate={stats.get('win_rate')} dir={exp_dir}",
        flush=True,
    )
    print(f"[exp] report={exp_dir / 'final_report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
