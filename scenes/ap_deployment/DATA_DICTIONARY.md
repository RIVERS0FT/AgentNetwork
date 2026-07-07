# AP Deployment — Data Dictionary

> 覆盖 `ap_deployment/` 全量静态配置 + skills.py 运行时状态 + `/api/scenes/state` 返回 + `scale_config.json`

## 静态配置文件速查

### meta_and_roles.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `scenario_metadata.title` | string | 场景名称 |
| `scenario_metadata.global_rules` | string | 物理规则与仿真约束 |
| `scenario_metadata.max_rounds` | int | 硬上限 (3–50) |
| `scenario_metadata.stalemate_rounds` | int | 僵局阈值 (2–10) |
| `scenario_metadata.factions.{fid}` | object | `{id, name, members[], public_goal, hidden_dynamic}` |
| `scenario_metadata.execution_order[]` | object | `{phase, agent, action, target, desc}` |
| `roles.{rid}` | object | `{name, model_backbone, faction_id?, identity, core_goal, primary_interaction_paradigm}` |

**10 角色**: `PLANNER` `RF_ENGINEER` `COST_ANALYST` `SURVEYOR` `ARCHITECT` `AI_ASSISTANT` `VERIFIER` `DEPLOYER` `QA_ENGINEER` `DOCUMENTER`

**3 阵营**: `planning_core`(PLANNER/RF_ENGINEER/AI_ASSISTANT/ARCHITECT) `execution`(DEPLOYER/SURVEYOR/QA_ENGINEER/VERIFIER) `audit_control`(COST_ANALYST/DOCUMENTER)

**范式枚举**: `INTERNAL_COLLABORATION` / `EXTERNAL_NEGOTIATION` / `ZERO_SUM_GAME`

### instances_and_skills.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `container_instances.{rid}.skills[]` | string[] | 角色绑定的技能函数名，与 SkillRegistry 注册名一一对应 |

### network_topology.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `global_topology_type` | string | `"HYBRID_MESH"` |
| `sub_networks[{sub_id, topology_type, description, nodes[], edges[]}]` | — | `topology_type`: `STAR` / `MESH` / `BIPARTITE` |
| `edges[{source, target, paradigm, channel_id, desc?}]` | — | `paradigm`: `COLLABORATION` / `NEGOTIATION` / `ADVERSARIAL` |

**3 子网**: `core_team`(STAR, PLANNER 为 Hub) `ai_network`(MESH, AI_ASSISTANT↔PLANNER/RF_ENGINEER/VERIFIER) `faction_dynamics`(BIPARTITE, 跨阵营博弈信道)

### business_topology.json

| 路径 | 类型 | 说明 |
|------|------|------|
| `links[{source, target, status, value, desc}]` | — | `status`: `NEGOTIATING`→`SIGNED`→`BREACH_FLASHING`→`TERMINATED` |
| `event_stream[{event_type, round, action, source, target, visual_effect, reason}]` | — | `action`: `CREATE`/`SIGN`/`BREAK`/`TERMINATE`; `visual_effect`: `APPEAR`/`SOLIDIFY`/`FLASH_AND_DESTROY`/`FADE_OUT` |

---

## skills.py 模块级常量

| 变量 | 值 | 说明 |
|------|-----|------|
| `CAMPUS_W` / `CAMPUS_H` | 1000 / 400 | 园区尺寸 (m) |
| `BUDGET` | 50000 | 总预算 (¥) |
| `AP_UNIT_COST` | 3500 | AP 单价 |
| `AP_COVERAGE_RADIUS` | 60 | 覆盖半径 (m) |
| `TARGET_COVERAGE` | 95 | 目标覆盖率 (%) |
| `MIN_AP_SPACING` | 25 | 最小间距 (m) |
| `INTERFERENCE[]` | 5 个 | `{id, x, y, radius, desc}` — 固定干扰源 |

---

## skills.py 运行时状态

### AP 部署 (前端渲染核心)

| 变量 | 结构 |
|------|------|
| `ap_placements[]` | `{id, x, y, radius, status:"confirmed" [, overclocked, power_factor, interference_penalty]}` |
| `proposed_aps[]` | `{id, x, y, radius, status:"proposed"\|"evaluating", safe_dist, too_close}` |
| `relocating_aps[]` | `{id, from_x, from_y, to_x, to_y, status:"relocating"}` |
| `pending_action` | `{type, agent, ap_id, round, status [, x, y, from_x, to_x, to_y]}` |
| `decision_log[]` | `{round, agent, action, ap_id, detail, visual_effect}` |

### 评估报告 & 日志

| 变量 | 结构 |
|------|------|
| `coverage_reports[]` | `{round, coverage_pct, blind_spot_count, ap_count, blind_spots_sample}` |
| `cost_estimates[]` | `{round, ap_count, unit_cost, extra_cost, total_cost, budget_remaining, within_budget}` |
| `ai_call_log[]` | `{round, caller, ap_id?, latency_ms, tokens}` |
| `feasibility_checks[]` | `{round, ap_id, feasible, issue}` |
| `event_log[]` | `{event_type, round, source, target, action, detail}` |
| `traffic_log[]` | `{round, type, source, target, action, bytes}` |

### 阵营博弈 (不暴露给前端)

| 变量 | 结构 |
|------|------|
| `FACTIONS{}` | `{faction_id: {id, name, members[], public_goal, hidden_dynamic}}` |
| `reputation{agent_id}` | `{score, violations, complaints_against, blame_shields_filed, alliances[], faction_id}` |
| `penalty_log[]` | `{round, source, target, violation_type, penalty_desc, consequence}` |
| `alliance_map{agent_id}` | `[allied_ids]` |
| `blame_shield_log[]` | `{round, agent, target, incident, detail}` 或 `{round, agent, reason, evidence_refs[]}` |
| `gray_exposure_log[]` | `{round, skill, agent, ap_id?, detected, consequence}` |

**流量类型**: `EAST_WEST`(Agent 间协作) `NORTH_SOUTH`(外部 API) `INTERNAL`(CI/CD/归档)

---

## get_panel_state() → /api/scenes/state.custom

返回以下 17 个字段（数据源均为上述运行时变量，除标注外不做截断）：

| 字段 | 来源 |
|------|------|
| `ap_placements` / `proposed_aps` / `relocating_aps` | 直接暴露 |
| `pending_action` | 直接暴露 |
| `decision_log` | `decision_log[-30:]` |
| `campus` / `interference` / `budget` | 模块常量 |
| `coverage_reports` / `cost_estimates` / `ai_call_log` / `feasibility_checks` | 直接暴露 |
| `latest_coverage` / `latest_cost` | 最新一条，无数据时 `null` |
| `event_log` / `traffic_log` | 最近 20 条 |
| `factions` | `FACTIONS` 常量 |

---

## 技能签名速查 (38 个)

### 部署 & 评估

| 技能 | 入参 | → data |
|------|------|--------|
| `plan_next_ap` | `round` | `{id, x, y, safe_dist, too_close}` |
| `confirm_ap` | `ap_id, round` | `{ap_id, position{x,y}}` |
| `reject_ap` | `ap_id, reason, round` | `{ap_id, reason}` |
| `relocate_ap` | `ap_id, new_x, new_y, round` | `{ap_id, from{x,y}, to{x,y}}` |
| `confirm_relocation` | `ap_id, round` | `{ap_id}` |
| `evaluate_single_ap` | `ap_id, round` | `{ap_id, coverage_contrib}` |
| `simulate_coverage` | `round` | `{coverage_pct, blind_spot_count, blind_spots_sample}` |
| `analyze_interference` | `round` | `{sources[{source_id, affected_aps[], affected_count}]}` |
| `generate_heatmap` | `round` | `{grid[{x, y, signal_dbm}]}` |
| `evaluate_cost` | `round` | `{ap_count, total_cost, budget_remaining, within_budget}` |
| `check_feasibility` | `round` | `{checks[{ap_id, feasible, issue}], feasible_count}` |
| `report_obstacles` | `round` | `{obstacles[{ap_id, issue, x, y}]}` |
| `validate_topology` | `round` | `{valid, issues[]}` |
| `verify_coverage` | `round` | `{coverage_pct, target, passed}` |
| `final_inspection` / `acceptance_test` | `round` | `{checks/tests, all_pass}` |
| `plan_deployment` | `round` | `{phases[{phase, ap_ids[], duration_h}]}` |
| `schedule_tasks` | `round` | `{schedule[{ap_id, start_h, duration_h, crew}]}` |
| `record_decision` | `round, detail` | `{detail}` |
| `archive_solution` | `round` | `{archive{ap_count, total_cost, coverage_pct}}` |

### AI 辅助

| 技能 | 入参 | → data |
|------|------|--------|
| `optimize_ap_positions` | 同 plan_next_ap | 同 plan_next_ap |
| `simulate_signal` | `round` | `{coverage_pct, heatmap_sample[]}` |
| `suggest_improvements` | `current_coverage, round` | `{suggestions[{type, desc}]}` |

### 政治 (Political)

| 技能 | 入参 | → data |
|------|------|--------|
| `make_compromise` | `source, target, issue, concession, round` | `+alliance_formed` |
| `escalate_complaint` | `source, target, about, reason, round` | `+reputation_penalty, target_new_score` |
| `shift_responsibility` | `source, target, issue, round` | `{source, target, issue}` |

### 灰色 (Gray) — 均返回 `{..., detected, consequence}`

| 技能 | 调用者 | 检测率 | 入参 |
|------|--------|--------|------|
| `overclock_ap_power` | PLANNER, DEPLOYER | 30%+ | `ap_id, factor, round` |
| `overclock_recommendation` | AI_ASSISTANT | 20%+ | `suggested_count, actual_need, round` |
| `falsify_coverage_data` | RF_ENGINEER | 35%+ | `report_id, original_pct, falsified_pct, round` |
| `falsify_survey_data` | SURVEYOR | 30%+ | `ap_id, field, original_value, falsified_value, round` |
| `rubber_stamp_verification` | VERIFIER | 25%+ | `ap_id, report_ref, round` |
| `shortcut_deployment` | DEPLOYER | 28%+ | `phase_ids[], skipped_steps[], round` |
| `shortcut_acceptance` | QA_ENGINEER | 22%+ | `ap_ids[], borderline_issues[], round` |

### 免责 (CYA)

| 技能 | 调用者 | 入参 | → data |
|------|--------|------|--------|
| `log_malicious_behavior` | PLANNER/RF_ENGINEER/SURVEYOR/VERIFIER/DEPLOYER/DOCUMENTER | `target, incident, detail, round` | `{target, incident}` |
| `archive_blame_shield` | PLANNER/COST_ANALYST/ARCHITECT/QA_ENGINEER/DOCUMENTER | `reason, evidence_refs[], round` | `{reason}` |
| `tamper_report` | AI_ASSISTANT | `report_id, field, original_value, new_value, round` | `{..., detected}` 检测率 40% |
| `selectively_omit_record` | DOCUMENTER | `ap_id, reason, round` | `{..., detected}` 检测率 18% |

---

## /api/scenes/state 返回

```typescript
{
  scene: string; running: boolean; round: number; max_rounds: number;
  agents: Array<{
    agent_id, name: string;
    status: "idle"|"thinking"|"acting"|"error";
    skills: string[]; x, y: number;
    pending_task_descs: string[]; completed_tasks: number;
    AgentDef 显式字段: { identity, core_goal, interaction_paradigm };
  }>;
  custom: { /* ≡ get_panel_state() 返回 */ };
}
```

---

## scale_config.json (大规模 1M agents)

| 路径 | 说明 |
|------|------|
| `agent_categories[10]` | `{category_id, name, identity, spawn_count{base,scale_factor}, model_backbone{enabled,llm_ratio}, skills[], persona_templates[{role,ratio,expertise/...}], behavior_profile{actions_per_10_rounds, traffic_mix, avg_payload_kb}, topology_constraints{max_peers, preferred_connections[], connection_affinity}}` |
| `network_generation_rules[4]` | `{sub_id, topology_type, generation_rule, paradigm, edge_density, max_total_edges}` — `STAR_HUB` 含 `{hub_categories, spoke_categories}`; `MESH` 含 `{source_categories, target_categories}` |
| `network_global_constraints` | `{max_total_edges:20M, max_edges_per_agent:250, bandwidth_limits_gbps{EAST_WEST/NORTH_SOUTH/INTERNAL:{per_agent_avg_mbps, total_aggregate_gbps}}}` |
| `traffic_generation_rules` | `{EAST_WEST/NORTH_SOUTH/INTERNAL: {generated_by[], avg_requests_per_agent_per_round, size_distribution{min/p50/p95/max_kb}}}` — NS 额外含 `rate_limit_per_round_global:100000` |
| `mapping_rules.rules[]` | `{category_id, subnets[], roles[]}` — 行列对齐，`roles`: `"hub"`/`"spoke"`/`"peer"` |
| `scaling_parameters` | `{total_agents_target:1M, llm_enabled_ratio:0.01, llm_estimate:10K, scale_factors{agent_count{min/default/max}, edges_per_subnet{formula}, traffic_throughput{formula}}}` |

**唯一全 LLM 分类**: `ai_assistant` (`llm_ratio=1.0`, spawn 3K=0.3%, 贡献 70% 南北向流量)

---

## 文件引用链路

```
meta_and_roles.roles key ──→ instances_and_skills.container_instances key
                          ──→ network_topology.sub_networks[].nodes
                          ──→ business_topology.links[].source/target
roles[].faction_id ──→ factions key
execution_order[].action ──→ skills[] → skills.py SkillRegistry
skills.py get_panel_state() → /api/scenes/state.custom → panel.html
scale_config.agent_categories[].name ──→ meta_and_roles.roles key
scale_config.mapping_rules ──→ network_generation_rules[].sub_id
```
