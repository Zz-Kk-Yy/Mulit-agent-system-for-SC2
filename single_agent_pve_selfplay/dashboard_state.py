from __future__ import annotations

import json
import re
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Iterable


COMMAND_PATTERN = re.compile(r"\(([^)]+)\)->\(([^)]+)\)->\(([^)]+)\)")
LOCATION_PATTERN = re.compile(r"\b([AB]\d{1,2})\b")

_UNIT_CN: dict[str, str] = {
    "drone": "工蜂",
    "overlord": "王虫",
    "zergling": "跳虫",
    "baneling": "毒爆虫",
    "roach": "蟑螂",
    "ravager": "破坏者",
    "hydralisk": "刺蛇",
    "infestor": "感染虫",
    "mutalisk": "飞龙",
    "corruptor": "腐化者",
    "viper": "响尾蛇",
    "ultralisk": "雷兽",
    "queen": "女王",
    "larva": "幼虫",
    "hatchery": "孵化场",
    "lair": "虫穴",
    "hive": "蜂巢",
    "extractor": "提炼厂",
    "spawningpool": "孵化池",
    "evolutionchamber": "进化腔",
    "roachwarren": "蟑螂窝",
    "banelingnest": "毒爆虫巢",
    "hydraliskden": "刺蛇窝",
    "infestationpit": "感染坑",
    "spire": "尖塔",
    "greaterspire": "高等尖塔",
    "ultraliskcavern": "雷兽洞穴",
    "spinecrawler": "脊椎爬行虫",
    "sporecrawler": "孢子爬行虫",
    "nydusnetwork": "奈达斯网络",
    "nydusworm": "奈达斯蠕虫",
    "broodlord": "巢虫领主",
    "swarmhostmp": "虫群宿主",
    "overseer": "监视者",
    "marine": "陆战队员",
    "marauder": "掠夺者",
    "reaper": "死神",
    "ghost": "幽灵",
    "hellion": "地狱火",
    "hellbat": "地狱蝙蝠",
    "widowmine": "寡妇雷",
    "cyclone": "旋风",
    "siegetank": "攻城坦克",
    "thor": "雷神",
    "viking": "维京战机",
    "medivac": "医疗机",
    "liberator": "解放者",
    "raven": "渡鸦",
    "banshee": "女妖",
    "battlecruiser": "战列巡洋舰",
    "scv": "工程机甲",
    "zealot": "狂热者",
    "stalker": "追猎者",
    "sentry": "哨兵",
    "adept": "使徒",
    "hightemplar": "高阶圣堂武士",
    "darktemplar": "黑暗圣堂武士",
    "archon": "执政官",
    "colossus": "巨像",
    "carrier": "航母",
    "phoenix": "凤凰",
    "voidray": "虚空辉光舰",
    "oracle": "先知",
    "tempest": "风暴战舰",
    "disruptor": "干扰者",
    "observer": "观察者",
    "probe": "探机",
    "nexus": "星灵枢纽",
    "pylon": "水晶塔",
    "commandcenter": "指挥中心",
    "supplydepot": "补给站",
    "bunker": "地堡",
    "missileturret": "导弹塔",
    "barracks": "兵营",
    "factory": "重工厂",
    "starport": "星港",
    "gateway": "传送门",
    "cyberneticscore": "控制芯核",
    "roboticsfacility": "机械台",
    "stargate": "星门",
    # 常见 API / 内部类型名（无空格，与 _cn_unit 规范化一致）
    "egg": "虫卵",
    "banelingcocoon": "毒爆虫茧",
    "ravagercocoon": "破坏者茧",
    "broodlordcocoon": "巢虫领主茧",
    "transportoverlordcocoon": "运输王虫茧",
    "lurkerden": "潜伏者巢穴",
    "lurker": "潜伏者",
    "droneburrowed": "工蜂（潜地）",
    "roachburrowed": "蟑螂（潜地）",
    "hydraliskburrowed": "刺蛇（潜地）",
    "supplydepotlowered": "补给站（收起）",
    "planetaryfortress": "行星要塞",
    "orbitalcommand": "轨道指挥部",
    "fusioncore": "聚变芯体",
    "ghostacademy": "幽灵军校",
    "engineeringbay": "工程站",
    "armory": "军械库",
    "sensortower": "感应塔",
    "refinery": "精炼厂",
    "factorytechlab": "重工厂科技实验室",
    "factoryreactor": "重工厂反应堆",
    "barrackstechlab": "兵营科技实验室",
    "barracksreactor": "兵营反应堆",
    "starporttechlab": "星港科技实验室",
    "starportreactor": "星港反应堆",
    "techlab": "科技实验室",
    "reactor": "反应堆",
    "helliontank": "地狱火坦克",
    "widowmineburrowed": "寡妇雷（埋地）",
    "liberatorag": "解放者（对地模式）",
    "tank": "坦克",
    "autoarchon": "执政官",
    "immortal": "不朽者",
    "immortalburrowed": "不朽者",
    "warpprism": "折跃棱镜",
    "warpprismphasing": "折跃棱镜（相位模式）",
    "shieldbattery": "护盾充能器",
    "photoncannon": "光子炮台",
    "assimilator": "吸纳仓",
    "gatewaywarpin": "折跃门",
    "warpgate": "折跃门",
    "templararchive": "圣堂文献库",
    "fleetbeacon": "舰队航标",
    "roboticsbay": "机械研究所",
    "twilightcouncil": "光影议会",
    "darkshrine": "黑暗圣坛",
    "cannon": "光子炮台",
    "nyduscanal": "虫道网络",
    "lurkerdenmp": "潜伏者巢穴",
    "spinecrawleruprooted": "脊针爬虫（拔起）",
    "sporecrawleruprooted": "孢子爬虫（拔起）",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DashboardStateWriter:
    def __init__(self, root_dir: Path | str, state_path: Path | str, game_embed_url: str = "") -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_path = Path(state_path).resolve()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._map_layout: dict[str, dict[str, float]] = {}
        self._state = self._build_default_state(game_embed_url.strip())
        existing = self._read_existing_state()
        if existing:
            self._state = self._merge_dicts(self._state, existing)
            if game_embed_url.strip():
                self._state["gameView"]["mode"] = "iframe"
                self._state["gameView"]["embedUrl"] = game_embed_url.strip()
        if not self._state["gameView"].get("embedUrl"):
            self._state["gameView"]["mode"] = "iframe"
            self._state["gameView"]["embedUrl"] = "/game-view/"
        self._write_locked()

    def _build_default_state(self, game_embed_url: str) -> dict[str, Any]:
        resolved_embed_url = game_embed_url or "/game-view/"
        return {
            "updatedAt": _now_iso(),
            "status": "idle",
            "gameView": {
                "mode": "iframe",
                "embedUrl": resolved_embed_url,
                "title": "StarCraft II Live View",
                "hint": "Set SWARMBRAIN_GAME_EMBED_URL to override the default local game-view page.",
            },
            "game": {
                "startLocation": "",
                "timeFormatted": "",
                "iteration": 0,
                "gameStage": "early",
                "armyCount": 0,
                "workerCount": 0,
                "supplyUsed": 0,
                "supplyCap": 0,
                "pendingCommandCount": 0,
                "featureSession": "",
                "minerals": 0,
                "vespene": 0,
                "enemyUnitsVisible": 0,
                "enemyStructuresVisible": 0,
                "ownUnitComposition": [],
                "enemyVisibleComposition": [],
                "ownForceDetails": [],
                "enemyVisibleDetails": [],
                "resourceMarkers": [],
                "forceCollaborationSuggestion": {
                    "currentStrategyCommand": "",
                    "targetType": "",
                    "recommendationReason": "",
                    "protectionTarget": "",
                    "strikeTarget": "",
                },
                "ownHpTotal": 0,
            },
            "latestFeatureFrame": None,
            "messages": {
                "latest": None,
                "macro": None,
                "building": None,
                "attack": None,
            },
            "lastMessage": None,
            "commandSequence": [],
            "mapLayout": {},
            "minimapOverlay": [],
            "agentWorkbench": self._build_empty_workbench(),
        }

    @staticmethod
    def _build_empty_workbench() -> dict[str, Any]:
        return {
            "situationAwareness": {
                "title": "态势感知智能体",
                "agentName": "态势感知智能体",
                "updatedAt": "",
                "status": "idle",
                "summary": "等待对局数据...",
                "resourcePanel": {
                    "ownMinerals": 0,
                    "ownVespene": 0,
                    "ownSupply": "0/0",
                    "ownWorkers": 0,
                    "ownEconomyIndex": 0,
                },
                "forcePanel": {
                    "ownArmyCount": 0,
                    "enemyVisibleUnits": 0,
                    "enemyStructures": 0,
                    "gameStage": "early",
                },
                "healthPanel": {"ownHpTotal": 0},
                "ownUnitList": [],
                "enemyVisibleUnitList": [],
                "ownTopUnits": [],
                "enemyTopUnits": [],
                "insights": [],
            },
            "intentRisk": {
                "title": "行为意图与风险识别",
                "agentName": "行为意图与风险识别智能体",
                "updatedAt": "",
                "status": "idle",
                "summary": "等待对局数据...",
                "behaviorIntent": [],
                "riskRecognition": [],
            },
            "killchainDiscovery": {
                "title": "杀伤链发现智能体",
                "agentName": "杀伤链发现智能体",
                "updatedAt": "",
                "status": "idle",
                "summary": "等待敌方链路分析数据...",
                "enemyChains": [],
            },
            "combatPlanning": {
                "title": "作战方案智能体",
                "agentName": "作战方案智能体",
                "updatedAt": "",
                "status": "idle",
                "summary": "等待我方作战方案数据...",
                "plans": [],
            },
        }

    def update_runtime(self, **fields: Any) -> None:
        with self._lock:
            game = self._state["game"]
            for key, value in fields.items():
                if value is None:
                    continue
                game[key] = value
            self._state["status"] = "running"
            self._touch_locked()

    def update_feature_frame(self, frame_dir: Path | str, metadata: dict[str, Any]) -> None:
        frame_dir = Path(frame_dir).resolve()
        payload = deepcopy(metadata)
        payload["frameDir"] = self._to_web_path(frame_dir)
        payload["updatedAt"] = _now_iso()

        features = []
        for feature in payload.get("features", []):
            feature_record = dict(feature)
            image_name = feature_record.get("path", "")
            feature_record["imagePath"] = self._to_web_path(frame_dir / image_name)
            features.append(feature_record)
        payload["features"] = features

        minimap = payload.get("minimap")
        if isinstance(minimap, dict):
            minimap_record = dict(minimap)
            minimap_name = minimap_record.get("path", "")
            minimap_record["imagePath"] = self._to_web_path(frame_dir / minimap_name)
            payload["minimap"] = minimap_record

        with self._lock:
            self._state["latestFeatureFrame"] = payload
            self._state["game"]["featureSession"] = self._to_web_path(frame_dir.parent)
            time_formatted = payload.get("time_formatted")
            if time_formatted:
                self._state["game"]["timeFormatted"] = time_formatted
            self._state["status"] = "running"
            self._touch_locked()

    def update_message(
        self,
        channel: str,
        content: str,
        *,
        title: str = "",
        time_formatted: str = "",
        input_summary: str = "",
        agent_name: str = "",
        source_name: str = "脚本调度器",
    ) -> None:
        normalized_channel = channel if channel in {"latest", "macro", "building", "attack"} else "latest"
        message = {
            "channel": normalized_channel,
            "title": title or normalized_channel,
            "content": content or "",
            "timeFormatted": time_formatted,
            "inputSummary": input_summary or "",
            "agentName": agent_name or "方案生成智能体",
            "sourceName": source_name or "脚本调度器",
            "updatedAt": _now_iso(),
        }
        with self._lock:
            self._state["messages"][normalized_channel] = message
            self._state["messages"]["latest"] = message
            self._state["lastMessage"] = message
            self._state["status"] = "running"
            self._touch_locked()

    def update_commands(self, commands: Iterable[Any]) -> None:
        serialized = self._serialize_commands(commands)
        with self._lock:
            self._state["commandSequence"] = serialized
            self._state["minimapOverlay"] = self._build_minimap_overlays(serialized)
            self._state["game"]["pendingCommandCount"] = len(serialized)
            self._state["status"] = "running"
            self._touch_locked()

    def update_map_layout(self, locations: dict[str, tuple[float, float]]) -> None:
        self._map_layout = self._normalize_map_layout(locations)
        with self._lock:
            self._state["mapLayout"] = deepcopy(self._map_layout)
            self._state["minimapOverlay"] = self._build_minimap_overlays(self._state.get("commandSequence", []))
            self._touch_locked()

    def _serialize_commands(self, commands: Iterable[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for index, command in enumerate(commands, start=1):
            unit, action, target = self._normalize_command(command)
            if not unit and not action and not target:
                continue
            items.append(
                {
                    "order": index,
                    "unit": unit,
                    "action": action,
                    "target": target,
                    "category": self._classify_command(unit, action, target),
                    "locationLabel": self._extract_location_label(target) or self._extract_location_label(action),
                    "summary": " / ".join(part for part in (unit, action, target) if part),
                    "updatedAt": _now_iso(),
                }
            )
        return items

    def _normalize_command(self, command: Any) -> tuple[str, str, str]:
        if isinstance(command, str):
            match = COMMAND_PATTERN.search(command)
            if match:
                return tuple(part.strip() for part in match.groups())
            return command.strip(), "", ""

        if isinstance(command, (list, tuple)):
            values = [str(part).strip() for part in command[:3]]
            while len(values) < 3:
                values.append("")
            return values[0], values[1], values[2]

        return "", "", ""

    def _classify_command(self, unit: str, action: str, target: str) -> str:
        combined = " ".join(part.lower() for part in (unit, action, target))
        action_lower = action.lower()
        start_location = str(self._state.get("game", {}).get("startLocation", "") or "")
        target_location = self._extract_location_label(target) or self._extract_location_label(action)
        building_terms = (
            "hatchery",
            "extractor",
            "spawning pool",
            "evolution chamber",
            "spine crawler",
            "spore crawler",
            "roach warren",
            "baneling nest",
            "hydralisk den",
            "infestation pit",
            "spire",
            "hive",
            "ultralisk cavern",
            "greater spire",
        )

        if any(term in combined for term in (
            "attack", "counterattack", "assault", "strike", "harass", "push", "contain",
            "drop", "all-in", "timing",
        )):
            return "attack"
        if any(term in combined for term in ("defend", "retreat", "hold", "protect", "fallback")):
            return "defend"
        if "overlord" in combined or "scout" in combined:
            return "scout"
        if "build" in action_lower or any(term in combined for term in building_terms):
            return "build"
        # 科技 / 升级 / 施法准备类（星际常见指令语义，与「建造建筑」区分）
        tech_tokens = (
            "research",
            "evolve",
            "chronoboost",
            "chrono boost",
            "stim",
            "metabolic",
            "tunneling claws",
            "blink",
            "charge",
            "resonating glaives",
            "weapon refit",
            "smart servos",
            "drilling claws",
            "pathogen glands",
            "adrenal glands",
            "glial",
            "caduceus reactor",
            "concussive shells",
            "siege tech",
            "cloak",
            "extended thermal lance",
            "gravitic drive",
            "gravitic booster",
            "anabolic synthesis",
            "chitinous plating",
            "pneumatized carapace",
            "grooved spines",
            "muscular augments",
            "lurker aspect",
            "burrow attack",
            "weapon upgrade",
            "armor upgrade",
            "shield upgrade",
            "air weapon",
            "air armor",
            "ground weapon",
            "ground armor",
        )
        if any(t in combined for t in tech_tokens):
            return "tech"
        if "larva" in unit.lower() or any(term in combined for term in ("train", "morph")):
            return "train"
        if any(term in combined for term in ("gather", "collect", "mineral", "vespene", "gas", "harvest")):
            return "gather"
        if any(term in action_lower for term in ("move", "attack")) and target_location and start_location:
            if target_location[0] != start_location[0]:
                return "attack"
            return "defend"
        return "other"

    def _build_minimap_overlays(self, commands: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        if not getattr(self, "_map_layout", None):
            return []

        overlays: list[dict[str, Any]] = []
        stack_counts: dict[tuple[str, str], int] = {}
        for command in commands:
            category = str(command.get("category", "other") or "other")
            if category not in {"attack", "defend", "scout"}:
                continue

            location_label = str(command.get("locationLabel", "") or "")
            if not location_label:
                continue
            point = self._map_layout.get(location_label)
            if not point:
                continue

            stack_key = (location_label, category)
            stack_index = stack_counts.get(stack_key, 0)
            stack_counts[stack_key] = stack_index + 1

            overlays.append(
                {
                    "order": int(command.get("order", 0) or 0),
                    "category": category,
                    "locationLabel": location_label,
                    "xPercent": point["xPercent"],
                    "yPercent": point["yPercent"],
                    "stackIndex": stack_index,
                    "summary": command.get("summary", ""),
                    "unit": command.get("unit", ""),
                    "action": command.get("action", ""),
                    "target": command.get("target", ""),
                }
            )
        return overlays

    def _normalize_map_layout(self, locations: dict[str, tuple[float, float]]) -> dict[str, dict[str, float]]:
        if not locations:
            return {}

        xs = [float(position[0]) for position in locations.values()]
        ys = [float(position[1]) for position in locations.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)

        normalized: dict[str, dict[str, float]] = {}
        for label, position in locations.items():
            x_value = float(position[0])
            y_value = float(position[1])
            normalized[label] = {
                "xPercent": round(((x_value - min_x) / span_x) * 100, 3),
                "yPercent": round((1 - ((y_value - min_y) / span_y)) * 100, 3),
            }
        return normalized

    def _extract_location_label(self, value: str) -> str:
        if not value:
            return ""
        match = LOCATION_PATTERN.search(value.upper())
        return match.group(1) if match else ""

    def _touch_locked(self) -> None:
        self._state["updatedAt"] = _now_iso()
        self._state["agentWorkbench"] = self._derive_workbench()
        self._write_locked()

    # ── 自动推导 agentWorkbench ───────────────────────────────────────────────

    def _cn_unit(self, name: str) -> str:
        key = name.lower().strip().replace("_", "").replace(" ", "")
        return _UNIT_CN.get(key, name)

    @staticmethod
    def _is_structure_name(name: str) -> bool:
        key = name.lower().strip().replace("_", "").replace(" ", "")
        structure_terms = (
            "hatchery", "lair", "hive", "extractor", "pool", "chamber", "warren",
            "nest", "den", "pit", "spire", "cavern", "crawler", "network", "worm",
            "nexus", "pylon", "commandcenter", "orbitalcommand", "planetaryfortress",
            "supplydepot", "bunker", "turret", "barracks", "factory", "starport",
            "gateway", "warpgate", "cyberneticscore", "roboticsfacility", "stargate",
            "roboticsbay", "fleetbeacon", "twilightcouncil", "darkshrine",
            "shieldbattery", "photoncannon", "assimilator", "refinery", "armory",
            "engineeringbay", "fusioncore", "ghostacademy", "sensortower", "techlab",
            "reactor",
        )
        return any(term in key for term in structure_terms)

    def _derive_workbench(self) -> dict[str, Any]:
        game = self._state.get("game", {})
        commands = self._state.get("commandSequence", [])
        status = self._state.get("status", "idle")

        cat_counts: dict[str, int] = {
            "attack": 0, "defend": 0, "gather": 0,
            "build": 0, "train": 0, "scout": 0, "tech": 0, "other": 0,
        }
        for cmd in commands:
            cat = str(cmd.get("category", "other") or "other")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        worker_count = int(game.get("workerCount", 0) or 0)
        army_count = int(game.get("armyCount", 0) or 0)
        supply_used = int(game.get("supplyUsed", 0) or 0)
        supply_cap = int(game.get("supplyCap", 0) or 0)
        minerals = int(game.get("minerals", 0) or 0)
        vespene = int(game.get("vespene", 0) or 0)
        game_stage = str(game.get("gameStage", "early") or "early")
        enemy_visible = int(game.get("enemyUnitsVisible", 0) or 0)
        enemy_structs = int(game.get("enemyStructuresVisible", 0) or 0)

        economy_idx = min(100, worker_count * 2 + (minerals // 50) + (vespene // 30))

        own_hp_live = int(game.get("ownHpTotal", 0) or 0)
        own_hp = own_hp_live if own_hp_live > 0 else army_count * 80 + worker_count * 45

        def _rows_from_game(key: str) -> list[dict[str, Any]]:
            raw_rows = game.get(key) or []
            if not isinstance(raw_rows, list):
                return []
            out: list[dict[str, Any]] = []
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("name", "") or "").strip()
                cnt = int(row.get("count", 0) or 0)
                if not code:
                    continue
                out.append({"code": code, "name": self._cn_unit(code), "count": cnt})
            return out

        def _enemy_detail_rows_from_game() -> list[dict[str, Any]]:
            raw_rows = game.get("enemyVisibleDetails") or []
            out: list[dict[str, Any]] = []
            if isinstance(raw_rows, list):
                for row in raw_rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("name", "") or "").strip()
                    count = int(row.get("count", 0) or 0)
                    if not code or count <= 0:
                        continue
                    out.append({
                        "code": code,
                        "name": self._cn_unit(code),
                        "count": count,
                        "location": str(row.get("location", "") or "--"),
                        "action": str(row.get("action", "") or "当前可见"),
                        "kind": str(row.get("kind", "") or ("structure" if self._is_structure_name(code) else "unit")),
                    })
            if out:
                return out
            fallback_rows = _rows_from_game("enemyVisibleComposition")
            return [
                {
                    "code": row["code"],
                    "name": row["name"],
                    "count": row["count"],
                    "location": "--",
                    "action": "当前可见",
                    "kind": "structure" if self._is_structure_name(row["code"]) else "unit",
                }
                for row in fallback_rows
            ]

        def _own_detail_rows_from_game() -> list[dict[str, Any]]:
            raw_rows = game.get("ownForceDetails") or []
            out: list[dict[str, Any]] = []
            if isinstance(raw_rows, list):
                for row in raw_rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("name", "") or "").strip()
                    count = int(row.get("count", 0) or 0)
                    if not code or count <= 0:
                        continue
                    out.append({
                        "code": code,
                        "name": self._cn_unit(code),
                        "count": count,
                        "location": str(row.get("location", "") or "--"),
                        "action": str(row.get("action", "") or "待命/集结"),
                        "kind": str(row.get("kind", "") or ("structure" if self._is_structure_name(code) else "unit")),
                    })
            if out:
                return out
            fallback_rows = _rows_from_game("ownUnitComposition")
            return [
                {
                    "code": row["code"],
                    "name": row["name"],
                    "count": row["count"],
                    "location": "--",
                    "action": "待命/集结",
                    "kind": "structure" if self._is_structure_name(row["code"]) else "unit",
                }
                for row in fallback_rows
            ]

        own_unit_list = _rows_from_game("ownUnitComposition")
        enemy_visible_list = _rows_from_game("enemyVisibleComposition")
        enemy_visible_details = _enemy_detail_rows_from_game()
        own_force_details = _own_detail_rows_from_game()

        # 无实时单位表时，用训练指令兜底（仅我方）
        if not own_unit_list:
            train_units = [cmd.get("unit", "") for cmd in commands
                           if cmd.get("category") == "train" and cmd.get("unit")]
            unit_ctr = Counter(train_units)
            own_unit_list = [
                {"code": u, "name": self._cn_unit(u), "count": c}
                for u, c in unit_ctr.most_common(20) if u
            ]

        own_top = own_unit_list[:5]

        # 推导战场总结语
        stage_zh = {"early": "前期", "mid": "中期", "late": "后期"}.get(game_stage, "前期")
        dominant = max(cat_counts, key=cat_counts.get)  # type: ignore[arg-type]
        dom_zh = {"attack": "进攻", "defend": "防守", "build": "建造扩张",
                  "train": "兵力生产", "scout": "侦察", "gather": "资源采集",
                  "tech": "科技升级", "other": "综合"}.get(dominant, "综合")
        summary = f"{stage_zh}阶段 · 当前主导行动：{dom_zh}（兵力 {army_count}，工人 {worker_count}）"
        intent_summary = (
            f"已观测 {len(enemy_visible_details)} 组敌情 · 可见目标 {enemy_visible + enemy_structs}"
            if enemy_visible_details else
            f"共 {len(commands)} 条指令 · 主导意图：{dom_zh}"
        )

        # 行为意图推导
        behavior_intent = self._build_behavior_intent(
            cat_counts, game_stage, army_count, commands, enemy_visible_details
        )
        # 风险识别推导
        risk_recognition = self._build_risk_recognition(
            cat_counts,
            game_stage,
            worker_count,
            army_count,
            supply_used,
            supply_cap,
            enemy_visible_details,
            enemy_visible,
            enemy_structs,
        )
        killchain_discovery = self._build_enemy_killchain_discovery(enemy_visible_details)
        combat_planning = self._build_own_combat_plans(own_force_details, killchain_discovery.get("enemyChains", []))

        now = _now_iso()
        return {
            "situationAwareness": {
                "title": "态势感知智能体",
                "agentName": "态势感知智能体",
                "updatedAt": now,
                "status": status,
                "summary": summary,
                "resourcePanel": {
                    "ownMinerals": minerals,
                    "ownVespene": vespene,
                    "ownSupply": f"{supply_used}/{supply_cap}",
                    "ownWorkers": worker_count,
                    "ownEconomyIndex": economy_idx,
                },
                "forcePanel": {
                    "ownArmyCount": army_count,
                    "enemyVisibleUnits": enemy_visible,
                    "enemyStructures": enemy_structs,
                    "gameStage": game_stage,
                },
                "healthPanel": {
                    "ownHpTotal": own_hp,
                },
                "ownUnitList": own_unit_list,
                "enemyVisibleUnitList": enemy_visible_list,
                "ownTopUnits": own_top,
                "enemyTopUnits": enemy_visible_list[:5],
                "insights": [],
            },
            "intentRisk": {
                "title": "行为意图与风险识别",
                "agentName": "行为意图与风险识别智能体",
                "updatedAt": now,
                "status": status,
                "summary": intent_summary,
                "behaviorIntent": behavior_intent,
                "riskRecognition": risk_recognition,
            },
            "killchainDiscovery": {
                "title": "杀伤链发现智能体",
                "agentName": "杀伤链发现智能体",
                "updatedAt": now,
                "status": status,
                "summary": killchain_discovery.get("summary", "等待敌方链路分析数据..."),
                "enemyChains": killchain_discovery.get("enemyChains", []),
            },
            "combatPlanning": {
                "title": "作战方案智能体",
                "agentName": "作战方案智能体",
                "updatedAt": now,
                "status": status,
                "summary": combat_planning.get("summary", "等待我方作战方案数据..."),
                "plans": combat_planning.get("plans", []),
            },
        }

    @staticmethod
    def _norm_token(text: str) -> str:
        return str(text or "").lower().replace("_", "").replace(" ", "").replace("-", "")

    @staticmethod
    def _score_level(score: int) -> str:
        value = int(score or 0)
        if value >= 80:
            return "强"
        if value >= 60:
            return "可行"
        return "受限"

    def _classify_capability(self, name: str, kind: str, action: str) -> str:
        token = self._norm_token(name)
        action_text = str(action or "").lower()
        # broodlord 含子串 overlord，不可与王虫 overlord 共用同一匹配规则
        if "broodlord" in token:
            return "strike"
        detect_tokens = ("overlord", "overseer", "observer", "raven", "oracle", "scout", "sensor")
        defense_tokens = ("spine", "spore", "bunker", "turret", "cannon", "battery", "fortress")
        support_tokens = (
            "drone", "scv", "probe", "queen", "medivac", "warpprism",
            "hatchery", "lair", "hive", "extractor", "refinery", "assimilator", "commandcenter", "nexus",
        )
        if any(t in token for t in detect_tokens) or any(t in action_text for t in ("侦察", "扫描", "探测", "scout")):
            return "detect"
        if kind == "structure" and any(t in token for t in defense_tokens):
            return "defense"
        if any(t in token for t in support_tokens) or any(t in action_text for t in ("采集", "补给", "生产", "gather", "supply")):
            return "support"
        return "strike"

    def _build_enemy_killchain_discovery(self, enemy_rows: list[dict[str, Any]]) -> dict[str, Any]:
        stage_order = [
            ("discover", "侦察发现"),
            ("locate", "目标定位"),
            ("track", "持续跟踪"),
            ("designate", "目标指示"),
            ("strike", "火力打击"),
            ("assess", "效果评估"),
        ]
        dim_meta = {
            "strike": {"label": "打击", "verb": "压制", "nodeType": "打击单元"},
            "detect": {"label": "探测", "verb": "致盲", "nodeType": "侦查单元"},
            "defense": {"label": "防御", "verb": "破防", "nodeType": "支援单元"},
            "support": {"label": "保障", "verb": "断补", "nodeType": "支援单元"},
        }
        grouped: dict[str, list[dict[str, Any]]] = {k: [] for k in ("strike", "detect", "defense", "support")}
        for row in enemy_rows:
            capability = self._classify_capability(
                str(row.get("code", row.get("name", ""))),
                str(row.get("kind", "unit")),
                str(row.get("action", "")),
            )
            row_with_cap = dict(row)
            row_with_cap["capability"] = capability
            grouped.setdefault(capability, []).append(row_with_cap)

        enemy_chains: list[dict[str, Any]] = []
        for dim in ("strike", "detect", "defense", "support"):
            rows = sorted(grouped.get(dim, []), key=lambda r: -(int(r.get("count", 0) or 0)))[:3]
            meta = dim_meta[dim]
            for idx, row in enumerate(rows):
                count = int(row.get("count", 0) or 0)
                location = str(row.get("location", "--") or "--")
                pressure_bonus = 10 if any(k in str(row.get("action", "")).lower() for k in ("attack", "harass", "压制", "前压")) else 0
                confidence = max(45, min(100, 52 + count * 9 + pressure_bonus))
                status = "待激活"
                if confidence >= 84:
                    status = "已锁定"
                elif confidence >= 68:
                    status = "进行中"
                confidence_level = self._score_level(confidence)
                nodes = []
                for stage_key, stage_label in stage_order:
                    nodes.append({
                        "stageKey": stage_key,
                        "stageLabel": stage_label,
                        "nodeType": meta["nodeType"],
                        "unitName": str(row.get("name", "敌方目标")),
                        "action": str(row.get("action", "当前可见")),
                        "location": location,
                        "capability": dim,
                    })
                enemy_chains.append({
                    "key": f"{dim}-{idx}",
                    "dimension": dim,
                    "title": f"{meta['verb']}敌方{meta['label']}链-{location}",
                    "targetLocation": location,
                    "status": status,
                    "confidence": confidence,
                    "confidenceLevel": confidence_level,
                    "summary": f"目标 {row.get('name', '未知')}×{count}，在 {location} 执行 {row.get('action', '活动')}",
                    "nodes": nodes,
                })

        enemy_chains.sort(key=lambda c: -(int(c.get("confidence", 0) or 0)))
        if not enemy_chains:
            return {
                "summary": "暂无足够敌方信息，无法构建可打击链路。",
                "enemyChains": [],
            }
        top_dims = Counter(str(c.get("dimension", "")) for c in enemy_chains)
        top_dim = top_dims.most_common(1)[0][0] if top_dims else "strike"
        top_dim_zh = dim_meta.get(top_dim, {}).get("label", "打击")
        return {
            "summary": f"已识别 {len(enemy_chains)} 条敌方可破坏链路，当前优先压制敌方{top_dim_zh}体系。",
            "enemyChains": enemy_chains,
        }

    def _build_own_combat_plans(self, own_rows: list[dict[str, Any]], enemy_chains: list[dict[str, Any]]) -> dict[str, Any]:
        dim_label = {"strike": "打击", "detect": "探测", "defense": "防御", "support": "保障"}
        chains_by_dim: dict[str, list[dict[str, Any]]] = {k: [] for k in ("strike", "detect", "defense", "support")}
        for chain in enemy_chains:
            dim = str(chain.get("dimension", "strike"))
            chains_by_dim.setdefault(dim, []).append(chain)
        for dim in chains_by_dim:
            chains_by_dim[dim].sort(key=lambda c: -(int(c.get("confidence", 0) or 0)))

        dim_priority = sorted(
            ("strike", "detect", "defense", "support"),
            key=lambda d: -max((int(c.get("confidence", 0) or 0) for c in chains_by_dim.get(d, [])), default=0),
        )
        plan_count = max(2, min(4, len(enemy_chains)))
        plans: list[dict[str, Any]] = []
        own_force_score = sum(int(r.get("count", 0) or 0) for r in own_rows)
        title_prefix = ["优先压制", "集中反制", "分层瓦解", "联动封控", "持续削弱", "态势阻断"]
        for plan_idx in range(plan_count):
            dims = [dim_priority[(plan_idx + i) % len(dim_priority)] for i in range(min(4, len(dim_priority)))]
            key = f"plan-{plan_idx + 1}"
            title = f"{title_prefix[plan_idx % len(title_prefix)]}方案"
            used_chain_keys: set[str] = set()
            tasks: list[dict[str, Any]] = []
            for dim in dims:
                dim_candidates = chains_by_dim.get(dim, [])
                if not dim_candidates:
                    continue
                candidate = None
                for offset in range(len(dim_candidates)):
                    probe = dim_candidates[(plan_idx + offset) % len(dim_candidates)]
                    if str(probe.get("key")) not in used_chain_keys:
                        candidate = probe
                        break
                if not candidate:
                    continue
                used_chain_keys.add(str(candidate.get("key")))
                confidence = int(candidate.get("confidence", 0) or 0)
                executability = max(35, min(100, int(confidence * 0.72 + own_force_score * 0.25)))
                rank_score = max(35, min(100, int(confidence * 0.58 + executability * 0.42)))
                priority_level = self._score_level(rank_score)
                executability_level = self._score_level(executability)
                tasks.append({
                    "taskKey": f"{key}:{candidate.get('key')}",
                    "chainKey": candidate.get("key"),
                    "dimension": dim,
                    "dimensionLabel": dim_label.get(dim, dim),
                    "title": f"破坏敌方{dim_label.get(dim, dim)}节点：{candidate.get('targetLocation', '--')}",
                    "summary": candidate.get("summary", ""),
                    "targetLocation": candidate.get("targetLocation", "--"),
                    "status": candidate.get("status", "待激活"),
                    "confidence": confidence,
                    "confidenceLevel": self._score_level(confidence),
                    "executability": executability,
                    "executabilityLevel": executability_level,
                    "rankScore": rank_score,
                    "priorityLevel": priority_level,
                    "nodes": candidate.get("nodes", []),
                })
            if not tasks:
                continue
            plan_score = int(sum(int(t.get("rankScore", 0) or 0) for t in tasks) / max(1, len(tasks)))
            plan_priority_level = self._score_level(plan_score)
            plans.append({
                "key": key,
                "title": title,
                "description": f"我方围绕 {', '.join(dim_label.get(d, d) for d in dims[:3])} 协同执行，优先破坏敌方关键链路。",
                "planScore": plan_score,
                "planPriorityLevel": plan_priority_level,
                "tasks": sorted(tasks, key=lambda t: -(int(t.get("rankScore", 0) or 0))),
                "chainKeys": [str(t.get("chainKey", "")) for t in tasks],
            })
        plans.sort(key=lambda p: -(int(p.get("planScore", 0) or 0)))
        if len(plans) == 1:
            base = plans[0]
            cloned_tasks = [dict(task) for task in base.get("tasks", [])]
            plans.append({
                "key": "plan-2",
                "title": "弹性补位方案",
                "description": "在主方案基础上保留机动冗余，用于应对敌方链路突变。",
                "planScore": int(base.get("planScore", 0) or 0),
                "planPriorityLevel": str(base.get("planPriorityLevel", "可行") or "可行"),
                "tasks": cloned_tasks,
                "chainKeys": [str(t.get("chainKey", "")) for t in cloned_tasks],
            })
        for idx, plan in enumerate(plans, start=1):
            plan["rank"] = idx
        if not plans:
            return {
                "summary": "暂无可执行作战方案，等待更多敌情与我方编组信息。",
                "plans": [],
            }
        return {
            "summary": f"已生成 {len(plans)} 套我方作战方案，按可执行度与态势优先级排序。",
            "plans": plans,
        }

    def _build_behavior_intent(
        self, counts: dict[str, int], stage: str, army: int,
        commands: list[dict[str, Any]],
        enemy_details: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if enemy_details:
            return [
                {
                    "title": row["name"],
                    "count": int(row.get("count", 0) or 0),
                    "location": str(row.get("location", "") or "--"),
                    "action": str(row.get("action", "") or "当前可见"),
                    "kind": str(row.get("kind", "") or "unit"),
                    "confidence": "实时",
                    "evidence": (
                        f"{'建筑' if row.get('kind') == 'structure' else '单位'}情报"
                        f" · 当前观测数量 {int(row.get('count', 0) or 0)}"
                    ),
                }
                for row in enemy_details[:12]
            ]
        items: list[dict[str, Any]] = []
        total = max(sum(counts.values()), 1)
        if counts["attack"] / total >= 0.3 or (army >= 10 and counts["attack"] > 0):
            items.append({
                "title": "进攻意图",
                "confidence": "高" if counts["attack"] / total >= 0.45 else "中",
                "target": next(
                    (c.get("target", "--") for c in commands if c.get("category") == "attack" and c.get("target")),
                    "--",
                ),
                "evidence": f"进攻指令占比 {counts['attack']} 条，当前兵力 {army}",
            })
        if counts["scout"] > 0:
            items.append({
                "title": "侦察意图",
                "confidence": "中",
                "target": next(
                    (c.get("target", "--") for c in commands if c.get("category") == "scout" and c.get("target")),
                    "前线阵地",
                ),
                "evidence": f"侦察指令 {counts['scout']} 条，正在补充战场视野",
            })
        if counts["build"] + counts["train"] >= 3:
            items.append({
                "title": "扩张生产意图",
                "confidence": "高" if stage == "mid" else "中",
                "target": "--",
                "evidence": f"建造 {counts['build']} + 训练 {counts['train']} 条，经济型扩张节奏",
            })
        if counts["defend"] > 0:
            items.append({
                "title": "防守部署意图",
                "confidence": "中",
                "target": "主基地",
                "evidence": f"防守指令 {counts['defend']} 条，优先巩固防线",
            })
        if not items:
            items.append({
                "title": "综合运营",
                "confidence": "低",
                "target": "--",
                "evidence": "当前指令较少，处于过渡状态",
            })
        return items

    def _build_risk_recognition(
        self,
        counts: dict[str, int],
        stage: str,
        workers: int,
        army: int,
        supply_used: int,
        supply_cap: int,
        enemy_details: list[dict[str, Any]],
        enemy_visible: int,
        enemy_structs: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if enemy_details:
            front_rows = [
                row for row in enemy_details
                if str(row.get("location", "--")).startswith("A") or str(row.get("location", "--")).startswith("B")
            ]
            closest_pressure = next(
                (
                    row for row in front_rows
                    if str(row.get("action", "")).find("前压") >= 0
                    or str(row.get("action", "")).find("进攻") >= 0
                    or str(row.get("action", "")).find("骚扰") >= 0
                ),
                None,
            )
            if closest_pressure:
                items.append({
                    "title": "前线接触风险",
                    "priority": "高",
                    "action": (
                        f"在 {closest_pressure.get('location', '--')} 观测到"
                        f"{closest_pressure.get('count', 0)} 个{closest_pressure.get('name', '敌方目标')}，"
                        f"其状态为“{closest_pressure.get('action', '当前可见')}”，"
                        "短时内可能形成前压或骚扰。"
                    ),
                })

            air_units = {"医疗机", "维京战机", "解放者", "女妖", "战列巡洋舰", "飞龙", "腐化者", "凤凰", "先知", "虚空辉光舰"}
            air_row = next((row for row in enemy_details if str(row.get("name", "")) in air_units), None)
            if air_row:
                items.append({
                    "title": "空中威胁研判",
                    "priority": "中",
                    "action": (
                        f"已观测到 {air_row.get('name', '空中单位')} 出现在 {air_row.get('location', '--')}，"
                        "需要警惕空投、绕后或对空压制。"
                    ),
                })

            production_names = {"兵营", "重工厂", "星港", "传送门", "机械台", "星门"}
            production_rows = [row for row in enemy_details if str(row.get("name", "")) in production_names]
            if production_rows:
                total_prod = sum(int(row.get("count", 0) or 0) for row in production_rows)
                if total_prod >= 3:
                    sample = production_rows[0]
                    items.append({
                        "title": "敌方扩兵迹象",
                        "priority": "中",
                        "action": (
                            f"当前可见情报中包含 {total_prod} 个高价值生产建筑，"
                            f"其中 {sample.get('location', '--')} 一带有 {sample.get('name', '生产建筑')}，"
                            "敌方更像在持续补兵或准备下一波攻势。"
                        ),
                    })

        if workers < 12 and stage in ("mid", "late"):
            items.append({
                "title": "工人数量不足",
                "priority": "高",
                "action": f"当前工人仅 {workers}，建议优先补充至 16 以上维持经济",
            })
        if supply_cap > 0 and supply_used / supply_cap >= 0.9:
            items.append({
                "title": "供应上限危机",
                "priority": "高",
                "action": f"补给使用率 {supply_used}/{supply_cap}，立即生产额外虫卵传输",
            })
        if counts["scout"] == 0 and stage != "early":
            items.append({
                "title": "情报盲区风险",
                "priority": "中",
                "action": "无侦察指令，敌方动向不明，建议派遣覆盖前线和分矿区域",
            })
        if counts["defend"] == 0 and army < 6:
            items.append({
                "title": "防线空虚风险",
                "priority": "中" if stage == "early" else "高",
                "action": "兵力薄弱且无防守指令，注意敌方突袭风险",
            })
        if not enemy_details and enemy_visible + enemy_structs == 0:
            items.append({
                "title": "视野信息不足",
                "priority": "中" if stage != "early" else "低",
                "action": "当前视野内尚未捕获敌方可观测目标，建议继续补充侦察以刷新敌情。",
            })
        if not items:
            items.append({
                "title": "当前态势平稳",
                "priority": "低",
                "action": "当前可见敌情未显示出明显高压动作，继续跟踪敌军动向即可。",
            })
        return items

    def _write_locked(self) -> None:
        payload = json.dumps(self._state, indent=2, ensure_ascii=False)
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        for _ in range(6):
            try:
                temp_path.write_text(payload, encoding="utf-8")
                temp_path.replace(self.state_path)
                return
            except (PermissionError, OSError):
                time.sleep(0.05)
        try:
            temp_path.unlink(missing_ok=True)
        except (PermissionError, OSError):
            pass

    def _to_web_path(self, path: Path) -> str:
        try:
            relative_path = path.resolve().relative_to(self.root_dir)
            return relative_path.as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def _read_existing_state(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _merge_dicts(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged
