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

STATE_PATH = ROOT_DIR / "dashboard_state.json"
KEY_SITUATION = "situation:current"


def _resolve_llm_api_key() -> str:
    """与 `swarmbrain.build_model` 同源：swarmbrain_llm_shared（环境变量 / .env / 与对局一致的内嵌末位兜底）。"""
    return str(get_swarmbrain_llm_credentials()["api_key"]).strip()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(default="default")


class AskResponse(BaseModel):
    answer: str
    tool_traces: list[str]
    source: str


class DeerflowRiskRequest(BaseModel):
    replay: bool = True
    timeout_sec: int = Field(default=500, ge=30, le=600)
    thread_id: str = Field(default="")


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


class StrikeTargetsAdviceRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=15, le=180)
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


def _run_deerflow_pipeline(*, thread_id: str, replay: bool, timeout_sec: int) -> tuple[bool, str, list[str]]:
    cmd = [sys.executable, "run_sc2_deerflow_pipeline.py", "--thread-id", thread_id]
    if replay:
        cmd.append("--replay")
    traces = [f"pipeline:cmd={' '.join(cmd)}", f"pipeline:timeout={timeout_sec}s"]
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
        desc = str(meta.get("description_cn") or "").strip()
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


def _read_situation_from_redis() -> dict[str, Any] | None:
    try:
        r = _redis_client()
        raw = r.get(KEY_SITUATION)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _build_situation_from_dashboard_state() -> dict[str, Any] | None:
    try:
        if not STATE_PATH.exists():
            return None
        with STATE_PATH.open("r", encoding="utf-8") as f:
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
    question: str, situation_text: str
) -> tuple[str | None, str | None]:
    """(回答文本, 未走通 LLM 时的诊断代号)，代号仅写入 tool_traces。"""
    if ChatOpenAI is None:
        return None, "llm:none:langchain_openai_missing"
    api_key = _resolve_llm_api_key()
    if not api_key:
        return None, "llm:none:no_api_key"

    cred = get_swarmbrain_llm_credentials()
    model_name = str(cred["model"])
    base_url = str(cred["base_url"])
    temperature = float(cred["temperature"])
    try:
        llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )
        prompt = (
            "你是星际争霸II战术分析助手。请基于实时态势回答用户问题，"
            "输出要简洁、可执行，优先给2-4条建议。\n\n"
            f"[实时态势]\n{situation_text}\n\n"
            f"[用户问题]\n{question}"
        )
        reply = llm.invoke(prompt)
        content = getattr(reply, "content", "")
        if isinstance(content, str) and content.strip():
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
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        k = str(item.get("key") or "").strip() or "support"
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

    cred = get_swarmbrain_llm_credentials()
    prompt = (
        "你是《星际争霸II》高水平参谋，根据下列实时战场摘要，为指挥官生成「兵力布势」维度的协同建议。\n"
        "请只输出一段合法 JSON（不要用 markdown 代码围栏，不要输出其它说明文字），格式严格如下：\n"
        '{"sections":['
        '{"key":"strike","title":"打击类建议","summary":"一句话摘要","detail":"2-4条可执行要点"},'
        '{"key":"defense","title":"防御类建议","summary":"...","detail":"..."},'
        '{"key":"detect","title":"侦察探测建议","summary":"...","detail":"..."},'
        '{"key":"support","title":"保障类建议","summary":"...","detail":"..."}'
        "]}\n"
        "要求：summary 每条不超过 80 字；detail 每条不超过 220 字；用语简洁、可执行。\n\n"
        f"[战场态势]\n{context_text}"
    )
    try:
        llm = ChatOpenAI(
            model=str(cred["model"]),
            api_key=api_key,
            base_url=str(cred["base_url"]),
            temperature=min(0.65, float(cred["temperature"]) + 0.05),
        )
        reply = llm.invoke(prompt)
        raw = getattr(reply, "content", "")
        if not isinstance(raw, str) or not raw.strip():
            return [], raw if isinstance(raw, str) else "", "llm:none:empty_reply"
        parsed = _extract_json_object(raw)
        if not parsed:
            return [], raw, "llm:none:json_parse"
        sections = _normalize_force_advice_sections(parsed)
        if not sections:
            return [], raw, "llm:none:empty_sections"
        return sections, raw, None
    except Exception as exc:  # noqa: BLE001
        return [], "", f"llm:error:{type(exc).__name__}"


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
        llm = ChatOpenAI(
            model=str(cred["model"]),
            api_key=api_key,
            base_url=str(cred["base_url"]),
            temperature=min(0.55, float(cred["temperature"]) + 0.08),
        )
        reply = llm.invoke(prompt)
        raw = getattr(reply, "content", "")
        if not isinstance(raw, str) or not raw.strip():
            return "", "llm:none:empty_reply"
        return raw.strip(), None
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
        llm = ChatOpenAI(
            model=str(cred["model"]),
            api_key=api_key,
            base_url=str(cred["base_url"]),
            temperature=min(0.6, float(cred["temperature"]) + 0.08),
        )
        reply = llm.invoke(prompt)
        raw = getattr(reply, "content", "")
        if not isinstance(raw, str) or not raw.strip():
            return [], raw if isinstance(raw, str) else "", "llm:none:empty_reply"
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
    allow_credentials=True,
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

    situation = _read_situation_from_redis()
    source = "redis"
    traces.append("sc2_get_situation(redis)")
    if not situation:
        situation = _build_situation_from_dashboard_state()
        source = "dashboard_state.json"
        traces.append("fallback:dashboard_state.json")
    if not situation:
        return AskResponse(
            answer="暂时读取不到态势数据。请先确认游戏、bridge 与 Redis 都在运行。",
            tool_traces=traces,
            source="none",
        )

    situation_text = _compact_situation_text(situation)

    answer, llm_skip = _answer_with_llm(req.question, situation_text)
    if answer:
        traces.append("llm:chat")
    else:
        if llm_skip:
            traces.append(llm_skip)
        no_key_diag = llm_skip == "llm:none:no_api_key"
        allow_heuristic = (
            os.getenv("SWARMBRAIN_ASK_HEURISTIC", "").strip().lower() in ("1", "true", "yes")
            or no_key_diag
        )
        heuristic = None
        if allow_heuristic:
            heuristic = _answer_heuristic(req.question, situation)
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
    thread_id = req.thread_id.strip() or str(uuid.uuid4())
    mode = "replay" if req.replay else "live"

    ok, pipeline_out, traces = _run_deerflow_pipeline(
        thread_id=thread_id,
        replay=req.replay,
        timeout_sec=req.timeout_sec,
    )

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
        mode=mode,
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
    """基于当前 Redis / dashboard_state 态势调用大模型，生成兵力布势四维协同建议（JSON 结构化）。"""
    traces: list[str] = []
    situation = _read_situation_from_redis()
    source = "redis"
    if not situation:
        situation = _build_situation_from_dashboard_state()
        source = "dashboard_state.json"
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


@app.post("/api/advice/strike-targets", response_model=StrikeTargetsAdviceResponse)
def strike_targets_advice(req: StrikeTargetsAdviceRequest) -> StrikeTargetsAdviceResponse:
    """基于 Redis / dashboard_state 态势 + 前端传入的关键节点列表，生成大模型打击目标建议。"""
    traces: list[str] = []
    situation = _read_situation_from_redis()
    source = "redis"
    if not situation:
        situation = _build_situation_from_dashboard_state()
        source = "dashboard_state.json"
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
    situation = _read_situation_from_redis()
    source = "redis"
    if not situation:
        situation = _build_situation_from_dashboard_state()
        source = "dashboard_state.json"
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

