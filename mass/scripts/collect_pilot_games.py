#!/usr/bin/env python3
"""Collect qualified pilot games until target count is reached."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "logs" / "runs"
STATE = ROOT / "docs" / "replay" / "pilot_collection_state.json"
QUAR = ROOT / "docs" / "replay" / "pilot_quarantine"


def _load_state() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"qualified": [], "quarantine": [], "attempts": []}


def _save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _latest_run_id(before: set[str]) -> str | None:
    if not RUNS.is_dir():
        return None
    dirs = sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs:
        if d.is_dir() and d.name not in before:
            return d.name
    return None


def _validate(run_id: str) -> tuple[int, dict]:
    summary = ROOT / "docs" / "replay" / f"pilot_validate_{run_id}.json"
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
    return rc, data


def main() -> int:
    target = int(os.getenv("SWARMBRAIN_PILOT_TARGET", "5"))
    st = _load_state()
    # seed known S2 winner if present
    s2 = "20260721T095812Z_fe2fb72f"
    if s2 not in st["qualified"] and (RUNS / s2).is_dir():
        st["qualified"].append(s2)
        _save_state(st)

    while len(st["qualified"]) < target:
        print(f"[collect] qualified={len(st['qualified'])}/{target}", flush=True)
        before = {p.name for p in RUNS.iterdir()} if RUNS.is_dir() else set()
        before |= set(st["qualified"]) | {x.get("run_id") for x in st["quarantine"] if isinstance(x, dict)}
        env = os.environ.copy()
        env["SWARMBRAIN_PILOT"] = "1"
        # Ensure unique seed each attempt
        env["SWARMBRAIN_RANDOM_SEED"] = os.urandom(8).hex()
        t0 = time.time()
        rc_game = subprocess.call([sys.executable, str(ROOT / "scripts" / "run_pilot_game.py")], cwd=str(ROOT), env=env)
        run_id = _latest_run_id(before)
        attempt = {
            "run_id": run_id,
            "game_exit": rc_game,
            "elapsed_sec": round(time.time() - t0, 1),
            "seed": env["SWARMBRAIN_RANDOM_SEED"],
        }
        if not run_id:
            attempt["exclude_reason"] = "no_new_run_dir"
            st["attempts"].append(attempt)
            _save_state(st)
            print("[collect] no new run dir; abort")
            return 1
        # Prefer completed validation; crashed runs go quarantine
        mpath = RUNS / run_id / "run_manifest.json"
        status = None
        if mpath.is_file():
            status = json.loads(mpath.read_text(encoding="utf-8")).get("status")
        if status != "completed":
            attempt["exclude_reason"] = f"status={status}"
            st["quarantine"].append(attempt)
            st["attempts"].append(attempt)
            QUAR.mkdir(parents=True, exist_ok=True)
            (QUAR / f"{run_id}.json").write_text(json.dumps(attempt, indent=2), encoding="utf-8")
            _save_state(st)
            print(f"[collect] quarantine {run_id}: {attempt['exclude_reason']}")
            continue
        vrc, summary = _validate(run_id)
        attempt["validation_status"] = summary.get("validation_status")
        attempt["failed_rules"] = summary.get("failed_rules")
        if vrc == 0 and summary.get("validation_status") == "pass":
            # dedupe by replay sha
            sha = summary.get("replay_sha256")
            existing_shas = set()
            for qid in st["qualified"]:
                qm = RUNS / qid / "run_manifest.json"
                if qm.is_file():
                    existing_shas.add((json.loads(qm.read_text(encoding="utf-8")).get("replay") or {}).get("sha256"))
            if sha and sha in existing_shas:
                attempt["exclude_reason"] = "duplicate_replay_sha256"
                st["quarantine"].append(attempt)
            else:
                st["qualified"].append(run_id)
                attempt["qualified"] = True
                print(f"[collect] QUALIFIED {run_id}")
        else:
            attempt["exclude_reason"] = "validator_failed"
            st["quarantine"].append(attempt)
            QUAR.mkdir(parents=True, exist_ok=True)
            (QUAR / f"{run_id}.json").write_text(json.dumps(attempt, indent=2), encoding="utf-8")
            print(f"[collect] quarantine {run_id}: {attempt.get('failed_rules')}")
        st["attempts"].append(attempt)
        _save_state(st)

    print("[collect] done:", st["qualified"])
    # build delivery
    ids = ",".join(st["qualified"][:target])
    return subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "build_pilot_delivery.py"), "--run-ids", ids],
        cwd=str(ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
