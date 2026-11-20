#!/usr/bin/env python3
"""Collect formal strategic_plan runs (reuse pilot collector mechanics)."""
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
STATE = ROOT / "docs" / "replay" / "strategic_plan_collection_state.json"
OUT = ROOT / "docs" / "replay" / "strategic_plan_delivery"
QUAR = ROOT / "docs" / "replay" / "strategic_plan_quarantine"

MIN_REPLAY_SEC = float(os.getenv("SWARMBRAIN_MIN_REPLAY_SEC", "480"))


def _load() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8-sig"))
    return {"qualified": [], "quarantine": [], "attempts": [], "target": 4}


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


def _runtime_values(manifest: dict) -> list[str]:
    runtime = manifest.get("runtime_result") or manifest.get("result") or {}
    if isinstance(runtime, dict):
        return [str(v) for v in runtime.values()]
    return [str(runtime)]


def _is_dual_victory(manifest: dict) -> bool:
    vals = _runtime_values(manifest)
    n = sum(1 for v in vals if ("Victory" in v) or (v in {"Win", "Result.Win"}))
    return n >= 2


def _replay_gate(run_id: str, manifest: dict) -> tuple[bool, str | None, dict]:
    """Require parseable replay with duration >= MIN_REPLAY_SEC."""
    replay = manifest.get("replay") or {}
    path = replay.get("path") or replay.get("file_path")
    if not path:
        # fall back to run dir scan
        hits = list((RUNS / run_id).glob("*.SC2Replay"))
        if not hits:
            return False, "replay_file_missing", {}
        path = str(hits[0])
    meta = parse_replay_file(path)
    if meta.get("parse_status") != "ok":
        return False, f"replay_parse_failed:{meta.get('parse_error')}", meta
    dur = float(meta.get("replay_duration_sec") or 0.0)
    if dur < MIN_REPLAY_SEC:
        return False, f"replay_duration_lt_{int(MIN_REPLAY_SEC)}:{dur:.1f}", meta
    return True, None, meta


def _validate(run_id: str) -> tuple[int, dict]:
    summary = ROOT / "docs" / "replay" / f"strategic_plan_validate_{run_id}.json"
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
    st["quarantine"].append(attempt)
    st["attempts"].append(attempt)
    QUAR.mkdir(parents=True, exist_ok=True)
    rid = attempt.get("run_id") or "unknown"
    (QUAR / f"{rid}.json").write_text(json.dumps(attempt, indent=2), encoding="utf-8")
    print(f"[strategic-collect] quarantine {rid}: {attempt.get('exclude_reason')}", flush=True)
    _save(st)


def main() -> int:
    target = int(os.getenv("SWARMBRAIN_STRATEGIC_TARGET", "4"))
    st = _load()
    st["target"] = target
    st.setdefault("replacements", [])
    while len(st["qualified"]) < target:
        print(f"[strategic-collect] qualified={len(st['qualified'])}/{target}", flush=True)
        before = {p.name for p in RUNS.iterdir()} if RUNS.is_dir() else set()
        before |= set(st["qualified"])
        env = os.environ.copy()
        env["SWARMBRAIN_PILOT"] = "1"
        env["SWARMBRAIN_RANDOM_SEED"] = os.urandom(8).hex()
        t0 = time.time()
        rc_game = subprocess.call(
            [sys.executable, str(ROOT / "scripts" / "run_pilot_game.py")],
            cwd=str(ROOT),
            env=env,
        )
        run_id = _latest(before)
        attempt = {
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
        if m.get("status") != "completed":
            attempt["exclude_reason"] = f"status={m.get('status')}"
            _quarantine(st, attempt)
            continue
        if _is_dual_victory(m):
            attempt["exclude_reason"] = "dual_victory_runtime"
            attempt["runtime_result"] = m.get("runtime_result") or m.get("result")
            _quarantine(st, attempt)
            continue
        ok_replay, replay_reason, replay_meta = _replay_gate(run_id, m)
        attempt["replay_duration_sec"] = replay_meta.get("replay_duration_sec")
        attempt["replay_parse_status"] = replay_meta.get("parse_status")
        if not ok_replay:
            attempt["exclude_reason"] = replay_reason
            _quarantine(st, attempt)
            continue
        vrc, summary = _validate(run_id)
        attempt["validation_status"] = summary.get("validation_status")
        attempt["failed_rules"] = summary.get("failed_rules")
        attempt["strategic_audit_exit"] = summary.get("strategic_audit_exit")
        ok = (
            vrc == 0
            and summary.get("validation_status") == "pass"
            and summary.get("strategic_audit_exit") == 0
        )
        if ok:
            sha = summary.get("replay_sha256")
            existing = set()
            for qid in st["qualified"]:
                qm = RUNS / qid / "run_manifest.json"
                if qm.is_file():
                    existing.add(
                        (json.loads(qm.read_text(encoding="utf-8")).get("replay") or {}).get("sha256")
                    )
            if sha and sha in existing:
                attempt["exclude_reason"] = "duplicate_replay_sha256"
                _quarantine(st, attempt)
            else:
                st["qualified"].append(run_id)
                attempt["qualified"] = True
                if st.get("replacement_mode"):
                    st.setdefault("replacements", []).append(run_id)
                st["attempts"].append(attempt)
                _save(st)
                print(f"[strategic-collect] QUALIFIED {run_id}", flush=True)
        else:
            attempt["exclude_reason"] = "validator_or_audit_failed"
            _quarantine(st, attempt)

    ids = ",".join(st["qualified"][:target])
    return subprocess.call(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_pilot_delivery.py"),
            "--run-ids",
            ids,
            "--out-dir",
            str(OUT),
        ],
        cwd=str(ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
