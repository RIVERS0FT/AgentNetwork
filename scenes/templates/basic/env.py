"""基础剧本环境数据。"""

ENV = {
    "metadata": {
        "title": "基础剧本",
        "description": "AgentNetwork v2 剧本目录模板",
    },
    "environment": {
        "global_rules": ["所有输出必须可审计"],
        "initial_state": {"status": "ready"},
        "shared_data": {},
    },
    "scene_tasks": [
        {
            "task_id": "finish-scene",
            "goal": "完成剧本级验收",
            "input": {},
            "depends_on": ["draft-plan"],
        }
    ],
}
