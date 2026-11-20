import unittest
from datetime import datetime, timedelta

from local_joint_debug import lock_hold_mtime
from local_model_live import (
    LIVE_TIMEOUT_S,
    decision_for_current_observation,
    match_runs_realtime,
    request_feedback_gap,
    request_is_due,
    should_send_model_request,
)
from local_rule_observation import build_observation
from local_rule_plan import drone_goal, model_production_targets, model_structures


class LiveModelScheduleTests(unittest.TestCase):
    def test_first_request_waits_until_the_opening_is_visible(self):
        self.assertFalse(request_is_due(89.9, None))
        self.assertTrue(request_is_due(90.0, None))

    def test_later_requests_keep_three_minutes_apart(self):
        self.assertFalse(request_is_due(269.9, 90.0))
        self.assertTrue(request_is_due(270.0, 90.0))

    def test_a_live_request_waits_for_a_long_generation_without_overlapping(self):
        self.assertEqual(LIVE_TIMEOUT_S, 1200.0)
        self.assertFalse(should_send_model_request(True, True))
        self.assertTrue(should_send_model_request(True, False))
        self.assertFalse(should_send_model_request(False, False))
        self.assertGreater(lock_hold_mtime(1_000.0, LIVE_TIMEOUT_S), 1_000.0 + 180)

    def test_request_feedback_gap_records_wall_time_and_game_time(self):
        start = datetime(2026, 11, 1, 19, 0, 0)
        gap = request_feedback_gap(start, start + timedelta(seconds=66.235), 90.0, 156.2)
        self.assertEqual(gap["latency_s"], 66.235)
        self.assertEqual(gap["game_time_delta_s"], 66.2)

    def test_a_live_model_game_keeps_real_time(self):
        self.assertTrue(match_runs_realtime(True, False))
        self.assertFalse(match_runs_realtime(False, False))
        self.assertTrue(match_runs_realtime(False, True))

    def test_a_low_drone_total_does_not_shrink_the_local_miner_plan(self):
        self.assertEqual(drone_goal(32, 19), 32)
        self.assertEqual(drone_goal(16, 40), 40)
        self.assertEqual(drone_goal(16, None), 16)

    def test_a_mixed_reply_keeps_the_actions_the_game_can_afford(self):
        drone_then_expand = (
            '{"actions": [{"type": "set_production_target", "unit": "DRONE", "target_count": 19},'
            ' {"type": "expand", "base_id": "A5"}]}'
        )
        observation = self._opening(minerals=150, larva=1, supply_left=4, drones=17, training=1)
        judged = decision_for_current_observation(
            {"decision": "拒绝", "content": drone_then_expand, "finish_reason": "stop", "reject_reasons": []},
            observation,
        )
        self.assertEqual(judged["decision"], "可执行")
        self.assertEqual(judged["actions_from_content"], [{"type": "set_production_target", "unit": "DRONE", "target_count": 19}])

        expand_then_lings = (
            '{"actions": [{"type": "expand", "base_id": "A5"},'
            ' {"type": "set_production_target", "unit": "ZERGLING", "target_count": 20}]}'
        )
        rich = self._opening(minerals=2440, larva=11, supply_left=2, drones=32, lings=12)
        judged = decision_for_current_observation(
            {"decision": "拒绝", "content": expand_then_lings, "finish_reason": "stop", "reject_reasons": []},
            rich,
        )
        self.assertEqual([action["type"] for action in judged["actions_from_content"]], ["expand"])

        no_larva = self._opening(minerals=3590, larva=0, supply_left=14, drones=16, training=4)
        drones_then_expand = (
            '{"actions": [{"type": "set_production_target", "unit": "DRONE", "target_count": 25},'
            ' {"type": "expand", "base_id": "A5"}]}'
        )
        judged = decision_for_current_observation(
            {"decision": "拒绝", "content": drones_then_expand, "finish_reason": "stop", "reject_reasons": []},
            no_larva,
        )
        self.assertEqual([action["type"] for action in judged["actions_from_content"]], ["expand"])

    def test_thinking_text_before_json_still_yields_the_action(self):
        content = (
            '先想一下基地。\n</think>\n\n'
            '{"actions": [{"type": "expand", "base_id": "A3"}]}'
        )
        judged = decision_for_current_observation(
            {"decision": "拒绝", "reject_reasons": ["content 不是完整 JSON"], "content": content, "finish_reason": "stop"},
            self._observation(minerals=500),
        )
        self.assertEqual(judged["actions_from_content"], [{"type": "expand", "base_id": "A3"}])

    def test_a_reply_is_rejected_again_when_the_game_has_moved(self):
        content = '{"actions":[{"type":"expand","base_id":"A3"}]}'
        record = {
            "decision": "可执行",
            "content": content,
            "finish_reason": "stop",
            "reject_reasons": [],
            "actions_from_content": [{"type": "expand", "base_id": "A3"}],
        }
        later = decision_for_current_observation(record, self._observation(minerals=200))
        self.assertEqual(later["decision"], "拒绝")
        self.assertEqual(later["actions_from_content"], [])
        refused = decision_for_current_observation(
            {"decision": "拒绝", "reject_reasons": ["content 为空"], "content": None, "finish_reason": "stop"},
            self._observation(minerals=500),
        )
        self.assertEqual(refused["actions_from_content"], [])

    def _opening(
        self,
        minerals: int,
        larva: int,
        supply_left: int,
        drones: int,
        training: int = 0,
        lings: int = 0,
    ) -> dict:
        units = [{"type": "DRONE", "count": drones}]
        if lings:
            units.append({"type": "ZERGLING", "count": lings})
        in_training = [{"type": "DRONE", "count": training}] if training else None
        return build_observation(
            game_id="live-test",
            map_name="AutomatonLE",
            observation_id="live-test-t90",
            game_time_s=90,
            game_loop=2016,
            plan_epoch=1,
            resources={
                "minerals": minerals,
                "vespene": 0,
                "supply_used": 18,
                "supply_cap": 22,
                "supply_left": supply_left,
                "larva": larva,
            },
            own_units=units,
            own_structures=[{"type": "HATCHERY", "count": 1, "positions": [[29.5, 65.5]]}, {"type": "SPAWNINGPOOL", "count": 1, "positions": [[35.5, 67.5]]}],
            own_start={"label": "A1", "side": "A", "position": [29.5, 65.5]},
            known_bases=[{"base_id": "A1", "controlled_by": "self", "position": [29.5, 65.5]}],
            candidate_expansions=[{"base_id": "A5", "controlled_by": "unknown", "position": [103.5, 113.5]}],
            sightings=[],
            plan_summary="保持本地计划",
            recent_feedback=[],
            own_in_training=in_training,
        )

    def _observation(self, minerals: int) -> dict:
        return build_observation(
            game_id="live-test",
            map_name="AutomatonLE",
            observation_id="live-test-t200",
            game_time_s=200,
            game_loop=4480,
            plan_epoch=1,
            resources={"minerals": minerals, "vespene": 0, "supply_used": 16, "supply_cap": 22, "supply_left": 6, "larva": 3},
            own_units=[{"type": "DRONE", "count": 16}],
            own_structures=[{"type": "HATCHERY", "count": 1, "positions": [[29.5, 65.5]]}],
            own_start={"label": "A1", "side": "A", "position": [29.5, 65.5]},
            known_bases=[{"base_id": "A1", "controlled_by": "self", "position": [29.5, 65.5]}],
            candidate_expansions=[{"base_id": "A3", "controlled_by": "unknown", "position": [56.5, 65.5]}],
            sightings=[],
            plan_summary="保持本地计划",
            recent_feedback=[],
        )

    def test_model_plan_can_be_shown_back_to_the_model(self):
        actions = [
            {"type": "expand", "base_id": "A3"},
            {"type": "set_production_target", "unit": "ZERGLING", "target_count": 12},
            {"type": "build_structure", "structure": "EXTRACTOR"},
        ]
        observation = build_observation(
            game_id="live-test",
            map_name="AutomatonLE",
            observation_id="live-test-t90",
            game_time_s=90,
            game_loop=2016,
            plan_epoch=2,
            resources={"minerals": 300, "vespene": 0, "supply_used": 16, "supply_cap": 22, "larva": 3},
            own_units=[{"type": "DRONE", "count": 16}],
            own_structures=[{"type": "HATCHERY", "count": 1, "positions": [[29.5, 65.5]]}],
            own_start={"label": "A1", "side": "A", "position": [29.5, 65.5]},
            known_bases=[{"base_id": "A1", "controlled_by": "self", "position": [29.5, 65.5]}],
            candidate_expansions=[{"base_id": "A3", "controlled_by": "unknown", "position": [56.5, 65.5]}],
            sightings=[],
            plan_summary="保持本地计划",
            recent_feedback=[],
            plan_source="model",
            plan_actions=actions,
            own_in_training=[{"type": "DRONE", "count": 2}],
        )
        self.assertEqual(observation["plan"]["source"], "model")
        self.assertEqual(observation["plan"]["actions"], actions)
        self.assertEqual(observation["own"]["in_training"], [{"type": "DRONE", "count": 2}])
        self.assertEqual(model_production_targets(actions), {"ZERGLING": 12})
        self.assertEqual(model_structures(actions)[0]["structure"], "EXTRACTOR")


if __name__ == "__main__":
    unittest.main()
