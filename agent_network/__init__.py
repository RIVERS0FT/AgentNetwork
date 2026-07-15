"""
AI Agent 仿真运行平台 - Agent Network Simulation Platform
========================================================
企业级 AI Agent 仿真、推演与编排平台。
"""

__version__ = "0.1.0"

# Install infrastructure adapters before callers import agent_network.log_manager.
from agent_network.file_management.log_integration import install_log_manager
from agent_network.log_batch import install_log_batch_manager

install_log_manager()
install_log_batch_manager()
