"""
DreamMoon-SC2 MCP Server — StarCraft II game interface (stdio/SSE).

Implementations: project-root `sc2_redis_tools.py`.

Usage:
    python mcp_server/sc2_mcp_server.py
    python mcp_server/sc2_mcp_server.py --sse
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("sc2-mcp")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    logger.error("mcp package not installed. Run: pip install mcp>=1.0.0")
    sys.exit(1)

from sc2_redis_tools import (  # noqa: E402
    sc2_analysis_cache_get as _analysis_cache_get,
    sc2_analysis_cache_set as _analysis_cache_set,
    sc2_get_game_status as _get_game_status,
    sc2_get_layer as _get_layer,
    sc2_get_situation as _get_situation,
    sc2_get_trend as _get_trend,
    sc2_replay_list as _replay_list,
    sc2_replay_load as _replay_load,
    sc2_replay_seek as _replay_seek,
    sc2_replay_status as _replay_status,
    sc2_replay_timeline as _replay_timeline,
    sc2_send_commands as _send_commands,
    sc2_unit_capabilities as _unit_capabilities,
)

mcp = FastMCP(
    "sc2-game",
    instructions=(
        "StarCraft II game interface: read real-time situation data "
        "(unit/economy/map/vision layers), send battle commands, and "
        "control game state via Redis bridge to burnysc2 bot."
    ),
)


@mcp.tool()
async def sc2_get_situation() -> str:
    """Read the complete current SC2 situation snapshot (JSON layers: raw/unit/economy/map/vision)."""
    return await _get_situation()


@mcp.tool()
async def sc2_get_layer(layer_name: str) -> str:
    """Read one layer: unit | economy | map | vision."""
    return await _get_layer(layer_name)


@mcp.tool()
async def sc2_send_commands(commands: list[dict]) -> str:
    """Queue combat/build commands for the bot (max 50)."""
    return await _send_commands(commands)


@mcp.tool()
async def sc2_get_game_status() -> str:
    """Game status: idle|running|finished, pause, speed, bot_running."""
    return await _get_game_status()


@mcp.tool()
async def sc2_unit_capabilities(unit_names: list[str] | None = None) -> str:
    """Static HP/DPS/cost stats; omit unit_names for the full catalog."""
    return await _unit_capabilities(unit_names)


@mcp.tool()
async def sc2_analysis_cache_get(thread_id: str, agent_name: str | None = None) -> str:
    """Read staged analysis JSON for thread_id (all agents if agent_name omitted)."""
    return await _analysis_cache_get(thread_id, agent_name)


@mcp.tool()
async def sc2_analysis_cache_set(thread_id: str, agent_name: str, payload: str) -> str:
    """Write one agent's JSON artifact (TTL ~1h)."""
    return await _analysis_cache_set(thread_id, agent_name, payload)


@mcp.tool()
async def sc2_get_trend() -> str:
    """Approximate supply/army deltas vs previous anchored snapshot."""
    return await _get_trend()


@mcp.tool()
async def sc2_replay_list() -> str:
    """List cached replays."""
    return await _replay_list()


@mcp.tool()
async def sc2_replay_load(replay_id: str, observed_player: int = 1) -> str:
    """Post replay load request to Redis."""
    return await _replay_load(replay_id, observed_player)


@mcp.tool()
async def sc2_replay_seek(game_loop: int) -> str:
    """Seek replay to game_loop."""
    return await _replay_seek(game_loop)


@mcp.tool()
async def sc2_replay_timeline() -> str:
    """Replay keyframes."""
    return await _replay_timeline()


@mcp.tool()
async def sc2_replay_status() -> str:
    """Replay load/playback state."""
    return await _replay_status()


if __name__ == "__main__":
    if "--sse" in sys.argv:
        logger.info("Starting MCP server in SSE mode on port 8765...")
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = 8765
        mcp.run(transport="sse")
    else:
        logger.info("Starting MCP server in stdio mode...")
        mcp.run(transport="stdio")
