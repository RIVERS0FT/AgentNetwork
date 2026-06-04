"""
Tool 体系 — 对应架构文档 第四节：Toolbox体系

工具中心统一注册，支持：
- 装饰器注册
- 按名称查找
- 工具列表查询

Tool注册示例：
@tool
class SearchTool:
    name = "search"
    async def execute(self, keyword):
        pass
"""

from typing import Any, Dict, Type, Optional
import time
import hashlib
import json


class Tool:
    """工具基类 — 所有工具的抽象"""
    name: str = "base_tool"
    description: str = "Base tool"

    def execute(self, **kwargs) -> Any:
        raise NotImplementedError(f"Tool '{self.name}' must implement execute()")

    def __repr__(self):
        return f"Tool(name={self.name})"


class ToolRegistry:
    """工具注册中心 — 统一管理所有工具"""
    _instance: Optional["ToolRegistry"] = None
    _tools: Dict[str, Tool] = {}
    _call_stats: Dict[str, Dict[str, Any]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, tool: Tool):
        """注册一个工具实例"""
        cls._tools[tool.name] = tool
        cls._call_stats[tool.name] = {"calls": 0, "total_latency": 0.0}

    @classmethod
    def get(cls, name: str) -> Optional[Tool]:
        """按名称获取工具"""
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> list:
        """列出所有已注册工具"""
        return [
            {"name": t.name, "description": t.description}
            for t in cls._tools.values()
        ]

    @classmethod
    def execute(cls, tool_name: str, **kwargs) -> Any:
        """执行工具并记录统计"""
        tool = cls.get(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found. Available: {list(cls._tools.keys())}")

        start = time.time()
        result = tool.execute(**kwargs)
        latency = (time.time() - start) * 1000  # ms

        cls._call_stats[tool_name]["calls"] += 1
        cls._call_stats[tool_name]["total_latency"] += latency

        return result

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """获取工具调用统计"""
        return {
            name: {
                **stats,
                "avg_latency_ms": round(stats["total_latency"] / stats["calls"], 2) if stats["calls"] > 0 else 0
            }
            for name, stats in cls._call_stats.items()
        }

    @classmethod
    def reset(cls):
        """重置注册中心（测试用）"""
        cls._tools.clear()
        cls._call_stats.clear()


def tool(cls):
    """
    装饰器：将类注册为工具

    用法：
    @tool
    class MyTool:
        name = "my_tool"
        description = "Does something"

        def execute(self, **kwargs):
            return "result"
    """
    instance = cls()
    ToolRegistry.register(instance)
    return cls
