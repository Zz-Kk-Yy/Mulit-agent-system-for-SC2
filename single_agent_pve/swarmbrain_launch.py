import os

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import json
import re
import asyncio
import random
import threading
from collections import Counter
from typing import Any, Union
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.constants import *
from sc2.data import Race, Difficulty, ActionResult, Result
from sc2.main import run_game
from sc2 import main as sc2_main
from sc2.sc2process import SC2Process as OrigSC2Process
from sc2.player import Bot, Computer, Human
from sc2.unit import Unit
from sc2.position import Point2, Point3
from sc2.units import Units

from feature_map_exporter import build_live_feature_map_exporter, enable_feature_layer_join_patch
from dashboard_state import DashboardStateWriter
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

_TOWNHALL_TYPES = {
    UnitTypeId.COMMANDCENTER,
    UnitTypeId.ORBITALCOMMAND,
    UnitTypeId.PLANETARYFORTRESS,
    UnitTypeId.COMMANDCENTERFLYING,
    UnitTypeId.ORBITALCOMMANDFLYING,
    UnitTypeId.HATCHERY,
    UnitTypeId.LAIR,
    UnitTypeId.HIVE,
    UnitTypeId.NEXUS,
}
_FLYING_TOWNHALL_TYPES = {
    UnitTypeId.COMMANDCENTERFLYING,
    UnitTypeId.ORBITALCOMMANDFLYING,
}
_NON_ARMY_TYPES = {
    UnitTypeId.DRONE,
    UnitTypeId.SCV,
    UnitTypeId.PROBE,
    UnitTypeId.MULE,
    UnitTypeId.OVERLORD,
    UnitTypeId.OVERSEER,
    UnitTypeId.LARVA,
    UnitTypeId.EGG,
}
_GG_CHAT_RE = re.compile(
    r"(?is)^\s*(?:"
    r"g+\s*g+(?:\s*wp)?|ggwp|good\s*games?|wp|"
    r"投降|认输|我认输|已投降|"
    r"(?:i\s+)?(?:gg|surrender|resign)(?:ed)?"
    r")\s*[!！.~。]*\s*$"
)
_GG_LOOSE_RE = re.compile(
    r"(?is)(?:(?<![a-z])gg(?:wp)?(?![a-z])|good\s*game|投降|认输|"
    r"has\s+left(?:\s+the\s+game)?|left\s+the\s+game|"
    r"已离开(?:游戏)?|对手已离开)"
)
try:
    _MATCH_TIME_LIMIT_SEC = max(60.0, float(os.getenv("SWARMBRAIN_MATCH_TIME_LIMIT_SEC", "1260")))
except ValueError:
    _MATCH_TIME_LIMIT_SEC = 1260.0


def _match_time_limit_clock() -> str:
    sec = int(round(_MATCH_TIME_LIMIT_SEC))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _manual_quit_mode() -> bool:
    return os.getenv("SWARMBRAIN_MANUAL_QUIT", "0").strip().lower() in {"1", "true", "yes", "on"}


def _board_gg_not_win() -> bool:
    return os.getenv("SWARMBRAIN_BOARD_GG_NOT_WIN", "0").strip().lower() in {"1", "true", "yes", "on"}


class SwarmBrain(BotAI):
    raw_affects_selection = False

    def __init__(self, command_first, early_values, mid_values, late_values, drone_attack, counterattack):
        super().__init__()
        self.agent_id = "player_1"
        self.agent_side = "single"
        self.player_id = 1
        self.run_logger = None
        self._match_result = None
        self._claimed_outcome = None
        self._outcome_reason = None
        self._no_enemy_th_steps = 0
        self._no_own_th_steps = 0
        self._cleanup_steps = 0
        self._sweep_i = 0
        self._seen_enemy_townhall = False
        self._visited_enemy_start = False
        self._last_flying_cc_time = -999.0
        self._last_board_win = False
        self._gg_seen = False
        self._our_gg_seen = False
        self._enemy_gg_steps = 0
        self._enemy_eliminated_steps = 0
        self._seen_chat_keys = set()
        self._last_own_th_count = 0
        self._last_enemy_th_count = 0
        self._last_enemy_army_count = 0
        self._experiment_quiet = os.getenv("SWARMBRAIN_EXPERIMENT_QUIET", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
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

        self.command_list = command_first
        self.building_tasks = []
        self.attack_tasks = []
        self.macro_tasks = []

        self.mineral_location_labels = dict(MINERAL_LOCATION_LABELS)
        self.mineral_location_labels_reverse = dict(MINERAL_LOCATION_LABELS_REVERSE)

        self.existing_hatchery_locations = []

        self.worker_aggressive = drone_attack
        self.counterattack = counterattack

        self.hatchery_queen_pairs = {}
        self.waiting_for_hatchery = True
        self.is_attack_command_issued = False

        self.previous_enemy_units = {}
        self.previous_enemy_units_attack = {}
        self.previous_enemy_damage = 0
        self.previous_enemy_buildings = {}
        self.previous_commands = []

        self.base_being_attacked = False
        self.defence = False

        self.spread_distance = 13

        self.game_stage = 0

        self.attack_wave = 0

        self.fight_back = False
        self.queen_spread_progress = {}
        self.feature_map_exporter = build_live_feature_map_exporter()
        self.dashboard_state = DashboardStateWriter(
            ROOT_DIR,
            DASHBOARD_STATE_PATH,
            game_embed_url=os.getenv("SWARMBRAIN_GAME_EMBED_URL", ""),
        )
        self.dashboard_state.update_map_layout(self.mineral_location_labels_reverse)

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

        self.command_list = self.filter_commands(self.command_list,
                                                 ['Queen', 'Gather minerals', 'Extractor', 'Mineral', 'Overlord',
                                                  'Move', 'Zergling', 'Creep'])

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

        for command in Overmind_commands:
            parts = pattern.findall(command)
            self.parsed_commands.append(parts)
        # print("self.parsed_commands:", self.parsed_commands)
        self._update_dashboard_runtime()
        self._sync_dashboard_commands()

    def _qprint(self, *args, **kwargs) -> None:
        if not self._experiment_quiet:
            print(*args, **kwargs)

    async def on_end(self, game_result):
        print(f"[match-result] {game_result}", flush=True)
        if self._claimed_outcome is not None:
            print(
                f"[match-result] ignore on_end={game_result}, keep claimed {self._claimed_outcome}",
                flush=True,
            )
            return
        if game_result == Result.Victory:
            self._claimed_outcome = Result.Victory
            self._match_result = Result.Victory
            self._outcome_reason = "engine_on_end"
            print("[match-claim] Victory reason=engine_on_end", flush=True)
            _write_experiment_sidecar(
                outcome="Victory",
                result_raw=game_result,
                run_status="completed",
                run_logger=self.run_logger,
                extra={
                    "claimed": True,
                    "claim_reason": "engine_on_end",
                    "engine_on_end": str(game_result),
                    "gg_seen": self._gg_seen,
                    **_match_clock_fields(self),
                },
            )
            return
        if game_result == Result.Defeat and _manual_quit_mode():
            self._claimed_outcome = Result.Defeat
            self._match_result = Result.Defeat
            self._outcome_reason = "user_quit"
            print(
                "[match-claim] user_quit "
                f"gg_seen={self._gg_seen} board_gg={self._opponent_gg_confirmed()} "
                f"th={self._last_enemy_th_count} army={self._last_enemy_army_count}",
                flush=True,
            )
            _write_experiment_sidecar(
                outcome="user_quit",
                result_raw=game_result,
                run_status="completed",
                run_logger=self.run_logger,
                extra={
                    "claimed": False,
                    "claim_reason": "user_quit",
                    "engine_on_end": str(game_result),
                    "gg_seen": self._gg_seen,
                    "opponent_gg_confirmed": self._opponent_gg_confirmed(),
                    "last_enemy_th_count": self._last_enemy_th_count,
                    "last_enemy_army_count": self._last_enemy_army_count,
                    "last_own_th_count": self._last_own_th_count,
                    "note": "玩家手动退出。胜负由退出后的对手GG检查决定，退出本身不记胜。",
                    **_match_clock_fields(self),
                },
            )
            return
        if game_result == Result.Defeat and self._gg_seen:
            self._claimed_outcome = Result.Victory
            self._match_result = Result.Victory
            self._outcome_reason = "on_end_after_enemy_gg_chat"
            print("[match-claim] Victory reason=on_end_after_enemy_gg_chat", flush=True)
            _write_experiment_sidecar(
                outcome="Victory",
                result_raw=game_result,
                run_status="completed",
                run_logger=self.run_logger,
                extra={
                    "claimed": True,
                    "claim_reason": "on_end_after_enemy_gg_chat",
                    "engine_on_end": str(game_result),
                    "gg_seen": True,
                    **_match_clock_fields(self),
                },
            )
            return
        self._match_result = game_result
        self._claimed_outcome = game_result
        if game_result == Result.Defeat:
            self._our_gg_seen = True
            self._outcome_reason = "our_gg"
            print("[match-claim] Defeat reason=our_gg", flush=True)
            _write_experiment_sidecar(
                outcome="Defeat",
                result_raw=game_result,
                run_status="completed",
                run_logger=self.run_logger,
                extra={
                    "claimed": True,
                    "claim_reason": "our_gg",
                    "engine_on_end": str(game_result),
                    "gg_seen": self._gg_seen,
                    "our_gg_seen": True,
                    "note": "我方 GG/投降/离开，记失败。",
                    **_match_clock_fields(self),
                },
            )
            return
        self._outcome_reason = "engine_on_end"

    def _quiet_close_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            token in text
            for token in ("already", "closing", "not in a game", "connection already closed")
        )

    async def _force_close_sc2(self) -> None:
        try:
            await self.client.leave()
        except Exception as exc:
            if not self._quiet_close_error(exc):
                print(f"[match-claim] leave: {exc}", flush=True)
        try:
            await self.client.quit()
        except Exception as exc:
            if not self._quiet_close_error(exc):
                print(f"[match-claim] quit: {exc}", flush=True)

    async def _claim_time_limit(self) -> None:
        if self._claimed_outcome is not None:
            return
        if self._opponent_gg_confirmed():
            self._gg_seen = True
            await self._claim_match_outcome(Result.Victory, "time_limit_opponent_gg")
            return
        clock = _match_time_limit_clock()
        self._claimed_outcome = Result.Tie
        self._match_result = Result.Tie
        self._outcome_reason = "time_limit_draw"
        print(f"[match-claim] Tie t={self.time_formatted} limit={clock} no opponent gg", flush=True)
        _write_experiment_sidecar(
            outcome="Tie",
            result_raw=Result.Tie,
            run_status="completed",
            run_logger=self.run_logger,
            extra={
                "claimed": True,
                "claim_reason": "time_limit_draw",
                "gg_seen": self._gg_seen,
                "our_gg_seen": self._our_gg_seen,
                "note": f"对局达到 {clock} 仍未结束，对手未 GG，记平局。",
                **_match_clock_fields(self),
            },
        )
        pid = getattr(self.client, "_player_id", None) or self.player_id
        if not getattr(self.client, "_game_result", None):
            self.client._game_result = {pid: Result.Tie}
        await self._force_close_sc2()

    async def _claim_match_outcome(self, result: Result, reason: str, *, leave: bool = True) -> None:
        if self._claimed_outcome is not None:
            return
        self._claimed_outcome = result
        self._match_result = result
        self._outcome_reason = reason
        print(f"[match-claim] {result.name} reason={reason}", flush=True)
        _write_experiment_sidecar(
            outcome=result.name,
            result_raw=result,
            run_status="completed",
            run_logger=self.run_logger,
            extra={
                "claimed": True,
                "claim_reason": reason,
                "gg_seen": self._gg_seen,
                "our_gg_seen": self._our_gg_seen,
                **_match_clock_fields(self),
            },
        )
        if not leave:
            return
        pid = getattr(self.client, "_player_id", None) or self.player_id
        if result == Result.Victory and not getattr(self.client, "_game_result", None):
            # leave() 在 _game_result 为空时会把自己记成投降失败。
            self.client._game_result = {pid: Result.Victory}
        await self._force_close_sc2()

    def _is_live_enemy(self, unit) -> bool:
        try:
            return not bool(getattr(unit, "is_snapshot", False))
        except Exception:
            return True

    def _live_enemy_structures(self):
        return self.enemy_structures.filter(self._is_live_enemy)

    def _live_enemy_units(self):
        return self.enemy_units.filter(self._is_live_enemy)

    def _visible_enemy_townhalls(self):
        structs = self._live_enemy_structures()
        th = structs.of_type(_TOWNHALL_TYPES)
        flying = self._live_enemy_units().of_type(_FLYING_TOWNHALL_TYPES) | structs.of_type(
            _FLYING_TOWNHALL_TYPES
        )
        return th | flying

    def _enemy_army(self):
        return self._live_enemy_units().exclude_type(_NON_ARMY_TYPES)

    def _message_is_gg(self, text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        if _GG_CHAT_RE.match(raw):
            return True
        if len(raw) <= 48 and _GG_LOOSE_RE.search(raw):
            return True
        lowered = raw.lower()
        return len(raw) <= 24 and re.search(r"(?<![a-z])gg(?:wp)?(?![a-z])", lowered) is not None

    def _as_result(self, raw):
        if raw is None:
            return None
        if isinstance(raw, Result):
            return raw
        try:
            return Result(raw)
        except Exception:
            name = str(raw).rsplit(".", 1)[-1]
            try:
                return Result[name]
            except Exception:
                return None

    def _iter_chat_messages(self):
        seen = set()

        def take(player_id, text):
            pid = int(player_id or 0)
            raw = str(text or "").strip()
            if not raw:
                return
            key = (pid, raw)
            if key in seen:
                return
            seen.add(key)
            yield pid, raw

        try:
            for msg in list(self.state.chat or []):
                yield from take(getattr(msg, "player_id", 0), getattr(msg, "message", ""))
        except Exception:
            pass

        observations = []
        try:
            observations.append(self.state.response_observation)
        except Exception:
            pass
        try:
            observations.append(self.state.previous_observation)
        except Exception:
            pass
        for obs in observations:
            if obs is None:
                continue
            try:
                for msg in list(getattr(obs, "chat", None) or []):
                    yield from take(getattr(msg, "player_id", 0), getattr(msg, "message", ""))
            except Exception:
                pass
            try:
                for action in list(getattr(obs, "actions", None) or []):
                    chat = getattr(action, "action_chat", None)
                    message = str(getattr(chat, "message", "") or "").strip()
                    if message:
                        yield from take(0, message)
            except Exception:
                pass

    def _snapshot_board(self) -> None:
        visible_th = self._visible_enemy_townhalls()
        if visible_th.exists:
            self._seen_enemy_townhall = True
            if visible_th.of_type(_FLYING_TOWNHALL_TYPES).exists:
                self._last_flying_cc_time = self.time
        if self.enemy_start_locations and self.units.closer_than(18, self.enemy_start_locations[0]).exists:
            self._visited_enemy_start = True
        self._last_own_th_count = self.townhalls.amount
        self._last_enemy_th_count = visible_th.amount
        self._last_enemy_army_count = self._enemy_army().amount
        self._last_board_win = self._opponent_eliminated()

    def _combat_army(self):
        return self.units.exclude_type(_NON_ARMY_TYPES | {UnitTypeId.QUEEN})

    def _has_eyes_on_enemy_main(self) -> bool:
        if not self.enemy_start_locations:
            return False
        return self.units.closer_than(11, self.enemy_start_locations[0]).exists

    def _collect_player_results(self) -> dict:
        out = {}
        engine = getattr(self.client, "_game_result", None) or {}
        for key, raw in dict(engine).items():
            parsed = self._as_result(raw)
            try:
                pid = int(key)
            except (TypeError, ValueError):
                continue
            if parsed is not None:
                out[pid] = parsed
        sources = []
        try:
            sources.append(self.state.player_result)
        except Exception:
            pass
        try:
            sources.append(self.state.response_observation.player_result)
        except Exception:
            pass
        for src in sources:
            for pr in list(src or []):
                try:
                    pid = int(getattr(pr, "player_id", 0) or 0)
                except (TypeError, ValueError):
                    continue
                parsed = self._as_result(getattr(pr, "result", None))
                if parsed is not None:
                    out[pid] = parsed
        return out

    def _observation_player_result(self):
        results = self._collect_player_results()
        my_id = int(getattr(self.client, "_player_id", None) or self.player_id or 0)
        mine = results.get(my_id) or results.get(int(self.player_id or 0))
        if mine == Result.Victory:
            return Result.Victory
        for pid, parsed in results.items():
            if pid not in {my_id, int(self.player_id or 0)} and parsed == Result.Defeat:
                return Result.Victory
        if mine is not None:
            return mine
        if results:
            return next(iter(results.values()))
        return None

    def _opponent_eliminated(self) -> bool:
        """对方活着的单位和建筑都没了，且我方正看着对方出生点。迷雾残影不算。"""
        if not self.townhalls.exists or self.time < 240:
            return False
        if not self._seen_enemy_townhall:
            return False
        if self._visible_enemy_townhalls().exists:
            return False
        if self.time - self._last_flying_cc_time < 20:
            return False
        if self._enemy_army().exists:
            return False
        if self._live_enemy_structures().exists or self._live_enemy_units().exists:
            return False
        return self._has_eyes_on_enemy_main()

    def _opponent_effectively_gg(self) -> bool:
        """电脑认输常见盘面：主矿没了、军队没了；可剩建筑。迷雾残影不算还活着。"""
        if not self.townhalls.exists or self.time < 240:
            return False
        if not self._seen_enemy_townhall:
            return False
        if self._visible_enemy_townhalls().exists:
            return False
        if self.time - self._last_flying_cc_time < 20:
            return False
        if self._enemy_army().exists:
            return False
        leftover = self._live_enemy_structures()
        army = self._combat_army()
        if leftover.exists:
            if not army.exists:
                return False
            near = army.closer_than(15, leftover.closest_to(army.center)).amount >= 2
            return near or self._has_eyes_on_enemy_main()
        return self._has_eyes_on_enemy_main()

    def _opponent_gg_confirmed(self) -> bool:
        if _board_gg_not_win():
            return bool(self._gg_seen)
        return bool(self._gg_seen) or self._opponent_effectively_gg() or self._opponent_eliminated()

    def _should_count_quit_as_victory(self) -> bool:
        return bool(self._gg_seen)

    async def _claim_if_enemy_gg_chat(self) -> bool:
        claimed = False
        for player_id, text in self._iter_chat_messages():
            key = (player_id, text)
            if key not in self._seen_chat_keys:
                self._seen_chat_keys.add(key)
                print(f"[chat] player={player_id} {text}", flush=True)
            if not self._message_is_gg(text):
                continue
            if player_id == self.player_id:
                self._our_gg_seen = True
                if _manual_quit_mode():
                    print(f"[gg-check] our chat GG, wait for manual quit: {text[:40]}", flush=True)
                    continue
                await self._claim_match_outcome(Result.Defeat, f"our_gg_chat:{text[:40]}")
                claimed = True
                break
            self._gg_seen = True
            if _manual_quit_mode():
                print(f"[gg-check] enemy chat GG, wait for manual quit: {text[:40]}", flush=True)
                continue
            await self._claim_match_outcome(Result.Victory, f"enemy_gg_chat:{text[:40]}")
            claimed = True
            break
        return claimed

    def _board_looks_like_win(self) -> bool:
        return self._opponent_effectively_gg() or self._opponent_eliminated()

    def _finish_off_enemy(self, iteration: int) -> None:
        if iteration % 8 != 0 or self.base_being_attacked:
            return
        army = self.units.exclude_type(_NON_ARMY_TYPES | {UnitTypeId.QUEEN})
        if not army.exists:
            return
        if self.enemy_structures.exists:
            target = self.enemy_structures.closest_to(army.center)
            pos = target.position
        else:
            locs = list(self.enemy_start_locations)
            exp_list = getattr(self, "expansion_locations_list", None)
            if exp_list:
                locs.extend(list(exp_list))
            else:
                exp_map = getattr(self, "expansion_locations", None)
                if exp_map:
                    locs.extend(list(exp_map))
            if not locs:
                return
            self._sweep_i += 1
            pos = locs[self._sweep_i % len(locs)]
        for unit in army:
            if unit.is_idle or not unit.is_attacking:
                unit.attack(pos)

    async def _experiment_end_check(self, iteration: int) -> bool:
        if self._claimed_outcome is not None:
            return True

        self._snapshot_board()
        if await self._claim_if_enemy_gg_chat():
            return True

        engine_result = self._observation_player_result()
        if engine_result == Result.Victory:
            self._gg_seen = True
            if _manual_quit_mode():
                print("[gg-check] engine Victory, wait for manual quit", flush=True)
                return False
            await self._claim_match_outcome(Result.Victory, "engine_observation", leave=True)
            return True
        if engine_result == Result.Defeat:
            if _manual_quit_mode():
                print(
                    "[match] engine Defeat in manual-quit mode, wait on_end "
                    f"gg_seen={self._gg_seen} board_gg={self._opponent_gg_confirmed()}",
                    flush=True,
                )
                return True
            if self._gg_seen or self._opponent_gg_confirmed():
                await self._claim_match_outcome(Result.Victory, "engine_defeat_after_enemy_gg", leave=False)
                return True
            self._our_gg_seen = True
            await self._claim_match_outcome(Result.Defeat, "our_gg", leave=False)
            return True
        if engine_result is not None:
            await self._claim_match_outcome(engine_result, "engine_observation", leave=False)
            return True

        if self.time > 90 and not self.townhalls.exists:
            self._no_own_th_steps += 1
            if self._no_own_th_steps >= 16:
                self._our_gg_seen = True
                if _manual_quit_mode():
                    if self._no_own_th_steps == 16 or self._no_own_th_steps % 32 == 0:
                        print(
                            f"[gg-check] our townhalls gone t={self.time_formatted} wait for manual quit",
                            flush=True,
                        )
                    return False
                await self._claim_match_outcome(Result.Defeat, "our_gg_townhalls_gone")
                return True
        else:
            self._no_own_th_steps = 0

        leftover = self._live_enemy_structures().amount
        if self._opponent_effectively_gg() or self._opponent_eliminated():
            self._enemy_gg_steps += 1
            self._finish_off_enemy(iteration)
            reason = "opponent_eliminated" if self._opponent_eliminated() else "opponent_gg"
            if self._enemy_gg_steps == 1 or self._enemy_gg_steps % 8 == 0:
                print(
                    "[gg-check] "
                    f"t={self.time_formatted} reason={reason} "
                    f"vis_th={self._last_enemy_th_count} vis_army={self._last_enemy_army_count} "
                    f"eyes_main={self._has_eyes_on_enemy_main()} leftover={leftover} "
                    f"steps={self._enemy_gg_steps}",
                    flush=True,
                )
            if self._enemy_gg_steps >= 8:
                if _board_gg_not_win():
                    if self.time >= _MATCH_TIME_LIMIT_SEC:
                        await self._claim_time_limit()
                        return True
                    if self._enemy_gg_steps == 8 or self._enemy_gg_steps % 32 == 0:
                        print(
                            f"[gg-check] board looks empty t={self.time_formatted} "
                            "not a valid win until chat GG or engine Victory",
                            flush=True,
                        )
                    return True
                self._gg_seen = True
                if _manual_quit_mode():
                    if self._enemy_gg_steps == 8 or self._enemy_gg_steps % 32 == 0:
                        print(
                            f"[gg-check] CONFIRMED {reason} t={self.time_formatted} "
                            "wait for manual quit",
                            flush=True,
                        )
                    return False
                await self._claim_match_outcome(Result.Victory, reason)
                return True
        else:
            self._enemy_gg_steps = 0

        if self.time >= _MATCH_TIME_LIMIT_SEC:
            if _manual_quit_mode():
                return False
            await self._claim_time_limit()
            return True
        return False

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
        if self.supply_cap <= 60:
            return 0  # early
        elif self.supply_cap > 60:
            return 1  # mid
        elif self.supply_cap > 100:
            return 2  # late

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
        self._record_llm_message("macro", "Opening Macro Plan", output, input_summary=input_summary)

        # self.game_stage = self.detect_stage(output)

        matches = re.findall(r'\(.*?\)->\(.*?\)->\(.*?\)', output)

        temp_commands = []
        for match in matches:
            temp_commands.append(match)

        self.previous_commands = temp_commands
        banned = ['Queen', 'Gather minerals', 'Extractor', 'Mineral', 'Overlord', 'Move',
                  'Zergling', 'Creep']
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
        self._record_llm_message("building", "Building Planner", output, input_summary=input_summary)

        # self.game_stage = self.detect_stage(output)

        matches = re.findall(r'\(.*?\)->\(.*?\)->\(.*?\)', output)

        temp_commands = []
        for match in matches:
            temp_commands.append(match)

        self.previous_commands = temp_commands
        banned = ['Queen', 'Gather minerals', 'Extractor', 'Mineral', 'Overlord', 'Move',
                  'Zergling', 'Creep']
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
        self._record_llm_message("attack", "Attack Decision", output, input_summary=input_summary)
        decided_attack = "True" in (output or "")
        complete_llm_with_commands(
            self.run_logger,
            llm_call_id,
            parsed_commands=["ATTACK_ENEMY_START" if decided_attack else "RETREAT_TO_BASE"],
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
            decided_attack=decided_attack,
        )

        if "True" in output:
            print("Need to attack")
            avenager_units_types = [
                UnitTypeId.ZERGLING, UnitTypeId.ROACH,
                UnitTypeId.RAVAGER, UnitTypeId.BANELING, UnitTypeId.HYDRALISK, UnitTypeId.INFESTOR,
                UnitTypeId.SWARMHOSTMP, UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR, UnitTypeId.VIPER,
                UnitTypeId.ULTRALISK, UnitTypeId.BROODLORD
            ]
            avenager_units = self.units.filter(
                lambda unit: unit.type_id in avenager_units_types
            )
            for avenager in avenager_units:
                avenager.attack(self.enemy_start_locations[0])
            # await self.llm_attack_enemy()
        else:
            print("Do not Need to attack! Retreat to base")
            if self.start_location_label == 'A1':
                if 'A3' in self.existing_hatchery_locations:
                    retreat_base = 'A3'
                else:
                    retreat_base = self.existing_hatchery_locations[-1]
            elif self.start_location_label == 'B1':
                if 'B3' in self.existing_hatchery_locations:
                    retreat_base = 'B3'
                else:
                    retreat_base = self.existing_hatchery_locations[-1]
            retreat_point = Point2(self.mineral_location_labels_reverse[retreat_base])
            avenager_units_types = [
                UnitTypeId.ZERGLING, UnitTypeId.ROACH,
                UnitTypeId.RAVAGER, UnitTypeId.BANELING, UnitTypeId.HYDRALISK, UnitTypeId.INFESTOR,
                UnitTypeId.SWARMHOSTMP, UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR, UnitTypeId.VIPER,
                UnitTypeId.ULTRALISK, UnitTypeId.BROODLORD
            ]
            defensive_units = self.units.filter(
                lambda unit: unit.type_id in avenager_units_types
            )
            for defender in defensive_units:
                defender.move(retreat_point)

            self.is_attack_command_issued = False

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
        if self.start_location_label == "A1":
            enemy_base_list = ["B2", "B4", "B1", "B3"]
            point1 = Point2(self.mineral_location_labels_reverse["B3"])
            rally_point = point1.position.to2.towards(self.game_info.map_center, 10.0)
        elif self.start_location_label == "B1":
            enemy_base_list = ["A2", "A4", "A1", "A3"]
            point1 = Point2(self.mineral_location_labels_reverse["A3"])
            rally_point = point1.position.to2.towards(self.game_info.map_center, 10.0)

        avenger_units_types = [
            UnitTypeId.ZERGLING, UnitTypeId.OVERSEER, UnitTypeId.ROACH,
            UnitTypeId.RAVAGER, UnitTypeId.BANELING, UnitTypeId.HYDRALISK, UnitTypeId.INFESTOR,
            UnitTypeId.SWARMHOSTMP, UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR, UnitTypeId.VIPER,
            UnitTypeId.ULTRALISK, UnitTypeId.BROODLORD
        ]

        _, count_all = self.count_units_around_minerals()
        if count_all > 10:
            rally_point = self.enemy_start_locations[0]

        avenger_units = self.units.filter(lambda unit: unit.type_id in avenger_units_types)
        total_avenger_units = avenger_units.amount

        if total_avenger_units == 0:
            return

        units_at_rally_point = avenger_units.closer_than(10, rally_point).amount  # 假设的最接近距离，根据需要进行调整

        required_ratio = 0.75

        if units_at_rally_point / total_avenger_units >= required_ratio:
            for avenger in avenger_units:
                avenger.attack(Point2(self.mineral_location_labels_reverse[enemy_base_list[0]]))
        else:
            for avenger in avenger_units:
                if avenger.distance_to(rally_point) > 10:
                    avenger.move(rally_point)

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
                self._qprint("Detect enemy around", hatchery_label)
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
                        defensive_units = self.units.filter(lambda unit: unit.type_id in counterattack_units_types)
                        for defender in defensive_units:
                            defender.attack(Point2(self.mineral_location_labels_reverse[enemy_base_list[0]]))
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
        if self.waiting_for_hatchery == True and self.minerals <= 400:
            self._qprint("No morphing due to waiting_for_hatchery")
            return

        # print("morphing")
        # Roach
        if self.structures(UnitTypeId.ROACHWARREN).ready.exists:
            if self.game_stage == 0:  # Early Stage
                roach_thre = self.early_roach_num
                if self.units(UnitTypeId.ROACH).amount < self.early_roach_num:
                    if self.can_afford(UnitTypeId.ROACH) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ROACH)
                        self._qprint("early larva.train(UnitTypeId.ROACH)")
            elif self.game_stage == 1:  # Mid Stage
                roach_thre = self.mid_roach_num
                if self.units(UnitTypeId.ROACH).amount < self.mid_roach_num:
                    if self.can_afford(UnitTypeId.ROACH) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ROACH)
                        self._qprint("mid larva.train(UnitTypeId.ROACH)")
            elif self.game_stage == 2:  # Late Stage
                roach_thre = self.late_roach_num
                if self.units(UnitTypeId.ROACH).amount < self.late_roach_num:
                    if self.can_afford(UnitTypeId.ROACH) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ROACH)
                        self._qprint("late larva.train(UnitTypeId.ROACH)")

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
                    self._qprint("train hydralisk")

        # Zergling
        if self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists:
            if self.game_stage == 0:  # Early Stage
                if self.units(UnitTypeId.ZERGLING).amount < self.early_zergling_num:
                    if self.can_afford(UnitTypeId.ZERGLING) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ZERGLING)
                        self._qprint("early larva.train(UnitTypeId.ZERGLING)")
            elif self.game_stage == 1:  # Mid Stage
                if self.units(UnitTypeId.ZERGLING).amount < self.mid_zergling_num:
                    if self.can_afford(UnitTypeId.ZERGLING) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ZERGLING)
                        self._qprint("mid larva.train(UnitTypeId.ZERGLING)")
            elif self.game_stage == 2:  # Late Stage
                if self.units(UnitTypeId.ZERGLING).amount < self.late_zergling_num:
                    if self.can_afford(UnitTypeId.ZERGLING) and self.units(UnitTypeId.LARVA).exists:
                        larva = self.units(UnitTypeId.LARVA).random
                        larva.train(UnitTypeId.ZERGLING)
                        self._qprint("late larva.train(UnitTypeId.ZERGLING)")

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

    async def on_step(self, iteration: int):
        if await self._experiment_end_check(iteration):
            return

        if self.feature_map_exporter:
            try:
                self.feature_map_exporter.export(self.state.response_observation, iteration)
                self._sync_feature_frame()
            except Exception as exc:
                print(f"Feature export failed: {exc}")
                self.feature_map_exporter = None

        await self.Drone_ReflexNet()
        await self.morphing()

        await self.Queen_ReflexNet()
        await self.distribute_workers()
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
        if iteration % 8 == 0 or iteration <= 8:
            self._update_dashboard_runtime(iteration)

        if iteration % 120 == 0:
            if len(self.parsed_commands) < 8 and not self.macro_tasks:
                macro_task = asyncio.create_task(self.overmindbrain_iter())
                self.macro_tasks.append(macro_task)

        if iteration % 60 == 0:
            if len(self.parsed_commands) < 10 and not self.building_tasks:
                building_task = asyncio.create_task(self.overmind_building_iter())
                self.building_tasks.append(building_task)

        if iteration % 150 == 0:
            if self.army_count > 15 and not self.attack_tasks:
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

    async def build_buildings(self, name, i, location):
        if "Drone" in name:
            if i < len(self.parsed_commands):
                del self.parsed_commands[i]
                return

        if "Hatchery" in name:
            self._qprint("Hatchery in name")
            if self.can_afford(UnitTypeId.HATCHERY):
                pos = Point2(self.mineral_location_labels_reverse[location])
                if not self.time_exceeds(4) and self.army_count <= 15 and self.townhalls.amount == 2:
                    return
                if await self.can_place(UnitTypeId.HATCHERY, pos):
                    success = self.workers.collecting.random.build(UnitTypeId.HATCHERY, pos)
                    if success:
                        self.waiting_for_hatchery = False
                        self._qprint("self.waiting_for_hatchery set to False")
                        if i < len(self.parsed_commands):
                            del self.parsed_commands[i]
                        return
                else:
                    self._qprint("cannot place Hatchery!", location)
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Spawning Pool" in name:
            # print("Spawning Pool in name")
            if self.already_pending(UnitTypeId.SPAWNINGPOOL):
                self._qprint("SPAWNINGPOOL in pending")
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

        if self.waiting_for_hatchery == True:
            return

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
            return

        elif "Lair" in name:
            # print("Lair in name")
            if self.can_afford(UnitTypeId.LAIR):
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])

                if self.townhalls.closer_than(2.0, base_location).ready.exists:
                    hatchery = self.townhalls.closer_than(2.0, base_location).ready.first
                    success = hatchery.build(UnitTypeId.LAIR)
                    if success:
                        # print("Successfully built Lair")
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
                # print("Already have HYDRALISKDEN:", self.structures(UnitTypeId.HYDRALISKDEN).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if self.can_afford(UnitTypeId.HYDRALISKDEN) and not self.structures(
                    UnitTypeId.HYDRALISKDEN).ready.exists and self.townhalls(UnitTypeId.LAIR).ready.exists:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.HYDRALISKDEN,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.HYDRALISKDEN, placement_position)
                if success:
                    # print("Successfully built Hydralisk den")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Infestation Pit" in name:
            if self.structures(UnitTypeId.INFESTATIONPIT).amount >= 1:
                # print("Already have INFESTATIONPIT:", self.structures(UnitTypeId.INFESTATIONPIT).amount)
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

            if not self.townhalls(UnitTypeId.LAIR).ready.exists and not self.already_pending(UnitTypeId.LAIR):
                if self.can_afford(UnitTypeId.LAIR):
                    hatchery = self.townhalls(UnitTypeId.HATCHERY).ready.first
                    success = hatchery.build(UnitTypeId.LAIR)

            if self.can_afford(UnitTypeId.INFESTATIONPIT) and not self.structures(
                    UnitTypeId.INFESTATIONPIT).ready.exists and self.townhalls(UnitTypeId.LAIR).ready.exists:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                pos = base_location.position.to2.towards(self.game_info.map_center, 5.0)
                placement_position = await self.find_placement(UnitTypeId.ROACHWARREN,
                                                               near=pos)

                success = self.workers.collecting.random.build(UnitTypeId.INFESTATIONPIT, placement_position)
                if success:
                    # print("Successfully built Infestation Pit")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        elif "Spire" in name:
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
            # print("Hive in name")
            if self.can_afford(UnitTypeId.HIVE) and self.structures(
                    UnitTypeId.LAIR).ready.exists and self.structures(UnitTypeId.INFESTATIONPIT).ready.exists:
                if self.start_location_label == "A1":
                    base_location = Point2(self.mineral_location_labels_reverse["A1"])
                elif self.start_location_label == "B1":
                    base_location = Point2(self.mineral_location_labels_reverse["B1"])
                if self.townhalls.closer_than(2.0, base_location).ready.exists:
                    hatchery = self.townhalls.closer_than(2.0, base_location).ready.first
                    success = hatchery.build(UnitTypeId.HIVE)
                    if success:
                        # print("Successfully built Hive")
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
            if self.can_afford(UnitTypeId.RAVAGER) and self.units(UnitTypeId.ROACH).exists and self.supply_left > 0:
                roach = self.units(UnitTypeId.ROACH).ready.idle
                if roach.exists:
                    roach.random(AbilityId.MORPHTORAVAGER_RAVAGER)

        elif "Hydralisk" in name:
            if self.can_afford(UnitTypeId.HYDRALISK) and self.structures(
                    UnitTypeId.HYDRALISKDEN).ready.exists and self.units(UnitTypeId.LARVA).exists:
                larva = self.units(UnitTypeId.LARVA).random
                larva.train(UnitTypeId.HYDRALISK)

                # print("Successfully built Hydralisk")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
                return

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
        if 'baneling' in name.lower():
            if self.can_afford(UnitTypeId.BANELING) and self.structures(
                    UnitTypeId.BANELINGNEST).ready.exists and self.supply_left > 0:
                zergling = self.units(UnitTypeId.ZERGLING).ready.idle
                if zergling.exists:
                    zergling.random(AbilityId.MORPHTOBANELING_BANELING)

                    # print("Successfully morph Baneling")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        if location == "":
            return
        loc = Point2(self.mineral_location_labels_reverse[location])
        keywords = ['move', 'attack']
        if any(keyword.lower() in action.lower() for keyword in keywords):

            # await self.attack_enemy()

            # for zergling in self.units(UnitTypeId.ZERGLING).ready:
            #     zergling.attack(loc)

            # print("Successfully move Zergling")
            if i < len(self.parsed_commands):
                del self.parsed_commands[i]
            return

    async def roach_command(self, name, action, i, location):
        if 'ravager' in name.lower():
            if self.can_afford(UnitTypeId.RAVAGER) and self.structures(
                    UnitTypeId.ROACHWARREN).ready.exists and self.supply_left > 0:
                roach = self.units(UnitTypeId.ROACH).ready.idle
                if roach.exists:
                    roach.random(AbilityId.MORPHTORAVAGER_RAVAGER)

                    # print("Successfully morph Ravager")
                    if i < len(self.parsed_commands):
                        del self.parsed_commands[i]
                    return

        if location == "":
            return
        loc = Point2(self.mineral_location_labels_reverse[location])
        keywords = ['move', 'attack']
        if any(keyword.lower() in action.lower() for keyword in keywords):
            # await self.attack_enemy()
            for roach in self.units(UnitTypeId.ZERGLING).ready:
                roach.attack(loc)

                # print("Successfully move roach")
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
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
                            self._qprint("self.waiting_for_hatchery set to True")
            filtered_commands.append(command_list)
        self.parsed_commands = filtered_commands
        if not _env_flag("SWARMBRAIN_EXPERIMENT_QUIET"):
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

            if is_builder_actor:
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
                location = self.check_loc(payload)
                await self.overlord_scout(i, location)

            elif "Zergling" in actor:
                location = self.check_loc(payload)
                await self.zergling_command(payload, action, i, location)

            elif "Roach" in actor:
                location = self.check_loc(payload)
                await self.roach_command(payload, action, i, location)

            else:
                if i < len(self.parsed_commands):
                    del self.parsed_commands[i]
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


def _start_dashboard_redis_bridge_background() -> None:
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
                DASHBOARD_STATE_PATH.resolve(),
                poll_interval_s=interval,
                ttl_seconds=ttl_s,
                read_attempts=retries,
                once=False,
                log_prefix="[redis-bridge]",
            )
        except Exception as exc:  # pragma: no cover - 后台线程容错
            print(f"[redis-bridge] 同步线程退出: {exc}", flush=True)

    t = threading.Thread(target=_runner, name="dashboard-redis-bridge", daemon=True)
    t.start()
    print("[redis-bridge] 已启动后台同步（如需关闭请设置 SWARMBRAIN_DISABLE_REDIS_BRIDGE=1）。")


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _install_windowed_sc2() -> dict[str, Any]:
    """Launch SC2 in a framed window, not exclusive/borderless fullscreen."""
    fullscreen = _env_flag("SWARMBRAIN_FULLSCREEN", "0")
    width = _env_int("SWARMBRAIN_WINDOW_WIDTH", 1280)
    height = _env_int("SWARMBRAIN_WINDOW_HEIGHT", 720)
    pos_x = _env_int("SWARMBRAIN_WINDOW_X", 80)
    pos_y = _env_int("SWARMBRAIN_WINDOW_Y", 80)
    settings = {
        "fullscreen": fullscreen,
        "window_width": width,
        "window_height": height,
        "window_x": pos_x,
        "window_y": pos_y,
        "display_mode": 1 if fullscreen else 0,
    }
    if fullscreen:
        return settings

    class WindowedSC2Process(OrigSC2Process):
        def __init__(self, *args, **kwargs):
            kwargs["fullscreen"] = False
            kwargs["resolution"] = (width, height)
            kwargs["placement"] = (pos_x, pos_y)
            super().__init__(*args, **kwargs)

    sc2_main.SC2Process = WindowedSC2Process
    return settings


def _computer_difficulty():
    raw = (os.getenv("SWARMBRAIN_DIFFICULTY", "Medium") or "Medium").strip()
    aliases = {
        "veryeasy": "VeryEasy",
        "easy": "Easy",
        "medium": "Medium",
        "中等": "Medium",
        "mediumhard": "MediumHard",
        "困难": "MediumHard",
        "hard": "Hard",
        "艰难": "Hard",
        "harder": "Harder",
        "veryhard": "VeryHard",
        "精英": "VeryHard",
    }
    key = raw.lower().replace(" ", "").replace("_", "")
    name = aliases.get(key, raw)
    try:
        return getattr(Difficulty, name)
    except AttributeError:
        print(f"[difficulty] unknown {raw!r}, fallback Medium", flush=True)
        return Difficulty.Medium


def _match_clock_fields(bot: Any) -> dict[str, Any]:
    game_time_sec = None
    game_time = None
    try:
        if bot is not None:
            raw_time = getattr(bot, "time", None)
            if raw_time is not None:
                game_time_sec = round(float(raw_time), 1)
            game_time = getattr(bot, "time_formatted", None)
    except Exception:
        pass
    if not game_time and game_time_sec is not None:
        minutes = int(game_time_sec) // 60
        seconds = int(game_time_sec) % 60
        game_time = f"{minutes:02d}:{seconds:02d}"
    return {
        "difficulty": os.getenv("SWARMBRAIN_DIFFICULTY", "Medium"),
        "game_time": game_time,
        "game_time_sec": game_time_sec,
    }
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _classify_pve_outcome(result: Any) -> str:
    if result is None:
        return "unknown"
    if isinstance(result, Result):
        return result.name
    if isinstance(result, dict):
        for key, value in result.items():
            name = type(key).__name__.lower()
            if "bot" in name or getattr(key, "name", "") not in {"Computer", "Human"}:
                if isinstance(value, Result):
                    return value.name
                text = str(value)
                for token in ("Victory", "Defeat", "Tie", "Undecided"):
                    if token in text:
                        return token
        text = str(result)
        for token in ("Victory", "Defeat", "Tie", "Undecided"):
            if token in text:
                return token
        return "unknown"
    text = str(result)
    for token in ("Victory", "Defeat", "Tie", "Undecided"):
        if token in text:
            return token
    return "unknown"


def _sidecar_outcome_from_claim(bot, result: Any) -> str:
    reason = str(getattr(bot, "_outcome_reason", None) or "")
    if reason == "time_limit_opponent_gg":
        return "Victory"
    if reason in {"time_limit_exit", "time_limit_25min", "time_limit_draw", "exit"}:
        return "Tie"
    if reason.startswith("our_gg"):
        return "Defeat"
    if reason == "user_quit":
        return "exit"
    return _classify_pve_outcome(result)


def _write_experiment_sidecar(
    *,
    outcome: str,
    result_raw: Any,
    run_status: str,
    run_logger: SwarmBrainRunLogger | None,
    extra: dict[str, Any] | None = None,
) -> None:
    path_raw = os.getenv("SWARMBRAIN_EXPERIMENT_RESULT_FILE", "").strip()
    payload = {
        "arm": os.getenv("SWARMBRAIN_EXPERIMENT_ARM", ""),
        "game_index": os.getenv("SWARMBRAIN_EXPERIMENT_GAME_INDEX", ""),
        "prompts_modified": False,
        "outcome": outcome,
        "run_status": run_status,
        "result_raw": str(result_raw) if result_raw is not None else None,
        "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": getattr(run_logger, "run_id", None),
        "run_dir": str(getattr(run_logger, "run_dir", "") or ""),
    }
    if extra:
        payload.update(extra)
    print(
        f"[experiment-result] outcome={outcome} status={run_status} run_id={payload.get('run_id')}",
        flush=True,
    )
    if not path_raw:
        return
    path = Path(path_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
        if prev.get("claimed") and prev.get("outcome") not in {"", None, "crashed", "unknown"}:
            if outcome in {"crashed", "user_quit"} or (
                prev.get("outcome") in {"Victory", "Defeat", "Tie", "time_limit", "exit"}
                and outcome != prev.get("outcome")
            ):
                print(
                    f"[experiment-result] keep claimed {prev.get('outcome')}, ignore later {outcome}",
                    flush=True,
                )
                return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    creds = get_swarmbrain_llm_credentials()
    difficulty = _computer_difficulty()
    realtime = _env_flag("SWARMBRAIN_REALTIME", "1")
    replay_rel = os.getenv("SWARMBRAIN_REPLAY_REL", "./videos/ZvT_Medium.SC2Replay")
    print(
        f"[llm] model={creds.get('model')} base={creds.get('base_url')} realtime={realtime} difficulty={difficulty.name}",
        flush=True,
    )
    if realtime:
        print("[sc2-ui] realtime=True：窗口可切视角、可控兵（机器人下令可能覆盖）。", flush=True)
    else:
        print("[sc2-ui] realtime=False：对局在模型思考时暂停，窗口通常只能拖镜头。", flush=True)
    if _manual_quit_mode():
        print(
            "[experiment] 本局由你手动退出。程序不自动关窗。"
            "退出后根据聊天/盘面检查对手是否 GG；退出本身不记胜。",
            flush=True,
        )
    else:
        print(
            f"[experiment] 对手 GG（聊天/盘面主矿与军队没了）立即关窗记 Victory。"
            f"我方 GG/投降记 Defeat。"
            f"对局满 {_match_time_limit_clock()} 仍未结束：对手已 GG 则记胜，否则记平局。"
            "请勿手动点退出。",
            flush=True,
        )
    run_logger = SwarmBrainRunLogger(
        package_root=ROOT_DIR,
        mode="single_agent_pve",
        map_name="AutomatonLE",
        model_name=str(creds.get("model") or ""),
    )
    run_logger.write_run_event("run_started", mode="single_agent_pve")
    run_logger.register_agents(
        [
            {"agent_id": "player_1", "agent_side": "single", "display_name": "SwarmBrain", "race": "Zerg"},
        ]
    )
    _start_dashboard_redis_bridge_background()
    dashboard_writer = DashboardStateWriter(
        ROOT_DIR,
        DASHBOARD_STATE_PATH,
        game_embed_url=os.getenv("SWARMBRAIN_GAME_EMBED_URL", "/game-view/"),
    )
    dashboard_writer.update_map_layout(MINERAL_LOCATION_LABELS_REVERSE)

    opening_common = dict(
        channel="opening",
        agent_id="player_1",
        agent_side="single",
        player_id=1,
        feature_scope="label_only",
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
    pending_question = None
    for line in strategy.strip().split('\n'):
        text = line.strip()
        if not text:
            continue
        if ':' in text:
            left, right = [part.strip() for part in text.split(':', 1)]
            if "Question" in left:
                if 'False' in right:
                    results[left] = False
                    pending_question = None
                elif 'True' in right:
                    results[left] = True
                    pending_question = None
                else:
                    pending_question = left
                continue
        if pending_question is not None:
            if 'False' in text:
                results[pending_question] = False
                pending_question = None
            elif 'True' in text:
                results[pending_question] = True
                pending_question = None

    drone_attack = bool(results.get('Question 1', False))
    counterattack = bool(results.get('Question 2', False))
    if 'Question 1' not in results or 'Question 2' not in results:
        print(
            f"[opening-strategy] missing keys {sorted(results)}; "
            f"default drone_attack={drone_attack} counterattack={counterattack}",
            flush=True,
        )

    call_id = None
    token = begin_llm_context(
        run_logger,
        input_state={"phase": "opening_macro_plan"},
        opening_index=3,
        commands_expected=True,
        prompt_template_version="opening_macro_v1",
        **opening_common,
    )
    try:
        output1 = overmind_brain_1()
        call_id = end_llm_context(token, auto_complete=False)
        token = None
    except Exception as exc:
        print(f"[opening-plan] LLM unavailable: {exc}")
        call_id = end_llm_context(token, auto_complete=False)
        token = None
        if call_id:
            run_logger.fail_llm_call(call_id, error=exc, fallback_used=False)
        raise
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
        )
        log_parsed_command_batch(
            run_logger,
            llm_call_id=call_id,
            channel="opening",
            proposed=list(command_first),
            accepted=list(command_first),
            agent_id="player_1",
            agent_side="single",
            player_id=1,
        )

    dashboard_writer.update_message(
        "attack",
        strategy,
        title="Opening Strategy Answers",
        input_summary="初始化阶段：读取开局策略结果，作为本局对战的首轮任务规划输入。",
    )
    dashboard_writer.update_message(
        "macro",
        output1,
        title="Opening Macro Plan",
        input_summary="初始化阶段：脚本已完成开局侦察与前置配置，正在生成首轮宏观规划。",
    )
    dashboard_writer.update_message(
        "latest",
        output1,
        title="Opening Macro Plan",
        input_summary="初始化阶段：脚本已完成开局侦察与前置配置，正在生成首轮宏观规划。",
    )
    dashboard_writer.update_commands(command_first)

    bot = SwarmBrain(command_first, early_values, mid_values, late_values, drone_attack, counterattack)
    bot.run_logger = run_logger

    replay_path = Path(replay_rel)
    replay_abs = replay_path if replay_path.is_absolute() else (ROOT_DIR / replay_path).resolve()
    replay_abs.parent.mkdir(parents=True, exist_ok=True)
    run_status = "completed"
    result = None
    extra_meta = {
        "model": str(creds.get("model") or ""),
        "base_url": str(creds.get("base_url") or ""),
        "realtime": realtime,
        "replay": str(replay_abs),
        "difficulty": difficulty.name,
    }
    try:
        run_logger.write_run_event("game_connected", map_name="AutomatonLE")
        window_settings = _install_windowed_sc2()
        extra_meta = {**extra_meta, **window_settings}
        if window_settings["fullscreen"]:
            print("[experiment] 开局 LLM 已完成，正在弹出星际争霸窗口（全屏）。", flush=True)
        else:
            print(
                "[experiment] 开局 LLM 已完成，正在弹出星际争霸窗口"
                f"（窗口化 {window_settings['window_width']}x{window_settings['window_height']}，"
                "非全屏/非无边框全局窗口）。",
                flush=True,
            )
        skip_replay = _env_flag("SWARMBRAIN_SKIP_REPLAY")
        result = run_game(
            maps.get("AutomatonLE"),
            [
                Bot(Race.Zerg, bot, fullscreen=bool(window_settings["fullscreen"])),
                Computer(Race.Terran, difficulty),
            ],
            realtime=realtime,
            save_replay_as=None if skip_replay else str(replay_abs),
        )
        claimed = getattr(bot, "_claimed_outcome", None)
        claim_reason = getattr(bot, "_outcome_reason", None)
        extra_meta = {
            **extra_meta,
            "claimed": claimed is not None,
            "claim_reason": claim_reason,
            "gg_seen": getattr(bot, "_gg_seen", False),
            "our_gg_seen": getattr(bot, "_our_gg_seen", False),
        }
        if claimed is not None:
            result = claimed
        extra_meta = {**extra_meta, **_match_clock_fields(bot)}
        result_map = None
        try:
            if isinstance(result, Result):
                result_map = {"player_1": result.name}
            elif isinstance(result, (list, tuple)) and len(result) >= 2:
                result_map = {"player_1": str(result[0]), "player_2": str(result[1])}
            elif result is not None:
                result_map = {"raw": str(result)}
        except Exception:
            result_map = None
        run_logger.attach_replay(
            replay_abs if replay_abs.is_file() else None,
            result=result_map,
            final_game_loop=run_logger._last_known_game_loop,
            final_game_time_sec=run_logger._last_known_game_time_sec,
            save_error=None if replay_abs.is_file() else "replay path missing",
        )
        sidecar_outcome = _sidecar_outcome_from_claim(bot, result)
        _write_experiment_sidecar(
            outcome=sidecar_outcome,
            result_raw=result,
            run_status=run_status,
            run_logger=run_logger,
            extra=extra_meta,
        )
    except KeyboardInterrupt:
        run_status = "aborted"
        run_logger.attach_replay(
            replay_abs if replay_abs.is_file() else None,
            save_error="aborted_by_user",
            final_game_loop=run_logger._last_known_game_loop,
            final_game_time_sec=run_logger._last_known_game_time_sec,
        )
        _write_experiment_sidecar(
            outcome="aborted",
            result_raw=result,
            run_status=run_status,
            run_logger=run_logger,
            extra=extra_meta,
        )
        raise
    except Exception as exc:
        recovered = getattr(bot, "_claimed_outcome", None) or getattr(bot, "_match_result", None)
        ended_already = "already ended" in (str(exc) or "").lower() or type(exc).__name__ == "ConnectionAlreadyClosedError"
        if ended_already and recovered is not None:
            result = recovered
            run_status = "completed"
            result_map = {"player_1": recovered.name} if isinstance(recovered, Result) else {"raw": str(recovered)}
            run_logger.attach_replay(
                replay_abs if replay_abs.is_file() else None,
                result=result_map,
                final_game_loop=run_logger._last_known_game_loop,
                final_game_time_sec=run_logger._last_known_game_time_sec,
                save_error=None if replay_abs.is_file() else "replay save skipped after game end",
            )
            recovered_reason = getattr(bot, "_outcome_reason", None)
            _write_experiment_sidecar(
                outcome=_sidecar_outcome_from_claim(bot, recovered),
                result_raw=recovered,
                run_status=run_status,
                run_logger=run_logger,
                extra={
                    **extra_meta,
                    "claimed": getattr(bot, "_claimed_outcome", None) is not None,
                    "claim_reason": recovered_reason,
                    "recovered_from_on_end": True,
                    "error": str(exc) or type(exc).__name__,
                },
            )
        else:
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
                replay_abs if replay_abs.is_file() else None,
                save_error=str(exc) or type(exc).__name__,
                final_game_loop=run_logger._last_known_game_loop,
                final_game_time_sec=run_logger._last_known_game_time_sec,
            )
            _write_experiment_sidecar(
                outcome="crashed",
                result_raw=result,
                run_status=run_status,
                run_logger=run_logger,
                extra={**extra_meta, "error": str(exc) or type(exc).__name__},
            )
            raise
    finally:
        print(f"[run-logger] run_id={run_logger.run_id} dir={run_logger.run_dir}")
        run_logger.finalize(run_status)


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        result_file = os.getenv("SWARMBRAIN_EXPERIMENT_RESULT_FILE", "").strip()
        if result_file:
            path = Path(result_file)
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                outcome = "aborted" if isinstance(exc, KeyboardInterrupt) else "startup_failed"
                path.write_text(
                    json.dumps(
                        {
                            "arm": os.getenv("SWARMBRAIN_EXPERIMENT_ARM", ""),
                            "game_index": os.getenv("SWARMBRAIN_EXPERIMENT_GAME_INDEX", ""),
                            "prompts_modified": False,
                            "outcome": outcome,
                            "run_status": outcome,
                            "error": str(exc) or type(exc).__name__,
                            "ended_at_utc": datetime.now(timezone.utc).isoformat(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"[experiment-result] outcome={outcome} status={outcome}", flush=True)
        raise
