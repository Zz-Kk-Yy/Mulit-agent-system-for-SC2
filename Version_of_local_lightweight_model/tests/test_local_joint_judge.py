import json
import unittest
from pathlib import Path

from local_joint_debug import completion_max_tokens
from local_joint_judge import ACTION_PROTOCOL, build_chat_request, judge_reply
from local_rule_observation import compress_observation

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "outputs" / "local_rule" / "local-rule-20261101T141120" / "observation_sample.json"
FINAL = ROOT / "outputs" / "local_rule" / "local-rule-20261101T141120" / "observation_final.json"
FOURTH = ROOT / "outputs" / "joint_debug" / "joint-20261101T160031" / "joint_debug_record.json"


class JointJudgeTests(unittest.TestCase):
    def setUp(self):
        self.observation = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def test_request_has_only_the_three_contract_fields(self):
        request = build_chat_request(self.observation)
        self.assertEqual(set(request), {"model", "messages", "max_tokens"})
        self.assertEqual(request["model"], "swarmbrain-qwen3-4b")
        self.assertEqual(request["max_tokens"], 2048)
        self.assertNotIn("stop", request)
        self.assertNotIn("thinking_token_budget", request)
        self.assertNotIn("request_id", request)
        self.assertIn("使用 unit 和正整数 target_count", ACTION_PROTOCOL)
        self.assertIn("目标总数，不是这次要新造的数量", ACTION_PROTOCOL)
        self.assertIn("使用 mode 和 region_id", ACTION_PROTOCOL)
        self.assertNotIn("使用 count", ACTION_PROTOCOL)
        self.assertNotIn("使用 target", ACTION_PROTOCOL)

    def test_empty_actions_are_executable_but_not_a_decision(self):
        judged = judge_reply(self.observation, '{"actions": []}', "stop")
        self.assertEqual(judged["decision"], "可执行")
        self.assertEqual(judged["hold_plan_count"], 1)
        self.assertEqual(judged["valid_decision_count"], 0)
        self.assertFalse(judged["executed"])

    def test_reasoning_content_is_not_a_source_of_actions(self):
        judged = judge_reply(self.observation, "   ", "stop")
        self.assertEqual(judged["decision"], "拒绝")
        self.assertIn("content 为空", judged["reject_reasons"])
        self.assertEqual(judged["actions_from_content"], [])

    def test_length_and_broken_json_are_rejected(self):
        self.assertIn("content 不是完整 JSON", judge_reply(self.observation, "不是 JSON", "stop")["reject_reasons"])
        length = judge_reply(self.observation, '{"actions": []}', "length")
        self.assertEqual(length["decision"], "拒绝")
        self.assertIn("finish_reason 为 length", length["reject_reasons"])
        self.assertEqual(length["hold_plan_count"], 0)

    def test_too_many_or_unknown_goals_are_rejected(self):
        too_many = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "set_army_goal", "goal": "defend"}] * 4}),
            "stop",
        )
        self.assertIn("目标数超过 3", too_many["reject_reasons"])
        unknown = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "attack_now"}]}),
            "stop",
        )
        self.assertTrue(any("协议之外的类型" in reason for reason in unknown["reject_reasons"]))

    def test_known_structure_and_expand_use_the_observation(self):
        pool = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "build_structure", "structure": "SPAWNINGPOOL"}]}),
            "stop",
        )
        self.assertEqual(pool["decision"], "拒绝")
        self.assertTrue(any("已经有了" in reason for reason in pool["reject_reasons"]))
        queen = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "set_production_target", "unit": "QUEEN", "target_count": 1}]}),
            "stop",
        )
        self.assertEqual(queen["decision"], "可执行")
        self.assertEqual(queen["valid_decision_count"], 1)
        self.assertFalse(queen["executed"])
        poor = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "expand", "base_id": "A5"}, {"type": "expand", "base_id": "A2"}]}),
            "stop",
        )
        self.assertEqual(poor["decision"], "拒绝")
        self.assertTrue(any("资源" in reason for reason in poor["reject_reasons"]))

    def test_production_rejects_count_and_target_names(self):
        old_count = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "set_production_target", "unit": "QUEEN", "count": 1}]}),
            "stop",
        )
        old_target = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "set_production_target", "target": "DRONE"}]}),
            "stop",
        )
        self.assertEqual(old_count["decision"], "拒绝")
        self.assertEqual(old_target["decision"], "拒绝")
        self.assertTrue(any("target_count" in reason for reason in old_count["reject_reasons"]))
        self.assertTrue(any("target_count" in reason for reason in old_target["reject_reasons"]))

    def test_army_goal_uses_mode_and_region_id(self):
        accepted = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "set_army_goal", "mode": "defend", "region_id": "A1"}]}),
            "stop",
        )
        old_goal = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "set_army_goal", "goal": "defend"}]}),
            "stop",
        )
        self.assertEqual(accepted["decision"], "可执行")
        self.assertEqual(accepted["valid_decision_count"], 1)
        self.assertFalse(accepted["executed"])
        self.assertEqual(old_goal["decision"], "拒绝")

    def test_fourth_reply_counts_target_as_a_total_and_is_not_executed(self):
        record = json.loads(FOURTH.read_text(encoding="utf-8"))
        judged = judge_reply(self.observation, record["content"], record["finish_reason"])
        self.assertEqual(judged["decision"], "可执行")
        self.assertEqual(judged["valid_decision_count"], 2)
        self.assertEqual(
            judged["actions_from_content"],
            [
                {"type": "set_production_target", "unit": "DRONE", "target_count": 4},
                {"type": "expand", "base_id": "A5"},
            ],
        )
        self.assertFalse(judged["executed"])

    def test_drone_total_of_20_rejects_when_the_gap_exceeds_larva(self):
        judged = judge_reply(
            self.observation,
            json.dumps({"actions": [{"type": "set_production_target", "unit": "DRONE", "target_count": 20}]}),
            "stop",
        )
        self.assertEqual(judged["decision"], "拒绝")
        self.assertTrue(any("资源" in reason for reason in judged["reject_reasons"]))
        self.assertFalse(judged["executed"])
        self.assertEqual(judged["actions_from_content"], [])

    def test_completion_limit_follows_the_context_room(self):
        self.assertEqual(completion_max_tokens(3744), 69696)
        self.assertEqual(completion_max_tokens(2372), 71068)
        with self.assertRaises(ValueError):
            completion_max_tokens(73440)
        request = build_chat_request(self.observation, max_tokens=4448)
        self.assertEqual(request["max_tokens"], 4448)
        self.assertEqual(set(request), {"model", "messages", "max_tokens"})

    def test_final_observation_drops_enemy_tracks_that_are_not_visible(self):
        final = json.loads(FINAL.read_text(encoding="utf-8"))
        compressed = compress_observation(final)
        self.assertEqual(compressed["game"]["game_time_s"], 749.3)
        self.assertEqual(compressed["resources"], final["resources"])
        self.assertEqual(compressed["own"], final["own"])
        self.assertEqual(compressed["enemy"]["visible_counts"], final["enemy"]["visible_counts"])
        self.assertLess(len(compressed["enemy"]["sightings"]), len(final["enemy"]["sightings"]))
        self.assertTrue(all(row["currently_visible"] is True for row in compressed["enemy"]["sightings"]))
        self.assertEqual(len(compressed["enemy"]["sightings"]), sum(final["enemy"]["visible_counts"].values()))


if __name__ == "__main__":
    unittest.main()
