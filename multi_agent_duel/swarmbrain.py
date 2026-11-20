import time
from datetime import datetime
# from langchain.chat_models import AzureChatOpenAI
from langchain_openai import ChatOpenAI
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swarmbrain_llm_shared import get_swarmbrain_llm_credentials, llm_api_key_is_configured
from swarmbrain_strategic_plan import STRATEGIC_PLAN_PROMPT_APPENDIX


executor = ThreadPoolExecutor(max_workers=4)


def _strategic_plan_prompt_appendix() -> str:
    """Keep historical behavior by default; allow independent unlabeled runs."""
    enabled = os.getenv("SWARMBRAIN_ATTACH_STRATEGIC_PLAN_PROMPT", "1").strip().lower()
    return STRATEGIC_PLAN_PROMPT_APPENDIX if enabled not in {"0", "false", "no", "off"} else ""


# Duty-desk narrative language. Command triples stay English for the executor.
_DUTY_DESK_OUTPUT_LANGUAGE = """
---Duty-desk language (must follow)
Write every analysis paragraph, conclusion, risk note, and scouting comment in Simplified Chinese.
Keep the numbered RESPONSE FORMAT headings in English exactly as listed.
When mentioning units, buildings, or tech in analysis, write Chinese then English in parentheses, e.g. 刺蛇(Roach), 破坏者(Ravager), 工蜂(Drone), 孵化场(Hatchery), 刺蛇穴(Hydralisk Den).
Keep map cells as English labels: A1, B2.
Executable command triples MUST remain ASCII English only, e.g. (Zergling)->(Attack)->(B1). Never translate Command Target, Action, or Target inside a triple. Comments after // may be Chinese.
In strategic_plan JSON: primary_intent / secondary_intents stay English closed-set labels; intent_rationale_short must be Simplified Chinese.
---
"""


def build_model(model_conf="", extra={}):
    # return AzureChatOpenAI(
    #     **extra,
    #     openai_api_base="",
    #     openai_api_version="",
    #     model_version="",
    #     deployment_name="",
    #     openai_api_key="",
    #     openai_api_type="",
    #     temperature=0.5,
    #     # response_format={"type": "json_object"}
    # )

    # qwen-turbo
    # qwen-plus
    # qwen-max
    # model_name = os.getenv("SWARMBRAIN_LLM_MODEL", "qwen3-max")
    # base_url = os.getenv("SWARMBRAIN_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    # api_key = (
    #     os.getenv("SWARMBRAIN_LLM_API_KEY")
    #     or os.getenv("DASHSCOPE_API_KEY")
    #     or os.getenv("OPENAI_API_KEY")
    # )
    # if not api_key:
    #     raise ValueError(
    #         "Missing LLM API key. Set env SWARMBRAIN_LLM_API_KEY "
    #         "(or DASHSCOPE_API_KEY / OPENAI_API_KEY)."
    #     )
    # temperature = float(os.getenv("SWARMBRAIN_LLM_TEMPERATURE", "0.5"))
    p = get_swarmbrain_llm_credentials()
    return ChatOpenAI(
        **extra,
        model=p["model"],
        api_key=p["api_key"],
        base_url=p["base_url"],
        temperature=float(p["temperature"]),
    )

    # ChatOpenAI(
    #     **extra,
    #     model="deepseek-chat",  # <--- 修改点 1：使用 DeepSeek 的主力对话模型（V3版本）
    #     api_key="sk-你新申请的DeepSeek的Key",  # <--- 修改点 2：填入你在 DeepSeek 生成的 Key
    #     base_url="https://api.deepseek.com",  # <--- 修改点 3：这是 DeepSeek 官方的接口地址
    #     temperature=0.5,
    # )

model_gpt35_turbo = build_model()
model_gpt4 = build_model()
model_gpt4_32k = build_model()
model_gpt4_turbo = build_model()


_DEFAULT_STAGE_INFO = """Early stage:
Zergling: 24
Baneling: 6
Roach: 8
Ravager: 0
Hydralisk: 0
Infestor: 0
Swarm host: 0
Mutalisk: 0
Corruptor: 0
Viper: 0
Ultralisk: 0
Brood Lord: 0

Mid stage:
Zergling: 32
Baneling: 12
Roach: 16
Ravager: 6
Hydralisk: 12
Infestor: 4
Swarm host: 0
Mutalisk: 0
Corruptor: 0
Viper: 2
Ultralisk: 0
Brood Lord: 0

Late stage:
Zergling: 20
Baneling: 8
Roach: 0
Ravager: 0
Hydralisk: 24
Infestor: 6
Swarm host: 8
Mutalisk: 0
Corruptor: 6
Viper: 4
Ultralisk: 6
Brood Lord: 8
"""

_DEFAULT_INITIAL_STRATEGY = """Question 1: True
Question 2: True
"""


_llm_circuit_lock = threading.Lock()
_llm_circuit_reason = "" if llm_api_key_is_configured() else "LLM API key is not configured"


def llm_requests_enabled() -> bool:
    return not bool(_llm_circuit_reason)


def _is_permanent_llm_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "invalid_api_key" in text or "incorrect api key" in text or "authentication" in text


def _invoke_with_retries(model, prompt: str, attempts: int = 2, delay_seconds: float = 1.0):
    global _llm_circuit_reason
    if _llm_circuit_reason:
        raise RuntimeError(_llm_circuit_reason)

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return model.invoke(prompt), attempt - 1
        except Exception as exc:
            last_error = exc
            print(f"[llm] invoke failed ({attempt}/{attempts}): {exc}", flush=True)
            if _is_permanent_llm_auth_error(exc):
                with _llm_circuit_lock:
                    _llm_circuit_reason = "LLM authentication failed; configure a valid API key"
                raise RuntimeError(_llm_circuit_reason) from exc
            if attempt < attempts:
                time.sleep(delay_seconds * attempt)
    raise last_error


def _extract_token_usage(message) -> dict | None:
    usage = getattr(message, "usage_metadata", None) or getattr(message, "response_metadata", None)
    if not isinstance(usage, dict):
        return None
    # LangChain / OpenAI compatible shapes
    if "input_tokens" in usage or "output_tokens" in usage:
        return {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    token_usage = usage.get("token_usage") if isinstance(usage.get("token_usage"), dict) else usage
    if isinstance(token_usage, dict) and (
        "prompt_tokens" in token_usage or "completion_tokens" in token_usage or "total_tokens" in token_usage
    ):
        return {
            "prompt_tokens": token_usage.get("prompt_tokens"),
            "completion_tokens": token_usage.get("completion_tokens"),
            "total_tokens": token_usage.get("total_tokens"),
        }
    return None


def _run_logged_invoke(model, prompt: str, if_print_log: bool = False) -> str:
    """Invoke model; defer complete until caller finishes parse (P0-3). Identity frozen at start (P0-2)."""
    from swarmbrain_run_logger import get_llm_call_context

    ctx = get_llm_call_context() or {}
    logger = ctx.get("logger")
    channel = ctx.get("channel") or "other"
    llm_call_id = None
    start = time.time()
    if logger is not None:
        obs_loop = ctx.get("observation_game_loop", ctx.get("game_loop"))
        obs_sec = ctx.get("observation_game_time_sec", ctx.get("game_time_sec"))
        # request_started ≈ wall clock of HTTP invoke; game clock = latest known (may equal obs if sync).
        req_loop = logger._last_known_game_loop if logger._last_known_game_loop is not None else obs_loop
        req_sec = logger._last_known_game_time_sec if logger._last_known_game_time_sec is not None else obs_sec
        start_kwargs = {
            "channel": channel,
            "prompt": prompt,
            "input_state": ctx.get("input_state") or {},
            "previous_commands": ctx.get("previous_commands") or [],
            "prompt_template_version": ctx.get("prompt_template_version"),
            "model_name": getattr(model, "model_name", None),
            "game_loop": obs_loop,
            "game_time_sec": obs_sec,
            "game_time_formatted": ctx.get("game_time_formatted"),
            "observation_game_loop": obs_loop,
            "observation_game_time_sec": obs_sec,
            "request_started_game_loop": req_loop,
            "request_started_game_time_sec": req_sec,
            "agent_id": ctx.get("agent_id"),
            "agent_side": ctx.get("agent_side"),
            "player_id": ctx.get("player_id"),
            "race": ctx.get("race", "Zerg"),
            "feature_scope": ctx.get("feature_scope") or "label_only",
        }
        if ctx.get("phase") is not None:
            start_kwargs["phase"] = ctx.get("phase")
        if ctx.get("opening_index") is not None:
            start_kwargs["opening_index"] = ctx.get("opening_index")
        llm_call_id = logger.start_llm_call(**start_kwargs)
        ctx["llm_call_id"] = llm_call_id
        # Keep open-call request clock if still missing
        frozen = logger._open_calls.get(llm_call_id)
        if isinstance(frozen, dict):
            frozen["request_started_game_loop"] = req_loop
            frozen["request_started_game_time_sec"] = req_sec
    try:
        message, retry_count = _invoke_with_retries(model, prompt)
        content = message.content if hasattr(message, "content") else str(message)
        if if_print_log:
            print(content)
        latency_ms = (time.time() - start) * 1000.0
        if logger is not None and llm_call_id:
            finish_reason = None
            meta = getattr(message, "response_metadata", None)
            if isinstance(meta, dict):
                finish_reason = meta.get("finish_reason") or meta.get("stop_reason")
            # Defer complete so parsed_commands can be attached (P0-3).
            # Do not pass observation clock here — stash uses logger last_known as response_completed.
            if ctx.get("defer_complete", True):
                logger.stash_llm_response(
                    llm_call_id,
                    raw_response=content,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason or "stop",
                    token_usage=_extract_token_usage(message),
                    retry_count=retry_count,
                )
            else:
                logger.complete_llm_call(
                    llm_call_id,
                    raw_response=content,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason or "stop",
                    token_usage=_extract_token_usage(message),
                    game_loop=logger._last_known_game_loop,
                    game_time_sec=logger._last_known_game_time_sec,
                    retry_count=retry_count,
                    commands_expected=ctx.get("commands_expected"),
                )
        return content
    except Exception as exc:
        if logger is not None and llm_call_id:
            logger.fail_llm_call(
                llm_call_id,
                error=exc,
                retry_count=0,
                fallback_used=bool(ctx.get("expect_fallback")),
                game_loop=ctx.get("game_loop"),
            )
        raise


def llm_gpt4(prompt: str, if_print_log: bool = False):
    return __llm_call(prompt, model_gpt4, if_print_log)


def llm_gpt4_32k(prompt: str, if_print_log: bool = False):
    return __llm_call(prompt, model_gpt4_32k, if_print_log)


def llm_gpt4_turbo(prompt: str, if_print_log: bool = False):
    return __llm_call(prompt, model_gpt4_turbo, if_print_log)


async def llm_gpt35_turbo(prompt: str, if_print_log: bool = False):
    return await __llm_call_async(prompt, model_gpt35_turbo, if_print_log)


def llm_gpt35_turbo2(prompt: str, if_print_log: bool = False):
    return __llm_call2(prompt, model_gpt35_turbo, if_print_log)


def __llm_call2(prompt: str, model, if_print_log: bool = False):
    print('\033[34m' + "executing GPT------------------" + '\033[0m')
    print(f"Model Name: {model.model_name}")
    print(f"Model Version: Official")
    return _run_logged_invoke(model, prompt, if_print_log=if_print_log)


async def __llm_call_async(prompt: str, model, if_print_log: bool = False):
    print('\033[34m' + "executing GPT------------------" + '\033[0m')
    print(f"Model Name: {model.model_name}")
    print(f"Model Version: Official")
    # ThreadPoolExecutor does not inherit contextvars; copy so run-logger context reaches the worker.
    import contextvars

    loop = asyncio.get_event_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(
        executor,
        lambda: ctx.run(_run_logged_invoke, model, prompt, if_print_log),
    )


def __llm_call(prompt: str, model, if_print_log: bool = False):
    print('\033[34m' + "executing GPT------------------" + '\033[0m')
    print(f"Model Name: {model.model_name}")
    print(f"Model Version: Official")
    return _run_logged_invoke(model, prompt, if_print_log=if_print_log)

# 一.兵力目标初始化
def overmind_brain_initial():
    prompt = '''
You are an intelligent brain of Zerg swarm in StarCraft II game.
You are very aggressive and know all the dependencies between Zerg units, Zerg buildings, and Zerg technological research.

Your task is to analyze how many different Zerg units are needed at different stages of the game when facing powerful Terran players

---rule
The value given must be an exact value, for instance:
Zergling: 20
---

For instance:
RESPONSE FORMAT:

Early stage:
Zergling: [num]
Baneling: [num]
Roach: [num]
Ravager: [num]
Hydralisk: [num]
Infestor: [num]
Swarm host: [num]
Mutalisk: [num]
Corruptor: [num]
Viper: [num]
Ultralisk: [num]
Brood Lord: [num]

Mid stage:
Zergling: [num]
Baneling: [num]
Roach: [num]
Ravager: [num]
Hydralisk: [num]
Infestor: [num]
Swarm host: [num]
Mutalisk: [num]
Corruptor: [num]
Viper: [num]
Ultralisk: [num]
Brood Lord: [num]

Late stage:
Zergling: [num]
Baneling: [num]
Roach: [num]
Ravager: [num]
Hydralisk: [num]
Infestor: [num]
Swarm host: [num]
Mutalisk: [num]
Corruptor: [num]
Viper: [num]
Ultralisk: [num]
Brood Lord: [num]
    '''

    try:
        output1 = llm_gpt35_turbo2(prompt, True)
    except Exception as exc:
        print(f"[llm] initial unit plan failed; using default stage plan: {exc}", flush=True)
        output1 = _DEFAULT_STAGE_INFO

    return output1

# 二.极端战术倾向设定
def overmind_brain_initial2():
    prompt = '''
You are an intelligent brain of Zerg swarm in StarCraft II game.
You are very aggressive and know all the dependencies between Zerg units, Zerg buildings, and Zerg technological research.

Now, you need to answer the following questions:

Question 1: When the enemy's army launched an attack on your base, and you sent out all the attacking units, but the whole army was wiped out, you still have some Drones mining, will you send out all your Drones at this time to make the final resistance? If yes, answer True, otherwise answer False.

Question 2: When the enemy's army launched an attack on your base, you sent out all the attacking units, and all the enemy troops were defeated. At this time, you still have some attacking units. Will you send out your attacking units to counterattack the enemy's base? If yes, answer True, otherwise answer False.

RESPONSE FORMAT:
Question 1:
Question 2:
    '''

    try:
        output1 = llm_gpt35_turbo2(prompt, True)
    except Exception as exc:
        print(f"[llm] initial strategy flags failed; using defaults: {exc}", flush=True)
        output1 = _DEFAULT_INITIAL_STRATEGY

    return output1


# 三.开局首轮指令生成
def overmind_brain_1():
    prompt = '''
You are an intelligent brain of Zerg swarm in StarCraft II game.
You are very aggressive and know all the dependencies between Zerg units, Zerg buildings, and Zerg technological research.
Your task is to generate construction commands for Zerg buildings.

You and Terran players are on a square map with 16 mines evenly distributed on the map, as shown in the following matrix:
[[0, B7, B6, B4, B2],
[A8, 0, B5, B3, B1],
[A1, A3, A5, 0, B8],
[A2, A4, A6, A7, 0]]
Currently, your base is at position A1 and the Terran base is at position B1. 0 represents no mine, and other letters represent the mine number.

---Current battle situation
game time: 00:00

The Units you currently have are: 12 Drone, 1 Overlord.
The Buildings you currently have are: 1 Hatchery

The Enemy Units you curently detect are: Nothing.
The Enemy Buildings you curently detect are: Nothing.

---

--- rule
You need to analyze the game progression by following a structured protocol based on the game situation. Make sure to include the following perspectives in your analysis:
1. Current Stage of the Match: Determine the current game stage based on the game time and types of our units, whether it’s early, middle, or late stage.
2. The Condition of Our Forces: Assess our current status in dimensions of:
2.1 Our Zerg Units and Buildings: Scrutinize the state of Zerg Units and Buildings.
2.2 Our Zerg Technology: Analyze the current status of the Zerg technological research based on the unlocked research.
3. Our current Zerg Operational Strategy: Devise a reasonable strategy based on our current situation, opponent’s situation, and scouting intel.
4. Opponent's situation: Assess opponent’s current status in dimensions of:
4.1 Opponent's Units and Buildings: Analyze state of opponent’s Units and Buildings.
4.1 Opponent's Tactical Plan and our Potential Risks: Based on detected opponent’s Units and Buildings, predict the opponent's attack timing to prepare defensive measures in advance.
5. Scouting Intel: Stress the importance of recent and consistent scouting reports to stay updated on the enemy's unit composition, positioning, and possible incoming attacks or expansions.
6. Eliminate Repeated instructions: Based on "Your commands in previous round", analysis which commands are not needed to execute again.
7. Avoid problematic commands: Analysis the problematic commands in "Avoid problematic commands", and avoid the problematic commands this time.
---

---Game Key Decisions Memory
The key decisions in your memory, you can refer to these decisions for strategic deployment.
{{
Based on previous competition experience, you found that:
0. Remember to build more hatchery to expend the economy, like (Drone, A1)->(Build)->(Hatchery, A1), (Drone, A1)->(Build)->(Hatchery, A2), (Drone, A1)->(Build)->(Hatchery, A3).
1. Remember to build Baneling Nest to enable morph Baneling to cause more damage.
2. You can morph many Zerglings to Banelings to cause more damage to the enemy's Marine, Marauder and Siege tanks.
3. You can morph many Roaches to Ravagers to cause more damage to the enemy's Marine, Marauder and Siege tanks.
4. Zergling/Baneling/Roach/Ravager is ONLY an early opener. After about 6 minutes you MUST leave this composition; tanks, ravens and battlecruisers beat low-tier units.
5. Correct tech path: Spawning Pool unlocks Lair. Morph Lair with (Hatchery, A1)->(Build)->(Lair). Infestation Pit REQUIRES an existing Lair; it does NOT unlock Lair. Never build Infestation Pit before Lair.
6. Hydralisk Den requires Lair. After Lair, immediately build Hydralisk Den and produce Hydralisks: (Drone, A1)->(Build)->(Hydralisk Den, A1).
7. Hive requires Lair + Infestation Pit: (Lair, A1)->(Build)->(Hive). Ultralisk Cavern requires Hive.
8. Spire requires Lair. Greater Spire requires Hive plus an existing Spire; then morph Corruptors to Brood Lords. Do not order Greater Spire first.
9. When minerals are banked and scouting shows expansions are reasonably safe, take a 3rd and 4th Hatchery at valid map labels from the matrix, and build Extractors at each new base so vespene keeps pace with army and tech.
}}
---

---Self-Verification (Avoid problematic commands)
These are the experiences you have gained through self-verification, please try to avoid these situations.
{{
'0': (Drone, A1)->(Expand Creep)->(A2)
Problem: Since "Drone" cannot expand creep (only Queens can), this instruction is unreasonable.
'1': (Drone, A1)->(Build)->(Queen Nest, A1)
Problem: This instruction is unreasonable. Drones cannot build Queen Nests; they are constructed by Drones transforming into the structure.
'2': (Drone, A1)->(Build)->(Overlord)
Problem: The problem is that "Drone" cannot hatch "Overlord", only "Larva" can hatch "Overlord".
'3': (Drone, A1)->(Build)->(Drone)
Problem: The problem is that "Drone" cannot hatch "Drone", only "Larva" can hatch "Drone".
}}
---

---Your commands in previous round
{{
(none)
}}
---

Based on the current battle situation and Units and Buildings from both sides, following the rules above, write a brief step-by-step analysis in Simplified Chinese from our strategy, Units and Buildings, economic, technical perspectives, And "Your commands in last round".
Then, formulate up to 20 actionable, specific decisions (at most 20; include the target location, like A1, A2, etc.) from the following action list.
These decisions should be numbered from 0, denoting the order in which they ought to be executed, with 0 signifying the most immediate and crucial action. Do not output more than 20 numbered commands.

Note:
1. Game Key Decisions Memory is the key decisions in your memory, you can refer to these decisions for strategic deployment.
2. Self-Verification (Avoid problematic commands) is the experiences you have gained through self-verification, please try to avoid these situations.
3. Please reflect the 'Game Key Decisions Memory' and 'Self-Verification (Avoid problematic commands)' in the analysis of the results.
4. "Command Target" means the unit you want to command, such as "Drone", "Overlord", etc.
"Action" means the actions you want the unit to do, such as "Move", "Morph", etc.
"Target" means the objection, like "A1". Please see the example.
There's an example:
{{
‘0’: (Zergling, A1)->(Move)->(A4), //把 A1 的小狗(Zergling)调往 A4
‘1’: (Drone, A1)->(Gather gas)->(Extractor1, A1) //派 A1 工蜂(Drone)去该点萃取房采气
‘2’: (Zergling, A1)->(Morph)->(Baneling) //把 A1 小狗(Zergling)变形成毒爆虫(Baneling)
‘3’: (Hatchery, A1)->(Build)->(Lair) //孵化池就绪后把孵化场(Hatchery)升巢穴(Lair)；须早于感染深渊或刺蛇穴
...
}}
''' + _DUTY_DESK_OUTPUT_LANGUAGE + '''
For instance:
RESPONSE FORMAT:
1. Current Stage of the Match;
2. Our Zerg Units and Buildings;
3. Our current Zerg Operational Strategy;
4. Opponent's Units and Buildings;
5. Opponent's Tactical Plan and our Potential Risks;
6. Scouting Intel;
7. Analyze based on 'Game Key Decisions Memory' and 'Self-Verification (Avoid problematic commands)'.
8. Based on above analysis, each instruction should follow the below format:
{{
'0': (Command Target)->(Action)->(Target),
'1': (Command Target)->(Action)->(Target),
'2': (Command Target)->(Action)->(Target),
...
}}

        '''
    output1 = llm_gpt35_turbo2(prompt, True)

    return output1

# 四.游戏主循环指令生成
async def overmind_brain_iter(game_time, current_units, current_buildings, current_tech, current_enemy_units,
                              current_enemy_buildings, previous_commands):
    prompt = '''
You are an intelligent brain of Zerg swarm in StarCraft II game.
You are very aggressive and know all the dependencies between Zerg units, Zerg buildings, and Zerg technological research.
You and Terran players are on a square map with 16 mines evenly distributed on the map, as shown in the following matrix:
[[0, B7, B6, B4, B2],
[A8, 0, B5, B3, B1],
[A1, A3, A5, 0, B8],
[A2, A4, A6, A7, 0]]
Currently, your base is at position A1 and the Terran base is at position B1. 0 represents no mine, and other letters represent the mine number.

---Current battle situation
game time: {game_time}

Your current Units consists of:
{{
    {current_units}
    ...
   Omitting means no units
}}

Your current Buildings consists of :
{{
    {current_buildings}
    ...
    Omitting means no Buildings
}}

Your Zerg has developed these technological research:
{{
    {current_tech}
}}

You have detected enemy units in:
{{
    {current_enemy_units}
    ...
   Omitting means no units
}}

You have detected enemy buildings in:
{{
    {current_enemy_buildings}
    ...
   Omitting means no Buildings
}}

---

--- rule
You need to analyze the game progression by following a structured protocol based on the game situation. Make sure to include the following perspectives in your analysis:
1. Current Stage of the Match: Determine the current game stage based on the game time and types of our units, whether it’s early, middle, or late stage.
2. The Condition of Our Forces: Assess our current status in dimensions of:
2.1 Our Zerg Units and Buildings: Scrutinize the state of Zerg Units and Buildings.
2.2 Our Zerg Technology: Analyze the current status of the Zerg technological research based on the unlocked research.
3. Our current Zerg Operational Strategy: Devise a reasonable strategy based on our current situation, opponent’s situation, and scouting intel.
4. Opponent's situation: Assess opponent’s current status in dimensions of:
4.1 Opponent's Units and Buildings: Analyze state of opponent’s Units and Buildings.
4.1 Opponent's Tactical Plan and our Potential Risks: Based on detected opponent’s Units and Buildings, predict the opponent's attack timing to prepare defensive measures in advance.
5. Scouting Intel: Stress the importance of recent and consistent scouting reports to stay updated on the enemy's unit composition, positioning, and possible incoming attacks or expansions.
6. Eliminate Repeated instructions: Based on "Your commands in previous round", analysis which commands are not needed to execute again.
7. Avoid problematic commands: Analysis the problematic commands in "Avoid problematic commands", and avoid the problematic commands this time.
---

---Game Key Decisions Memory
The key decisions in your memory, you can refer to these decisions for strategic deployment.
{{
Based on previous competition experience, you found that:
1. Remember to build Baneling Nest to enable morph Baneling to cause more damage.
2. You can morph many Zerglings to Banelings to cause more damage to the enemy's Marine, Marauder and Siege tanks.
3. You can morph many Roaches to Ravagers to cause more damage to the enemy's Marine, Marauder and Siege tanks.
4. Zergling/Baneling/Roach/Ravager is ONLY an early opener. After about 6 minutes you MUST leave this composition; tanks, ravens and battlecruisers beat low-tier units.
5. Correct tech path: Spawning Pool unlocks Lair. Morph Lair with (Hatchery, A1)->(Build)->(Lair). Infestation Pit REQUIRES an existing Lair; it does NOT unlock Lair. Never build Infestation Pit before Lair.
6. Hydralisk Den requires Lair. After Lair, immediately build Hydralisk Den and produce Hydralisks: (Drone, A1)->(Build)->(Hydralisk Den, A1).
7. Hive requires Lair + Infestation Pit: (Lair, A1)->(Build)->(Hive). Ultralisk Cavern requires Hive.
8. Spire requires Lair. Greater Spire requires Hive plus an existing Spire; then morph Corruptors to Brood Lords. Do not order Greater Spire first.
9. When minerals are banked and scouting shows expansions are reasonably safe, take a 3rd and 4th Hatchery at valid map labels from the matrix, and build Extractors at each new base so vespene keeps pace with army and tech.
}}
---

---Self-Verification (Avoid problematic commands)
These are the experiences you have gained through self-verification, please try to avoid these situations.
{{
'0': (Drone, A1)->(Expand Creep)->(A2)
Problem: Since "Drone" cannot expand creep (only Queens can), this instruction is unreasonable.
'1': (Drone, A1)->(Build)->(Queen Nest, A1)
Problem: This instruction is unreasonable. Drones cannot build Queen Nests; they are constructed by Drones transforming into the structure.
'2': (Drone, A1)->(Build)->(Overlord)
Problem: The problem is that "Drone" cannot hatch "Overlord", only "Larva" can hatch "Overlord".
'3': (Drone, A1)->(Build)->(Drone)
Problem: The problem is that "Drone" cannot hatch "Drone", only "Larva" can hatch "Drone".
}}
---

---Your commands in previous round
{{
{previous_commands}
}}
---

Based on the current battle situation and Units and Buildings from both sides, following the rules above, write a brief step-by-step analysis in Simplified Chinese from our strategy, Units and Buildings, economic, technical perspectives, And "Your commands in last round".
Then, formulate up to 20 actionable, specific decisions (at most 20; include the target location, like A1, A2, etc.) from the following action list.
These decisions should be numbered from 0, denoting the order in which they ought to be executed, with 0 signifying the most immediate and crucial action. Do not output more than 20 numbered commands.

Note:
1. Game Key Decisions Memory is the key decisions in your memory, you can refer to these decisions for strategic deployment.
2. Self-Verification (Avoid problematic commands) is the experiences you have gained through self-verification, please try to avoid these situations.
3. Please reflect the 'Game Key Decisions Memory' and 'Self-Verification (Avoid problematic commands)' in the analysis of the results.
4. "Command Target" means the unit you want to command, such as "Drone", "Overlord", etc.
"Action" means the actions you want the unit to do, such as "Move", "Morph", etc.
"Target" means the objection, like "A1". Please see the example.
There's an example:
{{
‘0’: (Zergling, A1)->(Move)->(A4), //把 A1 的小狗(Zergling)调往 A4
‘1’: (Drone, A1)->(Gather gas)->(Extractor1, A1) //派 A1 工蜂(Drone)去该点萃取房采气
‘2’: (Zergling, A1)->(Morph)->(Baneling) //把 A1 小狗(Zergling)变形成毒爆虫(Baneling)
‘3’: (Hatchery, A1)->(Build)->(Lair) //孵化池就绪后把孵化场(Hatchery)升巢穴(Lair)；须早于感染深渊或刺蛇穴
...
}}
''' + _DUTY_DESK_OUTPUT_LANGUAGE + '''
For instance:
RESPONSE FORMAT:
1. Current Stage of the Match;
2. Our Zerg Units and Buildings;
3. Our current Zerg Operational Strategy;
4. Opponent's Units and Buildings;
5. Opponent's Tactical Plan and our Potential Risks;
6. Scouting Intel;
7. Analyze based on 'Game Key Decisions Memory' and 'Self-Verification (Avoid problematic commands)'.
8. Based on above analysis, each instruction should follow the below format:
{{
'0': (Command Target)->(Action)->(Target),
'1': (Command Target)->(Action)->(Target),
'2': (Command Target)->(Action)->(Target),
...
}}

    ''' + _strategic_plan_prompt_appendix()
    prompt = prompt.format(game_time=game_time, current_units=current_units, current_buildings=current_buildings,
                           current_tech=current_tech, current_enemy_units=current_enemy_units,
                           current_enemy_buildings=current_enemy_buildings,
                           previous_commands=previous_commands or "(none)")
    print("prompt:\n", prompt)
    output = await llm_gpt35_turbo(prompt, True)

    return output

# 五.专属建筑扩张循环
async def overmind_building_iter(game_time, current_units, current_buildings, current_enemy_units,
                                 current_enemy_buildings, previous_commands):
    prompt = '''
You are an intelligent brain of Zerg swarm in StarCraft II game.
You are very aggressive and know all the dependencies between Zerg units, Zerg buildings, and Zerg technological research.
Your task is to generate construction commands for Zerg buildings.

You and Terran players are on a square map with 16 mines evenly distributed on the map, as shown in the following matrix:
[[0, B7, B6, B4, B2],
[A8, 0, B5, B3, B1],
[A1, A3, A5, 0, B8],
[A2, A4, A6, A7, 0]]
Currently, your base is at position A1 and the Terran base is at position B1. 0 represents no mine, and other letters represent the mine number.

---Current battle situation
game time: {game_time}

The Units you currently have are: {current_units}
The Buildings you currently have are: {current_buildings}

The Enemy Units you curently detect are: {current_enemy_units}
The Enemy Buildings you curently detect are: {current_enemy_buildings}
---

--- rule
You need to analyze the game progression by following a structured protocol based on the game situation. Make sure to include the following perspectives in your analysis:
1. Current Stage of the Match: Determine the current game stage based on the game time and types of our units, whether it’s early, middle, or late stage.
2. The Condition of Our Forces: Assess our current status in dimensions of:
2.1 Our Zerg Units and Buildings: Scrutinize the state of Zerg Units and Buildings.
2.2 Our Zerg Technology: Analyze the current status of the Zerg technological research based on the unlocked research.
3. Our current Zerg Operational Strategy: Devise a reasonable strategy based on our current situation, opponent’s situation, and scouting intel.
4. Opponent's situation: Assess opponent’s current status in dimensions of:
4.1 Opponent's Units and Buildings: Analyze state of opponent’s Units and Buildings.
4.1 Opponent's Tactical Plan and our Potential Risks: Based on detected opponent’s Units and Buildings, predict the opponent's attack timing to prepare defensive measures in advance.
5. Scouting Intel: Stress the importance of recent and consistent scouting reports to stay updated on the enemy's unit composition, positioning, and possible incoming attacks or expansions.
6. Eliminate Repeated instructions: Based on "Your commands in previous round", analysis which commands are not needed to execute again.
7. Avoid problematic commands: Analysis the problematic commands in "Avoid problematic commands", and avoid the problematic commands this time.
---

---Game Key Decisions Memory
The key decisions in your memory, you can refer to these decisions for strategic deployment.
{{
Based on previous competition experience, you found that:
0. Remember to build more hatchery to expend the economy, like (Drone, A1)->(Build)->(Hatchery, A1), (Drone, A1)->(Build)->(Hatchery, A2), (Drone, A1)->(Build)->(Hatchery, A3).
1. Remember to build Baneling Nest to enable morph Baneling to cause more damage.
2. You can morph many Zerglings to Banelings to cause more damage to the enemy's Marine, Marauder and Siege tanks.
3. You can morph many Roaches to Ravagers to cause more damage to the enemy's Marine, Marauder and Siege tanks.
4. Zergling/Baneling/Roach/Ravager is ONLY an early opener. After about 6 minutes you MUST leave this composition; tanks, ravens and battlecruisers beat low-tier units.
5. Correct tech path: Spawning Pool unlocks Lair. Morph Lair with (Hatchery, A1)->(Build)->(Lair). Infestation Pit REQUIRES an existing Lair; it does NOT unlock Lair. Never build Infestation Pit before Lair.
6. Hydralisk Den requires Lair. After Lair, immediately build Hydralisk Den and produce Hydralisks: (Drone, A1)->(Build)->(Hydralisk Den, A1).
7. Hive requires Lair + Infestation Pit: (Lair, A1)->(Build)->(Hive). Ultralisk Cavern requires Hive.
8. Spire requires Lair. Greater Spire requires Hive plus an existing Spire; then morph Corruptors to Brood Lords. Do not order Greater Spire first.
9. When minerals are banked and scouting shows expansions are reasonably safe, take a 3rd and 4th Hatchery at valid map labels from the matrix, and build Extractors at each new base so vespene keeps pace with army and tech.
}}

---Self-Verification (Avoid problematic commands)
These are the experiences you have gained through self-verification, please try to avoid these situations.
{{
'0': (Drone, A1)->(Expand Creep)->(A2)
Problem: Since "Drone" cannot expand creep (only Queens can), this instruction is unreasonable.
'1': (Drone, A1)->(Build)->(Queen Nest, A1)
Problem: This instruction is unreasonable. Drones cannot build Queen Nests; they are constructed by Drones transforming into the structure.
'2': (Drone, A1)->(Build)->(Overlord)
Problem: The problem is that "Drone" cannot hatch "Overlord", only "Larva" can hatch "Overlord".
'3': (Drone, A1)->(Build)->(Drone)
Problem: The problem is that "Drone" cannot hatch "Drone", only "Larva" can hatch "Drone".
}}
---

---Your commands in previous round
{{
{previous_commands}
}}
---

Based on the current battle situation and Units and Buildings from both sides, following the rules above, write a brief step-by-step analysis in Simplified Chinese from our strategy, Units and Buildings, economic, technical perspectives, And "Your commands in last round".
Then, formulate up to 20 actionable, specific decisions (at most 20; include the target location, like A1, A2, etc.) from the following action list.
These decisions should be numbered from 0, denoting the order in which they ought to be executed, with 0 signifying the most immediate and crucial action. Do not output more than 20 numbered commands.

Note:
1. Game Key Decisions Memory is the key decisions in your memory, you can refer to these decisions for strategic deployment.
2. Self-Verification (Avoid problematic commands) is the experiences you have gained through self-verification, please try to avoid these situations.
3. Please reflect the 'Game Key Decisions Memory' and 'Self-Verification (Avoid problematic commands)' in the analysis of the results.
4. "Command Target" means the unit you want to command, such as "Drone", "Overlord", etc.
"Action" means the actions you want the unit to do, such as "Move", "Morph", etc.
"Target" means the objection, like "A1". Please see the example.
There's an example:
{{
‘0’: (Zergling, A1)->(Move)->(A4), //把 A1 的小狗(Zergling)调往 A4
‘1’: (Drone, A1)->(Gather gas)->(Extractor1, A1) //派 A1 工蜂(Drone)去该点萃取房采气
‘2’: (Zergling, A1)->(Morph)->(Baneling) //把 A1 小狗(Zergling)变形成毒爆虫(Baneling)
‘3’: (Hatchery, A1)->(Build)->(Lair) //孵化池就绪后把孵化场(Hatchery)升巢穴(Lair)；须早于感染深渊或刺蛇穴
...
}}
''' + _DUTY_DESK_OUTPUT_LANGUAGE + '''
For instance:
RESPONSE FORMAT:
1. Current Stage of the Match;
2. Our Zerg Units and Buildings;
3. Our current Zerg Operational Strategy;
4. Opponent's Units and Buildings;
5. Opponent's Tactical Plan and our Potential Risks;
6. Scouting Intel;
7. Analyze based on 'Game Key Decisions Memory' and 'Self-Verification (Avoid problematic commands)'.
8. Based on above analysis, each instruction should follow the below format:
{{
'0': (Command Target)->(Action)->(Target),
'1': (Command Target)->(Action)->(Target),
'2': (Command Target)->(Action)->(Target),
...
}}
    ''' + _strategic_plan_prompt_appendix()
    prompt = prompt.format(game_time=game_time, current_units=current_units, current_buildings=current_buildings,
                           current_enemy_units=current_enemy_units,
                           current_enemy_buildings=current_enemy_buildings,
                           previous_commands=previous_commands or "(none)")
    print("Building prompt:\n", prompt)
    output = await llm_gpt35_turbo(prompt, True)

    return output

# 六.进攻时机研判
async def overmind_attack_module(game_time, current_units, army_damage, current_enemy_units,
                                 current_enemy_buildings, enemy_damage):
    prompt = '''
You are an intelligent brain of Zerg swarm in StarCraft II game.
You are very aggressive and know all the dependencies between Zerg units, Zerg buildings, and Zerg technological research.

---Current battle situation
game time: {game_time}

You have: {current_units}.
The total damage of your army: {army_damage}.
Enemy units you currently detect are: {current_enemy_units}.
The Enemy buildings you currently detect are: {current_enemy_buildings}.
The total damage of enemy army: {enemy_damage}.

---

--- rule
You need to analyze the suitable time to attack enemy by following a structured protocol based on the game situation. Make sure to include the following perspectives in your analysis:
1. Our Zerg Units: Based on the current match stage, analyze whether the current status of Zerg units to suitable to launch an attack.
2. Enemy's Units: First analyze the enemy's units , and then analyze whether the our Zerg units can defeat the enemy's units.
3. Enemy's Buildings: Based on the enemy's buildings, analyze the enemy's strategy.
4 Current Stage of the Match: Determine the current game stage based on the game time and types of our units, whether it’s early, middle, or late stage.
5. Whether to attack: True if it is appropriate to launch an attack now, False otherwise.
---

---Game Key Decisions Memory
The key decisions in your memory, you can refer to these decisions for strategic deployment.
{{
Based on previous competition experience, you found that:
0. Do NOT attack before 6:00 unless the enemy is already inside our base.
1. Only answer True if we have a real army (about 28+ combat units, including Roaches or Hydralisks), not just Zerglings/Banelings.
2. If we have not detected enemy army units or production buildings, answer False and hold at home.
3. If we see Siege Tanks, Thors, Ravens, Bunkers or Battlecruisers and we cannot clearly outnumber them, answer False and defend.
4. Ling/bane all-ins into a Terran wall lose. Wait for Hydralisks before committing a push.
}}
---

Based on the Units and Buildings from both sides, following the rules above, write a brief step-by-step analysis in Simplified Chinese from Zerg units and buildings, technical perspectives, and Enemy's units and buildings.

Note:
1. Game Key Decisions Memory is the key decisions in your memory, you can refer to these decisions for strategic deployment.
2. Item 5 must start with the English token True or False. If holding, write False and do not write the word True anywhere else. After that token, explain in Chinese.
''' + _DUTY_DESK_OUTPUT_LANGUAGE + '''
For instance:
RESPONSE FORMAT:
1. Our Zerg Units;
2. Enemy's Units;
3. Enemy's Buildings;
4. Current Stage of the Match;
5. Whether to attack;
6. True (if it's the suitable time to attack, otherwise is false)
    ''' + _strategic_plan_prompt_appendix()
    prompt = prompt.format(game_time=game_time, current_units=current_units, army_damage=army_damage,
                           current_enemy_units=current_enemy_units,
                           current_enemy_buildings=current_enemy_buildings,
                           enemy_damage=enemy_damage)
    print("Attack prompt:\n", prompt)
    output = await llm_gpt35_turbo(prompt, True)

    return output


if __name__ == '__main__':
    situation__ = '''
    You are an intelligent brain of Zerg swarm in StarCraft II game.
    You are very aggressive and know all the dependencies between Zerg units, Zerg buildings, and Zerg technological research.
    You and Terran players are on a square map with 16 mines evenly distributed on the map, as shown in the following matrix:
    [[0, B7, B6, B4, B2],
    [A8, 0, B5, B3, B1],
    [A1, A3, A5, 0, B8],
    [A2, A4, A6, A7, 0]]
    Currently, your base is at position A1 and the Terran base is at position B1. 0 represents no mine, and other letters represent the mine number.

    ---Current battle situation
    game time: 00:35
    Your current Units consists of:
    {
    At point A1, there are: 16 Drones are gathering minerals in Hatchery, 1 Overlord are idling;
	...
	Omitting means no units
    }

    Your current Buildings consists of :
    {
    At point A1, there are: 1 Hatchery, 1 Extractor, 1 Evolution Chamber;
    ...
    Omitting means no Buildings
    }

    Your Zerg has developed these technological research:
    {
    None
    }

    You have detected enemy units in:
    {
    At point B1, there are: Unknown;
    ...
    Omitting means no units
    }

    You have detected enemy buildings in:
    {
    At point B1, there are: Unknown;
    ...
    Omitting means no Buildings
    }
    ---

    --- rule
    You need to analyze the game progression by following a structured protocol based on the game situation. Make sure to include the following perspectives in your analysis:
    1. Current Stage of the Match: Determine the current game stage based on our situation and opponent’s status you detected, whether it’s early, middle, or late stage.
    2. The Condition of Our Forces: Assess our current status in dimensions of:
    2.1 Our Zerg Units and Buildings: Scrutinize the state of Zerg Units and Buildings.
    2.2 Our Zerg Technology: Analyze the current status of the Zerg technological research based on the unlocked research.
    technology tree based on the Zerg Units and Buildings
    3. Our current Zerg Operational Strategy: Devise a reasonable strategy based on our current situation, opponent’s situation, and scouting intel.
    4. Opponent's situation: Assess opponent’s current status in dimensions of:
    4.1 Opponent's Units and Buildings: Analyze state of opponent’s Units and Buildings.
    4.1 Opponent's Tactical Plan and our Potential Risks: Based on detected opponent’s Units and Buildings, predict the opponent's attack timing to prepare defensive measures in advance.
    5. Scouting Intel: Stress the importance of recent and consistent scouting reports to stay updated on the enemy's unit composition, positioning, and possible incoming attacks or expansions.
    6. Eliminate Repeated instructions: Based on "Your commands in previous round", analysis which commands are not needed to execute again.
    7. Avoid problematic commands: Analysis the problematic commands in "Your problematic commands in previous rounds", and avoid the problematic commands this time.
    ---

    ---Your commands in previous round
    {

    }
    ---

    ---Your problematic commands in previous rounds
    {
    '0': (Drone, A1)->(Expand Creep)->(A2)
    ANSWER: Since "Drone" cannot expand creep (only Queens can), this instruction is unreasonable.
    '1': (Drone, A1)->(Build)->(Queen Nest, A1)
    ANSWER: This instruction is unreasonable. Drones cannot build Queen Nests; they are constructed by Drones transforming into the structure.
    }
    ---


    Based on the current battle situation and Units and Buildings from both sides, following the rules above, a brief step-by-step analysis can be done from our strategy, Units and Buildings, economic, technical perspectives, And "Your commands in last round".
    Then, formulate up to 20 actionable, specific decisions (at most 20; include the target location, like A1, A2, etc.) from the following action list.
    These decisions should be numbered from 0, denoting the order in which they ought to be executed, with 0 signifying the most immediate and crucial action. Do not output more than 20 numbered commands.


    For instance:

    RESPONSE FORMAT:
    1. Current Stage of the Match;
    2. Our Zerg Units and Buildings;
    3. Our current Zerg Operational Strategy;
    4. Opponent's Units and Buildings;
    5. Opponent's Tactical Plan and our Potential Risks;
    6. Scouting Intel;
    7. Eliminate Repeated instructions;
    8. Avoid problematic commands;
    9. Based on above analysis, each instruction should follow the below format:
    {
    '0': (Command Target)->(Action)->(Target),
    '1': (Command Target)->(Action)->(Target),
    '2': (Command Target)->(Action)->(Target),
    ...
    }
    Note: "Command Target" means the unit you want to command, such as "Drone", "Overlord", etc.
    "Action" means the actions you want the unit to do, such as "Move", "Morph", etc.
    "Target" means the objection, like "A1". Please see the example.
    There's an example:
    {
    ‘0’: (Zergling, A1)->(Move)->(A4), //It means send Zerglings at A1 to A4
    ‘1’: (Drone, A1)->(Gather gas)->(Extractor1, A1) // It means send Drones at A1 to gathering gas at Extractor1 at A1
    ‘2’: (Zergling, A1)->(Morph)->(Baneling) //It means Zergling at A1 need to morph to Baneling
    ...
    }


'''

    prompt = situation__

    #res = llm_gpt35_turbo(prompt, True)
    res =  asyncio.run(llm_gpt35_turbo(prompt, True))
    print('res is ', res)
