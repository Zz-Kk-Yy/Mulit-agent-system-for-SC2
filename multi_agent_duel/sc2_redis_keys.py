"""Redis key helpers for dual-agent duel mode (SC2_AGENT_SIDE=red|blue)."""
from __future__ import annotations

import os


def normalize_side(side: str | None = None) -> str:
    value = (side if side is not None else os.getenv("SC2_AGENT_SIDE", "")).strip().lower()
    return value if value in {"red", "blue"} else ""


def _suffix(side: str | None = None) -> str:
    normalized = normalize_side(side)
    return f":{normalized}" if normalized else ""


def situation_key(side: str | None = None) -> str:
    return f"situation:current{_suffix(side)}"


def game_status_key(side: str | None = None) -> str:
    return f"game:status{_suffix(side)}"


def commands_key(side: str | None = None) -> str:
    return f"commands:pending{_suffix(side)}"


def layer_key(layer_name: str, side: str | None = None) -> str:
    return f"situation:layer:{layer_name}{_suffix(side)}"


def trend_anchor_key(side: str | None = None) -> str:
    return f"situation:trend_anchor{_suffix(side)}"
