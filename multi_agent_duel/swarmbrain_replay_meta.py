"""Static SC2 replay header/details parsing via mpyq + s2protocol."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# Faster/Normal SC2: 16 * 1.4 = 22.4 game loops per second
LOOPS_PER_SECOND = 22.4

_RESULT_MAP = {
    0: "Undecided",
    1: "Win",
    2: "Loss",
    3: "Tie",
}


def loops_to_seconds(game_loop: int | float | None) -> float | None:
    if game_loop is None:
        return None
    try:
        return float(game_loop) / LOOPS_PER_SECOND
    except Exception:
        return None


def format_game_time(game_time_sec: float | None) -> str | None:
    if game_time_sec is None:
        return None
    try:
        total = max(0, int(float(game_time_sec)))
        mm, ss = divmod(total, 60)
        hh, mm = divmod(mm, 60)
        if hh:
            return f"{hh:02d}:{mm:02d}:{ss:02d}"
        return f"{mm:02d}:{ss:02d}"
    except Exception:
        return None


def _decode_bytes(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for enc in ("utf-8", "latin-1"):
            try:
                return value.decode(enc)
            except Exception:
                continue
        return value.hex().upper()
    return str(value)


def _hex_blob(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict) and "m_data" in value:
        value = value["m_data"]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex().upper()
    if isinstance(value, str):
        return value
    return None


def parse_replay_file(path: str | Path) -> dict[str, Any]:
    """
    Parse replay header/details.

    Returns dict with parse_status ok|failed and metadata fields used by run logger.
    """
    path = Path(path)
    out: dict[str, Any] = {
        "parse_status": "failed",
        "parse_error": None,
        "path": str(path.resolve()) if path.exists() else str(path),
    }
    try:
        import mpyq
        from s2protocol import versions
    except Exception as exc:
        out["parse_error"] = f"import_error:{type(exc).__name__}"
        return out

    try:
        if not path.is_file() or path.stat().st_size <= 0:
            out["parse_error"] = "file_missing_or_empty"
            return out

        archive = mpyq.MPQArchive(str(path))
        contents = archive.header["user_data_header"]["content"]
        header = versions.latest().decode_replay_header(contents)
        base_build = None
        version = header.get("m_version") or {}
        if isinstance(version, dict):
            base_build = version.get("m_baseBuild") or version.get("m_build")
        try:
            protocol = versions.build(int(base_build)) if base_build else versions.latest()
        except Exception:
            protocol = versions.latest()

        # Re-decode with matched protocol when possible
        try:
            header = protocol.decode_replay_header(contents)
            version = header.get("m_version") or version
            if isinstance(version, dict):
                base_build = version.get("m_baseBuild") or version.get("m_build") or base_build
        except Exception:
            pass

        details = {}
        try:
            details_raw = archive.read_file("replay.details")
            if details_raw:
                details = protocol.decode_replay_details(details_raw) or {}
        except Exception as exc:
            details = {"_details_error": type(exc).__name__}

        elapsed = header.get("m_elapsedGameLoops")
        try:
            elapsed_i = int(elapsed) if elapsed is not None else None
        except Exception:
            elapsed_i = None

        data_version = _hex_blob(header.get("m_ngdpRootKey"))
        major = minor = revision = build = None
        if isinstance(version, dict):
            major = version.get("m_major")
            minor = version.get("m_minor")
            revision = version.get("m_revision")
            build = version.get("m_build")
            base_build = version.get("m_baseBuild") or base_build

        sc2_version = None
        if None not in (major, minor, revision, build):
            sc2_version = f"{major}.{minor}.{revision}.{build}"

        players = []
        for p in details.get("m_playerList") or []:
            result_code = p.get("m_result")
            players.append(
                {
                    "name": _decode_bytes(p.get("m_name")),
                    "race": _decode_bytes(p.get("m_race")),
                    "result_code": result_code,
                    "result": _RESULT_MAP.get(result_code, str(result_code)),
                    "team_id": p.get("m_teamId"),
                }
            )

        map_name = _decode_bytes(details.get("m_title"))
        duration = loops_to_seconds(elapsed_i)

        out.update(
            {
                "parse_status": "ok",
                "parse_error": None,
                "base_build": int(base_build) if base_build is not None else None,
                "data_build_num": header.get("m_dataBuildNum"),
                "data_version": data_version,
                "sc2_version": sc2_version,
                "replay_final_game_loop": elapsed_i,
                "replay_duration_sec": duration,
                "map_name_replay": map_name,
                "players": players,
                "player_races": [x.get("race") for x in players],
                "player_results": {
                    f"player_{i+1}": x.get("result") for i, x in enumerate(players)
                },
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
        return out
    except Exception as exc:
        out["parse_status"] = "failed"
        out["parse_error"] = f"{type(exc).__name__}:{str(exc)[:300]}"
        return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
