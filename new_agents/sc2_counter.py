"""示例：兵种克制分析子智能体（接入模板参考，合并进主工程前需在根目录 sc2_agents 注册）。"""

from create_new_agent import create_new_agent
from sc2_agents import ALL_SC2_SUBAGENTS

counter_agent = create_new_agent(
    agent_name="sc2-counter",
    description="兵种克制分析：基于 sensing 和 compute 数据，推荐最优克制单位",
    role="识别敌方主力单位类型，给出克制生产建议",
    input_sources=["sensing", "compute"],
    output_json_schema={
        "enemy_main_composition": {"unit_type": "string", "count": "int"},
        "counters": [
            {"our_unit": "string", "effectiveness": "float", "action": "produce|tech"}
        ],
        "urgent_switch": "boolean",
    },
    extra_tools=["sc2_unit_capabilities"],
    max_turns=6,
    timeout_seconds=45,
)

# 合并进主仓库时取消注释：
# ALL_SC2_SUBAGENTS["sc2-counter"] = counter_agent
