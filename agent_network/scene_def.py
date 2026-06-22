"""
场景与 Agent 结构定义与 API 配置模块

提供:
- AgentDef 与 SceneDefinition 数据结构
- 自动检测可用的 LLM API Key (get_api_config)
"""

import os

from .config import DEFAULT_LLM_MODEL
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class AgentDef:
    """LLM 解析出的单个 Agent 定义"""
    agent_id: str
    role: str
    name: str
    skills: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)  # 该 agent 要执行的任务
    extra_meta: Dict[str, Any] = field(default_factory=dict)  # script_json 扩展字段


@dataclass
class SceneDefinition:
    """LLM 解析出的完整场景定义"""
    scene_name: str = ""
    description: str = ""
    agents: List[AgentDef] = field(default_factory=list)
    workflow: List[Dict[str, Any]] = field(default_factory=list)  # 任务执行顺序
    event_triggers: List[Dict[str, Any]] = field(default_factory=list)  # script_json 事件

    def to_workflow_steps(self):
        """
        将 workflow 字典列表转换为 WorkflowStep 对象列表

        支持两种格式:
        1. 新格式: {"step_id": "s1", "type": "task", "agent_id": "...", "depends_on": [...]}
        2. 旧格式: {"step": 1, "agent": "agent_id", "action": "..."} → 自动转换
        """
        from .workflow import WorkflowStep

        steps = []
        for i, wf in enumerate(self.workflow):
            # 检测旧格式: 有 "step" 字段但没有 "step_id"
            if "step" in wf and "step_id" not in wf:
                step_id = f"step-{wf['step']}"
                agent_id = wf.get("agent", "")
                action = wf.get("action", "")
                # 旧格式线性依赖（除了第一步，其他依赖前一步）
                deps = [f"step-{wf['step'] - 1}"] if wf["step"] > 1 else []
                wf = {
                    "step_id": step_id,
                    "type": "task",
                    "agent_id": agent_id,
                    "action": action,
                    "depends_on": deps,
                    "description": wf.get("description", f"Step {wf['step']}"),
                }

            step = WorkflowStep.from_dict(wf)
            steps.append(step)

        return steps


# Agent 角色模板库
ROLE_TEMPLATES = {
    "scout": {
        "skills": ["intelligence_collection", "reconnaissance"],
        "tags": ["blue_force", "recon"],
    },
    "commander": {
        "skills": ["strategy_planning", "command", "analysis"],
        "tags": ["blue_force", "command"],
    },
    "analyst": {
        "skills": ["data_analysis", "intelligence_collection"],
        "tags": ["blue_force", "analysis"],
    },
    "support": {
        "skills": ["logistics", "report_generation"],
        "tags": ["blue_force", "support"],
    },
    "observer": {
        "skills": ["reconnaissance", "monitoring"],
        "tags": ["blue_force", "observer"],
    },
    "generic": {
        "skills": ["intelligence_collection"],
        "tags": ["blue_force"],
    },
}

SCENE_TEMPLATES = {
    "battlefield": SceneDefinition(
        scene_name="战场推演",
        agents=[
            AgentDef("scout-001", "scout", "侦察兵", ["intelligence_collection", "reconnaissance"],
                     ["blue_force", "recon"], ["搜索敌军位置并分析地形"]),
            AgentDef("commander-001", "commander", "指挥官", ["strategy_planning", "command", "analysis"],
                     ["blue_force", "command"], ["接收情报", "制定攻击方案并下达指令"]),
        ],
    ),
    "fleet": SceneDefinition(
        scene_name="编队推演",
        agents=[
            AgentDef("scout-fleet-a", "scout", "侦察兵A", ["intelligence_collection", "reconnaissance"],
                     ["blue_force", "recon", "alpha_team"], ["搜索敌军雷达信号"]),
            AgentDef("scout-fleet-b", "scout", "侦察兵B", ["intelligence_collection", "reconnaissance"],
                     ["blue_force", "recon", "bravo_team"], ["收集目标区域地形数据"]),
            AgentDef("cmd-fleet", "commander", "指挥官", ["strategy_planning", "command", "analysis"],
                     ["blue_force", "command"], ["综合分析多路情报，制定联合作战方案"]),
        ],
    ),
}


def get_api_config() -> Dict[str, str]:
    """获取 LLM API 配置，优先级: 环境变量 > 配置文件"""
    config = {
        "provider": "auto",           # "anthropic" | "openai" | "auto"
        "api_key": "",
        "api_base": "",
        "model": "",
    }

    # Anthropic
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        config["api_key"] = anthropic_key
        config["provider"] = "anthropic"
        config["model"] = os.environ.get("ANTHROPIC_MODEL", DEFAULT_LLM_MODEL)

    # OpenAI (优先于 Anthropic，如果同时存在)
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        config["api_key"] = openai_key
        config["provider"] = "openai"
        config["api_base"] = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        config["model"] = os.environ.get("OPENAI_MODEL", "gpt-4o")

    # 自定义 API Base
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
