"""SwarmBrain per-run LLM / command JSONL logger (schema 1.0.0)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import traceback
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
_MISSING = object()

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|authorization|bearer|password|secret|cookie)\s*[:=]\s*['\"]?([^\s'\"\\]+)"),
    re.compile(r"(?i)sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sanitize_text(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    out = value
    for pat in _SECRET_PATTERNS:
        out = pat.sub(
            lambda m: (m.group(1) + "=***REDACTED***")
            if m.lastindex and m.lastindex >= 1 and not m.group(0).lower().startswith("sk-")
            else "***REDACTED***",
            out,
        )
        out = re.sub(r"(?i)sk-[A-Za-z0-9_\-]{16,}", "***REDACTED***", out)
        out = re.sub(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer ***REDACTED***", out)
    return out


def sanitize_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_obj(v) for v in obj]
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return sanitize_text(str(obj))


def default_log_root(package_root: Path) -> Path:
    env = os.getenv("SWARMBRAIN_RUN_LOG_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (package_root / "logs" / "runs").resolve()


def parse_command_parts(raw: str) -> dict[str, Any] | None:
    parts = re.findall(r"\(([^)]+)\)", raw or "")
    if len(parts) < 3:
        return None
    actor = parts[0].strip()
    action = parts[1].strip()
    target = parts[2].strip()
    actor_name, actor_loc = (actor.split(",", 1) + [""])[:2] if "," in actor else (actor, "")
    target_name, target_loc = (target.split(",", 1) + [""])[:2] if "," in target else (target, "")
    return {
        "actor": actor_name.strip(),
        "actor_location": actor_loc.strip() or None,
        "action": action,
        "target": target_name.strip(),
        "target_location": target_loc.strip() or None,
    }


def classify_filter_reason(raw: str, banned_keywords: list[str] | None = None) -> tuple[str, str]:
    """Map filter hits to finer reason_code values (P1-4)."""
    text = raw or ""
    banned = banned_keywords or []
    for kw in banned:
        if kw and kw.lower() in text.lower():
            low = kw.lower()
            if low in {"queen", "overlord", "zergling", "drone", "larva"}:
                return "unsupported_unit", f"banned_unit:{kw}"
            if low in {"move", "gather minerals", "creep", "mineral"}:
                return "unsupported_action", f"banned_action:{kw}"
            if low in {"extractor"}:
                return "unsupported_unit", f"banned_building:{kw}"
            return "other", f"banned_keyword:{kw}"
    if not re.search(r"\(.*?\)->\(.*?\)->\(.*?\)", text):
        return "invalid_syntax", "not_triple_arrow_command"
    return "other", "removed_by_filter_commands"


def infer_parse_status(
    *,
    channel: str,
    parsed_commands: list[Any] | None,
    parse_error: str | None = None,
    commands_expected: bool | None = None,
) -> str:
    if parse_error:
        return "failed"
    expect = commands_expected
    if expect is None:
        expect = channel in {"macro", "building", "opening"} and channel != "attack"
        # opening force-composition / strategy: callers pass commands_expected=False
    if not expect and channel in {"opening", "attack", "other"}:
        if commands_expected is False:
            return "not_applicable"
    cmds = parsed_commands or []
    if cmds:
        return "success_nonempty"
    if expect:
        return "success_empty"
    return "not_applicable"


@dataclass
class LoggedCommand:
    command_id: str
    command_batch_id: str
    llm_call_id: str | None
    run_id: str
    agent_id: str
    agent_side: str
    player_id: int | None
    channel: str
    raw_command: str
    parsed_command: dict[str, Any] | None
    source: str = "llm"
    race: str | None = "Zerg"
    parts: list[str] = field(default_factory=list)
    status: str = "accepted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_raw(
        cls,
        *,
        raw: str,
        command_id: str,
        command_batch_id: str,
        llm_call_id: str | None,
        run_id: str,
        agent_id: str,
        agent_side: str,
        player_id: int | None,
        channel: str,
        source: str = "llm",
        race: str | None = "Zerg",
    ) -> "LoggedCommand":
        parts = re.findall(r"\(([^)]+)\)", raw or "")
        return cls(
            command_id=command_id,
            command_batch_id=command_batch_id,
            llm_call_id=llm_call_id,
            run_id=run_id,
            agent_id=agent_id,
            agent_side=agent_side,
            player_id=player_id,
            channel=channel,
            raw_command=raw,
            parsed_command=parse_command_parts(raw),
            source=source,
            race=race,
            parts=list(parts[:3]) if len(parts) >= 3 else list(parts),
        )


class SwarmBrainRunLogger:
    def __init__(
        self,
        *,
        package_root: Path,
        mode: str,
        map_name: str = "AutomatonLE",
        run_id: str | None = None,
        log_root: Path | None = None,
        model_name: str | None = None,
        model_provider: str = "openai_compatible",
    ) -> None:
        self.package_root = Path(package_root).resolve()
        self.mode = mode
        self.map_name = map_name
        self.run_id = run_id or new_run_id()
        self.log_root = Path(log_root) if log_root else default_log_root(self.package_root)
        self.run_dir = self.log_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.model_provider = model_provider
        self._lock = threading.RLock()
        self._manifest_path = self.run_dir / "run_manifest.json"
        self._files = {
            "run": self.run_dir / "run_events.jsonl",
            "llm": self.run_dir / "llm_events.jsonl",
            "command": self.run_dir / "command_events.jsonl",
            "errors": self.run_dir / "errors.jsonl",
        }
        for p in self._files.values():
            p.touch(exist_ok=True)
        # Immutable per-call identity snapshots keyed by llm_call_id
        self._open_calls: dict[str, dict[str, Any]] = {}
        self._pending_response: dict[str, dict[str, Any]] = {}
        # command_id -> LoggedCommand pending terminal state
        self._pending_commands: dict[str, LoggedCommand] = {}
        self._last_known_game_loop: int | None = None
        self._last_known_game_time_sec: float | None = None
        self._started_wall_ts = time.time()
        # Run-level defaults only (never used to overwrite frozen call identity)
        self._system_identity = {
            "agent_id": "system",
            "agent_side": "shared",
            "player_id": None,
            "race": None,
        }
        self._write_manifest_initial()

    def note_game_clock(self, game_loop: int | None = None, game_time_sec: float | None = None) -> None:
        if isinstance(game_loop, int):
            self._last_known_game_loop = game_loop
        if isinstance(game_time_sec, (int, float)):
            self._last_known_game_time_sec = float(game_time_sec)

    def set_agent_defaults(self, **_kwargs: Any) -> None:
        """Deprecated no-op: identity must come from immutable call context (P0-2)."""
        return

    def _write_manifest_initial(self) -> None:
        git = _git_commit(self.package_root)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": "running",
            "started_at_utc": utc_now_iso(),
            "ended_at_utc": None,
            "mode": self.mode,
            "map_name": self.map_name,
            "sc2_version": None,
            "base_build": None,
            "data_version": None,
            "random_seed": os.getenv("SWARMBRAIN_RANDOM_SEED") or None,
            "code_git_commit": git,
            "environment": {
                "sc2_version": {"value": None, "unavailable_reason": "not_captured_yet"},
                "base_build": {"value": None, "unavailable_reason": "not_captured_yet"},
                "data_version": {"value": None, "unavailable_reason": "not_captured_yet"},
                "random_seed": {
                    "value": os.getenv("SWARMBRAIN_RANDOM_SEED") or None,
                    "unavailable_reason": None if os.getenv("SWARMBRAIN_RANDOM_SEED") else "not_set",
                },
                "code_git_commit": {
                    "value": git,
                    "unavailable_reason": None if git else "git_unavailable",
                },
            },
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "agents": [],
            "replay": None,
            "result": None,
            "final_game_loop": None,
            "final_game_time_sec": None,
            "log_files": {k: v.name for k, v in self._files.items()},
        }
        self._atomic_write_json(self._manifest_path, manifest)

    def update_manifest(self, **fields: Any) -> None:
        with self._lock:
            data = {}
            if self._manifest_path.is_file():
                try:
                    data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {"schema_version": SCHEMA_VERSION, "run_id": self.run_id}
            data.update(sanitize_obj(fields))
            self._atomic_write_json(self._manifest_path, data)

    def register_agents(self, agents: list[dict[str, Any]]) -> None:
        self.update_manifest(agents=agents)

    def _atomic_write_json(self, path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def _append_jsonl(self, kind: str, event: dict[str, Any], *, fsync: bool = False) -> None:
        path = self._files[kind]
        line = json.dumps(sanitize_obj(event), ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                if fsync:
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass

    def _base_event(
        self,
        event_type: str,
        *,
        feature_scope: str,
        game_loop: Any = _MISSING,
        game_time_sec: Any = _MISSING,
        game_time_formatted: Any = _MISSING,
        agent_id: Any = _MISSING,
        agent_side: Any = _MISSING,
        player_id: Any = _MISSING,
        race: Any = _MISSING,
        **extra: Any,
    ) -> dict[str, Any]:
        sys_id = self._system_identity
        ev = {
            "schema_version": SCHEMA_VERSION,
            "event_type": event_type,
            "event_id": str(uuid.uuid4()),
            "run_id": self.run_id,
            "agent_id": sys_id["agent_id"] if agent_id is _MISSING else agent_id,
            "agent_side": sys_id["agent_side"] if agent_side is _MISSING else agent_side,
            "player_id": sys_id["player_id"] if player_id is _MISSING else player_id,
            "race": sys_id["race"] if race is _MISSING else race,
            "wall_time_utc": utc_now_iso(),
            "game_loop": None if game_loop is _MISSING else game_loop,
            "game_time_sec": None if game_time_sec is _MISSING else game_time_sec,
            "game_time_formatted": None if game_time_formatted is _MISSING else game_time_formatted,
            "feature_scope": feature_scope,
        }
        ev.update(extra)
        return ev

    def write_run_event(self, event_type: str, **payload: Any) -> None:
        try:
            # Run-level events are system/shared by default (P1-1).
            payload.setdefault("agent_id", "system")
            payload.setdefault("agent_side", "shared")
            payload.setdefault("player_id", None)
            payload.setdefault("race", None)
            feature_scope = payload.pop("feature_scope", "audit_only")
            ev = self._base_event(event_type, feature_scope=feature_scope, **payload)
            self._append_jsonl(
                "run",
                ev,
                fsync=event_type
                in {
                    "run_started",
                    "run_completed",
                    "run_crashed",
                    "run_aborted",
                    "replay_saved",
                    "replay_save_failed",
                },
            )
        except Exception as exc:
            self._stderr_warn(f"write_run_event failed: {exc}")

    def write_error(
        self,
        *,
        component: str,
        error_type: str,
        message: str,
        recoverable: bool = True,
        action_taken: str = "continue",
        game_loop: int | None = None,
        operation: str | None = None,
        exc: BaseException | None = None,
        **extra: Any,
    ) -> None:
        try:
            tb_text = None
            source_file = source_function = None
            source_line = None
            if exc is not None:
                tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
                if frames:
                    last = frames[-1]
                    source_file = last.filename
                    source_function = last.name
                    source_line = last.lineno
            elif message:
                tb_text = message
            ev = self._base_event(
                "error",
                feature_scope="audit_only",
                game_loop=game_loop if game_loop is not None else self._last_known_game_loop,
                agent_id="system",
                agent_side="shared",
                player_id=None,
                race=None,
                component=component,
                operation=operation,
                error_type=error_type,
                sanitized_message=sanitize_text(str(message))[:2000] or error_type,
                source_file=source_file,
                source_function=source_function,
                source_line=source_line,
                traceback_sha256=sha256_text(tb_text) if tb_text else None,
                recoverable=recoverable,
                action_taken=action_taken,
                last_known_game_loop=self._last_known_game_loop,
                last_known_game_time_sec=self._last_known_game_time_sec,
                **extra,
            )
            self._append_jsonl("errors", ev, fsync=True)
        except Exception as exc2:
            self._stderr_warn(f"write_error failed: {exc2}")

    def start_llm_call(
        self,
        *,
        channel: str,
        prompt: str,
        input_state: dict[str, Any] | None = None,
        previous_commands: list[Any] | None = None,
        prompt_template_version: str | None = None,
        model_name: str | None = None,
        model_provider: str | None = None,
        game_loop: int | None = None,
        game_time_sec: float | None = None,
        game_time_formatted: str | None = None,
        agent_id: str | None = None,
        agent_side: str | None = None,
        player_id: int | None = None,
        race: str | None = "Zerg",
        feature_scope: str = "label_only",
        **meta: Any,
    ) -> str:
        llm_call_id = str(uuid.uuid4())
        self.note_game_clock(game_loop, game_time_sec)
        try:
            from swarmbrain_replay_meta import format_game_time, loops_to_seconds

            if game_time_sec is None and isinstance(game_loop, int):
                game_time_sec = loops_to_seconds(game_loop)
            if game_time_formatted is None:
                game_time_formatted = format_game_time(game_time_sec)
            # Freeze identity at start (P0-2). Explicit None is preserved.
            # Time semantics (pilot): game_loop on started == observation_game_loop.
            obs_loop = meta.pop("observation_game_loop", game_loop)
            obs_sec = meta.pop("observation_game_time_sec", game_time_sec)
            req_loop = meta.pop("request_started_game_loop", None)
            req_sec = meta.pop("request_started_game_time_sec", None)
            if obs_sec is None and isinstance(obs_loop, int):
                obs_sec = loops_to_seconds(obs_loop)
            if game_time_formatted is None:
                game_time_formatted = format_game_time(obs_sec if obs_sec is not None else game_time_sec)
            frozen = {
                "llm_call_id": llm_call_id,
                "run_id": self.run_id,
                "channel": channel,
                "agent_id": agent_id,
                "agent_side": agent_side,
                "player_id": player_id,
                "race": race,
                "started_game_loop": obs_loop,
                "observation_game_loop": obs_loop,
                "observation_game_time_sec": obs_sec,
                "request_started_game_loop": req_loop,
                "request_started_game_time_sec": req_sec,
                "feature_scope": feature_scope,
                "phase": meta.get("phase"),
                "opening_index": meta.get("opening_index"),
            }
            ev = self._base_event(
                "llm_call_started",
                feature_scope=feature_scope,
                game_loop=obs_loop,
                game_time_sec=obs_sec,
                game_time_formatted=game_time_formatted,
                agent_id=agent_id,
                agent_side=agent_side,
                player_id=player_id,
                race=race,
                llm_call_id=llm_call_id,
                channel=channel,
                model_provider=model_provider or self.model_provider,
                model_name=model_name or self.model_name,
                prompt_template_version=prompt_template_version or f"{channel}_v1",
                prompt_sha256=sha256_text(prompt),
                prompt=prompt,
                input_state=input_state or {},
                previous_commands=previous_commands or [],
                observation_game_loop=obs_loop,
                observation_game_time_sec=obs_sec,
                request_started_game_loop=req_loop,
                request_started_game_time_sec=req_sec,
                **meta,
            )
            self._open_calls[llm_call_id] = frozen
            self._append_jsonl("llm", ev, fsync=True)
        except Exception as exc:
            self._stderr_warn(f"start_llm_call failed: {exc}")
            self.write_error(component="run_logger", error_type=type(exc).__name__, message=str(exc), exc=exc)
        return llm_call_id

    def stash_llm_response(
        self,
        llm_call_id: str,
        *,
        raw_response: str,
        latency_ms: float | None = None,
        finish_reason: str | None = "stop",
        token_usage: dict[str, Any] | None = None,
        retry_count: int = 0,
        game_loop: int | None = None,
        game_time_sec: float | None = None,
        game_time_formatted: str | None = None,
    ) -> None:
        """Store response until parse finishes, then complete_llm_call (P0-3)."""
        # Prefer logger's latest known clock for response_completed when caller omitted it.
        gl = game_loop if game_loop is not None else self._last_known_game_loop
        gts = game_time_sec if game_time_sec is not None else self._last_known_game_time_sec
        from swarmbrain_replay_meta import format_game_time, loops_to_seconds

        if gts is None and isinstance(gl, int):
            gts = loops_to_seconds(gl)
        gtf = game_time_formatted or format_game_time(gts)
        info = self._open_calls.get(llm_call_id) or {}
        self._pending_response[llm_call_id] = {
            "raw_response": raw_response,
            "latency_ms": latency_ms,
            "finish_reason": finish_reason,
            "token_usage": token_usage,
            "retry_count": retry_count,
            "game_loop": gl,
            "game_time_sec": gts,
            "game_time_formatted": gtf,
            "request_started_game_loop": info.get("request_started_game_loop"),
            "request_started_game_time_sec": info.get("request_started_game_time_sec"),
            "response_completed_game_loop": gl,
            "response_completed_game_time_sec": gts,
        }
        self.note_game_clock(gl, gts)

    def complete_llm_call(
        self,
        llm_call_id: str,
        *,
        raw_response: str | None = None,
        parsed_commands: list[Any] | None = None,
        parse_status: str | None = None,
        parse_error: str | None = None,
        latency_ms: float | None = None,
        finish_reason: str | None = None,
        token_usage: dict[str, Any] | None = None,
        fallback_used: bool = False,
        game_loop: int | None = None,
        game_time_sec: float | None = None,
        game_time_formatted: str | None = None,
        commands_expected: bool | None = None,
        **meta: Any,
    ) -> None:
        try:
            info = self._open_calls.pop(llm_call_id, {})
            pending = self._pending_response.pop(llm_call_id, {})
            raw = raw_response if raw_response is not None else pending.get("raw_response", "")
            latency_ms = latency_ms if latency_ms is not None else pending.get("latency_ms")
            finish_reason = finish_reason if finish_reason is not None else pending.get("finish_reason", "stop")
            token_usage = token_usage if token_usage is not None else pending.get("token_usage")
            gl = game_loop if game_loop is not None else pending.get(
                "response_completed_game_loop", pending.get("game_loop", info.get("started_game_loop"))
            )
            gts = game_time_sec if game_time_sec is not None else pending.get(
                "response_completed_game_time_sec", pending.get("game_time_sec")
            )
            gtf = game_time_formatted if game_time_formatted is not None else pending.get("game_time_formatted")
            from swarmbrain_replay_meta import format_game_time, loops_to_seconds

            if gts is None and isinstance(gl, int):
                gts = loops_to_seconds(gl)
            if gtf is None:
                gtf = format_game_time(gts)
            channel = info.get("channel") or meta.get("channel")
            cmds = list(parsed_commands or [])
            status = parse_status or infer_parse_status(
                channel=str(channel or "other"),
                parsed_commands=cmds,
                parse_error=parse_error,
                commands_expected=commands_expected,
            )
            if status == "success_nonempty" and not cmds:
                status = "failed"
                parse_error = parse_error or "success_nonempty_but_empty_list"
            if status == "success" and cmds:
                status = "success_nonempty"
            elif status == "success" and not cmds:
                status = "success_empty"
            self.note_game_clock(gl if isinstance(gl, int) else None, gts if isinstance(gts, (int, float)) else None)
            extra_meta = {k: v for k, v in meta.items() if k not in {"channel", "agent_id", "agent_side", "player_id", "race"}}
            if info.get("phase") is not None:
                extra_meta.setdefault("phase", info.get("phase"))
            if info.get("opening_index") is not None:
                extra_meta.setdefault("opening_index", info.get("opening_index"))
            obs_loop = info.get("observation_game_loop", info.get("started_game_loop"))
            obs_sec = info.get("observation_game_time_sec")
            if obs_sec is None and isinstance(obs_loop, int):
                obs_sec = loops_to_seconds(obs_loop)
            # Compatibility: completed.game_loop == response_completed_game_loop
            resp_loop = gl
            resp_sec = gts
            req_loop = pending.get("request_started_game_loop", info.get("request_started_game_loop"))
            req_sec = pending.get("request_started_game_time_sec", info.get("request_started_game_time_sec"))
            if isinstance(obs_loop, int) and isinstance(resp_loop, int) and resp_loop < obs_loop:
                # Never invent time travel; clamp completed to observation if clock unavailable.
                resp_loop = obs_loop
                resp_sec = obs_sec
                gtf = format_game_time(resp_sec)

            # strategic_plan (label_only macro/building/attack)
            strategic_plan = extra_meta.pop("strategic_plan", None)
            feature_scope = info.get("feature_scope") or "label_only"
            try:
                from swarmbrain_strategic_plan import (
                    make_failed_plan,
                    parse_strategic_plan_from_response,
                    should_attach_strategic_plan,
                )

                if should_attach_strategic_plan(channel=str(channel) if channel else None, feature_scope=feature_scope):
                    if strategic_plan is None:
                        strategic_plan = parse_strategic_plan_from_response(
                            raw if isinstance(raw, str) else str(raw or ""),
                            fallback_used=bool(fallback_used),
                            channel=str(channel) if channel else None,
                            parsed_commands=cmds,
                        )
                    elif isinstance(strategic_plan, dict):
                        from swarmbrain_strategic_plan import normalize_strategic_plan, reconcile_intent_with_commands

                        strategic_plan, _ = normalize_strategic_plan(
                            strategic_plan, fallback_used=bool(fallback_used)
                        )
                        strategic_plan = reconcile_intent_with_commands(
                            strategic_plan,
                            channel=str(channel) if channel else None,
                            parsed_commands=cmds,
                        )
                    else:
                        strategic_plan = make_failed_plan(
                            reason="strategic_plan_not_object", fallback_used=bool(fallback_used)
                        )
            except Exception as sp_exc:
                strategic_plan = {
                    "schema_version": "strategic_plan_v0.1.0",
                    "primary_intent": "unknown",
                    "secondary_intents": [],
                    "intent_confidence": 0.0,
                    "intent_rationale_short": f"strategic_plan_hook_error:{type(sp_exc).__name__}"[:200],
                    "horizon_sec": None,
                    "source": "llm_structured",
                    "parse_status": "failed",
                }

            ev = self._base_event(
                "llm_call_completed",
                feature_scope=feature_scope,
                game_loop=resp_loop,
                game_time_sec=resp_sec,
                game_time_formatted=gtf,
                agent_id=info.get("agent_id"),
                agent_side=info.get("agent_side"),
                player_id=info.get("player_id"),
                race=info.get("race"),
                llm_call_id=llm_call_id,
                channel=channel,
                raw_response=raw,
                finish_reason=finish_reason,
                latency_ms=latency_ms,
                token_usage=token_usage,
                parsed_commands=cmds,
                parse_status=status,
                parse_error=parse_error,
                fallback_used=fallback_used,
                started_game_loop=obs_loop,
                observation_game_loop=obs_loop,
                observation_game_time_sec=obs_sec,
                request_started_game_loop=req_loop,
                request_started_game_time_sec=req_sec,
                response_completed_game_loop=resp_loop,
                response_completed_game_time_sec=resp_sec,
                retry_count=pending.get("retry_count", meta.get("retry_count")),
                **({"strategic_plan": strategic_plan} if strategic_plan is not None else {}),
                **extra_meta,
            )
            self._append_jsonl("llm", ev)
        except Exception as exc:
            self._stderr_warn(f"complete_llm_call failed: {exc}")

    def fail_llm_call(
        self,
        llm_call_id: str,
        *,
        error: Exception | str,
        retry_count: int = 0,
        fallback_used: bool = False,
        game_loop: int | None = None,
        game_time_sec: float | None = None,
        game_time_formatted: str | None = None,
        **meta: Any,
    ) -> None:
        try:
            from swarmbrain_replay_meta import format_game_time, loops_to_seconds

            info = self._open_calls.pop(llm_call_id, {})
            self._pending_response.pop(llm_call_id, None)
            err = error if isinstance(error, Exception) else RuntimeError(str(error))
            gl = game_loop if game_loop is not None else (
                self._last_known_game_loop
                if self._last_known_game_loop is not None
                else info.get("started_game_loop")
            )
            gts = game_time_sec
            if gts is None:
                gts = self._last_known_game_time_sec
            if gts is None and isinstance(gl, int):
                gts = loops_to_seconds(gl)
            gtf = game_time_formatted or format_game_time(gts)
            ev = self._base_event(
                "llm_call_failed",
                feature_scope=info.get("feature_scope") or "label_only",
                game_loop=gl,
                game_time_sec=gts,
                game_time_formatted=gtf,
                agent_id=info.get("agent_id"),
                agent_side=info.get("agent_side"),
                player_id=info.get("player_id"),
                race=info.get("race"),
                llm_call_id=llm_call_id,
                channel=info.get("channel") or meta.get("channel"),
                error_type=type(err).__name__,
                error_message=sanitize_text(str(err))[:2000],
                retry_count=retry_count,
                fallback_used=fallback_used,
                phase=info.get("phase"),
                opening_index=info.get("opening_index"),
                **{k: v for k, v in meta.items() if k not in {"channel", "agent_id", "agent_side", "player_id", "race"}},
            )
            # Optional failed strategic_plan for in-game label_only channels
            try:
                from swarmbrain_strategic_plan import make_failed_plan, should_attach_strategic_plan

                ch = info.get("channel") or meta.get("channel")
                fs = info.get("feature_scope") or "label_only"
                if should_attach_strategic_plan(channel=ch, feature_scope=fs):
                    ev["strategic_plan"] = make_failed_plan(
                        reason="llm_call_failed",
                        fallback_used=bool(fallback_used),
                    )
            except Exception:
                pass
            self._append_jsonl("llm", ev, fsync=True)
        except Exception as exc:
            self._stderr_warn(f"fail_llm_call failed: {exc}")

    def _last_known_clock_payload(self) -> dict[str, Any]:
        from swarmbrain_replay_meta import format_game_time, loops_to_seconds

        loop = self._last_known_game_loop
        sec = self._last_known_game_time_sec
        if sec is None and isinstance(loop, int):
            sec = loops_to_seconds(loop)
        return {
            "last_known_game_loop": loop,
            "last_known_game_time_sec": sec,
            "last_known_game_time_formatted": format_game_time(sec),
        }

    def attach_replay(
        self,
        replay_path: str | Path | None,
        *,
        result: dict[str, Any] | None = None,
        save_error: str | None = None,
        final_game_loop: int | None = None,
        final_game_time_sec: float | None = None,
        termination_reason: str | None = None,
        runtime_result: dict[str, Any] | None = None,
    ) -> None:
        """Replay state machine: requested -> saved | save_failed; parse header for final loops."""
        try:
            from swarmbrain_replay_meta import parse_replay_file

            self.write_run_event(
                "replay_save_requested",
                expected_path=str(replay_path) if replay_path else None,
                **self._last_known_clock_payload(),
            )
            last_obs_loop = self._last_known_game_loop
            last_obs_sec = self._last_known_game_time_sec

            def _fail(err: str, path: Path | None = None) -> None:
                replay = {
                    "path": str(path) if path else (str(replay_path) if replay_path else None),
                    "file_name": path.name if path else None,
                    "save_status": "failed",
                    "error": sanitize_text(err),
                }
                self.write_run_event(
                    "replay_save_failed",
                    replay=replay,
                    error_type="ReplaySaveError",
                    sanitized_message=sanitize_text(err)[:500],
                    **self._last_known_clock_payload(),
                )
                fields: dict[str, Any] = {
                    "replay": replay,
                    "last_observed_game_loop": last_obs_loop,
                    "last_observed_game_time_sec": last_obs_sec,
                    "replay_final_game_loop": None,
                    "replay_duration_sec": None,
                }
                if result is not None:
                    fields["result"] = result
                if runtime_result is not None:
                    fields["runtime_result"] = runtime_result
                if termination_reason:
                    fields["termination_reason"] = termination_reason
                # Do not invent replay final times from last observed.
                fields["final_game_loop"] = None
                fields["final_game_time_sec"] = None
                self.update_manifest(**fields)

            if save_error:
                path = Path(replay_path).resolve() if replay_path else None
                _fail(save_error, path if path and path.is_file() else None)
                return
            if not replay_path:
                _fail("replay path missing")
                return
            path = Path(replay_path).resolve()
            if not path.is_file():
                _fail("file_not_found_after_run_game", path)
                return
            size = path.stat().st_size
            if size <= 0:
                _fail("empty_replay_file", path)
                return

            digest = sha256_file(path)
            meta = parse_replay_file(path)
            parse_ok = meta.get("parse_status") == "ok"
            replay = {
                "path": str(path),
                "file_name": path.name,
                "size_bytes": size,
                "sha256": digest,
                "saved_at_utc": utc_now_iso(),
                "save_status": "ok",
                "parse_status": "ok" if parse_ok else "failed",
                "parse_error": None if parse_ok else sanitize_text(str(meta.get("parse_error") or "parse_failed"))[:500],
            }

            replay_final_loop = meta.get("replay_final_game_loop") if parse_ok else None
            replay_duration = meta.get("replay_duration_sec") if parse_ok else None
            # Compatibility: final_* means replay real end time when parse succeeds.
            fields: dict[str, Any] = {
                "replay": replay,
                "last_observed_game_loop": last_obs_loop,
                "last_observed_game_time_sec": last_obs_sec,
                "replay_final_game_loop": replay_final_loop,
                "replay_duration_sec": replay_duration,
                "final_game_loop": replay_final_loop,
                "final_game_time_sec": replay_duration,
                "map_name_runtime": self.map_name,
                "map_name_replay": meta.get("map_name_replay") if parse_ok else None,
            }
            if parse_ok:
                fields["base_build"] = meta.get("base_build")
                fields["data_version"] = meta.get("data_version")
                fields["sc2_version"] = meta.get("sc2_version")
                fields["player_races"] = meta.get("player_races")
                fields["replay_result"] = meta.get("player_results")
                fields["environment"] = {
                    "sc2_version": {"value": meta.get("sc2_version"), "unavailable_reason": None},
                    "base_build": {"value": meta.get("base_build"), "unavailable_reason": None},
                    "data_version": {"value": meta.get("data_version"), "unavailable_reason": None},
                    "random_seed": {
                        "value": os.getenv("SWARMBRAIN_RANDOM_SEED") or None,
                        "unavailable_reason": None if os.getenv("SWARMBRAIN_RANDOM_SEED") else "not_set",
                    },
                    "code_git_commit": {
                        "value": _git_commit(self.package_root),
                        "unavailable_reason": None if _git_commit(self.package_root) else "git_unavailable",
                    },
                }
            if runtime_result is not None:
                fields["runtime_result"] = runtime_result
            elif result is not None:
                fields["runtime_result"] = result
            if result is not None:
                fields["result"] = result
            if termination_reason:
                fields["termination_reason"] = termination_reason
            if runtime_result is not None or result is not None:
                fields["result_source"] = "runtime_and_replay" if parse_ok else "runtime"

            self.update_manifest(**fields)
            self.write_run_event(
                "replay_saved",
                replay=replay,
                game_loop=replay_final_loop,
                game_time_sec=replay_duration,
                **self._last_known_clock_payload(),
            )
        except Exception as exc:
            self.write_error(
                component="attach_replay",
                operation="attach_replay",
                error_type=type(exc).__name__,
                message=str(exc),
                recoverable=True,
                action_taken="continue",
                exc=exc,
            )
            try:
                self.write_run_event(
                    "replay_save_failed",
                    error_type=type(exc).__name__,
                    sanitized_message=sanitize_text(str(exc))[:500],
                    **self._last_known_clock_payload(),
                )
                self.update_manifest(
                    replay={
                        "path": str(replay_path) if replay_path else None,
                        "save_status": "failed",
                        "error": sanitize_text(str(exc)),
                    }
                )
            except Exception:
                pass

    def finalize(self, status: str, **meta: Any) -> None:
        try:
            # Close any in-flight LLM calls interrupted by run end (crash retest).
            for cid in list(self._open_calls.keys()):
                try:
                    self.fail_llm_call(
                        cid,
                        error=RuntimeError(f"run_{status}_before_llm_complete"),
                        fallback_used=False,
                        game_loop=self._last_known_game_loop,
                        game_time_sec=self._last_known_game_time_sec,
                    )
                except Exception:
                    pass
            if status in {"crashed", "aborted", "startup_failed"}:
                self.skip_pending_commands(
                    reason_code="run_crashed" if status == "crashed" else "run_aborted",
                    game_loop=self._last_known_game_loop,
                )
            elif status == "completed":
                self.skip_pending_commands(
                    reason_code="end_of_run_unexecuted",
                    game_loop=self._last_known_game_loop,
                )
            event_map = {
                "completed": "run_completed",
                "aborted": "run_aborted",
                "crashed": "run_crashed",
                "startup_failed": "run_aborted",
            }
            clock = self._last_known_clock_payload()
            meta.setdefault("last_known_game_loop", clock["last_known_game_loop"])
            meta.setdefault("last_known_game_time_sec", clock["last_known_game_time_sec"])
            meta.setdefault("last_known_game_time_formatted", clock["last_known_game_time_formatted"])
            meta.setdefault("active_llm_call_ids", list(self._open_calls.keys()))
            meta.setdefault("pending_command_ids", list(self._pending_commands.keys()))
            self.write_run_event(event_map.get(status, "run_completed"), status=status, **meta)
            manifest_fields = {
                "status": status,
                "ended_at_utc": utc_now_iso(),
                "last_observed_game_loop": self._last_known_game_loop,
                "last_observed_game_time_sec": self._last_known_game_time_sec,
            }
            # Do not overwrite replay_final_* here; attach_replay owns those.
            self.update_manifest(**manifest_fields)
        except Exception as exc:
            self._stderr_warn(f"finalize failed: {exc}")

    def select_fallback(
        self,
        llm_call_id: str,
        *,
        reason: str,
        strategy_name: str,
        strategy_version: str = "v1",
        commands: list[Any] | None = None,
        channel: str | None = None,
        game_loop: int | None = None,
        **meta: Any,
    ) -> None:
        try:
            info = self._open_calls.get(llm_call_id, {})
            ev = self._base_event(
                "llm_fallback_selected",
                feature_scope=info.get("feature_scope") or "label_only",
                game_loop=game_loop if game_loop is not None else info.get("started_game_loop"),
                agent_id=info.get("agent_id"),
                agent_side=info.get("agent_side"),
                player_id=info.get("player_id"),
                race=info.get("race"),
                llm_call_id=llm_call_id,
                channel=channel or info.get("channel"),
                reason=reason,
                strategy_name=strategy_name,
                strategy_version=strategy_version,
                commands=commands or [],
                is_llm_generated=False,
                phase=info.get("phase"),
                opening_index=info.get("opening_index"),
                **meta,
            )
            self._append_jsonl("llm", ev, fsync=True)
        except Exception as exc:
            self._stderr_warn(f"select_fallback failed: {exc}")

    def write_command_event(
        self,
        event_type: str,
        *,
        command_id: str,
        llm_call_id: str | None,
        command_batch_id: str | None,
        channel: str,
        raw_command: str,
        status: str,
        source: str = "llm",
        parsed_command: dict[str, Any] | None = None,
        reason_code: str | None = None,
        reason_detail: str | None = None,
        game_loop: int | None = None,
        game_time_sec: float | None = None,
        game_time_formatted: str | None = None,
        agent_id: str | None = None,
        agent_side: str | None = None,
        player_id: int | None = None,
        race: str | None = None,
        call_context: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        try:
            ctx = call_context or self._open_calls.get(llm_call_id or "", {}) or {}
            if source == "llm" and not llm_call_id:
                raise ValueError("source=llm requires llm_call_id")
            # fallback may still carry llm_call_id for provenance
            event_llm_id = llm_call_id
            if source == "system" and not llm_call_id:
                event_llm_id = None
            self.note_game_clock(game_loop, game_time_sec)
            ev = self._base_event(
                event_type,
                feature_scope="label_only" if source in {"llm", "fallback"} else "audit_only",
                game_loop=game_loop,
                game_time_sec=game_time_sec,
                game_time_formatted=game_time_formatted,
                agent_id=agent_id if agent_id is not None else ctx.get("agent_id"),
                agent_side=agent_side if agent_side is not None else ctx.get("agent_side"),
                player_id=player_id if player_id is not None else ctx.get("player_id"),
                race=race if race is not None else ctx.get("race"),
                llm_call_id=event_llm_id,
                command_batch_id=command_batch_id,
                command_id=command_id,
                channel=channel,
                raw_command=raw_command,
                parsed_command=parsed_command or parse_command_parts(raw_command),
                source=source,
                status=status,
                reason_code=reason_code,
                reason_detail=reason_detail,
                **extra,
            )
            self._append_jsonl("command", ev)
        except Exception as exc:
            self._stderr_warn(f"write_command_event failed: {exc}")

    def log_command_pipeline(
        self,
        *,
        llm_call_id: str,
        channel: str,
        proposed: list[str],
        accepted: list[str],
        filtered: list[tuple[str, str, str]] | None = None,
        banned_keywords: list[str] | None = None,
        source: str = "llm",
        game_loop: int | None = None,
        game_time_sec: float | None = None,
        game_time_formatted: str | None = None,
        agent_id: str | None = None,
        agent_side: str | None = None,
        player_id: int | None = None,
        race: str | None = "Zerg",
        feature_scope: str | None = None,
    ) -> list[LoggedCommand]:
        """Emit proposed/accepted/filtered; return accepted LoggedCommand objects (P0-4)."""
        batch_id = str(uuid.uuid4())
        accepted_set = set(accepted)
        call_ctx = self._open_calls.get(llm_call_id, {})
        # Prefer frozen call identity over caller args when available
        agent_id = call_ctx.get("agent_id", agent_id)
        agent_side = call_ctx.get("agent_side", agent_side)
        player_id = call_ctx.get("player_id", player_id)
        race = call_ctx.get("race", race)
        accepted_objs: list[LoggedCommand] = []
        for raw in proposed:
            cid = str(uuid.uuid4())
            use_llm_id = llm_call_id if source in {"llm", "fallback"} else None
            self.write_command_event(
                "command_proposed",
                command_id=cid,
                llm_call_id=use_llm_id,
                command_batch_id=batch_id,
                channel=channel,
                raw_command=raw,
                status="proposed",
                source=source,
                game_loop=game_loop,
                game_time_sec=game_time_sec,
                game_time_formatted=game_time_formatted,
                agent_id=agent_id,
                agent_side=agent_side,
                player_id=player_id,
                race=race,
                call_context=call_ctx,
            )
            if raw in accepted_set:
                self.write_command_event(
                    "command_accepted",
                    command_id=cid,
                    llm_call_id=use_llm_id,
                    command_batch_id=batch_id,
                    channel=channel,
                    raw_command=raw,
                    status="accepted",
                    source=source,
                    game_loop=game_loop,
                    game_time_sec=game_time_sec,
                    game_time_formatted=game_time_formatted,
                    agent_id=agent_id,
                    agent_side=agent_side,
                    player_id=player_id,
                    race=race,
                    call_context=call_ctx,
                )
                obj = LoggedCommand.from_raw(
                    raw=raw,
                    command_id=cid,
                    command_batch_id=batch_id,
                    llm_call_id=use_llm_id,
                    run_id=self.run_id,
                    agent_id=str(agent_id or ""),
                    agent_side=str(agent_side or ""),
                    player_id=player_id,
                    channel=channel,
                    source=source,
                    race=race,
                )
                self._pending_commands[cid] = obj
                accepted_objs.append(obj)
            else:
                reason_code, reason_detail = classify_filter_reason(raw, banned_keywords)
                if filtered:
                    for fr, code, detail in filtered:
                        if fr == raw:
                            reason_code, reason_detail = code, detail
                            break
                self.write_command_event(
                    "command_filtered",
                    command_id=cid,
                    llm_call_id=use_llm_id,
                    command_batch_id=batch_id,
                    channel=channel,
                    raw_command=raw,
                    status="filtered",
                    source=source,
                    reason_code=reason_code,
                    reason_detail=reason_detail,
                    game_loop=game_loop,
                    game_time_sec=game_time_sec,
                    game_time_formatted=game_time_formatted,
                    agent_id=agent_id,
                    agent_side=agent_side,
                    player_id=player_id,
                    race=race,
                    call_context=call_ctx,
                )
        return accepted_objs

    def log_command_terminal(
        self,
        cmd: LoggedCommand,
        event_type: str,
        *,
        reason_code: str | None = None,
        reason_detail: str | None = None,
        game_loop: int | None = None,
        game_time_sec: float | None = None,
        game_time_formatted: str | None = None,
    ) -> None:
        status = {
            "command_dispatched": "dispatched",
            "command_succeeded": "succeeded",
            "command_failed": "failed",
            "command_skipped": "skipped",
        }.get(event_type, event_type.replace("command_", ""))
        self.write_command_event(
            event_type,
            command_id=cmd.command_id,
            llm_call_id=cmd.llm_call_id,
            command_batch_id=cmd.command_batch_id,
            channel=cmd.channel,
            raw_command=cmd.raw_command,
            status=status,
            source=cmd.source,
            parsed_command=cmd.parsed_command,
            reason_code=reason_code,
            reason_detail=reason_detail,
            game_loop=game_loop,
            game_time_sec=game_time_sec,
            game_time_formatted=game_time_formatted,
            agent_id=cmd.agent_id,
            agent_side=cmd.agent_side,
            player_id=cmd.player_id,
            race=cmd.race,
        )
        if event_type in {"command_succeeded", "command_failed", "command_skipped"}:
            self._pending_commands.pop(cmd.command_id, None)
        elif event_type == "command_dispatched":
            cmd.status = "dispatched"

    def skip_pending_commands(self, *, reason_code: str = "run_crashed", game_loop: int | None = None) -> int:
        pending = list(self._pending_commands.values())
        for cmd in pending:
            if cmd.status in {"accepted", "dispatched"}:
                self.log_command_terminal(
                    cmd,
                    "command_skipped",
                    reason_code=reason_code,
                    reason_detail=reason_code,
                    game_loop=game_loop if game_loop is not None else self._last_known_game_loop,
                )
        return len(pending)

    def replay_belongs_to_this_run(self, path: Path | None) -> bool:
        """True only if replay exists, non-empty, and mtime is after this run started."""
        if path is None:
            return False
        try:
            p = Path(path)
            if not p.is_file() or p.stat().st_size <= 0:
                return False
            return p.stat().st_mtime >= (self._started_wall_ts - 2.0)
        except Exception:
            return False

    @staticmethod
    def _stderr_warn(msg: str) -> None:
        print(f"[run-logger] WARNING: {msg}", flush=True)


def _git_commit(root: Path) -> str | None:
    try:
        import subprocess

        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if r.returncode == 0:
            return (r.stdout or "").strip() or None
    except Exception:
        return None
    return None


# --- LLM call context (used by swarmbrain.py invoke layer) ---

_llm_ctx: ContextVar[dict[str, Any] | None] = ContextVar("swarmbrain_llm_ctx", default=None)


def set_llm_call_context(**kwargs: Any):
    """Set context for the next LLM invoke(s) in this task/thread."""
    return _llm_ctx.set(kwargs)


def reset_llm_call_context(token) -> None:
    _llm_ctx.reset(token)


def get_llm_call_context() -> dict[str, Any] | None:
    return _llm_ctx.get()
