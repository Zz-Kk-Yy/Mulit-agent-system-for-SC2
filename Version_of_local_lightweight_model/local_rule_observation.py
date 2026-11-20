"""Observation shape for the later Qwen request. Unseen enemies are omitted, never written as zero."""

from __future__ import annotations

import copy
import math

from local_rule_config import ALLOWED_GOAL_TYPES

BASES_NOTE = "controlled_by 为 unknown 时表示还没看到主基地建筑，不能当成数量 0。"
ENEMY_NOTE = "只记录已经看到的单位。没出现过的兵种不会写进 visible_counts，也不能读成 0。"


def nearest_location_label(
    position: tuple[float, float],
    labels: dict[str, tuple[float, float]],
) -> dict:
    best_name = None
    best_distance = None
    for name, point in labels.items():
        distance = math.hypot(position[0] - point[0], position[1] - point[1])
        if best_distance is None or distance < best_distance:
            best_name = name
            best_distance = distance
    side = best_name[0] if best_name else None
    return {
        "label": best_name,
        "side": side,
        "distance": None if best_distance is None else round(best_distance, 1),
        "position": [round(position[0], 1), round(position[1], 1)],
    }


def compress_observation(observation: dict) -> dict:
    """Keep the saved situation, but drop enemy tracks that are no longer visible."""
    compressed = copy.deepcopy(observation)
    enemy = compressed.get("enemy")
    if not isinstance(enemy, dict):
        return compressed
    sightings = enemy.get("sightings") or []
    enemy["sightings"] = [
        row for row in sightings if isinstance(row, dict) and row.get("currently_visible") is True
    ]
    return compressed


def visible_enemy_counts(sightings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sighting in sightings:
        if not sighting.get("currently_visible"):
            continue
        unit_type = str(sighting.get("type") or "").strip()
        if not unit_type:
            continue
        counts[unit_type] = counts.get(unit_type, 0) + 1
    return {unit_type: count for unit_type, count in counts.items() if count > 0}


def build_observation(
    *,
    game_id: str,
    map_name: str,
    observation_id: str,
    game_time_s: float,
    game_loop: int,
    plan_epoch: int,
    resources: dict,
    own_units: list[dict],
    own_structures: list[dict],
    own_start: dict,
    known_bases: list[dict],
    candidate_expansions: list[dict],
    sightings: list[dict],
    plan_summary: str,
    recent_feedback: list[dict],
    plan_source: str = "local_rules",
    plan_actions: list[dict] | None = None,
    own_in_training: list[dict] | None = None,
) -> dict:
    counts = visible_enemy_counts(sightings)
    own = {
        "units": own_units,
        "structures": own_structures,
    }
    if own_in_training:
        own["in_training"] = list(own_in_training)
    observation = {
        "game": {
            "game_id": game_id,
            "map_name": map_name,
            "observation_id": observation_id,
            "game_time_s": round(float(game_time_s), 1),
            "game_loop": int(game_loop),
            "plan_epoch": int(plan_epoch),
        },
        "resources": resources,
        "own": own,
        "bases": {
            "note": BASES_NOTE,
            "own_start": own_start,
            "known_bases": known_bases,
            "candidate_expansions": candidate_expansions,
        },
        "enemy": {
            "note": ENEMY_NOTE,
            "sightings": sightings,
            "visible_counts": counts,
        },
        "plan": {
            "plan_epoch": int(plan_epoch),
            "source": plan_source,
            "summary": plan_summary,
            "actions": [] if plan_actions is None else list(plan_actions),
        },
        "recent_feedback": recent_feedback,
        "allowed_goal_types": list(ALLOWED_GOAL_TYPES),
    }
    validate_observation(observation)
    return observation


def validate_observation(observation: dict) -> None:
    if observation["plan"]["actions"] != [] and observation["plan"]["source"] == "local_rules":
        raise ValueError("local rules keep actions empty; an empty list means hold the current plan")
    if tuple(observation["allowed_goal_types"]) != ALLOWED_GOAL_TYPES:
        raise ValueError("allowed_goal_types drifted from the contract")
    for unit_type, count in observation["enemy"]["visible_counts"].items():
        if count <= 0:
            raise ValueError(f"enemy count for {unit_type} must be omitted instead of {count}")
    for sighting in observation["enemy"]["sightings"]:
        if "count" in sighting and sighting["count"] == 0:
            raise ValueError("a sighting cannot carry a zero count")
