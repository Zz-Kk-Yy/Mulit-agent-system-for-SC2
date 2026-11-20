#!/usr/bin/env python3
"""Collect pilot v0.3 replacement/coverage runs with dual-observer replay smoke."""
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
STATE = ROOT / "docs" / "replay" / "v03_next_collection_state.json"
OUT = ROOT / "docs" / "replay" / "strategic_plan_delivery_v03_next"
QUAR = ROOT / "docs" / "replay" / "v03_next_quarantine"
# Active cover config after paper intake: v1.3.0 targets mid/late macro·building
# and early attack naturally (no forced channel labels).
CONFIG = ROOT / "configs" / "pilot_dual_llm_v03_cover_v1_3.yaml"
MIN_REPLAY_LOOP = 10752
TARGET_DEFAULT = 12  # keep existing 4; continue for coverage gaps


def _load() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8-sig"))
    return {
        "qualified": [],
        "quarantine": [],
        "attempts": [],
        "target": TARGET_DEFAULT,
        "task": "swarmbrain_v03_next_collection_20260728",
        "excluded_old_runs": [
            "20260727T074148Z_ed48f9c5",
            "20260727T125458Z_ff4cb329",
            "20260727T132849Z_5d1cc263",
            "20260727T223412Z_b244d8b5",
        ],
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
    s = str(v).lower().replace("result.", "")
    return s.replace("victory", "win").replace("defeat", "loss").strip()


def _exact_win_loss(result: object) -> bool:
    if not isinstance(result, dict) or len(result) < 2:
        return False
    vals = sorted(_norm_res(v) for v in result.values())
    return vals == ["loss", "win"]


def _equiv(a: object, b: object) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return _norm_res(a) == _norm_res(b)
    if set(a.keys()) != set(b.keys()):
        return sorted(_norm_res(v) for v in a.values()) == sorted(_norm_res(v) for v in b.values())
    return all(_norm_res(a[k]) == _norm_res(b[k]) for k in a)


def _replay_path(run_id: str, manifest: dict) -> Path | None:
    p = (manifest.get("replay") or {}).get("path")
    if p and Path(p).is_file():
        return Path(p)
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
    if not _exact_win_loss(runtime):
        return False, f"runtime_not_exact_win_loss:{runtime}", {}
    if replay_res and not _exact_win_loss(replay_res):
        return False, f"replay_not_exact_win_loss:{replay_res}", {}
    if replay_res and not _equiv(runtime, replay_res):
        return False, f"runtime_replay_mismatch:{runtime}|{replay_res}", {}
    path = _replay_path(run_id, manifest)
    if path is None:
        return False, "replay_missing", {}
    meta = parse_replay_file(path)
    if meta.get("parse_status") != "ok":
        return False, f"replay_parse_failed:{meta.get('parse_error')}", meta
    loop = int(meta.get("replay_final_game_loop") or 0)
    if loop < MIN_REPLAY_LOOP:
        return False, f"replay_loop_lt_{MIN_REPLAY_LOOP}:{loop}", meta
    return True, None, meta


def _validate(run_id: str) -> tuple[int, dict]:
    summary = ROOT / "docs" / "replay" / f"v03_validate_{run_id}.json"
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


def _smoke(run_id: str, manifest: dict, meta: dict) -> dict:
    path = _replay_path(run_id, manifest)
    assert path is not None
    out_json = RUNS / run_id / "replay_smoke_summary.json"
    env = os.environ.copy()
    env["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    env.setdefault("SC2PATH", env.get("SC2PATH") or r"E:\star_carft\StarCraft II")
    env["SWARMBRAIN_SC2PATH"] = env["SC2PATH"]
    rc = subprocess.call(
        [
            sys.executable,
            str(ROOT / "scripts" / "replay_observer_smoke.py"),
            "--replay",
            str(path.resolve()),
            "--run-id",
            run_id,
            "--out-json",
            str(out_json),
            "--runtime-result-json",
            json.dumps(manifest.get("runtime_result") or manifest.get("result") or {}),
            "--replay-result-json",
            json.dumps(manifest.get("replay_result") or meta.get("player_results") or {}),
        ],
        cwd=str(ROOT),
        env=env,
    )
    data = json.loads(out_json.read_text(encoding="utf-8")) if out_json.is_file() else {"status": "fail"}
    data["smoke_exit"] = rc
    return data


def _quarantine(st: dict, attempt: dict) -> None:
    st.setdefault("quarantine", []).append(attempt)
    st.setdefault("attempts", []).append(attempt)
    QUAR.mkdir(parents=True, exist_ok=True)
    rid = attempt.get("run_id") or "unknown"
    (QUAR / f"{rid}.json").write_text(json.dumps(attempt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[v03-collect] quarantine {rid}: {attempt.get('exclude_reason')}", flush=True)
    _save(st)


def main() -> int:
    target = int(os.getenv("SWARMBRAIN_V03_TARGET", str(TARGET_DEFAULT)))
    st = _load()
    st["target"] = target
    _save(st)

    while len(st["qualified"]) < target:
        print(f"[v03-collect] qualified={len(st['qualified'])}/{target}", flush=True)
        before = {p.name for p in RUNS.iterdir()} if RUNS.is_dir() else set()
        before |= set(st["qualified"])
        env = os.environ.copy()
        env["SWARMBRAIN_PILOT"] = "1"
        env["SWARMBRAIN_PILOT_CONFIG_PATH"] = str(CONFIG)
        env["SWARMBRAIN_RANDOM_SEED"] = os.urandom(8).hex()
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
            "config": CONFIG.name,
        }
        if not run_id:
            attempt["exclude_reason"] = "no_new_run_dir"
            st["attempts"].append(attempt)
            _save(st)
            return 1

        m = json.loads((RUNS / run_id / "run_manifest.json").read_text(encoding="utf-8"))
        ok_gate, reason, meta = _hard_gates(run_id, m)
        attempt["runtime_result"] = m.get("runtime_result") or m.get("result")
        attempt["replay_result"] = m.get("replay_result")
        attempt["replay_final_game_loop"] = meta.get("replay_final_game_loop")
        if not ok_gate:
            attempt["exclude_reason"] = reason
            _quarantine(st, attempt)
            continue

        vrc, summary = _validate(run_id)
        attempt["validation_status"] = summary.get("validation_status")
        attempt["failed_rules"] = summary.get("failed_rules")
        attempt["strategic_audit_exit"] = summary.get("strategic_audit_exit")
        if not (
            vrc == 0
            and summary.get("validation_status") == "pass"
            and summary.get("strategic_audit_exit") == 0
        ):
            attempt["exclude_reason"] = "validator_or_audit_failed"
            _quarantine(st, attempt)
            continue

        print(f"[v03-collect] dual-observer smoke {run_id} ...", flush=True)
        smoke = _smoke(run_id, m, meta)
        attempt["replay_smoke_status"] = smoke.get("status")
        attempt["observer_1_last_game_loop"] = smoke.get("observer_1_last_game_loop")
        attempt["observer_2_last_game_loop"] = smoke.get("observer_2_last_game_loop")
        if smoke.get("status") != "pass":
            attempt["exclude_reason"] = f"replay_smoke_failed:{smoke.get('early_stall_detected')}"
            attempt["replay_smoke"] = smoke
            _quarantine(st, attempt)
            continue

        sha = summary.get("replay_sha256") or meta.get("sha256")
        existing = set()
        for qid in st["qualified"]:
            qm = RUNS / qid / "run_manifest.json"
            if qm.is_file():
                existing.add((json.loads(qm.read_text(encoding="utf-8")).get("replay") or {}).get("sha256"))
        if sha and sha in existing:
            attempt["exclude_reason"] = "duplicate_replay_sha256"
            _quarantine(st, attempt)
            continue

        # Persist validator summary into run dir for delivery packaging.
        vs_src = ROOT / "docs" / "replay" / f"v03_validate_{run_id}.json"
        vs_dst = RUNS / run_id / "validation_summary.json"
        if vs_src.is_file():
            vs_dst.write_text(vs_src.read_text(encoding="utf-8"), encoding="utf-8")
        err_path = RUNS / run_id / "errors.jsonl"
        if not err_path.is_file():
            err_path.write_text("", encoding="utf-8")

        st["qualified"].append(run_id)
        attempt["qualified"] = True
        st["attempts"].append(attempt)
        _save(st)
        print(
            f"[v03-collect] QUALIFIED {run_id} "
            f"o1={smoke.get('observer_1_last_game_loop')} o2={smoke.get('observer_2_last_game_loop')}",
            flush=True,
        )

    return subprocess.call(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_v03_delivery.py"),
            "--run-ids",
            ",".join(st["qualified"][:target]),
            "--out-dir",
            str(OUT),
        ],
        cwd=str(ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
