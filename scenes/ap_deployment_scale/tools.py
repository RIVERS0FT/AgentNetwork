import random
import math
import json

# ============================================================
# 园区地图 & 干扰源（固定）
# ============================================================
CAMPUS_W = 1000  # 园区宽度 (m)
CAMPUS_H = 400   # 园区高度 (m)

INTERFERENCE = [
    {"id": "INT_01", "x": 150, "y": 80,  "radius": 80,  "desc": "变电站电磁干扰"},
    {"id": "INT_02", "x": 420, "y": 280, "radius": 120, "desc": "大型电机设备"},
    {"id": "INT_03", "x": 680, "y": 120, "radius": 60,  "desc": "微波通信塔"},
    {"id": "INT_04", "x": 850, "y": 320, "radius": 100, "desc": "高压输电线"},
    {"id": "INT_05", "x": 300, "y": 350, "radius": 150, "desc": "工业焊接车间"},
]

# ============================================================
# 模块级状态
# ============================================================
ap_placements = []        # [{id, x, y, radius, cost, status}]
coverage_reports = []     # [{round, coverage_pct, blind_spots, ap_count, total_cost}]
cost_estimates = []       # [{round, ap_count, unit_cost, total_cost, budget_remaining}]
ai_call_log = []          # [{round, caller, request_type, latency_ms, tokens}]
feasibility_checks = []   # [{round, ap_id, feasible, issue}]

BUDGET = 50000  # 总预算
AP_UNIT_COST = 3500  # 单AP成本（含安装）
AP_COVERAGE_RADIUS = 60  # AP覆盖半径 (m)
TARGET_COVERAGE = 95  # 目标覆盖率(%)

event_log = []
traffic_log = []


def _emit_event(etype, round_num, source, target, action, detail=""):
    event_log.append({"event_type": etype, "round": round_num, "source": source, "target": target, "action": action, "detail": detail})


def _emit_traffic(round_num, ttype, source, target, action, kbytes):
    traffic_log.append({"round": round_num, "type": ttype, "source": source, "target": target, "action": action, "bytes": kbytes * 1024})


# ============================================================
# SkillRegistry
# ============================================================
class ToolRegistry:
    _tools = {}

    @classmethod
    def register(cls, name, fn):
        cls._tools[name] = fn

    @classmethod
    def execute(cls, name, **kwargs):
        if name not in cls._tools:
            return {"status": "error", "result": None, "data": {"error": f"Skill '{name}' not found"}}
        return cls._tools[name](**kwargs)

    @classmethod
    def list_tools(cls):
        return list(cls._tools.keys())


# ============================================================
# PLANNER: call_ai_optimizer
# ============================================================
def call_ai_optimizer_tool(**kwargs):
    """
    调用AI优化助手获取最优AP位置。产生南北向流量（外部LLM调用）。
    """
    round_num = kwargs.get("round", 0)
    num_aps = kwargs.get("num_aps", 8)

    # 模拟AI推理
    latency = random.randint(200, 800)
    tokens = random.randint(500, 2000)

    # 优化算法：网格化 + 避开干扰源
    positions = []
    cols = max(2, int(math.sqrt(num_aps * CAMPUS_W / CAMPUS_H)))
    rows = max(2, int(math.ceil(num_aps / cols)))
    step_x = CAMPUS_W / (cols + 1)
    step_y = CAMPUS_H / (rows + 1)
    idx = 0
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            if idx >= num_aps:
                break
            bx, by = step_x * c, step_y * r
            # 微调避开干扰
            best_x, best_y, best_dist = bx, by, 0
            for _ in range(20):
                tx = bx + random.uniform(-step_x * 0.3, step_x * 0.3)
                ty = by + random.uniform(-step_y * 0.3, step_y * 0.3)
                tx = max(10, min(CAMPUS_W - 10, tx))
                ty = max(10, min(CAMPUS_H - 10, ty))
                min_dist = min((math.sqrt((tx - s["x"]) ** 2 + (ty - s["y"]) ** 2) for s in INTERFERENCE), default=999)
                if min_dist > best_dist:
                    best_x, best_y, best_dist = tx, ty, min_dist
            positions.append({"x": round(best_x, 1), "y": round(best_y, 1), "safe_dist": round(best_dist, 1)})
            idx += 1

    ai_call_log.append({"round": round_num, "caller": "PLANNER", "request_type": "optimize_ap_positions", "latency_ms": latency, "tokens": tokens})
    _emit_traffic(round_num, "NORTH_SOUTH", "PLANNER", "AI_ASSISTANT", "optimize_ap", tokens * 4)
    _emit_event("AI_CALL", round_num, "PLANNER", "AI_ASSISTANT", "optimize_ap_positions",
                f"{num_aps} APs, {latency}ms, {tokens} tokens")

    return {
        "status": "success", "result": "ai_optimized",
        "data": {"positions": positions, "num_aps": num_aps, "latency_ms": latency, "tokens": tokens, "round": round_num}
    }
ToolRegistry.register("call_ai_optimizer_tool", call_ai_optimizer_tool)


# ============================================================
# RF_ENGINEER: simulate_coverage, analyze_interference
# ============================================================
def simulate_coverage_tool(**kwargs):
    """
    仿真信号覆盖。计算覆盖率、盲区列表。
    """
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    if not aps:
        aps = [{"x": random.uniform(50, CAMPUS_W - 50), "y": random.uniform(50, CAMPUS_H - 50),
                "radius": AP_COVERAGE_RADIUS, "id": f"AP_{i + 1}"} for i in range(8)]

    # 蒙特卡洛采样
    samples = 2000
    covered = 0
    blind_spots = []
    for _ in range(samples):
        sx, sy = random.uniform(0, CAMPUS_W), random.uniform(0, CAMPUS_H)
        in_range = any(math.sqrt((sx - ap["x"]) ** 2 + (sy - ap["y"]) ** 2) < ap.get("radius", AP_COVERAGE_RADIUS)
                       for ap in aps)
        in_interference = any(math.sqrt((sx - s["x"]) ** 2 + (sy - s["y"]) ** 2) < s["radius"] for s in INTERFERENCE)
        if in_range and not in_interference:
            covered += 1
        elif not in_range:
            blind_spots.append({"x": round(sx, 1), "y": round(sy, 1)})

    coverage_pct = round(covered / samples * 100, 1)
    report = {
        "round": round_num, "coverage_pct": coverage_pct, "blind_spot_count": len(blind_spots),
        "ap_count": len(aps), "blind_spots_sample": blind_spots[:10]
    }
    coverage_reports.append(report)

    _emit_traffic(round_num, "EAST_WEST", "RF_ENGINEER", "PLANNER", "coverage_report", 16)
    _emit_event("COVERAGE_SIM", round_num, "RF_ENGINEER", "PLANNER", "simulate_coverage",
                f"{coverage_pct}% ({covered}/{samples}), {len(blind_spots)} blind spots")

    return {
        "status": "success", "result": "coverage_simulated",
        "data": report
    }
ToolRegistry.register("simulate_coverage_tool", simulate_coverage_tool)


def analyze_interference_tool(**kwargs):
    """分析干扰源影响范围"""
    round_num = kwargs.get("round", 0)
    analysis = []
    for src in INTERFERENCE:
        affected_aps = []
        for ap in ap_placements:
            dist = math.sqrt((ap["x"] - src["x"]) ** 2 + (ap["y"] - src["y"]) ** 2)
            if dist < src["radius"]:
                affected_aps.append(ap["id"])
        analysis.append({
            "source_id": src["id"], "desc": src["desc"], "radius": src["radius"],
            "affected_ap_count": len(affected_aps), "affected_aps": affected_aps
        })
    _emit_event("INTERFERENCE_ANALYSIS", round_num, "RF_ENGINEER", "PLANNER", "analyze_interference",
                f"{len(INTERFERENCE)} sources, {sum(a['affected_ap_count'] for a in analysis)} APs affected")
    return {"status": "success", "result": "analysis_complete", "data": {"sources": analysis, "round": round_num}}
ToolRegistry.register("analyze_interference_tool", analyze_interference_tool)


# ============================================================
# COST_ANALYST
# ============================================================
def evaluate_cost_tool(**kwargs):
    """评估方案成本"""
    round_num = kwargs.get("round", 0)
    ap_count = kwargs.get("ap_count", len(ap_placements))
    unit_cost = kwargs.get("unit_cost", AP_UNIT_COST)
    extra_cost = random.randint(2000, 8000)  # 安装/线缆/交换机等
    total = ap_count * unit_cost + extra_cost
    remaining = BUDGET - total
    estimate = {"round": round_num, "ap_count": ap_count, "unit_cost": unit_cost, "extra_cost": extra_cost,
                "total_cost": total, "budget_remaining": remaining, "within_budget": remaining >= 0}
    cost_estimates.append(estimate)
    _emit_event("COST_EVAL", round_num, "COST_ANALYST", "PLANNER", "evaluate_cost",
                f"{ap_count} APs × {unit_cost} + {extra_cost} = {total} (budget:{BUDGET})")
    _emit_traffic(round_num, "EAST_WEST", "COST_ANALYST", "PLANNER", "cost_report", 8)
    return {"status": "success", "result": "cost_evaluated", "data": estimate}
ToolRegistry.register("evaluate_cost_tool", evaluate_cost_tool)


# ============================================================
# SURVEYOR
# ============================================================
def check_feasibility_tool(**kwargs):
    """现场勘测物理可行性"""
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    checks = []
    for ap in aps:
        feasible = random.random() > 0.15
        issue = None if feasible else random.choice(["电源不可达", "承重不足", "信号遮挡", "无安装支架"])
        checks.append({"ap_id": ap.get("id", "?"), "feasible": feasible, "issue": issue})
        feasibility_checks.append({"round": round_num, "ap_id": ap.get("id", "?"), "feasible": feasible, "issue": issue})
    feasible_count = sum(1 for c in checks if c["feasible"])
    _emit_event("FEASIBILITY_CHECK", round_num, "SURVEYOR", "PLANNER", "check_feasibility",
                f"{feasible_count}/{len(checks)} feasible")
    return {"status": "success", "result": "feasibility_checked",
            "data": {"checks": checks, "feasible_count": feasible_count, "total": len(checks), "round": round_num}}
ToolRegistry.register("check_feasibility_tool", check_feasibility_tool)


# ============================================================
# AI_ASSISTANT: optimize_ap_positions, simulate_signal
# ============================================================
def optimize_ap_positions_tool(**kwargs):
    """AI优化AP位置（外部工具调用）"""
    round_num = kwargs.get("round", 0)
    num_aps = kwargs.get("num_aps", 8)
    latency = random.randint(300, 1000)
    tokens = random.randint(1000, 3000)
    # K-means启发式 + 避开干扰
    positions = []
    for i in range(num_aps):
        bx = CAMPUS_W * (i + 1) / (num_aps + 1)
        by = CAMPUS_H / 2 + random.uniform(-CAMPUS_H * 0.3, CAMPUS_H * 0.3)
        best_x, best_y, best_score = bx, by, -1
        for _ in range(30):
            tx = max(10, min(CAMPUS_W - 10, bx + random.uniform(-100, 100)))
            ty = max(10, min(CAMPUS_H - 10, by + random.uniform(-80, 80)))
            min_safe = min((math.sqrt((tx - s["x"]) ** 2 + (ty - s["y"]) ** 2) - s["radius"] for s in INTERFERENCE), default=0)
            score = min_safe - abs(tx - bx) * 0.01
            if score > best_score:
                best_x, best_y, best_score = tx, ty, score
        positions.append({"x": round(best_x, 1), "y": round(best_y, 1), "score": round(best_score, 1)})

    ai_call_log.append({"round": round_num, "caller": "AI_ASSISTANT", "request_type": "optimize_ap", "latency_ms": latency, "tokens": tokens})
    _emit_traffic(round_num, "NORTH_SOUTH", "AI_ASSISTANT", "EXTERNAL:LLM", "llm_inference", tokens * 4)
    _emit_event("AI_OPTIMIZE", round_num, "AI_ASSISTANT", "EXTERNAL:LLM", "optimize_ap_positions",
                f"{num_aps} APs optimized, {tokens} tokens, {latency}ms")
    return {"status": "success", "result": "optimized", "data": {"positions": positions, "latency_ms": latency, "tokens": tokens, "round": round_num}}
ToolRegistry.register("optimize_ap_positions_tool", optimize_ap_positions_tool)


def simulate_signal_tool(**kwargs):
    """AI仿真信号强度（外部LLM辅助计算）"""
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    if not aps:
        aps = [{"x": random.uniform(20, CAMPUS_W - 20), "y": random.uniform(20, CAMPUS_H - 20), "radius": AP_COVERAGE_RADIUS, "id": f"AP_{i + 1}"} for i in range(8)]
    samples, covered = 1500, 0
    heatmap = []
    for _ in range(samples):
        sx, sy = random.uniform(0, CAMPUS_W), random.uniform(0, CAMPUS_H)
        best_signal = max((-50 - random.randint(0, 30) for ap in aps
                           if math.sqrt((sx - ap["x"]) ** 2 + (sy - ap["y"]) ** 2) < ap.get("radius", AP_COVERAGE_RADIUS)), default=-90)
        in_interference = any(math.sqrt((sx - s["x"]) ** 2 + (sy - s["y"]) ** 2) < s["radius"] for s in INTERFERENCE)
        if best_signal > -75 and not in_interference:
            covered += 1
        if len(heatmap) < 50:
            heatmap.append({"x": round(sx, 1), "y": round(sy, 1), "signal_dbm": best_signal})
    coverage = round(covered / samples * 100, 1)

    ai_call_log.append({"round": round_num, "caller": "AI_ASSISTANT", "request_type": "simulate_signal", "latency_ms": random.randint(100, 500), "tokens": random.randint(300, 800)})
    _emit_traffic(round_num, "NORTH_SOUTH", "AI_ASSISTANT", "EXTERNAL:LLM", "llm_inference", 2048)
    _emit_event("AI_SIGNAL_SIM", round_num, "AI_ASSISTANT", "VERIFIER", "simulate_signal", f"coverage:{coverage}%")
    return {"status": "success", "result": "signal_simulated", "data": {"coverage_pct": coverage, "heatmap_sample": heatmap, "round": round_num}}
ToolRegistry.register("simulate_signal_tool", simulate_signal_tool)


# ============================================================
# VERIFIER / QA / DEPLOYER
# ============================================================
def verify_coverage_tool(**kwargs):
    """验证覆盖达标"""
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    if not aps:
        return {"status": "error", "result": "no_aps", "data": {}}
    coverage = simulate_coverage(ap_placements=aps, round=round_num)["data"]["coverage_pct"]
    passed = coverage >= TARGET_COVERAGE
    _emit_event("VERIFY_COVERAGE", round_num, "VERIFIER", "PLANNER", "verify_coverage",
                f"{coverage}% {'PASS' if passed else 'FAIL'} (target:{TARGET_COVERAGE}%)")
    return {"status": "success", "result": "pass" if passed else "fail",
            "data": {"coverage_pct": coverage, "target": TARGET_COVERAGE, "passed": passed, "round": round_num}}
ToolRegistry.register("verify_coverage_tool", verify_coverage_tool)


def final_inspection_tool(**kwargs):
    """最终验收"""
    round_num = kwargs.get("round", 0)
    checks = {
        "coverage": random.random() > 0.1,
        "cost_within_budget": sum(e["total_cost"] for e in cost_estimates[-1:]) <= BUDGET if cost_estimates else True,
        "interference_avoided": True,
        "feasibility_ok": random.random() > 0.05,
    }
    all_pass = all(checks.values())
    _emit_event("FINAL_INSPECTION", round_num, "QA_ENGINEER", "PLANNER", "final_inspection",
                "ALL PASS" if all_pass else f"FAIL: {[k for k, v in checks.items() if not v]}")
    return {"status": "success", "result": "pass" if all_pass else "fail", "data": {"checks": checks, "all_pass": all_pass, "round": round_num}}
ToolRegistry.register("final_inspection_tool", final_inspection_tool)


def plan_deployment_tool(**kwargs):
    """制定部署计划"""
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    phases = [{"phase": i + 1, "ap_ids": [ap["id"] for ap in aps[i * 3:(i + 1) * 3]], "duration_h": random.randint(4, 12)}
              for i in range((len(aps) + 2) // 3)]
    _emit_event("DEPLOY_PLAN", round_num, "DEPLOYER", "PLANNER", "plan_deployment", f"{len(phases)} phases")
    return {"status": "success", "result": "plan_created", "data": {"phases": phases, "round": round_num}}
ToolRegistry.register("plan_deployment_tool", plan_deployment_tool)


def record_decision_tool(**kwargs):
    """记录决策"""
    round_num = kwargs.get("round", 0)
    detail = kwargs.get("detail", "decision recorded")
    _emit_event("DECISION_RECORDED", round_num, "DOCUMENTER", "PLANNER", "record", detail)
    return {"status": "success", "result": "recorded", "data": {"detail": detail, "round": round_num}}
ToolRegistry.register("record_decision_tool", record_decision_tool)


# ============================================================
# 补充技能
# ============================================================

def generate_heatmap_tool(**kwargs):
    """RF_ENGINEER: 生成覆盖热力图数据"""
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    grid = []
    for gx in range(0, CAMPUS_W + 1, 50):
        for gy in range(0, CAMPUS_H + 1, 50):
            best_signal = -90
            for ap in aps:
                dist = math.sqrt((gx - ap["x"]) ** 2 + (gy - ap["y"]) ** 2)
                if dist < ap.get("radius", AP_COVERAGE_RADIUS):
                    best_signal = max(best_signal, -30 - int(dist / 2))
            in_int = any(math.sqrt((gx - s["x"]) ** 2 + (gy - s["y"]) ** 2) < s["radius"] for s in INTERFERENCE)
            grid.append({"x": gx, "y": gy, "signal_dbm": best_signal if not in_int else min(best_signal, -85)})
    _emit_event("HEATMAP", round_num, "RF_ENGINEER", "PLANNER", "generate_heatmap", f"{len(grid)} grid points")
    return {"status": "success", "result": "heatmap_generated", "data": {"grid": grid, "round": round_num}}
ToolRegistry.register("generate_heatmap_tool", generate_heatmap_tool)


def report_obstacles_tool(**kwargs):
    """SURVEYOR: 报告部署障碍"""
    round_num = kwargs.get("round", 0)
    obstacles = []
    for ap in ap_placements:
        if not random.random() > 0.85:
            continue
        obstacles.append({"ap_id": ap.get("id", "?"), "issue": random.choice(["电源不可达", "承重不足", "信号遮挡", "无安装支架"]),
                          "x": ap["x"], "y": ap["y"]})
    _emit_event("OBSTACLE_REPORT", round_num, "SURVEYOR", "PLANNER", "report_obstacles", f"{len(obstacles)} obstacles")
    return {"status": "success", "result": "obstacles_reported", "data": {"obstacles": obstacles, "round": round_num}}
ToolRegistry.register("report_obstacles_tool", report_obstacles_tool)


def validate_topology_tool(**kwargs):
    """ARCHITECT: 验证AP网络拓扑"""
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    issues = []
    for i, ap1 in enumerate(aps):
        for ap2 in aps[i + 1:]:
            dist = math.sqrt((ap1["x"] - ap2["x"]) ** 2 + (ap1["y"] - ap2["y"]) ** 2)
            if dist < 20:
                issues.append(f"AP {ap1.get('id', '?')} 与 {ap2.get('id', '?')} 距离过近({dist:.0f}m)")
    valid = len(issues) == 0
    _emit_event("TOPOLOGY_CHECK", round_num, "ARCHITECT", "PLANNER", "validate_topology",
                "PASS" if valid else f"{len(issues)} issues")
    return {"status": "success", "result": "valid" if valid else "issues_found",
            "data": {"valid": valid, "issues": issues, "round": round_num}}
ToolRegistry.register("validate_topology_tool", validate_topology_tool)


def suggest_improvements_tool(**kwargs):
    """AI_ASSISTANT: 基于当前方案建议改进点"""
    round_num = kwargs.get("round", 0)
    current_cov = kwargs.get("current_coverage", 0)
    suggestions = []
    if current_cov < TARGET_COVERAGE:
        suggestions.append({"type": "add_ap", "desc": f"覆盖仅{current_cov}%，建议在盲区边缘增加1-2个AP"})
    for src in INTERFERENCE:
        affected = [ap for ap in ap_placements if math.sqrt((ap["x"] - src["x"]) ** 2 + (ap["y"] - src["y"]) ** 2) < src["radius"]]
        if affected:
            suggestions.append({"type": "relocate", "desc": f"{src['id']}干扰区内有{len(affected)}个AP,建议外移{src['radius'] * 0.3:.0f}m"})
    _emit_event("AI_SUGGEST", round_num, "AI_ASSISTANT", "PLANNER", "suggest_improvements", f"{len(suggestions)} suggestions")
    return {"status": "success", "result": "suggestions_ready", "data": {"suggestions": suggestions, "round": round_num}}
ToolRegistry.register("suggest_improvements_tool", suggest_improvements_tool)


def schedule_tasks_tool(**kwargs):
    """DEPLOYER: 制定部署时间表"""
    round_num = kwargs.get("round", 0)
    aps = kwargs.get("ap_placements", ap_placements)
    schedule = [{"ap_id": ap.get("id", f"AP_{i + 1}"), "start_h": i * 2, "duration_h": random.randint(2, 6),
                 "crew": f"team_{random.choice(['A', 'B', 'C'])}"} for i, ap in enumerate(aps)]
    _emit_event("SCHEDULE", round_num, "DEPLOYER", "PLANNER", "schedule_tasks", f"{len(schedule)} tasks")
    return {"status": "success", "result": "schedule_created", "data": {"schedule": schedule, "round": round_num}}
ToolRegistry.register("schedule_tasks_tool", schedule_tasks_tool)


def acceptance_test_tool(**kwargs):
    """QA_ENGINEER: 验收测试"""
    round_num = kwargs.get("round", 0)
    tests = {
        "signal_strength": random.random() > 0.05,
        "coverage_completeness": random.random() > 0.08,
        "interference_resilience": random.random() > 0.12,
        "throughput_benchmark": random.random() > 0.10,
    }
    all_pass = all(tests.values())
    _emit_event("ACCEPTANCE", round_num, "QA_ENGINEER", "PLANNER", "acceptance_test",
                "ALL PASS" if all_pass else f"FAIL: {[k for k, v in tests.items() if not v]}")
    return {"status": "success", "result": "pass" if all_pass else "fail", "data": {"tests": tests, "all_pass": all_pass, "round": round_num}}
ToolRegistry.register("acceptance_test_tool", acceptance_test_tool)


def archive_solution_tool(**kwargs):
    """DOCUMENTER: 归档最终方案"""
    round_num = kwargs.get("round", 0)
    archive = {
        "ap_count": len(ap_placements),
        "total_cost": sum(e["total_cost"] for e in cost_estimates[-1:]) if cost_estimates else 0,
        "coverage_pct": coverage_reports[-1]["coverage_pct"] if coverage_reports else 0,
        "interference_sources": len(INTERFERENCE),
        "rounds_taken": round_num,
    }
    _emit_event("ARCHIVE", round_num, "DOCUMENTER", "PLANNER", "archive_solution",
                f"{archive['ap_count']} APs, {archive['coverage_pct']}%, ¥{archive['total_cost']}")
    return {"status": "success", "result": "archived", "data": {"archive": archive, "round": round_num}}
ToolRegistry.register("archive_solution_tool", archive_solution_tool)


# ============================================================
# get_dynamic_behavior — 平台每轮调用，基于当前状态返回动态行为剖面
# ============================================================
def get_dynamic_behavior_tool(round_num=0, agent_distribution=None):
    """
    平台每轮启动时调用此函数，基于当前仿真状态返回各分类的动态行为权重。
    返回结构: { category_id: { actions_weight, traffic_mix, avg_payload_kb } }
    平台将返回值与 scale_config.json 的静态 behavior_profile 合并（动态优先）。
    """
    # 采样当前状态
    latest_cov = coverage_reports[-1]["coverage_pct"] if coverage_reports else 0
    latest_cost = cost_estimates[-1] if cost_estimates else None
    ap_count = len(ap_placements)
    budget_remaining = latest_cost["budget_remaining"] if latest_cost else BUDGET
    budget_exhausted = budget_remaining < AP_UNIT_COST

    # 阶段判定（基于状态而非固定轮次）
    if latest_cov >= TARGET_COVERAGE:
        phase = "finalize"       # 达标 → 验收归档
    elif ap_count == 0:
        phase = "bootstrap"      # 冷启动 → 首次AI生成
    elif budget_exhausted:
        phase = "optimize_only"  # 没钱了 → 只能优化不能新增
    elif latest_cov < 60:
        phase = "aggressive"     # 覆盖严重不足 → 全力部署
    else:
        phase = "refine"         # 接近目标 → 精细调整

    dynamic = {
        "_meta": {
            "phase": phase,
            "coverage_pct": latest_cov,
            "ap_count": ap_count,
            "budget_remaining": budget_remaining,
            "target": TARGET_COVERAGE,
        },

        "ap_planner": {
            "actions_weight": {
                "call_ai_optimizer": 0 if (phase == "finalize" or budget_exhausted)
                                       else 0.6 if phase == "bootstrap"
                                       else 0.5 if phase == "aggressive"
                                       else 0.2,
                "evaluate_cost":      0.1 if phase in ("bootstrap", "aggressive")
                                       else 0.3 if phase == "refine"
                                       else 0.1,
                "plan_deployment":    0 if phase in ("bootstrap", "aggressive")
                                       else 0.6 if phase == "finalize"
                                       else 0.3,
                "idle":               0.3 if phase == "bootstrap"
                                       else 0.1,
            },
        },

        "rf_engineer": {
            "actions_weight": {
                "simulate_coverage":    0.6 if phase == "bootstrap"
                                         else 0.4 if phase == "aggressive"
                                         else 0.5 if phase == "refine"
                                         else 0.2,
                "analyze_interference": 0.2 if phase in ("aggressive", "refine")
                                         else 0.3,
                "generate_heatmap":     0.2 if phase in ("refine", "finalize")
                                         else 0.1,
                "idle":                 0.1 if phase != "finalize" else 0.4,
            },
        },

        "cost_analyst": {
            "actions_weight": {
                "evaluate_cost": 0.8 if budget_remaining < BUDGET * 0.3
                                  else 0.6 if phase in ("refine", "aggressive")
                                  else 0.3,
                "idle":           0.2 if budget_remaining < BUDGET * 0.3
                                  else 0.7,
            },
            "traffic_mix_override": {
                "EAST_WEST": 0.50 if budget_remaining < BUDGET * 0.3 else 0.40,
            },
        },

        "surveyor": {
            "actions_weight": {
                "check_feasibility": 0.5 if phase in ("aggressive", "refine") else 0.3,
                "report_obstacles":  0.3 if phase == "refine" else 0.2,
                "idle":               0.5 if phase == "finalize" else 0.2,
            },
        },

        "architect": {
            "actions_weight": {
                "validate_topology": 0.6 if phase in ("aggressive", "refine")
                                      else 0.3 if phase == "bootstrap"
                                      else 0.1,
                "idle":              0.4 if phase == "finalize" else 0.7,
            },
        },

        "ai_assistant": {
            "actions_weight": {
                "optimize_ap_positions": 0   if phase == "finalize"
                                          else 0.5 if phase in ("bootstrap", "aggressive")
                                          else 0.3,
                "simulate_signal":        0.3 if phase in ("aggressive", "refine")
                                          else 0.5 if phase == "finalize"
                                          else 0.2,
                "suggest_improvements":   0.5 if phase == "refine"
                                          else 0.3,
                "idle":                   0.2,
            },
        },

        "verifier": {
            "actions_weight": {
                "verify_coverage": 0.7 if phase in ("refine", "finalize") else 0.4,
                "idle":            0.3 if phase in ("refine", "finalize") else 0.6,
            },
        },

        "deployer": {
            "actions_weight": {
                "plan_deployment": 0 if phase in ("bootstrap", "aggressive")
                                    else 0.7 if phase == "finalize"
                                    else 0.4,
                "schedule_tasks":  0 if phase in ("bootstrap", "aggressive")
                                    else 0.3,
                "idle":            1.0 if phase in ("bootstrap", "aggressive")
                                    else 0.3,
            },
        },

        "qa_engineer": {
            "actions_weight": {
                "final_inspection": 0.5 if phase == "refine"
                                     else 0.8 if phase == "finalize"
                                     else 0.1,
                "acceptance_test":  0.1 if phase != "finalize"
                                     else 0.7,
                "idle":             0.8 if phase in ("bootstrap", "aggressive")
                                     else 0.2,
            },
        },

        "documenter": {
            "actions_weight": {
                "record_decision":  0.5 if phase != "bootstrap" else 0.2,
                "archive_solution": 0   if phase != "finalize"
                                     else 0.8,
                "idle":             0.5 if phase == "bootstrap" else 0.2,
            },
        },
    }

    # 全局流量调整：覆盖率提升后东西向报告流量递减
    if latest_cov > 80:
        ew_reduction = (latest_cov - 80) / 20  # 0→1
        for cat_id in dynamic:
            if cat_id == "_meta":
                continue
            if "traffic_mix_override" not in dynamic[cat_id]:
                dynamic[cat_id]["traffic_mix_override"] = {}
            ov = dynamic[cat_id]["traffic_mix_override"]
            static_mix = {  # fallback from scale_config
                "ap_planner": {"EAST_WEST": 0.60},
                "rf_engineer": {"EAST_WEST": 0.55},
                "cost_analyst": {"EAST_WEST": 0.40},
                "surveyor": {"EAST_WEST": 0.45},
                "architect": {"EAST_WEST": 0.50},
                "verifier": {"EAST_WEST": 0.40},
                "deployer": {"EAST_WEST": 0.50},
                "qa_engineer": {"EAST_WEST": 0.35},
                "documenter": {"EAST_WEST": 0.30},
            }.get(cat_id, {}).get("EAST_WEST", 0.40)
            ov["EAST_WEST"] = round(static_mix * (1 - ew_reduction * 0.5), 2)
            ov["INTERNAL"] = round(1 - ov.get("NORTH_SOUTH", 0.15) - ov["EAST_WEST"], 2)

    return dynamic
ToolRegistry.register("get_dynamic_behavior_tool", get_dynamic_behavior_tool)


# ============================================================
# get_panel_state — 供 GET /api/scenes/{name}/state 调用
# ============================================================
def get_panel_state_tool(**kwargs):
    return {
        "campus": {"width": CAMPUS_W, "height": CAMPUS_H},
        "interference": INTERFERENCE,
        "ap_placements": ap_placements,
        "coverage_reports": coverage_reports,
        "cost_estimates": cost_estimates,
        "ai_call_log": ai_call_log,
        "feasibility_checks": feasibility_checks,
        "budget": {"total": BUDGET, "unit_ap_cost": AP_UNIT_COST, "target_coverage_pct": TARGET_COVERAGE},
        "latest_coverage": coverage_reports[-1] if coverage_reports else None,
        "latest_cost": cost_estimates[-1] if cost_estimates else None,
        "event_log": event_log[-20:],
        "traffic_log": traffic_log[-20:],
    }
ToolRegistry.register("get_panel_state_tool", get_panel_state_tool)
