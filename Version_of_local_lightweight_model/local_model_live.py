"""Ask the tunneled model once, from a live game, and archive the raw reply.

The request body stays model, messages, and max_tokens. The caller decides
whether an accepted plan is installed. reasoning_content is only archived.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from local_joint_debug import (
    BASE_URL,
    MAX_MODEL_LEN,
    completion_max_tokens,
    count_message_tokens,
    extract_reply,
    post_chat,
    read_models,
    single_request_lock,
    write_json,
)
from local_joint_judge import (
    ALLOWED_GOAL_TYPES,
    _budget,
    _check_action,
    _owned_structures,
    build_chat_request,
    judge_reply,
    render_user_message,
)

FIRST_REQUEST_GAME_TIME_S = 90.0
REQUEST_INTERVAL_S = 180.0
LIVE_TIMEOUT_S = 1200.0


def request_is_due(game_time_s: float, last_request_game_time_s: float | None) -> bool:
    if last_request_game_time_s is None:
        return game_time_s >= FIRST_REQUEST_GAME_TIME_S
    return game_time_s >= last_request_game_time_s + REQUEST_INTERVAL_S


def should_send_model_request(due: bool, in_flight: bool) -> bool:
    return bool(due) and not in_flight


def request_feedback_gap(
    request_wall: datetime,
    feedback_wall: datetime,
    request_game_time_s: float,
    feedback_game_time_s: float,
) -> dict:
    return {
        "latency_s": round((feedback_wall - request_wall).total_seconds(), 3),
        "game_time_delta_s": round(float(feedback_game_time_s) - float(request_game_time_s), 3),
    }


def match_runs_realtime(call_model: bool, configured_realtime: bool) -> bool:
    """A live model game keeps wall time and game time together. The request does not pause it."""
    if call_model:
        return True
    return bool(configured_realtime)


def extract_actions_object(content: object) -> dict | None:
    """Use the last complete actions object, including one written after thinking text."""
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    found = None
    start = 0
    while start < len(text):
        brace = text.find("{", start)
        if brace < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[brace:])
        except json.JSONDecodeError:
            start = brace + 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("actions"), list):
            found = obj
        start = brace + end
    return found


def accept_compatible_actions(observation: dict, content: object) -> tuple[list[dict], list[str]]:
    """Keep every action the current game can carry out. Skip the rest."""
    parsed = extract_actions_object(content)
    if parsed is None:
        return [], ["content 不是完整 JSON"]
    actions = parsed["actions"]
    reasons = []
    if len(actions) > 3:
        reasons.append("超过 3 个目标的部分没有执行")
        actions = actions[:3]
    if actions == []:
        return [], reasons
    budget = _budget(observation)
    owned = _owned_structures(observation)
    accepted = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            reasons.append(f"第 {index} 个目标不是对象")
            continue
        if action.get("type") not in ALLOWED_GOAL_TYPES:
            reasons.append(f"第 {index} 个目标出现协议之外的类型")
            continue
        reason = _check_action(action, budget, owned, observation)
        if reason:
            reasons.append(f"第 {index} 个目标{reason}")
            continue
        accepted.append(action)
    return accepted, reasons


def decision_for_current_observation(record: dict, observation: dict) -> dict:
    """Apply every currently affordable action from the returned content."""
    content = record.get("content")
    if not isinstance(content, str) or not content.strip():
        reasons = list(record.get("reject_reasons") or []) or ["content 为空"]
        return _live_decision("拒绝", reasons, [])
    accepted, skipped = accept_compatible_actions(observation, content)
    if accepted:
        return _live_decision("可执行", skipped, accepted)
    if not skipped:
        return _live_decision("可执行", [], [])
    return _live_decision("拒绝", skipped, [])


def _live_decision(decision: str, reasons: list[str], actions: list[dict]) -> dict:
    return {
        "decision": decision,
        "reject_reasons": reasons,
        "actions_from_content": actions,
        "hold_plan_count": 1 if decision == "可执行" and not actions else 0,
        "valid_decision_count": len(actions) if decision == "可执行" else 0,
    }


def fetch_model_decision(
    observation: dict,
    output_dir: Path,
    timeout_s: float = LIVE_TIMEOUT_S,
    base_url: str = BASE_URL,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    game = observation.get("game") or {}
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    game_time = game.get("game_time_s")
    request_id = f"live-{stamp}-t{game_time}"
    record_path = output_dir / f"{request_id}.json"
    record = {
        "record_path": str(record_path),
        "request_meta": {
            "game_id": game.get("game_id"),
            "request_id": request_id,
            "observation_id": game.get("observation_id"),
            "observation_game_time_s": game_time,
            "plan_epoch": game.get("plan_epoch"),
            "monotonic_send_s": None,
            "monotonic_deadline_s": None,
            "wall_timeout_s": timeout_s,
            "elapsed_s": None,
        },
        "observation": observation,
        "request_body": None,
        "model_check": None,
        "chat_sent": False,
        "http_response": None,
        "content": None,
        "reasoning_content": None,
        "finish_reason": None,
        "usage": None,
        "decision": "拒绝",
        "reject_reasons": [],
        "hold_plan_count": 0,
        "valid_decision_count": 0,
        "actions_from_content": [],
        "executed": False,
        "execution_note": "等待对局接收这条回复。",
    }
    try:
        with single_request_lock(timeout_s):
            model_check = read_models(base_url)
            record["model_check"] = model_check
            if not model_check["accepted"]:
                record["reject_reasons"] = ["未看到模型 swarmbrain-qwen3-4b，已停止，没有发送补全请求"]
                return record
            messages = [{"role": "user", "content": render_user_message(observation)}]
            message_tokens = count_message_tokens(base_url, messages)
            max_tokens = completion_max_tokens(message_tokens)
            request_body = build_chat_request(observation, max_tokens=max_tokens)
            record["request_body"] = request_body
            record["max_tokens_rule"] = {
                "max_model_len": MAX_MODEL_LEN,
                "message_tokens": message_tokens,
                "max_tokens": max_tokens,
            }
            started = time.monotonic()
            record["request_meta"]["monotonic_send_s"] = round(started, 6)
            record["request_meta"]["monotonic_deadline_s"] = round(started + timeout_s, 6)
            record["chat_sent"] = True
            response, past_deadline = post_chat(base_url, request_body, started, timeout_s=timeout_s)
            record["request_meta"]["elapsed_s"] = response["elapsed_s"]
            record["http_response"] = {
                "status": response["status"],
                "headers": response["headers"],
                "body_text": response["body_text"],
                "body_json": response["body_json"],
            }
            content, reasoning_content, finish_reason, usage, unexpected = extract_reply(response["body_json"])
            record["content"] = content
            record["reasoning_content"] = reasoning_content
            record["unexpected_reasoning_field"] = unexpected
            record["finish_reason"] = finish_reason
            record["usage"] = usage
            if response["status"] == 200 and not response.get("error"):
                judged = judge_reply(observation, content, finish_reason, past_deadline=past_deadline)
            elif past_deadline:
                judged = _refused("请求已过本地期限")
            else:
                judged = _refused(response.get("error") or f"HTTP 状态 {response['status']}")
            record.update(judged)
            record["executed"] = False
            record["execution_note"] = "等待对局接收这条回复。"
    except Exception as exc:
        record["decision"] = "拒绝"
        record["reject_reasons"] = [f"{type(exc).__name__}: {exc}"]
        record["executed"] = False
        record["execution_note"] = "请求没有完成，当前计划保持不变。"
    finally:
        write_json(record_path, _public_record(record))
    return record


def _public_record(record: dict) -> dict:
    public = dict(record)
    public.pop("record_path", None)
    return public


def _refused(reason: str) -> dict:
    return {
        "decision": "拒绝",
        "reject_reasons": [reason],
        "hold_plan_count": 0,
        "valid_decision_count": 0,
        "actions_from_content": [],
        "executed": False,
        "execution_note": "等待对局接收这条回复。",
    }
