"""Decisions for the local plan used when the model gives no executable goal."""

from __future__ import annotations

import math

EXPAND_MINERALS = 300


def worker_goal(worker_target: int, base_target: int, committed_bases: int) -> int:
    bases = min(base_target, max(1, committed_bases))
    return worker_target * bases


def drone_goal(local_goal: int, model_target: int | None) -> int:
    """Keep the local miner count. A model total can raise it, not shrink it."""
    if isinstance(model_target, bool) or not isinstance(model_target, int) or model_target <= 0:
        return local_goal
    return max(local_goal, model_target)


def should_expand(
    *,
    base_target: int,
    committed_bases: int,
    workers: int,
    worker_target: int,
    pool_placed: bool,
    under_attack: bool,
) -> bool:
    if under_attack or not pool_placed:
        return False
    if committed_bases >= base_target:
        return False
    return workers >= worker_target


def should_save_for_expand(expand: bool, minerals: int) -> bool:
    return expand and minerals < EXPAND_MINERALS


def model_actions_from_record(record: dict) -> list[dict]:
    actions = record.get("accepted_actions_not_executed")
    if not isinstance(actions, list) or not actions:
        raise ValueError("记录里没有可执行的模型动作")
    return actions


def model_expand_base(actions: list[dict]) -> str | None:
    for action in actions:
        if action.get("type") == "expand" and isinstance(action.get("base_id"), str):
            return action["base_id"]
    return None


def model_production_targets(actions: list[dict]) -> dict[str, int]:
    targets = {}
    for action in actions:
        if action.get("type") != "set_production_target":
            continue
        unit = action.get("unit")
        count = action.get("target_count")
        if isinstance(unit, str) and isinstance(count, int) and not isinstance(count, bool) and count > 0:
            targets[unit] = count
    return targets


def model_structures(actions: list[dict]) -> list[dict]:
    return [action for action in actions if action.get("type") == "build_structure" and isinstance(action.get("structure"), str)]


def model_army_goal(actions: list[dict]) -> tuple[str | None, str | None]:
    for action in actions:
        if action.get("type") != "set_army_goal":
            continue
        mode = action.get("mode")
        region_id = action.get("region_id")
        if isinstance(mode, str) and isinstance(region_id, str):
            return mode, region_id
    return None, None


def point_is_held(
    point: tuple[float, float],
    blocked: list[tuple[float, float]],
    block_radius: float,
) -> bool:
    return any(math.hypot(point[0] - taken[0], point[1] - taken[1]) <= block_radius for taken in blocked)


def nearest_open_base(
    start: tuple[float, float],
    candidates: list[tuple[float, float]],
    blocked: list[tuple[float, float]],
    block_radius: float,
) -> tuple[float, float] | None:
    best = None
    best_distance = None
    for point in candidates:
        if any(math.hypot(point[0] - taken[0], point[1] - taken[1]) <= block_radius for taken in blocked):
            continue
        distance = math.hypot(point[0] - start[0], point[1] - start[1])
        if best_distance is None or distance < best_distance:
            best = point
            best_distance = distance
    return best
