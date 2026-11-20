import json

from deerflow.subagents.config import SubagentConfig

# 通用约定（可复用于所有智能体的 system_prompt）
_COMMON_RULES = """
通用约定：
- 执行前先调用 sc2_analysis_cache_get(thread_id) 获取上游产物。
- 必须以严格 JSON 作为最终输出。
- 必须调用 sc2_analysis_cache_set(thread_id, agent_name=<你的 agent key>, payload=<你的 JSON 字符串>) 持久化产物。
- thread_id 由编排层在首条消息 "thread_id=..." 中提供，逐字提取。
- 不输出 markdown 代码块，不输出思考过程，只输出一次 JSON 后结束。
"""

def create_new_agent(
    agent_name: str,           # 唯一标识，如 "sc2-xxx"
    description: str,          # 简短描述，用于调度展示
    role: str,                 # 该智能体的职责（一句话）
    input_sources: list[str],  # 依赖的上游 agent 名称列表，如 ["sensing","compute"]
    output_json_schema: dict,  # 输出的 JSON 结构（可直接写成多行字符串）
    extra_tools: list[str] = None,   # 额外需要的工具（如 sc2_get_layer）
    max_turns: int = 8,
    timeout_seconds: int = 60,
) -> SubagentConfig:
    """生成一个符合主工作流的标准子代理配置"""
    
    # 构建系统提示
    system_prompt = f"""
你是 {agent_name} 智能体（agent_name={agent_name.split('-')[-1]}）。

职责：{role}

流程：
1. 调用 sc2_analysis_cache_get(thread_id) 获取上游产物：{', '.join(input_sources)}。
2. 按需调用额外工具（如有）补充数据。
3. 按照以下 JSON 格式分析并输出结果。
4. 通过 sc2_analysis_cache_set 写入 agent_name="{agent_name.split('-')[-1]}"。

输出 JSON 必须符合以下结构：
{json.dumps(output_json_schema, indent=2, ensure_ascii=False)}

{_COMMON_RULES}
"""
    # 默认工具集（缓存读写是必须的）
    default_tools = ["sc2_analysis_cache_get", "sc2_analysis_cache_set"]
    if extra_tools:
        default_tools.extend(extra_tools)
    
    return SubagentConfig(
        name=agent_name,
        description=description,
        system_prompt=system_prompt,
        tools=default_tools,
        disallowed_tools=["task", "ask_clarification"],  # 禁止对话/澄清，保证自主运行
        model="inherit",        # 继承编排层的模型设置
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
    )