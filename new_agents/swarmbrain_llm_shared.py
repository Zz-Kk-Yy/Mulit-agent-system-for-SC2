"""
对局（swarmbrain.build_model）与 Ask API（ask_api_server）共用的 LLM 连接参数。
读取顺序：进程环境变量（含 python-dotenv 注入）→ 项目根 .env 文件 → 与历史 swarmbrain 一致的内嵌末位兜底。

公开仓库请勿提交真实 Key；生产环境请仅用 .env / 环境变量覆盖。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
_rp = str(ROOT_DIR)
if _rp not in sys.path:
    sys.path.insert(0, _rp)
_DOTENV_PATH = ROOT_DIR / ".env"

_API_KEY_VARS = ("SWARMBRAIN_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")

_DEFAULT_MODEL = "qwen3-max"
_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_EMBEDDED_DEV_API_KEY_FALLBACK = ""

try:
    from dotenv import load_dotenv

    load_dotenv(_DOTENV_PATH)
except ImportError:
    pass


def _dotenv_snapshot() -> dict[str, str | None]:
    if not _DOTENV_PATH.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    raw = dotenv_values(_DOTENV_PATH)
    return dict(raw) if raw else {}


def _merged_env_or_dot(dot: dict[str, str | None], name: str) -> str:
    v = os.getenv(name, "").strip()
    if v:
        return v
    r = dot.get(name)
    return r.strip() if isinstance(r, str) and r.strip() else ""


def dotenv_file_has_llm_key_line() -> bool:
    """仅看 `.env` 文件里是否配置了非空 LLM Key（不判环境变量）。"""
    dot = _dotenv_snapshot()
    return any(
        isinstance(dot.get(k), str) and str(dot.get(k) or "").strip() for k in _API_KEY_VARS
    )


def get_swarmbrain_llm_credentials() -> dict[str, str | float]:
    """供 swarmbrain.build_model 与 ask_api_server ChatOpenAI 调用。"""
    dot = _dotenv_snapshot()

    api_key = ""
    for k in _API_KEY_VARS:
        api_key = _merged_env_or_dot(dot, k)
        if api_key:
            break
    if not api_key:
        api_key = _EMBEDDED_DEV_API_KEY_FALLBACK.strip()
    # 若仍为空：常见原因是把 Key 写在 .env 却被「空字符串环境变量」挡住；再用 override 载入一次并重算。
    if not api_key and _DOTENV_PATH.is_file():
        try:
            from dotenv import load_dotenv, dotenv_values

            load_dotenv(_DOTENV_PATH, override=True)
            dot = dict(dotenv_values(_DOTENV_PATH) or {})
            for k in _API_KEY_VARS:
                api_key = _merged_env_or_dot(dot, k)
                if api_key:
                    break
        except ImportError:
            pass
        if not api_key:
            api_key = _EMBEDDED_DEV_API_KEY_FALLBACK.strip()

    model = _merged_env_or_dot(dot, "SWARMBRAIN_LLM_MODEL") or _DEFAULT_MODEL
    base_url = _merged_env_or_dot(dot, "SWARMBRAIN_LLM_BASE_URL") or _DEFAULT_BASE_URL

    ts = _merged_env_or_dot(dot, "SWARMBRAIN_LLM_TEMPERATURE")
    if ts:
        try:
            temperature = float(ts)
        except ValueError:
            temperature = 0.5
    else:
        try:
            temperature = float(os.getenv("SWARMBRAIN_LLM_TEMPERATURE", "0.5"))
        except ValueError:
            temperature = 0.5

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
    }


def llm_api_key_is_embedded_fallback() -> bool:
    """在未通过 env/.env 提供任一 LLM Key 时使用内嵌兜底。"""
    if any(os.getenv(k, "").strip() for k in _API_KEY_VARS):
        return False
    dot = _dotenv_snapshot()
    return not any(bool(_merged_env_or_dot(dot, k)) for k in _API_KEY_VARS)
