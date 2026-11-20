#!/usr/bin/env python3
"""Validate SwarmBrain per-run LLM JSONL logs (schema 1.0.0)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SECRET_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{16,}|bearer\s+[A-Za-z0-9\-._~+/]+=*|api[_-]?key\s*[:=]\s*\S+)")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="Path to logs/runs/<run_id>")
    args = ap.parse_args()
    run_dir = Path(args.run_dir).resolve()
    fails: list[str] = []
    passes = 0

    def ok(msg: str) -> None:
        nonlocal passes
        passes += 1
        print(f"[PASS] {msg}")

    def bad(msg: str) -> None:
        fails.append(msg)
        print(f"[FAIL] {msg}")

    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        bad("missing run_manifest.json")
        print(f"Summary: pass={passes} fail={len(fails)}")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ok("manifest JSON parses")
    except Exception as exc:
        bad(f"manifest parse error: {exc}")
        print(f"Summary: pass={passes} fail={len(fails)}")
        return 1

    run_id = manifest.get("run_id")
    if not run_id or run_dir.name != run_id:
        bad(f"run_id mismatch manifest={run_id} dir={run_dir.name}")
    else:
        ok("run_id matches directory name")

    llm = load_jsonl(run_dir / "llm_events.jsonl")
    run_ev = load_jsonl(run_dir / "run_events.jsonl")
    cmd = load_jsonl(run_dir / "command_events.jsonl")
    err = load_jsonl(run_dir / "errors.jsonl")
    ok(f"jsonl loaded llm={len(llm)} run={len(run_ev)} cmd={len(cmd)} errors={len(err)}")

    event_ids = []
    for rows in (llm, run_ev, cmd, err):
        for r in rows:
            if r.get("run_id") != run_id:
                bad(f"event run_id mismatch: {r.get('event_type')} {r.get('event_id')}")
            event_ids.append(r.get("event_id"))
            if r.get("schema_version") != "1.0.0":
                bad(f"bad schema_version on {r.get('event_id')}")
    if len(event_ids) != len(set(event_ids)):
        bad("duplicate event_id values")
    else:
        ok("event_id unique")

    started = {r["llm_call_id"] for r in llm if r.get("event_type") == "llm_call_started"}
    completed = {r["llm_call_id"] for r in llm if r.get("event_type") == "llm_call_completed"}
    failed = {r["llm_call_id"] for r in llm if r.get("event_type") == "llm_call_failed"}
    both = completed & failed
    if both:
        bad(f"llm_call both completed and failed: {sorted(both)[:3]}")
    else:
        ok("no llm_call marked both completed and failed")
    unfinished = started - completed - failed
    if unfinished:
        bad(f"llm_call started without terminal event: {len(unfinished)}")
    else:
        ok("all started llm_calls have completed/failed")

    for r in llm:
        if r.get("event_type") == "llm_fallback_selected" and not r.get("llm_call_id"):
            bad("fallback missing llm_call_id")
        if r.get("feature_scope") not in {"label_only", "audit_only", None}:
            bad(f"bad feature_scope {r.get('feature_scope')}")

    # command linkage for llm-sourced
    for r in cmd:
        if r.get("source") == "llm" and r.get("event_type") == "command_proposed":
            if not r.get("llm_call_id"):
                bad(f"llm proposed command missing llm_call_id: {r.get('command_id')}")

    # game_loop non-decreasing per agent_side for llm events with numeric loops
    by_side: dict[str, list[int]] = {}
    for r in llm:
        gl = r.get("game_loop")
        if isinstance(gl, int):
            by_side.setdefault(str(r.get("agent_side")), []).append(gl)
    loop_ok = True
    for side, loops in by_side.items():
        if loops != sorted(loops):
            loop_ok = False
            bad(f"game_loop went backwards for side={side}")
    if loop_ok:
        ok("game_loop non-decreasing per side (where present)")

    # secrets scan
    blob = manifest_path.read_text(encoding="utf-8")
    for p in (run_dir / "llm_events.jsonl", run_dir / "command_events.jsonl", run_dir / "errors.jsonl"):
        if p.is_file():
            blob += p.read_text(encoding="utf-8")
    if SECRET_RE.search(blob):
        bad("possible secret pattern found in logs")
    else:
        ok("no common secret patterns found")

    # replay checks for completed runs
    status = manifest.get("status")
    if status == "completed":
        replay = manifest.get("replay") or {}
        path = replay.get("path")
        if not path or not Path(path).is_file():
            bad("completed run missing replay file")
        else:
            ok("replay file exists")
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while True:
                    c = f.read(1024 * 1024)
                    if not c:
                        break
                    h.update(c)
            digest = h.hexdigest()
            if digest != replay.get("sha256"):
                bad("replay sha256 mismatch")
            else:
                ok("replay sha256 matches manifest")
    else:
        ok(f"status={status} (skip strict replay file requirement)")

    print(f"Summary: pass={passes} fail={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
