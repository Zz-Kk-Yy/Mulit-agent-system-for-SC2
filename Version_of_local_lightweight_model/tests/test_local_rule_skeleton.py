import json
import unittest
from pathlib import Path

from local_rule_config import ALLOWED_GOAL_TYPES, load_match_config
from local_rule_observation import build_observation, nearest_location_label, visible_enemy_counts
from local_rule_plan import (
    model_actions_from_record,
    model_army_goal,
    model_expand_base,
    nearest_open_base,
    point_is_held,
    should_expand,
    should_save_for_expand,
    worker_goal,
)

ROOT = Path(__file__).resolve().parents[1]


class LocalRuleSkeletonTests(unittest.TestCase):
    def test_match_config_uses_medium_and_config_file(self):
        config = load_match_config()
        self.assertEqual(config.difficulty, "Medium")
        self.assertEqual(config.own_race, "Zerg")
        self.assertEqual(config.opponent_race, "Terran")
        self.assertEqual(config.spawn_direction, "auto")
        self.assertEqual(config.map_name, "AutomatonLE")
        self.assertIn("A1", config.location_labels)
        self.assertIn("B1", config.location_labels)
        self.assertFalse(config.model_called)
        self.assertGreaterEqual(config.opening.queen_per_base, 1)
        self.assertEqual(config.opening.base_target, 2)
        self.assertEqual(config.opening.worker_target, 16)

    def test_spawn_label_comes_from_config_points(self):
        config = load_match_config()
        resolved = nearest_location_label((155.0, 115.0), config.location_labels)
        self.assertEqual(resolved["label"], "B1")
        self.assertEqual(resolved["side"], "B")

    def test_observation_omits_unseen_enemy_zeros(self):
        observation = build_observation(
            game_id="local-rule-test",
            map_name="AutomatonLE",
            observation_id="local-rule-test-t10",
            game_time_s=10,
            game_loop=224,
            plan_epoch=1,
            resources={"minerals": 50, "vespene": 0, "supply_used": 12, "supply_cap": 14, "larva": 3},
            own_units=[{"type": "DRONE", "count": 12}],
            own_structures=[{"type": "HATCHERY", "count": 1, "positions": [[29.5, 65.5]]}],
            own_start={"label": "A1", "side": "A", "position": [29.5, 65.5]},
            known_bases=[{"base_id": "A1", "controlled_by": "self", "position": [29.5, 65.5]}],
            candidate_expansions=[{"base_id": "B1", "controlled_by": "unknown", "position": [154.5, 114.5]}],
            sightings=[
                {
                    "type": "SCV",
                    "position": [40.0, 70.0],
                    "last_seen_game_time_s": 9.5,
                    "currently_visible": True,
                    "is_structure": False,
                }
            ],
            plan_summary="保持本地计划",
            recent_feedback=[],
        )
        self.assertEqual(observation["plan"]["actions"], [])
        self.assertEqual(tuple(observation["allowed_goal_types"]), ALLOWED_GOAL_TYPES)
        self.assertEqual(observation["enemy"]["visible_counts"], {"SCV": 1})
        self.assertNotIn("MARINE", observation["enemy"]["visible_counts"])
        encoded = json.dumps(observation)
        self.assertNotIn('"MARINE": 0', encoded)
        self.assertNotIn('"count": 0', encoded)

    def test_hidden_sightings_do_not_become_a_zero_or_a_current_count(self):
        counts = visible_enemy_counts(
            [
                {
                    "type": "MARINE",
                    "currently_visible": False,
                    "last_seen_game_time_s": 20,
                }
            ]
        )
        self.assertEqual(counts, {})

    def test_local_plan_expands_once_the_first_base_is_saturated(self):
        self.assertFalse(
            should_expand(
                base_target=2,
                committed_bases=1,
                workers=15,
                worker_target=16,
                pool_placed=True,
                under_attack=False,
            )
        )
        self.assertTrue(
            should_expand(
                base_target=2,
                committed_bases=1,
                workers=16,
                worker_target=16,
                pool_placed=True,
                under_attack=False,
            )
        )
        self.assertFalse(
            should_expand(
                base_target=2,
                committed_bases=1,
                workers=16,
                worker_target=16,
                pool_placed=True,
                under_attack=True,
            )
        )
        self.assertFalse(
            should_expand(
                base_target=2,
                committed_bases=2,
                workers=16,
                worker_target=16,
                pool_placed=True,
                under_attack=False,
            )
        )
        self.assertTrue(should_save_for_expand(True, 299))
        self.assertFalse(should_save_for_expand(True, 300))
        self.assertEqual(worker_goal(16, 2, 1), 16)
        self.assertEqual(worker_goal(16, 2, 2), 32)
        chosen = nearest_open_base((29.5, 65.5), [(80.5, 66.5), (154.5, 114.5)], [(29.5, 65.5)], 15)
        self.assertEqual(chosen, (80.5, 66.5))

    def test_server_actions_expand_a1_and_defend_a1(self):
        record = json.loads(
            (ROOT / "outputs" / "joint_debug" / "joint-20261101T175827" / "joint_debug_record.json").read_text(
                encoding="utf-8"
            )
        )
        actions = model_actions_from_record(record)
        self.assertEqual(model_expand_base(actions), "A1")
        self.assertEqual(model_army_goal(actions), ("defend", "A1"))
        self.assertTrue(point_is_held((29.5, 65.5), [(29.5, 65.5)], 15))
        self.assertFalse(point_is_held((29.5, 65.5), [(56.5, 65.5)], 15))

    def test_rule_entry_does_not_call_the_old_llm_client(self):
        bot_source = (ROOT / "local_rule_bot.py").read_text(encoding="utf-8")
        launch_source = (ROOT / "local_rule_launch.py").read_text(encoding="utf-8")
        for source in (bot_source, launch_source):
            self.assertNotIn("ChatOpenAI", source)
            self.assertNotIn("get_swarmbrain_llm_credentials", source)
            self.assertNotIn("swarmbrain_launch", source)


if __name__ == "__main__":
    unittest.main()
