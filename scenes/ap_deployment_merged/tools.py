import random
import math

# ============================================================
# 园区地图 & 干扰源（固定）
# ============================================================
CAMPUS_W = 1000
CAMPUS_H = 400

INTERFERENCE = [
    {"id": "INT_01", "x": 150, "y": 80,  "radius": 80,  "desc": "变电站电磁干扰"},
    {"id": "INT_02", "x": 420, "y": 280, "radius": 120, "desc": "大型电机设备"},
    {"id": "INT_03", "x": 680, "y": 120, "radius": 60,  "desc": "微波通信塔"},
    {"id": "INT_04", "x": 850, "y": 320, "radius": 100, "desc": "高压输电线"},
    {"id": "INT_05", "x": 300, "y": 350, "radius": 150, "desc": "工业焊接车间"},
]

BUDGET = 50000
AP_UNIT_COST = 3500
AP_COVERAGE_RADIUS = 60
TARGET_COVERAGE = 95
MIN_AP_SPACING = 25

# ============================================================
# 模块级状态
# ============================================================
ap_placements = []         # 已确认的AP [{id, x, y, radius, status:"confirmed"}]
proposed_aps = []           # 待评估的AP [{id, x, y, radius, status:"proposed"|"evaluating"}]
relocating_aps = []         # 正在迁移的AP [{id, from_x, from_y, to_x, to_y, status:"relocating"}]
pending_action = None       # 当前决策 {type, agent, ap_id, detail, visual_effect, round}
decision_log = []           # 决策历史 [{round, agent, action, ap_id, detail, visual_effect}]

coverage_reports = []
cost_estimates = []
ai_call_log = []
feasibility_checks = []
event_log = []
traffic_log = []

_ap_counter = 0
_current_round = 0


def _next_ap_id():
    global _ap_counter; _ap_counter += 1; return f"AP_{_ap_counter}"


def _emit_event(etype, round_num, source, target, action, detail=""):
    event_log.append({"event_type": etype, "round": round_num, "source": source, "target": target, "action": action, "detail": detail})

def _emit_traffic(round_num, ttype, source, target, action, kbytes):
    traffic_log.append({"round": round_num, "type": ttype, "source": source, "target": target, "action": action, "bytes": kbytes * 1024})

def _log_decision(agent, action, ap_id, detail, visual_effect=""):
    decision_log.append({"round": _current_round, "agent": agent, "action": action, "ap_id": ap_id, "detail": detail, "visual_effect": visual_effect})

def _min_interference_dist(px, py):
    return min((math.sqrt((px-s["x"])**2+(py-s["y"])**2)-s["radius"] for s in INTERFERENCE), default=999)

def _is_in_interference(px, py):
    for src in INTERFERENCE:
        if math.sqrt((px-src["x"])**2+(py-src["y"])**2)<src["radius"]: return True, src
    return False, None


# ============================================================
# SkillRegistry
# ============================================================
class ToolRegistry:
    _tools = {}
    @classmethod
    def register(cls, name, fn): cls._tools[name] = fn
    @classmethod
    def execute(cls, name, **kwargs):
        if name not in cls._tools: return {"status":"error","result":None,"data":{"error":f"'{name}' not found"}}
        return cls._tools[name](**kwargs)
    @classmethod
    def list_tools(cls): return list(cls._tools.keys())


# ============================================================
# PLANNER: 逐点部署流程
# ============================================================

def plan_next_ap_tool(**kwargs):
    """
    PLANNER调用AI获取下一个AP的候选位置（每次只返回1个最优位置）。
    visual_effect: "proposed" → 前端显示虚线闪烁新AP
    """
    global _current_round, pending_action
    round_num = kwargs.get("round", _current_round)
    _current_round = round_num

    # 排除已有AP太近的位置
    existing = ap_placements + proposed_aps
    cols, rows = 5, 3
    candidates = []
    for r in range(1, rows+1):
        for c in range(1, cols+1):
            bx = CAMPUS_W*c/(cols+1); by = CAMPUS_H*r/(rows+1)
            best_x, best_y, best_dist = bx, by, _min_interference_dist(bx, by)
            for dx in [-CAMPUS_W*0.06, 0, CAMPUS_W*0.06]:
                for dy in [-CAMPUS_H*0.06, 0, CAMPUS_H*0.06]:
                    tx = max(10, min(CAMPUS_W-10, bx+dx)); ty = max(10, min(CAMPUS_H-10, by+dy))
                    d = _min_interference_dist(tx, ty)
                    if d > best_dist: best_x, best_y, best_dist = tx, ty, d
            # 检查与已有AP的间距
            too_close = any(math.sqrt((best_x-ap["x"])**2+(best_y-ap["y"])**2)<MIN_AP_SPACING for ap in existing)
            candidates.append({"x": round(best_x,1), "y": round(best_y,1), "safe_dist": round(best_dist,1), "too_close": too_close})

    # 选最优候选
    candidates.sort(key=lambda c: (-c["safe_dist"], c["too_close"]))
    best = candidates[0]
    ap_id = _next_ap_id()
    best["id"] = ap_id
    best["radius"] = AP_COVERAGE_RADIUS

    if best["too_close"]:
        # 自动微调
        for _ in range(50):
            tx = best["x"] + (random.random()-0.5)*80; ty = best["y"] + (random.random()-0.5)*80
            tx = max(10, min(CAMPUS_W-10, tx)); ty = max(10, min(CAMPUS_H-10, ty))
            tc = any(math.sqrt((tx-ap["x"])**2+(ty-ap["y"])**2)<MIN_AP_SPACING for ap in existing)
            if not tc:
                best["x"]=round(tx,1); best["y"]=round(ty,1); best["too_close"]=False; break

    best["status"] = "proposed"
    proposed_aps.append(best)

    pending_action = {"type":"propose","agent":"PLANNER","ap_id":ap_id,
                       "x":best["x"],"y":best["y"],"round":round_num,"status":"proposed"}
    _log_decision("PLANNER", "propose", ap_id,
                  f"建议在({best['x']},{best['y']})部署 (安全距离{best['safe_dist']}m)",
                  "APPEAR_DASHED")

    latency = 200 + (len(ap_placements)+1)*50
    tokens = 500 + (len(ap_placements)+1)*100
    ai_call_log.append({"round":round_num,"caller":"PLANNER","ap_id":ap_id,"latency_ms":latency,"tokens":tokens})
    _emit_traffic(round_num, "NORTH_SOUTH", "PLANNER", "AI_ASSISTANT", "single_ap_optimize", tokens*4)
    _emit_event("AP_PROPOSED", round_num, "PLANNER", "AI_ASSISTANT", "propose",
                f"{ap_id} at ({best['x']},{best['y']})")

    return {"status":"success","result":"ap_proposed",
            "data":best}
ToolRegistry.register("plan_next_ap_tool", plan_next_ap_tool)


def confirm_ap_tool(**kwargs):
    """
    PLANNER确认AP位置。visual_effect: "SOLIDIFY" → 前端虚线变实线
    """
    global pending_action
    round_num = kwargs.get("round", _current_round)
    ap_id = kwargs.get("ap_id","")

    ap = next((a for a in proposed_aps if a["id"]==ap_id), None)
    if not ap: return {"status":"error","result":"not_found","data":{}}

    proposed_aps.remove(ap)
    ap["status"] = "confirmed"
    ap_placements.append(ap)

    pending_action = {"type":"confirm","agent":"PLANNER","ap_id":ap_id,"round":round_num,"status":"confirmed"}
    _log_decision("PLANNER", "confirm", ap_id,
                  f"AP_{ap_id} 部署确认 位置({ap['x']},{ap['y']})",
                  "SOLIDIFY")
    _emit_event("AP_CONFIRMED", round_num, "PLANNER", "DEPLOYER", "confirm", ap_id)

    return {"status":"success","result":"confirmed",
            "data":{"ap_id":ap_id,"position":{"x":ap["x"],"y":ap["y"]},"round":round_num}}
ToolRegistry.register("confirm_ap_tool", confirm_ap_tool)


def reject_ap_tool(**kwargs):
    """
    PLANNER否决提案。visual_effect: "FADE_OUT" → 前端虚线消失
    """
    global pending_action
    round_num = kwargs.get("round", _current_round)
    ap_id = kwargs.get("ap_id","")
    reason = kwargs.get("reason","不可行")

    ap = next((a for a in proposed_aps if a["id"]==ap_id), None)
    if ap: proposed_aps.remove(ap)

    pending_action = {"type":"reject","agent":"PLANNER","ap_id":ap_id,"round":round_num,"status":"rejected"}
    _log_decision("PLANNER", "reject", ap_id, reason, "FADE_OUT")
    _emit_event("AP_REJECTED", round_num, "PLANNER", "", "reject", f"{ap_id}: {reason}")

    return {"status":"success","result":"rejected",
            "data":{"ap_id":ap_id,"reason":reason,"round":round_num}}
ToolRegistry.register("reject_ap_tool", reject_ap_tool)


def relocate_ap_tool(**kwargs):
    """
    迁移已有AP到新位置。visual_effect: "FLASH_THEN_DASHED"
    旧位置→闪烁，新位置→虚线，确认后→实线
    """
    global pending_action
    round_num = kwargs.get("round", _current_round)
    ap_id = kwargs.get("ap_id","")
    new_x = kwargs.get("new_x",0)
    new_y = kwargs.get("new_y",0)

    # 找已有AP
    old_ap = next((a for a in ap_placements if a["id"]==ap_id), None)
    if not old_ap: return {"status":"error","result":"not_found","data":{}}

    from_x, from_y = old_ap["x"], old_ap["y"]
    # 旧位置标记闪烁
    relocating_aps.append({"id":ap_id, "from_x":from_x, "from_y":from_y,
                            "to_x":new_x, "to_y":new_y, "status":"relocating"})
    old_ap["x"], old_ap["y"] = new_x, new_y

    pending_action = {"type":"relocate","agent":"PLANNER","ap_id":ap_id,
                       "from_x":from_x,"from_y":from_y,"to_x":new_x,"to_y":new_y,
                       "round":round_num,"status":"relocating"}
    _log_decision("PLANNER", "relocate", ap_id,
                  f"迁移 ({from_x},{from_y})→({new_x},{new_y})",
                  "FLASH_THEN_DASHED")
    _emit_event("AP_RELOCATING", round_num, "PLANNER", "", "relocate",
                f"{ap_id}: ({from_x},{from_y})→({new_x},{new_y})")

    return {"status":"success","result":"relocating",
            "data":{"ap_id":ap_id,"from":{"x":from_x,"y":from_y},"to":{"x":new_x,"y":new_y},"round":round_num}}
ToolRegistry.register("relocate_ap_tool", relocate_ap_tool)


def confirm_relocation_tool(**kwargs):
    """确认迁移完成。visual_effect: "SOLIDIFY" — 新位置虚线消失变实线"""
    round_num = kwargs.get("round", _current_round)
    ap_id = kwargs.get("ap_id","")
    idx = next((i for i,a in enumerate(relocating_aps) if a["id"]==ap_id), None)
    if idx is not None:
        del relocating_aps[idx]
    _log_decision("PLANNER", "confirm_relocate", ap_id, "迁移完成", "SOLIDIFY")
    _emit_event("AP_RELOCATED", round_num, "PLANNER", "", "confirm_relocate", ap_id)
    return {"status":"success","result":"relocation_confirmed","data":{"ap_id":ap_id,"round":round_num}}
ToolRegistry.register("confirm_relocation_tool", confirm_relocation_tool)


# ============================================================
# 评估技能（逐AP评估）
# ============================================================

def evaluate_single_ap_tool(**kwargs):
    """RF_ENGINEER: 评估单个AP的覆盖贡献"""
    round_num = kwargs.get("round", _current_round)
    ap_id = kwargs.get("ap_id","")

    ap = next((a for a in proposed_aps+ap_placements if a["id"]==ap_id), None)
    if not ap: return {"status":"error","result":"not_found","data":{}}

    # 蒙特卡洛采样该AP的覆盖贡献
    samples, covered = 1000, 0
    for _ in range(samples):
        angle = random.random()*2*math.pi; dist = random.random()*AP_COVERAGE_RADIUS
        sx = ap["x"] + math.cos(angle)*dist; sy = ap["y"] + math.sin(angle)*dist
        if 0<=sx<=CAMPUS_W and 0<=sy<=CAMPUS_H:
            in_int, _ = _is_in_interference(sx, sy)
            if not in_int: covered += 1
    coverage_contrib = round(covered/samples*100, 1)

    _emit_traffic(round_num, "EAST_WEST", "RF_ENGINEER", "PLANNER", "single_ap_eval", 8)
    _emit_event("AP_EVALUATED", round_num, "RF_ENGINEER", "PLANNER", "evaluate",
                f"{ap_id}: coverage_contrib={coverage_contrib}%")
    _log_decision("RF_ENGINEER", "evaluate", ap_id,
                  f"覆盖贡献{coverage_contrib}%", "EVALUATING")

    return {"status":"success","result":"evaluated",
            "data":{"ap_id":ap_id,"coverage_contrib":coverage_contrib,"round":round_num}}
ToolRegistry.register("evaluate_single_ap_tool", evaluate_single_ap_tool)


# ============================================================
# 全局评估（保留兼容）
# ============================================================

def simulate_coverage_tool(**kwargs):
    round_num = kwargs.get("round", _current_round)
    aps = ap_placements + proposed_aps
    if not aps: return {"status":"error","result":"no_aps","data":{}}
    samples, covered, blind = 2000, 0, []
    for _ in range(samples):
        sx, sy = random.uniform(0,CAMPUS_W), random.uniform(0,CAMPUS_H)
        in_range = any(math.sqrt((sx-a["x"])**2+(sy-a["y"])**2)<a.get("radius",AP_COVERAGE_RADIUS) for a in aps)
        in_int, _ = _is_in_interference(sx, sy)
        if in_range and not in_int: covered+=1
        elif not in_range: blind.append({"x":round(sx,1),"y":round(sy,1)})
    pct = round(covered/samples*100,1)
    rpt = {"round":round_num,"coverage_pct":pct,"blind_spot_count":len(blind),"ap_count":len(ap_placements),"blind_spots_sample":blind[:15]}
    coverage_reports.append(rpt)
    _emit_traffic(round_num,"EAST_WEST","RF_ENGINEER","PLANNER","coverage_report",16)
    _emit_event("COVERAGE_SIM",round_num,"RF_ENGINEER","PLANNER","simulate_coverage",f"{pct}%")
    return {"status":"success","result":"coverage_simulated","data":rpt}
ToolRegistry.register("simulate_coverage_tool", simulate_coverage_tool)


def analyze_interference_tool(**kwargs):
    round_num = kwargs.get("round", _current_round)
    analysis = []
    for src in INTERFERENCE:
        aff = [ap["id"] for ap in ap_placements if math.sqrt((ap["x"]-src["x"])**2+(ap["y"]-src["y"])**2)<src["radius"]]
        analysis.append({"source_id":src["id"],"desc":src["desc"],"radius":src["radius"],"affected_aps":aff,"affected_count":len(aff)})
    _emit_event("INTERFERENCE_ANALYSIS",round_num,"RF_ENGINEER","PLANNER","analyze",f"{len(INTERFERENCE)} sources")
    return {"status":"success","result":"analysis_complete","data":{"sources":analysis,"round":round_num}}
ToolRegistry.register("analyze_interference_tool", analyze_interference_tool)


def generate_heatmap_tool(**kwargs):
    round_num = kwargs.get("round", _current_round)
    aps = ap_placements + proposed_aps; grid = []
    for gx in range(0,CAMPUS_W+1,25):
        for gy in range(0,CAMPUS_H+1,25):
            best=-90
            for ap in aps:
                d=math.sqrt((gx-ap["x"])**2+(gy-ap["y"])**2)
                if d<ap.get("radius",AP_COVERAGE_RADIUS): best=max(best,-30-int(d/2))
            in_int,_=_is_in_interference(gx,gy)
            if in_int: best=min(best,-85)
            grid.append({"x":gx,"y":gy,"signal_dbm":best})
    _emit_event("HEATMAP",round_num,"RF_ENGINEER","PLANNER","generate_heatmap",f"{len(grid)} points")
    return {"status":"success","result":"heatmap_generated","data":{"grid":grid,"round":round_num}}
ToolRegistry.register("generate_heatmap_tool", generate_heatmap_tool)


def evaluate_cost_tool(**kwargs):
    round_num = kwargs.get("round", _current_round)
    ap_count = len(ap_placements)
    extra = random.randint(2000,8000)
    total = ap_count*AP_UNIT_COST + extra
    remaining = BUDGET-total
    est = {"round":round_num,"ap_count":ap_count,"unit_cost":AP_UNIT_COST,"extra_cost":extra,"total_cost":total,"budget_remaining":remaining,"within_budget":remaining>=0}
    cost_estimates.append(est)
    _emit_event("COST_EVAL",round_num,"COST_ANALYST","PLANNER","evaluate_cost",f"{ap_count}APs ¥{total}")
    _emit_traffic(round_num,"EAST_WEST","COST_ANALYST","PLANNER","cost_report",8)
    return {"status":"success","result":"cost_evaluated","data":est}
ToolRegistry.register("evaluate_cost_tool", evaluate_cost_tool)


def check_feasibility_tool(**kwargs):
    round_num = kwargs.get("round", _current_round)
    aps = kwargs.get("ap_placements", ap_placements)
    checks = []
    for ap in aps:
        feasible = random.random()>0.15
        issue = None if feasible else random.choice(["电源不可达","承重不足","信号遮挡","无安装支架"])
        checks.append({"ap_id":ap.get("id","?"),"feasible":feasible,"issue":issue})
        feasibility_checks.append({"round":round_num,"ap_id":ap.get("id","?"),"feasible":feasible,"issue":issue})
    fc=sum(1 for c in checks if c["feasible"])
    _emit_event("FEASIBILITY",round_num,"SURVEYOR","PLANNER","check_feasibility",f"{fc}/{len(checks)}")
    return {"status":"success","result":"feasibility_checked","data":{"checks":checks,"feasible_count":fc,"total":len(checks),"round":round_num}}
ToolRegistry.register("check_feasibility_tool", check_feasibility_tool)


def report_obstacles_tool(**kwargs):
    round_num = kwargs.get("round", _current_round)
    obstacles = []
    for ap in ap_placements:
        if random.random()>0.85: continue
        obstacles.append({"ap_id":ap.get("id","?"),"issue":random.choice(["电源不可达","承重不足","信号遮挡","无安装支架"]),"x":ap["x"],"y":ap["y"]})
    _emit_event("OBSTACLE",round_num,"SURVEYOR","PLANNER","report_obstacles",f"{len(obstacles)}")
    return {"status":"success","result":"obstacles_reported","data":{"obstacles":obstacles,"round":round_num}}
ToolRegistry.register("report_obstacles_tool", report_obstacles_tool)


def validate_topology_tool(**kwargs):
    round_num = kwargs.get("round", _current_round)
    issues = []
    for i,ap1 in enumerate(ap_placements):
        for ap2 in ap_placements[i+1:]:
            d=math.sqrt((ap1["x"]-ap2["x"])**2+(ap1["y"]-ap2["y"])**2)
            if d<MIN_AP_SPACING: issues.append(f"{ap1.get('id','?')}与{ap2.get('id','?')}间距{d:.0f}m")
    valid=len(issues)==0
    _emit_event("TOPOLOGY",round_num,"ARCHITECT","PLANNER","validate_topology","PASS" if valid else f"{len(issues)} issues")
    return {"status":"success","result":"valid" if valid else "issues_found","data":{"valid":valid,"issues":issues,"round":round_num}}
ToolRegistry.register("validate_topology_tool", validate_topology_tool)


def optimize_ap_positions_tool(**kwargs):
    return plan_next_ap(**kwargs)
ToolRegistry.register("optimize_ap_positions_tool", optimize_ap_positions_tool)


def simulate_signal_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    aps=ap_placements+proposed_aps
    if not aps:return{"status":"error","result":"no_aps","data":{}}
    samples,covered,heat=1500,0,[]
    for _ in range(samples):
        sx,sy=random.uniform(0,CAMPUS_W),random.uniform(0,CAMPUS_H)
        best=max((-50-random.randint(0,30) for ap in aps if math.sqrt((sx-ap["x"])**2+(sy-ap["y"])**2)<ap.get("radius",AP_COVERAGE_RADIUS)),default=-90)
        in_int,_=_is_in_interference(sx,sy)
        if in_int:best=min(best,-85)
        if best>-75 and not in_int:covered+=1
        if len(heat)<50:heat.append({"x":round(sx,1),"y":round(sy,1),"signal_dbm":best})
    pct=round(covered/samples*100,1)
    ai_call_log.append({"round":round_num,"caller":"AI_ASSISTANT","latency_ms":random.randint(100,500),"tokens":random.randint(300,800)})
    _emit_traffic(round_num,"NORTH_SOUTH","AI_ASSISTANT","EXTERNAL:LLM","signal_sim",2048)
    _emit_event("AI_SIGNAL",round_num,"AI_ASSISTANT","VERIFIER","simulate_signal",f"{pct}%")
    return {"status":"success","result":"signal_simulated","data":{"coverage_pct":pct,"heatmap_sample":heat,"round":round_num}}
ToolRegistry.register("simulate_signal_tool", simulate_signal_tool)


def suggest_improvements_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    cov=kwargs.get("current_coverage",0)
    suggestions=[]
    if cov<TARGET_COVERAGE:
        gap=TARGET_COVERAGE-cov;extra=max(1,int(gap/5))
        suggestions.append({"type":"add_ap","desc":f"覆盖{cov}%距目标{TARGET_COVERAGE}%差{gap}%，建议增加{extra}个AP"})
    for src in INTERFERENCE:
        aff=[ap for ap in ap_placements if math.sqrt((ap["x"]-src["x"])**2+(ap["y"]-src["y"])**2)<src["radius"]]
        if aff: suggestions.append({"type":"relocate","desc":f"{src['desc']}干扰区内有{len(aff)}个AP,建议外移"})
    _emit_event("AI_SUGGEST",round_num,"AI_ASSISTANT","PLANNER","suggest",f"{len(suggestions)}")
    return {"status":"success","result":"suggestions_ready","data":{"suggestions":suggestions,"round":round_num}}
ToolRegistry.register("suggest_improvements_tool", suggest_improvements_tool)


def verify_coverage_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    if not coverage_reports:return{"status":"error","result":"no_data","data":{}}
    cov=coverage_reports[-1]["coverage_pct"]
    passed=cov>=TARGET_COVERAGE
    _emit_event("VERIFY",round_num,"VERIFIER","PLANNER","verify_coverage",f"{cov}%")
    return {"status":"success","result":"pass" if passed else "fail","data":{"coverage_pct":cov,"target":TARGET_COVERAGE,"passed":passed,"round":round_num}}
ToolRegistry.register("verify_coverage_tool", verify_coverage_tool)


def final_inspection_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    cov_ok=coverage_reports[-1]["coverage_pct"]>=TARGET_COVERAGE if coverage_reports else False
    budget_ok=cost_estimates[-1]["within_budget"] if cost_estimates else False
    checks={"coverage":cov_ok,"budget":budget_ok}
    all_pass=all(checks.values())
    _emit_event("INSPECTION",round_num,"QA_ENGINEER","PLANNER","final_inspection","ALL PASS" if all_pass else "FAIL")
    return {"status":"success","result":"pass" if all_pass else "fail","data":{"checks":checks,"all_pass":all_pass,"round":round_num}}
ToolRegistry.register("final_inspection_tool", final_inspection_tool)


def acceptance_test_tool(**kwargs):
    return final_inspection(**kwargs)
ToolRegistry.register("acceptance_test_tool", acceptance_test_tool)


def plan_deployment_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    phases=[{"phase":i+1,"ap_ids":[ap["id"] for ap in ap_placements[i*3:(i+1)*3]],"duration_h":random.randint(4,12)} for i in range((len(ap_placements)+2)//3)]
    _emit_event("DEPLOY_PLAN",round_num,"DEPLOYER","PLANNER","plan_deployment",f"{len(phases)} phases")
    return {"status":"success","result":"plan_created","data":{"phases":phases,"round":round_num}}
ToolRegistry.register("plan_deployment_tool", plan_deployment_tool)


def schedule_tasks_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    sched=[{"ap_id":ap.get("id",f"AP_{i+1}"),"start_h":i*2,"duration_h":random.randint(2,6),"crew":f"team_{random.choice(['A','B','C'])}"} for i,ap in enumerate(ap_placements)]
    _emit_event("SCHEDULE",round_num,"DEPLOYER","PLANNER","schedule_tasks",f"{len(sched)} tasks")
    return {"status":"success","result":"schedule_created","data":{"schedule":sched,"round":round_num}}
ToolRegistry.register("schedule_tasks_tool", schedule_tasks_tool)


def record_decision_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    detail=kwargs.get("detail","decision recorded")
    _emit_event("RECORD",round_num,"DOCUMENTER","PLANNER","record",detail)
    return {"status":"success","result":"recorded","data":{"detail":detail,"round":round_num}}
ToolRegistry.register("record_decision_tool", record_decision_tool)


def archive_solution_tool(**kwargs):
    round_num=kwargs.get("round",_current_round)
    a={"ap_count":len(ap_placements),"total_cost":cost_estimates[-1]["total_cost"] if cost_estimates else 0,"coverage_pct":coverage_reports[-1]["coverage_pct"] if coverage_reports else 0,"rounds_taken":round_num}
    _emit_event("ARCHIVE",round_num,"DOCUMENTER","PLANNER","archive",f"{a['ap_count']}APs {a['coverage_pct']}%")
    return {"status":"success","result":"archived","data":{"archive":a,"round":round_num}}
ToolRegistry.register("archive_solution_tool", archive_solution_tool)


# ============================================================
# get_panel_state
# ============================================================
def get_panel_state_tool(**kwargs):
    return {
        "ap_placements": ap_placements,
        "proposed_aps": proposed_aps,
        "relocating_aps": relocating_aps,
        "pending_action": pending_action,
        "decision_log": decision_log[-30:],
        "campus": {"width": CAMPUS_W, "height": CAMPUS_H},
        "interference": INTERFERENCE,
        "coverage_reports": coverage_reports,
        "cost_estimates": cost_estimates,
        "ai_call_log": ai_call_log,
        "feasibility_checks": feasibility_checks,
        "latest_coverage": coverage_reports[-1] if coverage_reports else None,
        "latest_cost": cost_estimates[-1] if cost_estimates else None,
        "budget": {"total": BUDGET, "unit_ap_cost": AP_UNIT_COST, "target_coverage_pct": TARGET_COVERAGE},
        "event_log": event_log[-20:],
        "traffic_log": traffic_log[-20:],
    }
ToolRegistry.register("get_panel_state_tool", get_panel_state_tool)
