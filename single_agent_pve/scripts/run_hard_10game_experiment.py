#!/usr/bin/env python3
"""10-game unmodified-prompt PvE experiment vs Terran Hard.

Hard is one step above Medium in StarCraft II (中等 -> 困难).
Saves per-game launch.log / result.json and writes final_report.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_baseline_winrate_experiment import main  # noqa: E402


if __name__ == "__main__":
    extra = sys.argv[1:]
    sys.argv = [
        sys.argv[0],
        "--games",
        "10",
        "--difficulty",
        "Hard",
        "--arm",
        "hard_unmodified",
        "--timeout-sec",
        "3300",
        "--skip-smoke",
        *extra,
    ]
    raise SystemExit(main())
