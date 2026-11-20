from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DuelAgentSpec:
    agent_id: str
    display_name: str
    dashboard_state_path: Path
    feature_map_enabled: bool
    fullscreen: bool


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
