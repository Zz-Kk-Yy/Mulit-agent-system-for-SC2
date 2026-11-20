"""Load the no-model match config. Map points and the opening live here, not in the bot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_MATCH_CONFIG = PACKAGE_ROOT / "configs" / "match.json"

ALLOWED_GOAL_TYPES = (
    "build_structure",
    "expand",
    "set_production_target",
    "set_army_goal",
)

_RACES = {"Zerg", "Terran", "Protoss", "Random"}
_DIFFICULTIES = {
    "VeryEasy",
    "Easy",
    "Medium",
    "MediumHard",
    "Hard",
    "Harder",
    "VeryHard",
    "CheatVision",
    "CheatMoney",
    "CheatInsane",
}


@dataclass(frozen=True)
class OpeningPlan:
    worker_target: int
    spawning_pool_min_workers: int
    queen_per_base: int
    overlord_when_supply_left_at_most: int
    defense_radius: float
    emergency_ling_cap: int
    pull_workers_when_visible_enemy_at_most: int
    pull_workers_when_army_below: int
    pull_worker_count: int
    base_target: int
    expand_block_radius: float
    summary: str


@dataclass(frozen=True)
class MatchConfig:
    map_name: str
    own_race: str
    opponent: str
    opponent_race: str
    difficulty: str
    spawn_direction: str
    realtime: bool
    game_step: int
    match_time_limit_sec: int
    observation_dump_game_time_sec: float
    replay_rel: str
    opening: OpeningPlan
    location_labels: dict[str, tuple[float, float]]
    source_path: Path

    @property
    def model_called(self) -> bool:
        return False


def load_match_config(path: Path | None = None) -> MatchConfig:
    config_path = Path(path) if path else DEFAULT_MATCH_CONFIG
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    opening_raw = raw["opening"]
    labels = {
        str(name): (float(point[0]), float(point[1]))
        for name, point in raw["location_labels"].items()
    }
    opening = OpeningPlan(
        worker_target=int(opening_raw["worker_target"]),
        spawning_pool_min_workers=int(opening_raw["spawning_pool_min_workers"]),
        queen_per_base=int(opening_raw["queen_per_base"]),
        overlord_when_supply_left_at_most=int(opening_raw["overlord_when_supply_left_at_most"]),
        defense_radius=float(opening_raw["defense_radius"]),
        emergency_ling_cap=int(opening_raw["emergency_ling_cap"]),
        pull_workers_when_visible_enemy_at_most=int(opening_raw["pull_workers_when_visible_enemy_at_most"]),
        pull_workers_when_army_below=int(opening_raw["pull_workers_when_army_below"]),
        pull_worker_count=int(opening_raw["pull_worker_count"]),
        base_target=int(opening_raw["base_target"]),
        expand_block_radius=float(opening_raw["expand_block_radius"]),
        summary=str(opening_raw["summary"]).strip(),
    )
    config = MatchConfig(
        map_name=str(raw["map_name"]).strip(),
        own_race=str(raw["own_race"]).strip(),
        opponent=str(raw["opponent"]).strip(),
        opponent_race=str(raw["opponent_race"]).strip(),
        difficulty=str(raw["difficulty"]).strip(),
        spawn_direction=str(raw["spawn_direction"]).strip(),
        realtime=bool(raw["realtime"]),
        game_step=int(raw["game_step"]),
        match_time_limit_sec=int(raw["match_time_limit_sec"]),
        observation_dump_game_time_sec=float(raw["observation_dump_game_time_sec"]),
        replay_rel=str(raw["replay_rel"]).strip(),
        opening=opening,
        location_labels=labels,
        source_path=config_path.resolve(),
    )
    validate_match_config(config)
    return config


def validate_match_config(config: MatchConfig) -> None:
    if config.own_race not in _RACES or config.opponent_race not in _RACES:
        raise ValueError(f"unknown race: {config.own_race} vs {config.opponent_race}")
    if config.opponent != "Computer":
        raise ValueError("this entry only plays a built-in computer")
    if config.difficulty not in _DIFFICULTIES:
        raise ValueError(f"unknown difficulty: {config.difficulty}")
    if not config.map_name:
        raise ValueError("map_name is empty")
    if not config.location_labels:
        raise ValueError("location_labels is empty")
    if config.spawn_direction != "auto" and config.spawn_direction not in config.location_labels:
        side = config.spawn_direction
        if side not in {label[0] for label in config.location_labels}:
            raise ValueError("spawn_direction must be auto, a location label, or a side letter")
    if config.game_step < 1:
        raise ValueError("game_step must be >= 1")
    if config.match_time_limit_sec < 60:
        raise ValueError("match_time_limit_sec must be >= 60")
    opening = config.opening
    if opening.worker_target < 12:
        raise ValueError("worker_target must keep the starting drones")
    if opening.queen_per_base < 1:
        raise ValueError("queen_per_base must be >= 1 so larva can be injected")
    if opening.base_target < 1:
        raise ValueError("base_target must be >= 1")
    if opening.expand_block_radius <= 0:
        raise ValueError("expand_block_radius must be positive")
    if opening.defense_radius <= 0:
        raise ValueError("defense_radius must be positive")
    if not opening.summary:
        raise ValueError("opening.summary is empty")
