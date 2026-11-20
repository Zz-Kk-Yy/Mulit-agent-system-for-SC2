#!/usr/bin/env python3
"""Parallel six-stage DeerFlow pipelines for red and blue duel sides."""
from __future__ import annotations

import argparse
import asyncio
import uuid

from run_sc2_deerflow_pipeline import _async_main


async def _run_both(*, match_id: str, replay_mode: bool) -> None:
    base = match_id.strip() or str(uuid.uuid4())[:8]
    red_tid = f"red-{base}"
    blue_tid = f"blue-{base}"
    print(f"[dual-pipeline] match={base} red_thread={red_tid} blue_thread={blue_tid}", flush=True)
    await asyncio.gather(
        _async_main(red_tid, replay_mode, side="red"),
        _async_main(blue_tid, replay_mode, side="blue"),
    )
    print(
        f"[dual-pipeline] done. Inspect sc2:analysis:{red_tid}:* and sc2:analysis:{blue_tid}:*",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Run red+blue six-stage SC2 pipelines in parallel.")
    ap.add_argument("--match-id", default="", help="Shared match id prefix for both thread ids.")
    ap.add_argument("--replay", action="store_true", help="Replay mode (no live command dispatch).")
    args = ap.parse_args()
    asyncio.run(_run_both(match_id=args.match_id, replay_mode=bool(args.replay)))


if __name__ == "__main__":
    main()
