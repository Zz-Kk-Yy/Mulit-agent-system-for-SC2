#!/usr/bin/env python3
"""Audit strategic_plan fields on a completed run (smoke / formal acceptance)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarmbrain_strategic_plan import (  # noqa: E402
    PRIMARY_INTENTS,
    PARSE_STATUS_VALUES,
    SOURCE_VALUES,
    STRATEGIC_PLAN_SCHEMA_VERSION,
)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--min-building", type=int, default=20, help="Warn if fewer building completed calls")
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    llm = load_jsonl(run_dir / "llm_events.jsonl")
    completed = [r for r in llm if r.get("event_type") == "llm_call_completed"]
    ingame = [
        r
        for r in completed
        if r.get("channel") in {"macro", "building", "attack"}
        and (r.get("feature_scope") or "label_only") == "label_only"
    ]
    fails: list[str] = []
    with_sp = 0
    intents: Counter[str] = Counter()
    building_intents: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    parse_st: Counter[str] = Counter()

    for r in ingame:
        if r.get("feature_scope") != "label_only":
            fails.append(f"feature_scope!={r.get('feature_scope')} call={r.get('llm_call_id')}")
        if not r.get("channel"):
            fails.append(f"missing channel call={r.get('llm_call_id')}")
        sp = r.get("strategic_plan")
        if not isinstance(sp, dict):
            fails.append(f"missing strategic_plan {r.get('llm_call_id')}")
            continue
        with_sp += 1
        pi = sp.get("primary_intent")
        intents[str(pi)] += 1
        sources[str(sp.get("source"))] += 1
        parse_st[str(sp.get("parse_status"))] += 1
        if r.get("channel") == "building":
            building_intents[str(pi)] += 1
        if sp.get("schema_version") != STRATEGIC_PLAN_SCHEMA_VERSION:
            fails.append(f"bad schema_version {r.get('llm_call_id')}")
        if pi not in PRIMARY_INTENTS:
            fails.append(f"illegal primary_intent={pi!r} {r.get('llm_call_id')}")
        if pi in {"macro", "building", "attack"}:
            fails.append(f"channel-as-intent {pi} {r.get('llm_call_id')}")
        if sp.get("source") not in SOURCE_VALUES:
            fails.append(f"bad source {r.get('llm_call_id')}")
        if sp.get("parse_status") not in PARSE_STATUS_VALUES:
            fails.append(f"bad parse_status {r.get('llm_call_id')}")

    n = len(ingame)
    cov = (with_sp / n) if n else 0.0
    print(f"run_dir={run_dir}")
    print(f"ingame_completed={n} with_strategic_plan={with_sp} coverage={cov:.2%}")
    print(f"primary_intent_counts={dict(intents)}")
    print(f"building_primary_intent_counts={dict(building_intents)} (n={sum(building_intents.values())})")
    print(f"source_counts={dict(sources)}")
    print(f"parse_status_counts={dict(parse_st)}")

    if n == 0:
        fails.append("no in-game label_only completed calls")
    if cov < 0.95:
        fails.append(f"coverage {cov:.2%} < 95%")

    b_n = sum(building_intents.values())
    if b_n < args.min_building:
        print(f"[WARN] building completed calls={b_n} < {args.min_building} (spot-check sample thin)")
    if b_n > 0:
        tech = building_intents.get("tech_climb", 0)
        amb = building_intents.get("ambiguous", 0)
        tech_ratio = tech / b_n
        print(f"building tech_climb_ratio={tech_ratio:.2%} ambiguous={amb}")
        if tech_ratio >= 0.9 and b_n >= 10:
            fails.append(f"building intents collapsed to tech_climb ({tech}/{b_n})")
        if amb == 0 and b_n >= args.min_building:
            print("[WARN] no building primary_intent=ambiguous in sample; manual review recommended")

    if fails:
        print("FAILED:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS strategic_plan smoke audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
