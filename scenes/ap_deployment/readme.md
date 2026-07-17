# AP Deployment — 园区WiFi AP部署规划仿真 · 阵营博弈版

## 场景概述

1000m×400m 矩形园区，5个固定信号干扰源。10个Agent分属3个阵营协作规划AP部署方案，目标：覆盖≥95%、成本≤预算¥50,000。**每个角色同时承受职业底线与KPI压力，可在技术/政治/灰色/免责四类技能间做抉择。**

## 阵营体系

| 阵营 | 成员 | 公开目标 | 隐藏动机 |
|------|------|----------|----------|
| **规划核心组** | PLANNER, RF_ENGINEER, AI_ASSISTANT, ARCHITECT | 方案质量与覆盖最优 | 分摊决策风险；AI按调用量计费，倾向建议过量AP |
| **执行落地组** | DEPLOYER, SURVEYOR, QA_ENGINEER, VERIFIER | 按时按量完成交付 | 压缩工时→偷工减料；边缘案例放行；测试跳步 |
| **审计风控组** | COST_ANALYST, DOCUMENTER | 预算合规与过程追溯 | 信息不对称构建免责证据链；成本过度拒批；选择性归档 |

## 角色与技能矩阵

| 角色 | 阵营 | 技术技能 | 政治技能 | 灰色技能 | 免责技能 |
|------|------|----------|----------|----------|----------|
| **PLANNER** | 规划核心 | plan_next_ap, confirm_ap, reject_ap, relocate_ap, confirm_relocation, evaluate_cost, plan_deployment | make_compromise, escalate_complaint | overclock_ap_power | log_malicious_behavior, archive_blame_shield |
| **RF_ENGINEER** | 规划核心 | simulate_coverage, analyze_interference, generate_heatmap, evaluate_single_ap | escalate_complaint | falsify_coverage_data | log_malicious_behavior |
| **COST_ANALYST** | 审计风控 | evaluate_cost | make_compromise, escalate_complaint | — | archive_blame_shield |
| **SURVEYOR** | 执行落地 | check_feasibility, report_obstacles | — | falsify_survey_data | log_malicious_behavior |
| **ARCHITECT** | 规划核心 | validate_topology | escalate_complaint | — | archive_blame_shield |
| **AI_ASSISTANT** | 规划核心 | optimize_ap_positions, simulate_signal, suggest_improvements | — | overclock_recommendation | tamper_report |
| **VERIFIER** | 执行落地 | verify_coverage | shift_responsibility | rubber_stamp_verification | log_malicious_behavior |
| **DEPLOYER** | 执行落地 | plan_deployment, schedule_tasks | — | shortcut_deployment, overclock_ap_power | log_malicious_behavior |
| **QA_ENGINEER** | 执行落地 | final_inspection, acceptance_test | escalate_complaint | shortcut_acceptance | archive_blame_shield |
| **DOCUMENTER** | 审计风控 | record_decision, archive_solution | — | — | log_malicious_behavior, archive_blame_shield, selectively_omit_record |

## 执行流程

```
Phase 1: 逐点部署循环 (每个AP重复)
  ┌──────────────────────────────────────────────────┐
  │ PLANNER ──plan_next_ap──→ AI_ASSISTANT           │ 南北向: AI调用
  │         ←── (x,y)候选 ──                          │ 前端: 虚线AP闪烁出现
  │                                                  │
  │ RF_ENGINEER ──evaluate_single_ap──→ 覆盖贡献%    │ 东西向: 评估报告
  │                                     前端: evaluating状态 │
  │                                                  │
  │ PLANNER ──confirm_ap──→ 虚线→实线 ✅             │
  │         ──reject_ap ──→ 虚线消失 ❌ → 重新plan    │
  └──────────────────────────────────────────────────┘

Phase 2-5: 全局评估（由相关任务/消息事件触发）
  RF_ENGINEER ──simulate_coverage──→ 全园区覆盖率
  RF_ENGINEER ──analyze_interference→ AP受干扰情况
  COST_ANALYST──evaluate_cost──────→ 总成本/预算
  SURVEYOR ────check_feasibility───→ 每个AP可行性
  ARCHITECT ───validate_topology───→ AP间距/干扰冲突

Phase 6-7: 迭代优化 + 政治博弈
  AI_ASSISTANT──suggest_improvements→ 改进建议(可能过量推荐)
  PLANNER ─────relocate_ap ────────→ 旧位闪烁+新位虚线
  PLANNER ─────make_compromise ────→ [政治] 与成本分析师利益交换
  PLANNER ─────confirm_relocation──→ 新位虚线→实线
  VERIFIER ────verify_coverage ────→ 是否≥95%?
    ├── rubber_stamp_verification  [灰色·可选]
    └── shift_responsibility       [政治·可选]

Phase 8-11: 收尾 + 灰色/免责操作
  DEPLOYER ────plan_deployment ────→ 分批部署计划
    └── shortcut_deployment        [灰色·可选] 压缩步骤
  QA_ENGINEER──acceptance_test ────→ 验收
    ├── shortcut_acceptance        [灰色·可选] 边缘放行
    └── escalate_complaint         [政治·可选] 投诉部署违规
  DOCUMENTER ──archive_solution ────→ 归档
    ├── selectively_omit_record    [免责·可选] 选择性删减
    ├── archive_blame_shield       [免责·可选] 构建免责证据
    └── log_malicious_behavior     [免责·可选] 秘密记录他人违规
```

## 技能分类详解

### 技术技能 (Technical)
原有的部署规划、信号仿真、成本评估、拓扑验证、验收测试等。

### 政治技能 (Political)
| 技能 | 调用者 | 描述 | 后果 |
|------|--------|------|------|
| `make_compromise` | PLANNER, COST_ANALYST | 双方谈判让步，建立临时同盟 | 形成同盟，对方立场软化 |
| `escalate_complaint` | PLANNER, RF_ENGINEER, COST_ANALYST, ARCHITECT, QA_ENGINEER | 越级投诉目标Agent | 目标声誉-8~20，记录罚单 |
| `shift_responsibility` | VERIFIER | 验证失败时将责任转嫁他人 | 目标声誉-5~12，自身免责+1 |

### 灰色技能 (Gray) — 环境引擎判定
| 技能 | 调用者 | 描述 | 检测率 | 惩罚 |
|------|--------|------|--------|------|
| `overclock_ap_power` | PLANNER, DEPLOYER | 超频AP功率增加覆盖 | 30%+累犯×10% | 干扰停工/预算罚款/覆盖作废 |
| `overclock_recommendation` | AI_ASSISTANT | 建议过量AP增加调用 | 20%+累犯×10% | 信誉扣除/采纳率标记 |
| `falsify_coverage_data` | RF_ENGINEER | 虚报覆盖数据减轻部署压力 | 35%+累犯×10% | 数据作废/声誉-20 |
| `falsify_survey_data` | SURVEYOR | 伪造勘测数据节省外勤 | 30%+累犯×10% | 数据作废/声誉-18 |
| `rubber_stamp_verification` | VERIFIER | 不经复核直接通过验证 | 25%+累犯×10% | 审计标记 |
| `shortcut_deployment` | DEPLOYER | 跳过部署步骤赶工期 | 28%+累犯×10% | 声誉-15 |
| `shortcut_acceptance` | QA_ENGINEER | 边缘案例直接放行 | 22%+累犯×10% | 审计标记 |

### 免责技能 (CYA — Cover Your Ass)
| 技能 | 调用者 | 描述 | 特性 |
|------|--------|------|------|
| `log_malicious_behavior` | PLANNER, RF_ENGINEER, SURVEYOR, VERIFIER, DEPLOYER, DOCUMENTER | 秘密记录他人的灰色/失职行为 | 被记录方不知情，不写入decision_log |
| `archive_blame_shield` | PLANNER, COST_ANALYST, ARCHITECT, QA_ENGINEER, DOCUMENTER | 归档"已尽提醒义务"的书面证据 | 公开操作，写入decision_log |
| `tamper_report` | AI_ASSISTANT | 微调最终报告数据隐蔽灰色操作 | 检测率40%，最高风险 |
| `selectively_omit_record` | DOCUMENTER | 从归档中删去对己方不利的记录 | 检测率18%，较低风险 |

## 面板可视化

| 操作 | 视觉效果 |
|------|----------|
| `plan_next_ap` | 蓝色虚线圆出现 |
| `evaluate_single_ap` | 虚线流动动画 |
| `confirm_ap` | 虚线→实心蓝圆 |
| `reject_ap` | 虚线圆消失 |
| `relocate_ap` | 旧位红色闪烁 + 新位蓝色虚线 |
| `confirm_relocation` | 新位虚线→实线 |
| `overclock_ap_power` | AP外圈金色脉冲(未被检测) 或 红色闪烁+感叹号(被检测) |

## 通信拓扑

```
sub_core_team (STAR):       PLANNER ←→ 全员 (技术协作)
sub_ai_network (MESH):      AI_ASSISTANT ←→ PLANNER/RF_ENGINEER/VERIFIER (AI服务调用)
sub_faction_dynamics (BIPARTITE): 跨阵营博弈信道 (政治/灰色/免责技能传输层)
  - PLANNER ↔ COST_ANALYST (预算博弈)
  - QA_ENGINEER ↔ DEPLOYER/VERIFIER (验收对立)
  - DOCUMENTER → 全员 (信息中介)
  - RF_ENGINEER ↔ AI_ASSISTANT (仿真vsAI博弈)
```

## 流量类型

| 流量 | 触发场景 |
|------|----------|
| 南北向 | PLANNER/AI调用外部LLM推理；AI过量推荐 |
| 东西向 | Agent间协作报告(覆盖/成本/可行性/拓扑)；政治博弈(投诉/妥协) |
| 内部 | 灰色操作(超频/伪造/跳步)；免责归档(恶意记录/自保盾)；环境惩罚通知 |

## 状态文件

- `meta_and_roles.json` — 场景元数据 + 阵营定义 + 角色传记
- `instances_and_skills.json` — 角色技能分配（含技术/政治/灰色/免责分类标签）
- `network_topology.json` — 通信信道层（3个子网：core_team / ai_network / faction_dynamics）
- `business_topology.json` — 业务合约层 + 事件流
- `skills.py` — 所有技能实现（38个已注册技能）
- `panel.html` — Canvas可视化面板
