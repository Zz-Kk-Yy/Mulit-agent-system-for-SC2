#!/usr/bin/env python3
"""Local dry-run of paper ingest contract for a SwarmBrain delivery root.

Mirrors the checks expected by paper-side ingest_swarmbrain_runs.py --dry-run
when that repo is unavailable on this machine.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path


REQUIRED_RUN_FILES = (
    "run_manifest.json",
    "run_events.jsonl",
    "llm_events.jsonl",
    "command_events.jsonl",
    "errors.jsonl",
    "validation_summary.json",
    "replay.SC2Replay",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delivery-root", required=True)
    args = ap.parse_args()
    root = Path(args.delivery_root).resolve()
    fails: list[str] = []

    man_path = root / "pilot_collection_manifest.csv"
    rep_path = root / "pilot_validation_report.json"
    if not man_path.is_file():
        fails.append("missing pilot_collection_manifest.csv")
    if not rep_path.is_file():
        fails.append("missing pilot_validation_report.json")
    if fails:
        for f in fails:
            print(f"[FAIL] {f}")
        return 1

    rows = list(csv.DictReader(man_path.open(encoding="utf-8-sig")))
    reports = json.loads(rep_path.read_text(encoding="utf-8"))
    if not isinstance(reports, list):
        fails.append("pilot_validation_report.json must be a JSON array")
        reports = []

    man_ids = [r["run_id"] for r in rows]
    rep_ids = [r.get("run_id") for r in reports]
    if len(man_ids) != len(set(man_ids)):
        fails.append("duplicate run_id in pilot_collection_manifest.csv")
    if set(man_ids) != set(rep_ids):
        fails.append(f"run_id set mismatch manifest={sorted(set(man_ids))} report={sorted(set(rep_ids))}")

    required_cols = {
        "run_id",
        "config_name",
        "config_version",
        "config_sha256",
        "replay_file_name",
        "replay_sha256",
        "validation_status",
    }
    if rows and not required_cols.issubset(rows[0].keys()):
        fails.append(f"manifest missing columns: {sorted(required_cols - set(rows[0].keys()))}")

    for row in rows:
        rid = row["run_id"]
        run_dir = root / rid
        if not run_dir.is_dir():
            fails.append(f"{rid}: missing run directory")
            continue
        for name in REQUIRED_RUN_FILES:
            if not (run_dir / name).is_file():
                fails.append(f"{rid}: missing {name}")
        if row.get("replay_file_name") != "replay.SC2Replay":
            fails.append(f"{rid}: replay_file_name must be replay.SC2Replay")
        if row.get("validation_status") != "pass":
            fails.append(f"{rid}: validation_status != pass")
        replay = run_dir / "replay.SC2Replay"
        if replay.is_file():
            sha = _sha256(replay)
            if sha != row.get("replay_sha256"):
                fails.append(f"{rid}: replay sha mismatch csv vs file")
            m = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            rpath = str((m.get("replay") or {}).get("path") or "")
            if rpath != f"{rid}/replay.SC2Replay":
                fails.append(f"{rid}: run_manifest.replay.path not package-relative ({rpath})")
            if (m.get("replay") or {}).get("file_name") != "replay.SC2Replay":
                fails.append(f"{rid}: run_manifest.replay.file_name != replay.SC2Replay")
            if (m.get("replay") or {}).get("sha256") != sha:
                fails.append(f"{rid}: run_manifest.replay.sha256 mismatch")
            if str(m.get("config_version")) != str(row.get("config_version")):
                fails.append(f"{rid}: config_version mismatch manifest vs run_manifest")
            vs = json.loads((run_dir / "validation_summary.json").read_text(encoding="utf-8"))
            if vs.get("validation_status") != "pass":
                fails.append(f"{rid}: validation_summary not pass")

    # report items must include required keys
    for item in reports:
        for key in ("run_id", "validation_status", "replay_sha256", "base_build", "data_version"):
            if key not in item:
                fails.append(f"report[{item.get('run_id')}]: missing {key}")

    versions = sorted({r.get("config_version") for r in rows})
    print(f"[dry-run] delivery_root={root}")
    print(f"[dry-run] runs={len(rows)} config_versions={versions}")
    if fails:
        print(f"[dry-run] FAIL ({len(fails)} issues)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("[dry-run] PASS: paper ingest contract checks satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
