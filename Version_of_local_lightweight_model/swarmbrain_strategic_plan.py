"""Closed-set strategic_plan parsing for SwarmBrain LLM logs (v0.1.0)."""
from __future__ import annotations

import json
import re
from typing import Any

STRATEGIC_PLAN_SCHEMA_VERSION = "strategic_plan_v0.1.0"

PRIMARY_INTENTS = frozenset(
    {
        "early_rush",
        "fast_expand",
        "tech_climb",
        "air_harass_prep",
        "mid_timing_attack",
        "defensive_macro",
        "ambiguous",
        "unknown",
    }
)

# Must never be used as primary_intent (those are channels, not intents).
FORBIDDEN_CHANNEL_AS_INTENT = frozenset({"macro", "building", "attack", "opening", "other"})

SOURCE_VALUES = frozenset({"llm_structured", "llm_text_parsed", "rule_fallback"})
PARSE_STATUS_VALUES = frozenset({"success", "failed", "fallback"})

RATIONALE_MAX_CHARS = 200

STRATEGIC_PLAN_PROMPT_APPENDIX = """
---
STRATEGIC PLAN (required structured block; closed-set labels only)
After your analysis and commands, also output ONE JSON object with key "strategic_plan".
Do NOT use channel names (macro/building/attack) as primary_intent.

Allowed primary_intent / secondary_intents values ONLY:
early_rush, fast_expand, tech_climb, air_harass_prep, mid_timing_attack, defensive_macro, ambiguous, unknown

Rules:
- If multiple strategic goals are mixed and none dominates, set primary_intent to "ambiguous".
- Especially for building plans that mix expand + tech + air prep without one dominant goal, use "ambiguous" (do not force fast_expand or tech_climb).
- If evidence is insufficient, set primary_intent to "unknown".
- Prefer a scarce single-dominant label ONLY when commands clearly support it:
  tech_climb | air_harass_prep | defensive_macro | mid_timing_attack.
  Do NOT invent these labels without matching evidence; use ambiguous/unknown instead.
- secondary_intents may be [] ; must not repeat primary_intent.
- intent_confidence is a number in [0,1].
- intent_rationale_short <= 200 characters (audit only).
- horizon_sec is seconds into the future this plan targets, or null if unknown.
- Do NOT collapse every building plan to the same label.

Example (append after commands):
```json
{{
  "strategic_plan": {{
    "primary_intent": "tech_climb",
    "secondary_intents": ["fast_expand"],
    "intent_confidence": 0.6,
    "intent_rationale_short": "unlock den then take third",
    "horizon_sec": 90
  }}
}}
```
"""


def _clip_rationale(text: Any) -> str:
    s = "" if text is None else str(text).strip()
    if len(s) > RATIONALE_MAX_CHARS:
        return s[:RATIONALE_MAX_CHARS]
    return s


def _as_float_01(value: Any) -> float | None:
    try:
        x = float(value)
    except Exception:
        return None
    if x < 0.0 or x > 1.0:
        return None
    return x


def _as_horizon(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def make_failed_plan(
    *,
    reason: str,
    source: str = "llm_structured",
    fallback_used: bool = False,
) -> dict[str, Any]:
    src = "rule_fallback" if fallback_used else source
    if src not in SOURCE_VALUES:
        src = "llm_structured"
    conf = 0.2 if src == "rule_fallback" else 0.0
    return {
        "schema_version": STRATEGIC_PLAN_SCHEMA_VERSION,
        "primary_intent": "unknown",
        "secondary_intents": [],
        "intent_confidence": conf,
        "intent_rationale_short": _clip_rationale(reason) or "parse_failed",
        "horizon_sec": None,
        "source": src,
        "parse_status": "fallback" if fallback_used else "failed",
    }


def normalize_strategic_plan(
    raw: dict[str, Any] | None,
    *,
    source_hint: str | None = None,
    fallback_used: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """
    Validate/normalize a strategic_plan dict.
    Returns (plan, error_message). On illegal enum, returns failed plan (does not silently remap).
    """
    if not isinstance(raw, dict):
        return make_failed_plan(reason="missing_or_non_object", fallback_used=fallback_used), "missing_or_non_object"

    primary = raw.get("primary_intent")
    if not isinstance(primary, str) or primary not in PRIMARY_INTENTS:
        # Illegal enum: do NOT silently rewrite to a nearby class.
        reason = f"illegal_primary_intent:{primary!r}"
        plan = make_failed_plan(reason=reason, fallback_used=fallback_used)
        return plan, reason
    if primary in FORBIDDEN_CHANNEL_AS_INTENT:
        reason = f"channel_used_as_intent:{primary}"
        return make_failed_plan(reason=reason, fallback_used=fallback_used), reason

    secondary_raw = raw.get("secondary_intents", [])
    if secondary_raw is None:
        secondary_raw = []
    if not isinstance(secondary_raw, list):
        return make_failed_plan(reason="secondary_intents_not_list", fallback_used=fallback_used), "secondary_intents_not_list"

    secondary: list[str] = []
    for item in secondary_raw:
        if not isinstance(item, str) or item not in PRIMARY_INTENTS:
            reason = f"illegal_secondary_intent:{item!r}"
            return make_failed_plan(reason=reason, fallback_used=fallback_used), reason
        if item == primary:
            continue
        if item not in secondary:
            secondary.append(item)

    conf = _as_float_01(raw.get("intent_confidence"))
    if conf is None:
        return make_failed_plan(reason="bad_intent_confidence", fallback_used=fallback_used), "bad_intent_confidence"

    source = source_hint or raw.get("source") or "llm_structured"
    if fallback_used:
        source = "rule_fallback"
        conf = min(conf, 0.3)
    if source not in SOURCE_VALUES:
        return make_failed_plan(reason=f"illegal_source:{source!r}", fallback_used=fallback_used), f"illegal_source:{source!r}"

    parse_status = raw.get("parse_status") or "success"
    if fallback_used:
        parse_status = "fallback"
    if parse_status not in PARSE_STATUS_VALUES:
        parse_status = "success"

    rationale = raw.get("intent_rationale_short")
    if rationale is None or str(rationale).strip() == "":
        return make_failed_plan(reason="missing_rationale", fallback_used=fallback_used), "missing_rationale"

    horizon = raw.get("horizon_sec", None)
    if horizon is not None:
        horizon = _as_horizon(horizon)
        # If provided but not numeric, fail rather than invent.
        if horizon is None and raw.get("horizon_sec") is not None:
            return make_failed_plan(reason="bad_horizon_sec", fallback_used=fallback_used), "bad_horizon_sec"

    plan = {
        "schema_version": STRATEGIC_PLAN_SCHEMA_VERSION,
        "primary_intent": primary,
        "secondary_intents": secondary,
        "intent_confidence": conf,
        "intent_rationale_short": _clip_rationale(rationale),
        "horizon_sec": horizon,
        "source": source,
        "parse_status": parse_status,
    }
    return plan, None


def _extract_json_objects(text: str) -> list[Any]:
    """Best-effort extraction of JSON objects / fenced blocks from LLM text."""
    out: list[Any] = []
    if not text:
        return out
    # Fenced ```json ... ```
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE):
        try:
            out.append(json.loads(m.group(1)))
        except Exception:
            continue
    # Direct strategic_plan object substring search
    key = '"strategic_plan"'
    idx = 0
    while True:
        i = text.find(key, idx)
        if i < 0:
            break
        # walk back to nearest '{'
        start = text.rfind("{", 0, i)
        if start < 0:
            idx = i + len(key)
            continue
        depth = 0
        end = None
        for j in range(start, len(text)):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end:
            snippet = text[start:end]
            try:
                out.append(json.loads(snippet))
            except Exception:
                # try replacing single quotes lightly
                try:
                    out.append(json.loads(snippet.replace("'", '"')))
                except Exception:
                    pass
            idx = end
        else:
            idx = i + len(key)
    return out


def extract_strategic_plan_dict(text: str) -> dict[str, Any] | None:
    for obj in _extract_json_objects(text or ""):
        if not isinstance(obj, dict):
            continue
        if "strategic_plan" in obj and isinstance(obj["strategic_plan"], dict):
            return obj["strategic_plan"]
        # bare object that already looks like the plan
        if "primary_intent" in obj and "intent_confidence" in obj:
            return obj
    return None


def _command_motifs(commands: list[Any] | None) -> set[str]:
    text = " ".join(str(c) for c in (commands or [])).lower()
    motifs: set[str] = set()
    if any(k in text for k in ("hatchery", "extractor", "expand")):
        motifs.add("expand")
    if any(
        k in text
        for k in (
            "hydralisk den",
            "spire",
            "greater spire",
            "ultralisk cavern",
            "infestation pit",
            "evolution chamber",
            "lair",
            "hive",
            "baneling nest",
            "roach warren",
        )
    ):
        motifs.add("tech")
    if any(k in text for k in ("spire", "greater spire", "mutalisk", "corruptor", "brood lord")):
        motifs.add("air")
    if any(k in text for k in ("spine crawler", "spore crawler", "queen")):
        motifs.add("defend")
    return motifs


def reconcile_intent_with_commands(
    plan: dict[str, Any],
    *,
    channel: str | None,
    parsed_commands: list[Any] | None,
) -> dict[str, Any]:
    """
    If building/macro commands clearly mix multiple strategic motifs while the model
    picked a single non-ambiguous intent, mark ambiguous (evidence-based, not silent remap
    to another concrete class).
    """
    if not isinstance(plan, dict):
        return plan
    if plan.get("parse_status") != "success":
        return plan
    if plan.get("primary_intent") in {"ambiguous", "unknown"}:
        return plan
    if channel not in {"building", "macro"}:
        return plan
    motifs = _command_motifs(parsed_commands)
    if len(motifs) < 2:
        return plan
    out = dict(plan)
    prev = out.get("primary_intent")
    out["primary_intent"] = "ambiguous"
    # keep previous concrete label as secondary if valid
    secs = list(out.get("secondary_intents") or [])
    if isinstance(prev, str) and prev in PRIMARY_INTENTS and prev not in secs and prev != "ambiguous":
        secs.insert(0, prev)
    out["secondary_intents"] = secs
    out["source"] = "llm_text_parsed"
    out["intent_confidence"] = min(float(out.get("intent_confidence") or 0.5), 0.55)
    note = f"multi_motif:{','.join(sorted(motifs))};was:{prev}"
    rationale = str(out.get("intent_rationale_short") or "")
    out["intent_rationale_short"] = _clip_rationale(f"{note}; {rationale}" if rationale else note)
    return out


def parse_strategic_plan_from_response(
    raw_response: str | None,
    *,
    fallback_used: bool = False,
    channel: str | None = None,
    parsed_commands: list[Any] | None = None,
) -> dict[str, Any]:
    """
    Parse strategic_plan from LLM raw text for label_only in-game channels.
    Never invents a high-confidence intent on failure.
    """
    if fallback_used:
        plan, _ = normalize_strategic_plan(
            {
                "primary_intent": "unknown",
                "secondary_intents": [],
                "intent_confidence": 0.2,
                "intent_rationale_short": "rule_fallback_commands",
                "horizon_sec": None,
                "source": "rule_fallback",
                "parse_status": "fallback",
            },
            fallback_used=True,
        )
        return plan

    extracted = extract_strategic_plan_dict(raw_response or "")
    if extracted is None:
        return make_failed_plan(reason="strategic_plan_block_not_found", source="llm_structured")

    # Prefer structured path when a block is present.
    plan, err = normalize_strategic_plan(extracted, source_hint="llm_structured", fallback_used=False)
    if err:
        return plan
    # Guard: channel must not equal primary even if somehow slipped through.
    if channel and plan.get("primary_intent") == channel:
        return make_failed_plan(reason=f"channel_equals_primary:{channel}")
    return reconcile_intent_with_commands(plan, channel=channel, parsed_commands=parsed_commands)


def should_attach_strategic_plan(*, channel: str | None, feature_scope: str | None) -> bool:
    """Attach for in-game label_only planner channels only."""
    if feature_scope and feature_scope != "label_only":
        return False
    return channel in {"macro", "building", "attack"}
