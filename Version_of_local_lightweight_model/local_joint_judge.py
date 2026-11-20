"""Judge one Qwen reply. Actions are read only from content. Nothing here starts a game."""

from __future__ import annotations

import json
import math
from typing import Any

MODEL_NAME = "swarmbrain-qwen3-4b"
MAX_TOKENS = 2048
ALLOWED_GOAL_TYPES = (
    "build_structure",
    "expand",
    "set_production_target",
    "set_army_goal",
)
LOCAL_WALL_TIMEOUT_S = 180

_UNIQUE_STRUCTURES = {
    "SPAWNINGPOOL",
    "ROACHWARREN",
    "BANELINGNEST",
    "EVOLUTIONCHAMBER",
    "LAIR",
    "HIVE",
    "SPIRE",
    "GREATERSPIRE",
    "HYDRALISKDEN",
    "INFESTATIONPIT",
    "ULTRALISKCAVERN",
    "LURKERDEN",
}

_STRUCTURES = {
    "SPAWNINGPOOL": {"minerals": 200, "vespene": 0, "needs": ("HATCHERY",)},
    "EXTRACTOR": {"minerals": 25, "vespene": 0, "needs": ("HATCHERY",)},
    "EVOLUTIONCHAMBER": {"minerals": 75, "vespene": 0, "needs": ("HATCHERY",)},
    "BANELINGNEST": {"minerals": 100, "vespene": 50, "needs": ("SPAWNINGPOOL",)},
    "ROACHWARREN": {"minerals": 150, "vespene": 0, "needs": ("SPAWNINGPOOL",)},
    "SPINECRAWLER": {"minerals": 100, "vespene": 0, "needs": ("SPAWNINGPOOL",)},
    "SPORECRAWLER": {"minerals": 75, "vespene": 0, "needs": ("SPAWNINGPOOL",)},
    "LAIR": {"minerals": 150, "vespene": 100, "needs": ("HATCHERY", "SPAWNINGPOOL")},
    "INFESTATIONPIT": {"minerals": 100, "vespene": 100, "needs": ("LAIR",)},
    "SPIRE": {"minerals": 200, "vespene": 200, "needs": ("LAIR",)},
    "HYDRALISKDEN": {"minerals": 100, "vespene": 100, "needs": ("LAIR",)},
    "LURKERDEN": {"minerals": 150, "vespene": 150, "needs": ("HYDRALISKDEN",)},
    "HIVE": {"minerals": 200, "vespene": 150, "needs": ("INFESTATIONPIT",)},
    "ULTRALISKCAVERN": {"minerals": 150, "vespene": 200, "needs": ("HIVE",)},
    "NYDUSNETWORK": {"minerals": 150, "vespene": 150, "needs": ("LAIR",)},
}

_UNITS = {
    "DRONE": {"minerals": 50, "vespene": 0, "supply": 1, "larva": 1, "needs": ("HATCHERY",)},
    "OVERLORD": {"minerals": 100, "vespene": 0, "supply": -8, "larva": 1, "needs": ("HATCHERY",)},
    "QUEEN": {"minerals": 150, "vespene": 0, "supply": 2, "larva": 0, "needs": ("SPAWNINGPOOL",)},
    "ZERGLING": {"minerals": 25, "vespene": 0, "supply": 0.5, "larva": 0.5, "needs": ("SPAWNINGPOOL",)},
    "ROACH": {"minerals": 75, "vespene": 25, "supply": 2, "larva": 1, "needs": ("ROACHWARREN",)},
    "BANELING": {"minerals": 25, "vespene": 25, "supply": 0, "larva": 0, "needs": ("BANELINGNEST", "ZERGLING")},
    "HYDRALISK": {"minerals": 100, "vespene": 50, "supply": 2, "larva": 1, "needs": ("HYDRALISKDEN",)},
    "MUTALISK": {"minerals": 100, "vespene": 100, "supply": 2, "larva": 1, "needs": ("SPIRE",)},
}

_EXPAND_MINERALS = 300


def build_chat_request(observation: dict, max_tokens: int = MAX_TOKENS) -> dict:
    return {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": render_user_message(observation)}],
        "max_tokens": max_tokens,
    }


ACTION_PROTOCOL = (
    "先简短思考，然后输出 </think>，随后只给一个完整 JSON 对象，不要代码围栏，也不要额外解释。"
    '格式是 {"actions":[]}，最多 3 个动作。'
    "允许的 type 只有 build_structure、expand、set_production_target、set_army_goal。"
    "build_structure 使用 structure，可选 position。"
    "expand 使用 base_id，必须是 candidate_expansions 里的基地。"
    "set_production_target 使用 unit 和正整数 target_count。target_count 是现存单位加上在训单位后的目标总数，不是这次要新造的数量。新造数量等于 target_count 减去现存和在训；结果不是正数时不消耗 larva。"
    "set_army_goal 使用 mode 和 region_id。region_id 使用 Observation 里已有的基地编号。"
)


def render_user_message(observation: dict) -> str:
    return ACTION_PROTOCOL + "\n\nObservation:\n" + json.dumps(observation, ensure_ascii=False, indent=2)


def judge_reply(
    observation: dict,
    content: Any,
    finish_reason: Any,
    *,
    past_deadline: bool = False,
) -> dict:
    reasons: list[str] = []
    if past_deadline:
        reasons.append("请求已过本地期限")
    if content is None or (isinstance(content, str) and not content.strip()):
        reasons.append("content 为空")
        parsed = None
    else:
        parsed, parse_reason = parse_complete_json(content)
        if parse_reason:
            reasons.append(parse_reason)
    if finish_reason == "length":
        reasons.append("finish_reason 为 length")
    elif finish_reason in (None, ""):
        reasons.append("响应缺少 finish_reason")

    hold_plan_count = 0
    valid_decision_count = 0
    actions: list[dict] = []
    if parsed is not None and "content 为空" not in reasons and "content 不是完整 JSON" not in reasons:
        action_reasons, actions = validate_actions(observation, parsed)
        reasons.extend(action_reasons)
        if not action_reasons and not any(reason.startswith("finish_reason") or reason.startswith("响应缺少") or reason.startswith("请求已过") for reason in reasons):
            if actions == []:
                hold_plan_count = 1
            else:
                valid_decision_count = len(actions)

    decision = "可执行" if not reasons else "拒绝"
    if decision == "可执行" and actions == []:
        note = "空 actions 只表示保持当前计划，不算有效决策。第一次联调不下到对局。"
    elif decision == "可执行":
        note = "内容可执行。第一次联调仍不下到对局。"
    else:
        note = "拒绝执行。第一次联调不下到对局。"
    return {
        "decision": decision,
        "reject_reasons": reasons,
        "hold_plan_count": hold_plan_count,
        "valid_decision_count": valid_decision_count,
        "actions_from_content": actions if decision == "可执行" else [],
        "executed": False,
        "execution_note": note,
    }


def parse_complete_json(content: Any) -> tuple[dict | None, str | None]:
    if not isinstance(content, str):
        return None, "content 不是完整 JSON"
    text = content.strip()
    try:
        parsed, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return None, "content 不是完整 JSON"
    if text[end:].strip():
        return None, "content 不是完整 JSON"
    if not isinstance(parsed, dict):
        return None, "content 不是完整 JSON"
    return parsed, None


def validate_actions(observation: dict, parsed: dict) -> tuple[list[str], list[dict]]:
    if "actions" not in parsed or not isinstance(parsed["actions"], list):
        return ["参数在执行前检查里不成立：缺少 actions 数组"], []
    actions = parsed["actions"]
    if len(actions) > 3:
        return ["目标数超过 3"], []
    if actions == []:
        return [], []
    reasons = []
    accepted = []
    budget = _budget(observation)
    owned = _owned_structures(observation)
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            reasons.append(f"第 {index} 个目标不是对象")
            continue
        goal_type = action.get("type")
        if goal_type not in ALLOWED_GOAL_TYPES:
            reasons.append(f"第 {index} 个目标出现协议之外的类型")
            continue
        reason = _check_action(action, budget, owned, observation)
        if reason:
            reasons.append(f"第 {index} 个目标{reason}")
            continue
        accepted.append(action)
    if reasons:
        return reasons, []
    return [], accepted


def _check_action(action: dict, budget: dict, owned: set[str], observation: dict) -> str | None:
    goal_type = action["type"]
    if goal_type == "build_structure":
        return _check_structure(action, budget, owned)
    if goal_type == "expand":
        return _check_expand(action, budget, observation)
    if goal_type == "set_production_target":
        return _check_production(action, budget, owned, observation)
    return _check_army_goal(action, observation)


def _check_structure(action: dict, budget: dict, owned: set[str]) -> str | None:
    if _unexpected_keys(action, {"type", "structure", "position"}):
        return "参数在执行前检查里不成立：字段应为 structure 和可选 position"
    structure = _token(action.get("structure"))
    if structure == "HATCHERY":
        return "参数在执行前检查里不成立：新基地必须使用 expand"
    spec = _STRUCTURES.get(structure or "")
    if spec is None:
        return "参数在执行前检查里不成立：structure 无法核对"
    position_reason = _check_position(action.get("position"), budget["bounds"])
    if position_reason and "position" in action:
        return position_reason
    if structure in _UNIQUE_STRUCTURES and structure in owned:
        return "科技在执行前检查里不成立：该建筑已经有了"
    missing = [need for need in spec["needs"] if need not in owned]
    if missing:
        return "科技在执行前检查里不成立：缺少 " + "、".join(missing)
    if not _spend(budget, spec["minerals"], spec["vespene"]):
        return "资源在执行前检查里不成立"
    owned.add(structure)
    return None


def _check_expand(action: dict, budget: dict, observation: dict) -> str | None:
    if _unexpected_keys(action, {"type", "base_id"}):
        return "参数在执行前检查里不成立：字段应为 base_id"
    base_id = action.get("base_id")
    candidates = {
        row.get("base_id")
        for row in observation.get("bases", {}).get("candidate_expansions", [])
        if row.get("controlled_by") == "unknown"
    }
    if base_id in budget["used_bases"] or base_id not in candidates:
        return "位置在执行前检查里不成立：base_id 不是候选扩张点"
    if not _spend(budget, _EXPAND_MINERALS, 0):
        return "资源在执行前检查里不成立"
    budget["used_bases"].add(base_id)
    return None


def _check_production(action: dict, budget: dict, owned: set[str], observation: dict) -> str | None:
    unexpected = _unexpected_keys(action, {"type", "unit", "target_count"})
    if unexpected:
        return "参数在执行前检查里不成立：字段应为 unit 和 target_count"
    unit = _token(action.get("unit"))
    count = action.get("target_count")
    spec = _UNITS.get(unit or "")
    if spec is None or not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return "参数在执行前检查里不成立：unit 或 target_count 无法核对"
    missing = [need for need in spec["needs"] if need not in owned and need not in _owned_units(budget)]
    if missing:
        return "科技在执行前检查里不成立：缺少 " + "、".join(missing)
    # target_count 是现存加在训后的目标总数。只为差额占用资源。
    shortfall = count - _unit_total(observation, unit)
    if shortfall <= 0:
        return None
    minerals = spec["minerals"] * shortfall
    vespene = spec["vespene"] * shortfall
    supply = spec["supply"] * shortfall
    larva = math.ceil(spec["larva"] * shortfall - 1e-9)
    if larva > budget["larva"] or (supply > 0 and supply > budget["supply_left"] + 1e-6):
        return "资源在执行前检查里不成立"
    if not _spend(budget, minerals, vespene):
        return "资源在执行前检查里不成立"
    budget["larva"] -= larva
    budget["supply_left"] -= supply
    budget["units"].add(unit)
    return None


def _check_army_goal(action: dict, observation: dict) -> str | None:
    if _unexpected_keys(action, {"type", "mode", "region_id"}):
        return "参数在执行前检查里不成立：字段应为 mode 和 region_id"
    mode = action.get("mode")
    region_id = action.get("region_id")
    if not isinstance(mode, str) or not mode.strip():
        return "参数在执行前检查里不成立：mode 为空"
    if not isinstance(region_id, str) or region_id not in _region_ids(observation):
        return "位置在执行前检查里不成立：region_id 不在 Observation 的基地编号里"
    return None


def _unexpected_keys(action: dict, allowed: set[str]) -> bool:
    return any(key not in allowed for key in action)


def _region_ids(observation: dict) -> set[str]:
    bases = observation.get("bases") or {}
    found = set()
    own_start = bases.get("own_start") or {}
    if isinstance(own_start.get("label"), str):
        found.add(own_start["label"])
    for group in ("known_bases", "candidate_expansions"):
        for row in bases.get(group) or []:
            if isinstance(row.get("base_id"), str):
                found.add(row["base_id"])
    return found


def _budget(observation: dict) -> dict:
    resources = observation.get("resources") or {}
    return {
        "minerals": int(resources.get("minerals") or 0),
        "vespene": int(resources.get("vespene") or 0),
        "supply_left": float(resources.get("supply_left") or 0),
        "larva": int(resources.get("larva") or 0),
        "bounds": _bounds(observation),
        "units": _owned_unit_names(observation),
        "used_bases": set(),
    }


def _spend(budget: dict, minerals: int, vespene: int) -> bool:
    if minerals > budget["minerals"] or vespene > budget["vespene"]:
        return False
    budget["minerals"] -= minerals
    budget["vespene"] -= vespene
    return True


def _owned_structures(observation: dict) -> set[str]:
    owned = set()
    for row in observation.get("own", {}).get("structures", []):
        token = _token(row.get("type"))
        if token:
            owned.add(token)
    if "HATCHERY" in owned or "LAIR" in owned or "HIVE" in owned:
        owned.add("HATCHERY")
    if "LAIR" in owned or "HIVE" in owned:
        owned.add("LAIR")
    if "HIVE" in owned:
        owned.add("HIVE")
    return owned


def _unit_total(observation: dict, unit: str) -> int:
    total = 0
    own = observation.get("own") or {}
    for group in ("units", "in_training"):
        for row in own.get(group) or []:
            if _token(row.get("type") or row.get("unit")) != unit:
                continue
            count = row.get("count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                continue
            total += count
    return total


def _owned_unit_names(observation: dict) -> set[str]:
    names = set()
    for row in observation.get("own", {}).get("units", []):
        token = _token(row.get("type"))
        if token:
            names.add(token)
    return names


def _owned_units(budget: dict) -> set[str]:
    return budget["units"]


def _bounds(observation: dict) -> tuple[float, float, float, float] | None:
    points = []
    own_start = observation.get("bases", {}).get("own_start", {}).get("position")
    if isinstance(own_start, list) and len(own_start) == 2:
        points.append(own_start)
    for group in ("known_bases", "candidate_expansions"):
        for row in observation.get("bases", {}).get(group, []):
            position = row.get("position")
            if isinstance(position, list) and len(position) == 2:
                points.append(position)
    for row in observation.get("own", {}).get("structures", []):
        for position in row.get("positions") or []:
            if isinstance(position, list) and len(position) == 2:
                points.append(position)
    if not points:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs) - 20, min(ys) - 20, max(xs) + 20, max(ys) + 20


def _check_position(position: Any, bounds) -> str | None:
    if not isinstance(position, list) or len(position) != 2:
        return "位置在执行前检查里不成立"
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in position):
        return "位置在执行前检查里不成立"
    if bounds is None:
        return "位置在执行前检查里不成立"
    x, y = float(position[0]), float(position[1])
    if not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
        return "位置在执行前检查里不成立"
    return None


def _token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = "".join(character for character in value.upper() if character.isalnum())
    return token or None
