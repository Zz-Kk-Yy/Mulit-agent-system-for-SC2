from pathlib import Path
import os
import unittest

from duel_runtime import build_duel_agent_specs, fallback_opening_plan, resolve_pve_settings


class DuelRuntimeTests(unittest.TestCase):
    def test_agent_specs_isolate_state_and_export_features_for_both(self):
        root = Path("workspace")

        red, blue = build_duel_agent_specs(root)

        self.assertEqual(red.agent_id, "red")
        self.assertEqual(blue.agent_id, "blue")
        self.assertNotEqual(red.dashboard_state_path, blue.dashboard_state_path)
        self.assertEqual(
            {red.dashboard_state_path.name, blue.dashboard_state_path.name},
            {"dashboard_state_red.json", "dashboard_state_blue.json"},
        )
        self.assertTrue(red.feature_map_enabled)
        self.assertTrue(blue.feature_map_enabled)

    def test_fallback_opening_plan_uses_supported_command_shape(self):
        plan = fallback_opening_plan()

        self.assertIn("(Drone, A1)->(Build)->(Hatchery, A2)", plan)
        self.assertIn("(Zergling, A1)->(Attack)->(B1)", plan)
        self.assertGreaterEqual(plan.count(")->("), 6)

    def test_both_clients_use_lower_resource_windowed_mode(self):
        red, blue = build_duel_agent_specs(Path("workspace"))

        self.assertFalse(red.fullscreen)
        self.assertFalse(blue.fullscreen)

    def test_pve_defaults_to_computer_terran_medium(self):
        os.environ.pop("SWARMBRAIN_PVE_OPPONENT", None)
        os.environ.pop("SWARMBRAIN_PVE_RACE", None)
        os.environ.pop("SWARMBRAIN_PVE_DIFFICULTY", None)
        settings = resolve_pve_settings()
        self.assertEqual(settings.opponent, "computer")
        self.assertEqual(settings.computer_race, "Terran")
        self.assertEqual(settings.difficulty, "Medium")

    def test_pve_opponent_human_override(self):
        os.environ["SWARMBRAIN_PVE_OPPONENT"] = "human"
        os.environ["SWARMBRAIN_PVE_HUMAN_RACE"] = "Zerg"
        try:
            settings = resolve_pve_settings()
            self.assertEqual(settings.opponent, "human")
            self.assertEqual(settings.human_race, "Zerg")
        finally:
            os.environ.pop("SWARMBRAIN_PVE_OPPONENT", None)
            os.environ.pop("SWARMBRAIN_PVE_HUMAN_RACE", None)


if __name__ == "__main__":
    unittest.main()
