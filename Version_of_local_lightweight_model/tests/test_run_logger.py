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

    def test_context_hooks_extract_commands(self):
        cmds = extract_raw_commands("x (Drone, A1)->(Build)->(Pool, A1) y")
        self.assertEqual(cmds, ["(Drone, A1)->(Build)->(Pool, A1)"])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = SwarmBrainRunLogger(package_root=root, mode="single_agent_pve", log_root=root / "runs")
            token = begin_llm_context(logger, channel="macro", agent_id="player_1", agent_side="single", player_id=1)
            self.assertIsNotNone(token)
            end_llm_context(token, auto_complete=False)


if __name__ == "__main__":
    unittest.main()
