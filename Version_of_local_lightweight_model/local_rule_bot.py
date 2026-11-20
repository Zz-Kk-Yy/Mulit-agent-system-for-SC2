"""Zerg rules for one game. Mining, supply, inject, and emergency defense stay local.

A live model plan, when one has been accepted, replaces expand, production,
structure, and army goals. Opening numbers and map labels come from the match config.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from sc2.bot_ai import BotAI
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId

from local_model_live import (
    decision_for_current_observation,
    fetch_model_decision,
    request_feedback_gap,
    request_is_due,
    should_send_model_request,
)
from local_rule_config import MatchConfig
from local_rule_observation import build_observation, nearest_location_label
from local_rule_plan import (
    drone_goal,
    model_army_goal,
    model_expand_base,
    model_production_targets,
    model_structures,
    nearest_open_base,
    point_is_held,
    should_expand,
    should_save_for_expand,
    worker_goal,
)

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


_STRUCTURES = {
    "SPAWNINGPOOL": UnitTypeId.SPAWNINGPOOL,
    "EXTRACTOR": UnitTypeId.EXTRACTOR,
    "EVOLUTIONCHAMBER": UnitTypeId.EVOLUTIONCHAMBER,
    "BANELINGNEST": UnitTypeId.BANELINGNEST,
    "ROACHWARREN": UnitTypeId.ROACHWARREN,
    "SPINECRAWLER": UnitTypeId.SPINECRAWLER,
    "SPORECRAWLER": UnitTypeId.SPORECRAWLER,
    "HYDRALISKDEN": UnitTypeId.HYDRALISKDEN,
    "SPIRE": UnitTypeId.SPIRE,
    "INFESTATIONPIT": UnitTypeId.INFESTATIONPIT,
    "ULTRALISKCAVERN": UnitTypeId.ULTRALISKCAVERN,
    "NYDUSNETWORK": UnitTypeId.NYDUSNETWORK,
    "LURKERDEN": UnitTypeId.LURKERDENMP,
}
_MORPHS = {
    "LAIR": (UnitTypeId.HATCHERY, UnitTypeId.LAIR, AbilityId.UPGRADETOLAIR_LAIR),
    "HIVE": (UnitTypeId.LAIR, UnitTypeId.HIVE, AbilityId.UPGRADETOHIVE_HIVE),
    "GREATERSPIRE": (UnitTypeId.SPIRE, UnitTypeId.GREATERSPIRE, AbilityId.UPGRADETOGREATERSPIRE_GREATERSPIRE),
}
_ARMY_NAMES = (
    "ZERGLING",
    "BANELING",
    "ROACH",
    "RAVAGER",
    "HYDRALISK",
    "MUTALISK",
    "CORRUPTOR",
    "BROODLORD",
    "ULTRALISK",
)


class LocalRuleBot(BotAI):
    def __init__(
        self,
        config: MatchConfig,
        game_id: str,
        output_dir,
        model_actions: list[dict] | None = None,
        call_model: bool = False,
    ):
        super().__init__()
        self.config = config
        self.game_id = game_id
        self.output_dir = output_dir
        self.call_model = call_model
        self.model_request_count = 0
        self.model_executed_count = 0
        self.model_request_paths: list[str] = []
        self.last_model_game_time = None
        self._model_lock = threading.Lock()
        self._model_thread = None
        self._model_record = None
        self._model_error = None
        self._request_mark = None
        self.model_actions = list(model_actions or [])
        self._install_model_actions(self.model_actions, reset_expand=True)
        self.plan_epoch = 1
        self.spawn = None
        self.enemy_spawns: list[dict] = []
        self.expansions: list[dict] = []
        self.sightings: dict[int, dict] = {}
        self.feedback: list[dict] = []
        self.failure_hints: list[str] = []
        self.townhall_lost = False
        self.in_defense = False
        self.latest_observation = None
        self.sample_written = False
        self.sample_path = None
        self.ended = False
        self.end_result = None

    async def on_start(self):
        self.client.game_step = self.config.game_step
        start = (self.start_location.x, self.start_location.y)
        self.spawn = nearest_location_label(start, self.config.location_labels)
        self.enemy_spawns = [
            nearest_location_label((point.x, point.y), self.config.location_labels)
            for point in self.enemy_start_locations
        ]
        self.expansions = []
        for index, point in enumerate(self.expansion_locations_list):
            labeled = nearest_location_label((point.x, point.y), self.config.location_labels)
            self.expansions.append(
                {
                    "base_id": labeled["label"] or f"exp-{index}",
                    "position": [round(point.x, 1), round(point.y, 1)],
                }
            )
        self._note(
            f"出生点靠近 {self.spawn['label']}，方向 {self.spawn['side']}，距离 {self.spawn['distance']}"
        )
        print(
            f"[local-rule] spawn={self.spawn['label']} side={self.spawn['side']} "
            f"distance={self.spawn['distance']}",
            flush=True,
        )

    async def on_step(self, iteration: int):
        self._refresh_sightings()
        self._poll_model_request()
        model_due = self.call_model and should_send_model_request(
            request_is_due(float(self.time), self.last_model_game_time),
            self._model_request_in_flight(),
        )
        if iteration % 20 == 0 or model_due:
            self._capture_observation()
        if iteration % 20 == 0:
            print(
                f"[local-rule] t={self.time_formatted} minerals={self.minerals} "
                f"drones={self.workers.amount} supply={self.supply_used}/{self.supply_cap}",
                flush=True,
            )
        if model_due:
            self._start_model_request()
        if not self.townhalls:
            self._mark_townhall_lost()
            await self._expand()
        else:
            await self.distribute_workers()
            self._build_overlord()
            self._emergency_defense()
            await self._build_pool()
            self._train_queen()
            await self._build_model_structures()
            await self._expand()
            self._train_model_units()
            self._train_drone()
            self._inject()
            self._hold_model_army()

    async def on_end(self, game_result):
        self.ended = True
        self.end_result = game_result
        if self._request_mark is not None and self._model_request_in_flight():
            self._record_feedback_gap(None, missing="对局结束时回复还没到")
        try:
            self._capture_observation(force_sample=True)
        except Exception as exc:
            self.failure_hints.append(f"终局局面采样失败: {exc}")

    def _note(self, text: str) -> None:
        now = round(float(self.time), 1) if getattr(self, "state", None) is not None else 0.0
        if self.feedback and self.feedback[-1]["text"] == text and now - self.feedback[-1]["game_time_s"] < 10:
            return
        self.feedback.append({"game_time_s": now, "text": text})
        if len(self.feedback) > 12:
            del self.feedback[0]

    def _mark_townhall_lost(self) -> None:
        if self.townhall_lost:
            return
        self.townhall_lost = True
        text = f"{self.time_formatted} 主矿已经不在"
        self.failure_hints.append(text)
        self._note(text)

    def _refresh_sightings(self) -> None:
        for tag, sighting in self.sightings.items():
            sighting["currently_visible"] = False
        for unit in list(self.enemy_units) + list(self.enemy_structures):
            if not unit.is_visible:
                continue
            self.sightings[unit.tag] = {
                "type": unit.type_id.name,
                "position": [round(unit.position.x, 1), round(unit.position.y, 1)],
                "last_seen_game_time_s": round(float(self.time), 1),
                "currently_visible": True,
                "is_structure": bool(unit.is_structure),
            }
        for blip in self.blips:
            if not getattr(blip, "is_enemy", False):
                continue
            tag = int(getattr(blip._proto, "tag", 0) or 0)
            if not tag:
                continue
            position = blip.position
            self.sightings[tag] = {
                "type": "Unidentified",
                "position": [round(position.x, 1), round(position.y, 1)],
                "last_seen_game_time_s": round(float(self.time), 1),
                "currently_visible": True,
                "is_structure": False,
            }

    def _own_counts(self, units) -> list[dict]:
        counts: dict[str, int] = {}
        for unit in units:
            counts[unit.type_id.name] = counts.get(unit.type_id.name, 0) + 1
        return [{"type": name, "count": count} for name, count in sorted(counts.items()) if count > 0]

    def _structure_rows(self) -> list[dict]:
        grouped: dict[str, list] = {}
        for structure in self.structures:
            grouped.setdefault(structure.type_id.name, []).append(
                [round(structure.position.x, 1), round(structure.position.y, 1)]
            )
        return [
            {"type": name, "count": len(positions), "positions": positions}
            for name, positions in sorted(grouped.items())
            if positions
        ]

    def _base_rows(self) -> tuple[list[dict], list[dict]]:
        known = []
        candidates = []
        for expansion in self.expansions:
            position = expansion["position"]
            own_here = any(self._near(position, townhall.position, 12) for townhall in self.townhalls)
            enemy_seen = self._enemy_townhall_sighting(position)
            if own_here:
                known.append({**expansion, "controlled_by": "self"})
            elif enemy_seen is not None:
                known.append(
                    {
                        **expansion,
                        "controlled_by": "enemy",
                        "last_seen_game_time_s": enemy_seen,
                    }
                )
            else:
                candidates.append({**expansion, "controlled_by": "unknown"})
        return known, candidates

    def _enemy_townhall_sighting(self, position: list[float]) -> float | None:
        last_seen = None
        for sighting in self.sightings.values():
            if not sighting.get("is_structure"):
                continue
            if sighting["type"] not in {item.name for item in _TOWNHALL_TYPES}:
                continue
            if self._near(position, sighting["position"], 12):
                seen_at = float(sighting["last_seen_game_time_s"])
                if last_seen is None or seen_at > last_seen:
                    last_seen = seen_at
        return last_seen

    @staticmethod
    def _near(left, right, radius: float) -> bool:
        return ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5 <= radius

    def _capture_observation(self, force_sample: bool = False) -> None:
        known_bases, candidates = self._base_rows()
        sightings = sorted(self.sightings.values(), key=lambda item: item["last_seen_game_time_s"])
        observation = build_observation(
            game_id=self.game_id,
            map_name=self.config.map_name,
            observation_id=f"{self.game_id}-t{int(self.time)}",
            game_time_s=self.time,
            game_loop=self.state.game_loop,
            plan_epoch=self.plan_epoch,
            resources={
                "minerals": self.minerals,
                "vespene": self.vespene,
                "supply_used": self.supply_used,
                "supply_cap": self.supply_cap,
                "supply_left": self.supply_left,
                "supply_workers": self.supply_workers,
                "larva": self.larva.amount,
            },
            own_units=self._own_counts(self.units),
            own_structures=self._structure_rows(),
            own_start=self.spawn or {},
            known_bases=known_bases,
            candidate_expansions=candidates,
            sightings=sightings,
            plan_summary=self.config.opening.summary,
            recent_feedback=list(self.feedback),
            plan_source="model" if self.model_actions else "local_rules",
            plan_actions=list(self.model_actions),
            own_in_training=self._in_training_rows(),
        )
        self.latest_observation = observation
        due = float(self.time) >= self.config.observation_dump_game_time_sec
        if self.output_dir and (force_sample or due) and not self.sample_written:
            path = self.output_dir / "observation_sample.json"
            path.write_text(_dump(observation), encoding="utf-8")
            self.sample_path = path
            self.sample_written = True

    def _ling_counts(self) -> tuple[int, int]:
        alive = self.units(UnitTypeId.ZERGLING).amount + self.units(UnitTypeId.ZERGLINGBURROWED).amount
        morphing = 0
        for egg in self.units(UnitTypeId.EGG):
            for order in egg.orders:
                if order.ability.exact_id == AbilityId.LARVATRAIN_ZERGLING:
                    morphing += 2
                    break
        return alive, morphing

    def _train_from_larva(self, unit_type: UnitTypeId) -> bool:
        if not self.larva or not self.can_afford(unit_type):
            return False
        self.larva.random.train(unit_type)
        return True

    def _build_overlord(self) -> None:
        opening = self.config.opening
        if self.supply_cap >= 200 or self.supply_left > opening.overlord_when_supply_left_at_most:
            return
        if self.already_pending(UnitTypeId.OVERLORD) > 0:
            return
        if self._train_from_larva(UnitTypeId.OVERLORD):
            self._note("人口不够，补王虫")

    def _visible_threats(self):
        radius = self.config.opening.defense_radius
        threats = []
        for enemy in self.enemy_units:
            if not enemy.is_visible:
                continue
            if any(enemy.distance_to(townhall) <= radius for townhall in self.townhalls):
                threats.append(enemy)
        return threats

    def _emergency_defense(self) -> None:
        threats = self._visible_threats()
        if not threats:
            if self.in_defense:
                self.in_defense = False
                self._note("家里的可见敌军离开了防御范围")
            return
        alive, morphing = self._ling_counts()
        if not self.in_defense:
            self.in_defense = True
            text = (
                f"{self.time_formatted} 敌军进入防御半径，可见 {len(threats)}，"
                f"刺蛇 {alive}，孵化中 {morphing}，女王 {self.units(UnitTypeId.QUEEN).amount}"
            )
            self.failure_hints.append(text)
            self._note(text)
        target = min(threats, key=lambda unit: unit.distance_to(self.start_location))
        for ling in self.units(UnitTypeId.ZERGLING):
            ling.attack(target.position)
        opening = self.config.opening
        pool_ready = self.structures(UnitTypeId.SPAWNINGPOOL).ready
        if pool_ready and alive + morphing < opening.emergency_ling_cap:
            if self._train_from_larva(UnitTypeId.ZERGLING):
                self._note("紧急防守，补刺蛇")
        if alive < opening.pull_workers_when_army_below and len(threats) <= opening.pull_workers_when_visible_enemy_at_most:
            for worker in self.workers.closest_n_units(target.position, opening.pull_worker_count):
                worker.attack(target.position)

    async def _build_pool(self) -> None:
        opening = self.config.opening
        if self.structures(UnitTypeId.SPAWNINGPOOL).amount > 0 or self.already_pending(UnitTypeId.SPAWNINGPOOL) > 0:
            return
        if self.workers.amount < opening.spawning_pool_min_workers:
            return
        if not self.can_afford(UnitTypeId.SPAWNINGPOOL) or not self.townhalls:
            return
        placed = await self.build(
            UnitTypeId.SPAWNINGPOOL,
            near=self.townhalls.first.position.towards(self.game_info.map_center, 6),
        )
        if placed:
            self._note("农民达到开局数量，造血池")

    def _train_queen(self) -> None:
        if not self.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        opening = self.config.opening
        desired = self.townhalls.ready.amount * opening.queen_per_base
        current = self.units(UnitTypeId.QUEEN).amount + self.already_pending(UnitTypeId.QUEEN)
        if current >= desired or self.supply_left < 2:
            return
        trained = self.train(UnitTypeId.QUEEN, 1)
        if trained:
            self._note("血池完成，造女王")

    async def _expand(self) -> None:
        if self.model_expand_id:
            await self._expand_to_model_base()
            return
        opening = self.config.opening
        if not self._ready_to_expand():
            return
        if not self.can_afford(UnitTypeId.HATCHERY):
            return
        target = nearest_open_base(
            (self.start_location.x, self.start_location.y),
            [(row["position"][0], row["position"][1]) for row in self.expansions],
            [(townhall.position.x, townhall.position.y) for townhall in self.townhalls],
            opening.expand_block_radius,
        )
        if target is None:
            return
        placed = await self.build(UnitTypeId.HATCHERY, near=Point2(target))
        if placed:
            self._note("占最近的空矿")

    async def _expand_to_model_base(self) -> None:
        point = self._label_position(self.model_expand_id)
        if point is None:
            self._note(f"模型要扩张的 {self.model_expand_id} 不在配置或局面里")
            return
        held = point_is_held(
            point,
            [(townhall.position.x, townhall.position.y) for townhall in self.townhalls],
            self.config.opening.expand_block_radius,
        )
        if held:
            if not self.model_expand_done:
                self.model_expand_done = True
                self._note(f"模型要求的 {self.model_expand_id} 已经有主基地")
            return
        if self.in_defense or not self.can_afford(UnitTypeId.HATCHERY):
            return
        placed = await self.build(UnitTypeId.HATCHERY, near=Point2(point))
        if placed:
            self.model_expand_done = True
            self._note(f"按模型指令在 {self.model_expand_id} 扩张")

    def _label_position(self, label: str) -> tuple[float, float] | None:
        for row in self.expansions:
            if row.get("base_id") == label:
                position = row.get("position")
                if isinstance(position, list) and len(position) == 2:
                    return float(position[0]), float(position[1])
        return self.config.location_labels.get(label)

    def _hold_model_army(self) -> None:
        if not self.model_army_mode or not self.model_region_id or self.in_defense:
            return
        point = self._label_position(self.model_region_id)
        if point is None:
            return
        destination = Point2(point)
        for unit in self._army_units():
            if unit.is_idle and unit.distance_to(destination) > 8:
                unit.attack(destination)

    def _ready_to_expand(self) -> bool:
        opening = self.config.opening
        pool_placed = (
            self.structures(UnitTypeId.SPAWNINGPOOL).amount > 0
            or self.already_pending(UnitTypeId.SPAWNINGPOOL) > 0
        )
        return should_expand(
            base_target=opening.base_target,
            committed_bases=self._committed_bases(),
            workers=self.workers.amount,
            worker_target=opening.worker_target,
            pool_placed=pool_placed,
            under_attack=self.in_defense,
        )

    def _committed_bases(self) -> int:
        building = self.townhalls.not_ready.amount
        en_route = max(0, int(self.already_pending(UnitTypeId.HATCHERY)) - building)
        return self.townhalls.amount + en_route

    def _drone_cap(self) -> int:
        opening = self.config.opening
        local = worker_goal(opening.worker_target, opening.base_target, self._committed_bases())
        return drone_goal(local, self.model_production.get("DRONE"))

    def _train_drone(self) -> None:
        if self._saving_for_pool() or self._saving_for_queen() or self._saving_for_expand() or self._saving_for_structure():
            return
        current = self.workers.amount + self.already_pending(UnitTypeId.DRONE)
        if current >= self._drone_cap():
            return
        if self._train_from_larva(UnitTypeId.DRONE):
            self._note("补农民采矿")

    def _saving_for_pool(self) -> bool:
        opening = self.config.opening
        if self.workers.amount < opening.spawning_pool_min_workers:
            return False
        return self.structures(UnitTypeId.SPAWNINGPOOL).amount == 0 and self.already_pending(UnitTypeId.SPAWNINGPOOL) == 0

    def _saving_for_expand(self) -> bool:
        if self.model_expand_id:
            point = self._label_position(self.model_expand_id)
            if point is None or self.model_expand_done:
                return False
            held = point_is_held(
                point,
                [(townhall.position.x, townhall.position.y) for townhall in self.townhalls],
                self.config.opening.expand_block_radius,
            )
            return should_save_for_expand(not held and not self.in_defense, self.minerals)
        return should_save_for_expand(self._ready_to_expand(), self.minerals)

    def _saving_for_queen(self) -> bool:
        if not self.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return False
        desired = self.townhalls.ready.amount * self.config.opening.queen_per_base
        current = self.units(UnitTypeId.QUEEN).amount + self.already_pending(UnitTypeId.QUEEN)
        return current < desired

    def _inject(self) -> None:
        queens = [queen for queen in self.units(UnitTypeId.QUEEN).idle if queen.energy >= 25]
        if not queens:
            return
        assigned = set()
        for queen in queens:
            targets = self.townhalls.ready.filter(
                lambda hatch, assigned=assigned: hatch.tag not in assigned and not hatch.has_buff(BuffId.QUEENSPAWNLARVATIMER)
            )
            if not targets:
                return
            target = targets.closest_to(queen)
            queen(AbilityId.EFFECT_INJECTLARVA, target)
            assigned.add(target.tag)
            self._note("女王注卵")

    def _install_model_actions(self, actions: list[dict], reset_expand: bool) -> None:
        self.model_actions = list(actions)
        self.model_expand_id = model_expand_base(actions)
        self.model_army_mode, self.model_region_id = model_army_goal(actions)
        self.model_production = model_production_targets(actions)
        self.model_structures = model_structures(actions)
        if reset_expand:
            self.model_expand_done = False

    def _model_request_in_flight(self) -> bool:
        with self._model_lock:
            thread = self._model_thread
        return thread is not None and thread.is_alive()

    def _start_model_request(self) -> None:
        if self._model_request_in_flight() or self.latest_observation is None:
            return
        observation = json.loads(json.dumps(self.latest_observation))
        self.last_model_game_time = float(observation["game"]["game_time_s"])
        self._request_mark = {
            "observation_id": observation["game"]["observation_id"],
            "request_game_time_s": float(observation["game"]["game_time_s"]),
            "request_wall": datetime.now().astimezone(),
        }
        self._append_timing(
            {
                "event": "request",
                "observation_id": self._request_mark["observation_id"],
                "request_game_time_s": self._request_mark["request_game_time_s"],
                "request_wall_time": self._request_mark["request_wall"].isoformat(timespec="milliseconds"),
            }
        )
        print(
            f"[local-rule] t={self.time_formatted} 向模型发送局面 {observation['game']['observation_id']}，对局继续",
            flush=True,
        )
        output_dir = Path(self.output_dir) / "model_requests"
        thread = threading.Thread(
            target=self._fetch_model_in_background,
            args=(observation, output_dir),
            daemon=True,
            name="live-model-request",
        )
        with self._model_lock:
            self._model_record = None
            self._model_error = None
            self._model_thread = thread
        thread.start()

    def _fetch_model_in_background(self, observation: dict, output_dir: Path) -> None:
        try:
            record = fetch_model_decision(observation, output_dir)
            error = None
        except Exception as exc:
            record = None
            error = exc
        with self._model_lock:
            self._model_record = record
            self._model_error = error

    def _poll_model_request(self) -> None:
        with self._model_lock:
            thread = self._model_thread
            if thread is None or thread.is_alive():
                return
            record = self._model_record
            error = self._model_error
            self._model_thread = None
            self._model_record = None
            self._model_error = None
        self.model_request_count += 1
        if error is not None or record is None:
            self._note(f"模型请求没有完成，保持当前计划。{error}")
            self._record_feedback_gap(None, missing=str(error))
            print(f"[local-rule] decision=拒绝 reasons=['{error}'] executed=False", flush=True)
            return
        try:
            self._capture_observation()
        except Exception as exc:
            record["executed"] = False
            record["execution_note"] = f"回复到达时局面已经取不到，没有执行。{exc}"
            self._write_model_record(record)
            self._record_feedback_gap(record, missing=str(exc))
            print(f"[local-rule] decision=拒绝 reasons=['{exc}'] executed=False", flush=True)
            return
        self._receive_model_record(record)
        gap = self._record_feedback_gap(record)
        print(
            f"[local-rule] decision={record.get('apply_decision')} "
            f"reasons={record.get('apply_reject_reasons')} "
            f"latency_s={None if gap is None else gap['latency_s']} "
            f"game_time_delta_s={None if gap is None else gap['game_time_delta_s']} "
            f"executed={record.get('executed')}",
            flush=True,
        )

    def _receive_model_record(self, record: dict) -> None:
        self.model_request_paths.append(str(record["record_path"]))
        if self.ended or self.latest_observation is None:
            record["executed"] = False
            record["apply_decision"] = "拒绝"
            record["apply_reject_reasons"] = ["对局已经结束"]
            record["execution_note"] = "对局已经结束，没有执行。"
            self._write_model_record(record)
            return
        current = decision_for_current_observation(record, self.latest_observation)
        record["apply_observation_id"] = self.latest_observation["game"]["observation_id"]
        record["apply_decision"] = current.get("decision")
        record["apply_reject_reasons"] = list(current.get("reject_reasons") or [])
        actions = current.get("actions_from_content") or []
        if current.get("decision") == "可执行" and actions:
            self._install_model_actions(actions, reset_expand=True)
            self.plan_epoch += 1
            self.model_executed_count += 1
            skipped = "；".join(record["apply_reject_reasons"])
            record["executed"] = True
            record["execution_note"] = (
                "回复到达时按当前局面复核通过，能执行的动作已下到这盘对局。"
                + (f"跳过：{skipped}。" if skipped else "")
                + "采矿、人口、注卵、紧急防守和每个基地的农民目标仍由本地规则执行。"
            )
            self._note("模型计划已执行：" + _action_text(actions) + (f"。跳过：{skipped}" if skipped else ""))
        elif current.get("decision") == "可执行":
            record["executed"] = False
            record["execution_note"] = "空 actions 保持当前计划，不算有效决策。"
            self._note("模型返回空计划，保持当前计划")
        else:
            record["executed"] = False
            reasons = "；".join(record["apply_reject_reasons"])
            if record.get("decision") == "可执行":
                record["execution_note"] = "回复到达时当前局面已不再满足，保持当前计划。"
                self._note(f"模型回复到达时已不能执行，保持当前计划。{reasons}")
            else:
                record["execution_note"] = "拒绝执行，当前计划保持不变。"
                self._note(f"模型回复拒绝，保持当前计划。{reasons}")
        self._write_model_record(record)

    def _record_feedback_gap(self, record: dict | None, missing: str | None = None) -> dict | None:
        mark = self._request_mark
        if mark is None:
            return None
        feedback_wall = datetime.now().astimezone()
        feedback_game_time_s = round(float(self.time), 3) if getattr(self, "state", None) is not None else None
        gap = None
        if feedback_game_time_s is not None:
            gap = request_feedback_gap(
                mark["request_wall"],
                feedback_wall,
                mark["request_game_time_s"],
                feedback_game_time_s,
            )
        row = {
            "event": "feedback_missing" if missing else "feedback",
            "observation_id": mark["observation_id"],
            "request_game_time_s": mark["request_game_time_s"],
            "feedback_game_time_s": feedback_game_time_s,
            "request_wall_time": mark["request_wall"].isoformat(timespec="milliseconds"),
            "feedback_wall_time": feedback_wall.isoformat(timespec="milliseconds"),
            "latency_s": None if gap is None else gap["latency_s"],
            "game_time_delta_s": None if gap is None else gap["game_time_delta_s"],
            "http_elapsed_s": None if record is None else (record.get("request_meta") or {}).get("elapsed_s"),
            "decision": None if record is None else record.get("apply_decision") or record.get("decision"),
            "executed": None if record is None else record.get("executed"),
            "missing": missing,
        }
        self._append_timing(row)
        self._request_mark = None
        return gap

    def _append_timing(self, row: dict) -> None:
        path = Path(self.output_dir) / "model_timing.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_model_record(self, record: dict) -> None:
        path = Path(record["record_path"])
        public = dict(record)
        public.pop("record_path", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")

    def _in_training_rows(self) -> list[dict]:
        _, morphing_lings = self._ling_counts()
        pending = {
            "DRONE": self.already_pending(UnitTypeId.DRONE),
            "OVERLORD": self.already_pending(UnitTypeId.OVERLORD),
            "QUEEN": self.already_pending(UnitTypeId.QUEEN),
            "ZERGLING": morphing_lings,
            "ROACH": self.already_pending(UnitTypeId.ROACH),
            "HYDRALISK": self.already_pending(UnitTypeId.HYDRALISK),
            "MUTALISK": self.already_pending(UnitTypeId.MUTALISK),
            "BANELING": self.already_pending(UnitTypeId.BANELING),
        }
        return [{"type": name, "count": int(count)} for name, count in pending.items() if count > 0]

    def _train_model_units(self) -> None:
        for unit_name, target in self.model_production.items():
            if unit_name == "DRONE":
                continue
            self._train_toward(unit_name, target)

    def _train_toward(self, unit_name: str, target: int) -> None:
        current = self._unit_total(unit_name)
        if current >= target:
            return
        if unit_name == "QUEEN":
            if self.structures(UnitTypeId.SPAWNINGPOOL).ready and self.supply_left >= 2 and self.can_afford(UnitTypeId.QUEEN):
                if self.train(UnitTypeId.QUEEN, 1):
                    self._note(f"按模型指令补女王，目标 {target}")
            return
        if unit_name == "BANELING":
            if not self.structures(UnitTypeId.BANELINGNEST).ready or not self.can_afford(UnitTypeId.BANELING):
                return
            for ling in self.units(UnitTypeId.ZERGLING).idle:
                ling(AbilityId.MORPHZERGLINGTOBANELING_BANELING)
                self._note(f"按模型指令补爆虫，目标 {target}")
                return
            return
        unit_type = getattr(UnitTypeId, unit_name, None)
        if unit_type is None or not self.can_afford(unit_type):
            return
        supply_needed = {"DRONE": 1, "ZERGLING": 1, "ROACH": 2, "HYDRALISK": 2, "MUTALISK": 2}.get(unit_name, 1)
        if unit_name != "OVERLORD" and self.supply_left < supply_needed:
            return
        if self._train_from_larva(unit_type):
            self._note(f"按模型指令补 {unit_name}，目标 {target}")

    def _unit_total(self, unit_name: str) -> int:
        if unit_name == "ZERGLING":
            alive, morphing = self._ling_counts()
            return alive + morphing
        if unit_name == "DRONE":
            return self.workers.amount + self.already_pending(UnitTypeId.DRONE)
        unit_type = getattr(UnitTypeId, unit_name, None)
        if unit_type is None:
            return 0
        return self.units(unit_type).amount + self.already_pending(unit_type)

    async def _build_model_structures(self) -> None:
        for action in self.model_structures:
            if await self._place_model_structure(action):
                self._note(f"按模型指令建造 {action['structure']}")

    async def _place_model_structure(self, action: dict) -> bool:
        name = action["structure"]
        if name in _MORPHS:
            return self._morph_model_structure(name)
        if name == "EXTRACTOR":
            return self._build_extractor()
        unit_type = _STRUCTURES.get(name)
        if unit_type is None or not self.townhalls:
            return False
        if self.structures(unit_type).amount > 0 or self.already_pending(unit_type) > 0:
            return False
        if not self.can_afford(unit_type):
            return False
        position = action.get("position")
        if isinstance(position, list) and len(position) == 2:
            near = Point2((float(position[0]), float(position[1])))
        else:
            near = self.townhalls.first.position.towards(self.game_info.map_center, 6)
        return bool(await self.build(unit_type, near=near))

    def _morph_model_structure(self, name: str) -> bool:
        source, target, ability = _MORPHS[name]
        if self.structures(target).amount > 0 or self.already_pending(target) > 0:
            return False
        ready = self.structures(source).ready.idle
        if not ready or not self.can_afford(target):
            return False
        ready.first(ability)
        return True

    def _build_extractor(self) -> bool:
        if not self.townhalls or not self.can_afford(UnitTypeId.EXTRACTOR):
            return False
        if self.already_pending(UnitTypeId.EXTRACTOR) > 0:
            return False
        for townhall in self.townhalls.ready:
            geysers = self.vespene_geyser.closer_than(12, townhall)
            for geyser in geysers:
                if self.structures.closer_than(1, geyser):
                    continue
                worker = self.select_build_worker(geyser.position)
                if worker is None:
                    return False
                worker.build_gas(geyser)
                return True
        return False

    def _saving_for_structure(self) -> bool:
        for action in self.model_structures:
            name = action["structure"]
            unit_type = _STRUCTURES.get(name) or (_MORPHS[name][1] if name in _MORPHS else None)
            if unit_type is None:
                continue
            if self.structures(unit_type).amount > 0 or self.already_pending(unit_type) > 0:
                continue
            try:
                cost = self.calculate_cost(unit_type)
            except Exception:
                continue
            if self.minerals < cost.minerals or self.vespene < cost.vespene:
                return True
        return False

    def _army_units(self):
        collected = []
        for name in _ARMY_NAMES:
            unit_type = getattr(UnitTypeId, name, None)
            if unit_type is None:
                continue
            collected.extend(self.units(unit_type))
        return collected


def _action_text(actions: list[dict]) -> str:
    parts = []
    for action in actions:
        kind = action.get("type")
        if kind == "expand":
            parts.append(f"expand {action.get('base_id')}")
        elif kind == "set_army_goal":
            parts.append(f"{action.get('mode')} {action.get('region_id')}")
        elif kind == "set_production_target":
            parts.append(f"{action.get('unit')}->{action.get('target_count')}")
        elif kind == "build_structure":
            parts.append(str(action.get("structure")))
    return "，".join(parts)


def _dump(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
