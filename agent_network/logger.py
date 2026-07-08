"""兼容旧导入路径。

日志实现已迁移到 :mod:`agent_network.log_manager`。新代码应直接从
``agent_network.log_manager`` 导入；本文件仅用于过渡旧调用。
"""

from .log_manager import *  # noqa: F401,F403
