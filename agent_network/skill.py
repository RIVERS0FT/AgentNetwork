"""
Skill 体系 — 对应架构文档 第五节：Skill体系

Skill 是工具组合后的业务能力：将多个 Tool 组合为可复用的业务能力。

Skill示例：
class IntelligenceCollectionSkill:
    async def run(self):
        result = await search_tool.execute("目标区域")
        report = await llm.generate(result)
        return report
"""

from typing import Any, Dict, List, Optional
from .tool import ToolRegistry


class Skill:
    """技能基类 — 组合多个工具的业务能力"""
    name: str = "base_skill"
    description: str = "Base skill"
    required_tools: List[str] = []  # 依赖的工具名称列表

    def __init__(self):
        self._validate_tools()

    def _validate_tools(self):
        """验证依赖工具是否已注册"""
        missing = []
        for tool_name in self.required_tools:
            if ToolRegistry.get(tool_name) is None:
                missing.append(tool_name)
        if missing:
            print(f"[Skill] WARNING: {self.name} missing tools: {missing}")

    def run(self, **kwargs) -> Any:
        """
        执行技能 — 子类必须实现
        返回技能的执行结果
        """
        raise NotImplementedError(f"Skill '{self.name}' must implement run()")

    def use_tool(self, tool_name: str, **kwargs) -> Any:
        """调用已注册的工具"""
        return ToolRegistry.execute(tool_name, **kwargs)

    def __repr__(self):
        return f"Skill(name={self.name})"


class SkillRegistry:
    """技能注册中心"""
    _instance: Optional["SkillRegistry"] = None
    _skills: Dict[str, Skill] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, skill: Skill):
        cls._skills[skill.name] = skill

    @classmethod
    def get(cls, name: str) -> Optional[Skill]:
        return cls._skills.get(name)

    @classmethod
    def list_skills(cls) -> list:
        return [
            {"name": s.name, "description": s.description, "required_tools": s.required_tools}
            for s in cls._skills.values()
        ]

    @classmethod
    def execute(cls, skill_name: str, **kwargs) -> Any:
        skill = cls.get(skill_name)
        if not skill:
            raise ValueError(f"Skill '{skill_name}' not found. Available: {list(cls._skills.keys())}")
        return skill.run(**kwargs)

    @classmethod
    def reset(cls):
        cls._skills.clear()


def skill(cls):
    """装饰器：将类注册为技能"""
    instance = cls()
    SkillRegistry.register(instance)
    return cls
