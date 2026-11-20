"""Helpers to bind SwarmBrainRunLogger into BotAI / opening LLM calls."""
from __future__ import annotations

import re
import uuid
from typing import Any

from swarmbrain_run_logger import (
    LoggedCommand,
    get_llm_call_context,
    infer_parse_status,
    parse_command_parts,
    reset_llm_call_context,
    set_llm_call_context,
)


def bot_clock(bot) -> dict[str, Any]:
    loop = None
    try:
        loop = int(getattr(bot.state, "game_loop", None) or getattr(bot, "game_loop", None) or 0)
    except Exception:
        loop = None
    sec = None
    try:
        sec = float(getattr(bot, "time", None) or 0.0)
    except Exception:
        sec = None
    if bot is not None and getattr(bot, "run_logger", None) is not None:
        try:
            bot.run_logger.note_game_clock(loop, sec)
        except Exception:
            pass
    return {
        "game_loop": loop,
        "game_time_sec": sec,
        "game_time_formatted": getattr(bot, "time_formatted", None),
    }


def begin_llm_context(logger, *, channel: str, bot=None, input_state=None, previous_commands=None, **extra):
    if logger is None:
        return None
    payload = {
        "logger": logger,
        "channel": channel,
        "input_state": input_state or {},
        "previous_commands": previous_commands or [],
        "defer_complete": True,
        **extra,
    }
    if bot is not None:
        clock = bot_clock(bot)
        payload.update(clock)
        payload.setdefault("observation_game_loop", clock.get("game_loop"))
        payload.setdefault("observation_game_time_sec", clock.get("game_time_sec"))
        payload.setdefault("agent_id", getattr(bot, "agent_id", None))
        payload.setdefault("agent_side", getattr(bot, "agent_side", getattr(bot, "agent_id", None)))
        payload.setdefault("player_id", getattr(bot, "player_id", None))
        payload.setdefault("race", "Zerg")
    if "observation_game_loop" not in payload and "game_loop" in payload:
        payload["observation_game_loop"] = payload.get("game_loop")
        payload["observation_game_time_sec"] = payload.get("game_time_sec")
    return set_llm_call_context(**payload)


def finalize_pending_llm_call(
    *,
    parsed_commands: list[Any] | None = None,
    parse_status: str | None = None,
    parse_error: str | None = None,
    commands_expected: bool | None = None,
    fallback_used: bool = False,
) -> str | None:
    """Complete a deferred LLM call using stashed response + parsed commands (P0-3)."""
    ctx = get_llm_call_context() or {}
    logger = ctx.get("logger")
    call_id = ctx.get("llm_call_id")
    if logger is None or not call_id:
        return call_id
    # Already completed/failed
    if call_id not in getattr(logger, "_open_calls", {}) and call_id not in getattr(logger, "_pending_response", {}):
        return call_id
    if call_id not in getattr(logger, "_open_calls", {}):
        return call_id
    channel = ctx.get("channel") or "other"
    cmds = list(parsed_commands or [])
    status = parse_status or infer_parse_status(
        channel=channel,
        parsed_commands=cmds,
        parse_error=parse_error,
        commands_expected=commands_expected if commands_expected is not None else ctx.get("commands_expected"),
    )
    logger.complete_llm_call(
        call_id,
        parsed_commands=cmds,
        parse_status=status,
        parse_error=parse_error,
        fallback_used=fallback_used,
        # Prefer pending stash response clock; only fall back to observation if missing.
        game_loop=None,
        game_time_sec=None,
        game_time_formatted=None,
        commands_expected=commands_expected if commands_expected is not None else ctx.get("commands_expected"),
    )
    return call_id


def end_llm_context(
    token,
    *,
    parsed_commands: list[Any] | None = None,
    parse_status: str | None = None,
    parse_error: str | None = None,
    commands_expected: bool | None = None,
    fallback_used: bool = False,
    auto_complete: bool = True,
) -> str | None:
    ctx = get_llm_call_context() or {}
    call_id = ctx.get("llm_call_id")
    if auto_complete and call_id:
        finalize_pending_llm_call(
            parsed_commands=parsed_commands,
            parse_status=parse_status,
            parse_error=parse_error,
            commands_expected=commands_expected,
            fallback_used=fallback_used,
        )
    if token is not None:
        reset_llm_call_context(token)
    return call_id


def complete_llm_with_commands(
    logger,
    llm_call_id: str | None,
    *,
    parsed_commands: list[Any] | None = None,
    parse_status: str | None = None,
    parse_error: str | None = None,
    commands_expected: bool | None = None,
    fallback_used: bool = False,
    bot=None,
    channel: str | None = None,
) -> None:
    if logger is None or not llm_call_id:
        return
    if llm_call_id not in getattr(logger, "_open_calls", {}):
        return
    clock = bot_clock(bot) if bot is not None else {}
    cmds = list(parsed_commands or [])
    ch = channel or (logger._open_calls.get(llm_call_id, {}) or {}).get("channel") or "other"
    status = parse_status or infer_parse_status(
        channel=ch,
        parsed_commands=cmds,
        parse_error=parse_error,
        commands_expected=commands_expected,
    )
    logger.complete_llm_call(
        llm_call_id,
        parsed_commands=cmds,
        parse_status=status,
        parse_error=parse_error,
        fallback_used=fallback_used,
        commands_expected=commands_expected,
        **clock,
    )


def extract_raw_commands(text: str) -> list[str]:
    return re.findall(r"\(.*?\)->\(.*?\)->\(.*?\)", text or "")


def log_parsed_command_batch(
    logger,
    *,
    llm_call_id: str | None,
    channel: str,
    proposed: list[str],
    accepted: list[str],
    bot=None,
    source: str = "llm",
    agent_id: str | None = None,
    agent_side: str | None = None,
    player_id: int | None = None,
    banned_keywords: list[str] | None = None,
    race: str | None = "Zerg",
) -> list[LoggedCommand]:
    if logger is None or not llm_call_id:
        return []
    clock = bot_clock(bot) if bot is not None else {}
    return logger.log_command_pipeline(
        llm_call_id=llm_call_id,
        channel=channel,
        proposed=proposed,
        accepted=accepted,
        banned_keywords=banned_keywords,
        source=source,
        agent_id=agent_id or (getattr(bot, "agent_id", None) if bot else None),
        agent_side=agent_side or (getattr(bot, "agent_side", getattr(bot, "agent_id", None)) if bot else None),
        player_id=player_id if player_id is not None else (getattr(bot, "player_id", None) if bot else None),
        race=race,
        **clock,
    )


def enqueue_logged_commands(bot, logged: list[LoggedCommand], *, remap_ab: bool = False) -> None:
    """Append accepted LoggedCommands onto bot queues with stable provenance (P0-4)."""
    if bot is None:
        return
    if not hasattr(bot, "logged_command_queue"):
        bot.logged_command_queue = []
    for cmd in logged:
        raw = cmd.raw_command
        if remap_ab:

            def replace(match):
                char, num = match.group(1), match.group(2)
                return "B" + num if char == "A" else "A" + num

            raw = re.sub(r"(A|B)(\d+)", replace, raw)
            cmd = LoggedCommand.from_raw(
                raw=raw,
                command_id=cmd.command_id,
                command_batch_id=cmd.command_batch_id,
                llm_call_id=cmd.llm_call_id,
                run_id=cmd.run_id,
                agent_id=cmd.agent_id,
                agent_side=cmd.agent_side,
                player_id=cmd.player_id,
                channel=cmd.channel,
                source=cmd.source,
                race=cmd.race,
            )
        if len(cmd.parts) == 3:
            bot.parsed_commands.append(list(cmd.parts))
            bot.logged_command_queue.append(cmd)


def log_attack_decision_commands(logger, *, llm_call_id: str | None, output: str, bot, decided_attack: bool) -> None:
    if logger is None or not llm_call_id:
        return
    clock = bot_clock(bot)
    cid = str(uuid.uuid4())
    batch = str(uuid.uuid4())
    raw = "ATTACK_ENEMY_START" if decided_attack else "RETREAT_TO_BASE"
    identity = {
        "agent_id": getattr(bot, "agent_id", None),
        "agent_side": getattr(bot, "agent_side", getattr(bot, "agent_id", None)),
        "player_id": getattr(bot, "player_id", None),
        "race": "Zerg",
    }
    for et, status in (
        ("command_proposed", "proposed"),
        ("command_accepted", "accepted"),
        ("command_dispatched", "dispatched"),
        ("command_succeeded", "succeeded"),
    ):
        logger.write_command_event(
            et,
            command_id=cid,
            llm_call_id=llm_call_id,
            command_batch_id=batch,
            channel="attack",
            raw_command=raw,
            status=status,
            source="llm",
            parsed_command={"action": raw, "raw_response_has_true": "True" in (output or "")},
            **identity,
            **clock,
        )


def pop_logged_for_parts(bot, parts: list[str]) -> LoggedCommand | None:
    queue = getattr(bot, "logged_command_queue", None)
    if not queue:
        return None
    key = tuple(parts)
    for i, cmd in enumerate(queue):
        if tuple(cmd.parts) == key:
            return queue.pop(i)
    return None


def log_dispatched_logged_commands(logger, bot, consumed: list[list[str]], *, channel: str = "macro") -> None:
    """Dispatch using LoggedCommand provenance; never invent orphan llm dispatches (P0-4/P0-5)."""
    if logger is None:
        return
    clock = bot_clock(bot)
    for parts in consumed:
        if len(parts) != 3:
            continue
        cmd = pop_logged_for_parts(bot, parts)
        if cmd is None:
            # Opening / rule leftovers without provenance → system, not llm
            raw = f"({parts[0]})->({parts[1]})->({parts[2]})"
            cid = str(uuid.uuid4())
            batch = str(uuid.uuid4())
            for et, status in (
                ("command_proposed", "proposed"),
                ("command_accepted", "accepted"),
                ("command_dispatched", "dispatched"),
            ):
                logger.write_command_event(
                    et,
                    command_id=cid,
                    llm_call_id=None,
                    command_batch_id=batch,
                    channel=channel,
                    raw_command=raw,
                    status=status,
                    source="system",
                    parsed_command=parse_command_parts(raw),
                    agent_id=getattr(bot, "agent_id", None),
                    agent_side=getattr(bot, "agent_side", getattr(bot, "agent_id", None)),
                    player_id=getattr(bot, "player_id", None),
                    race="Zerg",
                    note="system_queue_without_llm_provenance",
                    **clock,
                )
            continue
        logger.log_command_terminal(cmd, "command_dispatched", **clock)
        # Framework can only confirm submit → keep dispatched; also mark succeeded as "submitted"
        logger.log_command_terminal(cmd, "command_succeeded", reason_code="api_submitted", **clock)


# Back-compat alias
def log_dispatched_parsed_commands(logger, bot, commands: list[list[str]], *, channel: str = "macro") -> None:
    log_dispatched_logged_commands(logger, bot, commands, channel=channel)
