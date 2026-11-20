#!/usr/bin/env python3
"""Build pilot v0.3 delivery package compatible with paper ingest contract."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarmbrain_replay_meta import parse_replay_file  # noqa: E402

BUCKETS = {
    "early": (60.0, 180.0),
    "mid": (210.0, 360.0),
    "late": (390.0, 480.0),
}
CHANNELS = ("macro", "building", "attack")

# Runtime-recorded config_sha256 -> declared config_version for delivery.
# Distinguishes the two historical cover configs already in the 4-game set.
CONFIG_VERSION_BY_SHA = {
    "802dfe0ef14685f15d2ce27f8efc619221d3d7ecde0d360fbe758e38f64b1fca": "1.0.0",
    "3a25eac9f410a8fb6dcf5789e9d400617ce5226348288d07df5f30ea2859e2ea": "1.1.0",
    "01b3965e3f139302057a4b17f78250f1d1e9236578aa1abdb2f254c548d9a58a": "1.2.0",
    "87a37be01fe162dcabb1bd5822f5f0daef673e246c962e41e6bbef5428246af6": "1.3.0",
}

PILOT_MANIFEST_FIELDS = [
    "run_id",
    "config_name",
    "config_version",
    "config_sha256",
    "random_seed",
    "model_name",
    "prompt_template_version",
    "replay_file_name",
    "replay_sha256",
    "base_build",
    "data_version",
    "map_name_runtime",
    "map_name_replay",
    "replay_final_game_loop",
    "replay_duration_sec",
    "player_1_race",
    "player_2_race",
    "runtime_result_p1",
    "runtime_result_p2",
    "replay_result_p1",
    "replay_result_p2",
    "termination_reason",
    "llm_call_count",
    "fallback_count",
    "validation_status",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_value(m: dict) -> str | None:
    seed = m.get("random_seed")
    if isinstance(seed, dict):
        return seed.get("value")
    return seed


def _llm_call_count(run_dir: Path) -> int:
    n = 0
    for line in (run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event_type") == "llm_call_completed":
            n += 1
    return n


def _fallback_count(run_dir: Path) -> int:
    n = 0
    for line in (run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event_type") in {"llm_call_completed", "llm_call_failed"} and e.get("fallback_used"):
            n += 1
    return n


def _resolve_config_version(m: dict) -> str:
    sha = str(m.get("config_sha256") or "")
    if sha in CONFIG_VERSION_BY_SHA:
        return CONFIG_VERSION_BY_SHA[sha]
    # Prefer runtime-declared version for newer configs (e.g. 1.2.0).
    return str(m.get("config_version") or "unknown")


def _bucket_counts(run_dir: Path) -> dict[str, Counter]:
    out = {b: Counter() for b in BUCKETS}
    for line in (run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines():
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
    return out


def _rewrite_delivery_manifest(m: dict, *, run_id: str, sha: str, size_bytes: int) -> dict:
    """Package-local replay path + corrected config_version; do not invent results."""
    out = dict(m)
    cfg_ver = _resolve_config_version(m)
    out["config_version"] = cfg_ver
    replay = dict(out.get("replay") or {})
    replay["path"] = f"{run_id}/replay.SC2Replay"
    replay["file_name"] = "replay.SC2Replay"
    replay["sha256"] = sha
    replay["size_bytes"] = size_bytes
    out["replay"] = replay
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", required=True)
    ap.add_argument(
        "--out-dir",
        default=str(ROOT / "docs" / "replay" / "strategic_plan_delivery_v03_next"),
    )
    ap.add_argument("--runs-root", default=str(ROOT / "logs" / "runs"))
    ap.add_argument(
        "--state",
        default=str(ROOT / "docs" / "replay" / "v03_next_collection_state.json"),
    )
    args = ap.parse_args()
    out = Path(args.out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    runs_root = Path(args.runs_root)
    state = _load(Path(args.state)) if Path(args.state).is_file() else {}

    batch_rows = []
    batch_reports = []
    pilot_rows = []
    pilot_reports = []

    for run_id in [x.strip() for x in args.run_ids.split(",") if x.strip()]:
        src = runs_root / run_id
        dest = out / run_id
        dest.mkdir(parents=True, exist_ok=True)
        for name in (
            "run_events.jsonl",
            "llm_events.jsonl",
            "command_events.jsonl",
        ):
            shutil.copy2(src / name, dest / name)
        err_src = src / "errors.jsonl"
        if err_src.is_file():
            shutil.copy2(err_src, dest / "errors.jsonl")
        else:
            (dest / "errors.jsonl").write_text("", encoding="utf-8")

        m_src = _load(src / "run_manifest.json")
        replay_src = Path((m_src.get("replay") or {}).get("path") or "")
        if not replay_src.is_file():
            hits = list(src.glob("*.SC2Replay"))
            if not hits:
                raise SystemExit(f"missing replay for {run_id}")
            replay_src = hits[0]
        shutil.copy2(replay_src, dest / "replay.SC2Replay")
        sha = _sha256(dest / "replay.SC2Replay")
        size_bytes = (dest / "replay.SC2Replay").stat().st_size
        manifest_sha = (m_src.get("replay") or {}).get("sha256")
        if manifest_sha and manifest_sha != sha:
            raise SystemExit(f"replay sha mismatch for {run_id}: {manifest_sha} vs {sha}")

        m = _rewrite_delivery_manifest(m_src, run_id=run_id, sha=sha, size_bytes=size_bytes)
        (dest / "run_manifest.json").write_text(
            json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        vs_src = src / "validation_summary.json"
        vs_alt = ROOT / "docs" / "replay" / f"v03_validate_{run_id}.json"
        if vs_src.is_file():
            shutil.copy2(vs_src, dest / "validation_summary.json")
        elif vs_alt.is_file():
            shutil.copy2(vs_alt, dest / "validation_summary.json")
        else:
            summary_path = dest / "validation_summary.json"
            subprocess.call(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "validate_run_logs.py"),
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
        smoke_src = src / "replay_smoke_summary.json"
        if smoke_src.is_file():
            shutil.copy2(smoke_src, dest / "replay_smoke_summary.json")
        else:
            raise SystemExit(f"missing replay_smoke_summary.json for {run_id}")

        summary = _load(dest / "validation_summary.json")
        if summary.get("validation_status") != "pass":
            raise SystemExit(f"validation_status not pass for {run_id}")
        smoke = _load(dest / "replay_smoke_summary.json")
        if smoke.get("status") != "pass":
            raise SystemExit(f"replay_smoke_status not pass for {run_id}")

        buckets = _bucket_counts(dest)
        meta = parse_replay_file(dest / "replay.SC2Replay")
        runtime = m.get("runtime_result") or m.get("result") or {}
        replay_res = m.get("replay_result") or meta.get("player_results") or {}
        races = m.get("player_races") or [None, None]
        cfg_ver = _resolve_config_version(m_src)

        batch_row = {
            "run_id": run_id,
            "status": m.get("status"),
            "config_name": m.get("config_name"),
            "config_version": cfg_ver,
            "config_sha256": m.get("config_sha256"),
            "replay_sha256": sha,
            "replay_final_game_loop": m.get("replay_final_game_loop") or meta.get("replay_final_game_loop"),
            "observer_1_last_game_loop": smoke.get("observer_1_last_game_loop"),
            "observer_2_last_game_loop": smoke.get("observer_2_last_game_loop"),
            "runtime_result": json.dumps(runtime, ensure_ascii=False),
            "replay_result": json.dumps(replay_res, ensure_ascii=False),
            "early_macro": buckets["early"].get("macro", 0),
            "early_building": buckets["early"].get("building", 0),
            "early_attack": buckets["early"].get("attack", 0),
            "mid_macro": buckets["mid"].get("macro", 0),
            "mid_building": buckets["mid"].get("building", 0),
            "mid_attack": buckets["mid"].get("attack", 0),
            "late_macro": buckets["late"].get("macro", 0),
            "late_building": buckets["late"].get("building", 0),
            "late_attack": buckets["late"].get("attack", 0),
            "validator_status": summary.get("validation_status"),
            "replay_smoke_status": smoke.get("status"),
            "notes": f"config={m.get('config_name')}@{cfg_ver}",
        }
        batch_rows.append(batch_row)
        batch_reports.append(
            {
                "run_id": run_id,
                "row": batch_row,
                "buckets": {k: dict(v) for k, v in buckets.items()},
                "smoke": smoke,
            }
        )

        pilot_rows.append(
            {
                "run_id": run_id,
                "config_name": m.get("config_name"),
                "config_version": cfg_ver,
                "config_sha256": m.get("config_sha256"),
                "random_seed": _seed_value(m),
                "model_name": m.get("model_name"),
                "prompt_template_version": m.get("prompt_template_version"),
                "replay_file_name": "replay.SC2Replay",
                "replay_sha256": sha,
                "base_build": m.get("base_build"),
                "data_version": m.get("data_version"),
                "map_name_runtime": m.get("map_name_runtime") or m.get("map_name"),
                "map_name_replay": m.get("map_name_replay"),
                "replay_final_game_loop": m.get("replay_final_game_loop"),
                "replay_duration_sec": m.get("replay_duration_sec"),
                "player_1_race": races[0] if len(races) > 0 else None,
                "player_2_race": races[1] if len(races) > 1 else None,
                "runtime_result_p1": runtime.get("player_1") if isinstance(runtime, dict) else None,
                "runtime_result_p2": runtime.get("player_2") if isinstance(runtime, dict) else None,
                "replay_result_p1": replay_res.get("player_1") if isinstance(replay_res, dict) else None,
                "replay_result_p2": replay_res.get("player_2") if isinstance(replay_res, dict) else None,
                "termination_reason": m.get("termination_reason"),
                "llm_call_count": _llm_call_count(dest),
                "fallback_count": _fallback_count(dest),
                "validation_status": "pass",
            }
        )
        report_item = dict(summary)
        report_item["run_id"] = run_id
        report_item["validation_status"] = "pass"
        report_item["replay_sha256"] = sha
        report_item["base_build"] = m.get("base_build")
        report_item["data_version"] = m.get("data_version")
        report_item["config_name"] = m.get("config_name")
        report_item["config_version"] = cfg_ver
        report_item["config_sha256"] = m.get("config_sha256")
        report_item["runtime_result"] = runtime
        report_item["replay_result"] = replay_res
        pilot_reports.append(report_item)
        print(
            f"[v03-delivery] {run_id} cfg={cfg_ver} smoke={smoke.get('status')} "
            f"o1={smoke.get('observer_1_last_game_loop')} o2={smoke.get('observer_2_last_game_loop')}"
        )

    with (out / "pilot_collection_manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PILOT_MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(pilot_rows)
    (out / "pilot_validation_report.json").write_text(
        json.dumps(pilot_reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with (out / "balanced_batch_manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(batch_rows[0].keys()))
        w.writeheader()
        w.writerows(batch_rows)
    (out / "balanced_batch_report.json").write_text(
        json.dumps(batch_reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Quarantine CSV (traceability only; not in pilot manifest)
    q_fields = [
        "run_id",
        "status",
        "quarantine_reason",
        "termination_reason",
        "runtime_result",
        "replay_result",
        "replay_final_game_loop",
        "validator_status",
        "failed_rules",
        "notes",
    ]
    quarantine = list(state.get("quarantine") or [])
    with (out / "v03_quarantine_manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=q_fields)
        w.writeheader()
        for q in quarantine:
            reason = str(q.get("exclude_reason") or "")
            term = "test_time_limit" if "test_time_limit" in reason else (q.get("termination_reason") or "")
            w.writerow(
                {
                    "run_id": q.get("run_id"),
                    "status": "quarantined",
                    "quarantine_reason": reason,
                    "termination_reason": term,
                    "runtime_result": json.dumps(q.get("runtime_result"), ensure_ascii=False)
                    if q.get("runtime_result") is not None
                    else "",
                    "replay_result": json.dumps(q.get("replay_result"), ensure_ascii=False)
                    if q.get("replay_result") is not None
                    else "",
                    "replay_final_game_loop": q.get("replay_final_game_loop"),
                    "validator_status": q.get("validation_status") or "",
                    "failed_rules": json.dumps(q.get("failed_rules") or [], ensure_ascii=False),
                    "notes": "",
                }
            )

    matrix = {b: Counter() for b in BUCKETS}
    runs_with = {b: {c: set() for c in CHANNELS} for b in BUCKETS}
    for r in batch_rows:
        for b in BUCKETS:
            for c in CHANNELS:
                n = int(r[f"{b}_{c}"])
                matrix[b][c] += n
                if n > 0:
                    runs_with[b][c].add(r["run_id"])

    cfg_counts = Counter(r["config_version"] for r in pilot_rows)
    lines = []
    a = lines.append
    a("# pilot v0.3 next-collection 交付回报")
    a("")
    a(f"- 总采集合格局数: **{len(batch_rows)}**")
    a(f"- quarantine 局数: **{len(quarantine)}**")
    a("- 配置族: `pilot_dual_llm_v03_cover_v1`")
    a(
        "- 配置版本: "
        + ", ".join(f"`{k}`×{v}" for k, v in sorted(cfg_counts.items()))
    )
    a("- 旧排除 run 未重交: `ed48f9c5`, `ff4cb329`, `5d1cc263`, `b244d8b5`")
    a("- 论文导入契约文件: `pilot_collection_manifest.csv`, `pilot_validation_report.json`")
    a("- 每局 `run_manifest.json` 中 `replay.path` 为包内相对路径 `<run_id>/replay.SC2Replay`")
    a("")
    a("## 新 run ID")
    a("")
    for r in pilot_rows:
        a(f"- `{r['run_id']}` (`config_version={r['config_version']}`)")
    a("")
    a("## 双视角 replay 覆盖")
    a("")
    a("| run_id | header_loop | o1 | o2 | smoke | config_version |")
    a("|---|---:|---:|---:|---|---|")
    for r in batch_rows:
        a(
            f"| `{r['run_id']}` | {r['replay_final_game_loop']} | "
            f"{r['observer_1_last_game_loop']} | {r['observer_2_last_game_loop']} | "
            f"{r['replay_smoke_status']} | {r['config_version']} |"
        )
    a("")
    a("## 时间段 × 类别（SwarmBrain 侧 label_only 调用近似）")
    a("")
    a("| bucket | macro | building | attack |")
    a("|---|---:|---:|---:|")
    for b in BUCKETS:
        a(f"| {b} | {matrix[b].get('macro',0)} | {matrix[b].get('building',0)} | {matrix[b].get('attack',0)} |")
    a("")
    a("### 每单元 run 覆盖数")
    a("")
    a("| bucket | macro_runs | building_runs | attack_runs |")
    a("|---|---:|---:|---:|")
    for b in BUCKETS:
        a(
            f"| {b} | {len(runs_with[b]['macro'])} | {len(runs_with[b]['building'])} | {len(runs_with[b]['attack'])} |"
        )
    a("")
    missing = []
    for b in BUCKETS:
        for c in CHANNELS:
            if matrix[b].get(c, 0) == 0:
                missing.append(f"{b}×{c}")
    a("## 覆盖门槛自评（相对本批，非论文合并后最终值）")
    a("")
    if missing:
        a("- 本批仍缺失单元: " + ", ".join(f"`{x}`" for x in missing))
        a("- 已如实记录；未通过 prompt/改标签伪造。")
        a("- 将继续用 `config_version=1.2.0` 自然补采 mid/late macro 与 building。")
    else:
        a("- 本批九个时间段×类别单元均出现了至少一次自然调用。")
    a("")
    a("## Quarantine 原因")
    a("")
    reasons = Counter()
    for q in quarantine:
        reason = str(q.get("exclude_reason") or "unknown")
        reasons[reason.split(":")[0][:80]] += 1
    for k, v in reasons.most_common():
        a(f"- `{k}`: {v}")
    a("")
    (out / "balanced_batch_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Integrity gates for paper contract
    assert {r["run_id"] for r in pilot_rows} == {r["run_id"] for r in pilot_reports}
    assert all(r["replay_file_name"] == "replay.SC2Replay" for r in pilot_rows)
    assert all(r["validation_status"] == "pass" for r in pilot_rows)
    q_ids = {q.get("run_id") for q in quarantine}
    assert not (q_ids & {r["run_id"] for r in pilot_rows})

    print(f"Wrote {out / 'pilot_collection_manifest.csv'}")
    print(f"Wrote {out / 'pilot_validation_report.json'}")
    print(f"Delivered {len(batch_rows)} runs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
