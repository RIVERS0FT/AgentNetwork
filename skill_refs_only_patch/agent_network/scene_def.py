"""
场景与 Agent 结构定义与 API 配置模块

提供:
- AgentDef 与 SceneDefinition 数据结构
- 自动检测可用的 LLM API Key (get_api_config)
"""

import os

from .config import DEFAULT_LLM_MODEL
from typing import Dict, Any, List
from dataclasses import dataclass, field


@dataclass
class AgentDef:
    """场景配置中的单个 Agent 定义。"""
    agent_id: str
    role: str  # 直接保存角色 identity 内容
    name: str
    skill_refs: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)
    extra_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SceneDefinition:
    """完整场景定义。"""
    scene_name: str = ""
    description: str = ""
    agents: List[AgentDef] = field(default_factory=list)
    topology: List[Dict[str, Any]] = field(default_factory=list)


ROLE_TEMPLATES = {
    "scout": {"skill_refs": ["intelligence_collection", "reconnaissance"]},
    "commander": {"skill_refs": ["strategy_planning", "command", "analysis"]},
    "analyst": {"skill_refs": ["data_analysis", "intelligence_collection"]},
    "support": {"skill_refs": ["logistics", "report_generation"]},
    "observer": {"skill_refs": ["reconnaissance", "monitoring"]},
    "generic": {"skill_refs": ["intelligence_collection"]},
}

SCENE_TEMPLATES = {
    "battlefield": SceneDefinition(
        scene_name="战场推演",
        agents=[
            AgentDef(
                "scout-001",
                "负责战场侦察、情报收集与地形分析",
                "侦察兵",
                ["intelligence_collection", "reconnaissance"],
                ["搜索敌军位置并分析地形"],
            ),
            AgentDef(
                "commander-001",
                "负责综合情报、制定作战方案并下达指令",
                "指挥官",
                ["strategy_planning", "command", "analysis"],
                ["接收情报", "制定攻击方案并下达指令"],
            ),
        ],
    ),
    "fleet": SceneDefinition(
        scene_name="编队推演",
        agents=[
            AgentDef(
                "scout-fleet-a",
                "负责搜索敌军雷达信号",
                "侦察兵A",
                ["intelligence_collection", "reconnaissance"],
                ["搜索敌军雷达信号"],
            ),
            AgentDef(
                "scout-fleet-b",
                "负责收集目标区域地形数据",
                "侦察兵B",
                ["intelligence_collection", "reconnaissance"],
                ["收集目标区域地形数据"],
            ),
            AgentDef(
                "cmd-fleet",
                "负责综合多路情报并制定联合作战方案",
                "指挥官",
                ["strategy_planning", "command", "analysis"],
                ["综合分析多路情报，制定联合作战方案"],
            ),
        ],
    ),
}


def get_api_config() -> Dict[str, str]:
    """获取 LLM API 配置，优先级: 环境变量 > 配置文件"""
    config = {
        "provider": "auto",
        "api_key": "",
        "api_base": "",
        "model": "",
    }

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        config["api_key"] = anthropic_key
        config["provider"] = "anthropic"
        config["model"] = os.environ.get("ANTHROPIC_MODEL", DEFAULT_LLM_MODEL)

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        config["api_key"] = openai_key
        config["provider"] = "openai"
        config["api_base"] = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        config["model"] = os.environ.get("OPENAI_MODEL", "gpt-4o")

    custom_base = os.environ.get("LLM_API_BASE", "")
    if custom_base:
        config["api_base"] = custom_base
        config["provider"] = os.environ.get("LLM_PROVIDER", "openai")

    custom_key = os.environ.get("LLM_API_KEY", "")
    if custom_key:
        config["api_key"] = custom_key

    custom_model = os.environ.get("LLM_MODEL", "")
    if custom_model:
        config["model"] = custom_model

    return config
