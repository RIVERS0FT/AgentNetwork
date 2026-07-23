def write_plan(**kwargs):
    """返回结构化计划数据。"""
    return {"status": "success", "plan": kwargs}


ToolRegistry.register("write_plan", write_plan)
