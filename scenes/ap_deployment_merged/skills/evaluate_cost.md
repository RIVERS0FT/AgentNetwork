---
name: evaluate_cost
description: "评估目前的硬件部署与施工总成本"
inputs:
  routers_count:
    type: integer
    required: true
tools:
  - evaluate_cost_tool
---

### SOP
1. 读取传入参数 `routers_count`。
2. 乘以 1200 得到结果。
3. 调用 `evaluate_cost_tool`。
