#!/usr/bin/env python3
"""Valid self-play PvE games vs Terran MediumHard.

This wrapper MUST launch from single_agent_pve_selfplay, not single_agent_pve.

- Agent: Overmind A/B/C/D (PROMPT_VARIANT=selfplay_reflect)
- Opponent: Computer Terran Difficulty.MediumHard
  Chinese client label for MediumHard is 困难, not 艰难 (艰难 is Hard).
- Valid games: opponent GG / engine Victory = win; our GG = loss; time limit = tie.
  Board-empty short wins do not count.

Usage:
  python run_mediumhard_valid_selfplay_experiment.py --check
  python run_mediumhard_valid_selfplay_experiment.py --run --valid-games 15
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SELFPLAY_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SELFPLAY_ROOT.parent
UNMODIFIED_ROOT = REPO_ROOT / "single_agent_pve"

ARM = "baseline_selfplay_reflect_mediumhard_valid15_noboard"
DIFFICULTY = "MediumHard"


def _fail(message: str) -> None:
    print(f"[check] FAIL {message}", flush=True)
    raise SystemExit(2)


def preflight(*, valid_games: int = 15, max_attempts: int | None = None) -> None:
    if SELFPLAY_ROOT.name != "single_agent_pve_selfplay":
        _fail(f"package dir is {SELFPLAY_ROOT.name!r}, expected single_agent_pve_selfplay")

    launch = SELFPLAY_ROOT / "swarmbrain_launch.py"
    brain = SELFPLAY_ROOT / "swarmbrain.py"
    runner = SCRIPT_DIR / "run_baseline_winrate_experiment.py"
    unmodified_brain = UNMODIFIED_ROOT / "swarmbrain.py"
    unmodified_launch = UNMODIFIED_ROOT / "swarmbrain_launch.py"
    for path in (launch, brain, runner, unmodified_brain, unmodified_launch):
        if not path.is_file():
            _fail(f"missing {path}")

    sys.path.insert(0, str(SELFPLAY_ROOT))
    import swarmbrain  # noqa: E402

    variant = getattr(swarmbrain, "PROMPT_VARIANT", None)
    protocol = getattr(swarmbrain, "SELFPLAY_REFLECT_PROTOCOL", "")
    if variant != "selfplay_reflect":
        _fail(f"PROMPT_VARIANT={variant!r}, expected selfplay_reflect")
    for token in ("A. OUR DRAFT PLAN", "B. PREDICT ENEMY NEXT MOVE", "C. REFLECT AND CORRECT", "D. CONVERGED OUTPUT"):
        if token not in protocol:
            _fail(f"selfplay protocol missing {token}")
    if "SELFPLAY_REFLECT_PROTOCOL" not in brain.read_text(encoding="utf-8"):
        _fail("swarmbrain.py does not embed SELFPLAY_REFLECT_PROTOCOL")
    if "SELFPLAY_REFLECT_PROTOCOL + " not in brain.read_text(encoding="utf-8"):
        _fail("overmind_brain_iter does not inject the A/B/C/D protocol")

    unmodified_text = unmodified_brain.read_text(encoding="utf-8")
    if "PROMPT_VARIANT" in unmodified_text or "SELFPLAY_REFLECT_PROTOCOL" in unmodified_text:
        _fail("unmodified single_agent_pve/swarmbrain.py unexpectedly has selfplay prompts")

    launch_text = launch.read_text(encoding="utf-8")
    if "Computer(Race.Terran, difficulty)" not in launch_text:
        _fail("launch does not create Computer(Race.Terran, difficulty)")
    if "def _computer_difficulty()" not in launch_text:
        _fail("launch missing _computer_difficulty()")
    if '"困难": "MediumHard"' not in launch_text:
        _fail("selfplay launch must map 困难 to MediumHard")
    if '"艰难": "Hard"' not in launch_text:
        _fail("selfplay launch must map 艰难 to Hard")

    runner_text = runner.read_text(encoding="utf-8")
    if "PVE_ROOT = Path(__file__).resolve().parents[1]" not in runner_text:
        _fail("experiment runner PVE_ROOT is not this package")
    if 'LAUNCH = PVE_ROOT / "swarmbrain_launch.py"' not in runner_text:
        _fail("experiment runner does not launch this package's swarmbrain_launch.py")

    os.environ["SWARMBRAIN_DIFFICULTY"] = DIFFICULTY
    from sc2.data import Difficulty  # noqa: E402
    import swarmbrain_launch  # noqa: E402

    resolved = getattr(Difficulty, DIFFICULTY)
    if resolved.name != "MediumHard":
        _fail(f"Difficulty.{DIFFICULTY} resolved to {resolved}")
    mapped = swarmbrain_launch._computer_difficulty()
    if mapped.name != "MediumHard":
        _fail(f"_computer_difficulty() resolved to {mapped}, expected MediumHard")

    attempts = max_attempts if max_attempts is not None else valid_games + 7
    print("[check] package=single_agent_pve_selfplay")
    print(f"[check] launch={launch}")
    print("[check] agent=selfplay_reflect Overmind A/B/C/D")
    print("[check] unmodified prompts untouched in single_agent_pve/")
    print(f"[check] opponent=Computer Terran {resolved.name} (Chinese UI 困难, not 艰难/Hard)")
    print(f"[check] valid-games={valid_games}  max-attempts={attempts}  board-empty win disabled")
    print("[check] OK")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight or run MediumHard selfplay PvE valid games.")
    parser.add_argument("--check", action="store_true", help="Verify selfplay agent + MediumHard computer, then exit.")
    parser.add_argument("--run", action="store_true", help="Start the valid-game experiment.")
    parser.add_argument("--valid-games", type=int, default=15)
    parser.add_argument("--max-attempts", type=int, default=0)
    parser.add_argument("--arm", default="")
    parser.add_argument("--exp-dir", default="")
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="Extra args forwarded to the experiment runner.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    extra = [item for item in args.extra if item != "--"]
    valid_games = int(args.valid_games)
    max_attempts = int(args.max_attempts) or (valid_games + 15)
    arm = args.arm or f"baseline_selfplay_reflect_mediumhard_valid{valid_games}_noboard"
    if args.run:
        preflight(valid_games=valid_games, max_attempts=max_attempts)
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        from run_baseline_winrate_experiment import main as run_experiment

        forwarded = [
            sys.argv[0],
            "--valid-games",
            str(valid_games),
            "--max-attempts",
            str(max_attempts),
            "--difficulty",
            DIFFICULTY,
            "--arm",
            arm,
            "--timeout-sec",
            "7200",
            "--skip-smoke",
        ]
        if args.exp_dir:
            forwarded.extend(["--exp-dir", args.exp_dir])
        forwarded.extend(extra)
        sys.argv = forwarded
        return int(run_experiment() or 0)
    preflight(valid_games=valid_games, max_attempts=max_attempts)
    if not args.check:
        print("[check] 脚本已就绪。加上 --run 才会开打有效局。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
