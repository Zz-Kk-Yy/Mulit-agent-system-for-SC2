#!/usr/bin/env python3
"""Normalize balanced v0.1 delivery for paper-side ingest (lossless).

Creates strategic_plan_delivery_balanced_v0.1_normalized/ without modifying
the original strategic_plan_delivery_balanced_v0.1/ tree.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "replay" / "strategic_plan_delivery_balanced_v0.1"
OUT = ROOT / "docs" / "replay" / "strategic_plan_delivery_balanced_v0.1_normalized"
STATE = ROOT / "docs" / "replay" / "balanced_channel_collection_state_v0.1.json"
REPORT_SRC = ROOT / "docs" / "replay" / "balanced_channel_delivery_report_v0.1.md"

MANIFEST_FIELDS = [
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

LOG_FILES = (
    "run_manifest.json",
    "run_events.jsonl",
    "llm_events.jsonl",
    "command_events.jsonl",
    "errors.jsonl",
    "validation_summary.json",
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


def _load(path: Path) -> dict:
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


def _find_replay(run_dir: Path) -> Path:
    hits = sorted(run_dir.glob("*.SC2Replay"))
    if not hits:
        raise FileNotFoundError(f"no replay in {run_dir}")
    # Prefer non-normalized original name if both somehow exist
    for h in hits:
        if h.name != "replay.SC2Replay":
            return h
    return hits[0]


def _enrich_report_with_quarantine(text: str, quarantine: list[dict]) -> str:
    if "quarantine_reason" in text and "run_id,status,quarantine_reason" in text:
        return text
    lines = [text.rstrip(), "", "## 6. Quarantine 明细表（可追溯）", ""]
    lines.append(
        "| run_id | status | quarantine_reason | termination_reason | "
        "runtime_result | replay_result | replay_final_game_loop | "
        "validator_status | failed_rules | notes |"
    )
    lines.append("|---|---|---|---|---|---|---:|---|---|---|")
    for q in quarantine:
        rid = q.get("run_id") or ""
        reason = str(q.get("exclude_reason") or "")
        # status for quarantine rows: not qualified
        status = q.get("status") or "quarantined"
        term = ""
        if reason.startswith("test_time_limit"):
            term = "test_time_limit"
            status = "quarantined"
        runtime = json.dumps(q.get("runtime_result"), ensure_ascii=False) if q.get("runtime_result") else ""
        replay = json.dumps(q.get("replay_result"), ensure_ascii=False) if q.get("replay_result") else ""
        loop = q.get("replay_final_game_loop")
        loop_s = "" if loop is None else str(loop)
        val = q.get("validation_status") or ""
        failed = json.dumps(q.get("failed_rules") or [], ensure_ascii=False)
        notes = ""
        if q.get("strategic_audit_exit") not in (None, 0):
            notes = f"strategic_audit_exit={q.get('strategic_audit_exit')}"
        # escape pipes
        def esc(x: object) -> str:
            return str(x).replace("|", "\\|")

        lines.append(
            f"| `{esc(rid)}` | {esc(status)} | `{esc(reason)}` | {esc(term)} | "
            f"`{esc(runtime)}` | `{esc(replay)}` | {esc(loop_s)} | {esc(val)} | "
            f"`{esc(failed)}` | {esc(notes)} |"
        )
    lines.append("")
    lines.append("说明：quarantine 仅出现在本回报中，不进入 `pilot_collection_manifest.csv`。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not SRC.is_dir():
        raise SystemExit(f"missing source delivery: {SRC}")
    state = _load(STATE) if STATE.is_file() else {}
    qualified = list(state.get("qualified") or [])
    if not qualified:
        # fall back to directories in SRC
        qualified = sorted(p.name for p in SRC.iterdir() if p.is_dir())
    quarantine = list(state.get("quarantine") or [])

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    reports: list[dict] = []
    for run_id in qualified:
        src = SRC / run_id
        if not src.is_dir():
            raise SystemExit(f"missing run dir: {src}")
        dest = OUT / run_id
        dest.mkdir(parents=True, exist_ok=True)
        for name in LOG_FILES:
            sp = src / name
            if not sp.is_file():
                raise SystemExit(f"missing {sp}")
            shutil.copy2(sp, dest / name)

        src_replay = _find_replay(src)
        src_sha = _sha256(src_replay)
        dest_replay = dest / "replay.SC2Replay"
        shutil.copy2(src_replay, dest_replay)
        dest_sha = _sha256(dest_replay)
        if src_sha != dest_sha:
            raise SystemExit(f"SHA mismatch after copy for {run_id}")

        m = _load(dest / "run_manifest.json")
        summary = _load(dest / "validation_summary.json")
        if summary.get("validation_status") != "pass":
            raise SystemExit(f"validation_status not pass for {run_id}")
        manifest_sha = (m.get("replay") or {}).get("sha256")
        if manifest_sha and manifest_sha != src_sha:
            raise SystemExit(
                f"manifest replay sha != file sha for {run_id}: {manifest_sha} vs {src_sha}"
            )
        if summary.get("replay_sha256") and summary.get("replay_sha256") != src_sha:
            raise SystemExit(f"summary replay sha mismatch for {run_id}")

        rr = m.get("runtime_result") or m.get("result") or {}
        pr = m.get("replay_result") or {}
        races = m.get("player_races") or [None, None]
        row = {
            "run_id": run_id,
            "config_name": m.get("config_name"),
            "config_version": m.get("config_version"),
            "config_sha256": m.get("config_sha256"),
            "random_seed": _seed_value(m),
            "model_name": m.get("model_name"),
            "prompt_template_version": m.get("prompt_template_version"),
            "replay_file_name": "replay.SC2Replay",
            "replay_sha256": src_sha,
            "base_build": m.get("base_build"),
            "data_version": m.get("data_version"),
            "map_name_runtime": m.get("map_name_runtime") or m.get("map_name"),
            "map_name_replay": m.get("map_name_replay"),
            "replay_final_game_loop": m.get("replay_final_game_loop"),
            "replay_duration_sec": m.get("replay_duration_sec"),
            "player_1_race": races[0] if len(races) > 0 else None,
            "player_2_race": races[1] if len(races) > 1 else None,
            "runtime_result_p1": rr.get("player_1") if isinstance(rr, dict) else None,
            "runtime_result_p2": rr.get("player_2") if isinstance(rr, dict) else None,
            "replay_result_p1": pr.get("player_1") if isinstance(pr, dict) else None,
            "replay_result_p2": pr.get("player_2") if isinstance(pr, dict) else None,
            "termination_reason": m.get("termination_reason"),
            "llm_call_count": _llm_call_count(dest),
            "fallback_count": _fallback_count(dest),
            "validation_status": "pass",
        }
        rows.append(row)

        # Keep full validation summary fields; ensure required keys present
        report_item = dict(summary)
        report_item["run_id"] = run_id
        report_item["validation_status"] = "pass"
        report_item["replay_sha256"] = src_sha
        report_item["base_build"] = m.get("base_build")
        report_item["data_version"] = m.get("data_version")
        reports.append(report_item)
        print(f"[ok] {run_id} sha={src_sha[:12]}... loop={row['replay_final_game_loop']}")

    # pilot-compatible files
    with (OUT / "pilot_collection_manifest.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(rows)
    (OUT / "pilot_validation_report.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # keep balanced extras
    for name in ("balanced_batch_manifest.csv", "balanced_batch_report.json"):
        shutil.copy2(SRC / name, OUT / name)

    report_text = REPORT_SRC.read_text(encoding="utf-8") if REPORT_SRC.is_file() else ""
    report_text = _enrich_report_with_quarantine(report_text, quarantine)
    (OUT / "balanced_channel_delivery_report_v0.1.md").write_text(report_text, encoding="utf-8")
    # also refresh repo-level report for consistency
    if REPORT_SRC.parent.is_dir():
        REPORT_SRC.write_text(report_text, encoding="utf-8")

    # quarantine CSV for machine traceability
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
    with (OUT / "balanced_quarantine_manifest.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=q_fields)
        w.writeheader()
        for q in quarantine:
            reason = str(q.get("exclude_reason") or "")
            term = "test_time_limit" if reason.startswith("test_time_limit") else ""
            w.writerow(
                {
                    "run_id": q.get("run_id"),
                    "status": q.get("status") or "quarantined",
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
                    "notes": (
                        f"strategic_audit_exit={q.get('strategic_audit_exit')}"
                        if q.get("strategic_audit_exit") not in (None, 0)
                        else ""
                    ),
                }
            )

    # integrity summary
    assert len(rows) == 12, len(rows)
    assert len(reports) == 12, len(reports)
    assert {r["run_id"] for r in rows} == {r["run_id"] for r in reports}
    assert all(r["replay_file_name"] == "replay.SC2Replay" for r in rows)
    assert all(r["validation_status"] == "pass" for r in rows)
    assert len(quarantine) == 16, len(quarantine)
    # ensure no quarantine id in qualified manifest
    q_ids = {q.get("run_id") for q in quarantine}
    assert not (q_ids & {r["run_id"] for r in rows})

    print(f"Wrote {OUT / 'pilot_collection_manifest.csv'}")
    print(f"Wrote {OUT / 'pilot_validation_report.json'}")
    print(f"Wrote {OUT / 'balanced_channel_delivery_report_v0.1.md'}")
    print(f"Wrote {OUT / 'balanced_quarantine_manifest.csv'}")
    print(f"Normalized {len(rows)} runs -> {OUT}")
    print(f"Source untouched: {SRC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
