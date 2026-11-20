import os

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import re
import asyncio
import random
import threading
from collections import Counter
from typing import Any, Union
from datetime import datetime
from pathlib import Path
from textwrap import shorten

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.constants import *
from sc2.data import Race, Difficulty, ActionResult
from sc2.main import run_game
from sc2.player import Bot, Computer, Human
from sc2.unit import Unit
from sc2.position import Point2, Point3
from sc2.units import Units

from feature_map_exporter import build_live_feature_map_exporter, enable_feature_layer_join_patch
from dashboard_state import DashboardStateWriter
from duel_runtime import build_duel_agent_specs, fallback_opening_plan, resolve_pve_settings
from swarmbrain import *
from swarmbrain_llm_shared import get_swarmbrain_llm_credentials
from swarmbrain_run_logger import SwarmBrainRunLogger
from swarmbrain_run_log_hooks import (
    begin_llm_context,
    complete_llm_with_commands,
    end_llm_context,
    enqueue_logged_commands,
    extract_raw_commands,
    log_attack_decision_commands,
    log_dispatched_logged_commands,
    log_parsed_command_batch,
)
# SC2 安装路径：
# - 推荐：在 PowerShell 里显式设置 $env:SC2PATH
# - 备用：可用 $env:SWARMBRAIN_SC2PATH 覆盖默认值
os.environ.setdefault(
    "SC2PATH",
    os.getenv("SWARMBRAIN_SC2PATH", r"E:\star_carft\StarCraft II"),
)
enable_feature_layer_join_patch()

ROOT_DIR = Path(__file__).resolve().parent
DASHBOARD_STATE_PATH = ROOT_DIR / "dashboard_state.json"
MINERAL_LOCATION_LABELS_REVERSE = {
    "A1": (29.5, 65.5),
    "A2": (35.5, 34.5),
    "A3": (56.5, 65.5),
    "A4": (63.5, 26.5),
    "A5": (80.5, 66.5),
    "A6": (98.5, 26.5),
    "A7": (129.5, 28.5),
    "A8": (33.5, 105.5),
    "B1": (154.5, 114.5),
    "B2": (148.5, 145.5),
    "B3": (127.5, 114.5),
    "B4": (120.5, 153.5),
    "B5": (103.5, 113.5),
    "B6": (85.5, 153.5),
    "B7": (54.5, 151.5),
    "B8": (150.5, 74.5),
}
MINERAL_LOCATION_LABELS = {
    coords: label for label, coords in MINERAL_LOCATION_LABELS_REVERSE.items()
}


def _unit_types(*names: str) -> set:
    found = set()
    for name in names:
        tid = getattr(UnitTypeId, name, None)
        if tid is not None:
            found.add(tid)
    return found


COMBAT_UNIT_TYPES = _unit_types(
    "ZERGLING", "BANELING", "ROACH", "RAVAGER", "HYDRALISK", "INFESTOR",
    "SWARMHOSTMP", "MUTALISK", "CORRUPTOR", "VIPER", "ULTRALISK", "BROODLORD",
    "LURKERMP", "LURKERMPBURROWED",
)
MID_TIER_UNIT_TYPES = _unit_types(
    "ROACH", "RAVAGER", "HYDRALISK", "MUTALISK", "CORRUPTOR", "VIPER",
    "ULTRALISK", "BROODLORD", "LURKERMP", "LURKERMPBURROWED", "INFESTOR",
)
ENEMY_COMBAT_UNIT_TYPES = _unit_types(
    "MARINE", "MARAUDER", "REAPER", "GHOST", "HELLION", "HELLIONTANK",
    "WIDOWMINE", "WIDOWMINEBURROWED", "SIEGETANK", "SIEGETANKSIEGED",
    "THOR", "CYCLONE", "VIKINGFIGHTER", "VIKINGASSAULT", "MEDIVAC", "RAVEN",
    "BANSHEE", "BATTLECRUISER", "LIBERATOR", "LIBERATORAG",
)
ENEMY_HEAVY_UNIT_TYPES = _unit_types(
    "SIEGETANK", "SIEGETANKSIEGED", "THOR", "BATTLECRUISER", "LIBERATOR",
    "LIBERATORAG", "RAVEN",
)

# 采集/菌毯仍由规则网接管，避免和大模型抢工蜂。作战与侦察指令必须放行。
REFLEX_OWNED_COMMAND_BANS = (
    "Gather minerals",
    "Extractor",
    "Mineral",
    "Creep",
)

COMBAT_ACTOR_TYPES = (
    ("Hydralisk", _unit_types("HYDRALISK")),
    ("Mutalisk", _unit_types("MUTALISK")),
    ("Corruptor", _unit_types("CORRUPTOR")),
    ("Ultralisk", _unit_types("ULTRALISK")),
    ("Infestor", _unit_types("INFESTOR")),
    ("Baneling", _unit_types("BANELING")),
    ("Ravager", _unit_types("RAVAGER")),
    ("Lurker", _unit_types("LURKERMP", "LURKERMPBURROWED")),
    ("Viper", _unit_types("VIPER")),
    ("Zergling", _unit_types("ZERGLING")),
    ("Roach", _unit_types("ROACH")),
)


class SwarmBrain(BotAI):
    raw_affects_selection = False

    def __init__(
        self,
        command_first,
        early_values,
        mid_values,
        late_values,
        drone_attack,
        counterattack,
        *,
        agent_id="red",
        display_name="SwarmBrain",
        dashboard_state_path=None,
        feature_map_enabled=True,
        run_logger=None,
        agent_side=None,
        player_id=None,
    ):
        super().__init__()
        self.agent_id = str(agent_id)
        self.agent_side = str(agent_side or agent_id)
        self.player_id = player_id
        self.display_name = str(display_name)
        self.run_logger = run_logger
        # print("early_values", early_values)
        # print("mid_values", mid_values)
        # print("late_values", late_values)
        self.early_zergling_num, self.early_baneling_num, self.early_roach_num, self.early_ravager_num, self.early_hydralisk_num, self.early_infestor_num, self.early_swarm_host_num, self.early_mutalisk_num, self.early_corruptor_num, self.early_viper_num, self.early_ultralisk_num, self.early_brood_lord_num = early_values
        self.mid_zergling_num, self.mid_baneling_num, self.mid_roach_num, self.mid_ravager_num, self.mid_hydralisk_num, self.mid_infestor_num, self.mid_swarm_host_num, self.mid_mutalisk_num, self.mid_corruptor_num, self.mid_viper_num, self.mid_ultralisk_num, self.mid_brood_lord_num = mid_values
        self.late_zergling_num, self.late_baneling_num, self.late_roach_num, self.late_ravager_num, self.late_hydralisk_num, self.late_infestor_num, self.late_swarm_host_num, self.late_mutalisk_num, self.late_corruptor_num, self.late_viper_num, self.late_ultralisk_num, self.late_brood_lord_num = late_values

        # print("self.early_hydralisk_num", self.early_hydralisk_num)
        # print("self.mid_hydralisk_num", self.mid_hydralisk_num)
        # print("self.late_hydralisk_num", self.late_hydralisk_num)

        self.start_location_label = None
        self.parsed_commands = []
        self.logged_command_queue = []
        self._command_birth_time = {}
        self._command_ttl_sec = float(os.getenv("SWARMBRAIN_COMMAND_TTL_SEC", "45") or "45")

        self.command_list = command_first
        self.opening_logged_commands = []
        self.building_tasks = []
        self.attack_tasks = []
        self.macro_tasks = []

        self.mineral_location_labels = dict(MINERAL_LOCATION_LABELS)
        self.mineral_location_labels_reverse = dict(MINERAL_LOCATION_LABELS_REVERSE)

        self.existing_hatchery_locations = []

        self.worker_aggressive = drone_attack
        self.counterattack = counterattack

        self.hatchery_queen_pairs = {}
        self.waiting_for_hatchery = False
        self.is_attack_command_issued = False

        self.previous_enemy_units = {}
        self.previous_enemy_units_attack = {}
        self.previous_enemy_damage = 0
        self.previous_enemy_buildings = {}
        self.previous_commands = []
        self._opponent_memory: dict[int, dict[str, Any]] = {}

        self.base_being_attacked = False
        self.defence = False

        self.spread_distance = 13

        self.game_stage = 0

        self.attack_wave = 0

        self.fight_back = False
        self.queen_spread_progress = {}
        self.macro_safety_net_enabled = os.getenv("SWARMBRAIN_DISABLE_MACRO_SAFETY_NET", "").strip().lower() not in (
            "1",
            "true",
            "yes",
            "on",
        )
        self.feature_map_exporter = (
            build_live_feature_map_exporter(self.agent_id) if feature_map_enabled else None
        )
        self.dashboard_state = DashboardStateWriter(
            ROOT_DIR,
            dashboard_state_path or DASHBOARD_STATE_PATH,
            game_embed_url=os.getenv("SWARMBRAIN_GAME_EMBED_URL", ""),
        )
        self.dashboard_state.update_map_layout(self.mineral_location_labels_reverse)
        self.dashboard_state.update_runtime(
            agentId=self.agent_id,
            agentName=self.display_name,
            side=self.agent_id,
        )
        self.opponent_dashboard_state = DashboardStateWriter(
            ROOT_DIR,
            ROOT_DIR / "dashboard_state_blue.json",
            game_embed_url=os.getenv("SWARMBRAIN_GAME_EMBED_URL", ""),
        )
        self.opponent_dashboard_state.update_map_layout(self.mineral_location_labels_reverse)
        self.opponent_dashboard_state.update_runtime(
            agentId="blue",
            agentName="蓝方对手",
            side="blue",
            perspective="opponent",
            perspectiveLabel="蓝方对手",
            perspectiveHint="从红方可见情报反演蓝方观察",
        )
        if self.run_logger is not None:
            self.run_logger.set_agent_defaults(
                agent_id=self.agent_id,
                agent_side=self.agent_side,
                player_id=self.player_id,
                race="Zerg",
            )

    async def on_start(self):
        #     # self.client.game_step = 2
        #     # await self.client.debug_show_map()
        start_location_key = tuple(self.start_location)
        # print("start_location_key ", start_location_key)
        self.start_location_label = self.mineral_location_labels.get(start_location_key)

        if self.start_location_label:
            print(f"Our starting location is at {self.start_location_label}")
        else:
            print("Could not determine the start location label.")

        if getattr(self, "realtime", False):
            print(
                "[sc2-ui] 实时对局：你可点小地图切视角、框选或热键控兵。机器人下一步下令可能盖掉你的指令。请勿点退出。",
                flush=True,
            )
        else:
            print(
                "[sc2-ui] 非实时对局：模型思考时游戏暂停，窗口通常只能拖镜头。",
                flush=True,
            )

        if self.feature_map_exporter:
            try:
                self.feature_map_exporter.configure_map_bounds(self.game_info)
            except Exception as exc:
                print(f"Feature map bounds setup failed: {exc}")
            print(f"Feature maps will be saved to {self.feature_map_exporter.output_dir}")

        self.command_list = self.filter_commands(self.command_list, list(REFLEX_OWNED_COMMAND_BANS))

        if self.start_location_label == "A1":
            Overmind_commands = self.command_list

        new_command_list = []
        if self.start_location_label == "B1":
            for command in self.command_list:
                def replace(match):
                    char, num = match.group(1), match.group(2)
                    return 'B' + num if char == 'A' else 'A' + num

                command = re.sub(r'(A|B)(\d+)', replace, command)
                new_command_list.append(command)

            Overmind_commands = new_command_list

        pattern = re.compile(r'\(([^)]+)\)')

        opening_logged = list(getattr(self, "opening_logged_commands", None) or [])
        if opening_logged:
            remap = self.start_location_label == "B1"
            enqueue_logged_commands(self, opening_logged, remap_ab=remap)
        else:
            for command in Overmind_commands:
                parts = pattern.findall(command)
                self.parsed_commands.append(parts)
        # print("self.parsed_commands:", self.parsed_commands)
        self._update_dashboard_runtime()
        self._sync_dashboard_commands()
        if self.run_logger is not None:
            try:
                self.run_logger.write_run_event(
                    "player_initialized",
                    agent_id=self.agent_id,
                    agent_side=self.agent_side,
                    player_id=getattr(self, "player_id", None),
                    race="Zerg",
                    feature_scope="label_only",
                    start_location_label=self.start_location_label,
                    map_name=str(getattr(getattr(self, "game_info", None), "map_name", "") or ""),
                    game_loop=0,
                )
            except Exception as exc:
                self.run_logger.write_error(
                    component="on_start",
                    operation="player_initialized",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    recoverable=True,
                    action_taken="continue",
                    exc=exc,
                )

    def _game_stage_label(self):
        stage_map = {0: "early", 1: "mid", 2: "late"}
        return stage_map.get(self.game_stage, "early")

    def _feature_session_path(self):
        if not self.feature_map_exporter:
            return ""
        try:
            return self.feature_map_exporter.output_dir.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            return self.feature_map_exporter.output_dir.as_posix()

    def _dashboard_unit_composition_rows(self, units_collection) -> list[dict[str, Any]]:
        """按 UnitTypeId 聚合单位种类与数量（用于态势感知面板）。"""
        if not units_collection:
            return []
        try:
            if len(units_collection) == 0:
                return []
        except TypeError:
            return []
        c: Counter[str] = Counter()
        for u in units_collection:
            try:
                tid = u.type_id.name
            except Exception:
                tid = "UNKNOWN"
            c[str(tid)] += 1
        return [{"name": k, "count": int(n)} for k, n in c.most_common(80)]

    def _dashboard_enemy_visible_composition_rows(self) -> list[dict[str, Any]]:
        """已侦察到的敌方单位与建筑（可见集合）。"""
        c: Counter[str] = Counter()
        for coll in (getattr(self, "enemy_units", None), getattr(self, "enemy_structures", None)):
            if not coll:
                continue
            try:
                if len(coll) == 0:
                    continue
            except TypeError:
                continue
            for u in coll:
                try:
                    tid = u.type_id.name
                except Exception:
                    tid = "UNKNOWN"
                c[str(tid)] += 1
        return [{"name": k, "count": int(n)} for k, n in c.most_common(80)]

    def _dashboard_world_percent(self, position: Any) -> tuple[float, float] | None:
        """把游戏世界坐标投到前端小地图百分比坐标。"""
        if position is None:
            return None
        try:
            x = float(getattr(position, "x"))
            y = float(getattr(position, "y"))
        except Exception:
            return None

        playable_area = getattr(getattr(self, "game_info", None), "playable_area", None)
        p0 = getattr(playable_area, "p0", None)
        p1 = getattr(playable_area, "p1", None)
        if p0 is not None and p1 is not None:
            origin_x = float(getattr(p0, "x", 0.0) or 0.0)
            origin_y = float(getattr(p0, "y", 0.0) or 0.0)
            width = max(1.0, float(getattr(p1, "x", origin_x + 1.0) or origin_x + 1.0) - origin_x)
            height = max(1.0, float(getattr(p1, "y", origin_y + 1.0) or origin_y + 1.0) - origin_y)
        else:
            width = max(1.0, float(getattr(getattr(self, "game_info", None), "map_size", Point2((200, 200))).x or 200))
            height = max(1.0, float(getattr(getattr(self, "game_info", None), "map_size", Point2((200, 200))).y or 200))
            origin_x = 0.0
            origin_y = 0.0

        x_percent = max(0.0, min(100.0, ((x - origin_x) / width) * 100.0))
        y_percent = max(0.0, min(100.0, (1.0 - ((y - origin_y) / height)) * 100.0))
        return round(x_percent, 3), round(y_percent, 3)

    def _dashboard_nearest_location_label(self, position: Any) -> str:
        if position is None:
            return "--"
        try:
            px = float(getattr(position, "x"))
            py = float(getattr(position, "y"))
        except Exception:
            return "--"
        best_label = "--"
        best_dist = float("inf")
        for mineral_position, location_label in self.mineral_location_labels.items():
            try:
                dx = px - float(getattr(mineral_position, "x", mineral_position[0]))
                dy = py - float(getattr(mineral_position, "y", mineral_position[1]))
            except Exception:
                continue
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best_dist = dist
                best_label = location_label
        return best_label

    def _dashboard_unit_action_label(self, unit: Any, side: str, kind: str) -> str:
        name = str(getattr(getattr(unit, "type_id", None), "name", "") or "").lower()
        if side == "enemy":
            return "当前可见"
        if kind == "structure":
            return "驻防/生产"
        if any(token in name for token in ("drone", "probe", "scv")):
            return "采集"
        if any(token in name for token in ("overlord", "overseer", "observer", "raven", "oracle")):
            return "侦察/探测"
        return "机动"

    def _dashboard_force_detail_rows(self, collections: list[Any], side: str, kind: str) -> list[dict[str, Any]]:
        """按实时单位位置输出地图标记数据；不再只输出 A/B 区域中心。"""
        grouped: dict[tuple[str, str, float, float, str], dict[str, Any]] = {}
        for units_collection in collections:
            if not units_collection:
                continue
            for unit in units_collection:
                position = getattr(unit, "position", None)
                percent = self._dashboard_world_percent(position)
                if percent is None:
                    continue
                try:
                    name = str(unit.type_id.name)
                except Exception:
                    name = "UNKNOWN"
                x_percent, y_percent = percent
                location = self._dashboard_nearest_location_label(position)
                action = self._dashboard_unit_action_label(unit, side, kind)
                key = (name, kind, round(x_percent, 1), round(y_percent, 1), action)
                if key not in grouped:
                    grouped[key] = {
                        "name": name,
                        "count": 0,
                        "location": location,
                        "action": action,
                        "kind": kind,
                        "xPercent": x_percent,
                        "yPercent": y_percent,
                        "worldX": round(float(getattr(position, "x", 0.0) or 0.0), 2),
                        "worldY": round(float(getattr(position, "y", 0.0) or 0.0), 2),
                    }
                grouped[key]["count"] += 1
        return sorted(
            grouped.values(),
            key=lambda row: (row["location"], row["kind"], row["name"], row["xPercent"], row["yPercent"]),
        )[:180]

    def _dashboard_resource_markers(self) -> list[dict[str, Any]]:
        markers: list[dict[str, Any]] = []
        minerals = getattr(self, "mineral_field", None)
        if not minerals:
            return markers
        for index, mineral in enumerate(minerals):
            position = getattr(mineral, "position", None)
            percent = self._dashboard_world_percent(position)
            if percent is None:
                continue
            x_percent, y_percent = percent
            location = self._dashboard_nearest_location_label(position)
            markers.append({
                "key": f"{location}-{index}",
                "label": location,
                "xPercent": x_percent,
                "yPercent": y_percent,
                "worldX": round(float(getattr(position, "x", 0.0) or 0.0), 2),
                "worldY": round(float(getattr(position, "y", 0.0) or 0.0), 2),
            })
        return markers

    def _dashboard_own_hp_sum(self) -> int:
        """我方单位总血量（含护盾），不包含敌方。"""
        if not self.units:
            return 0
        total = 0
        for u in self.units:
            try:
                total += int(getattr(u, "health", 0) or 0)
                total += int(getattr(u, "shield", 0) or 0)
            except Exception:
                pass
        return total

    def _update_dashboard_runtime(self, iteration: int | None = None):
        eu = getattr(self, "enemy_units", None)
        es = getattr(self, "enemy_structures", None)
        enemy_units_visible = int(eu.amount) if eu else 0
        enemy_structures_visible = int(es.amount) if es else 0
        self.dashboard_state.update_runtime(
            agentId=self.agent_id,
            agentName=self.display_name,
            side=self.agent_id,
            startLocation=self.start_location_label or "",
            timeFormatted=getattr(self, "time_formatted", ""),
            iteration=int(iteration) if iteration is not None else None,
            gameStage=self._game_stage_label(),
            armyCount=int(getattr(self, "army_count", 0)),
            workerCount=int(self.workers.amount) if self.workers else 0,
            supplyUsed=int(getattr(self, "supply_used", 0)),
            supplyCap=int(getattr(self, "supply_cap", 0)),
            pendingCommandCount=len(self.parsed_commands),
            featureSession=self._feature_session_path(),
            minerals=int(getattr(self, "minerals", 0)),
            vespene=int(getattr(self, "vespene", 0)),
            enemyUnitsVisible=enemy_units_visible,
            enemyStructuresVisible=enemy_structures_visible,
            ownUnitComposition=self._dashboard_unit_composition_rows(self.units),
            enemyVisibleComposition=self._dashboard_enemy_visible_composition_rows(),
            ownForceDetails=self._dashboard_force_detail_rows(
                [getattr(self, "units", None)], "own", "unit"
            ) + self._dashboard_force_detail_rows(
                [getattr(self, "structures", None)], "own", "structure"
            ),
            enemyVisibleDetails=self._dashboard_force_detail_rows(
                [getattr(self, "enemy_units", None)], "enemy", "unit"
            ) + self._dashboard_force_detail_rows(
                [getattr(self, "enemy_structures", None)], "enemy", "structure"
            ),
            resourceMarkers=self._dashboard_resource_markers(),
            ownHpTotal=self._dashboard_own_hp_sum(),
        )
        self._update_opponent_dashboard_runtime(iteration)

    def _iter_unit_collection(self, coll: Any):
        if not coll:
            return
        try:
            if len(coll) == 0:
                return
        except TypeError:
            return
        for unit in coll:
            yield unit

    def _unit_sight_range(self, unit: Any) -> float:
        for attr in ("sight_range", "detect_range"):
            raw = getattr(unit, attr, None)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                return value
        name = str(getattr(getattr(unit, "type_id", None), "name", "") or "").lower()
        if any(token in name for token in ("overlord", "observer", "overseer", "raven")):
            return 11.0
        if any(token in name for token in ("hatchery", "lair", "hive", "commandcenter", "orbital", "planetary", "nexus")):
            return 11.0
        return 9.0

    def _is_worker_name(self, name: str) -> bool:
        return str(name or "").upper() in {"SCV", "DRONE", "PROBE", "MULE"}

    def _refresh_opponent_memory(self) -> list[dict[str, Any]]:
        now = float(getattr(self, "time", 0.0) or 0.0)
        visible_tags: set[int] = set()
        for kind, coll in (
            ("unit", getattr(self, "enemy_units", None)),
            ("structure", getattr(self, "enemy_structures", None)),
        ):
            for unit in self._iter_unit_collection(coll):
                tag = int(getattr(unit, "tag", 0) or 0)
                if tag <= 0:
                    continue
                percent = self._dashboard_world_percent(getattr(unit, "position", None))
                if percent is None:
                    continue
                name = str(getattr(getattr(unit, "type_id", None), "name", "") or "UNKNOWN")
                position = getattr(unit, "position", None)
                snap = {
                    "tag": tag,
                    "name": name,
                    "kind": kind,
                    "location": self._dashboard_nearest_location_label(position),
                    "action": "当前可见",
                    "xPercent": percent[0],
                    "yPercent": percent[1],
                    "worldX": round(float(getattr(position, "x", 0.0) or 0.0), 2),
                    "worldY": round(float(getattr(position, "y", 0.0) or 0.0), 2),
                    "lastSeenTime": now,
                    "visible": True,
                    "stale": False,
                }
                self._opponent_memory[tag] = snap
                visible_tags.add(tag)
        for tag, snap in list(self._opponent_memory.items()):
            if tag in visible_tags:
                continue
            snap["visible"] = False
            last_seen = float(snap.get("lastSeenTime", 0.0) or 0.0)
            snap["stale"] = (now - last_seen) > 45.0
            snap["action"] = "上次目击" if not snap["stale"] else "情报过期"
        return list(self._opponent_memory.values())

    def _group_memory_force_rows(self, snaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple, dict[str, Any]] = {}
        for snap in snaps:
            key = (
                snap.get("name"),
                snap.get("kind"),
                round(float(snap.get("xPercent", 0.0) or 0.0), 1),
                round(float(snap.get("yPercent", 0.0) or 0.0), 1),
                snap.get("action"),
            )
            if key not in grouped:
                grouped[key] = {
                    "name": snap.get("name"),
                    "count": 0,
                    "location": snap.get("location") or "--",
                    "action": snap.get("action") or "当前可见",
                    "kind": snap.get("kind") or "unit",
                    "xPercent": snap.get("xPercent"),
                    "yPercent": snap.get("yPercent"),
                    "worldX": snap.get("worldX"),
                    "worldY": snap.get("worldY"),
                }
            grouped[key]["count"] += 1
        return sorted(
            grouped.values(),
            key=lambda row: (row["location"], row["kind"], row["name"], row["xPercent"], row["yPercent"]),
        )[:180]

    def _units_seen_by_opponent(self) -> tuple[list[Any], list[Any]]:
        watchers: list[tuple[Any, float]] = []
        for unit in list(self._iter_unit_collection(getattr(self, "enemy_units", None))) + list(
            self._iter_unit_collection(getattr(self, "enemy_structures", None))
        ):
            pos = getattr(unit, "position", None)
            if pos is None:
                continue
            watchers.append((pos, self._unit_sight_range(unit)))
        if not watchers:
            for snap in self._opponent_memory.values():
                if snap.get("kind") != "structure":
                    continue
                watchers.append((Point2((float(snap.get("worldX") or 0.0), float(snap.get("worldY") or 0.0))), 9.0))
        seen_units: list[Any] = []
        seen_structs: list[Any] = []
        if not watchers:
            return seen_units, seen_structs

        def _seen(unit: Any) -> bool:
            pos = getattr(unit, "position", None)
            if pos is None:
                return False
            for watcher_pos, radius in watchers:
                try:
                    if pos.distance_to(watcher_pos) <= radius:
                        return True
                except Exception:
                    try:
                        dx = float(pos.x) - float(getattr(watcher_pos, "x", 0.0))
                        dy = float(pos.y) - float(getattr(watcher_pos, "y", 0.0))
                        if (dx * dx + dy * dy) ** 0.5 <= radius:
                            return True
                    except Exception:
                        continue
            return False

        for unit in self._iter_unit_collection(getattr(self, "units", None)):
            if _seen(unit):
                seen_units.append(unit)
        for unit in self._iter_unit_collection(getattr(self, "structures", None)):
            if _seen(unit):
                seen_structs.append(unit)
        return seen_units, seen_structs

    def _update_opponent_dashboard_runtime(self, iteration: int | None = None) -> None:
        """从红方观测反演蓝方视角：己方=已目击对手，敌方=对手视野内的红方。"""
        snaps = self._refresh_opponent_memory()
        own_details = self._group_memory_force_rows(snaps)
        seen_units, seen_structs = self._units_seen_by_opponent()
        enemy_details = self._dashboard_force_detail_rows(
            [seen_units], "enemy", "unit"
        ) + self._dashboard_force_detail_rows(
            [seen_structs], "enemy", "structure"
        )
        composition: Counter[str] = Counter(str(snap.get("name") or "UNKNOWN") for snap in snaps)
        own_comp = [{"name": name, "count": int(count)} for name, count in composition.most_common(80)]
        workers = sum(1 for snap in snaps if snap.get("kind") == "unit" and self._is_worker_name(str(snap.get("name"))))
        army = sum(1 for snap in snaps if snap.get("kind") == "unit" and not self._is_worker_name(str(snap.get("name"))))
        structures = sum(1 for snap in snaps if snap.get("kind") == "structure")
        enemy_start = ""
        starts = getattr(self, "enemy_start_locations", None) or []
        if starts:
            enemy_start = self._dashboard_nearest_location_label(starts[0])
        self.opponent_dashboard_state.update_runtime(
            agentId="blue",
            agentName="蓝方对手",
            side="blue",
            perspective="opponent",
            perspectiveLabel="蓝方对手",
            perspectiveHint="从红方可见情报反演蓝方观察",
            startLocation=enemy_start,
            timeFormatted=getattr(self, "time_formatted", ""),
            iteration=int(iteration) if iteration is not None else None,
            gameStage=self._game_stage_label(),
            armyCount=army,
            workerCount=workers,
            supplyUsed=army + workers,
            supplyCap=None,
            pendingCommandCount=0,
            minerals=0,
            vespene=0,
            economyKnown=False,
            enemyUnitsVisible=len(seen_units),
            enemyStructuresVisible=len(seen_structs),
            ownUnitComposition=own_comp,
            enemyVisibleComposition=self._dashboard_unit_composition_rows(seen_units) + self._dashboard_unit_composition_rows(seen_structs),
            ownForceDetails=own_details,
            enemyVisibleDetails=enemy_details,
            resourceMarkers=self._dashboard_resource_markers(),
            ownHpTotal=0,
            knownContactCount=len(snaps),
            liveContactCount=sum(1 for snap in snaps if snap.get("visible")),
        )
        self.dashboard_state.update_runtime(
            opponentViewHint="已写入蓝方视角（由红方观测反演）",
            opponentKnownContacts=len(snaps),
        )

    def _sync_feature_frame(self):
        if not self.feature_map_exporter:
            return
        if self.feature_map_exporter.latest_frame_dir and self.feature_map_exporter.latest_metadata:
            self.dashboard_state.update_feature_frame(
                self.feature_map_exporter.latest_frame_dir,
                self.feature_map_exporter.latest_metadata,
            )

    def _sync_dashboard_commands(self):
        self.dashboard_state.update_commands(self.parsed_commands)

    def _stage_label_dashboard(self):
        return {"early": "前期", "mid": "中期", "late": "后期"}.get(self._game_stage_label(), "前期")

    def _summarize_dashboard_context(self, label: str, payload, limit: int = 3) -> str:
        if not payload:
            return f"{label}：暂无"

        if isinstance(payload, dict):
            items = [f"{key}: {value}" for key, value in payload.items()]
        elif isinstance(payload, (list, tuple, set)):
            items = [str(item) for item in payload]
        else:
            text = str(payload).strip()
            if not text:
                return f"{label}：暂无"
            items = [line.strip(" -") for line in text.splitlines() if line.strip()]
            if not items:
                items = [text]

        preview = "；".join(shorten(str(item), width=84, placeholder="...") for item in items[:limit])
        remaining = len(items) - limit
        if remaining > 0:
            preview = f"{preview}；另有 {remaining} 项"
        return f"{label}：{preview}"

    def _build_macro_input_summary(
        self,
        game_time,
        current_units,
        current_buildings,
        current_tech,
        enemy_units,
        enemy_buildings,
    ) -> str:
        sections = [
            (
                f"时间：{game_time or '--'}；出生点：{self.start_location_label or '--'}；"
                f"阶段：{self._stage_label_dashboard()}；军队：{int(getattr(self, 'army_count', 0))}；"
                f"工人：{int(self.workers.amount) if self.workers else 0}"
            ),
            self._summarize_dashboard_context("我方单位分布", current_units, 3),
            self._summarize_dashboard_context("我方建筑分布", current_buildings, 3),
            self._summarize_dashboard_context("当前科技", current_tech, 2),
            self._summarize_dashboard_context("敌方单位情报", enemy_units, 3),
            self._summarize_dashboard_context("敌方建筑情报", enemy_buildings, 3),
            self._summarize_dashboard_context("上一轮指令", self.previous_commands, 4),
        ]
        return "\n".join(section for section in sections if section)

    def _build_building_input_summary(
        self,
        game_time,
        current_units,
        current_buildings,
        enemy_units,
        enemy_buildings,
    ) -> str:
        sections = [
            (
                f"时间：{game_time or '--'}；出生点：{self.start_location_label or '--'}；"
                f"阶段：{self._stage_label_dashboard()}；待执行指令：{len(self.parsed_commands)}"
            ),
            self._summarize_dashboard_context("我方全部单位", current_units, 3),
            self._summarize_dashboard_context("我方全部建筑", current_buildings, 3),
            self._summarize_dashboard_context("敌方单位情报", enemy_units, 3),
            self._summarize_dashboard_context("敌方建筑情报", enemy_buildings, 3),
            self._summarize_dashboard_context("上一轮指令", self.previous_commands, 4),
        ]
        return "\n".join(section for section in sections if section)

    def _build_attack_input_summary(
        self,
        game_time,
        current_units,
        army_damage,
        enemy_units,
        enemy_buildings,
        enemy_damage,
    ) -> str:
        sections = [
            (
                f"时间：{game_time or '--'}；出生点：{self.start_location_label or '--'}；"
                f"阶段：{self._stage_label_dashboard()}；军队：{int(getattr(self, 'army_count', 0))}；"
                f"补给：{int(getattr(self, 'supply_used', 0))}/{int(getattr(self, 'supply_cap', 0))}"
            ),
            self._summarize_dashboard_context("我方可战单位", current_units, 3),
            f"我方火力估计：{army_damage}",
            self._summarize_dashboard_context("敌方作战单位", enemy_units, 3),
            f"敌方火力估计：{enemy_damage}",
            self._summarize_dashboard_context("敌方建筑情报", enemy_buildings, 3),
            self._summarize_dashboard_context("当前指令", self.parsed_commands, 4),
        ]
        return "\n".join(section for section in sections if section)

    def _record_llm_message(self, channel: str, title: str, content: str, *, input_summary: str = ""):
        self.dashboard_state.update_message(
            channel,
            content,
            title=title,
            time_formatted=getattr(self, "time_formatted", ""),
            input_summary=input_summary,
            agent_name="任务规划智能体",
            source_name="脚本调度器",
        )

    def _drain_task_list(self, tasks, label: str):
        finished_tasks = [task for task in tasks if task.done()]
        for task in finished_tasks:
            try:
                result = task.result()
                print(f"{label} result:", result)
            except Exception as exc:
                print(f"{label} failed: {exc}")
                self._record_llm_message("latest", f"{label} Error", str(exc))
            finally:
                tasks.remove(task)

    def get_units_distribution(self):
        distribution = {}
        for mineral_position, location_label in self.mineral_location_labels.items():
            units_near_townhall = self.units.closer_than(20, mineral_position)

            larvas_near_townhall = units_near_townhall.of_type({UnitTypeId.LARVA})
            drones_near_townhall = units_near_townhall.of_type({UnitTypeId.DRONE})
            overlords_near_townhall = units_near_townhall.of_type({UnitTypeId.OVERLORD})
            queens_near_townhall = units_near_townhall.of_type({UnitTypeId.QUEEN})
            zerglings_near_townhall = units_near_townhall.of_type({UnitTypeId.ZERGLING})
            overseers_near_townhall = units_near_townhall.of_type({UnitTypeId.OVERSEER})
            roaches_near_townhall = units_near_townhall.of_type({UnitTypeId.ROACH})
            ravagers_near_townhall = units_near_townhall.of_type({UnitTypeId.RAVAGER})
            banelings_near_townhall = units_near_townhall.of_type({UnitTypeId.BANELING})
            hydralisks_near_townhall = units_near_townhall.of_type({UnitTypeId.HYDRALISK})
            infestors_near_townhall = units_near_townhall.of_type({UnitTypeId.INFESTOR})
            swarmhosts_near_townhall = units_near_townhall.of_type({UnitTypeId.SWARMHOSTMP})
            mutalisks_near_townhall = units_near_townhall.of_type({UnitTypeId.MUTALISK})
            corruptors_near_townhall = units_near_townhall.of_type({UnitTypeId.CORRUPTOR})
            vipers_near_townhall = units_near_townhall.of_type({UnitTypeId.VIPER})
            ultralisks_near_townhall = units_near_townhall.of_type({UnitTypeId.ULTRALISK})
            broodlord_near_townhall = units_near_townhall.of_type({UnitTypeId.BROODLORD})

            # Now collect the summary for this point
            unit_summary = f""
            if larvas_near_townhall.amount > 0:
                unit_summary += f"{larvas_near_townhall.amount} Larvas are idling"
            if drones_near_townhall.amount > 0:
                unit_summary += f", {drones_near_townhall.amount} Drones are gathering mineral and gas in Hatchery"
            if overlords_near_townhall.amount > 0:
                unit_summary += f", {overlords_near_townhall.amount} Overlords are idling"
            if queens_near_townhall.amount > 0:
                unit_summary += f", {queens_near_townhall.amount} Queens are idling"
            if zerglings_near_townhall.amount > 0:
                unit_summary += f", {zerglings_near_townhall.amount} Zerglings are idling"
            if overseers_near_townhall.amount > 0:
                unit_summary += f", {overseers_near_townhall.amount} Overseers are idling"
            if roaches_near_townhall.amount > 0:
                unit_summary += f", {roaches_near_townhall.amount} Roaches are idling"
            if ravagers_near_townhall.amount > 0:
                unit_summary += f", {ravagers_near_townhall.amount} Ravagers are idling"
            if banelings_near_townhall.amount > 0:
                unit_summary += f", {banelings_near_townhall.amount} Banelings are idling"
            if hydralisks_near_townhall.amount > 0:
                unit_summary += f", {hydralisks_near_townhall.amount} Hydralisks are idling"
            if infestors_near_townhall.amount > 0:
                unit_summary += f", {infestors_near_townhall.amount} Infestors are idling"
            if swarmhosts_near_townhall.amount > 0:
                unit_summary += f", {swarmhosts_near_townhall.amount} Swarm Hosts are idling"
            if mutalisks_near_townhall.amount > 0:
                unit_summary += f", {mutalisks_near_townhall.amount} Mutalisks are idling"
            if corruptors_near_townhall.amount > 0:
                unit_summary += f", {corruptors_near_townhall.amount} Corruptors are idling"
            if vipers_near_townhall.amount > 0:
                unit_summary += f", {vipers_near_townhall.amount} Vipers are idling"
            if ultralisks_near_townhall.amount > 0:
                unit_summary += f", {ultralisks_near_townhall.amount} Ultralisks are idling"
            if broodlord_near_townhall.amount > 0:
                unit_summary += f", {broodlord_near_townhall.amount} Brood Lords are idling"

            if unit_summary == "":
                continue
            # Assign the summary to the distribution dict
            distribution[location_label] = unit_summary

        return distribution

    def get_units_all(self):
        drones = self.units(UnitTypeId.DRONE).amount
        overlords = self.units(UnitTypeId.OVERLORD).amount
        queens = self.units(UnitTypeId.QUEEN).amount
        zerglings = self.units(UnitTypeId.ZERGLING).amount
        overseers = self.units(UnitTypeId.OVERSEER).amount
        roaches = self.units(UnitTypeId.ROACH).amount
        ravagers = self.units(UnitTypeId.RAVAGER).amount
        banelings = self.units(UnitTypeId.BANELING).amount
        hydralisks = self.units(UnitTypeId.HYDRALISK).amount
        infestors = self.units(UnitTypeId.INFESTOR).amount
        swarmhosts = self.units(UnitTypeId.SWARMHOSTMP).amount
        mutalisks = self.units(UnitTypeId.MUTALISK).amount
        corruptors = self.units(UnitTypeId.CORRUPTOR).amount
        vipers = self.units(UnitTypeId.VIPER).amount
        ultralisks = self.units(UnitTypeId.ULTRALISK).amount
        broodlords = self.units(UnitTypeId.BROODLORD).amount

        summary = f""
        if drones > 0:
            summary += f"{drones} Drones"
        if overlords > 0:
            if summary:
                summary += f", {overlords} Overlords"
            else:
                summary += f"{overlords} Overlords"
        if queens > 0:
            if summary:
                summary += f", {queens} Queens"
            else:
                summary += f"{queens} Queens"
        if zerglings > 0:
            if summary:
                summary += f", {zerglings} Zerglings"
            else:
                summary += f"{zerglings} Zerglings"
        if overseers > 0:
            if summary:
                summary += f", {overseers} Overseers"
            else:
                summary += f"{overseers} Overseers"
        if roaches > 0:
            if summary:
                summary += f", {roaches} Roaches"
            else:
                summary += f"{roaches} Roaches"
        if ravagers > 0:
            if summary:
                summary += f", {ravagers} Ravagers"
            else:
                summary += f"{ravagers} Ravagers"
        if banelings > 0:
            if summary:
                summary += f", {banelings} Banelings"
            else:
                summary += f"{banelings} Banelings"
        if hydralisks > 0:
            if summary:
                summary += f", {hydralisks} Hydralisks"
            else:
                summary += f"{hydralisks} Hydralisks"
        if infestors > 0:
            if summary:
                summary += f", {infestors} Infestors"
            else:
                summary += f"{infestors} Infestors"
        if swarmhosts > 0:
            if summary:
                summary += f", {swarmhosts} Swarm Hosts"
            else:
                summary += f"{swarmhosts} Swarm Hosts"
        if mutalisks > 0:
            if summary:
                summary += f", {mutalisks} Mutalisks"
            else:
                summary += f"{mutalisks} Mutalisks"
        if corruptors > 0:
            if summary:
                summary += f", {corruptors} Corruptors"
            else:
                summary += f"{corruptors} Corruptors"
        if vipers > 0:
            if summary:
                summary += f", {vipers} Vipers"
            else:
                summary += f"{vipers} Vipers"
        if ultralisks > 0:
            if summary:
                summary += f", {ultralisks} Ultralisks"
            else:
                summary += f"{ultralisks} Ultralisks"
        if broodlords > 0:
            if summary:
                summary += f", {broodlords} Brood Lords"
            else:
                summary += f"{broodlords} Brood Lords"

        return summary

    def get_units_all_attack(self):
        zerglings = self.units(UnitTypeId.ZERGLING).amount
        roaches = self.units(UnitTypeId.ROACH).amount
        ravagers = self.units(UnitTypeId.RAVAGER).amount
        banelings = self.units(UnitTypeId.BANELING).amount
        hydralisks = self.units(UnitTypeId.HYDRALISK).amount
        infestors = self.units(UnitTypeId.INFESTOR).amount
        swarmhosts = self.units(UnitTypeId.SWARMHOSTMP).amount
        mutalisks = self.units(UnitTypeId.MUTALISK).amount
        corruptors = self.units(UnitTypeId.CORRUPTOR).amount
        vipers = self.units(UnitTypeId.VIPER).amount
        ultralisks = self.units(UnitTypeId.ULTRALISK).amount
        broodlords = self.units(UnitTypeId.BROODLORD).amount

        army_damage = zerglings * 5 + roaches * 16 + ravagers * 16 + banelings * 16 + hydralisks * 12 + mutalisks * 9 + corruptors * 14 + ultralisks * 35 + broodlords * 20

        summary = f""
        if zerglings > 0:
            summary += f"{zerglings} Zerglings"
        if roaches > 0:
            if summary:
                summary += f", {roaches} Roaches"
            else:
                summary += f"{roaches} Roaches"
        if ravagers > 0:
            if summary:
                summary += f", {ravagers} Ravagers"
            else:
                summary += f"{ravagers} Ravagers"
        if banelings > 0:
            if summary:
                summary += f", {banelings} Banelings"
            else:
                summary += f"{banelings} Banelings"
        if hydralisks > 0:
            if summary:
                summary += f", {hydralisks} Hydralisks"
            else:
                summary += f"{hydralisks} Hydralisks"
        if infestors > 0:
            if summary:
                summary += f", {infestors} Infestors"
            else:
                summary += f"{infestors} Infestors"
        if swarmhosts > 0:
            if summary:
                summary += f", {swarmhosts} Swarm Hosts"
            else:
                summary += f"{swarmhosts} Swarm Hosts"
        if mutalisks > 0:
            if summary:
                summary += f", {mutalisks} Mutalisks"
            else:
                summary += f"{mutalisks} Mutalisks"
        if corruptors > 0:
            if summary:
                summary += f", {corruptors} Corruptors"
            else:
                summary += f"{corruptors} Corruptors"
        if vipers > 0:
            if summary:
                summary += f", {vipers} Vipers"
            else:
                summary += f"{vipers} Vipers"
        if ultralisks > 0:
            if summary:
                summary += f", {ultralisks} Ultralisks"
            else:
                summary += f"{ultralisks} Ultralisks"
        if broodlords > 0:
            if summary:
                summary += f", {broodlords} Brood Lords"
            else:
                summary += f"{broodlords} Brood Lords"

        return summary, army_damage

    def get_buildings_distribution(self):
        distribution = {}
        for townhall in self.townhalls.ready:
            townhall_location_label = self.mineral_location_labels.get(townhall.position)

            buildings_near_townhall = self.structures.closer_than(20, townhall.position)

            extractor_near_townhall = buildings_near_townhall.of_type({UnitTypeId.EXTRACTOR})
            spawningpool_near_townhall = buildings_near_townhall.of_type({UnitTypeId.SPAWNINGPOOL})
            evolutionchamber_near_townhall = buildings_near_townhall.of_type({UnitTypeId.EVOLUTIONCHAMBER})
            roachwarren_near_townhall = buildings_near_townhall.of_type({UnitTypeId.ROACHWARREN})
            banelingnest_near_townhall = buildings_near_townhall.of_type({UnitTypeId.BANELINGNEST})
            spinecrawler_near_townhall = buildings_near_townhall.of_type({UnitTypeId.SPINECRAWLER})
            sporecrawler_near_townhall = buildings_near_townhall.of_type({UnitTypeId.SPORECRAWLER})
            hydraliskden_near_townhall = buildings_near_townhall.of_type({UnitTypeId.HYDRALISKDEN})
            infestationpit_near_townhall = buildings_near_townhall.of_type({UnitTypeId.INFESTATIONPIT})
            spire_near_townhall = buildings_near_townhall.of_type({UnitTypeId.SPIRE})
            nydusnetwork_near_townhall = buildings_near_townhall.of_type({UnitTypeId.NYDUSNETWORK})
            ultraliskcavern_near_townhall = buildings_near_townhall.of_type({UnitTypeId.ULTRALISKCAVERN})
            greaterspire_near_townhall = buildings_near_townhall.of_type({UnitTypeId.GREATERSPIRE})

            unit_summary = f"1 {townhall.name.lower()}"
            if extractor_near_townhall.amount > 0:
                unit_summary += f", {extractor_near_townhall.amount} Extractor"
            if spawningpool_near_townhall.amount > 0:
                unit_summary += f", {spawningpool_near_townhall.amount} Spawning Pool"
            if evolutionchamber_near_townhall.amount > 0:
                unit_summary += f", {evolutionchamber_near_townhall.amount} Evolution Chamber"
            if roachwarren_near_townhall.amount > 0:
                unit_summary += f", {roachwarren_near_townhall.amount} Roach Warren"
            if banelingnest_near_townhall.amount > 0:
                unit_summary += f", {banelingnest_near_townhall.amount} Baneling Nest"
            if spinecrawler_near_townhall.amount > 0:
                unit_summary += f", {spinecrawler_near_townhall.amount} Spine Crawler"
            if sporecrawler_near_townhall.amount > 0:
                unit_summary += f", {sporecrawler_near_townhall.amount} Spore Crawler"
            if hydraliskden_near_townhall.amount > 0:
                unit_summary += f", {hydraliskden_near_townhall.amount} Hydralisk Den"
            if infestationpit_near_townhall.amount > 0:
                unit_summary += f", {infestationpit_near_townhall.amount} Infestation Pit"
            if spire_near_townhall.amount > 0:
                unit_summary += f", {spire_near_townhall.amount} Spire"
            if nydusnetwork_near_townhall.amount > 0:
                unit_summary += f", {nydusnetwork_near_townhall.amount} Nydus Network"
            if ultraliskcavern_near_townhall.amount > 0:
                unit_summary += f", {ultraliskcavern_near_townhall.amount} Ultralisk Cavern"
            if greaterspire_near_townhall.amount > 0:
                unit_summary += f", {greaterspire_near_townhall.amount} Greater Spire"
            distribution[townhall_location_label] = unit_summary

        return distribution

    def get_buildings_all(self):
        hatchery = self.structures(UnitTypeId.HATCHERY).amount
        lair = self.structures(UnitTypeId.LAIR).amount
        hive = self.structures(UnitTypeId.HIVE).amount
        extractor = self.structures(UnitTypeId.EXTRACTOR).amount
        spawningpool = self.structures(UnitTypeId.SPAWNINGPOOL).amount
        evolutionchamber = self.structures(UnitTypeId.EVOLUTIONCHAMBER).amount
        roachwarren = self.structures(UnitTypeId.ROACHWARREN).amount
        banelingnest = self.structures(UnitTypeId.BANELINGNEST).amount
        spinecrawler = self.structures(UnitTypeId.SPINECRAWLER).amount
        sporecrawler = self.structures(UnitTypeId.SPORECRAWLER).amount
        hydraliskden = self.structures(UnitTypeId.HYDRALISKDEN).amount
        infestationpit = self.structures(UnitTypeId.INFESTATIONPIT).amount
        spire = self.structures(UnitTypeId.SPIRE).amount
        nydusnetwork = self.structures(UnitTypeId.NYDUSNETWORK).amount
        ultraliskcavern = self.structures(UnitTypeId.ULTRALISKCAVERN).amount
        greaterspire = self.structures(UnitTypeId.GREATERSPIRE).amount

        summary = f""
        if hatchery > 0:
            summary += f"{hatchery} Hatchery"
        if lair > 0:
            summary += f", {lair} Lair"
        if hive > 0:
            summary += f", {hive} Hive"
        if extractor > 0:
            summary += f", {extractor} Extractor"
        if spawningpool > 0:
            summary += f", {spawningpool} Spawningpool"
        if evolutionchamber > 0:
            summary += f", {evolutionchamber} Evolution Chamber"
        if roachwarren > 0:
            summary += f", {roachwarren} Roach Warren"
        if banelingnest > 0:
            summary += f", {banelingnest} Baneling Nest"
        if spinecrawler > 0:
            summary += f", {spinecrawler} Spine Crawler"
        if sporecrawler > 0:
            summary += f", {sporecrawler} Spore Crawler"
        if hydraliskden > 0:
            summary += f", {hydraliskden} Hydralisk Den"
        if infestationpit > 0:
            summary += f", {infestationpit} Infestation Pit"
        if spire > 0:
            summary += f", {spire} Spire"
        if nydusnetwork > 0:
            summary += f", {nydusnetwork} Nydus Network"
        if ultraliskcavern > 0:
            summary += f", {ultraliskcavern} Ultralisk Cavern"
        if greaterspire > 0:
            summary += f", {greaterspire} Greater Spire"

        return summary

    def get_tech(self):
        tech_info = []
        for upgrade in self.state.upgrades:
            upgrade_name = upgrade.name
            tech_info.append(upgrade_name)
        return tech_info

    def get_enemy_units(self):
        if len(self.enemy_units) == 0:
            return {}
        distribution = {}

        for mineral_position, location_label in self.mineral_location_labels.items():
            units_near_mineral = self.enemy_units.closer_than(25, mineral_position)

            scv_near_mineral = units_near_mineral.of_type({UnitTypeId.SCV})
            marine_near_mineral = units_near_mineral.of_type({UnitTypeId.MARINE})
            reaper_near_mineral = units_near_mineral.of_type({UnitTypeId.REAPER})
            marauder_near_mineral = units_near_mineral.of_type({UnitTypeId.MARAUDER})
            ghost_near_mineral = units_near_mineral.of_type({UnitTypeId.GHOST})
            hellion_near_mineral = units_near_mineral.of_type({UnitTypeId.HELLION})
            widowmine_near_mineral = units_near_mineral.of_type({UnitTypeId.WIDOWMINE})
            cyclone_near_mineral = units_near_mineral.of_type({UnitTypeId.CYCLONE})
            siegetank_near_mineral = units_near_mineral.of_type({UnitTypeId.SIEGETANK})
            hellbat_near_mineral = units_near_mineral.of_type({UnitTypeId.HELLBATACGLUESCREENDUMMY})
            thor_near_mineral = units_near_mineral.of_type({UnitTypeId.THOR})
            viking_near_mineral = units_near_mineral.of_type({UnitTypeId.VIKING})
            medivac_near_mineral = units_near_mineral.of_type({UnitTypeId.MEDIVAC})
            liberator_near_mineral = units_near_mineral.of_type({UnitTypeId.LIBERATOR})
            raven_near_mineral = units_near_mineral.of_type({UnitTypeId.RAVEN})
            banshee_near_mineral = units_near_mineral.of_type({UnitTypeId.BANSHEE})
            battlecruiser_near_mineral = units_near_mineral.of_type({UnitTypeId.BATTLECRUISER})

            unit_summary = f""
            if scv_near_mineral.amount > 0:
                unit_summary += f"{scv_near_mineral.amount} SCVs"
            if marine_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {marine_near_mineral.amount} Marines"
                else:
                    unit_summary += f"{marine_near_mineral.amount} Marines"
            if reaper_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {reaper_near_mineral.amount} Reapers"
                else:
                    unit_summary += f"{reaper_near_mineral.amount} Reapers"
            if marauder_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {marauder_near_mineral.amount} Marauders"
                else:
                    unit_summary += f"{marauder_near_mineral.amount} Marauders"
            if ghost_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {ghost_near_mineral.amount} Ghosts"
                else:
                    unit_summary += f"{ghost_near_mineral.amount} Ghosts"
            if hellion_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {hellion_near_mineral.amount} Hellions"
                else:
                    unit_summary += f"{hellion_near_mineral.amount} Hellions"
            if widowmine_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {widowmine_near_mineral.amount} Widow Mines"
                else:
                    unit_summary += f"{widowmine_near_mineral.amount} Widow Mines"
            if cyclone_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {cyclone_near_mineral.amount} Cyclones"
                else:
                    unit_summary += f"{cyclone_near_mineral.amount} Cyclones"
            if siegetank_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {siegetank_near_mineral.amount} Siege Tanks"
                else:
                    unit_summary += f"{siegetank_near_mineral.amount} Siege Tanks"
            if hellbat_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {hellbat_near_mineral.amount} Hellbats"
                else:
                    unit_summary += f"{hellbat_near_mineral.amount} Hellbats"
            if thor_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {thor_near_mineral.amount} Thors"
                else:
                    unit_summary += f"{thor_near_mineral.amount} Thors"
            if viking_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {viking_near_mineral.amount} Vikings"
                else:
                    unit_summary += f"{viking_near_mineral.amount} Vikings"
            if medivac_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {medivac_near_mineral.amount} Medivacs"
                else:
                    unit_summary += f"{medivac_near_mineral.amount} Medivacs"
            if liberator_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {liberator_near_mineral.amount} Liberators"
                else:
                    unit_summary += f"{liberator_near_mineral.amount} Liberators"
            if raven_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {raven_near_mineral.amount} Ravens"
                else:
                    unit_summary += f"{raven_near_mineral.amount} Ravens"
            if banshee_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {banshee_near_mineral.amount} Banshees"
                else:
                    unit_summary += f"{banshee_near_mineral.amount} Banshees"
            if battlecruiser_near_mineral.amount > 0:
                if unit_summary:
                    unit_summary += f", {battlecruiser_near_mineral.amount} Battle Cruisers"
                else:
                    unit_summary += f"{battlecruiser_near_mineral.amount} Battle Cruisers"

            if unit_summary == "":
                continue

            distribution[location_label] = unit_summary

        return distribution

    def get_enemy_units_all(self):
        marines = self.enemy_units(UnitTypeId.MARINE).amount
        reapers = self.enemy_units(UnitTypeId.REAPER).amount
        marauders = self.enemy_units(UnitTypeId.MARAUDER).amount
        ghosts = self.enemy_units(UnitTypeId.GHOST).amount
        hellions = self.enemy_units(UnitTypeId.HELLION).amount
        widowmines = self.enemy_units(UnitTypeId.WIDOWMINE).amount
        cyclones = self.enemy_units(UnitTypeId.CYCLONE).amount
        siegetanks = self.enemy_units(UnitTypeId.SIEGETANK).amount
        hellbats = self.enemy_units(UnitTypeId.HELLBATACGLUESCREENDUMMY).amount
        thors = self.enemy_units(UnitTypeId.THOR).amount
        vikings = self.enemy_units(UnitTypeId.VIKING).amount
        medivacs = self.enemy_units(UnitTypeId.MEDIVAC).amount
        liberators = self.enemy_units(UnitTypeId.LIBERATOR).amount
        ravens = self.enemy_units(UnitTypeId.RAVEN).amount
        banshees = self.enemy_units(UnitTypeId.BANSHEE).amount
        battlecruisers = self.enemy_units(UnitTypeId.BATTLECRUISER).amount

        enemy_damage = marines * 6 + reapers * 4 + marauders * 10 + ghosts * 10 + hellions * 8 + widowmines * 125 + cyclones * 11 + siegetanks * 40 + hellbats * 18 + thors * 30 + vikings * 12 + liberators * 5 + banshees * 12 + battlecruisers * 8

        summary = f""
        if marines > 0:
            summary += f"{marines} Marines"
        if reapers > 0:
            if summary:
                summary += f", {reapers} Reapers"
            else:
                summary += f", {reapers} Reapers"
        if marauders > 0:
            if summary:
                summary += f", {marauders} Marauders"
            else:
                summary += f"{marauders} Marauders"
        if ghosts > 0:
            if summary:
                summary += f", {ghosts} Ghosts"
            else:
                summary += f"{ghosts} Ghosts"
        if hellions > 0:
            if summary:
                summary += f", {hellions} Hellions"
            else:
                summary += f"{hellions} Hellions"
        if widowmines > 0:
            if summary:
                summary += f", {widowmines} Widow Mines"
            else:
                summary += f", {widowmines} Widow Mines"
        if cyclones > 0:
            if summary:
                summary += f", {cyclones} Cyclones"
            else:
                summary += f"{cyclones} Cyclones"
        if siegetanks > 0:
            if summary:
                summary += f", {siegetanks} Siege Tanks"
            else:
                summary += f"{siegetanks} Siege Tanks"
        if hellbats > 0:
            if summary:
                summary += f", {hellbats} Hellbats"
            else:
                summary += f"{hellbats} Hellbats"
        if thors > 0:
            if summary:
                summary += f", {thors} Thors"
            else:
                summary += f"{thors} Thors"
        if vikings > 0:
            if summary:
                summary += f", {vikings} Vikings"
            else:
                summary += f"{vikings} Vikings"
        if medivacs > 0:
            if summary:
                summary += f", {medivacs} Medivacs"
            else:
                summary += f"{medivacs} Medivacs"
        if liberators > 0:
            if summary:
                summary += f", {liberators} Liberators"
            else:
                summary += f"{liberators} Liberators"
        if ravens > 0:
            if summary:
                summary += f", {ravens} Ravens"
            else:
                summary += f"{ravens} Ravens"
        if banshees > 0:
            if summary:
                summary += f", {banshees} Banshees"
            else:
                summary += f"{banshees} Banshees"
        if battlecruisers > 0:
            if summary:
                summary += f", {battlecruisers} Battlecruisers"
            else:
                summary += f"{battlecruisers} Battlecruisers"

        return summary, enemy_damage

    def get_enemy_buildings(self):
        if len(self.enemy_structures) == 0:
            return {}
        distribution = {}

        for mineral_position, location_label in self.mineral_location_labels.items():
            units_near_mineral = self.enemy_structures.closer_than(25, mineral_position)

            commandcenter_near_townhall = units_near_mineral.of_type({UnitTypeId.COMMANDCENTER})
            refinery_near_townhall = units_near_mineral.of_type({UnitTypeId.REFINERY})
            supplydepot_near_townhall = units_near_mineral.of_type({UnitTypeId.SUPPLYDEPOT})
            engineeringbay_near_townhall = units_near_mineral.of_type({UnitTypeId.ENGINEERINGBAY})
            missileturret_near_townhall = units_near_mineral.of_type({UnitTypeId.MISSILETURRET})
            sensortower_near_townhall = units_near_mineral.of_type({UnitTypeId.SENSORTOWER})
            planetaryfortress_near_townhall = units_near_mineral.of_type({UnitTypeId.PLANETARYFORTRESS})
            barracks_near_townhall = units_near_mineral.of_type({UnitTypeId.BARRACKS})
            bunker_near_townhall = units_near_mineral.of_type({UnitTypeId.BUNKER})
            ghostacademy_near_townhall = units_near_mineral.of_type({UnitTypeId.GHOSTACADEMY})
            factory_near_townhall = units_near_mineral.of_type({UnitTypeId.FACTORY})
            orbitalcommand_near_townhall = units_near_mineral.of_type({UnitTypeId.ORBITALCOMMAND})
            armory_near_townhall = units_near_mineral.of_type({UnitTypeId.ARMORY})
            starport_near_townhall = units_near_mineral.of_type({UnitTypeId.STARPORT})
            fusioncore_near_townhall = units_near_mineral.of_type({UnitTypeId.FUSIONCORE})

            unit_summary = f""
            if commandcenter_near_townhall.amount > 0:
                unit_summary += f"{commandcenter_near_townhall.amount} Command Center"
            if refinery_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {refinery_near_townhall.amount} Refinery"
                else:
                    unit_summary += f"{refinery_near_townhall.amount} Refinery"
            if supplydepot_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {supplydepot_near_townhall.amount} Supply Depot"
                else:
                    unit_summary += f"{supplydepot_near_townhall.amount} Supply Depot"
            if engineeringbay_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {engineeringbay_near_townhall.amount} Engineering Bay"
                else:
                    unit_summary += f"{engineeringbay_near_townhall.amount} Engineering Bay"
            if missileturret_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {missileturret_near_townhall.amount} Missile Turret"
                else:
                    unit_summary += f"{missileturret_near_townhall.amount} Missile Turret"
            if sensortower_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {sensortower_near_townhall.amount} Sensor Tower"
                else:
                    unit_summary += f"{sensortower_near_townhall.amount} Sensor Tower"
            if planetaryfortress_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {planetaryfortress_near_townhall.amount} Planetary Fortress"
                else:
                    unit_summary += f"{planetaryfortress_near_townhall.amount} Planetary Fortress"
            if barracks_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {barracks_near_townhall.amount} Barracks"
                else:
                    unit_summary += f"{barracks_near_townhall.amount} Barracks"
            if bunker_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {bunker_near_townhall.amount} Bunker"
                else:
                    unit_summary += f"{bunker_near_townhall.amount} Bunker"
            if ghostacademy_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {ghostacademy_near_townhall.amount} Ghost Academy"
                else:
                    unit_summary += f"{ghostacademy_near_townhall.amount} Ghost Academy"
            if factory_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {factory_near_townhall.amount} Factory"
                else:
                    unit_summary += f"{factory_near_townhall.amount} Factory"
            if orbitalcommand_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {orbitalcommand_near_townhall.amount} Orbital Command"
                else:
                    unit_summary += f"{orbitalcommand_near_townhall.amount} Orbital Command"
            if armory_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {armory_near_townhall.amount} Armory"
                else:
                    unit_summary += f", {armory_near_townhall.amount} Armory"
            if starport_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {starport_near_townhall.amount} Starport"
                else:
                    unit_summary += f"{starport_near_townhall.amount} Starport"
            if fusioncore_near_townhall.amount > 0:
                if unit_summary:
                    unit_summary += f", {fusioncore_near_townhall.amount} Fusion Core"
                else:
                    unit_summary += f"{fusioncore_near_townhall.amount} Fusion Core"

            if unit_summary == "":
                continue

            distribution[location_label] = unit_summary

        return distribution

    def filter_commands(self, commands, target_objects):
        filtered_commands = [cmd for cmd in commands if not any(obj in cmd for obj in target_objects)]
        return filtered_commands

    def id_game_stage(self):
        cap = int(getattr(self, "supply_cap", 0) or 0)
        t = float(getattr(self, "time", 0) or 0)
        # supply_cap > 60 used to hide the late branch (dead elif). Use time as backup
        # so a stalled 50-supply game still leaves the early hydralisk_num=0 targets.
        if cap > 100 or t >= 12 * 60:
            return 2  # late
        if cap > 60 or t >= 6 * 60:
            return 1  # mid
        return 0  # early

    def detect_stage(self, text):
        if 'early' in text and 'mid' not in text:
            return 0
        elif 'early' in text and 'mid' in text:
            return 1
        elif 'late' in text:
            return 2
        return self.id_game_stage()

    async def overmindbrain_iter(self):
        game_time = self.time_formatted

        tmp_current_units = []
        current_units = self.get_units_distribution()
        print("our units:")
        for location, units_summary in current_units.items():
            if self.start_location_label == "B1":
                location = location.replace('B', 'A')
            print(f"At point {location}, there are: {units_summary}")
            tmp_current_units.append(f"At point {location}, there are: {units_summary}")

        tmp_current_buildings = []
        current_buildings = self.get_buildings_distribution()
        print("our buildings:")
        for location, units_summary in current_buildings.items():
            if self.start_location_label == "B1":
                location = location.replace('B', 'A')
            print(f"At point {location}, there are: {units_summary}")
            tmp_current_buildings.append(f"At point {location}, there are: {units_summary}")

        current_tech = self.get_tech()
        print("our tech:")
        print("current_tech:", current_tech)

        tmp_current_enemy_units = []
        current_enemy_units = self.get_enemy_units()
        if current_enemy_units == "":
            current_enemy_units = self.previous_enemy_units
        self.previous_enemy_units = current_enemy_units

        for location, units_summary in current_enemy_units.items():
            if self.start_location_label == "B1":
                location = location.replace('A', 'B')
            print(f"At point {location}, there are: {units_summary}")
            tmp_current_enemy_units.append(f"At point {location}, there are: {units_summary}")

        tmp_current_enemy_buildings = []
        current_enemy_buildings = self.get_enemy_buildings()
        if current_enemy_buildings == "":
            current_enemy_buildings = self.previous_enemy_buildings
        self.previous_enemy_buildings = current_enemy_buildings
        print("enemy buildings:")
        for location, units_summary in current_enemy_buildings.items():
            if self.start_location_label == "B1":
                location = location.replace('A', 'B')
            print(f"At point {location}, there are: {units_summary}")
            tmp_current_enemy_buildings.append(f"At point {location}, there are: {units_summary}")

        input_summary = self._build_macro_input_summary(
            game_time,
            tmp_current_units,
            tmp_current_buildings,
            current_tech,
            tmp_current_enemy_units,
            tmp_current_enemy_buildings,
        )
        token = begin_llm_context(
            self.run_logger,
            channel="macro",
            bot=self,
            input_state={"summary": input_summary},
            previous_commands=list(self.previous_commands or []),
            agent_id=self.agent_id,
            agent_side=self.agent_side,
            player_id=self.player_id,
            race="Zerg",
            commands_expected=True,
        )
        try:
            output = await overmind_brain_iter(
                game_time,
                tmp_current_units,
                tmp_current_buildings,
                current_tech,
                tmp_current_enemy_units,
                tmp_current_enemy_buildings,
                self.previous_commands,
            )
        finally:
            llm_call_id = end_llm_context(token, auto_complete=False)
        print(output)
        self._record_llm_message("macro", "宏观方案", output, input_summary=input_summary)

        # self.game_stage = self.detect_stage(output)

        matches = re.findall(r'\(.*?\)->\(.*?\)->\(.*?\)', output)

        temp_commands = []
        for match in matches:
            temp_commands.append(match)

        self.previous_commands = temp_commands
        banned = list(REFLEX_OWNED_COMMAND_BANS)
        temp_commands = self.filter_commands(temp_commands, banned)
        complete_llm_with_commands(
            self.run_logger,
            llm_call_id,
            parsed_commands=list(self.previous_commands or []),
            commands_expected=True,
            bot=self,
            channel="macro",
        )
        logged = log_parsed_command_batch(
            self.run_logger,
            llm_call_id=llm_call_id,
            channel="macro",
            proposed=list(self.previous_commands or []),
            accepted=list(temp_commands or []),
            bot=self,
            banned_keywords=banned,
            player_id=self.player_id,
        )
        enqueue_logged_commands(self, logged, remap_ab=(self.start_location_label == "B1"))

        print("self.parsed_commands:", self.parsed_commands)
        self._sync_dashboard_commands()

    async def overmind_building_iter(self):
        game_time = self.time_formatted

        current_units = self.get_units_all()
        print("[Building] our units:", current_units)

        current_buildings = self.get_buildings_all()
        print("[Building] our buildings:", current_buildings)

        tmp_current_enemy_units = []
        current_enemy_units = self.get_enemy_units()
        if current_enemy_units == "":
            current_enemy_units = self.previous_enemy_units
        self.previous_enemy_units = current_enemy_units

        for location, units_summary in current_enemy_units.items():
            if self.start_location_label == "B1":
                location = location.replace('A', 'B')
            print(f"At point {location}, there are: {units_summary}")
            tmp_current_enemy_units.append(f"At point {location}, there are: {units_summary}")

        tmp_current_enemy_buildings = []
        current_enemy_buildings = self.get_enemy_buildings()
        if current_enemy_buildings == "":
            current_enemy_buildings = self.previous_enemy_buildings
        self.previous_enemy_buildings = current_enemy_buildings
        print("enemy buildings:")
        for location, units_summary in current_enemy_buildings.items():
            if self.start_location_label == "B1":
                location = location.replace('A', 'B')
            print(f"At point {location}, there are: {units_summary}")
            tmp_current_enemy_buildings.append(f"At point {location}, there are: {units_summary}")

        print("[Building] previous_commands:", self.previous_commands)
        input_summary = self._build_building_input_summary(
            game_time,
            current_units,
            current_buildings,
            tmp_current_enemy_units,
            tmp_current_enemy_buildings,
        )
        token = begin_llm_context(
            self.run_logger,
            channel="building",
            bot=self,
            input_state={"summary": input_summary},
            previous_commands=list(self.previous_commands or []),
            agent_id=self.agent_id,
            agent_side=self.agent_side,
            player_id=self.player_id,
            race="Zerg",
            commands_expected=True,
        )
        try:
            output = await overmind_building_iter(
                game_time,
                current_units,
                current_buildings,
                tmp_current_enemy_units,
                tmp_current_enemy_buildings,
                self.previous_commands,
            )
        finally:
            llm_call_id = end_llm_context(token, auto_complete=False)
        print(output)
        self._record_llm_message("building", "建筑规划", output, input_summary=input_summary)

        # self.game_stage = self.detect_stage(output)

        matches = re.findall(r'\(.*?\)->\(.*?\)->\(.*?\)', output)

        temp_commands = []
        for match in matches:
            temp_commands.append(match)

        self.previous_commands = temp_commands
        banned = list(REFLEX_OWNED_COMMAND_BANS)
        temp_commands = self.filter_commands(temp_commands, banned)
        complete_llm_with_commands(
            self.run_logger,
            llm_call_id,
            parsed_commands=list(self.previous_commands or []),
            commands_expected=True,
            bot=self,
            channel="building",
        )
        logged = log_parsed_command_batch(
            self.run_logger,
            llm_call_id=llm_call_id,
            channel="building",
            proposed=list(self.previous_commands or []),
            accepted=list(temp_commands or []),
            bot=self,
            banned_keywords=banned,
            player_id=self.player_id,
        )
        enqueue_logged_commands(self, logged, remap_ab=(self.start_location_label == "B1"))

        print("self.parsed_commands:", self.parsed_commands)
        self._sync_dashboard_commands()

    async def can_attack(self, location):
        enemy_units_at_location = self.enemy_units.closer_than(15, location)
        return enemy_units_at_location.exists

    async def llm_attack_enemy(self):
        if self.is_attack_command_issued:
            pass
        else:
            if self.start_location_label == "A1":
                enemy_base_list = ["B2", "B4", "B1", "B3"]
                rally_point = Point2(self.mineral_location_labels_reverse["B7"])
            elif self.start_location_label == "B1":
                enemy_base_list = ["A2", "A4", "A1", "A3"]
                rally_point = Point2(self.mineral_location_labels_reverse["A7"])

            avenager_units_types = [
                UnitTypeId.ZERGLING, UnitTypeId.ROACH,
                UnitTypeId.RAVAGER, UnitTypeId.BANELING, UnitTypeId.HYDRALISK, UnitTypeId.INFESTOR,
                UnitTypeId.SWARMHOSTMP, UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR, UnitTypeId.VIPER,
                UnitTypeId.ULTRALISK, UnitTypeId.BROODLORD
            ]
            defensive_units = self.units.filter(lambda unit: unit.type_id in avenager_units_types)

            for enemy_base_label in enemy_base_list:
                print("enemy_base_label:", enemy_base_label)
                enemy_base_location = Point2(self.mineral_location_labels_reverse[enemy_base_label])

                if await self.can_attack(enemy_base_location):
                    for defender in defensive_units:
                        defender.attack(enemy_base_location)
                    self.is_attack_command_issued = True
                    break

            if not self.is_attack_command_issued:
                for defender in defensive_units:
                    defender.move(rally_point)

    async def overmind_attack_iter(self):
        print("overmind_attack_iter")
        game_time = self.time_formatted

        current_units, army_damage = self.get_units_all_attack()
        print("[Attack] our units:", current_units)

        current_enemy_units_attack, enemy_damage = self.get_enemy_units_all()
        if current_enemy_units_attack == "":
            current_enemy_units_attack = self.previous_enemy_units_attack
        self.previous_enemy_units_attack = current_enemy_units_attack
        print("enemy units:", current_enemy_units_attack)
        if enemy_damage == 0:
            enemy_damage = self.previous_enemy_damage
        self.previous_enemy_damage = enemy_damage

        tmp_current_enemy_buildings = []
        current_enemy_buildings = self.get_enemy_buildings()
        if current_enemy_buildings == "":
            current_enemy_buildings = self.previous_enemy_buildings
        self.previous_enemy_buildings = current_enemy_buildings
        print("enemy buildings:")
        for location, units_summary in current_enemy_buildings.items():
            if self.start_location_label == "B1":
                location = location.replace('A', 'B')
            print(f"At point {location}, there are: {units_summary}")
            tmp_current_enemy_buildings.append(f"At point {location}, there are: {units_summary}")

        input_summary = self._build_attack_input_summary(
            game_time,
            current_units,
            army_damage,
            current_enemy_units_attack,
            tmp_current_enemy_buildings,
            enemy_damage,
        )
        token = begin_llm_context(
            self.run_logger,
            channel="attack",
            bot=self,
            input_state={"summary": input_summary},
            agent_id=self.agent_id,
            agent_side=self.agent_side,
            player_id=self.player_id,
            race="Zerg",
            commands_expected=False,
        )
        try:
            output = await overmind_attack_module(
                game_time,
                current_units,
                army_damage,
                current_enemy_units_attack,
                tmp_current_enemy_buildings,
                enemy_damage,
            )
        finally:
            llm_call_id = end_llm_context(token, auto_complete=False)
        print(output)
        self._record_llm_message("attack", "进攻裁决", output, input_summary=input_summary)
        decided_attack = "True" in (output or "")
        will_attack = bool(decided_attack) and self._should_commit_attack()
        complete_llm_with_commands(
            self.run_logger,
            llm_call_id,
            parsed_commands=["ATTACK_ENEMY_START" if will_attack else "RETREAT_TO_BASE"],
            parse_status="success_nonempty",
            commands_expected=False,
            bot=self,
            channel="attack",
        )
        log_attack_decision_commands(
            self.run_logger,
            llm_call_id=llm_call_id,
            output=output or "",
            bot=self,
            decided_attack=will_attack,
        )

        if will_attack:
            print("Need to attack")
            await self._issue_commit_attack()
        elif self.base_being_attacked or self.defence:
            print("Holding current defence, skip retreat")
        else:
            if decided_attack:
                print("LLM wanted attack, holding at home: missing army, vision, or outgunned")
            else:
                print("Do not Need to attack! Retreat to base")
            await self._issue_defend_home()

    def get_units_around_location(self, location, radius):
        units_around = [unit for unit in self.units if self.distance(unit.position, location) < radius]
        return len(units_around)

    def distance(self, point1, point2):
        return ((point1[0] - point2[0]) ** 2 + (point1[1] - point2[1]) ** 2) ** 0.5

    def count_units_around_minerals(self):
        check_radius = 10
        if self.start_location_label == "A1":
            enemy_mineral_positions = ["B1", "B2", "B3", "B4"]
        elif self.start_location_label == "B1":
            enemy_mineral_positions = ["A1", "A2", "A3", "A4"]

        units_count_around_minerals = {}
        count_all = 0
        for label in enemy_mineral_positions:
            location = self.mineral_location_labels_reverse[label]
            count = self.get_units_around_location(location, check_radius)
            units_count_around_minerals[label] = count
            count_all += count
        return units_count_around_minerals, count_all

    async def attack_enemy(self):
        if self.base_being_attacked or self.defence:
            return
        if self._should_commit_attack():
            await self._issue_commit_attack()
        else:
            await self._issue_defend_home()

    async def detect_enemy_invasion(self):
        if self.start_location_label == "A1":
            enemy_base_list = ["B2", "B4", "B1", "B3"]
            rally_point = Point2(self.mineral_location_labels_reverse["B7"])
        elif self.start_location_label == "B1":
            enemy_base_list = ["A2", "A4", "A1", "A3"]
            rally_point = Point2(self.mineral_location_labels_reverse["A7"])

        enemy_close_distance = 20
        chase_limit = 30

        defensive_units_types = [
            UnitTypeId.QUEEN, UnitTypeId.ZERGLING, UnitTypeId.OVERSEER, UnitTypeId.ROACH,
            UnitTypeId.RAVAGER, UnitTypeId.BANELING, UnitTypeId.HYDRALISK, UnitTypeId.INFESTOR,
            UnitTypeId.SWARMHOSTMP, UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR, UnitTypeId.VIPER,
            UnitTypeId.ULTRALISK, UnitTypeId.BROODLORD
        ]
        counterattack_units_types = [
            UnitTypeId.ZERGLING, UnitTypeId.OVERSEER, UnitTypeId.ROACH,
            UnitTypeId.RAVAGER, UnitTypeId.BANELING, UnitTypeId.HYDRALISK, UnitTypeId.INFESTOR,
            UnitTypeId.SWARMHOSTMP, UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR, UnitTypeId.VIPER,
            UnitTypeId.ULTRALISK, UnitTypeId.BROODLORD
        ]

        base_count = self.townhalls.amount
        no_enemy_count = 0
        for hatchery in self.townhalls:
            close_enemies = self.enemy_units.closer_than(
                enemy_close_distance, hatchery.position).exclude_type(UnitTypeId.SCV)
            if close_enemies.exists:
                self.attack_wave += 1
                hatchery_label = self.mineral_location_labels.get(hatchery.position)
                print("Detect enemy around", hatchery_label)
                self.base_being_attacked = True

                defensive_units = self.units.filter(
                    lambda unit: unit.type_id in defensive_units_types
                )

                for defender in defensive_units:
                    closest_enemy = close_enemies.closest_to(defender)
                    if defender.distance_to(closest_enemy) < chase_limit:
                        defender.attack(closest_enemy)
                    else:
                        defender.move(hatchery.position)
                    self.defence = True

            else:
                no_enemy_count += 1

            if no_enemy_count == base_count:
                if not self.defence and not self.fight_back:
                    if self.base_being_attacked:
                        print("Enemy has been defeated.")
                        print("Whether we should counterattack:", self.counterattack)
                        self.fight_back = True
                        if self.counterattack and self._should_commit_attack():
                            await self._issue_commit_attack()
                        else:
                            await self._issue_defend_home()
                        self.base_being_attacked = False
                elif self.defence:
                    self.defence = False

    async def early_game_production(self):
        await self.Drone_ReflexNet()
        if self.early_zergling_num != 0:
            await self.Zergling_ReflexNet()
        if self.early_roach_num != 0:
            await self.Roach_ReflexNet()

    async def mid_game_production(self):
        await self.Drone_ReflexNet()
        if self.mid_roach_num != 0:
            await self.Roach_ReflexNet()
        if self.mid_ravager_num != 0:
            await self.Ravager_ReflexNet()
        if self.mid_hydralisk_num != 0:
            await self.Hydralisk_ReflexNet()
        if self.mid_zergling_num != 0:
            await self.Zergling_ReflexNet()

    async def late_game_production(self):
        if self.minerals < 1000:
            await self.Drone_ReflexNet()
        if self.late_roach_num != 0:
            await self.Roach_ReflexNet()
        if self.late_ravager_num != 0:
            await self.Ravager_ReflexNet()
        if self.late_zergling_num != 0:
            await self.Zergling_ReflexNet()
        if self.late_ultralisk_num != 0:
            await self.Ultralisk_ReflexNet()
        if self.late_hydralisk_num != 0:
            await self.Hydralisk_ReflexNet()
        if self.late_corruptor_num != 0:
            await self.Corruptor_ReflexNet()
        # await self.infestor_ReflexNet()

    async def morphing(self):
        if self.waiting_for_hatchery and self.minerals <= 400:
            print("Expansion pending, but continuing combat morphing")
            # Do not block combat production or tech while saving for an expansion.

        # print("morphing")
        # Roach
        if self.structures(UnitTypeId.ROACHWARREN).ready.exists:
            if self.game_stage == 0:  # Early Stage
                roach_thre = self.early_roach_num
                if self.units(UnitTypeId.ROACH).amount < self.early_roach_num:
                    if self.can_afford(UnitTypeId.ROACH) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ROACH)
                        print("early larva.train(UnitTypeId.ROACH)")
            elif self.game_stage == 1:  # Mid Stage
                roach_thre = self.mid_roach_num
                if self.units(UnitTypeId.ROACH).amount < self.mid_roach_num:
                    if self.can_afford(UnitTypeId.ROACH) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ROACH)
                        print("mid larva.train(UnitTypeId.ROACH)")
            elif self.game_stage == 2:  # Late Stage
                roach_thre = self.late_roach_num
                if self.units(UnitTypeId.ROACH).amount < self.late_roach_num:
                    if self.can_afford(UnitTypeId.ROACH) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ROACH)
                        print("late larva.train(UnitTypeId.ROACH)")

        # Ravager
        if self.can_afford(UnitTypeId.RAVAGER) and self.units(UnitTypeId.ROACH).exists and self.supply_left > 0:
            if self.game_stage == 0:  # Early Stage
                if self.units(UnitTypeId.RAVAGER).amount < self.early_ravager_num:
                    roach = self.units(UnitTypeId.ROACH).ready.idle
                    if roach.exists:
                        roach.random(AbilityId.MORPHTORAVAGER_RAVAGER)
            elif self.game_stage == 1:  # Mid Stage
                if self.units(UnitTypeId.RAVAGER).amount < self.mid_ravager_num:
                    roach = self.units(UnitTypeId.ROACH).ready.idle
                    if roach.exists:
                        roach.random(AbilityId.MORPHTORAVAGER_RAVAGER)
            elif self.game_stage == 2:  # Late Stage
                if self.units(UnitTypeId.ROACH).amount < self.late_ravager_num:
                    roach = self.units(UnitTypeId.ROACH).ready.idle
                    if roach.exists:
                        roach.random(AbilityId.MORPHTORAVAGER_RAVAGER)

        # Hydralisk
        if self.structures(UnitTypeId.HYDRALISKDEN).ready.exists:
            if self.game_stage == 0:
                hydralisk_num = self.early_hydralisk_num
            elif self.game_stage == 1:
                hydralisk_num = self.mid_hydralisk_num
            elif self.game_stage == 2:
                hydralisk_num = self.late_hydralisk_num
            if self.units(UnitTypeId.HYDRALISK).amount < hydralisk_num:
                if self.can_afford(UnitTypeId.HYDRALISK) and self.units(UnitTypeId.LARVA).exists:
                    larva = self.units(UnitTypeId.LARVA).random
                    larva.train(UnitTypeId.HYDRALISK)
                    print("train hydralisk")

        # Zergling
        if self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists:
            if self.game_stage == 0:  # Early Stage
                if self.units(UnitTypeId.ZERGLING).amount < self.early_zergling_num:
                    if self.can_afford(UnitTypeId.ZERGLING) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ZERGLING)
                        print("early larva.train(UnitTypeId.ZERGLING)")
            elif self.game_stage == 1:  # Mid Stage
                if self.units(UnitTypeId.ZERGLING).amount < self.mid_zergling_num:
                    if self.can_afford(UnitTypeId.ZERGLING) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ZERGLING)
                        print("mid larva.train(UnitTypeId.ZERGLING)")
            elif self.game_stage == 2:  # Late Stage
                if self.units(UnitTypeId.ZERGLING).amount < self.late_zergling_num:
                    if self.can_afford(UnitTypeId.ZERGLING) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ZERGLING)
                        print("late larva.train(UnitTypeId.ZERGLING)")

        # Baneling
        if self.structures(UnitTypeId.BANELINGNEST).ready.exists and self.supply_left > 0 and self.can_afford(
                UnitTypeId.BANELING):
            if self.game_stage == 0:  # Early Stage
                if self.units(UnitTypeId.BANELING).amount < self.early_baneling_num:
                    zergling = self.units(UnitTypeId.ZERGLING).ready.idle
                    if zergling.exists:
                        zergling.random(AbilityId.MORPHTOBANELING_BANELING)
            elif self.game_stage == 1:  # Mid Stage
                if self.units(UnitTypeId.BANELING).amount < self.mid_baneling_num:
                    zergling = self.units(UnitTypeId.ZERGLING).ready.idle
                    if zergling.exists:
                        zergling.random(AbilityId.MORPHTOBANELING_BANELING)
            elif self.game_stage == 2:  # Late Stage
                if self.units(UnitTypeId.BANELING).amount < self.late_baneling_num:
                    zergling = self.units(UnitTypeId.ZERGLING).ready.idle
                    if zergling.exists:
                        zergling.random(AbilityId.MORPHTOBANELING_BANELING)

        # Ultralisk
        if self.structures(UnitTypeId.ULTRALISKCAVERN).ready.exists:
            ultralisk_num = 0
            if self.game_stage == 0:
                ultralisk_num = self.early_ultralisk_num
            elif self.game_stage == 1:
                ultralisk_num = self.mid_ultralisk_num
            elif self.game_stage == 2:
                ultralisk_num = self.late_ultralisk_num

            if self.units(UnitTypeId.ULTRALISK).amount < ultralisk_num:
                if self.units(UnitTypeId.LARVA).exists:
                    larva = self.units(UnitTypeId.LARVA).random
                    larva.train(UnitTypeId.ULTRALISK)

        # Corruptor
        if self.structures(UnitTypeId.SPIRE).ready.exists:
            corruptor_num = 0
            if self.game_stage == 0:
                corruptor_num = self.early_corruptor_num
            elif self.game_stage == 1:
                corruptor_num = self.mid_corruptor_num
            elif self.game_stage == 2:
                corruptor_num = self.late_corruptor_num
            if self.units(UnitTypeId.CORRUPTOR).amount <= corruptor_num:
                if self.can_afford(UnitTypeId.CORRUPTOR) and self.units(UnitTypeId.LARVA).exists:
                    larva = self.units(UnitTypeId.LARVA).random
                    larva.train(UnitTypeId.CORRUPTOR)

    async def low_tier_morph_controller(self):
        """Convert excess low-tier units into high-impact morphs for Hard AI fights."""
        if self.base_being_attacked and self.army_count < 10:
            return

        game_time = float(getattr(self, "time", 0.0) or 0.0)

        if self.structures(UnitTypeId.BANELINGNEST).ready.exists:
            zerglings = self.units(UnitTypeId.ZERGLING).ready.idle
            banelings = self.units(UnitTypeId.BANELING).amount
            if zerglings.amount >= 14 and banelings < max(8, self.mid_baneling_num):
                morph_budget = min(3, zerglings.amount // 6)
                for zergling in zerglings.take(morph_budget):
                    if self.can_afford(UnitTypeId.BANELING):
                        zergling(AbilityId.MORPHTOBANELING_BANELING)

        if self.structures(UnitTypeId.ROACHWARREN).ready.exists:
            roaches = self.units(UnitTypeId.ROACH).ready.idle
            ravagers = self.units(UnitTypeId.RAVAGER).amount
            target_ravagers = self.mid_ravager_num if game_time >= 360 else max(3, self.early_ravager_num)
            if roaches.amount >= 8 and ravagers < target_ravagers and self.supply_left > 0:
                morph_budget = min(2, roaches.amount // 5, max(0, target_ravagers - ravagers))
                for roach in roaches.take(morph_budget):
                    if self.can_afford(UnitTypeId.RAVAGER) and self.supply_left > 0:
                        roach(AbilityId.MORPHTORAVAGER_RAVAGER)

    async def on_step(self, iteration: int):
        # if self.enemy_units:
        #     await self.client.debug_kill_unit(self.enemy_units)

        if self.feature_map_exporter:
            try:
                self.feature_map_exporter.export(self.state.response_observation, iteration)
                self._sync_feature_frame()
            except Exception as exc:
                print(f"Feature export failed: {exc}")
                self.feature_map_exporter = None

        await self.Drone_ReflexNet()
        await self.morphing()
        await self.low_tier_morph_controller()

        await self.Queen_ReflexNet()
        await self.distribute_workers()
        await self.macro_safety_net()
        await self.auto_build_extractors()
        await self.auto_research()
        await self.queen_spread()

        await self.Ravager_ReflexNet()

        if self.id_game_stage() == 0:
            self.game_stage = 0
            await self.early_game_production()
        elif self.id_game_stage() == 1:
            self.game_stage = 1
            await self.mid_game_production()
        elif self.id_game_stage() == 2:
            self.game_stage = 2
            await self.late_game_production()

        await self.Overlord_ReflexNet()
        await self.handle_commands()
        await self.detect_enemy_invasion()
        await self.auto_attack_timing()
        if iteration % 8 == 0 or iteration <= 8:
            self._update_dashboard_runtime(iteration)

        macro_every = int(os.getenv("SWARMBRAIN_MACRO_EVERY", "120") or "120")
        building_every = int(os.getenv("SWARMBRAIN_BUILDING_EVERY", "60") or "60")
        attack_every = int(os.getenv("SWARMBRAIN_ATTACK_EVERY", "150") or "150")
        attack_min_army = int(os.getenv("SWARMBRAIN_ATTACK_MIN_ARMY", "16") or "16")

        if llm_requests_enabled() and macro_every > 0 and iteration % macro_every == 0:
            if len(self.parsed_commands) < 8 and not self.macro_tasks:
                macro_task = asyncio.create_task(self.overmindbrain_iter())
                self.macro_tasks.append(macro_task)

        if llm_requests_enabled() and building_every > 0 and iteration % building_every == 0:
            if len(self.parsed_commands) < 10 and not self.building_tasks:
                building_task = asyncio.create_task(self.overmind_building_iter())
                self.building_tasks.append(building_task)

        if llm_requests_enabled() and attack_every > 0 and iteration % attack_every == 0:
            if self.army_count >= attack_min_army and not self.attack_tasks:
                attack_tasks = asyncio.create_task(self.overmind_attack_iter())
                self.attack_tasks.append(attack_tasks)

        self._drain_task_list(self.macro_tasks, "macro")
        self._drain_task_list(self.building_tasks, "building")
        self._drain_task_list(self.attack_tasks, "attack")

    def time_exceeds(self, minutes):
        minutes_passed, seconds_passed = map(int, self.time_formatted.split(':'))
        total_seconds_passed = (minutes_passed * 60) + seconds_passed

        threshold_seconds = minutes * 60

        return total_seconds_passed > threshold_seconds

    def _has_lair_tech(self) -> bool:
        return bool(
            self.townhalls(UnitTypeId.LAIR).exists
            or self.townhalls(UnitTypeId.HIVE).exists
            or self.already_pending(UnitTypeId.LAIR)
            or self.already_pending(UnitTypeId.HIVE)
        )

    def _try_morph_lair(self) -> bool:
        """Morph any ready Hatchery to Lair. Do not require the mineral-label 2.0 radius."""
        if self._has_lair_tech():
            return False
        if not self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists:
            return False
        if not self.can_afford(UnitTypeId.LAIR):
            return False
        hatcheries = self.townhalls(UnitTypeId.HATCHERY).ready
        if not hatcheries.exists:
            return False
        start = getattr(self, "start_location", None)
        hatchery = hatcheries.closest_to(start) if start is not None else hatcheries.first
        return bool(hatchery.build(UnitTypeId.LAIR))

    def _game_time_sec(self) -> float:
        return float(getattr(self, "time", 0.0) or 0.0)

    def _combat_units(self):
        return self.units.filter(lambda unit: unit.type_id in COMBAT_UNIT_TYPES)

    def _home_rally_point(self) -> Point2:
        labels = list(self.existing_hatchery_locations or [])
        preferred = "A3" if self.start_location_label == "A1" else "B3"
        if preferred in labels:
            return Point2(self.mineral_location_labels_reverse[preferred])
        if labels:
            coords = self.mineral_location_labels_reverse.get(labels[-1])
            if coords:
                return Point2(coords)
        if self.townhalls.exists:
            start = getattr(self, "start_location", None)
            townhall = self.townhalls.closest_to(start) if start is not None else self.townhalls.first
            return townhall.position
        if self.start_location_label in self.mineral_location_labels_reverse:
            return Point2(self.mineral_location_labels_reverse[self.start_location_label])
        return getattr(self, "start_location", Point2((0, 0)))

    def _has_enemy_vision(self) -> bool:
        if self.enemy_structures.exists:
            return True
        return self.enemy_units.of_type(ENEMY_COMBAT_UNIT_TYPES).exists

    def _should_commit_attack(self) -> bool:
        """Push when we have a usable army; do not sit home until mid-game vs a built-in AI."""
        if self.base_being_attacked or self.defence:
            return False
        game_time = self._game_time_sec()
        if game_time < 4 * 60:
            return False
        army = self._combat_units().ready
        army_n = army.amount
        min_army = int(os.getenv("SWARMBRAIN_ATTACK_MIN_ARMY", "16") or "16")
        if army_n < max(12, min_army):
            return False
        mid_n = self.units.of_type(MID_TIER_UNIT_TYPES).ready.amount
        if game_time < 6 * 60 and army_n < 20 and mid_n < 6:
            return False
        if not self._has_enemy_vision() and army_n < 20:
            return False
        heavy = self.enemy_units.of_type(ENEMY_HEAVY_UNIT_TYPES).amount
        bunkers = 0
        bunker_type = getattr(UnitTypeId, "BUNKER", None)
        if bunker_type is not None:
            bunkers = self.enemy_structures(bunker_type).amount
        heavy += bunkers
        if heavy >= 3 and mid_n < max(10, heavy * 4):
            return False
        return True

    async def _issue_defend_home(self) -> None:
        rally = self._home_rally_point()
        for unit in self._combat_units():
            if unit.distance_to(rally) > 8:
                unit.move(rally)
        self.is_attack_command_issued = False

    async def _issue_commit_attack(self) -> None:
        army = self._combat_units()
        if not army.exists:
            await self._issue_defend_home()
            return
        enemies = self.enemy_units.of_type(ENEMY_COMBAT_UNIT_TYPES)
        if enemies.exists:
            target = enemies.center
            for unit in army:
                unit.attack(target)
            self.is_attack_command_issued = True
            return
        structures = self.enemy_structures.ready
        if structures.exists:
            target = structures.closest_to(army.center).position
            for unit in army:
                unit.attack(target)
            self.is_attack_command_issued = True
            return
        await self._issue_defend_home()

    def _is_short_ttl_command(self, cmd: list) -> bool:
        if not isinstance(cmd, list) or len(cmd) != 3:
            return True
        actor, action, payload = cmd[0], cmd[1], cmd[2]
        blob = f"{actor} {action} {payload}"
        if "Hydralisk Den" in blob or "Roach Warren" in blob:
            return False
        if "Lair" in payload or ("Lair" in actor and "Morph" in action):
            return True
        if "Hive" in payload and "Morph" in action:
            return True
        if "Ravager" in payload:
            return True
        if any(key in action for key in ("Hatch", "Train", "Produce")):
            return True
        if "Hydralisk" in payload and "Den" not in payload:
            return True
        return False

    def _command_is_obsolete(self, cmd: list) -> bool:
        if not isinstance(cmd, list) or len(cmd) != 3:
            return True
        actor, action, payload = cmd[0], cmd[1], cmd[2]
        if "Lair" in payload or ("Lair" in actor and "Morph" in action):
            if self._has_lair_tech() or not self.townhalls.exists:
                return True
        if "Hive" in payload:
            if self.townhalls(UnitTypeId.HIVE).exists or self.already_pending(UnitTypeId.HIVE):
                return True
            if not self.townhalls.exists:
                return True
        if "Ravager" in payload and not self.units(UnitTypeId.ROACH).exists:
            if not self.structures(UnitTypeId.ROACHWARREN).exists:
                return True
        producing_army = any(
            key in payload
            for key in ("Hydralisk", "Roach", "Zergling", "Drone", "Overlord", "Ultralisk", "Mutalisk")
        ) and "Den" not in payload and "Warren" not in payload
        if producing_army and not self.townhalls.exists and not self.units(UnitTypeId.LARVA).exists:
            return True
        return False

    def _prune_stale_parsed_commands(self) -> None:
        now = self._game_time_sec()
        ttl = float(getattr(self, "_command_ttl_sec", 45.0) or 45.0)
        kept: list = []
        dropped: list = []
        for cmd in list(self.parsed_commands):
            if not isinstance(cmd, list) or len(cmd) != 3:
                dropped.append(cmd)
                self._command_birth_time.pop(id(cmd), None)
                continue
            birth = self._command_birth_time.setdefault(id(cmd), now)
            expired = self._is_short_ttl_command(cmd) and (now - birth) >= ttl
            if self._command_is_obsolete(cmd) or expired:
                dropped.append(cmd)
                self._command_birth_time.pop(id(cmd), None)
                continue
            kept.append(cmd)
        while len(kept) >= 14:
            drop_at = next((i for i, cmd in enumerate(kept) if self._is_short_ttl_command(cmd)), 0)
            dropped.append(kept.pop(drop_at))
        if dropped:
            print(f"dropped stale/obsolete commands ({len(dropped)}): {dropped[:8]}")
        self.parsed_commands = kept
        live_ids = {id(cmd) for cmd in kept}
        self._command_birth_time = {
            key: value for key, value in self._command_birth_time.items() if key in live_ids
        }

    async def auto_attack_timing(self):
        if self.base_being_attacked or self.defence:
            return
        if not self._should_commit_attack():
            return
        await self._issue_commit_attack()

    async def build_buildings(self, name, i, location):
        if "Drone" in name:
            if i < len(self.parsed_commands):
                del self.parsed_commands[i]
                return

        if "Hatchery" in name:
            print("Hatchery in name")
            if self.can_afford(UnitTypeId.HATCHERY):
                pos = Point2(self.mineral_location_labels_reverse[location])
                if not self.time_exceeds(4) and self.army_count <= 15 and self.townhalls.amount == 2:
                    return
                if await self.can_place(UnitTypeId.HATCHERY, pos):
                    success = self.workers.collecting.random.build(UnitTypeId.HATCHERY, pos)
                    if success:
                        self.waiting_for_hatchery = False
                        print("self.waiting_for_hatchery set to False")
                        if i < len(self.parsed_commands):
                            del self.parsed_commands[i]
                        return
                else:
                    print("cannot place Hatchery!", location)
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Spawning Pool" in name:
            # print("Spawning Pool in name")
            if self.already_pending(UnitTypeId.SPAWNINGPOOL):
                print("SPAWNINGPOOL in pending")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            # print("Already have SPAWNINGPOOL:", self.structures(UnitTypeId.SPAWNINGPOOL).amount)
            if self.structures(UnitTypeId.SPAWNINGPOOL).amount >= 1:
                # print("Already have SPAWNINGPOOL:", self.structures(UnitTypeId.SPAWNINGPOOL).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if self.can_afford(UnitTypeId.SPAWNINGPOOL) and self.already_pending(UnitTypeId.SPAWNINGPOOL) == 0:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)

                placement_position = await self.find_placement(UnitTypeId.SPAWNINGPOOL,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.SPAWNINGPOOL, placement_position)
                # print("Build success:", success)
                if success:
                    # print("Successfully built Spawning Pool")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return


        elif "Evolution Chamber" in name:
            if self.already_pending(UnitTypeId.EVOLUTIONCHAMBER):
                # print("Evolution Chamber in pending")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            # print("Already have EVOLUTIONCHAMBER:", self.structures(UnitTypeId.EVOLUTIONCHAMBER).amount)
            if self.structures(UnitTypeId.EVOLUTIONCHAMBER).amount >= 1:
                # print("Already have EVOLUTIONCHAMBER:", self.structures(UnitTypeId.EVOLUTIONCHAMBER).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            if self.can_afford(UnitTypeId.EVOLUTIONCHAMBER) and self.structures(
                    UnitTypeId.SPAWNINGPOOL).exists and not self.structures(
                UnitTypeId.EVOLUTIONCHAMBER).ready.exists:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.EVOLUTIONCHAMBER,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.EVOLUTIONCHAMBER, placement_position)
                # print("Build success:", success)
                if success:
                    # print("Successfully built Evolution Chamber")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        # Saving minerals for an expansion must not freeze Lair / Hydralisk Den / Infestation Pit.

        if "Extractor" in name:
            # print("Extractor in name")
            if self.can_afford(UnitTypeId.EXTRACTOR):
                base_location = Point2(self.mineral_location_labels_reverse[location])
                if self.townhalls.closer_than(2.0, base_location).ready.exists:
                    hatchery = self.townhalls.closer_than(2.0, base_location).ready.first
                    for vg in self.vespene_geyser.closer_than(10, hatchery):
                        drone: Unit = self.workers.random
                        drone.build_gas(vg)
                        # print("Successfully built gas")
                        if i < len(self.parsed_commands):
                            del self.parsed_commands[i]
                            # print("Deleted command_list at index", i)
                        break

        elif "Spine Crawler" in name:
            if self.can_afford(UnitTypeId.SPINECRAWLER):
                base_location = Point2(self.mineral_location_labels_reverse[location])
                spine_near_townhall = self.structures(UnitTypeId.SPINECRAWLER).closer_than(15, base_location).amount
                if spine_near_townhall >= 1:
                    # print("Already have SPINECRAWLER:", spine_near_townhall)
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return
                next_place = self.game_info.map_center
                if self.start_location_label == "A1":
                    next_place = Point2(self.mineral_location_labels_reverse["A2"])
                elif self.start_location_label == "B1":
                    next_place = Point2(self.mineral_location_labels_reverse["B2"])

                pos = base_location.position.to2.towards(next_place, 5.0)
                placement_position = await self.find_placement(UnitTypeId.SPINECRAWLER,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.SPINECRAWLER, placement_position)
                if success:
                    # print("Successfully built Spine Crawler")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Spore Crawler" in name:
            if self.can_afford(UnitTypeId.SPORECRAWLER):
                # Don't want too many spore crawler around base
                base_location = Point2(self.mineral_location_labels_reverse[location])
                spore_near_townhall = self.structures(UnitTypeId.SPORECRAWLER).closer_than(15, base_location).amount
                if spore_near_townhall >= 1:
                    # print("Already have SPORECRAWLER:", spore_near_townhall)
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.SPORECRAWLER,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.SPORECRAWLER, placement_position)
                if success:
                    # print("Successfully built Spore Crawler")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        if not self.time_exceeds(5):
            if "Lair" in name:
                if self._has_lair_tech() or self._try_morph_lair():
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                return
            return

        elif "Lair" in name:
            if self._has_lair_tech() or self._try_morph_lair():
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
            return

        elif "Roach Warren" in name:
            if self.already_pending(UnitTypeId.ROACHWARREN):
                # print("ROACHWARREN is already pending")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            if self.structures(UnitTypeId.ROACHWARREN).amount >= 1:
                # print("Already have ROACHWARREN:", self.structures(UnitTypeId.ROACHWARREN).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            if self.can_afford(UnitTypeId.ROACHWARREN) and not self.structures(
                    UnitTypeId.ROACHWARREN).ready.exists and self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.ROACHWARREN,
                                                               near=pos)

                if await self.can_place_single(UnitTypeId.ROACHWARREN, placement_position):
                    drone: Unit = self.workers.closest_to(placement_position)
                    success = drone.build(UnitTypeId.ROACHWARREN, placement_position)
                    if success:
                        # print("Successfully built Roach Warren")
                        if i < len(self.parsed_commands):
                            del self.parsed_commands[i]
                        return


        elif "Baneling Nest" in name:
            if self.structures(UnitTypeId.BANELINGNEST).amount >= 1 and self.already_pending(UnitTypeId.BANELINGNEST):
                # print("Already have BANELINGNEST:", self.structures(UnitTypeId.BANELINGNEST).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if self.can_afford(UnitTypeId.BANELINGNEST) and not self.structures(
                    UnitTypeId.BANELINGNEST).exists and not self.already_pending(UnitTypeId.BANELINGNEST):
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.BANELINGNEST,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.BANELINGNEST, placement_position)
                if success:
                    # print("Successfully built Baneling Nest")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Hydralisk" in name:
            if self.structures(UnitTypeId.HYDRALISKDEN).amount >= 1:
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if not self._has_lair_tech():
                self._try_morph_lair()
                return

            if self.can_afford(UnitTypeId.HYDRALISKDEN) and not self.structures(
                    UnitTypeId.HYDRALISKDEN).ready.exists and (
                    self.townhalls(UnitTypeId.LAIR).ready.exists or self.townhalls(UnitTypeId.HIVE).ready.exists):
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.HYDRALISKDEN,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.HYDRALISKDEN, placement_position)
                if success:
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Infestation Pit" in name:
            if self.structures(UnitTypeId.INFESTATIONPIT).amount >= 1:
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if not self._has_lair_tech():
                self._try_morph_lair()
                return

            if self.can_afford(UnitTypeId.INFESTATIONPIT) and not self.structures(
                    UnitTypeId.INFESTATIONPIT).ready.exists and (
                    self.townhalls(UnitTypeId.LAIR).ready.exists or self.townhalls(UnitTypeId.HIVE).ready.exists):
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.INFESTATIONPIT,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.INFESTATIONPIT, placement_position)
                if success:
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Greater Spire" not in name and "Spire" in name:
            if self.structures(UnitTypeId.SPIRE).amount >= 1 and not self.already_pending(UnitTypeId.SPIRE):
                # print("Already have SPIRE:", self.structures(UnitTypeId.SPIRE).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if self.can_afford(UnitTypeId.SPIRE) and not self.structures(
                    UnitTypeId.SPIRE).exists and self.townhalls(UnitTypeId.LAIR).ready.exists:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 6.0)
                placement_position = await self.find_placement(UnitTypeId.SPIRE,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.SPIRE, placement_position)
                if success:
                    # print("Successfully built Spire")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        # print("self.time_exceeds(10):", self.time_exceeds(10))
        if not self.time_exceeds(10):
            return

        elif "Hive" in name:
            if self.townhalls(UnitTypeId.HIVE).exists or self.already_pending(UnitTypeId.HIVE):
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            if not self.townhalls(UnitTypeId.LAIR).ready.exists:
                self._try_morph_lair()
                return
            if self.can_afford(UnitTypeId.HIVE) and self.structures(UnitTypeId.INFESTATIONPIT).ready.exists:
                lair = self.townhalls(UnitTypeId.LAIR).ready.first
                success = lair.build(UnitTypeId.HIVE)
                if success:
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                return

        elif "Ultralisk Cavern" in name:
            # if self.townhalls(UnitTypeId.LAIR).exists:
            if self.townhalls(UnitTypeId.LAIR).ready.exists:
                if not self.townhalls(UnitTypeId.HIVE).ready.exists and not self.already_pending(UnitTypeId.HIVE):
                    if self.can_afford(UnitTypeId.HIVE):
                        lair = self.townhalls(UnitTypeId.LAIR).ready.first
                        success = lair.build(UnitTypeId.HIVE)
            else:
                if self.can_afford(UnitTypeId.LAIR):
                    if self.start_location_label == "A1":
                        base_location = Point2(self.mineral_location_labels_reverse["A1"])
                    elif self.start_location_label == "B1":
                        base_location = Point2(self.mineral_location_labels_reverse["B1"])

                    if self.townhalls.closer_than(2.0, base_location).ready.exists:    
                        hatchery = self.townhalls.closer_than(2.0, base_location).ready.first
                        success = hatchery.build(UnitTypeId.LAIR)
                        if success:
                            print("build Lair success")

            if self.structures(UnitTypeId.ULTRALISKCAVERN).amount >= 1:
                # print("Already have ULTRALISKCAVERN:", self.structures(UnitTypeId.ULTRALISKCAVERN).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if self.can_afford(UnitTypeId.ULTRALISKCAVERN) and self.townhalls(
                    UnitTypeId.HIVE).ready.exists and not self.structures(UnitTypeId.ULTRALISKCAVERN).exists:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                if self.start_location_label == "A1":
                    next_place = Point2(self.mineral_location_labels_reverse["A2"])
                elif self.start_location_label == "B1":
                    next_place = Point2(self.mineral_location_labels_reverse["B2"])
                pos2 = base_location.position.to2.towards(next_place, 5.0)
                placement_position = await self.find_placement(UnitTypeId.ULTRALISKCAVERN,
                                                               near=pos2)

                success = self.workers.collecting.random.build(UnitTypeId.ULTRALISKCAVERN, placement_position)
                if success:
                    # print("Successfully built Ultralisk Cavern")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Greater Spire" in name:
            if not self.townhalls(UnitTypeId.HIVE).ready.exists and not self.already_pending(UnitTypeId.HIVE):
                if self.can_afford(UnitTypeId.HIVE) and self.townhalls(UnitTypeId.LAIR).ready.exists:
                # if self.can_afford(UnitTypeId.HIVE) and self.townhalls(UnitTypeId.LAIR).exists:
                    lair = self.townhalls(UnitTypeId.LAIR).ready.first
                    success = lair.build(UnitTypeId.HIVE)
                    if success:
                        print("Build Hive")

            if self.can_afford(UnitTypeId.GREATERSPIRE) and self.structures(UnitTypeId.SPIRE).ready.exists:
                spire = self.structures(UnitTypeId.SPIRE).ready.first
                success = spire.build(UnitTypeId.GREATERSPIRE)
                if success:
                    print("Build Greater Spire")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return


        else:
            if i < len(self.parsed_commands):
                del self.parsed_commands[i]
            return

    async def build_units(self, name, i):
        if "Drone" in name:
            # print("build drone")
            if self.can_afford(UnitTypeId.DRONE) and self.units(UnitTypeId.LARVA).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.DRONE)

                # print("Successfully built Drone")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]

        elif "Overlord" in name:
            if self.can_afford(UnitTypeId.OVERLORD) and self.units(UnitTypeId.LARVA).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.OVERLORD)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

        elif "Zergling" in name:
            if self.can_afford(UnitTypeId.ZERGLING) and self.structures(
                    UnitTypeId.SPAWNINGPOOL).ready.exists and self.units(
                UnitTypeId.LARVA).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.ZERGLING)

                # print("Successfully built Zergling")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

        elif "Roach" in name:
            if self.can_afford(UnitTypeId.ROACH) and self.structures(
                    UnitTypeId.ROACHWARREN).ready.exists and self.units(
                UnitTypeId.LARVA).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.ROACH)

                # print("Successfully built Roach")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

        elif "Ravager" in name:
            if not self.units(UnitTypeId.ROACH).exists:
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            if self.can_afford(UnitTypeId.RAVAGER) and self.supply_left > 0:
                roach = self.units(UnitTypeId.ROACH).ready.idle
                if not roach.exists:
                    roach = self.units(UnitTypeId.ROACH).ready
                if roach.exists:
                    roach.random(AbilityId.MORPHTORAVAGER_RAVAGER)
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Lair" in name:
            if self._has_lair_tech() or self._try_morph_lair():
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
            return

        elif "Hydralisk" in name:
            if not self.townhalls.exists and not self.units(UnitTypeId.LARVA).exists:
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return
            if self.can_afford(UnitTypeId.HYDRALISK) and self.structures(
                    UnitTypeId.HYDRALISKDEN).ready.exists and self.units(UnitTypeId.LARVA).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.HYDRALISK)

                # print("Successfully built Hydralisk")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

        elif "Mutalisk" in name:
            if self.can_afford(UnitTypeId.MUTALISK) and self.structures(UnitTypeId.SPIRE).ready.exists and self.units(
                UnitTypeId.LARVA
            ).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.MUTALISK)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

    def _consume_parsed_command(self, i: int) -> None:
        if 0 <= i < len(self.parsed_commands):
            del self.parsed_commands[i]

    def _resolve_attack_point(self, location: str = "", payload: str = "") -> Point2:
        loc = location or self.check_loc(payload or "")
        coords = self.mineral_location_labels_reverse.get(loc)
        if coords:
            return Point2(coords)
        enemies = self.enemy_units.of_type(ENEMY_COMBAT_UNIT_TYPES)
        if enemies.exists:
            return enemies.center
        if self.enemy_structures.exists:
            army = self._combat_units()
            anchor = army.center if army.exists else self.start_location
            return self.enemy_structures.closest_to(anchor).position
        starts = list(getattr(self.game_info, "enemy_start_locations", None) or [])
        if starts:
            return starts[0]
        enemy_label = "B1" if self.start_location_label == "A1" else "A1"
        fallback = self.mineral_location_labels_reverse.get(enemy_label)
        if fallback:
            return Point2(fallback)
        return getattr(self.game_info, "map_center", self.start_location)

    def _issue_typed_attack(self, type_ids, loc: Point2) -> int:
        issued = 0
        if not type_ids:
            return 0
        for unit in self.units.of_type(type_ids).ready:
            unit.attack(loc)
            issued += 1
        return issued

    def _match_combat_types(self, actor: str):
        for key, type_ids in COMBAT_ACTOR_TYPES:
            if key in actor:
                return type_ids
        return set()

    async def _dispatch_army_command(self, actor: str, action: str, payload: str, i: int, location: str) -> bool:
        type_ids = self._match_combat_types(actor)
        if not type_ids:
            return False
        action_l = str(action or "").lower()
        payload_l = str(payload or "").lower()
        if "baneling" in payload_l and "Zergling" in actor:
            return False
        if any(key in action_l for key in ("move", "attack", "harass", "scout", "push")):
            loc = self._resolve_attack_point(location, payload)
            self._issue_typed_attack(type_ids, loc)
            self._consume_parsed_command(i)
            return True
        return False

    def check_loc(self, input_str):
        loc_list = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"]
        for loc in loc_list:
            if loc in input_str:
                return loc
        return ""

    def find_safety(self, position):
        friendly_location = min(self.structures.ready,
                                key=lambda unit: unit.position.distance_to(position)).position
        return friendly_location

    def _main_base_position(self):
        if self.start_location_label in self.mineral_location_labels_reverse:
            return Point2(self.mineral_location_labels_reverse[self.start_location_label])
        return self.start_location

    async def _build_structure_near_main(self, structure_id, distance: float = 6.0):
        if not self.workers.exists:
            return False
        base_position = self._main_base_position()
        near = base_position.towards(self.game_info.map_center, distance)
        placement = await self.find_placement(structure_id, near=near)
        if not placement:
            return False
        worker = self.workers.collecting.closest_to(placement) if self.workers.collecting.exists else self.workers.random
        return bool(worker.build(structure_id, placement))

    async def _build_next_hatchery(self):
        if not self.workers.exists:
            return False

        if self.start_location_label == "B1":
            expansion_order = ["B2", "B3", "B5", "B4", "A5"]
        else:
            expansion_order = ["A2", "A3", "A5", "A4", "B5"]

        existing = self.townhalls.ready
        for label in expansion_order:
            coords = self.mineral_location_labels_reverse.get(label)
            if not coords:
                continue
            pos = Point2(coords)
            if existing.closer_than(6.0, pos).exists:
                continue
            if not await self.can_place(UnitTypeId.HATCHERY, pos):
                continue
            worker = self.workers.collecting.closest_to(pos) if self.workers.collecting.exists else self.workers.random
            if worker.build(UnitTypeId.HATCHERY, pos):
                self.waiting_for_hatchery = False
                return True
        return False

    async def macro_safety_net(self):
        """Low-latency macro guardrails so LLM delays do not stall the bot economy."""
        if not self.macro_safety_net_enabled:
            return

        if not self.townhalls.ready.exists:
            return

        game_time = float(getattr(self, "time", 0.0) or 0.0)
        pending_overlords = int(self.already_pending(UnitTypeId.OVERLORD) or 0)
        pending_hatcheries = int(self.already_pending(UnitTypeId.HATCHERY) or 0)
        pending_pool = int(self.already_pending(UnitTypeId.SPAWNINGPOOL) or 0)
        pending_warren = int(self.already_pending(UnitTypeId.ROACHWARREN) or 0)

        if (
            self.supply_cap < 200
            and self.supply_left <= 2
            and pending_overlords == 0
            and self.units(UnitTypeId.LARVA).exists
            and self.can_afford(UnitTypeId.OVERLORD)
        ):
            self.units(UnitTypeId.LARVA).random.train(UnitTypeId.OVERLORD)
            return

        if (
            not self.structures(UnitTypeId.SPAWNINGPOOL).exists
            and pending_pool == 0
            and self.can_afford(UnitTypeId.SPAWNINGPOOL)
        ):
            if await self._build_structure_near_main(UnitTypeId.SPAWNINGPOOL, distance=5.0):
                return

        if game_time >= 180 and self.supply_left > 1 and self.units(UnitTypeId.LARVA).exists:
            if (
                self.structures(UnitTypeId.ROACHWARREN).ready.exists
                and self.units(UnitTypeId.ROACH).amount < 8
                and self.can_afford(UnitTypeId.ROACH)
            ):
                self.units(UnitTypeId.LARVA).random.train(UnitTypeId.ROACH)
                return
            if (
                self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
                and self.units(UnitTypeId.ZERGLING).amount < 16
                and self.can_afford(UnitTypeId.ZERGLING)
            ):
                self.units(UnitTypeId.LARVA).random.train(UnitTypeId.ZERGLING)
                return

        extractor_count = self.structures(UnitTypeId.EXTRACTOR).ready.amount + int(self.already_pending(UnitTypeId.EXTRACTOR) or 0)
        worker_target = min(66, self.townhalls.ready.amount * 18 + extractor_count * 3)
        if game_time < 210:
            worker_target = min(worker_target, 28)
        elif game_time < 420:
            worker_target = min(worker_target, 48)

        if (
            self.workers.amount < worker_target
            and self.supply_left > 1
            and self.units(UnitTypeId.LARVA).exists
            and self.can_afford(UnitTypeId.DRONE)
            and not (self.base_being_attacked and self.army_count < 8)
        ):
            self.units(UnitTypeId.LARVA).random.train(UnitTypeId.DRONE)
            return

        desired_hatcheries = 1
        if game_time >= 150 and (self.workers.amount >= 20 or self.minerals >= 500):
            desired_hatcheries = 2
        if game_time >= 360 and self.workers.amount >= 36 and self.army_count >= 8:
            desired_hatcheries = 3

        if (
            self.townhalls.amount + pending_hatcheries < desired_hatcheries
            and self.can_afford(UnitTypeId.HATCHERY)
            and not self.base_being_attacked
        ):
            if await self._build_next_hatchery():
                return

        if (
            game_time >= 210
            and self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
            and not self.structures(UnitTypeId.ROACHWARREN).exists
            and pending_warren == 0
            and self.can_afford(UnitTypeId.ROACHWARREN)
        ):
            if await self._build_structure_near_main(UnitTypeId.ROACHWARREN, distance=7.0):
                return

        pending_baneling_nest = int(self.already_pending(UnitTypeId.BANELINGNEST) or 0)
        if (
            game_time >= 180
            and self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
            and not self.structures(UnitTypeId.BANELINGNEST).exists
            and pending_baneling_nest == 0
            and self.units(UnitTypeId.ZERGLING).amount >= 10
            and self.can_afford(UnitTypeId.BANELINGNEST)
        ):
            if await self._build_structure_near_main(UnitTypeId.BANELINGNEST, distance=7.0):
                return

        if (
            game_time >= 270
            and self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
            and not self._has_lair_tech()
        ):
            if self._try_morph_lair():
                return

        pending_hydra_den = int(self.already_pending(UnitTypeId.HYDRALISKDEN) or 0)
        if (
            game_time >= 360
            and (self.townhalls(UnitTypeId.LAIR).ready.exists or self.townhalls(UnitTypeId.HIVE).ready.exists)
            and not self.structures(UnitTypeId.HYDRALISKDEN).exists
            and pending_hydra_den == 0
            and self.can_afford(UnitTypeId.HYDRALISKDEN)
        ):
            if await self._build_structure_near_main(UnitTypeId.HYDRALISKDEN, distance=7.0):
                return

        if game_time >= 150 and self.supply_left > 1 and self.units(UnitTypeId.LARVA).exists:
            if (
                self.structures(UnitTypeId.ROACHWARREN).ready.exists
                and self.units(UnitTypeId.ROACH).amount < max(8, self.early_roach_num)
                and self.can_afford(UnitTypeId.ROACH)
            ):
                self.units(UnitTypeId.LARVA).random.train(UnitTypeId.ROACH)
                return
            if (
                self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
                and self.units(UnitTypeId.ZERGLING).amount < max(12, self.early_zergling_num)
                and self.can_afford(UnitTypeId.ZERGLING)
            ):
                self.units(UnitTypeId.LARVA).random.train(UnitTypeId.ZERGLING)
                return

    async def Drone_ReflexNet(self):
        # print("Drone_ReflexNet")
        await self.autocreating_drones()

        for drone in self.workers.idle:
            if self.townhalls.ready.exists:
                closest_hatchery = self.townhalls.ready.closest_to(drone)
                closest_minerals = self.mineral_field.closer_than(30, closest_hatchery)
                if closest_minerals.exists:
                    drone.gather(closest_minerals.closest_to(closest_hatchery))

        for drone in self.workers:
            threats = self.enemy_units.closer_than(5, drone)
            if threats:
                # print("Drone detected enemy!")
                no_threats = threats.filter(lambda unit: unit.can_attack_ground and unit.type_id == UnitTypeId.SCV)
                low_threats = threats.filter(lambda unit: unit.can_attack_ground and unit.type_id == UnitTypeId.MARINE)
                high_threats = threats.filter(
                    lambda
                        unit: unit.can_attack_ground and unit.type_id != UnitTypeId.MARINE and unit.type_id != UnitTypeId.SCV)
                if self.worker_aggressive == False:
                    if low_threats.amount > 0 and low_threats.amount <= 2:
                        for d in self.workers.closer_than(5, drone):
                            d.attack(low_threats.closest_to(d))

                    if high_threats.exists:
                        enemy_center = high_threats.center
                        for d in self.workers.closer_than(5, drone):
                            retreat_point = d.position.towards_with_random_angle(enemy_center, -5)
                            d.move(retreat_point)
                        continue

                    if not self.enemy_units.closer_than(10, drone):
                        bases = self.townhalls(UnitTypeId.HATCHERY).ready
                        if bases and self.mineral_field.exists:
                            base = max(bases, key=lambda b: b.ideal_harvesters - b.assigned_harvesters)
                            drone.gather(self.mineral_field.closest_to(base))

                else:
                    if high_threats.exists:
                        for d in self.workers.closer_than(8, drone):
                            d.attack(high_threats.closest_to(d))

                    if low_threats.exists:
                        for d in self.workers.closer_than(8, drone):
                            d.attack(low_threats.closest_to(d))

    async def autocreating_drones(self):
        all_hatcheries = self.townhalls.ready
        mineral_fields = self.mineral_field
        drones = self.units(UnitTypeId.DRONE)

        drones_per_mineral_patch = 2

        for hatchery in all_hatcheries:
            nearby_mineral_patches = mineral_fields.closer_than(10, hatchery)
            if not nearby_mineral_patches.exists:
                continue
            optimal_drones = drones_per_mineral_patch * nearby_mineral_patches.amount

            hatchery_drones = drones.closer_than(10, hatchery)

            if len(hatchery_drones) < optimal_drones:
                if self.can_afford(UnitTypeId.DRONE) and self.larva:
                    self.train(UnitTypeId.DRONE, amount=3)

    async def auto_build_extractors(self):
        for hatchery in self.townhalls.ready:
            if self.workers.closer_than(10, hatchery).amount > 14:
                if self.can_afford(UnitTypeId.EXTRACTOR):
                    for vg in self.vespene_geyser.closer_than(10, hatchery):
                        drone: Unit = self.workers.random
                        drone.build_gas(vg)

    async def auto_research(self):
        if self.units(UnitTypeId.OVERLORD).amount >= 5:
            if self.can_afford(UpgradeId.OVERLORDSPEED) and self.townhalls.exists:
                self.research(UpgradeId.OVERLORDSPEED)

        if self.units(UnitTypeId.ZERGLING).amount >= 5:
            if self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists and self.can_afford(
                    UpgradeId.ZERGLINGMOVEMENTSPEED):
                self.research(UpgradeId.ZERGLINGMOVEMENTSPEED)

        if self.units(UnitTypeId.BANELING).amount >= 4 and self.structures(
                UnitTypeId.BANELINGNEST).ready.exists and self.already_pending_upgrade(
            UpgradeId.CENTRIFICALHOOKS) == 0:
            if self.can_afford(UpgradeId.CENTRIFICALHOOKS):
                success = self.research(UpgradeId.CENTRIFICALHOOKS)

        if self.units(UnitTypeId.ZERGLING).amount >= 8 and self.structures(
                UnitTypeId.EVOLUTIONCHAMBER).ready.exists and self.already_pending_upgrade(
            UpgradeId.ZERGMELEEWEAPONSLEVEL1) == 0:
            if self.can_afford(UpgradeId.ZERGMELEEWEAPONSLEVEL1):
                success = self.research(UpgradeId.ZERGMELEEWEAPONSLEVEL1)

        if self.units(UnitTypeId.ROACH).amount >= 5 and self.structures(
                UnitTypeId.EVOLUTIONCHAMBER).ready.exists and self.already_pending_upgrade(
            UpgradeId.ZERGMISSILEWEAPONSLEVEL1) == 0:
            if self.can_afford(UpgradeId.ZERGMISSILEWEAPONSLEVEL1):
                success = self.research(UpgradeId.ZERGMISSILEWEAPONSLEVEL1)

        if self.units(UnitTypeId.ROACH).amount >= 8 and self.structures(
                UnitTypeId.EVOLUTIONCHAMBER).ready.exists and self.already_pending_upgrade(
            UpgradeId.ZERGGROUNDARMORSLEVEL1) == 0:
            if self.can_afford(UpgradeId.ZERGGROUNDARMORSLEVEL1):
                success = self.research(UpgradeId.ZERGGROUNDARMORSLEVEL1)

        if self.structures(UnitTypeId.EVOLUTIONCHAMBER).ready.exists and self.structures(UnitTypeId.LAIR).ready.exists:
            if self.can_afford(UpgradeId.ZERGMELEEWEAPONSLEVEL2) and self.already_pending_upgrade(
                    UpgradeId.ZERGMELEEWEAPONSLEVEL2) == 0:
                success = self.research(UpgradeId.ZERGMELEEWEAPONSLEVEL2)
                # if not success:
                #     print("ZERGMELEEWEAPONSLEVEL2 failed! ", success)
            if self.can_afford(UpgradeId.ZERGMISSILEWEAPONSLEVEL2) and self.already_pending_upgrade(
                    UpgradeId.ZERGMISSILEWEAPONSLEVEL2) == 0:
                success = self.research(UpgradeId.ZERGMISSILEWEAPONSLEVEL2)
                # if not success:
                #     print("ZERGMELEEWEAPONSLEVEL2 failed!", success)
            if self.can_afford(UpgradeId.ZERGGROUNDARMORSLEVEL2) and self.already_pending_upgrade(
                    UpgradeId.ZERGGROUNDARMORSLEVEL2) == 0:
                success = self.research(UpgradeId.ZERGGROUNDARMORSLEVEL2)
                # if not success:
                #     print("ZERGGROUNDARMORSLEVEL2 failed!", success)

        if self.structures(UnitTypeId.EVOLUTIONCHAMBER).ready.exists and self.structures(UnitTypeId.HIVE).ready.exists:
            if self.can_afford(UpgradeId.ZERGMELEEWEAPONSLEVEL3) and self.already_pending_upgrade(
                    UpgradeId.ZERGMELEEWEAPONSLEVEL3) == 0:
                success = self.research(UpgradeId.ZERGMELEEWEAPONSLEVEL3)
                # if not success:
                #     print("ZERGMELEEWEAPONSLEVEL3 failed!", success)
            if self.can_afford(UpgradeId.ZERGMISSILEWEAPONSLEVEL3) and self.already_pending_upgrade(
                    UpgradeId.ZERGMISSILEWEAPONSLEVEL3) == 0:
                success = self.research(UpgradeId.ZERGMISSILEWEAPONSLEVEL3)
                # if not success:
                #     print("ZERGMISSILEWEAPONSLEVEL3 failed!", success)
            if self.can_afford(UpgradeId.ZERGGROUNDARMORSLEVEL3) and self.already_pending_upgrade(
                    UpgradeId.ZERGGROUNDARMORSLEVEL3) == 0:
                success = self.research(UpgradeId.ZERGGROUNDARMORSLEVEL3)
                # if not success:
                #     print("ZERGGROUNDARMORSLEVEL3 failed!", success)

        if self.units(UnitTypeId.ROACH).amount > 5:
            if self.can_afford(UpgradeId.GLIALRECONSTITUTION) and not self.already_pending_upgrade(
                    UpgradeId.GLIALRECONSTITUTION):
                success = self.research(UpgradeId.GLIALRECONSTITUTION)
                # if not success:
                #     print("GLIALRECONSTITUTION failed:", success)

        if self.units(UnitTypeId.HYDRALISK).amount > 5:
            if self.can_afford(UpgradeId.EVOLVEMUSCULARAUGMENTS):
                success = self.research(UpgradeId.EVOLVEMUSCULARAUGMENTS)
                if not success:
                    print("EVOLVEMUSCULARAUGMENTS failed!", success)

    async def overlord_random_scout(self, overlord):
        if overlord.is_idle:
            all_coordinates = list(self.expansion_locations.keys())
            scout_position = random.choice(all_coordinates)
            overlord.move(scout_position)

    async def Overlord_ReflexNet(self):
        if self.supply_cap < 44:
            if self.supply_left < 3 and not self.already_pending(UnitTypeId.OVERLORD):
                larvas = self.units(UnitTypeId.LARVA)
                if larvas.exists and self.can_afford(UnitTypeId.OVERLORD):
                    # print("train overlord supply_left:", self.supply_left)
                    larvas.random.train(UnitTypeId.OVERLORD)
        elif self.supply_cap >= 44 and self.supply_cap != 200:
            if self.supply_left >= 0 and self.supply_left < 5:
                larvas = self.units(UnitTypeId.LARVA)
                if larvas.exists and self.can_afford(UnitTypeId.OVERLORD):
                    # print("train overlord supply_left:", self.supply_left)
                    larvas.random.train(UnitTypeId.OVERLORD)

        for overlord in self.units(UnitTypeId.OVERLORD):
            threats = self.enemy_units.filter(lambda unit: unit.can_attack_air).closer_than(15, overlord)

            if threats:
                # self.detect_threat(threats)
                if self.base_being_attacked == False:
                    retreat_point = self.find_safety(overlord.position)
                    overlord.move(retreat_point)
                else:
                    overlord.move(self.start_location)
            else:
                await self.overlord_random_scout(overlord)

    async def overlord_scout(self, i, location):
        if location == "":
            return
        loc = Point2(self.mineral_location_labels_reverse[location])
        overlords = self.units(UnitTypeId.OVERLORD)
        if overlords.exists:
            closest_overlord = overlords.closest_to(loc)
            closest_overlord.move(loc)
            # print("Successfully move Overlord")
            if i < len(self.parsed_commands):
                del self.parsed_commands[i]
            return

    async def zergling_command(self, name, action, i, location):
        if 'baneling' in str(name or "").lower():
            if self.can_afford(UnitTypeId.BANELING) and self.structures(
                    UnitTypeId.BANELINGNEST).ready.exists and self.supply_left > 0:
                zergling = self.units(UnitTypeId.ZERGLING).ready.idle
                if zergling.exists:
                    zergling.random(AbilityId.MORPHTOBANELING_BANELING)
                    self._consume_parsed_command(i)
                    return

        if await self._dispatch_army_command("Zergling", action, name, i, location):
            return

    async def roach_command(self, name, action, i, location):
        if 'ravager' in str(name or "").lower():
            if not self.units(UnitTypeId.ROACH).exists:
                self._consume_parsed_command(i)
                return
            if self.can_afford(UnitTypeId.RAVAGER) and self.structures(
                    UnitTypeId.ROACHWARREN).ready.exists and self.supply_left > 0:
                roach = self.units(UnitTypeId.ROACH).ready.idle
                if not roach.exists:
                    roach = self.units(UnitTypeId.ROACH).ready
                if roach.exists:
                    roach.random(AbilityId.MORPHTORAVAGER_RAVAGER)
                    self._consume_parsed_command(i)
                    return

        if await self._dispatch_army_command("Roach", action, name, i, location):
            return

    async def Zergling_ReflexNet(self):
        # print("Zergling_ReflexNet")
        for zergling in self.units(UnitTypeId.ZERGLING).ready:
            threats = self.enemy_units.closer_than(15, zergling)
            if threats:
                # print("zergling detected enemy!")
                sigetank = threats.filter(lambda unit: unit.type_id == UnitTypeId.SIEGETANK)
                if sigetank.exists:
                    sigetank_attack = sigetank.closest_to(zergling)
                    # print("zergling attack sigetank!")
                    zergling.attack(sigetank_attack)

    async def Queen_ReflexNet(self):
        # print("Queen_ReflexNet")
        all_hatcheries = self.townhalls.ready
        len_hatcheries = self.townhalls.ready.amount

        if self.units(UnitTypeId.QUEEN).amount < len_hatcheries * 4:
            if self.can_afford(UnitTypeId.QUEEN) and self.structures(
                    UnitTypeId.SPAWNINGPOOL).ready.exists and not self.already_pending(UnitTypeId.QUEEN):
                self.train(UnitTypeId.QUEEN)

        for hatchery in all_hatcheries:
            queens_nearby = self.units(UnitTypeId.QUEEN).closer_than(8, hatchery)
            if not queens_nearby.exists and self.base_being_attacked == False:
                if self.can_afford(UnitTypeId.QUEEN) and self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists:
                    self.train(UnitTypeId.QUEEN)

            if self.hatchery_queen_pairs.get(hatchery.tag) is None:
                queens_nearby = self.units(UnitTypeId.QUEEN).idle.closer_than(8, hatchery)
                if queens_nearby:
                    assignable_queens = [q for q in queens_nearby if
                                         q.energy >= 25 and q.tag not in self.hatchery_queen_pairs.values()]
                    if assignable_queens:
                        closest_queen = assignable_queens[0]
                        self.hatchery_queen_pairs[hatchery.tag] = closest_queen.tag

            queen_tag = self.hatchery_queen_pairs.get(hatchery.tag)
            if queen_tag is not None:
                queen = self.units(UnitTypeId.QUEEN).find_by_tag(queen_tag)
                if queen:
                    abilities = await self.get_available_abilities(queen)
                    if AbilityId.EFFECT_INJECTLARVA in abilities:
                        queen(AbilityId.EFFECT_INJECTLARVA, hatchery)
                else:
                    # print("Queen dead")
                    del self.hatchery_queen_pairs[hatchery.tag]

    async def queen_spread(self):
        hatcheries = self.townhalls(UnitTypeId.HATCHERY) | self.townhalls(UnitTypeId.LAIR) | self.townhalls(
            UnitTypeId.HIVE)
        self.existing_hatchery_locations = []
        for label, coordinate in self.mineral_location_labels_reverse.items():
            hatchery = hatcheries.closer_than(8, Point2(coordinate)).ready
            if hatchery.exists:
                self.existing_hatchery_locations.append(label)

        print("existing_hatchery_locations:", self.existing_hatchery_locations)

        spread_sequence_A = ["A1", "A2", "A3", "B5"]
        spread_sequence_B = ["B1", "B2", "B3", "A5"]
        spread_sequence = spread_sequence_A if self.start_location_label == "A1" else spread_sequence_B

        for queen in self.units(UnitTypeId.QUEEN).idle:
            if queen.tag not in self.hatchery_queen_pairs.values():
                if queen.energy >= 25:
                    if queen.tag not in self.queen_spread_progress:
                        self.queen_spread_progress[queen.tag] = 0
                    current_progress = self.queen_spread_progress[queen.tag]  # 0, 1, 2

                    if current_progress >= len(spread_sequence):
                        continue

                    current_target_label = spread_sequence[current_progress]  # "A1", "A2", "A4", "A3", "A7", "A4", "B4"
                    # print("current_target_label:", current_target_label)
                    next_target_label = spread_sequence[(current_progress + 1) % len(spread_sequence)]
                    # print("next_target_label:", next_target_label)
                    current_target = Point2(self.mineral_location_labels_reverse[current_target_label])
                    next_target = Point2(self.mineral_location_labels_reverse[next_target_label])

                    if self.start_location_label == "A1":
                        if next_target_label in ["B4"] and not await self.is_hatchery_ready_at(next_target_label):
                            continue
                    elif self.start_location_label == "B1":
                        if next_target_label in ["A4"] and not await self.is_hatchery_ready_at(next_target_label):
                            continue

                    spread_position = await self.find_creep_placement(AbilityId.BUILD_CREEPTUMOR_QUEEN,
                                                                      current_target.towards(next_target,
                                                                                             self.spread_distance))

                    if spread_position:
                        queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, spread_position)
                        self.queen_spread_progress[queen.tag] = current_progress + 1
                        self.spread_distance = self.spread_distance + 3

    async def find_creep_placement(self, ability, center, max_radius=5, step=1):
        for r in range(0, max_radius, step):
            position = await self.find_placement(ability, center, max_distance=r)
            if position:
                return position
        return None

    async def is_hatchery_ready_at(self, location_label):
        target_location = Point2(self.mineral_location_labels_reverse[location_label])
        hatcheries = self.units(UnitTypeId.HATCHERY).ready
        for hatchery in hatcheries:
            if hatchery.position.to2.distance_to(target_location) < 5:  # 5可根据实际情况调整为合适的距离
                return True
        return False

    def get_high_priority_targets_roach(self, enemies):
        high_priority = enemies.of_type({UnitTypeId.SIEGETANK, UnitTypeId.THOR, UnitTypeId.MARAUDER})
        if high_priority.exists:
            return high_priority
        return enemies

    def position_around_unit(self, pos: Union[Unit, Point2, Point3], distance: int = 1, step_size: int = 1,
                             exclude_out_of_bounds: bool = True):
        pos = pos.position.rounded
        positions = {
            pos.offset(Point2((x, y)))
            for x in range(-distance, distance + 1, step_size) for y in range(-distance, distance + 1, step_size)
            if (x, y) != (0, 0)
        }
        # filter positions outside map size
        if exclude_out_of_bounds:
            positions = {
                p
                for p in positions
                if 0 <= p[0] < self.game_info.pathing_grid.width and 0 <= p[1] < self.game_info.pathing_grid.height
            }
        return positions

    async def Roach_ReflexNet(self):
        # print("Roach_ReflexNet")
        for roach in self.units(UnitTypeId.ROACH):
            enemies_in_range = self.enemy_units.filter(lambda u: u.distance_to(roach) < 9)
            if enemies_in_range:
                if roach.weapon_cooldown == 0:
                    high_priority_targets = self.get_high_priority_targets_roach(enemies_in_range)
                    target = min(high_priority_targets, key=lambda u: u.health + u.shield)
                    roach.attack(target)

                else:
                    stutter_step_positions = self.position_around_unit(roach, distance=4)
                    # filter in pathing grid
                    stutter_step_positions = {p for p in stutter_step_positions if self.in_pathing_grid(p)}
                    # find position furthest away from enemies and closest to unit
                    enemies_in_range = self.enemy_units.filter(lambda u: roach.target_in_range(u, -0.5))

                    if stutter_step_positions and enemies_in_range:
                        retreat_position = max(
                            stutter_step_positions,
                            key=lambda x: x.distance_to(enemies_in_range.center) - x.distance_to(roach),
                        )
                        roach.move(retreat_position)

    async def Ravager_ReflexNet(self):
        # print("Ravager_ReflexNet")
        for ravager in self.units(UnitTypeId.RAVAGER):
            enemies_in_range = self.enemy_units.filter(lambda u: u.distance_to(ravager) < 9)
            if enemies_in_range:
                high_priority_targets = self.get_high_priority_targets_roach(enemies_in_range)
                if high_priority_targets:
                    target = min(high_priority_targets, key=lambda t: t.health + t.shield)

                    if ravager(AbilityId.EFFECT_CORROSIVEBILE, target.position):
                        # Ravager weapon is ready, find the highest priority target
                        stutter_step_positions = self.position_around_unit(ravager, distance=4)

                        # filter in pathing grid
                        stutter_step_positions = {p for p in stutter_step_positions if self.in_pathing_grid(p)}

                        # find position furthest away from enemies and closest to unit
                        enemies_in_range = self.enemy_units.filter(lambda u: ravager.target_in_range(u, -0.5))

                        if stutter_step_positions and enemies_in_range:
                            retreat_position = max(
                                stutter_step_positions,
                                key=lambda x: x.distance_to(enemies_in_range.center) - x.distance_to(ravager),
                            )
                            ravager.move(retreat_position)
                else:
                    stutter_step_positions = self.position_around_unit(ravager, distance=4)

                    # filter in pathing grid
                    stutter_step_positions = {p for p in stutter_step_positions if self.in_pathing_grid(p)}

                    # find position furthest away from enemies and closest to unit
                    enemies_in_range = self.enemy_units.filter(lambda u: ravager.target_in_range(u, -0.5))

                    if stutter_step_positions and enemies_in_range:
                        retreat_position = max(
                            stutter_step_positions,
                            key=lambda x: x.distance_to(enemies_in_range.center) - x.distance_to(ravager),
                        )
                        ravager.move(retreat_position)

    async def Hydralisk_ReflexNet(self):
        # print("Hydralisk_ReflexNet")
        if self.units(UnitTypeId.HYDRALISK).ready.exists:
            for hydralisk in self.units(UnitTypeId.HYDRALISK).ready:
                threats = self.enemy_units.closer_than(6, hydralisk)
                if threats:
                    # print("hydralisk detected enemy!")
                    medivac = threats.filter(lambda unit: unit.type_id == UnitTypeId.MEDIVAC)
                    if medivac.exists:
                        medivac_attack = medivac.closest_to(hydralisk)
                        # print("hydralisk attack medivac!")
                        hydralisk.attack(medivac_attack)

    async def Ultralisk_ReflexNet(self):
        # print("Ultralisk_ReflexNet")
        if self.structures(UnitTypeId.ULTRALISKCAVERN).ready.exists:
            ultralisk_num = 0
            if self.game_stage == 0:
                ultralisk_num = self.early_ultralisk_num
            elif self.game_stage == 1:
                ultralisk_num = self.mid_ultralisk_num
            elif self.game_stage == 2:
                ultralisk_num = self.late_ultralisk_num

            if self.units(UnitTypeId.ULTRALISK).amount < ultralisk_num:
                if self.units(UnitTypeId.LARVA).exists:
                    larva = self.units(UnitTypeId.LARVA).random
                    larva.train(UnitTypeId.ULTRALISK)

    def get_high_priority_targets_corruptor(self, enemies):
        high_priority = enemies.of_type({UnitTypeId.MEDIVAC, UnitTypeId.RAVEN})
        if high_priority.exists:
            return high_priority
        return enemies

    def get_high_priority_targets_infestor(self, enemies):
        high_priority = enemies.of_type({UnitTypeId.MARINE, UnitTypeId.MARAUDER})
        if high_priority.exists:
            return high_priority
        return enemies

    async def Corruptor_ReflexNet(self):
        # print("Corruptor_ReflexNet")
        for corruptor in self.units(UnitTypeId.CORRUPTOR):
            if self.enemy_units:
                if corruptor.weapon_cooldown == 0:
                    enemies_in_range = self.enemy_units.filter(lambda u: corruptor.target_in_range(u))
                    if enemies_in_range:
                        high_priority_targets = self.get_high_priority_targets_corruptor(enemies_in_range)
                        target = min(high_priority_targets, key=lambda u: u.health + u.shield)
                        corruptor.attack(target)

                else:
                    stutter_step_positions = self.position_around_unit(corruptor, distance=4)
                    # filter in pathing grid
                    stutter_step_positions = {p for p in stutter_step_positions if self.in_pathing_grid(p)}
                    # find position furthest away from enemies and closest to unit
                    enemies_in_range = self.enemy_units.filter(lambda u: corruptor.target_in_range(u, -0.5))

                    if stutter_step_positions and enemies_in_range:
                        retreat_position = max(
                            stutter_step_positions,
                            key=lambda x: x.distance_to(enemies_in_range.center) - x.distance_to(corruptor),
                        )
                        corruptor.move(retreat_position)

        if self.units(UnitTypeId.CORRUPTOR).exists and self.supply_left > 2:
            if self.structures(UnitTypeId.GREATERSPIRE).ready.exists and self.structures(UnitTypeId.HIVE).ready.exists:
                corruptor = self.units(UnitTypeId.CORRUPTOR).random
                corruptor(AbilityId.TRAIN_SWARMHOST)

    async def infestor_ReflexNet(self):
        # print("infestor_ReflexNet")
        if self.structures(UnitTypeId.INFESTATIONPIT).ready.exists and self.structures(
                UnitTypeId.INFESTATIONPIT).ready.exists:
            infestor_num = 0
            if self.game_stage == 0:
                infestor_num = self.early_infestor_num
            elif self.game_stage == 1:
                infestor_num = self.mid_infestor_num
            elif self.game_stage == 2:
                infestor_num = self.late_infestor_num

            if self.units(UnitTypeId.INFESTOR).amount <= infestor_num and self.units(UnitTypeId.LARVA).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.INFESTOR)

        for infestor in self.units(UnitTypeId.INFESTOR):
            enemies_in_range = self.enemy_units.filter(lambda u: u.distance_to(infestor) < 9)
            if enemies_in_range:
                high_priority_targets = self.get_high_priority_targets_infestor(enemies_in_range)
                target = min(high_priority_targets, key=lambda t: t.health + t.shield)

                if infestor(AbilityId.FUNGALGROWTH_FUNGALGROWTH, target.position):
                    # Ravager weapon is ready, find the highest priority target
                    stutter_step_positions = self.position_around_unit(infestor, distance=5)

                    # filter in pathing grid
                    stutter_step_positions = {p for p in stutter_step_positions if self.in_pathing_grid(p)}

                    # find position furthest away from enemies and closest to unit
                    enemies_in_range = self.enemy_units.filter(lambda u: infestor.target_in_range(u, -0.5))

                    if stutter_step_positions and enemies_in_range:
                        retreat_position = max(
                            stutter_step_positions,
                            key=lambda x: x.distance_to(enemies_in_range.center) - x.distance_to(infestor),
                        )
                        infestor.move(retreat_position)

                else:
                    stutter_step_positions = self.position_around_unit(infestor, distance=4)
                    # filter in pathing grid
                    stutter_step_positions = {p for p in stutter_step_positions if self.in_pathing_grid(p)}
                    # find position furthest away from enemies and closest to unit
                    enemies_in_range = self.enemy_units.filter(lambda u: infestor.target_in_range(u, -0.5))

                    if stutter_step_positions and enemies_in_range:
                        retreat_position = max(
                            stutter_step_positions,
                            key=lambda x: x.distance_to(enemies_in_range.center) - x.distance_to(infestor),
                        )
                        infestor.move(retreat_position)

    async def handle_commands(self):
        # if self.start_location_label == "A1":
        #     base_location = Point2(self.mineral_location_labels_reverse["A1"])
        # elif self.start_location_label == "B1":
        #     base_location = Point2(self.mineral_location_labels_reverse["B1"])

        self._prune_stale_parsed_commands()
        filtered_commands = []

        for command_list in self.parsed_commands:
            if len(command_list) != 3:
                continue

            action, location = command_list[1], command_list[2]
            if 'Build' in action and ', ' in location:
                building, loc_code = location.rsplit(', ', 1)
                base_location_coords = self.mineral_location_labels_reverse.get(loc_code)
                if base_location_coords and "Hatchery" in command_list[2]:
                    base_location = Point2(base_location_coords)
                    hatchery = self.townhalls.closer_than(2.0, base_location).ready
                    if hatchery.exists:
                        print("Hatchery exists in", loc_code)
                        self.waiting_for_hatchery = False
                        continue
                    else:
                        if "Hatchery" in building and self.minerals <= 400:
                            self.waiting_for_hatchery = True
                            print("self.waiting_for_hatchery set to True")
                            continue
            filtered_commands.append(command_list)
        self.parsed_commands = filtered_commands
        print("filtered self.parsed_commands:", self.parsed_commands)
        snapshot_before_dispatch = [list(c) for c in self.parsed_commands]

        for command_list in self.parsed_commands:
            i = self.parsed_commands.index(command_list)
            actor = command_list[0]
            action = command_list[1]
            payload = command_list[2]

            # NOTE: 早期版本只允许 Drone/Larva/... 作为指令主体，导致诸如
            # "(Hatchery)->(Build)->(Lair)" / "(Lair)->(Build)->(Hive)" 这类“科技树提升”
            # 指令会被当作未知指令直接删除。这里把关键建筑主体也纳入路由。
            is_builder_actor = any(
                key in actor
                for key in (
                    "Drone",
                    "Hatchery",
                    "Lair",
                    "Hive",
                    "Spire",
                    "Greater Spire",
                    "Evolution Chamber",
                    "Roach Warren",
                    "Hydralisk Den",
                    "Baneling Nest",
                    "Infestation Pit",
                    "Ultralisk Cavern",
                )
            )

            if any(key in actor for key in ("Hatchery", "Lair", "Hive")) and any(
                key in action for key in ("Morph", "Train", "Produce")
            ):
                if "Larva" in payload:
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    continue
                if "Queen" in payload:
                    if self.can_afford(UnitTypeId.QUEEN) and self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists:
                        self.train(UnitTypeId.QUEEN)
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    continue
                if any(key in payload for key in ("Lair", "Hive")):
                    location = self.check_loc(payload) or self.start_location_label or "B1"
                    await self.build_buildings(payload, i, location)
                    continue
                await self.build_units(payload, i)

            elif is_builder_actor:
                # print("Drone")
                location = self.check_loc(payload)
                if location == "":
                    if self.start_location_label == "A1":
                        location = "A1"
                    elif self.start_location_label == "B1":
                        location = "B1"
                await self.build_buildings(payload, i, location)

            elif "Larva" in actor:
                # print("Larva")
                await self.build_units(payload, i)

            elif "Overlord" in actor:
                location = self.check_loc(payload) or self.check_loc(action)
                if not location:
                    location = "B1" if self.start_location_label == "A1" else "A1"
                await self.overlord_scout(i, location)

            elif "Zergling" in actor:
                location = self.check_loc(payload) or self.check_loc(action)
                await self.zergling_command(payload, action, i, location)

            elif "Roach" in actor:
                location = self.check_loc(payload) or self.check_loc(action)
                await self.roach_command(payload, action, i, location)

            elif self._match_combat_types(actor):
                location = self.check_loc(payload) or self.check_loc(action)
                if await self._dispatch_army_command(actor, action, payload, i, location):
                    continue
                self._consume_parsed_command(i)

            elif (
                any(key in str(action).lower() for key in ("attack", "harass", "push"))
                and not any(
                    key in actor
                    for key in ("Queen", "Drone", "Larva", "Overlord", "Hatchery", "Lair", "Hive", "Extractor")
                )
            ):
                loc = self._resolve_attack_point(self.check_loc(payload) or self.check_loc(action), payload)
                self._issue_typed_attack(COMBAT_UNIT_TYPES, loc)
                self._consume_parsed_command(i)
                print("generic army attack from", actor, action, payload)

            else:
                self._consume_parsed_command(i)
                print("Deleted command_list at index", i)

        # Only log commands removed this step (actual consume), not the full pending queue every frame.
        remaining = {tuple(c) for c in self.parsed_commands if len(c) == 3}
        consumed = [c for c in snapshot_before_dispatch if len(c) == 3 and tuple(c) not in remaining]
        if consumed:
            log_dispatched_logged_commands(self.run_logger, self, consumed, channel="macro")
        self._sync_dashboard_commands()

    async def build_drones_for_second_hatchery(self):
        second_hatcheries = self.townhalls.ready.exclude_type({UnitTypeId.HATCHERY})
        if second_hatcheries.exists:
            second_hatchery = second_hatcheries.closest_to(self.start_location)
            if second_hatchery.assigned_harvesters < second_hatchery.ideal_harvesters:
                larvas = self.units(UnitTypeId.LARVA).closer_than(10.0, second_hatchery)
                if larvas.exists and self.can_afford(UnitTypeId.DRONE):
                    larva = larvas.random
                    larva.train(UnitTypeId.DRONE)


def extract_units_info(stage_info):
    # early_match = re.search(r'Early stage:(.*?)Mid stage:', stage_info, re.DOTALL)
    # mid_match = re.search(r'Mid stage:(.*?)Late stage:', stage_info, re.DOTALL)
    # late_match = re.search(r'Late stage:(.*)', stage_info, re.DOTALL)
    #
    # early_stage_text = early_match.group(1).strip() if early_match else ""
    # mid_stage_text = mid_match.group(1).strip() if mid_match else ""
    # late_stage_text = late_match.group(1).strip() if late_match else ""

    # pattern = r'(?<=Zergling: )\d+|(?<=Baneling: )\d+|(?<=Roach: )\d+|(?<=Ravager: )\d+|(?<=Hydralisk: )\d+|(?<=Infestor: )\d+|(?<=Swarm host: )\d+|(?<=Mutalisk: )\d+|(?<=Corruptor: )\d+|(?<=Viper: )\d+|(?<=Ultralisk: )\d+|(?<=Brood Lord: )\d+'

    # def extract_numbers(text):
    #     matches = re.findall(pattern, text)
    #     return [int(match) for match in matches]

    # 加上 re.IGNORECASE，忽略大小写，防止 LLM 输出 "Early Stage:" 导致匹配失败
    early_match = re.search(r'Early stage:(.*?)Mid stage:', stage_info, re.IGNORECASE | re.DOTALL)
    mid_match = re.search(r'Mid stage:(.*?)Late stage:', stage_info, re.IGNORECASE | re.DOTALL)
    late_match = re.search(r'Late stage:(.*)', stage_info, re.IGNORECASE | re.DOTALL)

    early_stage_text = early_match.group(1).strip() if early_match else ""
    mid_stage_text = mid_match.group(1).strip() if mid_match else ""
    late_stage_text = late_match.group(1).strip() if late_match else ""

    # 更新后的正则逻辑，能够处理 Markdown 格式（例如：**Zergling:** 20）
    def extract_numbers(text):
        # 按照固定顺序查找单位名称（忽略大小写），允许中间出现 Markdown 符号/冒号/空格，然后提取数字
        unit_order = ['Zergling', 'Baneling', 'Roach', 'Ravager', 'Hydralisk', 'Infestor', 'Swarm host', 'Mutalisk',
                      'Corruptor', 'Viper', 'Ultralisk', 'Brood Lord']
        counts = []
        for unit in unit_order:
            # 查找单位名称，随后跟随任意非数字字符，最后捕获数字
            match = re.search(rf'{unit}[^\d]*(\d+)', text, re.IGNORECASE)
            counts.append(int(match.group(1)) if match else 0)
        return counts

    early_stage_units = extract_numbers(early_stage_text)
    mid_stage_units = extract_numbers(mid_stage_text)
    late_stage_units = extract_numbers(late_stage_text)

    return early_stage_units, mid_stage_units, late_stage_units


def assign_values(units_list):
    zergling_num = units_list[0]
    baneling_num = units_list[1]
    roach_num = units_list[2]
    ravager_num = units_list[3]
    hydralisk_num = units_list[4]
    infestor_num = units_list[5]
    swarm_host_num = units_list[6]
    mutalisk_num = units_list[7]
    corruptor_num = units_list[8]
    viper_num = units_list[9]
    ultralisk_num = units_list[10]
    brood_lord_num = units_list[11]

    return (zergling_num, baneling_num, roach_num, ravager_num,
            hydralisk_num, infestor_num, swarm_host_num, mutalisk_num,
            corruptor_num, viper_num, ultralisk_num, brood_lord_num)


def _start_dashboard_redis_bridge_background(state_path=DASHBOARD_STATE_PATH, *, side: str = "") -> None:
    """将 dashboard_state.json 同步到 Redis，供 MCP / Ask API 读取；独立于旧版独立 bridge 脚本。"""
    off = os.getenv("SWARMBRAIN_DISABLE_REDIS_BRIDGE", "").strip().lower()
    if off in ("1", "true", "yes", "on"):
        print("[redis-bridge] 已通过 SWARMBRAIN_DISABLE_REDIS_BRIDGE 关闭后台同步。")
        return

    interval = float(os.getenv("SWARMBRAIN_REDIS_BRIDGE_INTERVAL", "0.5"))
    ttl_s = int(os.getenv("SWARMBRAIN_REDIS_BRIDGE_TTL", "30"))
    retries = int(os.getenv("SWARMBRAIN_REDIS_BRIDGE_READ_RETRIES", "5"))

    def _runner() -> None:
        try:
            import dashboard_to_redis_bridge as br

            br.run_persistent_mirror_to_redis(
                Path(state_path).resolve(),
                side=side,
                poll_interval_s=interval,
                ttl_seconds=ttl_s,
                read_attempts=retries,
                once=False,
                log_prefix=f"[redis-bridge:{side or 'default'}]",
            )
        except Exception as exc:  # pragma: no cover - 后台线程容错
            print(f"[redis-bridge] 同步线程退出: {exc}", flush=True)

    t = threading.Thread(target=_runner, name="dashboard-redis-bridge", daemon=True)
    t.start()
    print("[redis-bridge] 已启动后台同步（如需关闭请设置 SWARMBRAIN_DISABLE_REDIS_BRIDGE=1）。")


def _enum_by_name(enum_cls, raw: str, default):
    key = str(raw or "").strip().lower()
    for item in enum_cls:
        if str(item.name).lower() == key:
            return item
    return default


def build_pve_participants(bot: SwarmBrain, red_spec):
    """我方永远是玩家 1（红方）；对手永远是玩家 2（蓝方）。"""
    settings = resolve_pve_settings()
    if settings.opponent == "human":
        human_race = _enum_by_name(Race, settings.human_race, Race.Terran)
        return [
            Bot(Race.Zerg, bot, name=red_spec.display_name, fullscreen=red_spec.fullscreen),
            Human(human_race, name="Human", fullscreen=True),
        ], {
            "mode": "human",
            "label": f"SwarmBrain(Zerg, red/P1) vs Human({human_race.name}, blue/P2)",
            "replay_prefix": "ZvH",
        }
    race = _enum_by_name(Race, settings.computer_race, Race.Terran)
    difficulty = _enum_by_name(Difficulty, settings.difficulty, Difficulty.Medium)
    return [
        Bot(Race.Zerg, bot, name=red_spec.display_name, fullscreen=red_spec.fullscreen),
        Computer(race, difficulty),
    ], {
        "mode": "computer",
        "label": f"SwarmBrain(Zerg, red/P1) vs Computer({race.name}, {difficulty.name}, blue/P2)",
        "replay_prefix": f"Zv{race.name[:1]}",
    }


def main():
    from swarmbrain_pilot_config import (
        apply_pilot_config_to_env,
        build_manifest_config_fields,
        is_pilot_mode_requested,
        load_pilot_config,
    )

    duel_specs = build_duel_agent_specs(ROOT_DIR)
    red_spec, _blue_spec = duel_specs
    pve = resolve_pve_settings()
    bot_player_id = 1
    creds = get_swarmbrain_llm_credentials()

    pilot_cfg = None
    if is_pilot_mode_requested():
        pilot_cfg = load_pilot_config()
        apply_pilot_config_to_env(pilot_cfg, override_existing=True)
        creds = get_swarmbrain_llm_credentials()

    map_name = str((pilot_cfg or {}).get("map_name") or "AutomatonLE")

    run_logger = SwarmBrainRunLogger(
        package_root=ROOT_DIR,
        mode="single_agent_pve",
        map_name=map_name,
        model_name=str(creds.get("model") or ""),
    )
    if pilot_cfg:
        fields = build_manifest_config_fields(pilot_cfg, model_name=str(creds.get("model") or ""))
        fields.pop("test_config", None)
        run_logger.update_manifest(**fields, map_name_runtime=map_name)
        print(
            f"[pilot] config={fields.get('config_name')} v={fields.get('config_version')} "
            f"sha256={str(fields.get('config_sha256') or '')[:12]}…",
            flush=True,
        )
    else:
        test_config = {
            "SWARMBRAIN_REALTIME": os.getenv("SWARMBRAIN_REALTIME"),
            "SWARMBRAIN_MAX_GAME_TIME_SEC": os.getenv("SWARMBRAIN_MAX_GAME_TIME_SEC"),
            "SWARMBRAIN_MACRO_EVERY": os.getenv("SWARMBRAIN_MACRO_EVERY", "120"),
            "SWARMBRAIN_BUILDING_EVERY": os.getenv("SWARMBRAIN_BUILDING_EVERY", "60"),
            "SWARMBRAIN_ATTACK_EVERY": os.getenv("SWARMBRAIN_ATTACK_EVERY", "150"),
            "SWARMBRAIN_ATTACK_MIN_ARMY": os.getenv("SWARMBRAIN_ATTACK_MIN_ARMY", "28"),
        }
        run_logger.update_manifest(test_config=test_config, map_name_runtime=map_name)
    run_logger.write_run_event("run_started", mode="single_agent_pve", opponent=pve.opponent)
    if pve.opponent == "human":
        run_logger.register_agents(
            [
                {"agent_id": "red", "agent_side": "red", "display_name": red_spec.display_name, "race": "Zerg", "player_id": 1},
                {"agent_id": "human", "agent_side": "blue", "display_name": "Human", "race": pve.human_race, "player_id": 2},
            ]
        )
    else:
        run_logger.register_agents(
            [
                {"agent_id": "red", "agent_side": "red", "display_name": red_spec.display_name, "race": "Zerg", "player_id": 1},
                {
                    "agent_id": "computer",
                    "agent_side": "blue",
                    "display_name": f"Computer {pve.difficulty}",
                    "race": pve.computer_race,
                    "player_id": 2,
                },
            ]
        )
    _start_dashboard_redis_bridge_background(red_spec.dashboard_state_path, side="red")
    _start_dashboard_redis_bridge_background(ROOT_DIR / "dashboard_state_blue.json", side="blue")
    dashboard_writer = DashboardStateWriter(
        ROOT_DIR,
        red_spec.dashboard_state_path,
        game_embed_url=os.getenv("SWARMBRAIN_GAME_EMBED_URL", "/game-view/"),
    )
    dashboard_writer.update_map_layout(MINERAL_LOCATION_LABELS_REVERSE)
    dashboard_writer.update_runtime(
        agentId=red_spec.agent_id,
        agentName=red_spec.display_name,
        side=red_spec.agent_id,
    )

    opening_common = dict(
        channel="opening",
        agent_id="shared_opening",
        agent_side="shared",
        player_id=None,
        race=None,
        feature_scope="audit_only",
        phase="pregame",
        game_loop=None,
        game_time_sec=None,
        game_time_formatted=None,
    )

    token = begin_llm_context(
        run_logger,
        input_state={"phase": "stage_force_composition"},
        opening_index=1,
        commands_expected=False,
        prompt_template_version="opening_stage_v1",
        **opening_common,
    )
    try:
        stage_info = overmind_brain_initial()
    finally:
        end_llm_context(
            token,
            parsed_commands=[],
            parse_status="not_applicable",
            commands_expected=False,
        )
    print(stage_info)

    early_units, mid_units, late_units = extract_units_info(stage_info)
    print("Early stage units:", early_units)
    early_values = assign_values(early_units)

    print("Mid stage units:", mid_units)
    mid_values = assign_values(mid_units)

    print("Late stage units:", late_units)
    late_values = assign_values(late_units)

    token = begin_llm_context(
        run_logger,
        input_state={"phase": "strategy_true_false"},
        opening_index=2,
        commands_expected=False,
        prompt_template_version="opening_strategy_v1",
        **opening_common,
    )
    try:
        strategy = overmind_brain_initial2()
    finally:
        end_llm_context(
            token,
            parsed_commands=[],
            parse_status="not_applicable",
            commands_expected=False,
        )
    print(strategy)

    results = {}
    for line in strategy.strip().split('\n'):
        if not line.strip() or ':' not in line:
            continue
        parts = line.split(':', 1)
        if len(parts) == 2 and "Question" in parts[0]:
            question = parts[0].strip()
            answer = parts[1].strip()
            if 'False' in answer:
                results[question] = False
            elif 'True' in answer:
                results[question] = True

    drone_attack = results.get('Question 1', False)
    counterattack = results.get('Question 2', True)

    opening_fallback = False
    call_id = None
    token = begin_llm_context(
        run_logger,
        input_state={"phase": "opening_macro_plan"},
        opening_index=3,
        commands_expected=True,
        prompt_template_version="opening_macro_v1",
        expect_fallback=True,
        **opening_common,
    )
    try:
        output1 = overmind_brain_1()
        call_id = end_llm_context(token, auto_complete=False)
        token = None
    except Exception as exc:
        print(f"[opening-plan] LLM unavailable; using deterministic fallback: {exc}")
        opening_fallback = True
        call_id = end_llm_context(token, auto_complete=False)
        token = None
        fb_cmds = extract_raw_commands(fallback_opening_plan())
        output1 = fallback_opening_plan()
        if call_id and call_id in run_logger._open_calls:
            run_logger.fail_llm_call(call_id, error=exc, fallback_used=True)
        call_id = run_logger.start_llm_call(
            prompt="(unavailable)",
            input_state={"phase": "opening_macro_plan"},
            opening_index=3,
            prompt_template_version="opening_macro_v1",
            **opening_common,
        )
        run_logger.stash_llm_response(call_id, raw_response=output1)
        run_logger.select_fallback(
            call_id,
            reason=str(exc),
            strategy_name="fallback_opening_plan",
            commands=fb_cmds,
            channel="opening",
        )
    finally:
        if token is not None:
            call_id = end_llm_context(token, auto_complete=False)
    print(output1)
    matches = re.findall(r'\(.*?\)->\(.*?\)->\(.*?\)', output1)
    command_first = list(matches)
    if call_id:
        complete_llm_with_commands(
            run_logger,
            call_id,
            parsed_commands=list(command_first),
            commands_expected=True,
            channel="opening",
            fallback_used=opening_fallback,
        )
        opening_logged = log_parsed_command_batch(
            run_logger,
            llm_call_id=call_id,
            channel="opening",
            proposed=list(command_first),
            accepted=list(command_first),
            source="fallback" if opening_fallback else "llm",
            agent_id="shared_opening",
            agent_side="shared",
            player_id=None,
            race=None,
        )
    else:
        opening_logged = []

    dashboard_writer.update_message(
        "attack",
        strategy,
        title="开局策略问答",
        input_summary="???????????????????????????????",
    )
    dashboard_writer.update_message(
        "macro",
        output1,
        title="开局宏观方案",
        input_summary="????????????????????????????????",
    )
    dashboard_writer.update_message(
        "latest",
        output1,
        title="开局宏观方案",
        input_summary="????????????????????????????????",
    )
    dashboard_writer.update_commands(command_first)

    our_bot = SwarmBrain(
        command_first,
        early_values,
        mid_values,
        late_values,
        drone_attack,
        counterattack,
        agent_id=red_spec.agent_id,
        display_name=red_spec.display_name,
        dashboard_state_path=red_spec.dashboard_state_path,
        feature_map_enabled=red_spec.feature_map_enabled,
        run_logger=run_logger,
        agent_side="red",
        player_id=bot_player_id,
    )
    our_bot.opening_logged_commands = []
    _ = opening_logged  # retained in command_events as shared proposed/accepted

    our_bot.dashboard_state.update_message(
        "attack",
        strategy,
        title="开局策略问答",
        agent_name=our_bot.display_name,
    )
    our_bot.dashboard_state.update_message(
        "macro",
        output1,
        title="开局宏观方案",
        agent_name=our_bot.display_name,
    )
    our_bot.dashboard_state.update_commands(command_first)

    participants, pve_meta = build_pve_participants(our_bot, red_spec)
    print(f"[pve] 启动人机对局：{pve_meta['label']}", flush=True)

    replay_rel = f"./videos/{pve_meta['replay_prefix']}_PVE_{run_logger.run_id}.SC2Replay"
    replay_abs = (ROOT_DIR / "videos" / f"{pve_meta['replay_prefix']}_PVE_{run_logger.run_id}.SC2Replay").resolve()
    run_status = "completed"
    try:
        run_logger.write_run_event("game_connected", map_name=map_name)
        # Default realtime=True; set SWARMBRAIN_REALTIME=0 for faster offline runs.
        realtime = os.getenv("SWARMBRAIN_REALTIME", "1").strip().lower() not in {"0", "false", "no", "off"}
        if realtime:
            print("[sc2-ui] realtime=True：窗口可切视角、可控兵（机器人下令可能覆盖）。", flush=True)
        else:
            print("[sc2-ui] realtime=False：对局在模型思考时暂停，窗口通常只能拖镜头。", flush=True)
        # Optional: SWARMBRAIN_MAX_GAME_TIME_SEC ends the match cleanly via burnysc2 game_time_limit.
        max_t_raw = os.getenv("SWARMBRAIN_MAX_GAME_TIME_SEC", "").strip()
        game_time_limit = float(max_t_raw) if max_t_raw else None
        result = run_game(
            maps.get(map_name) or maps.get("AutomatonLE"),
            participants,
            realtime=realtime,
            save_replay_as=replay_rel,
            disable_fog=False,
            game_time_limit=game_time_limit,
        )
        result_map = None
        try:
            if isinstance(result, (list, tuple)) and len(result) >= 2:
                result_map = {"player_1": str(result[0]), "player_2": str(result[1])}
            elif result is not None:
                result_map = {"raw": str(result)}
        except Exception:
            result_map = None
        final_loop = run_logger._last_known_game_loop
        final_sec = run_logger._last_known_game_time_sec
        replay_ok = run_logger.replay_belongs_to_this_run(replay_abs)
        result_blob = " ".join(str(v) for v in ((result_map or {}).values()))
        decisive = any(
            tok in result_blob
            for tok in ("Victory", "Defeat", "Win", "Loss")
        )
        tied = ("Tie" in result_blob) and not decisive
        if game_time_limit and tied:
            term = "test_time_limit"
        elif decisive:
            term = "natural_end"
        elif game_time_limit:
            # Limit configured but result ambiguous — mark safety_limit_possible
            term = "test_time_limit_or_unspecified"
        else:
            term = "natural_end"
        run_logger.attach_replay(
            replay_abs if replay_ok else None,
            result=result_map,
            runtime_result=result_map,
            final_game_loop=final_loop,
            final_game_time_sec=final_sec,
            termination_reason=term,
            save_error=None if replay_ok else "replay path missing",
        )
    except KeyboardInterrupt:
        run_status = "aborted"
        run_logger.attach_replay(
            replay_abs if run_logger.replay_belongs_to_this_run(replay_abs) else None,
            save_error="aborted_by_user",
            termination_reason="aborted_by_user",
            final_game_loop=run_logger._last_known_game_loop,
            final_game_time_sec=run_logger._last_known_game_time_sec,
        )
        raise
    except AssertionError as exc:
        # Recover only when THIS run wrote a fresh replay (avoid stale shared replay paths).
        replay_ok = run_logger.replay_belongs_to_this_run(replay_abs)
        run_status = "completed" if replay_ok else "crashed"
        run_logger.write_error(
            component="run_game",
            operation="run_game_result_assert",
            error_type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
            recoverable=bool(replay_ok),
            action_taken="continue" if replay_ok else "abort_run",
            exc=exc,
            game_loop=run_logger._last_known_game_loop,
        )
        run_logger.attach_replay(
            replay_abs if replay_ok else None,
            result={"note": "recovered_after_result_assert"} if replay_ok else None,
            runtime_result={"note": "recovered_after_result_assert"} if replay_ok else None,
            save_error=None if replay_ok else (str(exc) or type(exc).__name__),
            termination_reason="test_time_limit_or_result_assert" if replay_ok else "crash",
            final_game_loop=run_logger._last_known_game_loop,
            final_game_time_sec=run_logger._last_known_game_time_sec,
        )
        if not replay_ok:
            raise
    except Exception as exc:
        run_status = "crashed"
        run_logger.write_error(
            component="run_game",
            operation="run_game",
            error_type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
            recoverable=False,
            action_taken="abort_run",
            exc=exc,
            game_loop=run_logger._last_known_game_loop,
        )
        run_logger.attach_replay(
            replay_abs if run_logger.replay_belongs_to_this_run(replay_abs) else None,
            save_error=str(exc) or type(exc).__name__,
            termination_reason="crash",
            final_game_loop=run_logger._last_known_game_loop,
            final_game_time_sec=run_logger._last_known_game_time_sec,
        )
        raise
    finally:
        print(f"[run-logger] run_id={run_logger.run_id} dir={run_logger.run_dir}", flush=True)
        run_logger.finalize(run_status)



if __name__ == '__main__':
    main()
