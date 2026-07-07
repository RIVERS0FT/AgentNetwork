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
    core_goal: str = ""
    backend: str = "openclaw"
    skill_refs: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)


@dataclass
class SceneDefinition:
    """完整场景定义。"""
    scene_key: str = ""
    title: str = ""
    description: str = ""
    agents: List[AgentDef] = field(default_factory=list)
    topology: List[Dict[str, Any]] = field(default_factory=list)


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
