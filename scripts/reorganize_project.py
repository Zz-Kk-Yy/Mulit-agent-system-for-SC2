"""Project hygiene helper (modes are self-contained; root no longer holds shared runtime copies).

Safe to re-run. Does not touch .env / videos / dist.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "single_agent_pve"
MULTI = ROOT / "multi_agent_duel"

# Runtime files that must NOT live at repo root (belong in mode packages).
FORBIDDEN_ROOT_FILES = [
    "ask_api_server.py",
    "ask_quick.ps1",
    "dashboard_state.py",
    "dashboard_to_redis_bridge.py",
    "docker-compose.yml",
    "feature_map_exporter.py",
    "launch_ask_stack.ps1",
    "launch_support_stack.py",
    "run_sc2_deerflow_pipeline.py",
    "run_swarmbrain.example.ps1",
    "sc2_agents.py",
    "sc2_redis_tools.py",
    "serve_dashboard.py",
    "swarmbrain.py",
    "swarmbrain_launch.py",
    "swarmbrain_llm_shared.py",
    "test_ask_api.ps1",
    "duel_runtime.py",
]
FORBIDDEN_ROOT_DIRS = ["dashboard", "deerflow", "game-view", "mcp_server"]


def main() -> None:
    missing = []
    for name in ("swarmbrain_launch.py", "dashboard_state.py", "serve_dashboard.py"):
        if not (SINGLE / name).is_file():
            missing.append(f"single_agent_pve/{name}")
        if not (MULTI / name).is_file():
            missing.append(f"multi_agent_duel/{name}")
    for name in ("duel_runtime.py", "sc2_redis_keys.py", "run_dual_deerflow_pipeline.py"):
        if not (MULTI / name).is_file():
            missing.append(f"multi_agent_duel/{name}")

    leaked = [n for n in FORBIDDEN_ROOT_FILES if (ROOT / n).exists()]
    leaked += [n for n in FORBIDDEN_ROOT_DIRS if (ROOT / n).exists()]

    if missing:
        print("ERROR missing mode files:")
        for m in missing:
            print(f"  - {m}")
        raise SystemExit(1)
    if leaked:
        print("ERROR root still has mode-runtime duplicates:")
        for m in leaked:
            print(f"  - {m}")
        raise SystemExit(1)

    print("OK: root is hub-only; both mode packages look present.")
    print(f"  single={SINGLE}")
    print(f"  multi={MULTI}")


if __name__ == "__main__":
    main()
