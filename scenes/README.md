# Scenes — 剧本编译器

`scenario.py` 将自然语言编译为多 Agent 仿真剧本。一键生成 5 个标准文件 + 合并包。

## 用法

```bash
pip install httpx
export ANTHROPIC_API_KEY="sk-..."    # DeepSeek API Key

python scenes/scenario.py -i "<想法>" -d ./scenes/<场景名>
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i` `--idea` | 场景概念描述 | 新能源项目并网审批示例 |
| `-d` `--dir` | 输出目录 | `./scenarios/energy_project_v1` |

```bash
python scenes/scenario.py \
  -i "电信运营商招投标市场竞争：5家机构与3家运营商进行商务谈判" \
  -d ./scenes/telecom_bidding_test
```

## 生成流程

```
用户 idea
  → scenario.py 调用 DeepSeek API (SYSTEM_PROMPT 铁律 1-10 + JSON Schema 约束)
    → LLM 返回 5 模块大 JSON
      → 多文件分发落盘
```

生成后微调：编写 `panel.html`、调整角色结构、扩展技能实现、适配 `get_panel_state()` 前端接口。

---

## 输出文件

### 标准包 (`<name>/`)

| 文件 | 用途 |
|------|------|
| `meta_and_roles.json` | 元数据、终止条件、角色定义 (含 identity/core_goal/paradigm) |
| `instances_and_skills.json` | 角色→技能绑定表 |
| `network_topology.json` | 通信信道层：天然双向的 TopologyLink 列表 |
| `business_topology.json` | 业务合约层：种子连线 + event_stream (NEGOTIATING→SIGNED→BREACH_FLASHING→TERMINATED) |
| `skills.py` | 技能可执行代码 (SkillRegistry + 所有技能函数) |

### 合并包 (`<name>_merged/`)

| 文件 | 用途 |
|------|------|
| `skills.py` | 同标准包 (复制) |
| `<name>_merged.json` | 四合一：roles + peers(从 edges 推导) + skills(从 instances 提取) + business_topology |

---

## 数据字典

### meta_and_roles.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `scenario_metadata.title` | string | 场景名 |
| `scenario_metadata.global_rules` | string | 物理/业务规则 |
| `scenario_metadata.max_rounds` | int | 硬上限 (3–30) |
| `scenario_metadata.stalemate_rounds` | int | 僵局阈值 (2–10) |
| `roles.{role_id}` | object | `{name, model_backbone, identity, core_goal, primary_interaction_paradigm}` |
| `roles.{role_id}.model_backbone` | enum | `openclaw` / `claude-code` |
| `roles.{role_id}.primary_interaction_paradigm` | enum | `INTERNAL_COLLABORATION` / `EXTERNAL_NEGOTIATION` / `ZERO_SUM_GAME` |

### instances_and_skills.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `container_instances.{role_id}.skills[]` | string[] | 技能函数名，与 skills.py 注册名一致 |

### network_topology.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `topology[]` | object[] | Agent 间天然双向的网络链路 |
| `topology[].endpoint_a` | string | 链路端点 A |
| `topology[].endpoint_b` | string | 链路端点 B |
| `topology[].channel_id` | string | 唯一通道 ID |
| `topology[].delay_ms` | number | 双向链路时延 |
| `topology[].jitter_ms` | number | 双向链路抖动 |
| `topology[].loss_pct` | number | 双向链路丢包率 |
| `topology[].rate_mbit` | number | 双向链路带宽限制 |

### business_topology.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `links[]` | object | `{source, target, status, value, desc}` — 初始种子，全部 NEGOTIATING |
| `links[].status` | enum | `NEGOTIATING` → `SIGNED` → `BREACH_FLASHING` → `TERMINATED` |
| `event_stream[]` | object | `{event_type, round, action, source, target, visual_effect, reason}` — 运行时动态追加 |

### skills.py

独立可运行的 Python 模块。每个函数接受 `**kwargs`，返回 `{"status", "result", "data"}`。

```python
SkillRegistry.execute("submit_code", developer="dev_fe", repo="main", round=1)
# → {"status": "success", "result": "code_submitted", "data": {...}}
```

| 方法 | 说明 |
|------|------|
| `SkillRegistry.register(name, fn)` | 注册技能函数 |
| `SkillRegistry.execute(name, **kwargs)` | 调用技能 |
| `SkillRegistry.list_skills()` | 列出所有已注册技能名 |

### 合并 JSON

| 字段 | 来源 |
|------|------|
| `scenario_metadata` | meta_and_roles |
| `topology` | network_topology |
| `roles.{id}` (含 `peers[]` `skills[]`) | roles + topology 双向推导 + instances 提取 |
| `business_topology` | business_topology.json |

---

## 手工迭代模式

LLM 生成的是**原型**。后续迭代链：

```
生成原型 → 写 panel.html → 调角色/拓扑 → 扩展 skills.py → 适配 get_panel_state()
  → 验证合理性 (否则循环) → 小规模定稿
    → 派生 {name}_scale/ (scale_config.json + 统计 skills.py)
      → 微调大规模 → 完成
```

各场景的详细字段定义见各自目录下的 `DATA_DICTIONARY.md`。API 规范见 [PANEL_API.md](PANEL_API.md)。
