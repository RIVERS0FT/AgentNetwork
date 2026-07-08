"""
AI Agent 仿真运行平台 - Agent Network Simulation Platform
========================================================
企业级 AI Agent 仿真、推演与编排平台。

Modules:
- core: 核心抽象 (Agent, Tool, Skill, Message)
- registry: Agent 注册与发现
- simulation: 仿真引擎与事件总线
- log_manager: 分层日志记录与文件管理
"""

import sys

from . import log_manager as _log_manager_module

# 兼容尚未迁移的容器或插件导入；仓库内不再保留 logger.py 实现文件。
sys.modules.setdefault(__name__ + ".logger", _log_manager_module)

__version__ = "0.1.0"
