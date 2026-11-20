"""Load and apply frozen pilot dual-LLM collection configs."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_PILOT_CONFIG = ROOT_DIR / "configs" / "pilot_dual_llm_v1.yaml"

# Smoke knobs that must never appear in a qualified pilot run.
_SMOKE_FORBIDDEN = {
    "attack_min_army": {0, "0"},
    "attack_every": {40, "40"},
    "max_game_time_sec": {420, "420", 420.0},
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def config_file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def load_pilot_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else Path(
        os.getenv("SWARMBRAIN_PILOT_CONFIG_PATH") or DEFAULT_PILOT_CONFIG
    )
    if not cfg_path.is_file():
        raise FileNotFoundError(f"pilot config not found: {cfg_path}")
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load pilot configs") from exc
    raw_text = cfg_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"pilot config must be a mapping: {cfg_path}")
    data = dict(data)
    data["_config_path"] = str(cfg_path.resolve())
    data["_config_sha256"] = _sha256_bytes(raw_text.encode("utf-8"))
    data["_raw_text"] = raw_text
    return data


def assert_not_smoke_config(cfg: dict[str, Any]) -> None:
    """Raise if known smoke-test knobs are present."""
    problems: list[str] = []
    amin = cfg.get("attack_min_army")
    if amin in _SMOKE_FORBIDDEN["attack_min_army"]:
        problems.append("attack_min_army=0 is smoke-only")
    aevery = cfg.get("attack_every")
    if aevery in _SMOKE_FORBIDDEN["attack_every"]:
        problems.append("attack_every=40 is smoke-only")
    mgt = cfg.get("max_game_time_sec")
    if mgt in _SMOKE_FORBIDDEN["max_game_time_sec"]:
        problems.append("max_game_time_sec=420 is smoke-only")
    if problems:
        raise ValueError("pilot config rejected: " + "; ".join(problems))


def resolve_random_seed(cfg: dict[str, Any]) -> tuple[str | None, str]:
    """Return (seed, strategy_note)."""
    env_seed = os.getenv("SWARMBRAIN_RANDOM_SEED", "").strip()
    if env_seed:
        return env_seed, "from_env_SWARMBRAIN_RANDOM_SEED"
    cfg_seed = cfg.get("random_seed")
    if cfg_seed is not None and str(cfg_seed).strip() != "":
        return str(cfg_seed), "from_config"
    strategy = str(cfg.get("random_seed_strategy") or "generate_per_run_if_unset")
    if strategy == "generate_per_run_if_unset":
        seed = secrets.token_hex(8)
        return seed, "generated_per_run"
    return None, "not_set"


def apply_pilot_config_to_env(cfg: dict[str, Any], *, override_existing: bool = True) -> dict[str, str]:
    """Map config fields onto SWARMBRAIN_* env vars used by launch."""
    assert_not_smoke_config(cfg)
    applied: dict[str, str] = {}

    def _set(key: str, value: Any) -> None:
        if value is None:
            return
        s = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if isinstance(value, bool):
            s = "1" if value else "0"
        if (not override_existing) and os.getenv(key):
            applied[key] = os.environ[key]
            return
        os.environ[key] = s
        applied[key] = s

    _set("SWARMBRAIN_REALTIME", "1" if cfg.get("realtime", True) else "0")
    _set("SWARMBRAIN_MACRO_EVERY", cfg.get("macro_every", 120))
    _set("SWARMBRAIN_BUILDING_EVERY", cfg.get("building_every", 60))
    _set("SWARMBRAIN_ATTACK_EVERY", cfg.get("attack_every", 150))
    _set("SWARMBRAIN_ATTACK_MIN_ARMY", cfg.get("attack_min_army", 28))
    mgt = cfg.get("max_game_time_sec")
    if mgt is not None and str(mgt).strip() != "":
        _set("SWARMBRAIN_MAX_GAME_TIME_SEC", mgt)
    else:
        # Ensure smoke leftovers do not linger when applying pilot.
        os.environ.pop("SWARMBRAIN_MAX_GAME_TIME_SEC", None)

    if cfg.get("disable_redis_bridge", True):
        _set("SWARMBRAIN_DISABLE_REDIS_BRIDGE", "1")

    model = cfg.get("model_name")
    if model:
        _set("SWARMBRAIN_LLM_MODEL", model)
    temp = cfg.get("temperature")
    if temp is not None:
        _set("SWARMBRAIN_LLM_TEMPERATURE", temp)

    seed, _ = resolve_random_seed(cfg)
    if seed:
        _set("SWARMBRAIN_RANDOM_SEED", seed)

    _set("SWARMBRAIN_PILOT_CONFIG_NAME", cfg.get("config_name") or "pilot_dual_llm_v1")
    _set("SWARMBRAIN_PILOT_CONFIG_VERSION", cfg.get("config_version") or "1.0.0")
    _set("SWARMBRAIN_PILOT_CONFIG_SHA256", cfg.get("_config_sha256") or "")
    _set("SWARMBRAIN_PROMPT_TEMPLATE_VERSION", cfg.get("prompt_template_version") or "swarmbrain_zvz_opening_ingame_v1")
    return applied


def build_manifest_config_fields(cfg: dict[str, Any], *, model_name: str | None = None) -> dict[str, Any]:
    seed, seed_strategy = resolve_random_seed(cfg)
    # Prefer env after apply
    seed = os.getenv("SWARMBRAIN_RANDOM_SEED") or seed
    runtime = {
        "realtime": bool(cfg.get("realtime", True)),
        "macro_every": int(cfg.get("macro_every", 120)),
        "building_every": int(cfg.get("building_every", 60)),
        "attack_every": int(cfg.get("attack_every", 150)),
        "attack_min_army": int(cfg.get("attack_min_army", 28)),
        "max_game_time_sec": cfg.get("max_game_time_sec"),
        "map_name": cfg.get("map_name") or "AutomatonLE",
        "player_1_race": cfg.get("player_1_race") or "Zerg",
        "player_2_race": cfg.get("player_2_race") or "Zerg",
        "fallback_strategy_version": cfg.get("fallback_strategy_version") or "none_v1",
        "disable_redis_bridge": bool(cfg.get("disable_redis_bridge", True)),
    }
    return {
        "config_name": cfg.get("config_name") or "pilot_dual_llm_v1",
        "config_version": cfg.get("config_version") or "1.0.0",
        "config_sha256": cfg.get("_config_sha256"),
        "config_path": cfg.get("_config_path"),
        "runtime_config": runtime,
        "prompt_template_version": cfg.get("prompt_template_version") or "swarmbrain_zvz_opening_ingame_v1",
        "model_provider": cfg.get("model_provider") or "openai_compatible",
        "model_name": model_name or cfg.get("model_name") or os.getenv("SWARMBRAIN_LLM_MODEL"),
        "random_seed": {
            "value": seed,
            "unavailable_reason": None if seed else "not_set",
            "strategy": seed_strategy,
        },
        "collection_kind": "pilot",
        "test_config": None,  # clear smoke test_config when pilot is active
    }


def is_pilot_mode_requested() -> bool:
    flag = os.getenv("SWARMBRAIN_PILOT", "").strip().lower()
    if flag in {"1", "true", "yes", "on", "pilot"}:
        return True
    if os.getenv("SWARMBRAIN_PILOT_CONFIG_PATH", "").strip():
        return True
    return False
