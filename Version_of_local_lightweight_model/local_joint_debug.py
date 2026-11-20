"""Send one saved Observation to the local model tunnel and archive the reply.

The model action is never sent to a live game.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import select
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from local_joint_judge import (
    LOCAL_WALL_TIMEOUT_S,
    MODEL_NAME,
    build_chat_request,
    judge_reply,
    render_user_message,
)
from local_rule_config import PACKAGE_ROOT

BASE_URL = "http://127.0.0.1:8000"
LOCK_PATH = PACKAGE_ROOT / "outputs" / "joint_debug" / "request.lock"
MAX_MODEL_LEN = 73440


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one saved Observation to 127.0.0.1:8000")
    parser.add_argument("--observation", type=Path, default=None)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--message-tokens", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=LOCAL_WALL_TIMEOUT_S)
    parser.add_argument("--cancel-after", type=float, default=None)
    parser.add_argument("--same-as", type=Path, default=None)
    args = parser.parse_args()
    if args.cancel_after is None and args.timeout < LOCAL_WALL_TIMEOUT_S:
        raise SystemExit(f"客户端等待时间必须不少于 {LOCAL_WALL_TIMEOUT_S} 秒")
    if args.cancel_after is not None and args.cancel_after <= 0:
        raise SystemExit("断开时间必须为正数")
    if args.max_tokens is not None and args.max_tokens < 1:
        raise SystemExit("max_tokens 必须为正数")
    if args.message_tokens is not None and args.message_tokens < 1:
        raise SystemExit("消息 token 数必须为正数")
    observation_path = args.observation or latest_observation()
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    request_id = "joint-" + datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = PACKAGE_ROOT / "outputs" / "joint_debug" / request_id
    output_dir.mkdir(parents=True, exist_ok=True)
    game = observation.get("game") or {}
    record = {
        "request_meta": {
            "game_id": game.get("game_id"),
            "request_id": request_id,
            "observation_id": game.get("observation_id"),
            "observation_game_time_s": game.get("game_time_s"),
            "plan_epoch": game.get("plan_epoch"),
            "monotonic_send_s": None,
            "monotonic_deadline_s": None,
            "wall_timeout_s": args.timeout,
            "elapsed_s": None,
        },
        "observation_path": str(observation_path),
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
        "executed": False,
        "execution_note": "第一次联调不下到对局。",
    }
    exit_code = 2
    try:
        with single_request_lock():
            model_check = read_models(args.base_url)
            record["model_check"] = model_check
            if not model_check["accepted"]:
                record["reject_reasons"] = ["未看到模型 swarmbrain-qwen3-4b，已停止，没有发送补全请求"]
                print(record["reject_reasons"][0], flush=True)
                return 2
            message_tokens = args.message_tokens
            if message_tokens is None:
                message_tokens = count_message_tokens(
                    args.base_url,
                    [{"role": "user", "content": render_user_message(observation)}],
                )
            max_tokens = completion_max_tokens(message_tokens)
            if args.max_tokens is not None and args.max_tokens != max_tokens:
                record["chat_sent"] = False
                record["reject_reasons"] = [
                    f"max_tokens 应为 {max_tokens}，与传入的 {args.max_tokens} 不一致，已停止，没有发送补全请求"
                ]
                print(record["reject_reasons"][0], flush=True)
                return 2
            request_body = build_chat_request(observation, max_tokens=max_tokens)
            record["request_body"] = request_body
            record["max_tokens_rule"] = {
                "max_model_len": MAX_MODEL_LEN,
                "message_tokens": message_tokens,
                "max_tokens": max_tokens,
            }
            if args.same_as is not None and not same_request_body(request_body, args.same_as):
                record["chat_sent"] = False
                record["reject_reasons"] = ["请求体与指定记录不一致，已停止，没有发送补全请求"]
                print(record["reject_reasons"][0], flush=True)
                return 2
            if args.cancel_after is not None:
                record["request_meta"]["wall_timeout_s"] = args.cancel_after
                record["request_meta"]["cancel_after_s"] = args.cancel_after
                record["chat_sent"] = True
                outcome = cancel_chat_request(args.base_url, request_body, args.cancel_after)
                record["cancel"] = outcome
                record["request_meta"]["sent_wall"] = outcome["sent_wall"]
                record["request_meta"]["disconnect_wall"] = outcome["disconnect_wall"]
                record["request_meta"]["elapsed_send_to_disconnect_s"] = outcome["elapsed_send_to_disconnect_s"]
                record["request_meta"]["elapsed_s"] = outcome["elapsed_send_to_observed_s"]
                record["http_response"] = {
                    "status": outcome["http_status"],
                    "headers": outcome["headers"],
                    "body_text": outcome["body_text"],
                    "body_json": _loads_or_none(outcome["body_text"]) if outcome["body_text"] else None,
                }
                record.update(cancel_verdict(outcome))
                exit_code = 0 if record["decision"] in {"已断开", "已收到响应但未执行"} else 3
                print(
                    f"[joint] decision={record['decision']} "
                    f"disconnect_wall={outcome['disconnect_wall']} "
                    f"elapsed_send_to_disconnect_s={outcome['elapsed_send_to_disconnect_s']} "
                    f"exception={outcome['exception_type']}: {outcome['exception_message']} "
                    f"executed={record['executed']}",
                    flush=True,
                )
                return exit_code
            started = time.monotonic()
            record["request_meta"]["monotonic_send_s"] = round(started, 6)
            record["request_meta"]["monotonic_deadline_s"] = round(started + args.timeout, 6)
            record["chat_sent"] = True
            response, past_deadline = post_chat(args.base_url, request_body, started, timeout_s=args.timeout)
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
            if response["status"] == 200:
                judged = judge_reply(
                    observation,
                    content,
                    finish_reason,
                    past_deadline=past_deadline,
                )
            elif past_deadline:
                judged = _refused("请求已过本地期限")
            else:
                judged = _refused(f"HTTP 状态 {response['status']}")
            record.update(judged)
            exit_code = 0 if record["decision"] == "可执行" else 3
            print(
                f"[joint] decision={record['decision']} reasons={record['reject_reasons']} "
                f"elapsed_s={record['request_meta']['elapsed_s']} executed={record['executed']}",
                flush=True,
            )
    except Exception as exc:
        record["decision"] = "拒绝"
        record["reject_reasons"] = [f"{type(exc).__name__}: {exc}"]
        record["executed"] = False
        print(record["reject_reasons"][0], flush=True)
        exit_code = 1
    finally:
        record["accepted_actions_not_executed"] = record.pop("actions_from_content", [])
        write_json(output_dir / "joint_debug_record.json", record)
        print(f"[joint] record={output_dir / 'joint_debug_record.json'}", flush=True)
    return exit_code


def latest_observation() -> Path:
    matches = sorted((PACKAGE_ROOT / "outputs" / "local_rule").glob("*/observation_sample.json"))
    if not matches:
        raise FileNotFoundError("没有找到 outputs/local_rule/*/observation_sample.json")
    return matches[-1]


def read_models(base_url: str) -> dict:
    url = base_url.rstrip("/") + "/v1/models"
    try:
        status, headers, body_text = http_json(url, None, timeout_s=10)
    except Exception as exc:
        return {"url": url, "accepted": False, "error": f"{type(exc).__name__}: {exc}"}
    parsed = _loads_or_none(body_text)
    model_ids = []
    if isinstance(parsed, dict):
        for row in parsed.get("data") or []:
            if isinstance(row, dict) and row.get("id"):
                model_ids.append(row["id"])
    return {
        "url": url,
        "status": status,
        "headers": headers,
        "body_text": body_text,
        "model_ids": model_ids,
        "accepted": status == 200 and MODEL_NAME in model_ids,
    }


def completion_max_tokens(message_tokens: int) -> int:
    if isinstance(message_tokens, bool) or not isinstance(message_tokens, int) or message_tokens < 1:
        raise ValueError("消息 token 数必须是正整数")
    room = MAX_MODEL_LEN - message_tokens
    if room < 1:
        raise ValueError("消息 token 数已经达到上下文上限，没有可生成的额度")
    return room


def count_message_tokens(base_url: str, messages: list) -> int:
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "add_generation_prompt": True,
    }
    status, _headers, body_text = http_json(base_url.rstrip("/") + "/tokenize", payload, timeout_s=30)
    parsed = _loads_or_none(body_text)
    count = parsed.get("count") if isinstance(parsed, dict) else None
    if status != 200 or isinstance(count, bool) or not isinstance(count, int):
        raise RuntimeError(f"无法取得消息 token 数：HTTP {status} {body_text[:500]}")
    return count


def same_request_body(request_body: dict, record_path: Path) -> bool:
    saved = json.loads(record_path.read_text(encoding="utf-8")).get("request_body")
    return json.dumps(saved, ensure_ascii=False, sort_keys=True) == json.dumps(
        request_body, ensure_ascii=False, sort_keys=True
    )


def cancel_verdict(outcome: dict) -> dict:
    if outcome.get("closed_by_client"):
        decision = "已断开"
    elif outcome.get("http_status") is not None:
        decision = "已收到响应但未执行"
    else:
        decision = "断开未完成"
    return {
        "decision": decision,
        "reject_reasons": [],
        "hold_plan_count": 0,
        "valid_decision_count": 0,
        "actions_from_content": [],
        "executed": False,
        "execution_note": "请求取消测试。返回内容不下到对局。",
    }


def cancel_chat_request(base_url: str, request_body: dict, cancel_after_s: float) -> dict:
    if set(request_body) != {"model", "messages", "max_tokens"}:
        raise ValueError("请求字段只能是 model、messages、max_tokens")
    if request_body.get("model") != MODEL_NAME:
        raise ValueError("模型名必须是 swarmbrain-qwen3-4b")
    if "stop" in request_body or "thinking_token_budget" in request_body:
        raise ValueError("请求不能包含 stop 或 thinking_token_budget")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http":
        raise ValueError("取消测试只走本机 http 隧道")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=cancel_after_s + 5)
    outcome = {
        "closed_by_client": False,
        "bytes_read": 0,
        "http_status": None,
        "headers": {},
        "body_text": "",
        "exception_type": None,
        "exception_message": None,
        "shutdown_error": None,
        "local_result": None,
        "local_address": None,
        "peer_address": None,
        "sent_wall": None,
        "sent_epoch_s": None,
        "disconnect_wall": None,
        "disconnect_epoch_s": None,
        "observed_wall": None,
        "observed_epoch_s": None,
        "elapsed_send_to_disconnect_s": None,
        "elapsed_send_to_observed_s": None,
    }
    try:
        conn.connect()
        sock = conn.sock
        if sock is None:
            raise ConnectionError("连接没有建立套接字")
        outcome["local_address"] = _socket_address(sock.getsockname())
        outcome["peer_address"] = _socket_address(sock.getpeername())
        conn.putrequest("POST", "/v1/chat/completions")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(payload)))
        conn.putheader("Connection", "close")
        conn.endheaders(payload)
        sent_mono = time.monotonic()
        outcome["sent_wall"] = _wall_time()
        outcome["sent_epoch_s"] = round(time.time(), 6)
        chunks: list[bytes] = []
        deadline = sent_mono + cancel_after_s
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = select.select([sock], [], [], min(0.2, remaining))
            except (OSError, ValueError) as exc:
                outcome["exception_type"] = type(exc).__name__
                outcome["exception_message"] = str(exc)
                break
            if not ready:
                continue
            try:
                data = sock.recv(65536)
            except (BlockingIOError, TimeoutError, socket.timeout):
                continue
            except OSError as exc:
                outcome["exception_type"] = type(exc).__name__
                outcome["exception_message"] = str(exc)
                break
            if not data:
                outcome["exception_type"] = "RemoteDisconnected"
                outcome["exception_message"] = "对端在客户端断开前关闭了连接"
                break
            chunks.append(data)
        outcome["disconnect_wall"] = _wall_time()
        outcome["disconnect_epoch_s"] = round(time.time(), 6)
        outcome["elapsed_send_to_disconnect_s"] = round(time.monotonic() - sent_mono, 6)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError as exc:
            outcome["shutdown_error"] = f"{type(exc).__name__}: {exc}"
        try:
            sock.close()
        except OSError as exc:
            outcome["shutdown_error"] = f"{type(exc).__name__}: {exc}"
        outcome["closed_by_client"] = True
        body = b"".join(chunks)
        outcome["bytes_read"] = len(body)
        outcome["body_text"] = body.decode("utf-8", errors="replace")
        outcome["observed_wall"] = outcome["disconnect_wall"]
        outcome["observed_epoch_s"] = outcome["disconnect_epoch_s"]
        outcome["elapsed_send_to_observed_s"] = outcome["elapsed_send_to_disconnect_s"]
        if outcome["exception_type"] is None and outcome["bytes_read"] == 0:
            outcome["local_result"] = "客户端主动关闭连接，未收到响应字节，没有 HTTP 状态码"
        elif outcome["exception_type"] is None:
            outcome["local_result"] = "客户端主动关闭连接，断开前收到未完成的响应字节"
        else:
            outcome["local_result"] = "客户端主动关闭连接，断开前连接出现 " + outcome["exception_type"]
    finally:
        conn.close()
    return outcome


def _wall_time() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _socket_address(value) -> str:
    if isinstance(value, tuple) and len(value) >= 2:
        return f"{value[0]}:{value[1]}"
    return str(value)


def post_chat(base_url: str, request_body: dict, started: float, timeout_s: float = LOCAL_WALL_TIMEOUT_S) -> tuple[dict, bool]:
    url = base_url.rstrip("/") + "/v1/chat/completions"
    try:
        status, headers, body_text = http_json(url, request_body, timeout_s=timeout_s)
        elapsed = time.monotonic() - started
        past_deadline = elapsed > timeout_s
    except TimeoutError as exc:
        elapsed = time.monotonic() - started
        return {
            "status": None,
            "headers": {},
            "body_text": "",
            "body_json": None,
            "elapsed_s": round(elapsed, 6),
            "error": str(exc),
        }, True
    return {
        "status": status,
        "headers": headers,
        "body_text": body_text,
        "body_json": _loads_or_none(body_text),
        "elapsed_s": round(elapsed, 6),
    }, past_deadline


def http_json(url: str, payload: dict | None, timeout_s: float) -> tuple[int, dict, str]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read()
            return response.status, dict(response.headers.items()), body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, dict(exc.headers.items()), body.decode("utf-8", errors="replace")
    except (TimeoutError, socket.timeout):
        raise TimeoutError("请求已过本地期限")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("请求已过本地期限") from exc
        raise


def extract_reply(payload) -> tuple[Any, Any, Any, Any, Any]:
    if not isinstance(payload, dict):
        return None, None, None, None, None
    usage = payload.get("usage")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, None, None, usage, None
    message = choices[0].get("message") if isinstance(choices[0].get("message"), dict) else {}
    unexpected = None
    if "reasoning_content" not in message and "reasoning" in message:
        unexpected = message.get("reasoning")
    return (
        message.get("content"),
        message.get("reasoning_content"),
        choices[0].get("finish_reason"),
        usage,
        unexpected,
    )


def single_request_lock(hold_s: float | None = None):
    return _RequestLock(LOCK_PATH, hold_s)


def lock_hold_mtime(created_s: float, hold_s: float | None) -> float:
    """A longer live wait must not look abandoned while the request is still open."""
    window_s = LOCAL_WALL_TIMEOUT_S + 30
    if isinstance(hold_s, bool) or not isinstance(hold_s, (int, float)) or hold_s <= window_s:
        return created_s
    return created_s + float(hold_s) - window_s


class _RequestLock:
    def __init__(self, path: Path, hold_s: float | None = None):
        self.path = path
        self.hold_s = hold_s
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and _lock_is_stale(self.path):
            self.path.unlink(missing_ok=True)
        try:
            self.handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError("已有一条模型请求未结束，这次不发送") from exc
        os.write(self.handle, str(os.getpid()).encode("ascii"))
        held_until = lock_hold_mtime(time.time(), self.hold_s)
        os.utime(self.path, (held_until, held_until))
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.handle is not None:
            os.close(self.handle)
            self.handle = None
        self.path.unlink(missing_ok=True)


def _lock_is_stale(path: Path) -> bool:
    age_s = time.time() - path.stat().st_mtime
    return age_s > LOCAL_WALL_TIMEOUT_S + 30


def _loads_or_none(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _refused(reason: str) -> dict:
    return {
        "decision": "拒绝",
        "reject_reasons": [reason],
        "hold_plan_count": 0,
        "valid_decision_count": 0,
        "executed": False,
        "execution_note": "第一次联调不下到对局。",
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
