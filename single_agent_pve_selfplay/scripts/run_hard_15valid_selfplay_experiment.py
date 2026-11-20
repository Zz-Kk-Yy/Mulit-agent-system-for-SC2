#!/usr/bin/env python3
"""15 valid self-play PvE games vs Terran Hard. See run_hard_3valid_selfplay_experiment.py."""

from __future__ import annotations

import sys

from run_hard_3valid_selfplay_experiment import main as _main

if __name__ == "__main__":
    extra = [item for item in sys.argv[1:] if item not in {"--valid-games", "15"}]
    sys.argv = [sys.argv[0], "--valid-games", "15", "--max-attempts", "30", "--arm", "baseline_selfplay_reflect_hard_valid15_noboard", *extra]
    raise SystemExit(_main())
