#!/usr/bin/env python3
"""Unattended closer for PvE experiments.

Does not invent wins from an empty board. If the engine already wrote Victory/Defeat/Tie
or the match clock hit the configured time limit without a sidecar, finish the process so the runner can count.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

GAME_TIME_RE = re.compile(r"(?:game time:\s*|\[gg-check\] t=)(\d{1,2}:\d{2})")
ENGINE_VICTORY_RE = re.compile(
    r"Result for player 1 - Bot SwarmBrain\(Zerg\): Victory|"
    r"\[match-result\] Result\.Victory|"
    r"\[match-claim\] Victory",
    re.I,
)
ENGINE_DEFEAT_RE = re.compile(
    r"\[match-claim\] Defeat|"
    r"our_gg_townhalls_gone|our_gg_chat|"
    r"Result for player 1 - Bot SwarmBrain\(Zerg\): Defeat|"
    r"\[match-result\] Result\.Defeat|"
    r"Result\.Defeat",
    re.I,
)
ENGINE_TIE_RE = re.compile(r"time_limit_draw|\[match-claim\] Tie", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clock_to_sec(text: str | None) -> float | None:
    if not text:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def last_clock(log: Path) -> tuple[str | None, float | None]:
    if not log.is_file():
        return None, None
    text = log.read_text(encoding="utf-8", errors="replace")
    hits = GAME_TIME_RE.findall(text)
    if not hits:
        return None, None
    clock = str(hits[-1])
    return clock, clock_to_sec(clock)


def kill_sc2() -> None:
    for name in ("SC2_x64.exe", "SC2Switcher_x64.exe", "SC2.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name, "/T"], capture_output=True, check=False)


def sc2_running() -> bool:
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SC2_x64.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return True
    blob = f"{proc.stdout or ''}{proc.stderr or ''}"
    if "SC2_x64.exe" in blob:
        return True
    low = blob.lower()
    if "no tasks" in low or "没有运行" in blob or not blob.strip():
        return False
    return True


def write_sidecar(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unattended closer for PvE experiments.")
    parser.add_argument("exp_dir", help="Experiment directory to watch.")
    parser.add_argument("--arm", default="", help="Arm name written into sidecars.")
    parser.add_argument("--difficulty", default="", help="Difficulty written into sidecars.")
    parser.add_argument(
        "--prompts-modified",
        default="",
        help="true/false override. Default: read protocol.json, else false.",
    )
    return parser.parse_args()


def protocol_field(exp: Path, key: str, fallback: str) -> str:
    path = exp / "protocol.json"
    if not path.is_file():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    value = data.get(key)
    if value is None or value == "":
        return fallback
    return str(value)


def match_time_limit(exp: Path) -> tuple[int, str]:
    raw = protocol_field(exp, "match_time_limit_sec", "") or os.getenv("SWARMBRAIN_MATCH_TIME_LIMIT_SEC", "1260")
    try:
        sec = max(60, int(float(raw)))
    except ValueError:
        sec = 1260
    clock = f"{sec // 60:02d}:{sec % 60:02d}"
    return sec, clock


def main() -> int:
    args = parse_args()
    exp = Path(args.exp_dir)
    arm = args.arm or protocol_field(exp, "arm", "baseline_unmodified")
    difficulty = args.difficulty or protocol_field(exp, "difficulty", "Medium")
    prompts_raw = args.prompts_modified or protocol_field(exp, "prompts_modified", "false")
    prompts_modified = str(prompts_raw).strip().lower() in {"1", "true", "yes", "on"}
    print(
        f"[watch] dir={exp} arm={arm} difficulty={difficulty} "
        f"prompts_modified={prompts_modified}",
        flush=True,
    )
    seen_result: dict[int, str] = {}
    sc2_dead_streak: dict[int, int] = {}
    while True:
        limit_sec, limit_clock = match_time_limit(exp)
        hb = exp / "heartbeat.json"
        summary = exp / "summary.json"
        if summary.is_file():
            try:
                s = json.loads(summary.read_text(encoding="utf-8"))
                if s.get("status") in {"completed", "paused", "aborted"}:
                    print(f"[watch] experiment {s.get('status')}, exit", flush=True)
                    return 0
            except json.JSONDecodeError:
                pass
        game_index = None
        if hb.is_file():
            try:
                data = json.loads(hb.read_text(encoding="utf-8"))
                game_index = data.get("game_index")
            except json.JSONDecodeError:
                game_index = None
        if game_index is None:
            time.sleep(15)
            continue
        try:
            idx = int(game_index)
        except (TypeError, ValueError):
            time.sleep(15)
            continue
        game_dir = exp / f"game_{idx:02d}"
        log = game_dir / "launch.log"
        result = game_dir / "result.json"
        if result.is_file():
            try:
                outcome = json.loads(result.read_text(encoding="utf-8")).get("outcome")
            except json.JSONDecodeError:
                outcome = None
            if outcome and seen_result.get(idx) != str(outcome):
                seen_result[idx] = str(outcome)
                print(f"[watch] game {idx} sidecar outcome={outcome}", flush=True)
            time.sleep(12)
            continue
        if not log.is_file():
            time.sleep(12)
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        clock, sec = last_clock(log)
        if ENGINE_VICTORY_RE.search(text):
            write_sidecar(
                result,
                {
                    "arm": arm,
                    "game_index": str(idx),
                    "prompts_modified": prompts_modified,
                    "outcome": "Victory",
                    "run_status": "completed",
                    "claimed": True,
                    "claim_reason": "engine_on_end",
                    "count_in_winrate": True,
                    "difficulty": difficulty,
                    "game_time": clock,
                    "game_time_sec": sec,
                    "note": "无人值守：日志已有引擎 Victory，按对手 GG 记有效胜。",
                    "ended_at_utc": utc_now(),
                },
            )
            print(f"[watch] game {idx} wrote Victory from engine log t={clock}", flush=True)
            time.sleep(8)
            kill_sc2()
            continue
        if ENGINE_TIE_RE.search(text) or (sec is not None and sec >= limit_sec):
            write_sidecar(
                result,
                {
                    "arm": arm,
                    "game_index": str(idx),
                    "prompts_modified": prompts_modified,
                    "outcome": "Tie",
                    "run_status": "completed",
                    "claimed": True,
                    "claim_reason": "time_limit_draw",
                    "count_in_winrate": True,
                    "difficulty": difficulty,
                    "game_time": clock or limit_clock,
                    "game_time_sec": limit_sec,
                    "note": f"无人值守：满 {limit_clock} 对手未 GG，记平局。",
                    "ended_at_utc": utc_now(),
                },
            )
            print(f"[watch] game {idx} wrote Tie at t={clock}", flush=True)
            time.sleep(5)
            kill_sc2()
            continue
        if ENGINE_DEFEAT_RE.search(text):
            write_sidecar(
                result,
                {
                    "arm": arm,
                    "game_index": str(idx),
                    "prompts_modified": prompts_modified,
                    "outcome": "Defeat",
                    "run_status": "completed",
                    "claimed": True,
                    "claim_reason": "our_gg",
                    "count_in_winrate": True,
                    "difficulty": difficulty,
                    "game_time": clock,
                    "game_time_sec": sec,
                    "note": "无人值守：日志为我方 GG/主矿没了，记对方胜。",
                    "ended_at_utc": utc_now(),
                },
            )
            print(f"[watch] game {idx} wrote Defeat from log t={clock}", flush=True)
            time.sleep(5)
            kill_sc2()
            continue
        if "Status.in_game" in text and not ENGINE_VICTORY_RE.search(text):
            if sc2_running():
                sc2_dead_streak[idx] = 0
            else:
                sc2_dead_streak[idx] = sc2_dead_streak.get(idx, 0) + 1
            if sc2_dead_streak.get(idx, 0) >= 3:
                write_sidecar(
                    result,
                    {
                        "arm": arm,
                        "game_index": str(idx),
                        "prompts_modified": prompts_modified,
                        "outcome": "Defeat",
                        "run_status": "completed",
                        "claimed": True,
                        "claim_reason": "sc2_exited_after_in_game",
                        "count_in_winrate": True,
                        "difficulty": difficulty,
                        "game_time": clock,
                        "game_time_sec": sec,
                        "note": "无人值守：对局已进入 in_game 后 SC2 进程消失且日志无 Victory，按对方胜记。",
                        "ended_at_utc": utc_now(),
                    },
                )
                print(f"[watch] game {idx} wrote Defeat because SC2 exited t={clock}", flush=True)
                sc2_dead_streak[idx] = 0
                time.sleep(5)
                continue
        time.sleep(15)


if __name__ == "__main__":
    raise SystemExit(main())
