#!/usr/bin/env python3
"""Build pilot delivery folder, CSV manifest, and validation report from qualified runs."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _channel_counts(run_dir: Path) -> dict[str, int]:
    from collections import Counter

    c: Counter[str] = Counter()
    for line in (run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event_type") == "llm_call_completed":
            c[str(e.get("channel") or "other")] += 1
    return dict(c)


def _fallback_count(run_dir: Path) -> int:
    n = 0
    for line in (run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event_type") in {"llm_call_completed", "llm_call_failed"} and e.get("fallback_used"):
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", required=True, help="Comma-separated qualified run_ids")
    ap.add_argument(
        "--out-dir",
        default=str(ROOT / "docs" / "replay" / "pilot_delivery"),
        help="Delivery output directory",
    )
    ap.add_argument("--runs-root", default=str(ROOT / "logs" / "runs"))
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    runs_root = Path(args.runs_root)
    py = Path(sys.executable)
    validator = ROOT / "scripts" / "validate_run_logs.py"

    rows = []
    reports = []
    seen_sha: set[str] = set()
    for run_id in [x.strip() for x in args.run_ids.split(",") if x.strip()]:
        src = runs_root / run_id
        if not src.is_dir():
            print(f"[FAIL] missing run dir {src}")
            return 1
        dest = out / run_id
        dest.mkdir(parents=True, exist_ok=True)
        for name in (
            "run_manifest.json",
            "run_events.jsonl",
            "llm_events.jsonl",
            "command_events.jsonl",
            "errors.jsonl",
        ):
            shutil.copy2(src / name, dest / name)
        m = _load(src / "run_manifest.json")
        replay_path = Path((m.get("replay") or {}).get("path") or "")
        if replay_path.is_file():
            shutil.copy2(replay_path, dest / "replay.SC2Replay")
        summary_path = dest / "validation_summary.json"
        rc = subprocess.call(
            [
                str(py),
                str(validator),
                "--run-dir",
                str(src),
                "--expect-status",
                "completed",
                "--pilot",
                "--summary-json",
                str(summary_path),
            ],
            cwd=str(ROOT),
        )
        summary = _load(summary_path) if summary_path.is_file() else {"validation_status": "fail"}
        sha = (m.get("replay") or {}).get("sha256")
        if sha in seen_sha:
            print(f"[FAIL] duplicate replay sha256 for {run_id}")
            return 1
        if sha:
            seen_sha.add(sha)
        if rc != 0 or summary.get("validation_status") != "pass":
            print(f"[FAIL] validation failed for {run_id}")
            return 1
        seed = m.get("random_seed")
        if isinstance(seed, dict):
            seed_v = seed.get("value")
        else:
            seed_v = seed
        rr = m.get("runtime_result") or m.get("result") or {}
        pr = m.get("replay_result") or {}
        rows.append(
            {
                "run_id": run_id,
                "config_name": m.get("config_name"),
                "config_version": m.get("config_version"),
                "config_sha256": m.get("config_sha256"),
                "random_seed": seed_v,
                "model_name": m.get("model_name"),
                "prompt_template_version": m.get("prompt_template_version"),
                "replay_file_name": (m.get("replay") or {}).get("file_name"),
                "replay_sha256": sha,
                "base_build": m.get("base_build"),
                "data_version": m.get("data_version"),
                "map_name_runtime": m.get("map_name_runtime") or m.get("map_name"),
                "map_name_replay": m.get("map_name_replay"),
                "replay_final_game_loop": m.get("replay_final_game_loop"),
                "replay_duration_sec": m.get("replay_duration_sec"),
                "player_1_race": (m.get("player_races") or [None, None])[0] if m.get("player_races") else None,
                "player_2_race": (m.get("player_races") or [None, None])[1] if m.get("player_races") else None,
                "runtime_result_p1": rr.get("player_1") if isinstance(rr, dict) else None,
                "runtime_result_p2": rr.get("player_2") if isinstance(rr, dict) else None,
                "replay_result_p1": pr.get("player_1") if isinstance(pr, dict) else None,
                "replay_result_p2": pr.get("player_2") if isinstance(pr, dict) else None,
                "termination_reason": m.get("termination_reason"),
                "llm_call_count": sum(_channel_counts(src).values()),
                "fallback_count": _fallback_count(src),
                "validation_status": summary.get("validation_status"),
            }
        )
        reports.append(summary)

    csv_path = out / "pilot_collection_manifest.csv"
    if rows:
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    report_path = out / "pilot_validation_report.json"
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")
    print(f"Delivered {len(rows)} runs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
