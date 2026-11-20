#!/usr/bin/env python3
"""Collect balanced-channel pilot runs (task v0.1).

Hard gates (docs/replay/swarmbrain_balanced_channel_collection_task_v0.1.md §3):
- status=completed, natural end
- exactly one Victory and one Defeat (runtime)
- runtime_result matches replay_result
- validator pass (pilot) + strategic_plan audit
- replay_final_game_loop >= 10752 (~480s)
- new run_ids only; never mutate old delivery trees
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarmbrain_replay_meta import parse_replay_file  # noqa: E402

RUNS = ROOT / "logs" / "runs"
STATE = ROOT / "docs" / "replay" / "balanced_channel_collection_state_v0.1.json"
OUT = ROOT / "docs" / "replay" / "strategic_plan_delivery_balanced_v0.1"
QUAR = ROOT / "docs" / "replay" / "balanced_channel_quarantine_v0.1"

MIN_REPLAY_LOOP = int(os.getenv("SWARMBRAIN_MIN_REPLAY_LOOP", "10752"))  # 480s * 22.4
TARGET_DEFAULT = 12


def _load() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8-sig"))
    return {
        "qualified": [],
        "quarantine": [],
        "attempts": [],
        "target": TARGET_DEFAULT,
        "task": "balanced_channel_collection_v0.1",
    }


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _latest(before: set[str]) -> str | None:
    if not RUNS.is_dir():
        return None
    for d in sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir() and d.name not in before:
            return d.name
    return None


def _norm_res(v: object) -> str:
    s = str(v).lower()
    for a, b in (("result.", ""), ("victory", "win"), ("defeat", "loss")):
        s = s.replace(a, b)
    return s.strip()


def _outcome_pair(result: object) -> tuple[str, str] | None:
    if not isinstance(result, dict) or len(result) < 2:
        return None
    vals = [_norm_res(result[k]) for k in sorted(result.keys())]
    if len(vals) < 2:
        return None
    return vals[0], vals[1]


def _exactly_one_win_one_loss(result: object) -> bool:
    pair = _outcome_pair(result)
    if not pair:
        return False
    a, b = pair
    return {a, b} == {"win", "loss"}


def _results_equiv(a: object, b: object) -> bool:
    if a == b:
        return True
    if not isinstance(a, dict) or not isinstance(b, dict):
        return _norm_res(a) == _norm_res(b)
    keys = set(a) | set(b)
    if not keys:
        return False
    # Compare by player index keys when possible
    if set(a.keys()) == set(b.keys()):
        return all(_norm_res(a[k]) == _norm_res(b[k]) for k in a)
    # Fall back to multiset of outcomes
    return sorted(_norm_res(v) for v in a.values()) == sorted(_norm_res(v) for v in b.values())


def _replay_path(run_id: str, manifest: dict) -> Path | None:
    replay = manifest.get("replay") or {}
    path = replay.get("path") or replay.get("file_path")
    if path and Path(path).is_file():
        return Path(path)
    hits = list((RUNS / run_id).glob("*.SC2Replay"))
    return hits[0] if hits else None


def _hard_gates(run_id: str, manifest: dict) -> tuple[bool, str | None, dict]:
    if manifest.get("status") != "completed":
        return False, f"status={manifest.get('status')}", {}
    term = str(manifest.get("termination_reason") or "")
    if "test_time_limit" in term:
        return False, f"test_time_limit:{term}", {}

    runtime = manifest.get("runtime_result") or manifest.get("result") or {}
    replay_res = manifest.get("replay_result") or {}
    if not _exactly_one_win_one_loss(runtime):
        return False, f"runtime_not_exact_win_loss:{runtime}", {}
    if replay_res and not _exactly_one_win_one_loss(replay_res):
        return False, f"replay_not_exact_win_loss:{replay_res}", {}
    if replay_res and not _results_equiv(runtime, replay_res):
        return False, f"runtime_replay_mismatch:runtime={runtime};replay={replay_res}", {}

    path = _replay_path(run_id, manifest)
    if path is None:
        return False, "replay_file_missing", {}
    meta = parse_replay_file(path)
    if meta.get("parse_status") != "ok":
        return False, f"replay_parse_failed:{meta.get('parse_error')}", meta
    loop = int(meta.get("replay_final_game_loop") or 0)
    if loop < MIN_REPLAY_LOOP:
        return False, f"replay_loop_lt_{MIN_REPLAY_LOOP}:{loop}", meta
    # Prefer header players when present for consistency
    header_res = meta.get("player_results") or {}
    if header_res and not _exactly_one_win_one_loss(header_res):
        return False, f"header_not_exact_win_loss:{header_res}", meta
    if header_res and not _results_equiv(runtime, header_res):
        return False, f"runtime_header_mismatch:runtime={runtime};header={header_res}", meta
    return True, None, meta


def _validate(run_id: str) -> tuple[int, dict]:
    summary = ROOT / "docs" / "replay" / f"balanced_validate_{run_id}.json"
    rc = subprocess.call(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_run_logs.py"),
            "--run-dir",
            str(RUNS / run_id),
            "--expect-status",
            "completed",
            "--pilot",
            "--summary-json",
            str(summary),
        ],
        cwd=str(ROOT),
    )
    data = json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else {}
    arc = subprocess.call(
        [
            sys.executable,
            str(ROOT / "scripts" / "audit_strategic_plan.py"),
            "--run-dir",
            str(RUNS / run_id),
            "--min-building",
            "1",
        ],
        cwd=str(ROOT),
    )
    data["strategic_audit_exit"] = arc
    return rc, data


def _quarantine(st: dict, attempt: dict) -> None:
    st.setdefault("quarantine", []).append(attempt)
    st.setdefault("attempts", []).append(attempt)
    QUAR.mkdir(parents=True, exist_ok=True)
    rid = attempt.get("run_id") or "unknown"
    (QUAR / f"{rid}.json").write_text(json.dumps(attempt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[balanced-collect] quarantine {rid}: {attempt.get('exclude_reason')}", flush=True)
    _save(st)


def main() -> int:
    target = int(os.getenv("SWARMBRAIN_BALANCED_TARGET", str(TARGET_DEFAULT)))
    st = _load()
    st["target"] = target
    st["task"] = "balanced_channel_collection_v0.1"
    _save(st)

    while len(st["qualified"]) < target:
        print(f"[balanced-collect] qualified={len(st['qualified'])}/{target}", flush=True)
        before = {p.name for p in RUNS.iterdir()} if RUNS.is_dir() else set()
        before |= set(st["qualified"])
        env = os.environ.copy()
        env["SWARMBRAIN_PILOT"] = "1"
        env["SWARMBRAIN_RANDOM_SEED"] = os.urandom(8).hex()
        # Ensure smoke overrides are not present
        for k in ("SWARMBRAIN_ATTACK_EVERY", "SWARMBRAIN_ATTACK_MIN_ARMY", "SWARMBRAIN_MAX_GAME_TIME_SEC"):
            env.pop(k, None)
        t0 = time.time()
        rc_game = subprocess.call(
            [sys.executable, str(ROOT / "scripts" / "run_pilot_game.py")],
            cwd=str(ROOT),
            env=env,
        )
        run_id = _latest(before)
        attempt: dict = {
            "run_id": run_id,
            "game_exit": rc_game,
            "elapsed_sec": round(time.time() - t0, 1),
            "seed": env["SWARMBRAIN_RANDOM_SEED"],
        }
        if not run_id:
            attempt["exclude_reason"] = "no_new_run_dir"
            st["attempts"].append(attempt)
            _save(st)
            return 1

        m = json.loads((RUNS / run_id / "run_manifest.json").read_text(encoding="utf-8"))
        ok_gate, reason, meta = _hard_gates(run_id, m)
        attempt["replay_final_game_loop"] = meta.get("replay_final_game_loop")
        attempt["replay_duration_sec"] = meta.get("replay_duration_sec")
        attempt["runtime_result"] = m.get("runtime_result") or m.get("result")
        attempt["replay_result"] = m.get("replay_result")
        if not ok_gate:
            attempt["exclude_reason"] = reason
            _quarantine(st, attempt)
            continue

        vrc, summary = _validate(run_id)
        attempt["validation_status"] = summary.get("validation_status")
        attempt["failed_rules"] = summary.get("failed_rules")
        attempt["warnings"] = summary.get("warnings")
        attempt["strategic_audit_exit"] = summary.get("strategic_audit_exit")
        ok = (
            vrc == 0
            and summary.get("validation_status") == "pass"
            and summary.get("strategic_audit_exit") == 0
        )
        # Also reject dual-victory warnings if any slipped through
        warns = " | ".join(summary.get("warnings") or [])
        if "dual Victory" in warns or "runtime_result != replay_result" in warns:
            ok = False
            attempt["exclude_reason"] = "validator_warning_hard_reject"
            _quarantine(st, attempt)
            continue

        if not ok:
            attempt["exclude_reason"] = "validator_or_audit_failed"
            _quarantine(st, attempt)
            continue

        sha = summary.get("replay_sha256") or (meta.get("sha256"))
        existing = set()
        for qid in st["qualified"]:
            qm = RUNS / qid / "run_manifest.json"
            if qm.is_file():
                existing.add((json.loads(qm.read_text(encoding="utf-8")).get("replay") or {}).get("sha256"))
        if sha and sha in existing:
            attempt["exclude_reason"] = "duplicate_replay_sha256"
            _quarantine(st, attempt)
            continue

        st["qualified"].append(run_id)
        attempt["qualified"] = True
        st["attempts"].append(attempt)
        _save(st)
        print(f"[balanced-collect] QUALIFIED {run_id} loop={meta.get('replay_final_game_loop')}", flush=True)

    # Build delivery
    return subprocess.call(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_balanced_delivery.py"),
            "--run-ids",
            ",".join(st["qualified"][:target]),
            "--out-dir",
            str(OUT),
        ],
        cwd=str(ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
