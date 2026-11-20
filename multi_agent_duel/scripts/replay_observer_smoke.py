#!/usr/bin/env python3
"""Dual player-view SC2 replay smoke via raw Client observations.

Avoids burnysc2 ObserverAI (Race.NoRace crashes on some ZvZ replays).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarmbrain_replay_meta import LOOPS_PER_SECOND, parse_replay_file  # noqa: E402

MIN_480_LOOP = int(480 * LOOPS_PER_SECOND)  # 10752


async def _observe_player(
    replay_path: Path,
    observed_id: int,
    *,
    step_size: int = 64,
    wall_timeout_sec: float = 600.0,
) -> dict:
    """Mirror burnysc2 _play_replay: observation() then step() to advance."""
    from sc2.client import Client
    from sc2.main import get_replay_version
    from sc2.protocol import ProtocolError
    from sc2.sc2process import SC2Process

    base_build, data_version = get_replay_version(replay_path)
    last_loop = 0
    steps = 0
    error = None
    t0 = time.time()
    try:
        async with SC2Process(
            fullscreen=False,
            base_build=base_build,
            data_hash=data_version,
        ) as server:
            await server.start_replay(str(replay_path.resolve()), realtime=False, observed_id=observed_id)
            client = Client(server._ws)
            client.game_step = step_size
            # First observation (loop ~0)
            state = await client.observation()
            steps = 1
            last_loop = int(state.observation.observation.game_loop)
            if client._game_result:
                pass
            else:
                while True:
                    if time.time() - t0 > wall_timeout_sec:
                        error = f"wall_timeout:{wall_timeout_sec}s:last_loop={last_loop}"
                        break
                    # Advance simulation — without this, observation can hang/stall.
                    try:
                        await client.step()
                        state = await client.observation()
                    except ProtocolError as exc:
                        if getattr(exc, "is_game_over_error", False):
                            break
                        raise
                    steps += 1
                    gl = int(state.observation.observation.game_loop)
                    if gl > last_loop:
                        last_loop = gl
                    if client._game_result:
                        break
                    # Bad replays often freeze at loop 4/5 — fail fast.
                    if steps >= 40 and last_loop <= 20:
                        error = f"early_stall:last_loop={last_loop}:steps={steps}"
                        break
                    if steps > 200000:
                        error = "too_many_steps"
                        break
            try:
                await client.leave()
            except Exception:
                pass
            try:
                await client.quit()
            except Exception:
                pass
    except Exception as exc:
        error = f"{type(exc).__name__}:{str(exc)[:300]}"

    status = "pass" if last_loop > 100 and error is None else "fail"
    return {
        "observed_id": observed_id,
        "last_game_loop": last_loop,
        "steps": steps,
        "status": status,
        "error": error,
        "elapsed_sec": round(time.time() - t0, 1),
        "base_build": base_build,
        "data_version": data_version,
    }


def smoke_replay(
    replay_path: Path,
    *,
    run_id: str | None = None,
    runtime_result: dict | None = None,
    replay_result: dict | None = None,
    header_final_loop: int | None = None,
) -> dict:
    replay_path = Path(replay_path).resolve()
    meta = parse_replay_file(replay_path)
    final_loop = int(header_final_loop or meta.get("replay_final_game_loop") or 0)

    loop = asyncio.new_event_loop()
    try:
        o1 = loop.run_until_complete(_observe_player(replay_path, 1))
        o2 = loop.run_until_complete(_observe_player(replay_path, 2))
    finally:
        loop.close()

    slack = max(200, int(0.02 * max(final_loop, 1)))
    covers_end_1 = o1["last_game_loop"] >= max(0, final_loop - slack) if final_loop else o1["last_game_loop"] > 100
    covers_end_2 = o2["last_game_loop"] >= max(0, final_loop - slack) if final_loop else o2["last_game_loop"] > 100
    need_480 = final_loop >= MIN_480_LOOP
    covers_480 = (not need_480) or (
        o1["last_game_loop"] >= MIN_480_LOOP and o2["last_game_loop"] >= MIN_480_LOOP
    )
    early_stall = need_480 and (o1["last_game_loop"] <= 20 or o2["last_game_loop"] <= 20)
    early_stall = early_stall or ("early_stall" in str(o1.get("error") or "")) or (
        "early_stall" in str(o2.get("error") or "")
    )

    def _norm(v: object) -> str:
        s = str(v).lower().replace("result.", "")
        return s.replace("victory", "win").replace("defeat", "loss").strip()

    consistent = True
    if runtime_result and replay_result and isinstance(runtime_result, dict) and isinstance(replay_result, dict):
        keys = set(runtime_result) & set(replay_result)
        if keys:
            consistent = all(_norm(runtime_result[k]) == _norm(replay_result[k]) for k in keys)

    o1_ok = o1["status"] == "pass" and covers_end_1 and not (need_480 and o1["last_game_loop"] <= 20)
    o2_ok = o2["status"] == "pass" and covers_end_2 and not (need_480 and o2["last_game_loop"] <= 20)
    overall = (
        meta.get("parse_status") == "ok"
        and o1_ok
        and o2_ok
        and covers_480
        and not early_stall
        and consistent
    )

    return {
        "run_id": run_id,
        "replay_path": str(replay_path),
        "replay_sha256": meta.get("sha256"),
        "replay_final_game_loop": final_loop,
        "observer_1_last_game_loop": o1["last_game_loop"],
        "observer_2_last_game_loop": o2["last_game_loop"],
        "observer_1_status": "pass" if o1_ok else "fail",
        "observer_2_status": "pass" if o2_ok else "fail",
        "observer_1_detail": o1,
        "observer_2_detail": o2,
        "covers_480_sec": bool(covers_480),
        "covers_true_end": bool(covers_end_1 and covers_end_2),
        "early_stall_detected": bool(early_stall),
        "runtime_replay_result_consistent": bool(consistent),
        "header_parse_status": meta.get("parse_status"),
        "status": "pass" if overall else "fail",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--runtime-result-json", default=None)
    ap.add_argument("--replay-result-json", default=None)
    args = ap.parse_args()
    runtime = json.loads(args.runtime_result_json) if args.runtime_result_json else None
    replay_res = json.loads(args.replay_result_json) if args.replay_result_json else None
    out = smoke_replay(
        Path(args.replay),
        run_id=args.run_id,
        runtime_result=runtime,
        replay_result=replay_res,
    )
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(text + "\n", encoding="utf-8")
    return 0 if out.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
