#!/usr/bin/env python3
"""Build balanced-channel delivery package + balanced_batch_manifest.csv."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarmbrain_replay_meta import parse_replay_file  # noqa: E402

# Paper cutoff buckets (seconds)
BUCKETS = {
    "early": (60.0, 180.0),
    "mid": (210.0, 360.0),
    "late": (390.0, 480.0),
}
CHANNELS = ("macro", "building", "attack")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _bucket_channel_counts(run_dir: Path) -> dict[str, dict[str, int]]:
    """Count label_only completed planner calls falling into early/mid/late."""
    out = {b: Counter() for b in BUCKETS}
    path = run_dir / "llm_events.jsonl"
    if not path.is_file():
        return {b: dict(c) for b, c in out.items()}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event_type") != "llm_call_completed":
            continue
        if (e.get("feature_scope") or "label_only") != "label_only":
            continue
        ch = e.get("channel")
        if ch not in CHANNELS:
            continue
        t = e.get("game_time_sec")
        if t is None:
            continue
        try:
            t = float(t)
        except Exception:
            continue
        for name, (lo, hi) in BUCKETS.items():
            if lo <= t <= hi:
                out[name][ch] += 1
                break
    return {b: dict(c) for b, c in out.items()}


def _channel_totals(run_dir: Path) -> dict[str, int]:
    c: Counter[str] = Counter()
    for line in (run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event_type") != "llm_call_completed":
            continue
        if (e.get("feature_scope") or "label_only") != "label_only":
            continue
        ch = e.get("channel")
        if ch in CHANNELS:
            c[ch] += 1
    return dict(c)


def _fmt_bucket(counts: dict[str, int]) -> str:
    return ";".join(f"{ch}={counts.get(ch, 0)}" for ch in CHANNELS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", required=True)
    ap.add_argument(
        "--out-dir",
        default=str(ROOT / "docs" / "replay" / "strategic_plan_delivery_balanced_v0.1"),
    )
    ap.add_argument("--runs-root", default=str(ROOT / "logs" / "runs"))
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    runs_root = Path(args.runs_root)
    validator = ROOT / "scripts" / "validate_run_logs.py"

    rows = []
    reports = []
    for run_id in [x.strip() for x in args.run_ids.split(",") if x.strip()]:
        src = runs_root / run_id
        if not src.is_dir():
            print(f"[FAIL] missing {src}")
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
        if not replay_path.is_file():
            hits = list(src.glob("*.SC2Replay"))
            replay_path = hits[0] if hits else Path()
        if replay_path.is_file():
            shutil.copy2(replay_path, dest / replay_path.name)

        summary_json = dest / "validation_summary.json"
        rc = subprocess.call(
            [
                sys.executable,
                str(validator),
                "--run-dir",
                str(dest),
                "--expect-status",
                "completed",
                "--pilot",
                "--summary-json",
                str(summary_json),
            ],
            cwd=str(ROOT),
        )
        summary = _load(summary_json) if summary_json.is_file() else {}
        meta = parse_replay_file(dest / replay_path.name) if replay_path.is_file() else {}
        buckets = _bucket_channel_counts(dest)
        totals = _channel_totals(dest)
        runtime = m.get("runtime_result") or m.get("result")
        replay_res = m.get("replay_result") or meta.get("player_results")
        row = {
            "run_id": run_id,
            "status": m.get("status"),
            "replay_sha256": (m.get("replay") or {}).get("sha256") or meta.get("sha256"),
            "replay_final_game_loop": m.get("replay_final_game_loop")
            or meta.get("replay_final_game_loop"),
            "runtime_result": json.dumps(runtime, ensure_ascii=False),
            "replay_result": json.dumps(replay_res, ensure_ascii=False),
            "early_counts": _fmt_bucket(buckets["early"]),
            "mid_counts": _fmt_bucket(buckets["mid"]),
            "late_counts": _fmt_bucket(buckets["late"]),
            "macro_windows": totals.get("macro", 0),
            "building_windows": totals.get("building", 0),
            "attack_windows": totals.get("attack", 0),
            "notes": (
                f"validation={summary.get('validation_status')};"
                f"validator_rc={rc};"
                f"warn={len(summary.get('warnings') or [])}"
            ),
        }
        rows.append(row)
        reports.append(
            {
                "run_id": run_id,
                "validation": summary,
                "buckets": buckets,
                "channel_totals": totals,
                "runtime_result": runtime,
                "replay_result": replay_res,
                "replay_final_game_loop": row["replay_final_game_loop"],
                "replay_duration_sec": meta.get("replay_duration_sec"),
            }
        )
        print(
            f"[balanced-delivery] {run_id} early={row['early_counts']} "
            f"mid={row['mid_counts']} late={row['late_counts']}",
            flush=True,
        )

    csv_path = out / "balanced_batch_manifest.csv"
    fields = [
        "run_id",
        "status",
        "replay_sha256",
        "replay_final_game_loop",
        "runtime_result",
        "replay_result",
        "early_counts",
        "mid_counts",
        "late_counts",
        "macro_windows",
        "building_windows",
        "attack_windows",
        "notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    report_path = out / "balanced_batch_report.json"
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")
    print(f"Delivered {len(rows)} runs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
