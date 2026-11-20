from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DuelAgentSpec:
    agent_id: str
    display_name: str
    dashboard_state_path: Path
    feature_map_enabled: bool
    fullscreen: bool


@dataclass(frozen=True)
class PveMatchSettings:
    """MASS 人机对局：默认 SwarmBrain vs 星际内置电脑，可选人类玩家上手。"""

    opponent: str  # computer | human
    computer_race: str
    difficulty: str
    human_race: str


def build_duel_agent_specs(root_dir: Path | str) -> tuple[DuelAgentSpec, DuelAgentSpec]:
    root = Path(root_dir).resolve()
    return (
        DuelAgentSpec(
            agent_id="red",
            display_name="Red SwarmBrain",
            dashboard_state_path=root / "dashboard_state_red.json",
            feature_map_enabled=True,
            fullscreen=False,
        ),
        DuelAgentSpec(
            agent_id="blue",
            display_name="Blue SwarmBrain",
            dashboard_state_path=root / "dashboard_state_blue.json",
            # 与红方对称导出特征图，供仪表盘蓝方沙盘/迷雾使用
            feature_map_enabled=True,
            fullscreen=False,
        ),
    )


def resolve_pve_settings() -> PveMatchSettings:
    raw_opponent = os.getenv("SWARMBRAIN_PVE_OPPONENT", "computer").strip().lower()
    if raw_opponent in {"human", "player", "人机玩家"}:
        opponent = "human"
    else:
        opponent = "computer"
    return PveMatchSettings(
        opponent=opponent,
        computer_race=os.getenv("SWARMBRAIN_PVE_RACE", "Terran").strip() or "Terran",
        difficulty=os.getenv("SWARMBRAIN_PVE_DIFFICULTY", "Medium").strip() or "Medium",
        human_race=os.getenv("SWARMBRAIN_PVE_HUMAN_RACE", "Terran").strip() or "Terran",
    )


def fallback_opening_plan() -> str:
    """Return a parser-compatible plan when the remote planner is unavailable."""
    return "\n".join(
        (
            "(Drone, A1)->(Build)->(Hatchery, A2)",
            "(Drone, A1)->(Build)->(Spawning Pool, A1)",
            "(Drone, A1)->(Build)->(Baneling Nest, A1)",
            "(Drone, A1)->(Build)->(Roach Warren, A1)",
            "(Zergling, A1)->(Attack)->(B1)",
            "(Roach, A1)->(Attack)->(B1)",
        )
    )
