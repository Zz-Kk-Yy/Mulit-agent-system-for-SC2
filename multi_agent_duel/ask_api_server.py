from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
_rp = str(ROOT_DIR)
if _rp not in sys.path:
    sys.path.insert(0, _rp)

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class Utf8JSONResponse(JSONResponse):
    """明确 charset，减轻部分客户端把 UTF-8 正文误读为系统 ANSI 的概率。"""

    media_type = "application/json; charset=utf-8"

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

from swarmbrain_llm_shared import (
    dotenv_file_has_llm_key_line,
    get_swarmbrain_llm_credentials,
    llm_api_key_is_embedded_fallback,
)

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None  # type: ignore

try:
    from dashboard_to_redis_bridge import build_situation as _build_full_situation_from_state
except Exception:  # pragma: no cover
    _build_full_situation_from_state = None  # type: ignore

try:
    from sc2_redis_keys import situation_key as _situation_key
except Exception:  # pragma: no cover
    def _situation_key(side: str | None = None) -> str:  # type: ignore
        value = str(side or "").strip().lower()
        return f"situation:current:{value}" if value in {"red", "blue"} else "situation:current"

STATE_PATH = ROOT_DIR / "dashboard_state.json"
KEY_SITUATION = "situation:current"


def _normalize_side(side: str | None) -> str:
    value = str(side or "").strip().lower()
    return value if value in {"red", "blue"} else ""


def _perspective_labels(side: str | None) -> tuple[str, str]:
    """返回 (己方标签, 敌方标签)，相对当前视角。"""
    if _normalize_side(side) == "blue":
        return "蓝方", "红方"
    return "红方", "蓝方"


def _cot_turn_specs(side: str | None = None) -> tuple[tuple[str, str, str], ...]:
    own, enemy = _perspective_labels(side)
    return (
        ("ours", f"{own}方案规划智能体", f"当前窗 · {own}先手"),
        ("enemy", f"{enemy}行为预测智能体", f"{enemy}下一手"),
        ("reflect", "方案反思纠正智能体", "对照张力 · 收敛"),
    )


def _env_cot_turn_specs(side: str | None = None) -> tuple[tuple[str, str, str], ...]:
    own, enemy = _perspective_labels(side)
    return (
        ("ours", f"{own}兵力布势方案规划智能体", "本轮 · 布势方案"),
        ("enemy", f"{enemy}兵力布势行为预测智能体", "本轮 · 对手布势反应"),
        ("reflect", f"{own}兵力布势方案反思纠正智能体", "本轮 · 对照张力"),
    )


def _is_duel_ask_scope(*, side: str | None = None, scope: str | None = None) -> bool:
    scope_value = str(scope or "").strip().lower()
    if scope_value in {"duel", "both", "all", "compare"}:
        return True
    side_value = str(side or "").strip().lower()
    return side_value in {"duel", "both", "all", "compare"}


def _load_situation_bundle(side: str | None = None) -> tuple[dict[str, Any] | None, str, list[str]]:
    """返回 (situation, source, traces)。"""
    traces: list[str] = []
    normalized = _normalize_side(side)
    situation = _read_situation_from_redis(normalized)
    source = f"redis:{normalized or 'default'}"
    traces.append(f"sc2_get_situation(redis:{normalized or 'default'})")
    if not situation:
        situation = _build_situation_from_dashboard_state(normalized)
        source = f"dashboard_state:{normalized or 'default'}"
        traces.append(f"fallback:dashboard_state:{normalized or 'default'}")
    return situation, source, traces


def _compact_side_situation_text(situation: dict[str, Any], *, side_label: str) -> str:
    unit = situation.get("unit", {}) or {}
    economy = situation.get("economy", {}) or {}
    map_layer = situation.get("map", {}) or {}
    vision = situation.get("vision", {}) or {}
    raw = situation.get("raw", {}) or {}
    return (
        f"【{side_label}态势】"
        f"时间={raw.get('time_formatted', '--')}, 回合={raw.get('game_loop', '--')}; "
        f"经济: 矿={economy.get('minerals', 0)} 气={economy.get('vespene', 0)} "
        f"补给={economy.get('supply_used', 0)}/{economy.get('supply_cap', 0)} 工人={economy.get('workers', 0)}; "
        f"兵力: 己方总军力={unit.get('army_count', 0)} "
        f"可见敌方单位={vision.get('enemy_units_visible', 0)} "
        f"可见敌方建筑={vision.get('enemy_structures_visible', 0)}; "
        f"地图={map_layer.get('map_name', '')} 开局={map_layer.get('start_location', '')} "
        f"阶段={map_layer.get('game_stage', '')}; "
        f"己方构成Top={json.dumps((unit.get('own_unit_composition') or [])[:6], ensure_ascii=False)}; "
        f"可见敌方构成Top={json.dumps((unit.get('enemy_visible_composition') or [])[:6], ensure_ascii=False)}"
    )


def _compact_duel_situation_text(
    red_situation: dict[str, Any] | None,
    blue_situation: dict[str, Any] | None,
) -> str:
    parts: list[str] = [
        "这是红蓝自博弈对局。下列分别为红方、蓝方各自视角的实时态势："
        "各方「己方」=该阵营单位，「可见敌方」=该阵营当前视野内的对手单位。"
    ]
    if red_situation:
        parts.append(_compact_side_situation_text(red_situation, side_label="红方"))
    else:
        parts.append("【红方态势】暂不可用")
    if blue_situation:
        parts.append(_compact_side_situation_text(blue_situation, side_label="蓝方"))
    else:
        parts.append("【蓝方态势】暂不可用")
    return "\n".join(parts)

def _dashboard_state_path_for_side(side: str | None = None) -> Path:
    normalized = _normalize_side(side)
    if normalized:
        return ROOT_DIR / f"dashboard_state_{normalized}.json"
    # 双智能体目录优先用红方文件兜底，避免读不到已废弃的 dashboard_state.json
    red = ROOT_DIR / "dashboard_state_red.json"
    if red.exists():
        return red
    return STATE_PATH


def _resolve_llm_api_key() -> str:
    """与 `swarmbrain.build_model` 同源：swarmbrain_llm_shared（环境变量 / .env / 与对局一致的内嵌末位兜底）。"""
    return str(get_swarmbrain_llm_credentials()["api_key"]).strip()


def _build_chat_openai(
    *,
    temperature: float | None = None,
    max_tokens: int = 4096,
    timeout: float = 40,
) -> Any:
    """统一构造对话模型：关 Qwen 思考链、补 max_tokens、给 HTTP 超时，避免空回复和整批挂死。"""
    if ChatOpenAI is None:
        raise RuntimeError("langchain_openai_missing")
    cred = get_swarmbrain_llm_credentials()
    model_name = str(cred["model"] or "")
    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": str(cred["api_key"]).strip(),
        "base_url": str(cred["base_url"]),
        "temperature": float(cred["temperature"] if temperature is None else temperature),
        "max_tokens": int(max_tokens),
        "timeout": float(timeout),
    }
    if "qwen" in model_name.lower():
        kwargs["extra_body"] = {"enable_thinking": False}
    return ChatOpenAI(**kwargs)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(default="default")
    side: str = Field(
        default="",
        description="red|blue 单侧；duel|both|all 表示红蓝自博弈双侧态势",
    )
    scope: str = Field(
        default="",
        description="side|duel；优先于 side 判定。duel=基于红蓝双方态势问答",
    )


class AskResponse(BaseModel):
    answer: str
    tool_traces: list[str]
    source: str


class DeerflowRiskRequest(BaseModel):
    replay: bool = True
    timeout_sec: int = Field(default=90, ge=15, le=600)
    thread_id: str = Field(default="")
    side: str = Field(default="", description="red|blue，双智能体侧别")
    mode: str = Field(
        default="fast",
        description="fast=单次大模型直接产出意图+风险（默认，快）；pipeline=完整 DeerFlow 六阶段（慢）",
    )


class DeerflowRiskResponse(BaseModel):
    ok: bool
    thread_id: str
    mode: str
    summary: str
    intent: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    stages: dict[str, Any] = Field(default_factory=dict)
    traces: list[str] = Field(default_factory=list)
    pipeline_stdout: str = ""


class ForceLlmAdviceRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=15, le=180)
    side: str = Field(default="", description="red|blue，双智能体侧别")


class ForceAdviceSection(BaseModel):
    key: str
    title: str
    summary: str
    detail: str


class ForceLlmAdviceResponse(BaseModel):
    ok: bool
    sections: list[ForceAdviceSection] = Field(default_factory=list)
    answer_raw: str = ""
    source: str = ""
    traces: list[str] = Field(default_factory=list)
    error: str = ""


class OrientUnderstandRequest(BaseModel):
    timeout_sec: int = Field(default=45, ge=15, le=120)
    side: str = Field(default="", description="red|blue，双智能体侧别")
    dimension: str = Field(default="strike", description="strike|detect|defense|support")
    question: str = Field(default="", max_length=2000)
    history: list[dict[str, Any]] = Field(default_factory=list)
    context: str = Field(default="", max_length=12000)


class OrientUnderstandStep(BaseModel):
    key: str
    title: str
    text: str = ""
    evidence: str = ""


class OrientUnderstandResponse(BaseModel):
    ok: bool
    answer: str = ""
    kind: str = "briefing"
    dimension: str = "strike"
    source: str = ""
    traces: list[str] = Field(default_factory=list)
    error: str = ""
    steps: list[OrientUnderstandStep] = Field(default_factory=list)
    conclusion: str = ""


class StrikeTargetsAdviceRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=15, le=180)
    side: str = Field(default="", description="red|blue，双智能体侧别")
    targets: list[dict[str, Any]] = Field(
        default_factory=list,
        description="仪表盘「关键打击节点」序列化条目，可为空则仅凭态势推断。",
    )


class StrikeTargetsAdviceResponse(BaseModel):
    ok: bool
    advice: str = ""
    source: str = ""
    traces: list[str] = Field(default_factory=list)
    error: str = ""


class KillChainPlansRequest(BaseModel):
    timeout_sec: int = Field(default=120, ge=20, le=240)
    side: str = Field(default="", description="red|blue，双智能体侧别")
    chains: list[dict[str, Any]] = Field(
        default_factory=list,
        description="前端从敌方可见对象归纳出的敌方杀伤链候选池。",
    )


class KillChainPlanStep(BaseModel):
    order: int
    chainKey: str
    dimension: str
    title: str
    targetLocation: str = "--"
    rationale: str = ""


class KillChainPlanTask(BaseModel):
    taskKey: str
    chainKey: str
    dimension: str
    title: str
    summary: str = ""
    status: str = "待激活"
    confidence: float = 0
    executability: float = 0
    rankScore: float = 0
    targetLocation: str = "--"


class KillChainPlan(BaseModel):
    key: str
    rank: int
    title: str
    description: str = ""
    planScore: float = 0
    chainKeys: list[str] = Field(default_factory=list)
    tasks: list[KillChainPlanTask] = Field(default_factory=list)
    enemyStrikeFlow: list[KillChainPlanStep] = Field(default_factory=list)


class KillChainPlansResponse(BaseModel):
    ok: bool
    plans: list[KillChainPlan] = Field(default_factory=list)
    answer_raw: str = ""
    source: str = ""
    traces: list[str] = Field(default_factory=list)
    error: str = ""


class CotDimBlock(BaseModel):
    summary: str = ""
    detail: str = ""
    thoughts: list[str] = Field(default_factory=list)


class CotTurn(BaseModel):
    key: str
    role: str
    title: str
    subtitle: str = ""
    dims: dict[str, CotDimBlock] = Field(default_factory=dict)
    conclusion: str = ""


class CotFinalPlan(BaseModel):
    title: str = "红方应对"
    subtitle: str = ""
    dims: dict[str, str] = Field(default_factory=dict)
    steps: list[str] = Field(default_factory=list)
    conclusion: str = ""


class CotSelfplayLoop(BaseModel):
    title: str = ""
    horizon: str = ""
    thesis: str = ""
    situationDigest: str = ""
    turns: list[CotTurn] = Field(default_factory=list)
    finalPlan: CotFinalPlan | None = None


class CotSelfplayRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=20, le=180)
    side: str = Field(default="red", description="red|blue")
    game_time: str = Field(default="", description="当前比赛时钟，如 05:40")
    previous_conclusion: str = Field(default="", description="上一轮收敛结论，供本轮承接")


class CotSelfplayResponse(BaseModel):
    ok: bool
    loop: CotSelfplayLoop | None = None
    game_time: str = ""
    answer_raw: str = ""
    source: str = ""
    traces: list[str] = Field(default_factory=list)
    error: str = ""


class CotKillnetRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=20, le=180)
    side: str = Field(default="red", description="red|blue")
    game_time: str = Field(default="", description="当前比赛时钟")
    situation_digest: str = ""
    final_plan: dict[str, Any] = Field(default_factory=dict, description="本轮红方应对")
    environment_plan: dict[str, Any] = Field(
        default_factory=dict,
        description="作战环境收敛方案（summary/steps/conclusion），写入杀伤网中心节点",
    )


class CotKillnetPayload(BaseModel):
    thesis: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    sequence: list[dict[str, Any]] = Field(default_factory=list)
    patternLabel: str = ""


class CotKillnetResponse(BaseModel):
    ok: bool
    killnet: CotKillnetPayload | None = None
    game_time: str = ""
    answer_raw: str = ""
    source: str = ""
    traces: list[str] = Field(default_factory=list)
    error: str = ""


class CotEnvTurn(BaseModel):
    """兵力布势自博弈单轮：扁平 summary/detail，无四维 dims。"""

    key: str
    role: str
    title: str
    subtitle: str = ""
    summary: str = ""
    detail: str = ""
    conclusion: str = ""


class CotEnvFinalPlan(BaseModel):
    title: str = "红方方案总结"
    subtitle: str = ""
    summary: str = ""
    steps: list[str] = Field(default_factory=list)
    conclusion: str = ""


class CotEnvSelfplayLoop(BaseModel):
    title: str = ""
    horizon: str = ""
    thesis: str = ""
    situationDigest: str = ""
    turns: list[CotEnvTurn] = Field(default_factory=list)
    finalPlan: CotEnvFinalPlan | None = None


class CotEnvSelfplayRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=20, le=180)
    side: str = Field(default="red", description="red|blue")
    game_time: str = Field(default="", description="当前比赛时钟，如 05:40")
    previous_conclusion: str = Field(default="", description="上一轮兵力布势结论，供本轮承接")


class CotEnvSelfplayResponse(BaseModel):
    ok: bool
    loop: CotEnvSelfplayLoop | None = None
    game_time: str = ""
    answer_raw: str = ""
    source: str = ""
    traces: list[str] = Field(default_factory=list)
    error: str = ""


def _redis_client() -> redis.Redis:
    kwargs: dict[str, Any] = {
        "host": os.getenv("REDIS_HOST", "127.0.0.1"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "db": int(os.getenv("REDIS_DB", "0")),
        "decode_responses": True,
    }
    password = os.getenv("REDIS_PASSWORD", "")
    if password:
        kwargs["password"] = password
    return redis.Redis(**kwargs)


def _run_deerflow_pipeline(
    *,
    thread_id: str,
    replay: bool,
    timeout_sec: int,
    side: str = "",
) -> tuple[bool, str, list[str]]:
    cmd = [sys.executable, "run_sc2_deerflow_pipeline.py", "--thread-id", thread_id]
    if replay:
        cmd.append("--replay")
    normalized = _normalize_side(side)
    if normalized:
        cmd.extend(["--side", normalized])
    traces = [f"pipeline:cmd={' '.join(cmd)}", f"pipeline:timeout={timeout_sec}s"]
    env = os.environ.copy()
    if normalized:
        env["SC2_AGENT_SIDE"] = normalized
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        return False, f"pipeline timeout after {timeout_sec}s", traces + [f"pipeline:timeout:{e}"]
    except Exception as e:
        return False, f"pipeline launch failed: {e}", traces + [f"pipeline:error:{e}"]

    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    traces.append(f"pipeline:exit_code={proc.returncode}")
    return proc.returncode == 0, out.strip(), traces


def _read_analysis_json(r: redis.Redis, thread_id: str, agent_name: str) -> dict[str, Any]:
    raw = r.get(f"sc2:analysis:{thread_id}:{agent_name}")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _build_risk_summary(intent: dict[str, Any], risk_id: dict[str, Any]) -> str:
    strategy = str(intent.get("primary_strategy") or "unknown")
    conf = float(intent.get("confidence") or 0.0)
    overall = str(risk_id.get("overall_risk_level") or "unknown")
    top = (risk_id.get("risks") or [{}])[0] if isinstance(risk_id.get("risks"), list) and risk_id.get("risks") else {}
    target = str(top.get("target") or "n/a")
    eta = top.get("time_to_impact_sec")
    eta_text = f"{eta}s" if isinstance(eta, int) else "--"
    return f"敌方意图={strategy}(置信{conf:.2f})；整体风险={overall}；最高风险目标={target}，预计影响时间={eta_text}。"


_STRATEGY_CN = {
    "rush": "速攻",
    "timing_attack": "时机进攻",
    "macro": "运营发育",
    "harassment": "骚扰牵制",
    "all_in": "一波梭哈",
    "tech_switch": "科技转型",
    "defensive": "防守反击",
    "unknown": "未知",
}
_RISK_LEVEL_CN = {
    "critical": "极高",
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
}
_THREAT_TYPE_CN = {
    "army_push": "正面推进",
    "harass": "骚扰牵制",
    "tech": "科技威胁",
    "economy_denial": "经济压制",
    "air_raid": "空袭威胁",
    "unknown": "未知",
}
_DIM_CN = {
    "attack": "进攻意图",
    "defense": "防守意图",
    "detect": "探测意图",
    "support": "保障意图",
}


def _normalize_intent_cn(intent: dict[str, Any]) -> dict[str, Any]:
    if not intent:
        return {}
    out = dict(intent)
    key = str(out.get("primary_strategy") or "unknown")
    out["primary_strategy_cn"] = _STRATEGY_CN.get(key, key)
    dims = out.get("intent_dimensions")
    if not isinstance(dims, dict):
        dims = {}
    ebd = out.get("evidence_by_dimension")
    if not isinstance(ebd, dict):
        ebd = {}
    normalized_dims: dict[str, Any] = {}
    normalized_ebd: dict[str, Any] = {}
    for k in ("attack", "defense", "detect", "support"):
        meta = dims.get(k) if isinstance(dims.get(k), dict) else {}
        desc = str(meta.get("description_cn") or meta.get("description") or "").strip()
        if not desc:
            desc = f"{_DIM_CN[k]}：证据不足，维持持续观察。"
        normalized_dims[k] = {
            "label_cn": _DIM_CN[k],
            "description_cn": desc,
            "level": str(meta.get("level") or "medium"),
        }
        raw_items = ebd.get(k) if isinstance(ebd.get(k), list) else []
        normalized_ebd[k] = [str(x) for x in raw_items if str(x).strip()]
    out["intent_dimensions"] = normalized_dims
    out["evidence_by_dimension"] = normalized_ebd
    return out


def _normalize_risk_cn(risk_id: dict[str, Any]) -> dict[str, Any]:
    if not risk_id:
        return {}
    out = dict(risk_id)
    lv = str(out.get("overall_risk_level") or "unknown")
    out["overall_risk_level_cn"] = _RISK_LEVEL_CN.get(lv, lv)
    risks = out.get("risks") or []
    if isinstance(risks, list):
        nr = []
        for item in risks:
            if not isinstance(item, dict):
                continue
            x = dict(item)
            t = str(x.get("threat_type") or "unknown")
            x["threat_type_cn"] = _THREAT_TYPE_CN.get(t, t)
            nr.append(x)
        out["risks"] = nr
    return out


def _build_risk_summary_cn(intent: dict[str, Any], risk_id: dict[str, Any]) -> str:
    strategy = str(intent.get("primary_strategy_cn") or intent.get("primary_strategy") or "未知")
    conf = float(intent.get("confidence") or 0.0)
    overall = str(risk_id.get("overall_risk_level_cn") or risk_id.get("overall_risk_level") or "未知")
    top = (risk_id.get("risks") or [{}])[0] if isinstance(risk_id.get("risks"), list) and risk_id.get("risks") else {}
    target = str(top.get("target") or "无")
    eta = top.get("time_to_impact_sec")
    eta_text = f"{eta}秒" if isinstance(eta, int) else "--"
    return f"敌方意图={strategy}（置信度 {conf:.2f}）；整体风险={overall}；最高风险目标={target}，预计影响时间={eta_text}。"


def _read_situation_from_redis(side: str | None = None) -> dict[str, Any] | None:
    try:
        r = _redis_client()
        key = _situation_key(_normalize_side(side) or None)
        raw = r.get(key)
        if not raw and _normalize_side(side):
            raw = r.get(KEY_SITUATION)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _build_situation_from_dashboard_state(side: str | None = None) -> dict[str, Any] | None:
    state_path = _dashboard_state_path_for_side(side)
    try:
        if not state_path.exists():
            return None
        with state_path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return None

    # 与 bridge 同一套 structure，便于 enemy_units / 区域 等字段存在
    if _build_full_situation_from_state is not None:
        try:
            return _build_full_situation_from_state(state)
        except Exception:
            pass

    game = state.get("game") or {}
    return {
        "raw": {
            "game_loop": state.get("latestFeatureFrame", {}).get("game_loop", game.get("iteration", 0)),
            "time_formatted": game.get("timeFormatted", ""),
            "updated_at": state.get("updatedAt", ""),
        },
        "unit": {
            "own_unit_composition": game.get("ownUnitComposition") or [],
            "enemy_visible_composition": game.get("enemyVisibleComposition") or [],
            "enemy_units": [],
            "army_count": game.get("armyCount", 0),
        },
        "economy": {
            "minerals": game.get("minerals", 0),
            "vespene": game.get("vespene", 0),
            "supply_used": game.get("supplyUsed", 0),
            "supply_cap": game.get("supplyCap", 0),
            "workers": game.get("workerCount", 0),
        },
        "map": {
            "map_name": game.get("mapName", ""),
            "start_location": game.get("startLocation", ""),
            "game_stage": game.get("gameStage", ""),
        },
        "vision": {
            "enemy_units_visible": game.get("enemyUnitsVisible", 0),
            "enemy_structures_visible": game.get("enemyStructuresVisible", 0),
        },
    }


def _compact_situation_text(situation: dict[str, Any]) -> str:
    unit = situation.get("unit", {})
    economy = situation.get("economy", {})
    map_layer = situation.get("map", {})
    vision = situation.get("vision", {})
    raw = situation.get("raw", {})
    return (
        f"时间={raw.get('time_formatted', '--')}, 回合={raw.get('game_loop', '--')}; "
        f"经济: 矿={economy.get('minerals', 0)} 气={economy.get('vespene', 0)} "
        f"补给={economy.get('supply_used', 0)}/{economy.get('supply_cap', 0)} 工人={economy.get('workers', 0)}; "
        f"兵力: 我方总军力={unit.get('army_count', 0)} "
        f"敌方可见单位={vision.get('enemy_units_visible', 0)} 敌方可见建筑={vision.get('enemy_structures_visible', 0)}; "
        f"地图={map_layer.get('map_name', '')} 阶段={map_layer.get('game_stage', '')}; "
        f"我方构成Top={json.dumps((unit.get('own_unit_composition') or [])[:6], ensure_ascii=False)}; "
        f"敌方构成Top={json.dumps((unit.get('enemy_visible_composition') or [])[:6], ensure_ascii=False)}"
    )


def _answer_enemy_main_force_location(situation: dict[str, Any]) -> str:
    unit = situation.get("unit") or {}
    vision = situation.get("vision") or {}
    enemy_units = unit.get("enemy_units") or []
    comp = unit.get("enemy_visible_composition") or []
    n_visible = int(vision.get("enemy_units_visible") or 0)
    n_struct = int(vision.get("enemy_structures_visible") or 0)

    detailed = []
    for e in enemy_units:
        if not isinstance(e, dict):
            continue
        loc = str(e.get("location", "") or "").strip()
        if loc and loc != "--":
            detailed.append(e)

    if detailed:
        from collections import defaultdict

        by_loc: dict[str, list[str]] = defaultdict(list)
        for e in detailed:
            loc = str(e.get("location", "--"))
            t = str(e.get("type", "?"))
            c = int(e.get("count") or 0) or 1
            by_loc[loc].append(f"{t}×{c}")

        lines = []
        for loc in sorted(by_loc.keys()):
            lines.append(f"  • {loc}：{', '.join(by_loc[loc])}")
        body = "\n".join(lines[:12])
        return (
            "针对你的问题「敌方主力 / 在什么位置」：下面是当前**视野内、按地图区域聚合**的可见敌军明细"
            "（无区域时不代表地图上没人，可能只是尚未侦察到）：\n"
            f"{body}\n"
            "说明：主力判断需结合多线视野；若某一区域兵种数量明显多于其他区域，可暂视为对线压力主轴。"
        )

    if comp:
        top = sorted(comp, key=lambda x: -int(x.get("count") or 0))[:6]
        parts = [f"{x.get('name', '?')}×{int(x.get('count') or 0)}" for x in top]
        return (
            "针对你的问题「敌方主力 / 在什么位置」：当前**没有带棋盘格位置的敌军明细**，"
            "只有兵种聚合统计，因此只能说「推断可见构成」，不能说精确坐标。\n"
            f"可见敌方单位计数约 **{n_visible}**，建筑约 **{n_struct}**；构成靠前的类型：**{', '.join(parts)}**。\n"
            "建议：用单位或侦查手段接触前线、开视野后，再问一次即可出现按区域的主力分布。"
        )

    if n_visible == 0 and n_struct == 0:
        return (
            "针对你的问题「敌方主力在哪」：**当前统计显示视野内未发现敌方单位/建筑（均为 0）或数据尚未同步。**\n"
            "这种情况下无法回答敌军主力位置；请先确认对局进行中、仪表盘已写入敌方可见统计，再扩大侦察。"
        )

    return (
        f"针对你的问题「敌方主力在哪」：可见敌军单位计数为 **{n_visible}**、建筑 **{n_struct}**，"
        "但暂时没有拆到兵种条目，无法用构成或区域做进一步判断。\n"
        "请稍等态势刷新后再问，或多接触前线以获得 `enemy_visible_composition / 明细`。"
    )


def _answer_economy_brief(situation: dict[str, Any]) -> str:
    economy = situation.get("economy") or {}
    return (
        "针对你的「经济」类问题：当前快照为——"
        f"晶矿 **{economy.get('minerals', 0)}**，瓦斯 **{economy.get('vespene', 0)}**，"
        f"补给 **{economy.get('supply_used', 0)}/{economy.get('supply_cap', 0)}**，"
        f"工人约 **{economy.get('workers', 0)}**。\n"
        "若要问「够不够扩张/一波」，需再结合敌方压迫度（可见兵量）一起看。"
    )


def _answer_heuristic(question: str, situation: dict[str, Any]) -> str | None:
    q_lower = question.lower().strip()
    q_raw = question.strip()

    en_main = (
        "enemy main" in q_lower
        or "main force" in q_lower
        or "where is the enemy" in q_lower
        or "where are the enemy" in q_lower
        or "enemy army" in q_lower
        or "enemy location" in q_lower
        or "enemy units" in q_lower and ("where" in q_lower or "location" in q_lower)
    )
    zh_main = any(
        x in q_raw
        for x in ("敌方主力", "敌人主力", "敌军主力", "敌人在哪", "敌方在哪", "主力在哪", "敌军在哪")
    )
    if en_main or zh_main:
        return _answer_enemy_main_force_location(situation)

    en_eco = any(
        x in q_lower for x in ("economy", "mineral", "vespene", "gas ", "supply", "workers")
    )
    zh_eco = any(x in q_raw for x in ("经济", "矿", "气", "补给", "农民", "晶矿", "瓦斯"))
    if en_eco or zh_eco:
        return _answer_economy_brief(situation)

    return None


def _answer_with_llm(
    question: str, situation_text: str, *, duel_mode: bool = False
) -> tuple[str | None, str | None]:
    """(回答文本, 未走通 LLM 时的诊断代号)，代号仅写入 tool_traces。"""
    if ChatOpenAI is None:
        return None, "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return None, "llm:none:no_api_key"

    cred = get_swarmbrain_llm_credentials()
    temperature = float(cred["temperature"])
    try:
        llm = _build_chat_openai(temperature=temperature, timeout=45)
        if duel_mode:
            role = (
                "你是星际争霸II红蓝自博弈分析助手。请同时基于红方与蓝方的实时态势回答用户问题，"
                "必要时对比双方经济、兵力、视野与构成差异；输出要简洁、可执行，优先给2-4条建议。"
                "回答中请明确使用「红方/蓝方」称呼，不要把单侧视角误称为全局上帝视角。"
            )
            situation_header = "[红蓝双方实时态势]"
        else:
            role = (
                "你是星际争霸II战术分析助手。请基于实时态势回答用户问题，"
                "输出要简洁、可执行，优先给2-4条建议。"
            )
            situation_header = "[实时态势]"
        prompt = (
            f"{role}\n\n"
            f"{situation_header}\n{situation_text}\n\n"
            f"[用户问题]\n{question}"
        )
        reply = llm.invoke(prompt)
        content = _llm_message_text(reply)
        if content:
            return content, None
        return None, "llm:none:empty_reply"
    except Exception as exc:  # noqa: BLE001
        return None, f"llm:error:{type(exc).__name__}"


def _answer_with_template(
    question: str, situation_text: str, *, llm_was_configured: bool, llm_call_failed: bool
) -> str:
    if llm_was_configured and llm_call_failed:
        return (
            "已配置大模型但未成功生成回答（仍为千问/OpenAI 兼容调用流程）。 "
            '请查看本次响应字段 `tool_traces`（如 `llm:error:*`）核对 Key、配额、模型名与 SWARMBRAIN_LLM_BASE_URL。\n'
            '若在无大模型时仍希望走关键词规则兜底，设置 `SWARMBRAIN_ASK_HEURISTIC=1` 后重启 Ask 服务。\n\n'
            f'你的问题：「{question}」。\n\n'
            f"态势摘要：{situation_text}"
        )
    return (
        f"当前未检测到可用的对话模型 API Key。你的问题是：「{question}」。\n"
        "问答与对局已通过 **`swarmbrain_llm_shared.py`** 共用同一条 Key、模型与 `base_url` 解析逻辑。\n"
        "请检查是否误删该文件的内嵌兜底、或在项目根 **`.env`** 填写 "
        "`SWARMBRAIN_LLM_API_KEY` / `DASHSCOPE_API_KEY`／`OPENAI_API_KEY`，然后重启 `launch_ask_stack`。\n"
        "`GET /health` 可看 `dotenv_has_llm_key_line`、`llm_ready`、`llm_uses_swarmbrain_embedded_key`。\n\n"
        "在无 Key 时的通用建议：\n"
        "1) 对齐补给与兵种曲线，再接敌；\n"
        "2) 敌军建筑多时优先科技与扩张侦察；\n"
        "3) 下一步拆成防守/扩张/压制三段执行。\n\n"
        f"态势摘要：{situation_text}"
    )


def _summarize_units_by_area(units: list[Any], *, limit: int = 28) -> str:
    lines: list[str] = []
    for u in units[:limit]:
        if not isinstance(u, dict):
            continue
        loc = str(u.get("location") or "").strip()
        pos = u.get("position")
        if isinstance(pos, dict):
            loc = loc or str(pos.get("label") or "").strip()
        if not loc:
            loc = "--"
        t = str(u.get("type") or "?")
        c = int(u.get("count") or 0) or 1
        act = str(u.get("action") or "").strip()
        suf = f" · {act}" if act else ""
        lines.append(f"  • {loc}: {t} ×{c}{suf}")
    return "\n".join(lines) if lines else "(无明细)"


def _build_force_advice_context_text(situation: dict[str, Any]) -> str:
    base = _compact_situation_text(situation)
    unit = situation.get("unit") or {}
    chunks = [base]
    ou = unit.get("own_units") or []
    eu = unit.get("enemy_units") or []
    if ou:
        chunks.append("【我方单位按区域(节选)】\n" + _summarize_units_by_area(ou))
    if eu:
        chunks.append("【敌方可见单位按区域(节选)】\n" + _summarize_units_by_area(eu))
    return "\n\n".join(chunks)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from free-form model output.

    LLMs sometimes emit disclaimers, markdown fences, or multiple JSON snippets.
    We attempt to extract the *first decodable* JSON object and prefer one with
    the expected "sections" shape when present.
    """

    s = (text or "").strip()
    if not s:
        return None

    # Strip a single markdown code fence if present.
    if s.startswith("```"):
        s = re.sub(r"^```\w*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s).strip()
        if not s:
            return None

    decoder = json.JSONDecoder()

    def _strip_trailing_non_json(x: str) -> str:
        # Remove common trailing junk (quotes / punctuation / whitespace) after JSON.
        x = (x or "").strip()
        x = x.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
        # keep trimming until last char is a plausible JSON terminator
        while x and x[-1] not in ("}", "]"):
            x = x[:-1].rstrip()
        return x

    def _repair_unclosed_brackets(x: str) -> str:
        """Best-effort repair when model forgot final } / ].

        Only appends missing closers; never inserts into the middle.
        """
        x = (x or "").strip()
        if not x:
            return x
        stack: list[str] = []
        in_str = False
        esc = False
        for ch in x:
            if in_str:
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
        # Close in reverse order.
        for opener in reversed(stack):
            x += "}" if opener == "{" else "]"
        return x

    # Fast path: the whole payload is a JSON object.
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    # Robust path: scan for decodable JSON objects via raw_decode.
    # This handles: leading text, trailing text, multiple objects, etc.
    candidates: list[dict[str, Any]] = []
    for m in re.finditer(r"\{", s):
        start = m.start()
        try:
            parsed, end = decoder.raw_decode(s, idx=start)
        except Exception:
            # Try best-effort repair if it looks like a truncated JSON object.
            repaired = _repair_unclosed_brackets(_strip_trailing_non_json(s[start:]))
            try:
                parsed = json.loads(repaired)
                end = start + len(repaired)
            except Exception:
                continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
            # If it's the shape we want, stop early.
            if isinstance(parsed.get("sections"), list):
                return parsed
            if isinstance(parsed.get("turns"), list):
                return parsed
            if isinstance(parsed.get("nodes"), list) and isinstance(parsed.get("edges"), list):
                return parsed
            if isinstance(parsed.get("intent"), dict) and isinstance(parsed.get("risk"), dict):
                return parsed
        # If we decoded something but it isn't a dict, keep scanning.
        _ = end

    if candidates:
        return candidates[0]

    # Last resort: tolerate python-like dicts (single quotes / None / True / False),
    # which some models emit even when asked for JSON.
    try:
        import ast

        # Try to isolate a plausible object-ish span first.
        left = s.find("{")
        right = s.rfind("}")
        blob = s[left : right + 1] if left >= 0 and right > left else s

        # Common cleanup: smart quotes + trailing commas.
        blob = (
            blob.replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2018", "'")
            .replace("\u2019", "'")
        )
        blob = re.sub(r",\s*([}\]])", r"\1", blob)

        obj = ast.literal_eval(blob)  # noqa: S307
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_force_advice_sections(obj: dict[str, Any]) -> list[dict[str, str]]:
    raw = obj.get("sections")
    if not isinstance(raw, list):
        return []
    allowed = {"strike", "detect", "defense", "support", "environment"}
    aliases = {
        "attack": "strike",
        "atk": "strike",
        "scout": "detect",
        "recon": "detect",
        "eco": "support",
        "economy": "support",
        "env": "environment",
        "terrain": "environment",
        "作战环境": "environment",
        "环境": "environment",
    }
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        k = str(item.get("key") or "").strip().lower() or "support"
        k = aliases.get(k, k)
        if k not in allowed:
            continue
        if k in seen:
            continue
        seen.add(k)
        detail_raw = item.get("detail")
        # Accept both string and list-of-strings from LLM output.
        if isinstance(detail_raw, list):
            detail = "\n".join(str(x).strip() for x in detail_raw if str(x).strip())
        else:
            detail = str(detail_raw or "").strip()
        out.append(
            {
                "key": k,
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "detail": detail,
            }
        )
        if len(out) >= 8:
            break
    return out


def _force_collaboration_advice_with_llm(context_text: str) -> tuple[list[dict[str, str]], str, str | None]:
    """返回 (sections 列表, 原始模型文本, 失败代号)。"""
    if ChatOpenAI is None:
        return [], "", "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return [], "", "llm:none:no_api_key"

    prompt = (
        "你是《星际争霸II》高水平参谋，根据下列实时战场摘要，为指挥官生成「兵力布势 + 作战环境」五维协同建议。\n"
        "请只输出一段合法 JSON（不要用 markdown 代码围栏，不要输出其它说明文字），格式严格如下：\n"
        '{"sections":['
        '{"key":"strike","title":"打击类建议","summary":"一句话摘要","detail":"2-4条可执行要点"},'
        '{"key":"defense","title":"防御类建议","summary":"...","detail":"..."},'
        '{"key":"detect","title":"侦察探测建议","summary":"...","detail":"..."},'
        '{"key":"support","title":"保障类建议","summary":"...","detail":"..."},'
        '{"key":"environment","title":"作战环境建议","summary":"地形/隘口/走廊/矿区控点一句话","detail":"2-4条环境约束与抢点要点"}'
        "]}\n"
        "五维必须齐全；environment 只谈地形控点、走廊、高地、瞭望与矿气区位，不要重复抄打击编制。\n"
        "要求：summary 每条不超过 80 字；detail 每条不超过 220 字；用语简洁、可执行；未见证据不要编造控点。\n\n"
        f"[战场态势]\n{context_text}"
    )
    try:
        cred = get_swarmbrain_llm_credentials()
        llm = _build_chat_openai(
            temperature=min(0.65, float(cred["temperature"]) + 0.05),
            timeout=40,
        )
        reply = llm.invoke(prompt)
        raw = _llm_message_text(reply)
        if not raw:
            return [], "", "llm:none:empty_reply"
        parsed = _extract_json_object(raw)
        if not parsed:
            return [], raw, "llm:none:json_parse"
        sections = _normalize_force_advice_sections(parsed)
        if not sections:
            return [], raw, "llm:none:empty_sections"
        return sections, raw, None
    except Exception as exc:  # noqa: BLE001
        return [], "", f"llm:error:{type(exc).__name__}"


_COT_DIM_KEYS = ("strike", "detect", "defense", "support")
_COT_DIM_ALIASES = {
    "strike": "strike",
    "attack": "strike",
    "atk": "strike",
    "打击": "strike",
    "进攻": "strike",
    "detect": "detect",
    "scout": "detect",
    "recon": "detect",
    "探测": "detect",
    "侦察": "detect",
    "defense": "defense",
    "defend": "defense",
    "defence": "defense",
    "防御": "defense",
    "防守": "defense",
    "support": "support",
    "economy": "support",
    "eco": "support",
    "保障": "support",
    "经济": "support",
}
_COT_TURN_SPECS = _cot_turn_specs("red")


def _map_cot_role(value: Any, *, side: str | None = None) -> str:
    role = str(value or "").strip().lower()
    if role in {"ours", "our", "self", "planner", "plan"}:
        return "ours"
    if role in {"enemy", "opp", "opponent", "predict", "prediction"}:
        return "enemy"
    if role in {"reflect", "revise", "correct", "reflection", "critique", "review"}:
        return "reflect"
    own, enemy = _perspective_labels(side)
    own_color = "blue" if own == "蓝方" else "red"
    enemy_color = "red" if own_color == "blue" else "blue"
    if role in {own_color, f"{own_color}_plan"}:
        return "ours"
    if role in {enemy_color, f"{enemy_color}_plan"}:
        return "enemy"
    compact = role.replace(" ", "").replace("·", "").replace("-", "").replace("_", "")
    if own in compact and "预测" not in compact and enemy not in compact:
        return "ours"
    if any(tok in compact for tok in (enemy, "对手", "敌方", "预测", "行为预测")):
        return "enemy"
    if any(tok in compact for tok in ("反思", "纠正", "对照", "张力")):
        return "reflect"
    if not _normalize_side(side):
        if role in {"red", "red_plan"}:
            return "ours"
        if role in {"blue", "blue_plan"}:
            return "enemy"
    return ""


def _coerce_cot_turns_list(src: dict[str, Any]) -> list[Any]:
    turns = src.get("turns")
    if isinstance(turns, list):
        return turns
    if isinstance(turns, dict):
        out: list[Any] = []
        for key, item in turns.items():
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("role", key)
                out.append(row)
            elif item is not None:
                out.append({"role": key, "summary": item})
        return out
    # 兼容顶层直接摊开 ours/enemy/reflect
    out = []
    for key in ("ours", "enemy", "reflect", "red", "blue", "our", "opponent"):
        item = src.get(key)
        if isinstance(item, dict):
            row = dict(item)
            row.setdefault("role", key)
            out.append(row)
    if out:
        return out
    for alt in ("agents", "rounds", "steps", "chain"):
        raw = src.get(alt)
        if isinstance(raw, list):
            return raw
    nested = src.get("loop") or src.get("selfplay") or src.get("data")
    if isinstance(nested, dict) and nested is not src:
        return _coerce_cot_turns_list(nested)
    return []


def _coerce_cot_dims_raw(item: dict[str, Any]) -> dict[str, Any]:
    dims_raw = item.get("dims") if isinstance(item.get("dims"), dict) else {}
    if not dims_raw:
        for key in ("dimensions", "intent_dimensions", "dim"):
            if isinstance(item.get(key), dict):
                dims_raw = item.get(key)  # type: ignore[assignment]
                break
    normalized: dict[str, Any] = {}
    for key, value in (dims_raw or {}).items():
        dim = _COT_DIM_ALIASES.get(str(key or "").strip().lower()) or _COT_DIM_ALIASES.get(str(key or "").strip())
        if not dim:
            continue
        normalized[dim] = value
    # 扁平 turn：只有 summary/detail/thoughts，没有 dims → 灌进 strike（避免 empty_turns）
    if not any(normalized.get(k) for k in _COT_DIM_KEYS):
        flat_summary = item.get("summary") or item.get("text") or item.get("plan")
        flat_detail = item.get("detail") or item.get("reason") or item.get("note")
        flat_thoughts = item.get("thoughts") or item.get("steps") or item.get("chain")
        if flat_summary or flat_detail or flat_thoughts:
            normalized["strike"] = {
                "summary": flat_summary or "",
                "detail": flat_detail or "",
                "thoughts": flat_thoughts if isinstance(flat_thoughts, list) else flat_thoughts or [],
            }
    return normalized


def _clip_dim_line(value: Any, limit: int = 120) -> str:
    if isinstance(value, dict):
        return _clip_text(value.get("summary") or value.get("text") or value.get("detail"), limit)
    return _clip_text(value, limit)


def _split_cot_thoughts(text: Any, *, limit: int = 8) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\n；;]+|(?<=[。！？!?])", raw)
    out: list[str] = []
    for part in parts:
        step = _clip_text(part, 100)
        if step:
            out.append(step)
        if len(out) >= limit:
            break
    return out


def _situation_clock(situation: dict[str, Any] | None, fallback: str = "") -> str:
    raw = ""
    try:
        raw = str(((situation or {}).get("raw") or {}).get("time_formatted") or "").strip()
    except Exception:
        raw = ""
    if raw in {"", "--:--", "样例窗"}:
        raw = ""
    fb = str(fallback or "").strip()
    if fb in {"", "--:--", "样例窗"}:
        fb = ""
    return raw or fb


def _live_game_clock(side: str, fallback: str = "") -> str:
    situation = _read_situation_from_redis(side) or _build_situation_from_dashboard_state(side)
    return _situation_clock(situation, fallback)


def _normalize_cot_dim(raw: Any) -> dict[str, Any]:
    thoughts: list[str] = []
    if isinstance(raw, dict):
        summary = raw.get("summary") or raw.get("title") or raw.get("text") or ""
        detail = raw.get("detail") or raw.get("reason") or raw.get("note") or ""
        raw_thoughts = raw.get("thoughts") or raw.get("steps") or raw.get("chain")
        if isinstance(raw_thoughts, list):
            thoughts = [_clip_text(item, 100) for item in raw_thoughts if str(item).strip()][:8]
        elif isinstance(raw_thoughts, str) and str(raw_thoughts).strip():
            thoughts = _split_cot_thoughts(raw_thoughts)
        if not thoughts:
            thoughts = _split_cot_thoughts(detail or summary)
        if not detail and thoughts:
            detail = "；".join(thoughts)
        return {
            "summary": _clip_text(summary, 80),
            "detail": _clip_text(detail, 360),
            "thoughts": thoughts,
        }
    text = _clip_text(raw, 220)
    thoughts = _split_cot_thoughts(text)
    return {"summary": text, "detail": text, "thoughts": thoughts}


def _clip_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _normalize_cot_selfplay(obj: dict[str, Any] | None, game_time: str = "", *, side: str | None = None) -> dict[str, Any] | None:
    root = obj if isinstance(obj, dict) else {}
    # 偶发包一层 loop/selfplay
    if not isinstance(root.get("turns"), (list, dict)) and isinstance(root.get("loop"), dict):
        root = root["loop"]
    elif not isinstance(root.get("turns"), (list, dict)) and isinstance(root.get("selfplay"), dict):
        root = root["selfplay"]
    src = root if isinstance(root, dict) else {}
    clock = str(game_time or src.get("game_time") or "").strip() or "--:--"
    turns_in = _coerce_cot_turns_list(src)
    by_role: dict[str, dict[str, Any]] = {}
    for item in turns_in:
        if not isinstance(item, dict):
            continue
        role = _map_cot_role(item.get("role") or item.get("key") or item.get("name") or item.get("title"), side=side)
        if not role:
            continue
        dims_raw = _coerce_cot_dims_raw(item)
        by_role[role] = item
        by_role[role]["_dims"] = {key: _normalize_cot_dim(dims_raw.get(key)) for key in _COT_DIM_KEYS}

    turns_out: list[dict[str, Any]] = []
    for role, title, subtitle_fallback in _cot_turn_specs(side):
        item = by_role.get(role) or {}
        dims = item.get("_dims") if isinstance(item.get("_dims"), dict) else {
            key: {"summary": "", "detail": "", "thoughts": []} for key in _COT_DIM_KEYS
        }
        turns_out.append(
            {
                "key": f"{role}",
                "role": role,
                "title": _clip_text(item.get("title") or title, 24) or title,
                "subtitle": _clip_text(item.get("subtitle") or subtitle_fallback, 36) or subtitle_fallback,
                "dims": dims,
                "conclusion": _clip_text(item.get("conclusion"), 160),
            }
        )

    has_turn_body = any(
        turn["conclusion"]
        or any(d.get("summary") or d.get("thoughts") for d in turn["dims"].values())
        for turn in turns_out
    )

    final_raw = src.get("finalPlan") if isinstance(src.get("finalPlan"), dict) else {}
    if not final_raw and isinstance(src.get("final_plan"), dict):
        final_raw = src.get("final_plan")
    if not final_raw and isinstance(src.get("verdict"), dict):
        final_raw = src.get("verdict")
    final_dims_raw = final_raw.get("dims") if isinstance(final_raw.get("dims"), dict) else {}
    if not final_dims_raw and final_raw:
        # 扁平 finalPlan 字符串维
        final_dims_raw = {
            key: final_raw.get(key)
            for key in _COT_DIM_KEYS
            if final_raw.get(key) is not None
        }
    steps_raw = final_raw.get("steps") if isinstance(final_raw.get("steps"), list) else []
    own_label, _enemy_label = _perspective_labels(side)
    final_title_fallback = f"{own_label}方案总结智能体"
    final_plan = {
        "title": _clip_text(final_raw.get("title") or final_title_fallback, 28) or final_title_fallback,
        "subtitle": _clip_text(final_raw.get("subtitle") or f"{clock} · 本轮收敛", 40),
        "dims": {key: _clip_dim_line(final_dims_raw.get(key)) for key in _COT_DIM_KEYS},
        "steps": [_clip_text(step, 80) for step in steps_raw if str(step).strip()][:4],
        "conclusion": _clip_text(final_raw.get("conclusion") or final_raw.get("summary"), 180),
    }
    if not final_plan["steps"]:
        final_plan["steps"] = [turn["conclusion"] for turn in turns_out if turn["conclusion"]][:3]
    if not final_plan["conclusion"]:
        final_plan["conclusion"] = turns_out[-1]["conclusion"] if turns_out else ""

    has_final_body = bool(
        final_plan["conclusion"]
        or final_plan["steps"]
        or any(final_plan["dims"].values())
    )
    if not has_turn_body and not has_final_body:
        return None
    # 只有 final、turns 空：用 final 回填 reflect，避免 empty_turns 直接丢整轮
    if not has_turn_body and has_final_body:
        seed = final_plan["conclusion"] or "；".join(final_plan["steps"]) or next(
            (v for v in final_plan["dims"].values() if v),
            "本轮已收敛",
        )
        for turn in turns_out:
            if turn["role"] == "reflect":
                turn["conclusion"] = turn["conclusion"] or seed
                if not any(d.get("summary") for d in turn["dims"].values()):
                    turn["dims"]["strike"] = {
                        "summary": _clip_text(seed, 80),
                        "detail": _clip_text(seed, 360),
                        "thoughts": _split_cot_thoughts(seed),
                    }

    return {
        "title": _clip_text(src.get("title") or f"{clock} 窗口推演", 32) or f"{clock} 窗口推演",
        "horizon": _clip_text(src.get("horizon") or "当前窗 + 下一窗", 24),
        "thesis": _clip_text(src.get("thesis"), 180),
        "situationDigest": _clip_text(src.get("situationDigest") or src.get("situation_digest"), 140),
        "turns": turns_out,
        "finalPlan": final_plan,
    }


def _selfplay_cot_with_llm(
    context_text: str,
    *,
    game_time: str = "",
    previous_conclusion: str = "",
    side: str | None = None,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """返回 (loop dict, 原始文本, 失败代号)。"""
    if ChatOpenAI is None:
        return None, "", "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return None, "", "llm:none:no_api_key"

    cred = get_swarmbrain_llm_credentials()
    clock = str(game_time or "--:--").strip() or "--:--"
    prev = str(previous_conclusion or "").strip()
    prev_line = f"上一轮收敛结论：{prev}\n" if prev else "上一轮：无（这是本局第一轮推演）\n"
    own, enemy = _perspective_labels(side)
    prompt = (
        f"你是《星际争霸II》{own} SwarmBrain 的对局参谋。根据当前态势，完成本时间点的一轮自博弈思维链。\n"
        f"流程必须是：{own}方案 → 对{enemy}的预测 → {own}反思纠正 → 收敛为{own}应对。\n"
        "四个维度固定为 strike/detect/defense/support（打击/探测/防御/保障）。\n"
        "每个智能体每个维度只输出两块：方案总体 summary，行动细节 thoughts（思维链步骤）。不要写 conclusion 字段。\n"
        "只输出一段合法 JSON，不要 markdown 围栏，不要解释。格式：\n"
        "{"
        '"title":"短标题","horizon":"当前窗 + 下一窗","thesis":"本轮推演命题",'
        '"situationDigest":"本时刻态势一句话",'
        '"turns":['
        f'{{"role":"ours","title":"{own}方案规划智能体","subtitle":"当前窗","dims":{{'
        '"strike":{"summary":"一句话方案总体","thoughts":["因为…","所以先…","若对方…则…"]},'
        '"detect":{"summary":"","thoughts":[]},"defense":{"summary":"","thoughts":[]},"support":{"summary":"","thoughts":[]}}},'
        f'{{"role":"enemy","title":"{enemy}行为预测智能体","subtitle":"{enemy}下一手","dims":{{...}}}},'
        '{"role":"reflect","title":"方案反思纠正智能体","subtitle":"对照张力","dims":{...}}'
        "],"
        f'"finalPlan":{{"title":"{own}方案总结智能体","subtitle":"本轮收敛","dims":{{"strike":"","detect":"","defense":"","support":""}},'
        '"steps":["当前窗动作","触发条件","下一窗收口"],"conclusion":""}'
        "}\n"
        "要求：所有判断必须扣住给定态势，禁止空泛教条。"
        "summary≤80字，是该维方案总体；thoughts 必须是 3～6 步思维链（因为/若/所以/则），每步≤80字，不要口号。"
        f"{own}=我方智能体，{enemy}=对手智能体。\n"
        "role 字段必须是英文：ours / enemy / reflect；dims 键必须是英文：strike/detect/defense/support。\n\n"
        f"比赛时钟：{clock}\n{prev_line}\n"
        f"[战场态势]\n{context_text}"
    )
    try:
        llm = _build_chat_openai(
            temperature=min(0.7, float(cred["temperature"]) + 0.08),
            max_tokens=5120,
            timeout=55,
        )
        reply = llm.invoke(prompt)
        raw = _llm_message_text(reply)
        if not raw:
            return None, "", "llm:none:empty_reply"
        parsed = _extract_json_object(raw)
        if not parsed:
            return None, raw, "llm:none:json_parse"
        loop = _normalize_cot_selfplay(parsed, clock, side=side)
        if loop:
            return loop, raw, None
        # 一次纠错重试：模型偶发用中文 role / 缺 dims 导致 empty_turns
        repair = (
            "上一次 JSON 无法解析为有效自博弈 turns（缺 ours/enemy/reflect 或 dims 为空）。"
            "请严格按下面格式重输一整段合法 JSON，不要 markdown：\n"
            '{"title":"短标题","horizon":"当前窗 + 下一窗","thesis":"命题","situationDigest":"态势",'
            '"turns":['
            '{"role":"ours","title":"红方方案","subtitle":"当前窗","dims":{"strike":{"summary":"…","thoughts":["…","…","…"]},'
            '"detect":{"summary":"…","thoughts":["…"]},'
            '"defense":{"summary":"…","thoughts":["…"]},'
            '"support":{"summary":"…","thoughts":["…"]}}},'
            '{"role":"enemy","title":"对蓝方的预测","subtitle":"蓝方下一手","dims":{...}},'
            '{"role":"reflect","title":"红方反思纠正","subtitle":"对照张力","dims":{...}}'
            '],"finalPlan":{"title":"红方应对","subtitle":"本轮收敛","dims":{"strike":"","detect":"","defense":"","support":""},'
            '"steps":["…","…","…"],"conclusion":"…"}}'
            "\nrole 只能是 ours/enemy/reflect。继续基于同一态势：\n"
            f"比赛时钟：{clock}\n{prev_line}\n[战场态势]\n{context_text}"
        )
        reply2 = llm.invoke(repair)
        raw2 = _llm_message_text(reply2) or raw
        parsed2 = _extract_json_object(raw2)
        loop2 = _normalize_cot_selfplay(parsed2, clock, side=side) if parsed2 else None
        if loop2:
            return loop2, raw2, None
        return None, raw2, "llm:none:empty_turns"
    except Exception as exc:  # noqa: BLE001
        return None, "", f"llm:error:{type(exc).__name__}"


_ENV_COT_TURN_SPECS = _env_cot_turn_specs("red")


def _normalize_env_cot_turn_body(item: dict[str, Any]) -> dict[str, str]:
    summary = _clip_text(item.get("summary") or item.get("title") or item.get("text"), 120)
    detail = _clip_text(item.get("detail") or item.get("reason") or item.get("note"), 480)
    thoughts_raw = item.get("thoughts") or item.get("steps") or item.get("chain")
    thoughts: list[str] = []
    if isinstance(thoughts_raw, list):
        thoughts = [_clip_text(x, 100) for x in thoughts_raw if str(x).strip()][:8]
    elif isinstance(thoughts_raw, str) and str(thoughts_raw).strip():
        thoughts = _split_cot_thoughts(thoughts_raw)
    if not thoughts:
        thoughts = _split_cot_thoughts(detail or summary)
    if not detail and thoughts:
        detail = "；".join(thoughts)
    if not summary and thoughts:
        summary = thoughts[0]
    return {
        "summary": summary,
        "detail": detail,
        "conclusion": _clip_text(item.get("conclusion"), 180),
    }


def _normalize_env_cot_selfplay(obj: dict[str, Any] | None, game_time: str = "", *, side: str | None = None) -> dict[str, Any] | None:
    """兵力布势自博弈：扁平 summary/detail，无 strike/detect dims，无当前窗/下一窗。"""
    root = obj if isinstance(obj, dict) else {}
    if not isinstance(root.get("turns"), (list, dict)) and isinstance(root.get("loop"), dict):
        root = root["loop"]
    src = root if isinstance(root, dict) else {}
    clock = str(game_time or src.get("game_time") or "").strip() or "--:--"
    turns_in = _coerce_cot_turns_list(src)
    by_role: dict[str, dict[str, Any]] = {}
    for item in turns_in:
        if not isinstance(item, dict):
            continue
        role = _map_cot_role(item.get("role") or item.get("key") or item.get("name") or item.get("title"), side=side)
        if not role:
            continue
        by_role[role] = item

    turns_out: list[dict[str, Any]] = []
    for role, title, subtitle_fallback in _env_cot_turn_specs(side):
        item = by_role.get(role) or {}
        body = _normalize_env_cot_turn_body(item)
        turns_out.append(
            {
                "key": role,
                "role": role,
                "title": _clip_text(item.get("title") or title, 36) or title,
                "subtitle": _clip_text(item.get("subtitle") or subtitle_fallback, 36) or subtitle_fallback,
                "summary": body["summary"],
                "detail": body["detail"],
                "conclusion": body["conclusion"],
            }
        )

    has_turn_body = any(turn["summary"] or turn["detail"] or turn["conclusion"] for turn in turns_out)

    final_raw = src.get("finalPlan") if isinstance(src.get("finalPlan"), dict) else {}
    if not final_raw and isinstance(src.get("final_plan"), dict):
        final_raw = src.get("final_plan")
    steps_raw = final_raw.get("steps") if isinstance(final_raw.get("steps"), list) else []
    final_summary = _clip_text(
        final_raw.get("summary") or final_raw.get("text") or final_raw.get("detail"),
        160,
    )
    own_label, _enemy_label = _perspective_labels(side)
    env_final_title = f"{own_label}兵力布势方案总结智能体"
    final_plan = {
        "title": _clip_text(final_raw.get("title") or env_final_title, 36) or env_final_title,
        "subtitle": _clip_text(final_raw.get("subtitle") or f"{clock} · 布势收敛", 40),
        "summary": final_summary,
        "steps": [_clip_text(step, 80) for step in steps_raw if str(step).strip()][:4],
        "conclusion": _clip_text(final_raw.get("conclusion"), 200),
    }
    if not final_plan["steps"]:
        final_plan["steps"] = [
            turn["conclusion"] or turn["summary"] for turn in turns_out if (turn["conclusion"] or turn["summary"])
        ][:3]
    if not final_plan["conclusion"]:
        final_plan["conclusion"] = turns_out[-1]["conclusion"] or turns_out[-1]["summary"] if turns_out else ""
    if not final_plan["summary"]:
        final_plan["summary"] = final_plan["conclusion"]

    has_final_body = bool(final_plan["conclusion"] or final_plan["summary"] or final_plan["steps"])
    if not has_turn_body and not has_final_body:
        return None
    if not has_turn_body and has_final_body:
        seed = final_plan["summary"] or final_plan["conclusion"]
        for turn in turns_out:
            if turn["role"] == "reflect":
                turn["summary"] = turn["summary"] or seed
                turn["detail"] = turn["detail"] or seed
                turn["conclusion"] = turn["conclusion"] or seed

    return {
        "title": _clip_text(src.get("title") or f"{clock} 布势推演", 32) or f"{clock} 布势推演",
        "horizon": _clip_text(src.get("horizon") or "本轮窗口", 24) or "本轮窗口",
        "thesis": _clip_text(src.get("thesis"), 180),
        "situationDigest": _clip_text(src.get("situationDigest") or src.get("situation_digest"), 140),
        "turns": turns_out,
        "finalPlan": final_plan,
    }


def _env_selfplay_cot_with_llm(
    context_text: str,
    *,
    game_time: str = "",
    previous_conclusion: str = "",
    side: str | None = None,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """兵力布势自博弈：返回扁平 loop dict。"""
    if ChatOpenAI is None:
        return None, "", "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return None, "", "llm:none:no_api_key"

    cred = get_swarmbrain_llm_credentials()
    clock = str(game_time or "--:--").strip() or "--:--"
    prev = str(previous_conclusion or "").strip()
    prev_line = f"上一轮兵力布势结论：{prev}\n" if prev else "上一轮：无（这是本局第一轮布势推演）\n"
    own, enemy = _perspective_labels(side)
    prompt = (
        f"你是《星际争霸II》{own} SwarmBrain 的兵力布势参谋。"
        "根据当前态势，完成本时间点一轮「兵力布势」自博弈思维链。\n"
        "只讨论双方编制落在哪、哪一维兵力厚/薄、前排与家门口如何摆，地形只作为落点约束，不要拆成打击/探测/防御/保障四套独立方案。\n"
        f"流程必须是：{own}兵力布势方案规划 → {enemy}兵力布势行为预测 → {own}兵力布势方案反思纠正 → {own}兵力布势方案总结。\n"
        "每个智能体只输出：summary（方案总体）+ detail/thoughts（行动细节思维链）。不要写当前窗/下一窗切换。\n"
        "只输出一段合法 JSON，不要 markdown 围栏，不要解释。格式：\n"
        "{"
        '"title":"短标题","horizon":"本轮窗口","thesis":"本轮布势推演命题",'
        '"situationDigest":"本时刻双方落点一句话",'
        '"turns":['
        f'{{"role":"ours","title":"{own}兵力布势方案规划智能体","subtitle":"本轮 · 布势方案",'
        '"summary":"一句话布势总体（谁在哪）","detail":"因为…；所以先摆…；若对方…则…"},'
        f'{{"role":"enemy","title":"{enemy}兵力布势行为预测智能体","subtitle":"本轮 · 对手布势反应",'
        '"summary":"","detail":""},'
        f'{{"role":"reflect","title":"{own}兵力布势方案反思纠正智能体","subtitle":"本轮 · 对照张力",'
        '"summary":"","detail":""}'
        "],"
        f'"finalPlan":{{"title":"{own}兵力布势方案总结智能体","subtitle":"兵力布势方案 · 由自博弈收敛",'
        '"summary":"收敛后的编成落位总体","steps":["落位1","落位2","落位3","约束"],"conclusion":"一句话本轮布势结论"}'
        "}\n"
        "要求：所有判断必须扣住给定态势中的可见单位、数量与区位，禁止空泛教条。"
        "summary≤80字；detail 写成 3～6 步思维链（因为/若/所以/则），可用中文分号或句号分隔，每步≤80字。\n"
        "finalPlan.steps 3～4 条可执行落位动作；conclusion 写清本轮不可破的布势纪律（哪路必须留兵、哪路禁止抽空）。\n"
        f"{own}=我方智能体，{enemy}=对手智能体。\n\n"
        f"比赛时钟：{clock}\n{prev_line}\n"
        f"[战场态势]\n{context_text}"
    )
    try:
        llm = _build_chat_openai(
            temperature=min(0.7, float(cred["temperature"]) + 0.08),
            max_tokens=4096,
            timeout=55,
        )
        reply = llm.invoke(prompt)
        raw = _llm_message_text(reply)
        if not raw:
            return None, "", "llm:none:empty_reply"
        parsed = _extract_json_object(raw)
        if not parsed:
            return None, raw, "llm:none:json_parse"
        loop = _normalize_env_cot_selfplay(parsed, clock, side=side)
        if not loop:
            return None, raw, "llm:none:empty_turns"
        return loop, raw, None
    except Exception as exc:  # noqa: BLE001
        return None, "", f"llm:error:{type(exc).__name__}"


_KILLNET_NODE_META = {
    "detect": ("探测", "提供目标信息"),
    "strike": ("打击", "形成任务效果"),
    "defense": ("防御", "保护行动窗口"),
    "support": ("保障", "维持资源与持续性"),
    "environment": ("作战环境", "约束四维行动"),
}
_KILLNET_DIM_KEYS = ("detect", "strike", "defense", "support")
_KILLNET_DEFAULT_EDGES = (
    ("detect", "strike", "causal", "目标信息 → 开火条件"),
    ("strike", "defense", "causal", "任务效果 → 需要窗口"),
    ("defense", "strike", "window", "保护接敌与收网窗"),
    ("support", "strike", "resource", "产能维持打击可持续"),
    ("support", "detect", "resource", "保障侦察不掉线"),
    ("detect", "defense", "space", "预警驱动设防"),
    ("defense", "support", "space", "守住矿线才有经济"),
    ("environment", "strike", "space", "地形约束打击接敌面"),
    ("environment", "detect", "space", "地貌决定开雾与哨位"),
    ("environment", "defense", "space", "隘口高地决定设防线"),
    ("environment", "support", "space", "走廊安全决定矿线"),
)


def _expand_killnet_node_text(key: str, dim_text: str, conclusion: str, steps: list[str]) -> str:
    label, role = _KILLNET_NODE_META[key]
    text = str(dim_text or "").strip()
    related = [str(step).strip() for step in steps if str(step).strip() and (label in str(step) or key in str(step).lower())][:2]
    inbound = [f"{_KILLNET_NODE_META[src][0]}→{label}（{lab}）" for src, dst, _kind, lab in _KILLNET_DEFAULT_EDGES if dst == key]
    outbound = [f"{label}→{_KILLNET_NODE_META[dst][0]}（{lab}）" for src, dst, _kind, lab in _KILLNET_DEFAULT_EDGES if src == key]
    parts: list[str] = []
    if text:
        parts.append(text)
    else:
        parts.append(f"本轮红方应对尚未写明{label}动作，先按「{role}」占位，避免空维。")
    if related:
        parts.append("方案步骤：" + "；".join(related))
    if inbound:
        parts.append("承接：" + "；".join(inbound[:2]))
    if outbound:
        parts.append("送出：" + "；".join(outbound[:2]))
    if conclusion and conclusion not in text:
        parts.append(f"服从总轴：{conclusion}")
    return _clip_text(" ".join(part for part in parts if part), 420)


def _env_plan_node_payload(environment_plan: dict[str, Any] | None, final_plan: dict[str, Any] | None) -> dict[str, Any]:
    env = environment_plan if isinstance(environment_plan, dict) else {}
    plan = final_plan if isinstance(final_plan, dict) else {}
    if not env:
        nested = plan.get("environment")
        if isinstance(nested, dict):
            env = nested
    label, role = _KILLNET_NODE_META["environment"]
    conclusion = _clip_text(env.get("conclusion") or env.get("summary") or env.get("fromPlan"), 280)
    steps = [str(step).strip() for step in (env.get("steps") or []) if str(step).strip()][:4]
    if not conclusion:
        conclusion = "本轮尚未写入作战环境约束，地形窗口仍按缺省占位。"
    outputs = steps or [part.strip() for part in conclusion.replace("；", "，").split("，") if len(part.strip()) >= 4][:4]
    return {
        "key": "environment",
        "label": label,
        "role": role,
        "fromPlan": conclusion,
        "outputs": [_clip_text(x, 48) for x in outputs if str(x).strip()][:5] or [role],
    }


def _killnet_from_final_plan(
    final_plan: dict[str, Any] | None,
    game_time: str = "",
    environment_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = final_plan if isinstance(final_plan, dict) else {}
    dims = plan.get("dims") if isinstance(plan.get("dims"), dict) else {}
    conclusion = _clip_text(plan.get("conclusion"), 280)
    steps = [str(step).strip() for step in (plan.get("steps") or []) if str(step).strip()]
    clock = str(game_time or "").strip() or "--:--"
    nodes: list[dict[str, Any]] = []
    for key in _KILLNET_DIM_KEYS:
        label, role = _KILLNET_NODE_META[key]
        text = _expand_killnet_node_text(key, str(dims.get(key) or ""), conclusion, steps)
        outputs = [part.strip() for part in text.replace("；", "，").split("，") if len(part.strip()) >= 4][:5]
        nodes.append(
            {
                "key": key,
                "label": label,
                "role": role,
                "fromPlan": text or f"本轮红方应对尚未给出{label}要点。",
                "outputs": outputs or [role],
            }
        )
    nodes.append(_env_plan_node_payload(environment_plan, plan))
    thesis = conclusion
    if not thesis:
        thesis = f"{clock} 红方应对已按打/探/防/保落网：探测开门、打击吃窗口、防御护收网、保障供产能；中心受作战环境约束。"
    elif len(thesis) < 48:
        thesis = f"{thesis} 探测给开火条件，防御护接敌与复位，保障把产能和侦察吊住，四维按边咬合而不是各写各的。"
    return {
        "thesis": thesis,
        "nodes": nodes,
        "edges": [
            {"from": src, "to": dst, "kind": kind, "label": label}
            for src, dst, kind, label in _KILLNET_DEFAULT_EDGES
        ],
        "sequence": [],
        "patternLabel": "",
    }


def _normalize_cot_killnet(
    obj: dict[str, Any] | None,
    final_plan: dict[str, Any] | None,
    game_time: str = "",
    environment_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = _killnet_from_final_plan(final_plan, game_time, environment_plan)
    src = obj if isinstance(obj, dict) else {}
    nodes_in = src.get("nodes") if isinstance(src.get("nodes"), list) else []
    by_key: dict[str, dict[str, Any]] = {}
    for item in nodes_in:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        if key in _KILLNET_NODE_META:
            by_key[key] = item
    nodes_out: list[dict[str, Any]] = []
    for key, (label, role) in _KILLNET_NODE_META.items():
        item = by_key.get(key) or {}
        fb = next((n for n in fallback["nodes"] if n["key"] == key), {})
        outputs_raw = item.get("outputs") if isinstance(item.get("outputs"), list) else fb.get("outputs") or []
        nodes_out.append(
            {
                "key": key,
                "label": label,
                "role": _clip_text(item.get("role") or role, 24) or role,
                "fromPlan": _clip_text(item.get("fromPlan") or item.get("from_plan") or fb.get("fromPlan"), 420),
                "outputs": [_clip_text(x, 48) for x in outputs_raw if str(x).strip()][:5],
            }
        )
    edges_in = src.get("edges") if isinstance(src.get("edges"), list) else []
    edges_out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in edges_in:
        if not isinstance(item, dict):
            continue
        src_key = str(item.get("from") or item.get("source") or "").strip().lower()
        dst_key = str(item.get("to") or item.get("target") or "").strip().lower()
        if src_key not in _KILLNET_NODE_META or dst_key not in _KILLNET_NODE_META or src_key == dst_key:
            continue
        pair = (src_key, dst_key)
        if pair in seen:
            continue
        seen.add(pair)
        kind = str(item.get("kind") or "causal").strip().lower()
        if kind not in {"causal", "window", "resource", "space"}:
            kind = "causal"
        edges_out.append(
            {
                "from": src_key,
                "to": dst_key,
                "kind": kind,
                "label": _clip_text(item.get("label"), 56),
            }
        )
    if len(edges_out) < 3:
        edges_out = fallback["edges"]
    thesis = _clip_text(src.get("thesis") or fallback.get("thesis"), 360)
    seq_in = src.get("sequence") if isinstance(src.get("sequence"), list) else []
    sequence: list[dict[str, Any]] = []
    for idx, item in enumerate(seq_in):
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dim") or item.get("key") or "").strip().lower()
        if dim not in _KILLNET_DIM_KEYS:
            continue
        sequence.append(
            {
                "id": _clip_text(item.get("id") or f"llm-{idx + 1}", 24) or f"llm-{idx + 1}",
                "order": _coerce_int(item.get("order"), idx + 1),
                "dim": dim,
                "phase": _clip_text(item.get("phase") or f"T{idx}", 8),
                "title": _clip_text(item.get("title") or item.get("label") or _KILLNET_NODE_META[dim][0], 36),
                "action": _clip_text(item.get("action") or item.get("text") or item.get("fromPlan"), 160),
                "gate": _clip_text(item.get("gate") or item.get("condition"), 56),
                "sync": _clip_text(item.get("sync") or item.get("parallel"), 56),
                "also": [
                    str(x).strip().lower()
                    for x in (item.get("also") or [])
                    if str(x).strip().lower() in _KILLNET_DIM_KEYS
                ][:2],
            }
        )
    pattern_label = _clip_text(src.get("patternLabel") or src.get("pattern_label"), 16)
    return {
        "thesis": thesis,
        "nodes": nodes_out,
        "edges": edges_out,
        "sequence": sequence,
        "patternLabel": pattern_label,
    }


def _cot_killnet_payload(raw: dict[str, Any] | None, fallback: dict[str, Any]) -> CotKillnetPayload:
    src = raw if isinstance(raw, dict) else None
    try:
        return CotKillnetPayload(**(src or fallback))
    except Exception:
        return CotKillnetPayload(**fallback)


def _cot_killnet_with_llm(
    context_text: str,
    final_plan: dict[str, Any] | None,
    *,
    game_time: str = "",
    situation_digest: str = "",
    environment_plan: dict[str, Any] | None = None,
    side: str | None = None,
) -> tuple[dict[str, Any] | None, str, str | None]:
    if ChatOpenAI is None:
        return None, "", "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return None, "", "llm:none:no_api_key"

    cred = get_swarmbrain_llm_credentials()
    clock = str(game_time or "--:--").strip() or "--:--"
    try:
        plan_text = json.dumps(final_plan or {}, ensure_ascii=False)
    except Exception:
        plan_text = str(final_plan or "")
    try:
        env_text = json.dumps(environment_plan or {}, ensure_ascii=False)
    except Exception:
        env_text = str(environment_plan or "")
    digest = str(situation_digest or "").strip()
    own, _enemy = _perspective_labels(side)
    prompt = (
        f"你是《星际争霸II》{own}杀伤网参谋。把下面这一轮「{own}应对」展开成可执行的打/探/防/保杀伤网，"
        "并写入中心节点「作战环境」约束。\n"
        "禁止只抄一句口号。值班台要看清：本维做什么、依赖谁、向谁交货、在哪停。\n"
        "只输出一段合法 JSON，不要 markdown 围栏。格式：\n"
        "{"
        '"thesis":"80-160字：本轮主轴、先开哪一维、四维如何咬合、何处收网、环境如何约束",'
        '"nodes":['
        '{"key":"detect","label":"探测","role":"提供目标信息","fromPlan":"至少3句，写清区位/单位/更新节奏/交给打击或防御的条件","outputs":["产物1","产物2","产物3","产物4"]},'
        '{"key":"strike","label":"打击","role":"形成任务效果","fromPlan":"...","outputs":["..."]},'
        '{"key":"defense","label":"防御","role":"保护行动窗口","fromPlan":"...","outputs":["..."]},'
        '{"key":"support","label":"保障","role":"维持资源与持续性","fromPlan":"...","outputs":["..."]},'
        '{"key":"environment","label":"作战环境","role":"约束四维行动","fromPlan":"地形/隘口/走廊/塔权对本轮四维的硬约束","outputs":["约束1","约束2","约束3"]}'
        "],"
        '"edges":[{"from":"detect","to":"strike","kind":"causal","label":"具体因果，不要空标签"},'
        '{"from":"environment","to":"strike","kind":"space","label":"地形约束打击接敌面"}],'
        '"patternLabel":"本轮节奏名",'
        '"sequence":[{"order":1,"dim":"detect","phase":"T0","title":"动作名","action":"40-80字的可执行动作","gate":"门槛","sync":"与谁并行","also":["support"]}]'
        "}\n"
        f"节点 fromPlan 必须基于{own}应对对应维度展开，不要另起无关方案；每维 80-180 字，至少 3 句："
        "①本轮具体动作（单位、区位、节奏）；②依赖哪一维的什么信息或窗口；③向下一维输出什么条件或产物。\n"
        "environment 节点必须吸收「环境约束方案」的 conclusion/steps，写清不可破的地形纪律。\n"
        "outputs 每维 4-6 条，每条 8-20 字，必须是可核对产物，不要形容词。\n"
        "edges.kind 只能是 causal / window / resource / space；边 6-10 条，须含至少 1 条 environment→四维，标签写清因果，≤56字。\n"
        "sequence 必须 8-12 步，打/探/防/保交错，禁止固定成探测→打击→防御→保障。"
        "每步 dim 只能是 detect/strike/defense/support（不要 environment）；action 写具体，不要四步走完。\n\n"
        f"比赛时钟：{clock}\n态势摘要：{digest or '无'}\n"
        f"[{own}应对]\n{plan_text}\n\n"
        f"[环境约束方案]\n{env_text}\n\n"
        f"[战场态势]\n{context_text}"
    )
    try:
        llm = _build_chat_openai(
            temperature=min(0.55, float(cred["temperature"]) + 0.02),
            timeout=40,
        )
        reply = llm.invoke(prompt)
        raw = _llm_message_text(reply)
        if not raw:
            return None, "", "llm:none:empty_reply"
        parsed = _extract_json_object(raw)
        if not parsed:
            return None, raw, "llm:none:json_parse"
        return _normalize_cot_killnet(parsed, final_plan, clock, environment_plan), raw, None
    except Exception as exc:  # noqa: BLE001
        return None, "", f"llm:error:{type(exc).__name__}"


_VALID_STRATEGIES = set(_STRATEGY_CN.keys())
_VALID_RISK_LEVELS = set(_RISK_LEVEL_CN.keys())
_VALID_THREAT_TYPES = set(_THREAT_TYPE_CN.keys())
_VALID_DIM_LEVELS = {"high", "medium", "low"}


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _normalize_fast_intent_payload(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    strategy = str(src.get("primary_strategy") or "unknown").strip().lower()
    if strategy not in _VALID_STRATEGIES:
        strategy = "unknown"
    conf = max(0.0, min(1.0, _coerce_float(src.get("confidence"), 0.55)))

    dims_in = src.get("intent_dimensions") if isinstance(src.get("intent_dimensions"), dict) else {}
    ebd_in = src.get("evidence_by_dimension") if isinstance(src.get("evidence_by_dimension"), dict) else {}
    dims_out: dict[str, Any] = {}
    ebd_out: dict[str, Any] = {}
    for key in ("attack", "defense", "detect", "support"):
        meta = dims_in.get(key) if isinstance(dims_in.get(key), dict) else {}
        level = str(meta.get("level") or "medium").strip().lower()
        if level not in _VALID_DIM_LEVELS:
            level = "medium"
        desc = str(meta.get("description_cn") or meta.get("description") or "").strip()
        dims_out[key] = {"level": level, "description": desc, "description_cn": desc}
        items = ebd_in.get(key) if isinstance(ebd_in.get(key), list) else []
        ebd_out[key] = [str(x).strip() for x in items if str(x).strip()][:6]

    evidence = src.get("evidence")
    evidence_out = (
        [str(x).strip() for x in evidence if str(x).strip()][:12]
        if isinstance(evidence, list)
        else []
    )

    timing = src.get("timing_window") if isinstance(src.get("timing_window"), dict) else {}
    timing_out = {
        "critical_window_start_sec": _coerce_int(timing.get("critical_window_start_sec")),
        "critical_window_end_sec": _coerce_int(timing.get("critical_window_end_sec")),
        "reason": str(timing.get("reason") or "").strip()[:180],
    }

    routes_out: list[dict[str, Any]] = []
    routes = src.get("expected_attack_routes")
    if isinstance(routes, list):
        for item in routes[:6]:
            if not isinstance(item, dict):
                continue
            units = item.get("involved_units")
            routes_out.append(
                {
                    "direction": str(item.get("direction") or "--").strip()[:48],
                    "probability": max(0.0, min(1.0, _coerce_float(item.get("probability"), 0.4))),
                    "eta_seconds": _coerce_int(item.get("eta_seconds"), 0) or 0,
                    "involved_units": (
                        [str(u).strip() for u in units if str(u).strip()][:4]
                        if isinstance(units, list)
                        else []
                    ),
                }
            )

    tech = src.get("tech_switch_risk") if isinstance(src.get("tech_switch_risk"), dict) else {}
    tech_out = {
        "switching_to": str(tech.get("switching_to") or "").strip()[:48],
        "probability": max(0.0, min(1.0, _coerce_float(tech.get("probability"), 0.2))),
    }

    return {
        "primary_strategy": strategy,
        "confidence": conf,
        "evidence": evidence_out,
        "intent_dimensions": dims_out,
        "evidence_by_dimension": ebd_out,
        "timing_window": timing_out,
        "expected_attack_routes": routes_out,
        "tech_switch_risk": tech_out,
        "source": "llm_fast",
    }


def _normalize_fast_risk_payload(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    overall = str(src.get("overall_risk_level") or "medium").strip().lower()
    if overall not in _VALID_RISK_LEVELS:
        overall = "medium"
    risks_out: list[dict[str, Any]] = []
    risks = src.get("risks")
    if isinstance(risks, list):
        for item in risks[:8]:
            if not isinstance(item, dict):
                continue
            threat = str(item.get("threat_type") or "unknown").strip().lower()
            if threat not in _VALID_THREAT_TYPES:
                threat = "unknown"
            risks_out.append(
                {
                    "threat_type": threat,
                    "target": str(item.get("target") or "未命名目标").strip()[:64],
                    "threat_score": round(max(0.0, min(1.0, _coerce_float(item.get("threat_score"), 0.5))), 2),
                    "time_to_impact_sec": _coerce_int(item.get("time_to_impact_sec"), 60) or 60,
                    "reasoning": str(item.get("reasoning") or "").strip()[:220],
                }
            )
    return {
        "overall_risk_level": overall,
        "risks": risks_out,
        "source": "llm_fast",
    }


def _normalize_fast_response_payload(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    adjustments_out: list[dict[str, Any]] = []
    adjustments = src.get("force_adjustments")
    if isinstance(adjustments, list):
        for item in adjustments[:6]:
            if not isinstance(item, dict):
                continue
            adjustments_out.append(
                {
                    "action": str(item.get("action") or "调整").strip()[:32],
                    "unit_or_building": str(item.get("unit_or_building") or "—").strip()[:48],
                    "quantity": _coerce_int(item.get("quantity"), 0) or 0,
                    "reason": str(item.get("reason") or "").strip()[:160],
                }
            )
    return {
        "plan_summary": str(src.get("plan_summary") or "").strip()[:320],
        "force_adjustments": adjustments_out,
        "mode": str(src.get("mode") or "advisory").strip()[:32] or "advisory",
        "commands": [],
        "hitl_required": bool(src.get("hitl_required")),
        "hitl_prompt": str(src.get("hitl_prompt") or "").strip()[:160],
        "source": "llm_fast",
    }


def _llm_message_text(reply: Any) -> str:
    """从 ChatOpenAI 回复中抽取可见文本（兼容 str / content blocks / reasoning 字段）。"""
    if reply is None:
        return ""
    raw = getattr(reply, "content", reply)
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            return text
    elif isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
            else:
                t = getattr(item, "text", None) or getattr(item, "content", None)
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        if parts:
            return "\n".join(parts).strip()
    # 部分推理模型把正文放在 additional_kwargs
    extras = getattr(reply, "additional_kwargs", None)
    if isinstance(extras, dict):
        for key in ("reasoning_content", "refusal", "output_text"):
            val = extras.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _composition_top_names(items: Any, limit: int = 4) -> list[str]:
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for item in items[:12]:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("type") or item.get("unit_type") or "").strip()
            if name:
                out.append(name)
        elif item is not None:
            name = str(item).strip()
            if name:
                out.append(name)
        if len(out) >= limit:
            break
    return out


def _intent_risk_from_situation(situation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """LLM 失败时的态势启发式兜底，保证前端始终有可读意图/风险。"""
    unit = situation.get("unit") or {}
    vision = situation.get("vision") or {}
    economy = situation.get("economy") or {}
    own_army = int(unit.get("army_count") or 0)
    enemy_vis = int(vision.get("enemy_units_visible") or 0)
    enemy_struct = int(vision.get("enemy_structures_visible") or 0)
    workers = int(economy.get("workers") or 0)
    enemy_names = _composition_top_names(unit.get("enemy_visible_composition"))
    own_names = _composition_top_names(unit.get("own_unit_composition"))

    if enemy_vis >= max(18, own_army + 8):
        strategy, attack_lv, defense_lv, overall = "timing_attack", "high", "medium", "high"
        conf = 0.72
    elif enemy_vis >= 8:
        strategy, attack_lv, defense_lv, overall = "harassment", "medium", "medium", "medium"
        conf = 0.64
    elif enemy_vis <= 2 and workers >= 16:
        strategy, attack_lv, defense_lv, overall = "macro", "low", "medium", "low"
        conf = 0.58
    else:
        strategy, attack_lv, defense_lv, overall = "unknown", "medium", "medium", "medium"
        conf = 0.5

    detect_lv = "high" if enemy_vis <= 3 else "medium"
    support_lv = "high" if workers < 14 else "medium"
    enemy_label = "、".join(enemy_names) if enemy_names else "可见敌军集群"
    own_label = "、".join(own_names) if own_names else "我方当前编成"

    intent = {
        "primary_strategy": strategy,
        "confidence": conf,
        "evidence": [
            f"敌方可见单位约 {enemy_vis}，建筑约 {enemy_struct}",
            f"我方军力约 {own_army}，工人 {workers}",
            f"敌方可见构成：{enemy_label}",
        ],
        "intent_dimensions": {
            "attack": {
                "level": attack_lv,
                "description": f"基于可见兵力对比，判断对方进攻压力为{attack_lv}。",
            },
            "defense": {
                "level": defense_lv,
                "description": f"对方可能保留防线/回防弹性，防守意图{defense_lv}。",
            },
            "detect": {
                "level": detect_lv,
                "description": "视野与侦察覆盖不足时探测意图上升。" if detect_lv == "high" else "已有基础可见情报，持续补侦。",
            },
            "support": {
                "level": support_lv,
                "description": f"经济与补给保障压力为{support_lv}（工人 {workers}）。",
            },
        },
        "evidence_by_dimension": {
            "attack": [f"可见敌军 {enemy_vis} vs 我方军力 {own_army}"],
            "defense": [f"可见敌方建筑 {enemy_struct}"],
            "detect": [f"敌方构成采样：{enemy_label}"],
            "support": [f"我方构成采样：{own_label}"],
        },
        "timing_window": {
            "critical_window_start_sec": 20,
            "critical_window_end_sec": 90,
            "reason": "按当前可见兵力差估算的近窗压力。",
        },
        "expected_attack_routes": [
            {
                "direction": "中路/正面接触线",
                "probability": 0.55 if attack_lv != "low" else 0.3,
                "eta_seconds": 45,
                "involved_units": enemy_names[:3],
            }
        ],
        "tech_switch_risk": {"switching_to": "", "probability": 0.25},
        "source": "heuristic",
    }
    risks: list[dict[str, Any]] = []
    if enemy_vis > 0:
        risks.append(
            {
                "threat_type": "army_push" if enemy_vis >= 12 else "harass",
                "target": "我方前线/矿线",
                "threat_score": round(min(0.95, 0.35 + enemy_vis / 80.0), 2),
                "time_to_impact_sec": 40 if enemy_vis >= 12 else 70,
                "reasoning": f"可见敌军约 {enemy_vis}，对 {own_label} 形成压迫。",
            }
        )
    if enemy_struct >= 3:
        risks.append(
            {
                "threat_type": "tech",
                "target": "我方科技节奏",
                "threat_score": 0.48,
                "time_to_impact_sec": 120,
                "reasoning": f"可见敌方建筑约 {enemy_struct}，存在科技/结构扩张压力。",
            }
        )
    if workers < 14:
        risks.append(
            {
                "threat_type": "economy_denial",
                "target": "我方经济线",
                "threat_score": 0.52,
                "time_to_impact_sec": 90,
                "reasoning": f"工人仅 {workers}，抗压与补给恢复偏弱。",
            }
        )
    if not risks:
        risks.append(
            {
                "threat_type": "unknown",
                "target": "信息不足区域",
                "threat_score": 0.4,
                "time_to_impact_sec": 60,
                "reasoning": "当前可见情报有限，维持警戒并补侦。",
            }
        )
    risk = {"overall_risk_level": overall, "risks": risks, "source": "heuristic"}
    response = {
        "plan_summary": "先稳住矿线与前线接触，补侦后再决定是否对推。",
        "force_adjustments": [
            {
                "action": "补侦",
                "unit_or_building": "Overlord/轻骑",
                "quantity": 1,
                "reason": "提高敌方编成与轴向确信度。",
            }
        ],
        "mode": "advisory",
        "commands": [],
        "hitl_required": False,
        "hitl_prompt": "",
        "source": "heuristic",
    }
    return intent, risk, response


def _intent_risk_with_llm(
    context_text: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str | None]:
    """单次大模型调用，直接产出意图识别 + 风险分析（及可选处置摘要）。"""
    if ChatOpenAI is None:
        return {}, {}, {}, "", "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return {}, {}, {}, "", "llm:none:no_api_key"

    cred = get_swarmbrain_llm_credentials()
    prompt = (
        "你是《星际争霸II》高水平态势参谋。根据下列实时战场摘要，同时完成「敌方意图识别」与「对我方风险分析」。\n"
        "请只输出一段合法 JSON（不要 markdown 代码围栏，不要其它说明文字），格式严格如下：\n"
        "{"
        '"intent":{'
        '"primary_strategy":"rush|timing_attack|macro|harassment|all_in|tech_switch|defensive|unknown",'
        '"confidence":0.0,'
        '"evidence":["要点1","要点2"],'
        '"intent_dimensions":{'
        '"attack":{"level":"high|medium|low","description":"一句话"},'
        '"defense":{"level":"high|medium|low","description":"一句话"},'
        '"detect":{"level":"high|medium|low","description":"一句话"},'
        '"support":{"level":"high|medium|low","description":"一句话"}'
        "},"
        '"evidence_by_dimension":{"attack":["证据"],"defense":[],"detect":[],"support":[]},'
        '"timing_window":{"critical_window_start_sec":0,"critical_window_end_sec":0,"reason":"一句话"},'
        '"expected_attack_routes":[{"direction":"中路","probability":0.5,"eta_seconds":40,"involved_units":["Roach"]}],'
        '"tech_switch_risk":{"switching_to":"","probability":0.2}'
        "},"
        '"risk":{'
        '"overall_risk_level":"critical|high|medium|low|unknown",'
        '"risks":[{'
        '"threat_type":"army_push|harass|tech|economy_denial|air_raid|unknown",'
        '"target":"目标",'
        '"threat_score":0.0,'
        '"time_to_impact_sec":60,'
        '"reasoning":"一句话"'
        "}]"
        "},"
        '"response":{'
        '"plan_summary":"处置摘要",'
        '"force_adjustments":[{"action":"增援","unit_or_building":"Roach","quantity":6,"reason":"一句话"}],'
        '"mode":"advisory","hitl_required":false,"hitl_prompt":""'
        "}"
        "}\n"
        "要求：紧扣当前态势，证据与风险各 3～6 条即可；字段值使用英文枚举；description/reasoning 用中文；总输出尽量紧凑。\n\n"
        f"[战场态势]\n{context_text}"
    )
    try:
        llm = _build_chat_openai(
            temperature=min(0.45, float(cred["temperature"]) + 0.02),
            timeout=50,
        )
        reply = llm.invoke(prompt)
        raw = _llm_message_text(reply)
        if not raw:
            return {}, {}, {}, "", "llm:none:empty_reply"
        parsed = _extract_json_object(raw)
        if not parsed:
            return {}, {}, {}, raw, "llm:none:json_parse"
        intent = _normalize_fast_intent_payload(
            parsed.get("intent") if isinstance(parsed.get("intent"), dict) else parsed
        )
        risk_src = parsed.get("risk") if isinstance(parsed.get("risk"), dict) else None
        if risk_src is None and isinstance(parsed.get("risks"), list):
            risk_src = {
                "overall_risk_level": parsed.get("overall_risk_level"),
                "risks": parsed.get("risks"),
            }
        risk = _normalize_fast_risk_payload(risk_src)
        response = _normalize_fast_response_payload(
            parsed.get("response") if isinstance(parsed.get("response"), dict) else {}
        )
        if not risk.get("risks") and str(intent.get("primary_strategy") or "unknown") == "unknown":
            return {}, {}, {}, raw, "llm:none:empty_payload"
        return intent, risk, response, raw, None
    except Exception as exc:  # noqa: BLE001
        return {}, {}, {}, "", f"llm:error:{type(exc).__name__}"


def _refresh_intent_risk_fast(
    *,
    side: str,
    timeout_sec: int,
) -> DeerflowRiskResponse:
    traces: list[str] = ["mode:fast"]
    thread_id = f"fast-{uuid.uuid4().hex[:12]}"
    situation = _read_situation_from_redis(side)
    source = f"redis:{side or 'default'}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side or 'default'}"
    if not situation:
        return DeerflowRiskResponse(
            ok=False,
            thread_id=thread_id,
            mode="fast",
            summary="暂时读取不到态势数据。请先确认对局、dashboard_state 或 Redis 可用。",
            traces=traces + ["context:none"],
        )
    traces.append(f"context:{source}")
    context_text = _build_force_advice_context_text(situation)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_intent_risk_with_llm, context_text)
        try:
            intent_raw, risk_raw, response_raw, answer_raw, llm_err = fut.result(timeout=float(timeout_sec))
        except concurrent.futures.TimeoutError:
            intent_raw, risk_raw, response_raw = _intent_risk_from_situation(situation)
            traces.extend(["llm:timeout", "fallback:heuristic"])
            intent = _normalize_intent_cn(intent_raw)
            risk = _normalize_risk_cn(risk_raw)
            return DeerflowRiskResponse(
                ok=True,
                thread_id=thread_id,
                mode="fast",
                summary=_build_risk_summary_cn(intent, risk) + "（大模型超时，已用态势启发式）",
                intent=intent,
                risk=risk,
                response=response_raw or {},
                stages={"intent": intent, "risk_id": risk, "risk_response": response_raw or {}},
                traces=traces,
                pipeline_stdout="",
            )

    traces.append("llm:intent_risk_fast")
    if llm_err:
        traces.append(llm_err)
    if answer_raw:
        traces.append(f"llm:raw_chars={len(answer_raw)}")

    used_fallback = False
    if llm_err or (
        not risk_raw.get("risks")
        and str(intent_raw.get("primary_strategy") or "unknown") == "unknown"
    ):
        intent_raw, risk_raw, response_raw = _intent_risk_from_situation(situation)
        traces.append("fallback:heuristic")
        used_fallback = True

    intent = _normalize_intent_cn(intent_raw)
    risk = _normalize_risk_cn(risk_raw)
    has_payload = bool(risk.get("risks")) or str(intent.get("primary_strategy") or "unknown") != "unknown"
    if not has_payload:
        return DeerflowRiskResponse(
            ok=False,
            thread_id=thread_id,
            mode="fast",
            summary=str(llm_err or "未能生成意图/风险结果"),
            intent=intent,
            risk=risk,
            response=response_raw or {},
            stages={"intent": intent, "risk_id": risk, "risk_response": response_raw or {}},
            traces=traces,
            pipeline_stdout=(answer_raw or "")[-4000:],
        )

    summary = _build_risk_summary_cn(intent, risk)
    if used_fallback:
        summary += "（大模型暂不可用，已用态势启发式）"
    return DeerflowRiskResponse(
        ok=True,
        thread_id=thread_id,
        mode="fast",
        summary=summary,
        intent=intent,
        risk=risk,
        response=response_raw or {},
        stages={"intent": intent, "risk_id": risk, "risk_response": response_raw or {}},
        traces=traces,
        pipeline_stdout=(answer_raw or "")[-4000:],
    )


def _strike_targets_advice_with_llm(situation_text: str, targets_brief: str) -> tuple[str, str | None]:
    """返回 (建议正文, 失败代号)。"""
    if ChatOpenAI is None:
        return "", "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return "", "llm:none:no_api_key"

    cred = get_swarmbrain_llm_credentials()
    prompt = (
        "你是《星际争霸II》高水平参谋。根据下列「实时态势摘要」与「系统识别出的候选关键打击节点」列表，"
        "给出**当前最佳打击目标**与可执行打法建议。\n\n"
        "请用中文输出（不要使用 JSON、不要用 markdown 代码围栏），按下面小节组织：\n"
        "【首要目标】具体类型/区域 + 选择理由\n"
        "【备选目标】1～2 个及切换条件\n"
        "【兵力与时机】推荐兵种组合、接敌与窗口\n"
        "【风险与规避】反打、误判、防线要点\n\n"
        "总字数约 380～700 字，简洁可执行。\n\n"
        f"[实时态势摘要]\n{situation_text}\n\n"
        f"[候选打击节点]\n{targets_brief}"
    )
    try:
        llm = _build_chat_openai(
            temperature=min(0.55, float(cred["temperature"]) + 0.08),
            timeout=40,
        )
        reply = llm.invoke(prompt)
        raw = _llm_message_text(reply)
        if not raw:
            return "", "llm:none:empty_reply"
        return raw, None
    except Exception as exc:  # noqa: BLE001
        return "", f"llm:error:{type(exc).__name__}"


_KILLCHAIN_DIM_LABEL = {
    "strike": "打击",
    "detect": "探测",
    "defense": "防御",
    "support": "保障",
}


def _normalize_killchain_chains(raw_chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_chains[:24]):
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension") or "strike").strip()
        if dim not in _KILLCHAIN_DIM_LABEL:
            dim = "strike"
        key = str(item.get("key") or f"{dim}-{idx}").strip()
        title = str(item.get("title") or f"{_KILLCHAIN_DIM_LABEL[dim]}链路").strip()
        loc = str(item.get("targetLocation") or "--").strip() or "--"
        out.append(
            {
                "key": key,
                "dimension": dim,
                "dimensionLabel": _KILLCHAIN_DIM_LABEL[dim],
                "title": title,
                "targetLocation": loc,
                "status": str(item.get("status") or "待激活"),
                "confidence": float(item.get("confidence") or 0),
                "summary": str(item.get("summary") or "")[:240],
            }
        )
    return out


def _normalize_killchain_plans(obj: dict[str, Any], chain_pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_plans = obj.get("plans")
    if not isinstance(raw_plans, list):
        return []
    chain_by_key = {str(c["key"]): c for c in chain_pool}
    out: list[dict[str, Any]] = []
    for plan_idx, plan in enumerate(raw_plans[:3]):
        if not isinstance(plan, dict):
            continue
        selected_keys: list[str] = []
        raw_keys = plan.get("chainKeys")
        if isinstance(raw_keys, list):
            selected_keys = [str(k).strip() for k in raw_keys if str(k).strip() in chain_by_key]
        raw_steps = plan.get("enemyStrikeFlow")
        if isinstance(raw_steps, list):
            for st in raw_steps:
                if isinstance(st, dict):
                    ck = str(st.get("chainKey") or "").strip()
                    if ck in chain_by_key and ck not in selected_keys:
                        selected_keys.append(ck)
        if not selected_keys:
            continue

        tasks: list[dict[str, Any]] = []
        for task_idx, chain_key in enumerate(selected_keys):
            ch = chain_by_key[chain_key]
            score = max(0.0, min(100.0, float(ch.get("confidence") or 0)))
            tasks.append(
                {
                    "taskKey": f"llm-{plan_idx + 1}:{task_idx + 1}:{chain_key}",
                    "chainKey": chain_key,
                    "dimension": ch["dimension"],
                    "title": str(ch["title"]),
                    "summary": str(ch.get("summary") or ""),
                    "status": str(ch.get("status") or "待激活"),
                    "confidence": score,
                    "executability": score,
                    "rankScore": score,
                    "targetLocation": str(ch.get("targetLocation") or "--"),
                }
            )

        steps: list[dict[str, Any]] = []
        if isinstance(raw_steps, list):
            for step_idx, st in enumerate(raw_steps[:8]):
                if not isinstance(st, dict):
                    continue
                ck = str(st.get("chainKey") or "").strip()
                if ck not in chain_by_key:
                    continue
                ch = chain_by_key[ck]
                dim = str(st.get("dimension") or ch["dimension"])
                if dim not in _KILLCHAIN_DIM_LABEL:
                    dim = ch["dimension"]
                loc = str(st.get("targetLocation") or ch.get("targetLocation") or "--")
                steps.append(
                    {
                        "order": int(st.get("order") or (step_idx + 1)),
                        "chainKey": ck,
                        "dimension": dim,
                        "title": str(st.get("title") or f"打击{_KILLCHAIN_DIM_LABEL[dim]}链路{loc}"),
                        "targetLocation": loc,
                        "rationale": str(st.get("rationale") or "")[:180],
                    }
                )
        if not steps:
            for idx, task in enumerate(tasks):
                dim = task["dimension"]
                loc = task["targetLocation"]
                steps.append(
                    {
                        "order": idx + 1,
                        "chainKey": task["chainKey"],
                        "dimension": dim,
                        "title": f"打击{_KILLCHAIN_DIM_LABEL[dim]}链路{loc}",
                        "targetLocation": loc,
                        "rationale": task["summary"][:180],
                    }
                )
        steps.sort(key=lambda x: int(x.get("order") or 999))
        avg_score = sum(float(t.get("rankScore") or 0) for t in tasks) / max(1, len(tasks))
        out.append(
            {
                "key": str(plan.get("key") or f"llm-plan-{plan_idx + 1}"),
                "rank": int(plan.get("rank") or (plan_idx + 1)),
                "title": str(plan.get("title") or f"敌链瓦解方案{plan_idx + 1}"),
                "description": str(plan.get("description") or "由大模型基于敌方杀伤链候选池生成。")[:220],
                "planScore": float(plan.get("planScore") or avg_score),
                "chainKeys": selected_keys,
                "tasks": tasks,
                "enemyStrikeFlow": steps,
            }
        )
    return sorted(out, key=lambda x: int(x.get("rank") or 999))


def _killchain_plans_with_llm(
    situation_text: str, chain_pool: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str, str | None]:
    """返回 (plans, 原始模型文本, 失败代号)。"""
    if ChatOpenAI is None:
        return [], "", "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return [], "", "llm:none:no_api_key"
    if not chain_pool:
        return [], "", "llm:none:no_enemy_chains"

    cred = get_swarmbrain_llm_credentials()
    chain_text = json.dumps(chain_pool, ensure_ascii=False, indent=2)
    prompt = (
        "你是《星际争霸II》敌方杀伤链分析与反制规划智能体。\n"
        "重要约束：\n"
        "1. 分析对象是【敌方】杀伤链，不要把我方虫族单位当作杀伤链主体；\n"
        "2. 只能从给定的 enemy_chains 候选池中选择链路，chainKey 必须逐字匹配候选池 key；\n"
        "3. 输出 1 到 3 个作战模式方案；每个方案可从任意维度选择若干链路，同一维度可选多个，也可不选；\n"
        "4. enemyStrikeFlow 必须由你直接给出执行顺序，title 用“打击X链路位置”的中文短句，例如“打击打击链路B1”；\n"
        "5. 只输出合法 JSON，不要 markdown，不要解释。\n\n"
        "JSON 格式严格如下：\n"
        '{"plans":[{"key":"llm-plan-1","rank":1,"title":"分层瓦解方案","description":"一句话说明",'
        '"planScore":86,"chainKeys":["strike-0","support-1"],'
        '"enemyStrikeFlow":[{"order":1,"chainKey":"strike-0","dimension":"strike","title":"打击打击链路B1","targetLocation":"B1","rationale":"选择原因"}]}]}\n\n'
        f"[实时态势]\n{situation_text}\n\n"
        f"[enemy_chains 候选池]\n{chain_text}"
    )
    try:
        llm = _build_chat_openai(
            temperature=min(0.6, float(cred["temperature"]) + 0.08),
            timeout=45,
        )
        reply = llm.invoke(prompt)
        raw = _llm_message_text(reply)
        if not raw:
            return [], "", "llm:none:empty_reply"
        parsed = _extract_json_object(raw)
        if not parsed:
            return [], raw, "llm:none:json_parse"
        plans = _normalize_killchain_plans(parsed, chain_pool)
        if not plans:
            return [], raw, "llm:none:empty_plans"
        return plans, raw, None
    except Exception as exc:  # noqa: BLE001
        return [], "", f"llm:error:{type(exc).__name__}"


app = FastAPI(
    title="SwarmBrain Ask API",
    version="0.1.0",
    default_response_class=Utf8JSONResponse,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str | bool]:
    key_ok = bool(_resolve_llm_api_key())
    cred = get_swarmbrain_llm_credentials()
    return {
        "status": "ok",
        "llm_ready": key_ok and ChatOpenAI is not None,
        "llm_profile": "swarmbrain_llm_shared",
        "llm_model": cred["model"],
        "dotenv_path_exists": (ROOT_DIR / ".env").is_file(),
        "dotenv_has_llm_key_line": dotenv_file_has_llm_key_line(),
        "llm_uses_swarmbrain_embedded_key": llm_api_key_is_embedded_fallback(),
        "llm_heuristic_fallback": (
            os.getenv("SWARMBRAIN_ASK_HEURISTIC", "").strip().lower() in ("1", "true", "yes")
            or not key_ok
        ),
    }


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    traces: list[str] = []
    duel_mode = _is_duel_ask_scope(side=req.side, scope=req.scope)
    red_sit = None
    blue_sit = None

    if duel_mode:
        red_sit, red_source, red_traces = _load_situation_bundle("red")
        blue_sit, blue_source, blue_traces = _load_situation_bundle("blue")
        traces.extend([f"red:{t}" for t in red_traces])
        traces.extend([f"blue:{t}" for t in blue_traces])
        if not red_sit and not blue_sit:
            return AskResponse(
                answer="暂时读取不到红蓝双方态势数据。请先确认对局、bridge 与 Redis 都在运行。",
                tool_traces=traces,
                source="none",
            )
        situation_text = _compact_duel_situation_text(red_sit, blue_sit)
        source = f"duel:{red_source}+{blue_source}"
        # 启发式仅在双侧都缺失结构化回答能力时退回单侧；默认仍优先 LLM
        situation_for_heuristic = red_sit or blue_sit
    else:
        side = _normalize_side(req.side)
        situation, source, load_traces = _load_situation_bundle(side)
        traces.extend(load_traces)
        if not situation:
            return AskResponse(
                answer="暂时读取不到态势数据。请先确认游戏、bridge 与 Redis 都在运行。",
                tool_traces=traces,
                source="none",
            )
        situation_text = _compact_situation_text(situation)
        situation_for_heuristic = situation

    answer, llm_skip = _answer_with_llm(req.question, situation_text, duel_mode=duel_mode)
    if answer:
        traces.append("llm:chat:duel" if duel_mode else "llm:chat")
    else:
        if llm_skip:
            traces.append(llm_skip)
        no_key_diag = llm_skip == "llm:none:no_api_key"
        # 默认禁用启发式；仅无 API Key 时才允许兜底，避免“本地记忆式”回答冒充大模型
        allow_heuristic = no_key_diag or (
            os.getenv("SWARMBRAIN_ASK_HEURISTIC", "").strip().lower() in ("1", "true", "yes")
        )
        heuristic = None
        if allow_heuristic and situation_for_heuristic and not duel_mode:
            heuristic = _answer_heuristic(req.question, situation_for_heuristic)
        elif allow_heuristic and duel_mode:
            # 双侧启发式：分别回答后拼接
            parts: list[str] = []
            if red_sit:
                h = _answer_heuristic(req.question, red_sit)
                if h:
                    parts.append(f"【红方视角】\n{h}")
            if blue_sit:
                h = _answer_heuristic(req.question, blue_sit)
                if h:
                    parts.append(f"【蓝方视角】\n{h}")
            if parts:
                heuristic = "\n\n".join(parts)
        if heuristic:
            answer = heuristic
            traces.append("heuristic:question_routing")
        elif not no_key_diag:
            answer = _answer_with_template(
                req.question, situation_text, llm_was_configured=True, llm_call_failed=True
            )
            traces.append("template:llm_failed")
        else:
            answer = _answer_with_template(
                req.question, situation_text, llm_was_configured=False, llm_call_failed=False
            )
            traces.append("template:fallback")

    return AskResponse(answer=answer, tool_traces=traces, source=source)


@app.post("/api/risk/refresh", response_model=DeerflowRiskResponse)
def refresh_deerflow_risk(req: DeerflowRiskRequest) -> DeerflowRiskResponse:
    side = _normalize_side(req.side)
    mode = str(req.mode or "fast").strip().lower()
    if mode not in {"fast", "pipeline"}:
        mode = "fast"

    # 默认快路径：单次大模型直接产出意图+风险，避免整条 DeerFlow 六阶段阻塞前端刷新
    if mode == "fast":
        return _refresh_intent_risk_fast(side=side, timeout_sec=int(req.timeout_sec))

    thread_id = req.thread_id.strip() or str(uuid.uuid4())
    mode_label = "replay" if req.replay else "live"

    ok, pipeline_out, traces = _run_deerflow_pipeline(
        thread_id=thread_id,
        replay=req.replay,
        timeout_sec=req.timeout_sec,
        side=side,
    )
    traces = ["mode:pipeline", *traces]

    intent: dict[str, Any] = {}
    risk_id: dict[str, Any] = {}
    risk_response: dict[str, Any] = {}
    all_stages: dict[str, Any] = {}

    try:
        r = _redis_client()
        intent = _read_analysis_json(r, thread_id, "intent")
        risk_id = _read_analysis_json(r, thread_id, "risk_id")
        risk_response = _read_analysis_json(r, thread_id, "risk_response")
        for k in ("sensing", "compute", "disposition", "intent", "risk_id", "risk_response"):
            all_stages[k] = _read_analysis_json(r, thread_id, k)
    except Exception as e:
        traces.append(f"redis:error:{e}")

    has_risk_payload = bool(risk_id) or bool(risk_response)
    intent = _normalize_intent_cn(intent)
    risk_id = _normalize_risk_cn(risk_id)

    final_ok = ok and has_risk_payload
    if not ok:
        summary = "流水线执行失败，请先检查 LLM 配置、Redis 和对局数据。"
    elif not has_risk_payload:
        summary = "流水线执行完成，但未读到 risk_id/risk_response 产物（可能模型未调用缓存写入）。"
    else:
        summary = _build_risk_summary_cn(intent, risk_id)

    return DeerflowRiskResponse(
        ok=final_ok,
        thread_id=thread_id,
        mode=mode_label,
        summary=summary,
        intent=intent,
        risk=risk_id,
        response=risk_response,
        stages=all_stages,
        traces=traces,
        pipeline_stdout=pipeline_out[-4000:] if pipeline_out else "",
    )


@app.post("/api/force/llm-advice", response_model=ForceLlmAdviceResponse)
def force_llm_advice(req: ForceLlmAdviceRequest) -> ForceLlmAdviceResponse:
    """基于当前 Redis / dashboard_state 态势调用大模型，生成兵力布势+作战环境五维协同建议（JSON 结构化）。"""
    traces: list[str] = []
    side = _normalize_side(req.side)
    situation = _read_situation_from_redis(side)
    source = f"redis:{side or 'default'}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side or 'default'}"
    if not situation:
        return ForceLlmAdviceResponse(
            ok=False,
            error="暂时读取不到态势数据。请先确认游戏、bridge 与 Redis 都在运行。",
            traces=traces + ["context:none"],
            source="none",
        )
    traces.append(f"context:{source}")
    context_text = _build_force_advice_context_text(situation)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_force_collaboration_advice_with_llm, context_text)
        try:
            sections_raw, answer_raw, llm_err = fut.result(timeout=float(req.timeout_sec))
        except concurrent.futures.TimeoutError:
            return ForceLlmAdviceResponse(
                ok=False,
                error=f"大模型调用超时（{req.timeout_sec}s），请稍后重试或增大 timeout_sec。",
                traces=traces + ["llm:timeout"],
                source=source,
            )

    traces.append("llm:force_collaboration")
    if llm_err:
        traces.append(llm_err)
    if sections_raw:
        return ForceLlmAdviceResponse(
            ok=True,
            sections=[ForceAdviceSection(**s) for s in sections_raw],
            answer_raw=(answer_raw or "")[:8000],
            traces=traces,
            source=source,
        )
    return ForceLlmAdviceResponse(
        ok=False,
        error=str(llm_err or "模型未返回可用的 JSON 建议"),
        answer_raw=(answer_raw or "")[:8000],
        traces=traces,
        source=source,
    )


_ORIENT_DIM_LABELS = {
    "strike": "打击",
    "detect": "探测",
    "defense": "防御",
    "support": "保障",
    "environment": "作战环境",
}


def _orient_flow_titles(dimension: str, *, side: str | None = None) -> list[tuple[str, str]]:
    dim = _ORIENT_DIM_LABELS.get(dimension, "打击")
    own, enemy = _perspective_labels(side)
    if dimension == "environment":
        return [
            ("intent", f"{enemy}地形利用意图预测"),
            ("risk", f"{own}作战环境风险研判"),
            ("vuln", f"{enemy}作战环境脆弱性预测"),
            ("nodes", f"{enemy}关键地形利用预测"),
        ]
    return [
        ("intent", f"{enemy}{dim}意图识别"),
        ("risk", f"我方{dim}风险识别"),
        ("vuln", f"{enemy}{dim}脆弱性分析"),
        ("nodes", f"对{enemy}关键节点分析"),
    ]


def _parse_orient_flow(raw: str, dimension: str, *, side: str | None = None) -> tuple[list[dict[str, str]], str, str]:
    obj = _extract_json_object(raw) or {}
    steps: list[dict[str, str]] = []
    for key, title in _orient_flow_titles(dimension, side=side):
        item = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(item, str):
            text, evidence = item.strip(), ""
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("summary") or "").strip()
            evidence = str(item.get("evidence") or item.get("proof") or "").strip()
        else:
            text, evidence = "", ""
        steps.append({
            "key": key,
            "title": title,
            "text": text[:120],
            "evidence": evidence[:320],
        })
    conclusion = ""
    if isinstance(obj, dict):
        conclusion = str(obj.get("conclusion") or obj.get("answer") or "").strip()
    if not conclusion:
        conclusion = " ".join(s["text"] for s in steps if s.get("text")).strip()
    answer = conclusion or (raw or "").strip()
    return steps, conclusion, answer


def _build_orient_understand_question(
    dimension: str, question: str, history: list[dict[str, Any]], context_text: str, *, side: str | None = None
) -> str:
    dim = _ORIENT_DIM_LABELS.get(dimension, "打击")
    own, enemy = _perspective_labels(side)
    follow = str(question or "").strip()
    hist_lines: list[str] = []
    for item in (history or [])[-6:]:
        if not isinstance(item, dict):
            continue
        role = "用户" if str(item.get("role") or "") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if content:
            hist_lines.append(f"{role}：{content[:500]}")
    history_block = "\n".join(hist_lines)
    context_block = str(context_text or "").strip()[:6000]
    chunks = [
        f"你是态势认知问答智能体。只围绕{enemy}的【{dim}】维作答。",
    ]
    if follow:
        chunks.append("用户要就刚才的理解继续追问，请紧扣上一轮结论和当前态势，不要跑题。")
    else:
        if dimension == "environment":
            nodes_hint = f"{enemy}关键地形利用预测：{enemy}如何用地形，不超过48字"
        else:
            nodes_hint = f"对{enemy}关键节点分析，不超过48字"
        chunks.append(
            "请根据当前态势，按流程理解" + enemy + "该维行为与作战模式。"
            "只输出一个 JSON 对象，不要其它文字或代码围栏。字段如下：\n"
            f'{{"intent":{{"text":"{enemy}{dim}意图识别：{enemy}该维想做什么，不超过48字","evidence":"2-4个证据要点，用中文分号分隔，共不超过160字"}},'
            f'"risk":{{"text":"我方{dim}风险识别：我方打这一维会不会吃亏（兵力对比、敌抗击、推进损失），不是{enemy}脆弱，不超过48字","evidence":"2-4个证据要点，用中文分号分隔，共不超过160字"}},'
            f'"vuln":{{"text":"{enemy}{dim}脆弱性分析，不超过48字","evidence":"2-4个证据要点，用中文分号分隔，共不超过160字"}},'
            f'"nodes":{{"text":"{nodes_hint}","evidence":"2-4个证据要点，用中文分号分隔，共不超过160字"}},'
            f'"conclusion":"对{enemy}{dim}态势的理解，3-6句，写清{enemy}想做什么、我方打这一维是否吃亏、何处可打"}}\n'
            "text 必须短；evidence 必须具体：写可见编制名称与数量、双方对照、落点格子、协同关系，不要编造未见单位。"
        )
    if context_block:
        chunks.append("【本维已解析要点】\n" + context_block)
    if follow and history_block:
        chunks.append("【最近对话】\n" + history_block)
    if follow:
        chunks.append("【用户追问】\n" + follow)
    return "\n\n".join(chunks)


@app.post("/api/orient/understand", response_model=OrientUnderstandResponse)
def orient_understand(req: OrientUnderstandRequest) -> OrientUnderstandResponse:
    """仪表盘态势认知问答：定时理解蓝方行为/作战模式，并支持用户追问。不改 SwarmBrain 对局输入。"""
    traces: list[str] = []
    side = _normalize_side(req.side)
    dimension = str(req.dimension or "strike").strip().lower()
    if dimension in {"attack", "atk"}:
        dimension = "strike"
    if dimension in {"env", "terrain"}:
        dimension = "environment"
    if dimension not in _ORIENT_DIM_LABELS:
        dimension = "strike"
    kind = "followup" if str(req.question or "").strip() else "briefing"
    situation = _read_situation_from_redis(side)
    source = f"redis:{side or 'default'}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side or 'default'}"
    if not situation:
        return OrientUnderstandResponse(
            ok=False,
            kind=kind,
            dimension=dimension,
            error="暂时读取不到态势数据。请先确认游戏、bridge 与 Redis 都在运行。",
            traces=traces + ["context:none"],
            source="none",
        )
    traces.append(f"context:{source}")
    situation_text = _compact_situation_text(situation)
    prompt_question = _build_orient_understand_question(
        dimension, req.question, req.history or [], req.context, side=side
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_answer_with_llm, prompt_question, situation_text, duel_mode=False)
        try:
            answer, llm_skip = fut.result(timeout=float(req.timeout_sec))
        except concurrent.futures.TimeoutError:
            return OrientUnderstandResponse(
                ok=False,
                kind=kind,
                dimension=dimension,
                error=f"大模型调用超时（{req.timeout_sec}s），请稍后重试。",
                traces=traces + ["llm:timeout"],
                source=source,
            )

    if answer:
        traces.append("llm:orient_understand")
        steps_raw, conclusion, chat_answer = _parse_orient_flow(answer, dimension, side=side) if kind == "briefing" else ([], "", answer)
        if kind == "briefing" and any(step.get("text") for step in steps_raw):
            traces.append("parse:orient_flow")
        return OrientUnderstandResponse(
            ok=True,
            answer=chat_answer or answer,
            kind=kind,
            dimension=dimension,
            traces=traces,
            source=source,
            steps=[OrientUnderstandStep(**step) for step in steps_raw],
            conclusion=conclusion,
        )
    if llm_skip:
        traces.append(llm_skip)
    fallback = _answer_with_template(
        prompt_question,
        situation_text,
        llm_was_configured=llm_skip != "llm:none:no_api_key",
        llm_call_failed=True,
    )
    traces.append("template:fallback")
    return OrientUnderstandResponse(
        ok=False,
        answer=fallback,
        kind=kind,
        dimension=dimension,
        error=str(llm_skip or "模型未返回可用理解"),
        traces=traces,
        source=source,
    )


@app.post("/api/advice/strike-targets", response_model=StrikeTargetsAdviceResponse)
def strike_targets_advice(req: StrikeTargetsAdviceRequest) -> StrikeTargetsAdviceResponse:
    """基于 Redis / dashboard_state 态势 + 前端传入的关键节点列表，生成大模型打击目标建议。"""
    traces: list[str] = []
    side = _normalize_side(req.side)
    situation = _read_situation_from_redis(side)
    source = f"redis:{side or 'default'}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side or 'default'}"
    if not situation:
        return StrikeTargetsAdviceResponse(
            ok=False,
            error="暂时读取不到态势数据。请先确认游戏、bridge 与 Redis / 写盘都在运行。",
            traces=traces + ["context:none"],
            source="none",
        )

    traces.append(f"context:{source}")
    situation_text = _compact_situation_text(situation)

    tg = req.targets[:24]
    if tg:
        try:
            targets_brief = json.dumps(tg, ensure_ascii=False, indent=2)
        except Exception:
            targets_brief = str(tg)
    else:
        targets_brief = "（当前无结构化关键节点条目，请仅凭态势摘要推断可操作打击方向。）"

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_strike_targets_advice_with_llm, situation_text, targets_brief)
        try:
            advice_raw, llm_err = fut.result(timeout=float(req.timeout_sec))
        except concurrent.futures.TimeoutError:
            return StrikeTargetsAdviceResponse(
                ok=False,
                error=f"大模型调用超时（{req.timeout_sec}s），请稍后重试或增大 timeout_sec。",
                traces=traces + ["llm:timeout"],
                source=source,
            )

    traces.append("llm:strike_targets")
    if llm_err:
        traces.append(llm_err)
    if advice_raw.strip():
        return StrikeTargetsAdviceResponse(ok=True, advice=advice_raw[:12000], traces=traces, source=source)
    return StrikeTargetsAdviceResponse(
        ok=False,
        advice="",
        error=str(llm_err or "模型未返回正文"),
        traces=traces,
        source=source,
    )


@app.post("/api/killchain/plans", response_model=KillChainPlansResponse)
def killchain_plans(req: KillChainPlansRequest) -> KillChainPlansResponse:
    """基于敌方杀伤链候选池调用大模型，生成作战模式与敌链破坏步骤流。"""
    traces: list[str] = []
    side = _normalize_side(req.side)
    situation = _read_situation_from_redis(side)
    source = f"redis:{side or 'default'}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side or 'default'}"
    if not situation:
        return KillChainPlansResponse(
            ok=False,
            error="暂时读取不到态势数据。请先确认游戏、bridge 与 Redis / 写盘都在运行。",
            traces=traces + ["context:none"],
            source="none",
        )
    chain_pool = _normalize_killchain_chains(req.chains)
    if not chain_pool:
        return KillChainPlansResponse(
            ok=False,
            error="敌方杀伤链候选池为空：当前视野内没有足够敌方链路对象。",
            traces=traces + [f"context:{source}", "chains:none"],
            source=source,
        )

    traces.append(f"context:{source}")
    traces.append(f"chains:{len(chain_pool)}")
    situation_text = _build_force_advice_context_text(situation)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_killchain_plans_with_llm, situation_text, chain_pool)
        try:
            plans_raw, answer_raw, llm_err = fut.result(timeout=float(req.timeout_sec))
        except concurrent.futures.TimeoutError:
            return KillChainPlansResponse(
                ok=False,
                error=f"大模型调用超时（{req.timeout_sec}s），请稍后重试或增大 timeout_sec。",
                traces=traces + ["llm:timeout"],
                source=source,
            )

    traces.append("llm:killchain_plans")
    if llm_err:
        traces.append(llm_err)
    if plans_raw:
        return KillChainPlansResponse(
            ok=True,
            plans=[KillChainPlan(**p) for p in plans_raw],
            answer_raw=(answer_raw or "")[:12000],
            traces=traces,
            source=source,
        )
    return KillChainPlansResponse(
        ok=False,
        error=str(llm_err or "模型未返回可用的作战模式 JSON"),
        answer_raw=(answer_raw or "")[:12000],
        traces=traces,
        source=source,
    )


@app.post("/api/cot/selfplay", response_model=CotSelfplayResponse)
def cot_selfplay(req: CotSelfplayRequest) -> CotSelfplayResponse:
    """按当前态势生成一轮自博弈思维链（红方方案→对蓝方的预测→红方反思→红方应对）。"""
    traces: list[str] = []
    side = _normalize_side(req.side) or "red"
    situation = _read_situation_from_redis(side)
    source = f"redis:{side}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side}"
    if not situation:
        return CotSelfplayResponse(
            ok=False,
            error="暂时读取不到态势数据。请先确认对局、Redis 或 dashboard_state 写盘。",
            traces=traces + ["context:none"],
            source="none",
            game_time=req.game_time,
        )

    traces.append(f"context:{source}")
    game_time = _situation_clock(situation, req.game_time)
    context_text = _build_force_advice_context_text(situation)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(
            _selfplay_cot_with_llm,
            context_text,
            game_time=game_time,
            previous_conclusion=req.previous_conclusion,
            side=side,
        )
        try:
            loop_raw, answer_raw, llm_err = fut.result(timeout=float(req.timeout_sec))
        except concurrent.futures.TimeoutError:
            return CotSelfplayResponse(
                ok=False,
                error=f"大模型调用超时（{req.timeout_sec}s），请稍后重试。",
                traces=traces + ["llm:timeout"],
                source=source,
                game_time=_live_game_clock(side, game_time),
            )

    stamp = _live_game_clock(side, game_time)
    traces.append("llm:cot_selfplay")
    if llm_err:
        traces.append(llm_err)
    if loop_raw:
        try:
            loop = CotSelfplayLoop(**loop_raw)
        except Exception as exc:  # noqa: BLE001
            return CotSelfplayResponse(
                ok=False,
                error=f"思维链结构校验失败：{type(exc).__name__}",
                answer_raw=(answer_raw or "")[:12000],
                traces=traces + ["llm:schema"],
                source=source,
                game_time=stamp,
            )
        return CotSelfplayResponse(
            ok=True,
            loop=loop,
            game_time=stamp,
            answer_raw=(answer_raw or "")[:12000],
            traces=traces,
            source=source,
        )
    return CotSelfplayResponse(
        ok=False,
        error=str(llm_err or "模型未返回可用的思维链 JSON"),
        answer_raw=(answer_raw or "")[:12000],
        traces=traces,
        source=source,
        game_time=stamp,
    )


@app.post("/api/cot/env-selfplay", response_model=CotEnvSelfplayResponse)
def cot_env_selfplay(req: CotEnvSelfplayRequest) -> CotEnvSelfplayResponse:
    """按当前态势生成一轮兵力布势自博弈（扁平 summary/detail，无四维 dims）。"""
    traces: list[str] = []
    side = _normalize_side(req.side) or "red"
    situation = _read_situation_from_redis(side)
    source = f"redis:{side}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side}"
    if not situation:
        return CotEnvSelfplayResponse(
            ok=False,
            error="暂时读取不到态势数据。请先确认对局、Redis 或 dashboard_state 写盘。",
            traces=traces + ["context:none"],
            source="none",
            game_time=req.game_time,
        )

    traces.append(f"context:{source}")
    game_time = _situation_clock(situation, req.game_time)
    context_text = _build_force_advice_context_text(situation)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(
            _env_selfplay_cot_with_llm,
            context_text,
            game_time=game_time,
            previous_conclusion=req.previous_conclusion,
            side=side,
        )
        try:
            loop_raw, answer_raw, llm_err = fut.result(timeout=float(req.timeout_sec))
        except concurrent.futures.TimeoutError:
            return CotEnvSelfplayResponse(
                ok=False,
                error=f"大模型调用超时（{req.timeout_sec}s），请稍后重试。",
                traces=traces + ["llm:timeout"],
                source=source,
                game_time=_live_game_clock(side, game_time),
            )

    stamp = _live_game_clock(side, game_time)
    traces.append("llm:cot_env_selfplay")
    if llm_err:
        traces.append(llm_err)
    if loop_raw:
        try:
            loop = CotEnvSelfplayLoop(**loop_raw)
        except Exception as exc:  # noqa: BLE001
            return CotEnvSelfplayResponse(
                ok=False,
                error=f"布势思维链结构校验失败：{type(exc).__name__}",
                answer_raw=(answer_raw or "")[:12000],
                traces=traces + ["llm:schema"],
                source=source,
                game_time=stamp,
            )
        return CotEnvSelfplayResponse(
            ok=True,
            loop=loop,
            game_time=stamp,
            answer_raw=(answer_raw or "")[:12000],
            traces=traces,
            source=source,
        )
    return CotEnvSelfplayResponse(
        ok=False,
        error=str(llm_err or "模型未返回可用的布势思维链 JSON"),
        answer_raw=(answer_raw or "")[:12000],
        traces=traces,
        source=source,
        game_time=stamp,
    )


@app.post("/api/cot/killnet", response_model=CotKillnetResponse)
def cot_killnet(req: CotKillnetRequest) -> CotKillnetResponse:
    """把本轮红方应对落成杀伤网，节点文案尽量与应对方案一致。"""
    traces: list[str] = []
    side = _normalize_side(req.side) or "red"
    situation = _read_situation_from_redis(side)
    source = f"redis:{side}"
    if not situation:
        situation = _build_situation_from_dashboard_state(side)
        source = f"dashboard_state:{side}"
    traces.append(f"context:{source if situation else 'none'}")
    game_time = str(req.game_time or "").strip()
    if not game_time and situation:
        try:
            game_time = str((situation.get("raw") or {}).get("time_formatted") or "")
        except Exception:
            game_time = ""
    context_text = _build_force_advice_context_text(situation) if situation else f"（当前无态势摘要，仅依据{_perspective_labels(side)[0]}应对构图。）"
    fallback = _killnet_from_final_plan(req.final_plan, game_time, req.environment_plan)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(
            _cot_killnet_with_llm,
            context_text,
            req.final_plan,
            game_time=game_time,
            situation_digest=req.situation_digest,
            environment_plan=req.environment_plan,
            side=side,
        )
        try:
            net_raw, answer_raw, llm_err = fut.result(timeout=float(req.timeout_sec))
        except concurrent.futures.TimeoutError:
            return CotKillnetResponse(
                ok=True,
                killnet=_cot_killnet_payload(None, fallback),
                game_time=game_time,
                traces=traces + ["llm:timeout", "fallback:final_plan"],
                source=source if situation else "final_plan",
                error=f"大模型超时（{req.timeout_sec}s），已先用红方应对落网。",
            )

    traces.append("llm:cot_killnet")
    if llm_err:
        traces.append(llm_err)
        return CotKillnetResponse(
            ok=True,
            killnet=_cot_killnet_payload(None, fallback),
            game_time=game_time,
            answer_raw=(answer_raw or "")[:8000],
            traces=traces + ["fallback:final_plan"],
            source=source if situation else "final_plan",
            error=str(llm_err),
        )
    return CotKillnetResponse(
        ok=True,
        killnet=_cot_killnet_payload(net_raw, fallback),
        game_time=game_time,
        answer_raw=(answer_raw or "")[:8000],
        traces=traces,
        source=source if situation else "final_plan",
    )

