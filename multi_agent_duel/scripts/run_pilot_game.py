#!/usr/bin/env python3
"""Run one pilot dual-LLM game with frozen pilot_dual_llm_v1 config."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = os.environ.copy()
    env["SWARMBRAIN_PILOT"] = "1"
    env.setdefault("SWARMBRAIN_PILOT_CONFIG_PATH", str(ROOT / "configs" / "pilot_dual_llm_v1.yaml"))
    env.setdefault("SC2PATH", env.get("SC2PATH") or r"E:\star_carft\StarCraft II")
    env.setdefault("SWARMBRAIN_SC2PATH", env["SC2PATH"])
    # Load .env into child via launch's own dotenv; just ensure CWD.
    py = Path(sys.executable)
    cmd = [str(py), str(ROOT / "swarmbrain_launch.py")]
    print("[run_pilot_game]", " ".join(cmd), flush=True)
    print("[run_pilot_game] SWARMBRAIN_PILOT=1", flush=True)
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
