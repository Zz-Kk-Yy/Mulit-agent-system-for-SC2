"""Static SC2 unit capability table.

Curated from Liquipedia/official balance data (LotV current patch).
Fields: hp, shield, armor, supply, minerals, vespene, build_time (s),
        ground_range, air_range, dps_ground, dps_air, speed, sight, is_air.

Covers the most common units; unknown units return a minimal default.
"""

from __future__ import annotations

UNIT_STATS: dict[str, dict] = {
    # ── Terran ────────────────────────────────────────────────────
    "SCV":        {"hp": 45,  "shield": 0, "armor": 0, "supply": 1, "minerals": 50,  "vespene": 0,   "build_time": 12, "ground_range": 0,  "air_range": 0, "dps_ground": 4.67, "dps_air": 0,    "speed": 3.94, "sight": 8,  "is_air": False, "role": "worker"},
    "Marine":     {"hp": 45,  "shield": 0, "armor": 0, "supply": 1, "minerals": 50,  "vespene": 0,   "build_time": 18, "ground_range": 5,  "air_range": 5, "dps_ground": 9.8,  "dps_air": 9.8,  "speed": 3.15, "sight": 9,  "is_air": False, "role": "infantry"},
    "Marauder":   {"hp": 125, "shield": 0, "armor": 1, "supply": 2, "minerals": 100, "vespene": 25,  "build_time": 21, "ground_range": 6,  "air_range": 0, "dps_ground": 9.3,  "dps_air": 0,    "speed": 3.15, "sight": 10, "is_air": False, "role": "infantry"},
    "Reaper":     {"hp": 60,  "shield": 0, "armor": 0, "supply": 1, "minerals": 50,  "vespene": 50,  "build_time": 32, "ground_range": 5,  "air_range": 0, "dps_ground": 10.1, "dps_air": 0,    "speed": 5.25, "sight": 9,  "is_air": False, "role": "harass"},
    "Ghost":      {"hp": 100, "shield": 0, "armor": 0, "supply": 2, "minerals": 150, "vespene": 125, "build_time": 29, "ground_range": 6,  "air_range": 6, "dps_ground": 9.3,  "dps_air": 9.3,  "speed": 3.94, "sight": 11, "is_air": False, "role": "caster"},
    "Hellion":    {"hp": 90,  "shield": 0, "armor": 0, "supply": 2, "minerals": 100, "vespene": 0,   "build_time": 21, "ground_range": 5,  "air_range": 0, "dps_ground": 4.48, "dps_air": 0,    "speed": 5.95, "sight": 10, "is_air": False, "role": "harass"},
    "Hellbat":    {"hp": 135, "shield": 0, "armor": 0, "supply": 2, "minerals": 100, "vespene": 0,   "build_time": 21, "ground_range": 2,  "air_range": 0, "dps_ground": 12.6, "dps_air": 0,    "speed": 3.15, "sight": 10, "is_air": False, "role": "infantry"},
    "SiegeTank":  {"hp": 175, "shield": 0, "armor": 1, "supply": 3, "minerals": 150, "vespene": 125, "build_time": 32, "ground_range": 7,  "air_range": 0, "dps_ground": 13.5, "dps_air": 0,    "speed": 3.15, "sight": 11, "is_air": False, "role": "siege"},
    "SiegeTankSieged": {"hp": 175, "shield": 0, "armor": 1, "supply": 3, "minerals": 150, "vespene": 125, "build_time": 32, "ground_range": 13, "air_range": 0, "dps_ground": 18.7, "dps_air": 0, "speed": 0, "sight": 11, "is_air": False, "role": "siege"},
    "Cyclone":    {"hp": 180, "shield": 0, "armor": 1, "supply": 3, "minerals": 150, "vespene": 100, "build_time": 32, "ground_range": 6,  "air_range": 6, "dps_ground": 25.2, "dps_air": 25.2, "speed": 4.13, "sight": 11, "is_air": False, "role": "vehicle"},
    "Thor":       {"hp": 400, "shield": 0, "armor": 1, "supply": 6, "minerals": 300, "vespene": 200, "build_time": 43, "ground_range": 7,  "air_range": 11,"dps_ground": 65.9, "dps_air": 28.1, "speed": 2.62, "sight": 11, "is_air": False, "role": "heavy"},
    "Viking":     {"hp": 135, "shield": 0, "armor": 0, "supply": 2, "minerals": 150, "vespene": 75,  "build_time": 30, "ground_range": 0,  "air_range": 9, "dps_ground": 0,    "dps_air": 14,   "speed": 3.85, "sight": 10, "is_air": True,  "role": "anti_air"},
    "VikingFighter": {"hp": 135, "shield": 0, "armor": 0, "supply": 2, "minerals": 150, "vespene": 75, "build_time": 30, "ground_range": 0, "air_range": 9, "dps_ground": 0, "dps_air": 14, "speed": 3.85, "sight": 10, "is_air": True, "role": "anti_air"},
    "Medivac":    {"hp": 150, "shield": 0, "armor": 1, "supply": 2, "minerals": 100, "vespene": 100, "build_time": 30, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 3.5,  "sight": 11, "is_air": True,  "role": "support"},
    "Liberator":  {"hp": 180, "shield": 0, "armor": 0, "supply": 3, "minerals": 150, "vespene": 150, "build_time": 43, "ground_range": 0,  "air_range": 5, "dps_ground": 0,    "dps_air": 10.9, "speed": 4.72, "sight": 10, "is_air": True,  "role": "siege_air"},
    "Raven":      {"hp": 140, "shield": 0, "armor": 1, "supply": 2, "minerals": 100, "vespene": 200, "build_time": 34, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 3.85, "sight": 11, "is_air": True,  "role": "caster"},
    "Banshee":    {"hp": 140, "shield": 0, "armor": 0, "supply": 3, "minerals": 150, "vespene": 100, "build_time": 43, "ground_range": 6,  "air_range": 0, "dps_ground": 27,   "dps_air": 0,    "speed": 3.85, "sight": 10, "is_air": True,  "role": "harass"},
    "Battlecruiser": {"hp": 550, "shield": 0, "armor": 3, "supply": 6, "minerals": 400, "vespene": 300, "build_time": 64, "ground_range": 6, "air_range": 6, "dps_ground": 49.8, "dps_air": 37.4, "speed": 2.62, "sight": 12, "is_air": True, "role": "heavy_air"},
    # ── Protoss ───────────────────────────────────────────────────
    "Probe":      {"hp": 20,  "shield": 20,"armor": 0, "supply": 1, "minerals": 50,  "vespene": 0,   "build_time": 12, "ground_range": 0,  "air_range": 0, "dps_ground": 4.67, "dps_air": 0,    "speed": 3.94, "sight": 8,  "is_air": False, "role": "worker"},
    "Zealot":     {"hp": 100, "shield": 50,"armor": 1, "supply": 2, "minerals": 100, "vespene": 0,   "build_time": 27, "ground_range": 0,  "air_range": 0, "dps_ground": 18.6, "dps_air": 0,    "speed": 3.15, "sight": 9,  "is_air": False, "role": "infantry"},
    "Stalker":    {"hp": 80,  "shield": 80,"armor": 1, "supply": 2, "minerals": 125, "vespene": 50,  "build_time": 27, "ground_range": 6,  "air_range": 6, "dps_ground": 9.7,  "dps_air": 9.7,  "speed": 4.13, "sight": 10, "is_air": False, "role": "ranged"},
    "Sentry":     {"hp": 40,  "shield": 40,"armor": 1, "supply": 2, "minerals": 50,  "vespene": 100, "build_time": 27, "ground_range": 5,  "air_range": 5, "dps_ground": 8.4,  "dps_air": 8.4,  "speed": 3.15, "sight": 9,  "is_air": False, "role": "caster"},
    "Adept":      {"hp": 70,  "shield": 70,"armor": 1, "supply": 2, "minerals": 100, "vespene": 25,  "build_time": 27, "ground_range": 4,  "air_range": 0, "dps_ground": 6.2,  "dps_air": 0,    "speed": 3.5,  "sight": 9,  "is_air": False, "role": "ranged"},
    "HighTemplar":{"hp": 40,  "shield": 40,"armor": 0, "supply": 2, "minerals": 50,  "vespene": 150, "build_time": 39, "ground_range": 6,  "air_range": 6, "dps_ground": 3.2,  "dps_air": 3.2,  "speed": 2.62, "sight": 10, "is_air": False, "role": "caster"},
    "DarkTemplar":{"hp": 40,  "shield": 80,"armor": 1, "supply": 2, "minerals": 125, "vespene": 125, "build_time": 39, "ground_range": 0,  "air_range": 0, "dps_ground": 37.2, "dps_air": 0,    "speed": 3.94, "sight": 8,  "is_air": False, "role": "harass"},
    "Archon":     {"hp": 10,  "shield": 350,"armor": 0,"supply": 4, "minerals": 0,   "vespene": 0,   "build_time": 9,  "ground_range": 3,  "air_range": 3, "dps_ground": 20,   "dps_air": 20,   "speed": 3.94, "sight": 9,  "is_air": False, "role": "infantry"},
    "Immortal":   {"hp": 200, "shield": 100,"armor":1, "supply": 4, "minerals": 275, "vespene": 100, "build_time": 39, "ground_range": 6,  "air_range": 0, "dps_ground": 19.2, "dps_air": 0,    "speed": 3.15, "sight": 9,  "is_air": False, "role": "heavy"},
    "Colossus":   {"hp": 200, "shield": 150,"armor":1, "supply": 6, "minerals": 300, "vespene": 200, "build_time": 54, "ground_range": 7,  "air_range": 0, "dps_ground": 18.7, "dps_air": 0,    "speed": 3.15, "sight": 9,  "is_air": False, "role": "siege"},
    "Disruptor":  {"hp": 100, "shield": 100,"armor":1, "supply": 3, "minerals": 150, "vespene": 150, "build_time": 36, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 3.15, "sight": 9,  "is_air": False, "role": "siege"},
    "Observer":   {"hp": 40,  "shield": 20,"armor": 0, "supply": 1, "minerals": 25,  "vespene": 75,  "build_time": 21, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 2.63, "sight": 11, "is_air": True,  "role": "scout"},
    "WarpPrism":  {"hp": 100, "shield": 100,"armor":0, "supply": 2, "minerals": 200, "vespene": 0,   "build_time": 36, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 3.94, "sight": 10, "is_air": True,  "role": "transport"},
    "Phoenix":    {"hp": 120, "shield": 60,"armor": 0, "supply": 2, "minerals": 150, "vespene": 100, "build_time": 25, "ground_range": 0,  "air_range": 5, "dps_ground": 0,    "dps_air": 25.2, "speed": 5.95, "sight": 10, "is_air": True,  "role": "anti_air"},
    "VoidRay":    {"hp": 150, "shield": 100,"armor":0, "supply": 4, "minerals": 250, "vespene": 150, "build_time": 43, "ground_range": 6,  "air_range": 6, "dps_ground": 16.8, "dps_air": 16.8, "speed": 3.5,  "sight": 10, "is_air": True,  "role": "heavy_air"},
    "Oracle":     {"hp": 100, "shield": 60,"armor": 0, "supply": 3, "minerals": 150, "vespene": 150, "build_time": 37, "ground_range": 4,  "air_range": 0, "dps_ground": 24.4, "dps_air": 0,    "speed": 5.6,  "sight": 10, "is_air": True,  "role": "harass"},
    "Carrier":    {"hp": 300, "shield": 150,"armor":2, "supply": 6, "minerals": 350, "vespene": 250, "build_time": 64, "ground_range": 8,  "air_range": 8, "dps_ground": 37.4, "dps_air": 37.4, "speed": 2.62, "sight": 12, "is_air": True,  "role": "heavy_air"},
    "Tempest":    {"hp": 300, "shield": 150,"armor":2, "supply": 5, "minerals": 250, "vespene": 175, "build_time": 43, "ground_range": 8,  "air_range":14, "dps_ground": 16.97,"dps_air": 22,   "speed": 3.15, "sight": 12, "is_air": True,  "role": "siege_air"},
    "Mothership": {"hp": 350, "shield": 350,"armor":2, "supply": 8, "minerals": 400, "vespene": 400, "build_time": 114,"ground_range": 7,  "air_range": 7, "dps_ground": 22.8, "dps_air": 22.8, "speed": 2.62, "sight": 12, "is_air": True,  "role": "heavy_air"},
    # ── Zerg ──────────────────────────────────────────────────────
    "Drone":      {"hp": 40,  "shield": 0, "armor": 0, "supply": 1, "minerals": 50,  "vespene": 0,   "build_time": 12, "ground_range": 0,  "air_range": 0, "dps_ground": 4.67, "dps_air": 0,    "speed": 3.94, "sight": 8,  "is_air": False, "role": "worker"},
    "Overlord":   {"hp": 200, "shield": 0, "armor": 0, "supply": 0, "minerals": 100, "vespene": 0,   "build_time": 18, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 0.902,"sight": 11, "is_air": True,  "role": "supply"},
    "Queen":      {"hp": 175, "shield": 0, "armor": 1, "supply": 2, "minerals": 150, "vespene": 0,   "build_time": 36, "ground_range": 5,  "air_range": 7, "dps_ground": 11.2, "dps_air": 12.6, "speed": 1.31, "sight": 9,  "is_air": False, "role": "caster"},
    "Zergling":   {"hp": 35,  "shield": 0, "armor": 0, "supply": 0.5,"minerals": 25, "vespene": 0,   "build_time": 17, "ground_range": 0,  "air_range": 0, "dps_ground": 10,   "dps_air": 0,    "speed": 4.13, "sight": 8,  "is_air": False, "role": "infantry"},
    "Baneling":   {"hp": 30,  "shield": 0, "armor": 0, "supply": 0.5,"minerals": 25, "vespene": 25,  "build_time": 14, "ground_range": 0,  "air_range": 0, "dps_ground": 20,   "dps_air": 0,    "speed": 3.5,  "sight": 8,  "is_air": False, "role": "suicide"},
    "Roach":      {"hp": 145, "shield": 0, "armor": 1, "supply": 2, "minerals": 75,  "vespene": 25,  "build_time": 19, "ground_range": 4,  "air_range": 0, "dps_ground": 11.2, "dps_air": 0,    "speed": 3.15, "sight": 9,  "is_air": False, "role": "ranged"},
    "Ravager":    {"hp": 120, "shield": 0, "armor": 1, "supply": 3, "minerals": 25,  "vespene": 75,  "build_time": 9,  "ground_range": 6,  "air_range": 0, "dps_ground": 14,   "dps_air": 0,    "speed": 3.85, "sight": 9,  "is_air": False, "role": "ranged"},
    "Hydralisk":  {"hp": 90,  "shield": 0, "armor": 0, "supply": 2, "minerals": 100, "vespene": 50,  "build_time": 24, "ground_range": 5,  "air_range": 5, "dps_ground": 20.4, "dps_air": 20.4, "speed": 3.15, "sight": 9,  "is_air": False, "role": "ranged"},
    "Lurker":     {"hp": 200, "shield": 0, "armor": 1, "supply": 3, "minerals": 50,  "vespene": 100, "build_time": 18, "ground_range": 8,  "air_range": 0, "dps_ground": 14,   "dps_air": 0,    "speed": 4.13, "sight": 10, "is_air": False, "role": "siege"},
    "LurkerBurrowed": {"hp": 200, "shield": 0, "armor": 1, "supply": 3, "minerals": 50, "vespene": 100, "build_time": 18, "ground_range": 8, "air_range": 0, "dps_ground": 14, "dps_air": 0, "speed": 0, "sight": 10, "is_air": False, "role": "siege"},
    "Infestor":   {"hp": 90,  "shield": 0, "armor": 0, "supply": 2, "minerals": 100, "vespene": 150, "build_time": 36, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 3.15, "sight": 10, "is_air": False, "role": "caster"},
    "SwarmHost":  {"hp": 160, "shield": 0, "armor": 1, "supply": 3, "minerals": 100, "vespene": 75,  "build_time": 29, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 3.15, "sight": 10, "is_air": False, "role": "siege"},
    "Ultralisk":  {"hp": 500, "shield": 0, "armor": 2, "supply": 6, "minerals": 275, "vespene": 200, "build_time": 39, "ground_range": 1,  "air_range": 0, "dps_ground": 57.4, "dps_air": 0,    "speed": 4.13, "sight": 9,  "is_air": False, "role": "heavy"},
    "Mutalisk":   {"hp": 120, "shield": 0, "armor": 0, "supply": 2, "minerals": 100, "vespene": 100, "build_time": 24, "ground_range": 3,  "air_range": 3, "dps_ground": 8.9,  "dps_air": 8.9,  "speed": 5.6,  "sight": 11, "is_air": True,  "role": "harass"},
    "Corruptor":  {"hp": 200, "shield": 0, "armor": 2, "supply": 2, "minerals": 150, "vespene": 100, "build_time": 29, "ground_range": 0,  "air_range": 6, "dps_ground": 0,    "dps_air": 10.3, "speed": 4.725,"sight": 10, "is_air": True,  "role": "anti_air"},
    "BroodLord":  {"hp": 225, "shield": 0, "armor": 1, "supply": 4, "minerals": 300, "vespene": 250, "build_time": 24, "ground_range": 10, "air_range": 0, "dps_ground": 14.3, "dps_air": 0,    "speed": 1.97, "sight": 12, "is_air": True,  "role": "siege_air"},
    "Viper":      {"hp": 150, "shield": 0, "armor": 1, "supply": 3, "minerals": 100, "vespene": 200, "build_time": 29, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 4.13, "sight": 10, "is_air": True,  "role": "caster"},
    "Overseer":   {"hp": 200, "shield": 0, "armor": 1, "supply": 0, "minerals": 50,  "vespene": 50,  "build_time": 12, "ground_range": 0,  "air_range": 0, "dps_ground": 0,    "dps_air": 0,    "speed": 2.62, "sight": 11, "is_air": True,  "role": "detector"},
}

DEFAULT_STAT = {
    "hp": 100, "shield": 0, "armor": 0, "supply": 2,
    "minerals": 100, "vespene": 0, "build_time": 30,
    "ground_range": 0, "air_range": 0, "dps_ground": 0, "dps_air": 0,
    "speed": 3.0, "sight": 9, "is_air": False, "role": "unknown",
}


def get_unit_stat(unit_name: str) -> dict:
    """Look up unit stats by name (case-insensitive). Returns default if unknown."""
    if not unit_name:
        return dict(DEFAULT_STAT, name="")
    stat = UNIT_STATS.get(unit_name)
    if stat is None:
        for k, v in UNIT_STATS.items():
            if k.lower() == unit_name.lower():
                stat = v
                break
    if stat is None:
        return dict(DEFAULT_STAT, name=unit_name, unknown=True)
    return dict(stat, name=unit_name)


def resource_value(stat: dict) -> int:
    """Resource-equivalent value = minerals + 1.5 * vespene."""
    return int(stat.get("minerals", 0) + 1.5 * stat.get("vespene", 0))
