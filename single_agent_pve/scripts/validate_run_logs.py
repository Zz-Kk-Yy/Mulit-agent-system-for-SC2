#!/usr/bin/env python3
"""Validate SwarmBrain per-run LLM JSONL logs (schema 1.0.0)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SECRET_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{16,}|bearer\s+[A-Za-z0-9\-._~+/]+=*|api[_-]?key\s*[:=]\s*\S+)")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="Path to logs/runs/<run_id>")
    ap.add_argument(
        "--expect-status",
        default=None,
        help="Optional expected manifest status (completed|crashed|aborted|running)",
    )
    ap.add_argument(
        "--require-channels",
        default="",
        help="Comma list e.g. opening,macro,building,attack",
    )
    ap.add_argument(
        "--require-agents",
        default="",
        help="Comma list e.g. red,blue (opening may be shared)",
    )
    ap.add_argument(
        "--pilot",
        action="store_true",
        help="Enforce pilot qualification rules (Victory/Defeat, no smoke/time-limit Tie)",
    )
    ap.add_argument(
        "--summary-json",
        default="",
        help="Optional path to write validation summary JSON",
    )
    args = ap.parse_args()
    run_dir = Path(args.run_dir).resolve()
    fails: list[str] = []
    warnings: list[str] = []
    passes = 0

    def ok(msg: str) -> None:
        nonlocal passes
        passes += 1
        print(f"[PASS] {msg}")

    def bad(msg: str) -> None:
        fails.append(msg)
        print(f"[FAIL] {msg}")

    def warn(msg: str) -> None:
        warnings.append(msg)
        print(f"[WARN] {msg}")

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

    if args.expect_status and manifest.get("status") != args.expect_status:
        bad(f"expected status={args.expect_status} got={manifest.get('status')}")
    elif args.expect_status:
        ok(f"status matches expect={args.expect_status}")

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

    started = {r["llm_call_id"]: r for r in llm if r.get("event_type") == "llm_call_started"}
    completed = {r["llm_call_id"]: r for r in llm if r.get("event_type") == "llm_call_completed"}
    failed = {r["llm_call_id"]: r for r in llm if r.get("event_type") == "llm_call_failed"}
    both = set(completed) & set(failed)
    if both:
        bad(f"llm_call both completed and failed: {sorted(both)[:3]}")
    else:
        ok("no llm_call marked both completed and failed")
    unfinished = set(started) - set(completed) - set(failed)
    if unfinished:
        bad(f"llm_call started without terminal event: {len(unfinished)}")
    else:
        ok("all started llm_calls have completed/failed")

    # P0-2 identity frozen across started/completed
    identity_ok = True
    for cid, s in started.items():
        t = completed.get(cid) or failed.get(cid)
        if not t:
            continue
        for key in ("agent_id", "agent_side", "player_id", "race", "channel"):
            if s.get(key) != t.get(key):
                identity_ok = False
                bad(f"identity drift on {cid} field={key}: {s.get(key)!r} -> {t.get(key)!r}")
                break
    if identity_ok:
        ok("llm identity stable across started/completed|failed")

    # parse_status rules (P0-3)
    parse_ok = True
    for r in completed.values():
        status = r.get("parse_status")
        cmds = r.get("parsed_commands") or []
        if status == "success":
            parse_ok = False
            bad(f"legacy parse_status=success on {r.get('llm_call_id')}")
        if status == "success_nonempty" and not cmds:
            parse_ok = False
            bad(f"success_nonempty but empty parsed_commands: {r.get('llm_call_id')}")
        if status not in {"success_nonempty", "success_empty", "failed", "not_applicable", None}:
            parse_ok = False
            bad(f"bad parse_status={status}")
    if parse_ok:
        ok("parse_status values valid")

    # proposed count vs parsed_commands for nonempty
    proposed_by_call: dict[str, int] = defaultdict(int)
    for r in cmd:
        if r.get("event_type") == "command_proposed" and r.get("llm_call_id"):
            proposed_by_call[r["llm_call_id"]] += 1
    for cid, comp in completed.items():
        if comp.get("parse_status") == "success_nonempty":
            n = len(comp.get("parsed_commands") or [])
            p = proposed_by_call.get(cid, 0)
            if n != p and comp.get("channel") != "attack":
                # attack may encode decision differently
                bad(f"parsed_commands({n}) != proposed({p}) for {cid}")
    else:
        ok("parsed_commands aligned with proposed (non-attack)")

    # opening metadata (P0-6)
    opening = [r for r in llm if r.get("channel") == "opening" and r.get("event_type") == "llm_call_started"]
    opening_meta_ok = True
    indexes = set()
    for r in opening:
        if r.get("phase") != "pregame":
            opening_meta_ok = False
            bad(f"opening missing phase=pregame: {r.get('llm_call_id')}")
        idx = r.get("opening_index")
        if idx not in {1, 2, 3}:
            opening_meta_ok = False
            bad(f"opening bad opening_index={idx}")
        else:
            indexes.add(idx)
        if r.get("agent_id") == "shared_opening" and r.get("agent_side") in {"red", "blue"}:
            opening_meta_ok = False
            bad("shared_opening must not use agent_side red/blue")
        if r.get("agent_id") == "shared_opening" and r.get("feature_scope") != "audit_only":
            opening_meta_ok = False
            bad("shared_opening must be audit_only")
    if opening and indexes != {1, 2, 3} and len(opening) >= 3:
        opening_meta_ok = False
        bad(f"opening_index set incomplete: {sorted(indexes)}")
    if opening_meta_ok:
        ok("opening phase/index/owner ok")

    # command provenance (P0-4)
    by_cid: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    orphan = 0
    for r in cmd:
        cid = r.get("command_id")
        by_cid[cid][r.get("event_type")].append(r)
        if r.get("source") == "llm" and not r.get("llm_call_id"):
            orphan += 1
            bad(f"source=llm missing llm_call_id command={cid}")
    for cid, evs in by_cid.items():
        if "command_dispatched" in evs:
            if "command_proposed" not in evs or "command_accepted" not in evs:
                orphan += 1
                bad(f"dispatched without proposed/accepted: {cid}")
    if orphan == 0:
        ok("no orphan llm/dispatched commands")

    # terminal coverage for accepted (soft for running)
    status = manifest.get("status")
    if status in {"completed", "crashed", "aborted"}:
        hanging = 0
        for cid, evs in by_cid.items():
            if "command_accepted" in evs:
                terminals = {"command_dispatched", "command_succeeded", "command_failed", "command_skipped", "command_filtered"}
                # filtered is alternate path from proposed; accepted needs terminal
                if not (terminals & set(evs)):
                    hanging += 1
        if hanging:
            bad(f"accepted commands without terminal state: {hanging}")
        else:
            ok("accepted commands have terminal follow-up")

    # replay state machine (P0-1)
    saved_ev = [r for r in run_ev if r.get("event_type") == "replay_saved"]
    failed_ev = [r for r in run_ev if r.get("event_type") == "replay_save_failed"]
    replay = manifest.get("replay") or {}
    if replay.get("save_status") == "failed" and saved_ev:
        bad("save_status=failed but replay_saved event present")
    elif replay.get("save_status") == "failed":
        ok("failed replay has no replay_saved")
    if status == "completed":
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
            if not saved_ev:
                bad("completed run missing replay_saved event")
            else:
                ok("replay_saved event present")
            # Replay header alignment
            try:
                sys.path.insert(0, str(run_dir.parent.parent.parent if False else Path(__file__).resolve().parents[1]))
                from swarmbrain_replay_meta import LOOPS_PER_SECOND, parse_replay_file

                meta = parse_replay_file(path)
                if meta.get("parse_status") != "ok":
                    bad(f"replay header parse failed: {meta.get('parse_error')}")
                else:
                    ok("replay header parses")
                    if manifest.get("replay_final_game_loop") != meta.get("replay_final_game_loop"):
                        bad(
                            f"replay_final_game_loop mismatch manifest={manifest.get('replay_final_game_loop')} "
                            f"header={meta.get('replay_final_game_loop')}"
                        )
                    else:
                        ok("replay_final_game_loop matches header")
                    if manifest.get("base_build") != meta.get("base_build"):
                        bad(f"base_build mismatch manifest={manifest.get('base_build')} header={meta.get('base_build')}")
                    else:
                        ok("base_build matches replay")
                    if (manifest.get("data_version") or "").upper() != (meta.get("data_version") or "").upper():
                        bad("data_version mismatch vs replay")
                    else:
                        ok("data_version matches replay")
                    last_obs = manifest.get("last_observed_game_loop")
                    replay_final = manifest.get("replay_final_game_loop")
                    if last_obs is not None and replay_final is not None and last_obs == replay_final:
                        # Allowed but warn if equal by chance; fail if last_obs used without separate field historically
                        pass
                    if manifest.get("final_game_loop") not in {None, replay_final} and replay_final is not None:
                        # final_* should equal replay final when present
                        if manifest.get("final_game_loop") != replay_final:
                            bad("final_game_loop is not replay_final_game_loop")
                        else:
                            ok("final_game_loop aliases replay final")
                    else:
                        ok("final/replay loop fields coherent")
            except Exception as exc:
                bad(f"replay header validation error: {exc}")

            runtime_result = manifest.get("runtime_result") or manifest.get("result")
            replay_result = manifest.get("replay_result")

            def _norm_res(v: object) -> str:
                s = str(v).lower()
                for a, b in (
                    ("result.", ""),
                    ("victory", "win"),
                    ("defeat", "loss"),
                ):
                    s = s.replace(a, b)
                return s.strip()

            def _equiv(a, b) -> bool:
                if a == b:
                    return True
                if not isinstance(a, dict) or not isinstance(b, dict):
                    return _norm_res(a) == _norm_res(b)
                if set(a) != set(b):
                    return False
                return all(_norm_res(a[k]) == _norm_res(b[k]) for k in a)

            if runtime_result and replay_result and not _equiv(runtime_result, replay_result):
                warn(
                    f"runtime_result != replay_result "
                    f"runtime={runtime_result} replay={replay_result} "
                    f"termination_reason={manifest.get('termination_reason')} "
                    f"result_source={manifest.get('result_source')}"
                )
            elif runtime_result or replay_result:
                ok("runtime/replay result fields present")
    else:
        ok(f"status={status} (skip strict replay file requirement)")

    # Non-opening time completeness
    time_ok = True
    for r in llm:
        if r.get("channel") == "opening":
            continue
        if r.get("event_type") not in {"llm_call_started", "llm_call_completed", "llm_call_failed"}:
            continue
        if not isinstance(r.get("game_loop"), int):
            time_ok = False
            bad(f"non-opening missing game_loop: {r.get('event_type')} {r.get('llm_call_id')}")
        if r.get("game_time_sec") is None and r.get("event_type") != "llm_call_started":
            # started may have time from bot; prefer require for completed/failed
            time_ok = False
            bad(f"non-opening missing game_time_sec: {r.get('event_type')} {r.get('llm_call_id')}")
        if r.get("event_type") in {"llm_call_completed", "llm_call_failed"} and not r.get("game_time_formatted"):
            # soft: require formatted for terminal
            time_ok = False
            bad(f"non-opening missing game_time_formatted: {r.get('llm_call_id')}")
        gl = r.get("game_loop")
        gts = r.get("game_time_sec")
        if isinstance(gl, int) and isinstance(gts, (int, float)):
            expected = gl / 22.4
            if abs(float(gts) - expected) > 2.0:
                time_ok = False
                bad(f"game_time_sec inconsistent with loop for {r.get('llm_call_id')}")
    if time_ok:
        ok("non-opening LLM time fields complete")

    # Channel / agent coverage
    req_channels = [c.strip() for c in (args.require_channels or "").split(",") if c.strip()]
    req_agents = [a.strip() for a in (args.require_agents or "").split(",") if a.strip()]
    if req_channels:
        cover_ok = True
        for ch in req_channels:
            if ch == "opening":
                starts = [r for r in llm if r.get("event_type") == "llm_call_started" and r.get("channel") == "opening"]
                if len(starts) < 3:
                    cover_ok = False
                    bad(f"require opening x3 got {len(starts)}")
                continue
            for agent in req_agents or ["red", "blue"]:
                starts = [
                    r
                    for r in llm
                    if r.get("event_type") == "llm_call_started"
                    and r.get("channel") == ch
                    and r.get("agent_side") == agent
                ]
                if not starts:
                    cover_ok = False
                    bad(f"missing channel={ch} agent_side={agent}")
                for s in starts:
                    cid = s.get("llm_call_id")
                    if cid not in completed and cid not in failed:
                        cover_ok = False
                        bad(f"{ch}/{agent} call not terminal: {cid}")
        if cover_ok:
            ok(f"required channels covered: {req_channels} agents={req_agents or 'n/a'}")

    # errors (P0-7)
    for r in err:
        if not (r.get("sanitized_message") or r.get("source_file") or r.get("traceback_sha256")):
            bad(f"error missing locatable fields: {r.get('event_id')}")
        rec = r.get("recoverable")
        act = r.get("action_taken")
        if rec is False and act == "continue":
            bad("recoverable=false with action_taken=continue")
        if rec is True and act == "abort_run":
            bad("recoverable=true with action_taken=abort_run")
    if err:
        ok("error recoverable/action_taken checked")
    else:
        ok("no errors (skip error semantics)")

    # secrets scan
    blob = manifest_path.read_text(encoding="utf-8")
    for p in (run_dir / "llm_events.jsonl", run_dir / "command_events.jsonl", run_dir / "errors.jsonl"):
        if p.is_file():
            blob += p.read_text(encoding="utf-8")
    if SECRET_RE.search(blob):
        bad("possible secret pattern found in logs")
    else:
        ok("no common secret patterns found")

    # Async LLM/commands can interleave; enforce monotonicity within one call/command id.
    by_call: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in llm:
        cid = r.get("llm_call_id")
        gl = r.get("game_loop")
        if cid and isinstance(gl, int):
            by_call[str(cid)].append((str(r.get("event_type")), gl))
    call_loop_ok = True
    for cid, items in by_call.items():
        loops = [gl for _, gl in items]
        if loops != sorted(loops):
            call_loop_ok = False
            bad(f"game_loop went backwards within llm_call_id={cid}")
    if call_loop_ok:
        ok("game_loop non-decreasing within each llm_call_id")

    # Observation vs response_completed semantics (per llm_call_id).
    obs_resp_ok = True
    for cid, s in started.items():
        if s.get("channel") == "opening":
            continue
        c = completed.get(cid)
        if not c:
            continue
        obs = s.get("observation_game_loop", s.get("game_loop"))
        resp = c.get("response_completed_game_loop", c.get("game_loop"))
        started_alias = c.get("started_game_loop", c.get("observation_game_loop"))
        if isinstance(obs, int) and isinstance(started_alias, int) and obs != started_alias:
            obs_resp_ok = False
            bad(f"completed.started_game_loop != observation for {cid}")
        if isinstance(obs, int) and isinstance(resp, int) and resp < obs:
            obs_resp_ok = False
            bad(f"response_completed loop {resp} < observation {obs} for {cid}")
    if obs_resp_ok:
        ok("observation/response_completed time semantics ok")

    cmd_order = {
        "command_proposed": 0,
        "command_accepted": 1,
        "command_filtered": 1,
        "command_dispatched": 2,
        "command_succeeded": 3,
        "command_failed": 3,
        "command_skipped": 3,
    }
    by_cmd: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in cmd:
        cid = r.get("command_id")
        gl = r.get("game_loop")
        et = str(r.get("event_type") or "")
        if cid and isinstance(gl, int) and et in cmd_order:
            by_cmd[str(cid)].append((et, gl))
    cmd_loop_ok = True
    for cid, items in by_cmd.items():
        items_sorted = sorted(items, key=lambda x: (cmd_order.get(x[0], 99), x[1]))
        loops = [gl for _, gl in items_sorted]
        if any(loops[i] > loops[i + 1] for i in range(len(loops) - 1)):
            cmd_loop_ok = False
            bad(f"game_loop went backwards within command_id={cid}")
    if cmd_loop_ok:
        ok("game_loop non-decreasing within each command_id")

    # Required five files
    required_files = [
        "run_manifest.json",
        "run_events.jsonl",
        "llm_events.jsonl",
        "command_events.jsonl",
        "errors.jsonl",
    ]
    missing = [n for n in required_files if not (run_dir / n).is_file()]
    if missing:
        bad(f"missing required files: {missing}")
    else:
        ok("five required log files present")

    # strategic_plan checks (strategic_plan_fields_v0.1.0)
    try:
        from swarmbrain_strategic_plan import (
            PRIMARY_INTENTS,
            PARSE_STATUS_VALUES,
            SOURCE_VALUES,
            STRATEGIC_PLAN_SCHEMA_VERSION,
            should_attach_strategic_plan,
        )
    except Exception as exc:
        PRIMARY_INTENTS = set()
        PARSE_STATUS_VALUES = set()
        SOURCE_VALUES = set()
        STRATEGIC_PLAN_SCHEMA_VERSION = "strategic_plan_v0.1.0"
        should_attach_strategic_plan = None
        warn(f"strategic_plan module import failed: {exc}")

    sp_ok = True
    sp_required = 0
    sp_present = 0
    for cid, r in completed.items():
        ch = r.get("channel")
        fs = r.get("feature_scope") or "label_only"
        need = False
        if should_attach_strategic_plan is not None:
            need = should_attach_strategic_plan(channel=ch, feature_scope=fs)
        else:
            need = ch in {"macro", "building", "attack"} and fs == "label_only"
        if not need:
            continue
        sp_required += 1
        sp = r.get("strategic_plan")
        if not isinstance(sp, dict):
            sp_ok = False
            bad(f"missing strategic_plan on completed {cid} channel={ch}")
            continue
        sp_present += 1
        if sp.get("schema_version") != STRATEGIC_PLAN_SCHEMA_VERSION:
            sp_ok = False
            bad(f"bad strategic_plan.schema_version on {cid}")
        if sp.get("primary_intent") not in PRIMARY_INTENTS:
            sp_ok = False
            bad(f"illegal strategic_plan.primary_intent={sp.get('primary_intent')!r} on {cid}")
        if sp.get("primary_intent") in {"macro", "building", "attack"}:
            sp_ok = False
            bad(f"channel used as primary_intent on {cid}")
        if sp.get("source") not in SOURCE_VALUES:
            sp_ok = False
            bad(f"illegal strategic_plan.source on {cid}")
        if sp.get("parse_status") not in PARSE_STATUS_VALUES:
            sp_ok = False
            bad(f"illegal strategic_plan.parse_status on {cid}")
        conf = sp.get("intent_confidence")
        if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
            sp_ok = False
            bad(f"bad strategic_plan.intent_confidence on {cid}")
        if r.get("fallback_used") and sp.get("source") != "rule_fallback":
            sp_ok = False
            bad(f"fallback_used but strategic_plan.source!={sp.get('source')} on {cid}")
        if r.get("fallback_used") and isinstance(conf, (int, float)) and conf > 0.3:
            sp_ok = False
            bad(f"fallback strategic_plan confidence>{conf} on {cid}")
        if fs != "label_only":
            sp_ok = False
            bad(f"strategic_plan attached with feature_scope={fs} on {cid}")
    if sp_required:
        coverage = sp_present / sp_required
        if coverage < 0.95:
            sp_ok = False
            bad(f"strategic_plan coverage {coverage:.2%} < 95% ({sp_present}/{sp_required})")
        elif sp_ok:
            ok(f"strategic_plan coverage {sp_present}/{sp_required} and schema ok")
    else:
        ok("strategic_plan checks skipped (no in-game label_only completed calls)")

    # Channel / command / fallback stats
    channel_counts: dict[str, int] = defaultdict(int)
    for r in completed.values():
        channel_counts[str(r.get("channel") or "other")] += 1
    fallback_count = sum(1 for r in list(completed.values()) + list(failed.values()) if r.get("fallback_used"))
    cmd_stats = defaultdict(int)
    for r in cmd:
        cmd_stats[str(r.get("event_type") or "")] += 1

    # Pilot qualification (blocking when --pilot)
    if args.pilot:
        if status != "completed":
            bad(f"pilot requires status=completed got={status}")
        term = str(manifest.get("termination_reason") or "")
        if "test_time_limit" in term:
            bad(f"pilot rejects test_time_limit termination: {term}")
        runtime = manifest.get("runtime_result") or manifest.get("result") or {}
        replay_res = manifest.get("replay_result") or {}
        runtime_s = " ".join(str(v) for v in (runtime.values() if isinstance(runtime, dict) else [runtime]))
        replay_s = " ".join(str(v) for v in (replay_res.values() if isinstance(replay_res, dict) else [replay_res]))
        if "Undecided" in replay_s:
            bad("pilot rejects Undecided replay_result")
        has_win = any(x in runtime_s for x in ("Victory", "Win", "Defeat", "Loss"))
        # Tie alone is not enough for pilot qualified set
        if "Tie" in runtime_s and not has_win:
            bad("pilot rejects Tie-only runtime result")
        if not has_win and "Tie" not in runtime_s:
            # Also accept Result.Victory style
            has_win = any(x in runtime_s for x in ("Result.Victory", "Result.Defeat", "Result.Win", "Result.Loss"))
        if not has_win:
            bad(f"pilot requires Victory/Defeat runtime_result got={runtime}")
        rc = manifest.get("runtime_config") or {}
        if int(rc.get("attack_min_army") or -1) == 0:
            bad("pilot forbids attack_min_army=0 (smoke)")
        if int(rc.get("attack_every") or -1) == 40:
            bad("pilot forbids attack_every=40 (smoke)")
        if not manifest.get("config_name") or not manifest.get("config_sha256"):
            bad("pilot requires config_name and config_sha256 in manifest")
        # Both sides should have macro+building at minimum for a normal duel
        for agent in ("red", "blue"):
            for ch in ("macro", "building"):
                n = sum(
                    1
                    for r in completed.values()
                    if r.get("agent_side") == agent and r.get("channel") == ch
                )
                if n < 1:
                    bad(f"pilot missing {agent}/{ch} completed calls")
        if not any(msg.startswith("pilot") for msg in fails):
            ok("pilot qualification rules passed")
        # Note: earlier non-pilot fails still fail the process; this ok only covers pilot block.

    summary = {
        "run_id": run_id,
        "validation_status": "pass" if not fails else "fail",
        "error_count": len(fails),
        "warning_count": len(warnings),
        "failed_rules": list(fails),
        "warnings": list(warnings),
        "pass_count": passes,
        "replay_sha256": (manifest.get("replay") or {}).get("sha256"),
        "base_build": manifest.get("base_build"),
        "data_version": manifest.get("data_version"),
        "replay_final_game_loop": manifest.get("replay_final_game_loop"),
        "channel_counts": dict(channel_counts),
        "fallback_count": fallback_count,
        "failed_llm_count": len(failed),
        "command_lifecycle_stats": dict(cmd_stats),
        "termination_reason": manifest.get("termination_reason"),
        "config_name": manifest.get("config_name"),
        "config_sha256": manifest.get("config_sha256"),
        "runtime_result": manifest.get("runtime_result") or manifest.get("result"),
        "replay_result": manifest.get("replay_result"),
    }
    print(
        f"Summary: run_id={run_id} validation_status={summary['validation_status']} "
        f"pass={passes} fail={len(fails)} warn={len(warnings)}"
    )
    print(
        f"Stats: channels={dict(channel_counts)} fallback={fallback_count} "
        f"failed_llm={len(failed)} commands={dict(cmd_stats)}"
    )
    if args.summary_json:
        out = Path(args.summary_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote summary JSON: {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
