"""
对局（swarmbrain.build_model）与 Ask API（ask_api_server）共用的 LLM 连接参数。
读取顺序：进程环境变量（含 python-dotenv 注入）→ 项目根 .env 文件。

请勿在源码中保存真实 Key；仅使用 .env / 环境变量配置。
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


def _api_key_vars_for_base_url(base_url: str) -> tuple[str, ...]:
    if "dashscope.aliyuncs.com" in base_url.lower():
        return ("SWARMBRAIN_LLM_API_KEY", "DASHSCOPE_API_KEY")
    return ("SWARMBRAIN_LLM_API_KEY", "OPENAI_API_KEY")


def dotenv_file_has_llm_key_line() -> bool:
    """仅看 `.env` 文件里是否配置了非空 LLM Key（不判环境变量）。"""
    dot = _dotenv_snapshot()
    return any(
        isinstance(dot.get(k), str) and str(dot.get(k) or "").strip() for k in _API_KEY_VARS
    )


def get_swarmbrain_llm_credentials() -> dict[str, str | float]:
    """供 swarmbrain.build_model 与 ask_api_server ChatOpenAI 调用。"""
    dot = _dotenv_snapshot()
    model = _merged_env_or_dot(dot, "SWARMBRAIN_LLM_MODEL") or _DEFAULT_MODEL
    base_url = _merged_env_or_dot(dot, "SWARMBRAIN_LLM_BASE_URL") or _DEFAULT_BASE_URL

    api_key = ""
    for k in _api_key_vars_for_base_url(base_url):
        api_key = _merged_env_or_dot(dot, k)
        if api_key:
            break
    # 若仍为空：常见原因是把 Key 写在 .env 却被「空字符串环境变量」挡住；再用 override 载入一次并重算。
    if not api_key and _DOTENV_PATH.is_file():
        try:
            from dotenv import load_dotenv, dotenv_values

            load_dotenv(_DOTENV_PATH, override=True)
            dot = dict(dotenv_values(_DOTENV_PATH) or {})
            for k in _api_key_vars_for_base_url(base_url):
                api_key = _merged_env_or_dot(dot, k)
                if api_key:
                    break
        except ImportError:
            pass

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
        "api_key": api_key or "missing-api-key",
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
    }


def llm_api_key_is_embedded_fallback() -> bool:
    """Legacy compatibility helper; embedded API keys are no longer supported."""
    return False


def llm_api_key_is_configured() -> bool:
    dot = _dotenv_snapshot()
    base_url = _merged_env_or_dot(dot, "SWARMBRAIN_LLM_BASE_URL") or _DEFAULT_BASE_URL
    return any(bool(_merged_env_or_dot(dot, k)) for k in _api_key_vars_for_base_url(base_url))
