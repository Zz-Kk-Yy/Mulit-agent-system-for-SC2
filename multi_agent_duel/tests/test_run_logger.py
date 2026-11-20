"""Unit tests for SwarmBrainRunLogger (review P0 fixes)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from swarmbrain_run_logger import LoggedCommand, SwarmBrainRunLogger, sanitize_text
from swarmbrain_run_log_hooks import begin_llm_context, end_llm_context, extract_raw_commands


class RunLoggerTests(unittest.TestCase):
    def test_sanitize_redacts_sk_keys(self):
        text = "key=sk-LL0d5E7OrLr0EiSwkVskbyLJkSIPnsXiYugp4r3J8Gzu656U"
        out = sanitize_text(text)
        self.assertNotIn("sk-LL0d5E7", out)
        self.assertIn("REDACTED", out)

    def test_replay_fail_writes_save_failed_not_saved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = SwarmBrainRunLogger(
                package_root=root, mode="multi_agent_duel", log_root=root / "runs", model_name="m"
            )
            logger.attach_replay(None, save_error="boom")
            logger.finalize("crashed")
            run_types = [
                json.loads(l)["event_type"]
                for l in (logger.run_dir / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            self.assertIn("replay_save_requested", run_types)
            self.assertIn("replay_save_failed", run_types)
            self.assertNotIn("replay_saved", run_types)
            manifest = json.loads((logger.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["replay"]["save_status"], "failed")

    def test_identity_frozen_and_parsed_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = SwarmBrainRunLogger(
                package_root=root, mode="multi_agent_duel", log_root=root / "runs", model_name="m"
            )
            # Simulate shared defaults pollution attempt
            logger.set_agent_defaults(agent_id="blue", agent_side="blue", player_id=2)
            call_id = logger.start_llm_call(
                channel="macro",
                prompt="p",
                agent_id="red",
                agent_side="red",
                player_id=1,
                race="Zerg",
                game_loop=10,
            )
            logger.stash_llm_response(call_id, raw_response="(Drone, A1)->(Build)->(Pool, A1)")
            # pollute again before complete
            logger.set_agent_defaults(agent_id="blue", agent_side="blue", player_id=2)
            cmds = ["(Drone, A1)->(Build)->(Pool, A1)"]
            logger.complete_llm_call(call_id, parsed_commands=cmds, commands_expected=True, game_loop=12)
            accepted = logger.log_command_pipeline(
                llm_call_id=call_id,
                channel="macro",
                proposed=cmds,
                accepted=cmds,
                agent_id="red",
                agent_side="red",
                player_id=1,
                game_loop=12,
            )
            self.assertEqual(len(accepted), 1)
            self.assertIsInstance(accepted[0], LoggedCommand)
            logger.log_command_terminal(accepted[0], "command_dispatched", game_loop=13)
            logger.log_command_terminal(accepted[0], "command_succeeded", game_loop=13)

            llm = [
                json.loads(l)
                for l in (logger.run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            started = next(x for x in llm if x["event_type"] == "llm_call_started")
            completed = next(x for x in llm if x["event_type"] == "llm_call_completed")
            self.assertEqual(started["player_id"], 1)
            self.assertEqual(completed["player_id"], 1)
            self.assertEqual(completed["agent_side"], "red")
            self.assertEqual(completed["parse_status"], "success_nonempty")
            self.assertEqual(completed["parsed_commands"], cmds)

            cmd = [
                json.loads(l)
                for l in (logger.run_dir / "command_events.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            dispatched = [x for x in cmd if x["event_type"] == "command_dispatched"][0]
            self.assertEqual(dispatched["llm_call_id"], call_id)
            self.assertEqual(dispatched["player_id"], 1)

    def test_opening_shared_owner(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = SwarmBrainRunLogger(
                package_root=root, mode="multi_agent_duel", log_root=root / "runs", model_name="m"
            )
            cid = logger.start_llm_call(
                channel="opening",
                prompt="x",
                agent_id="shared_opening",
                agent_side="shared",
                player_id=None,
                race=None,
                feature_scope="audit_only",
                phase="pregame",
                opening_index=1,
                game_loop=None,
            )
            logger.complete_llm_call(cid, raw_response="ok", parse_status="not_applicable", commands_expected=False)
            row = json.loads((logger.run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["agent_side"], "shared")
            self.assertEqual(row["phase"], "pregame")
            self.assertEqual(row["opening_index"], 1)
            self.assertIsNone(row["player_id"])

    def test_observation_vs_response_time_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = SwarmBrainRunLogger(
                package_root=root, mode="multi_agent_duel", log_root=root / "runs", model_name="m"
            )
            call_id = logger.start_llm_call(
                channel="macro",
                prompt="p",
                agent_id="red",
                agent_side="red",
                player_id=1,
                game_loop=100,
                observation_game_loop=100,
                request_started_game_loop=110,
            )
            logger.note_game_clock(200, 200 / 22.4)
            logger.stash_llm_response(call_id, raw_response="ok")
            logger.complete_llm_call(call_id, parsed_commands=[], parse_status="success_empty", commands_expected=True)
            rows = [
                json.loads(l)
                for l in (logger.run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            started = next(x for x in rows if x["event_type"] == "llm_call_started")
            completed = next(x for x in rows if x["event_type"] == "llm_call_completed")
            self.assertEqual(started["game_loop"], 100)
            self.assertEqual(started["observation_game_loop"], 100)
            self.assertEqual(started["request_started_game_loop"], 110)
            self.assertEqual(completed["started_game_loop"], 100)
            self.assertEqual(completed["observation_game_loop"], 100)
            self.assertGreaterEqual(completed["response_completed_game_loop"], 100)
            self.assertEqual(completed["game_loop"], completed["response_completed_game_loop"])

    def test_strategic_plan_success_and_illegal_enum(self):
        from swarmbrain_strategic_plan import parse_strategic_plan_from_response, normalize_strategic_plan

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = SwarmBrainRunLogger(
                package_root=root, mode="multi_agent_duel", log_root=root / "runs", model_name="m"
            )
            raw = """
analysis...
```json
{"strategic_plan": {"primary_intent": "tech_climb", "secondary_intents": ["fast_expand"], "intent_confidence": 0.62, "intent_rationale_short": "unlock Hydralisk Den", "horizon_sec": 90}}
```
"""
            call_id = logger.start_llm_call(
                channel="building",
                prompt="p",
                agent_id="red",
                agent_side="red",
                player_id=1,
                feature_scope="label_only",
                game_loop=100,
            )
            logger.stash_llm_response(call_id, raw_response=raw)
            logger.complete_llm_call(
                call_id,
                parsed_commands=["(Drone, A1)->(Build)->(Hydralisk Den, A1)"],
                commands_expected=True,
                game_loop=120,
            )
            rows = [
                json.loads(l)
                for l in (logger.run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            completed = next(x for x in rows if x["event_type"] == "llm_call_completed")
            sp = completed["strategic_plan"]
            self.assertEqual(sp["primary_intent"], "tech_climb")
            self.assertEqual(sp["parse_status"], "success")
            self.assertEqual(sp["source"], "llm_structured")
            self.assertEqual(completed["feature_scope"], "label_only")
            self.assertEqual(completed["channel"], "building")

            bad_plan, err = normalize_strategic_plan(
                {
                    "primary_intent": "not_a_real_intent",
                    "secondary_intents": [],
                    "intent_confidence": 0.9,
                    "intent_rationale_short": "x",
                }
            )
            self.assertIsNotNone(err)
            self.assertEqual(bad_plan["parse_status"], "failed")
            self.assertEqual(bad_plan["primary_intent"], "unknown")

            # Missing block -> failed unknown, not invented high confidence
            missing = parse_strategic_plan_from_response("no plan here")
            self.assertEqual(missing["parse_status"], "failed")
            self.assertEqual(missing["primary_intent"], "unknown")
            self.assertLessEqual(missing["intent_confidence"], 0.3)

            # fallback_used path
            call2 = logger.start_llm_call(
                channel="macro",
                prompt="p2",
                agent_id="blue",
                agent_side="blue",
                player_id=2,
                feature_scope="label_only",
                game_loop=200,
            )
            logger.stash_llm_response(call2, raw_response="fallback text")
            logger.complete_llm_call(call2, parsed_commands=[], parse_status="success_empty", fallback_used=True)
            rows2 = [
                json.loads(l)
                for l in (logger.run_dir / "llm_events.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            fb = [x for x in rows2 if x.get("llm_call_id") == call2][-1]
            self.assertEqual(fb["strategic_plan"]["source"], "rule_fallback")
            self.assertLessEqual(fb["strategic_plan"]["intent_confidence"], 0.3)

    def test_strategic_plan_multi_motif_becomes_ambiguous(self):
        from swarmbrain_strategic_plan import parse_strategic_plan_from_response

        raw = """
```json
{"strategic_plan": {"primary_intent": "fast_expand", "secondary_intents": [], "intent_confidence": 0.8, "intent_rationale_short": "expand", "horizon_sec": 60}}
```
"""
        cmds = [
            "(Drone, A1)->(Build)->(Hatchery, A2)",
            "(Drone, A1)->(Build)->(Hydralisk Den, A1)",
            "(Drone, A1)->(Build)->(Spire, A1)",
        ]
        plan = parse_strategic_plan_from_response(raw, channel="building", parsed_commands=cmds)
        self.assertEqual(plan["primary_intent"], "ambiguous")
        self.assertEqual(plan["source"], "llm_text_parsed")
        self.assertIn("fast_expand", plan.get("secondary_intents") or [])


if __name__ == "__main__":
    unittest.main()
