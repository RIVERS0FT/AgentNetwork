import random
import time

# ============================================================
# 模块级状态 — 三类流量追踪
# ============================================================
traffic_log = []       # 流量事件 [{round, type, source, target, action, bytes}]
event_log = []         # 业务事件 [{event_type, round, source, target, action, detail}]

git_commits = []
model_submissions = []
design_submissions = []
documents = []
test_reports = []
external_api_calls = []
ci_pipelines = []
reviews = []


# 中文名 → agent_id 映射
_NAME_TO_ID = {
    "前端开发工程师": "dev_fe", "后端开发工程师": "dev_be",
    "嵌入式固件工程师": "dev_fw", "AI模型训练工程师": "dev_ai",
    "IC设计工程师": "dev_ic", "技术架构师": "architect",
    "产品经理": "pm", "技术文档工程师": "doc_writer",
    "QA测试工程师": "qa", "内部代码仓库管理员": "repo_admin",
    "DevOps运维工程师": "dev_ops",
}


def _resolve_actor(value, fallback=""):
    """将 LLM 传的 actor（可能是中文名、agent_id 或 unknown）归一化为 agent_id"""
    v = str(value or "").strip()
    if not v or v.lower() in ("unknown", "none", "null"):
        return fallback
    # 中文名 → agent_id
    if v in _NAME_TO_ID:
        return _NAME_TO_ID[v]
    # 已经是 agent_id（含下划线）
    if "_" in v.lower():
        return v.lower()
    # 全大写可能是 agent_id
    return v.lower()


def _is_missing_actor(value):
    v = str(value or "").strip()
    return not v or v.lower() in ("unknown", "none", "null")

def _emit_traffic(round_num, traffic_type, source, target, action, bytes_est=0):
    event = {
        "round": round_num,
        "type": traffic_type,
        "source": source,
        "target": target,
        "action": action,
        "bytes": bytes_est,
    }
    traffic_log.append(event)
    return event


def _emit_event(event_type, round_num, source, target, action, detail=""):
    e = {"event_type": event_type, "round": round_num, "source": source, "target": target, "action": action, "detail": detail}
    event_log.append(e)
    return e


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
# 开发侧技能
# ============================================================

def submit_code_tool(**kwargs):
    """
    提交代码到Git仓库，触发CI/CD。
    参数: developer(str), repo(str), files_changed(int), round(int)
    """
    developer = _resolve_actor(kwargs.get("developer", ""), "dev_fw")
    repo = kwargs.get("repo", "main")
    files = kwargs.get("files_changed", random.randint(1, 5))
    current_round = kwargs.get("round", 0)

    commit_id = f"commit_{len(git_commits)+1}_{int(time.time()%100000)}"
    git_commits.append({"developer": developer, "repo": repo, "commit_id": commit_id, "files": files, "round": current_round})

    _emit_traffic(current_round, "EAST_WEST", developer, "REPO_ADMIN", "git_push", files * 2048)

    pipeline_id = f"ci_{len(ci_pipelines)+1}"
    build_result = random.choice(["success", "success", "success", "failed"])
    ci_pipelines.append({"pipeline_id": pipeline_id, "triggered_by": commit_id, "status": build_result, "round": current_round})
    _emit_traffic(current_round, "INTERNAL", "REPO_ADMIN", "CI_RUNNER", "trigger_pipeline", 512)
    _emit_traffic(current_round, "INTERNAL", "CI_RUNNER", "REPO_ADMIN", "build_result", 1024)

    _emit_event("CODE_SUBMITTED", current_round, developer, "REPO_ADMIN", "push", f"{commit_id} ({files} files) | CI: {build_result}")

    return {
        "status": "success", "result": "code_submitted",
        "data": {"commit_id": commit_id, "files": files, "pipeline_id": pipeline_id, "round": current_round}
    }
ToolRegistry.register("submit_code_tool", submit_code_tool)


# ============================================================
# AI/IC 侧技能
# ============================================================

def submit_model_tool(**kwargs):
    """
    提交训练好的模型文件。
    参数: developer(str), model_name(str), size_mb(float), round(int)
    """
    developer = _resolve_actor(kwargs.get("developer", ""), "dev_ai")
    model_name = kwargs.get("model_name", "model_v1")
    size_mb = kwargs.get("size_mb", random.randint(50, 500))
    current_round = kwargs.get("round", 0)

    model_id = f"model_{len(model_submissions)+1}_{int(time.time()%100000)}"
    model_submissions.append({"developer": developer, "model_name": model_name, "model_id": model_id, "size_mb": size_mb, "round": current_round})

    _emit_traffic(current_round, "INTERNAL", developer, "REPO_ADMIN", "model_push", int(size_mb * 1_048_576))
    _emit_event("MODEL_SUBMITTED", current_round, developer, "REPO_ADMIN", "push", f"{model_id} ({size_mb}MB)")

    return {"status": "success", "result": "model_submitted", "data": {"model_id": model_id, "size_mb": size_mb, "round": current_round}}
ToolRegistry.register("submit_model_tool", submit_model_tool)


def submit_design_tool(**kwargs):
    """
    提交芯片设计文件。
    参数: developer(str), design_name(str), size_mb(float), round(int)
    """
    developer = _resolve_actor(kwargs.get("developer", ""), "dev_ic")
    design_name = kwargs.get("design_name", "design_v1")
    size_mb = kwargs.get("size_mb", random.randint(100, 2000))
    current_round = kwargs.get("round", 0)

    design_id = f"design_{len(design_submissions)+1}_{int(time.time()%100000)}"
    design_submissions.append({"developer": developer, "design_name": design_name, "design_id": design_id, "size_mb": size_mb, "round": current_round})

    _emit_traffic(current_round, "INTERNAL", developer, "REPO_ADMIN", "design_push", int(size_mb * 1_048_576))
    _emit_event("DESIGN_SUBMITTED", current_round, developer, "REPO_ADMIN", "push", f"{design_id} ({size_mb}MB)")

    return {"status": "success", "result": "design_submitted", "data": {"design_id": design_id, "size_mb": size_mb, "round": current_round}}
ToolRegistry.register("submit_design_tool", submit_design_tool)


def request_external_api_tool(**kwargs):
    """
    请求外部API资源（LLM推理/EDA云仿真等）。
    参数: requester(str), api_name(str), payload_size(float,KB), round(int)
    流量: requester→external (南北向)
    """
    requester = _resolve_actor(kwargs.get("requester", ""), "dev_ai")
    api_name = kwargs.get("api_name", "external_service")
    payload_size = kwargs.get("payload_size", random.randint(1, 100))
    current_round = kwargs.get("round", 0)

    # 每轮限制10次外部调用
    current_count = len([c for c in external_api_calls if c["round"] == current_round])
    if current_count >= 10:
        _emit_event("API_BLOCKED", current_round, requester, "EXTERNAL", "rate_limited", api_name)
        return {"status": "error", "result": "rate_limited", "data": {"api_name": api_name, "reason": "超过限流阈值"}}

    call_id = f"api_{len(external_api_calls)+1}_{int(time.time()%100000)}"
    latency_ms = random.randint(50, 500)

    _emit_traffic(current_round, "NORTH_SOUTH", requester, f"EXTERNAL:{api_name}", "api_request", int(payload_size * 1024))

    resp_size = payload_size * random.uniform(0.5, 2.0)
    _emit_traffic(current_round, "NORTH_SOUTH", f"EXTERNAL:{api_name}", requester, "api_response", int(resp_size * 1024))

    external_api_calls.append({"requester": requester, "api_name": api_name, "call_id": call_id, "payload_kb": payload_size, "round": current_round, "latency_ms": latency_ms})
    _emit_event("EXTERNAL_API_CALL", current_round, requester, "EXTERNAL", api_name, f"{call_id} ({payload_size}KB, {latency_ms}ms)")

    return {
        "status": "success", "result": "api_call_completed",
        "data": {"call_id": call_id, "api_name": api_name, "payload_kb": payload_size, "response_kb": round(resp_size, 1), "latency_ms": latency_ms, "round": current_round}
    }
ToolRegistry.register("request_external_api_tool", request_external_api_tool)


# ============================================================
# 架构师/PM/文档侧技能
# ============================================================

def review_document_tool(**kwargs):
    """
    审查设计文档并通知相关方。
    参数: reviewer(str), doc_id(str), target_dev(str), round(int)
    """
    reviewer = _resolve_actor(kwargs.get("reviewer", ""), "architect")
    doc_id = kwargs.get("doc_id", f"doc_{len(documents)+1}")
    target_dev = kwargs.get("target_dev", "")
    current_round = kwargs.get("round", 0)

    decision = random.choice(["approved", "revision_required"])
    reviews.append({"reviewer": reviewer, "doc_id": doc_id, "decision": decision, "target_dev": target_dev, "round": current_round})
    _emit_traffic(current_round, "EAST_WEST", reviewer, target_dev or "DEV_TEAM", "review_feedback", 4096)

    if decision == "revision_required" and target_dev:
        ToolRegistry.execute("notify_team", sender=reviewer, target=target_dev,
                              message=f"文档 {doc_id} 需修改", round=current_round)

    _emit_event("DOC_REVIEWED", current_round, reviewer, target_dev or "DEV_TEAM", decision, doc_id)

    return {"status": "success", "result": decision, "data": {"doc_id": doc_id, "decision": decision, "target_dev": target_dev, "round": current_round}}
ToolRegistry.register("review_document_tool", review_document_tool)


def write_document_tool(**kwargs):
    """
    编写/协作编辑文档。
    参数: author(str), doc_type(str), title(str), round(int)
    """
    doc_type = kwargs.get("doc_type", "unknown")
    title = kwargs.get("title", "unknown")
    author = _resolve_actor(kwargs.get("author", ""), "doc_writer")
    current_round = kwargs.get("round", 0)

    doc_id = f"doc_{len(documents)+1}_{int(time.time()%100000)}"
    size_kb = random.randint(10, 200)
    documents.append({"author": author, "type": doc_type, "doc_id": doc_id, "title": title, "size_kb": size_kb, "status": "draft", "round": current_round})

    _emit_traffic(current_round, "INTERNAL", author, "REPO_ADMIN", "doc_push", size_kb * 1024)
    _emit_event("DOC_CREATED", current_round, author, "REPO_ADMIN", doc_type, f"{doc_id}: {title}")

    return {"status": "success", "result": "document_created", "data": {"doc_id": doc_id, "title": title, "size_kb": size_kb, "round": current_round}}
ToolRegistry.register("write_document_tool", write_document_tool)


# ============================================================
# 通知/测试/CI 侧技能
# ============================================================

def notify_team_tool(**kwargs):
    """
    发送通知。
    参数: sender(str), target(str), message(str), round(int)
    """
    sender = kwargs.get("sender", "unknown")
    target = kwargs.get("target", "unknown")
    message = kwargs.get("message", "")
    current_round = kwargs.get("round", 0)

    _emit_traffic(current_round, "EAST_WEST", sender, target, "notify", len(message.encode()) if message else 256)
    _emit_event("NOTIFY", current_round, sender, target, "notify", message[:80])

    return {"status": "success", "result": "notified", "data": {"sender": sender, "target": target, "round": current_round}}
ToolRegistry.register("notify_team_tool", notify_team_tool)


def run_test_tool(**kwargs):
    """
    执行自动化测试。
    参数: tester(str), target(str), test_suite(str), round(int)
    """
    tester = _resolve_actor(kwargs.get("tester", ""), "qa")
    target = kwargs.get("target", "DEV_FE")
    test_suite = kwargs.get("test_suite", "regression")
    current_round = kwargs.get("round", 0)

    test_id = f"test_{len(test_reports)+1}_{int(time.time()%100000)}"
    passed = random.random() > 0.3
    test_reports.append({"tester": tester, "target": target, "test_id": test_id, "passed": passed, "round": current_round})

    _emit_traffic(current_round, "EAST_WEST", tester, target, "test_report", 2048)
    _emit_event("TEST_COMPLETED", current_round, tester, target, "passed" if passed else "failed", test_id)

    if not passed:
        ToolRegistry.execute("notify_team", sender=tester, target=target, message=f"测试失败: {test_suite}", round=current_round)

    return {"status": "success", "result": "passed" if passed else "failed", "data": {"test_id": test_id, "passed": passed, "target": target, "round": current_round}}
ToolRegistry.register("run_test_tool", run_test_tool)


def handle_push_tool(**kwargs):
    """
    处理推送，触发CI/CD流水线。
    参数: pusher(str), push_type(str: code|model|design|doc), artifact_id(str), round(int)
    """
    pusher = _resolve_actor(kwargs.get("pusher", ""), "repo_admin")
    handled_by = kwargs.get("handled_by", "REPO_ADMIN")
    if _is_missing_actor(handled_by):
        handled_by = "REPO_ADMIN"
    push_type = kwargs.get("push_type", "code")
    artifact_id = kwargs.get("artifact_id", "unknown")
    current_round = kwargs.get("round", 0)

    pipeline_id = f"ci_{len(ci_pipelines)+1}_{int(time.time()%100000)}"
    ci_pipelines.append({
        "pipeline_id": pipeline_id,
        "type": push_type,
        "triggered_by": pusher,
        "handled_by": handled_by,
        "status": "running",
        "round": current_round,
    })

    _emit_traffic(current_round, "INTERNAL", handled_by, "CI_RUNNER", "trigger_build", 4096)
    _emit_event("PUSH_HANDLED", current_round, handled_by, pusher, push_type, f"{artifact_id}->{pipeline_id}")

    build_result = random.choice(["success", "success", "success", "failed"])
    ci_pipelines[-1]["status"] = build_result
    _emit_traffic(current_round, "INTERNAL", "CI_RUNNER", "REPO_ADMIN", "build_result", 1024)

    if build_result == "success" and push_type in ("code", "model"):
        img_size_mb = random.randint(50, 500)
        _emit_traffic(current_round, "INTERNAL", "REPO_ADMIN", "REGISTRY", "image_push", int(img_size_mb * 1_048_576))
        _emit_event("IMAGE_PUSHED", current_round, "REPO_ADMIN", "REGISTRY", "push", f"{pipeline_id} ({img_size_mb}MB)")

    return {
        "status": "success", "result": build_result,
        "data": {"pipeline_id": pipeline_id, "push_type": push_type, "build_result": build_result, "handled_by": handled_by, "round": current_round}
    }
ToolRegistry.register("handle_push_tool", handle_push_tool)


def review_code_tool(**kwargs):
    """
    仓库管理员审阅代码提交，决定是否合并。
    参数: reviewer(str), commit_id(str), author(str), files_changed(int), round(int)
    流量: reviewer→author (东西向通知)
    """
    reviewer = kwargs.get("reviewer", "REPO_ADMIN")
    commit_id = kwargs.get("commit_id", "unknown")
    author = kwargs.get("author", "unknown")
    files = kwargs.get("files_changed", random.randint(1, 10))
    current_round = kwargs.get("round", 0)

    # 审查决策：代码质量 + 测试覆盖
    passed = random.random() > 0.2
    issues = []
    if not passed:
        issues = random.sample(["代码风格不符合规范", "缺少单元测试", "存在潜在空指针", "未处理边界条件", "缺少错误处理"], random.randint(1, 2))

    _emit_traffic(current_round, "EAST_WEST", reviewer, author, "code_review", files * 1024)

    if not passed and issues:
        ToolRegistry.execute("notify_team", sender=reviewer, target=author,
                              message=f"代码审查未通过: {commit_id} - {'; '.join(issues)}", round=current_round)

    _emit_event("CODE_REVIEWED", current_round, reviewer, author,
                "approved" if passed else "rejected", f"{commit_id} ({files} files)")

    return {
        "status": "success", "result": "approved" if passed else "rejected",
        "data": {"commit_id": commit_id, "passed": passed, "issues": issues, "files": files, "round": current_round}
    }
ToolRegistry.register("review_code_tool", review_code_tool)


def trigger_ci_cd_tool(**kwargs):
    """
    手动触发CI/CD流水线。
    参数: trigger_by(str), target_artifact(str), round(int)
    """
    trigger_by = kwargs.get("trigger_by", "DEV_OPS")
    if _is_missing_actor(trigger_by):
        trigger_by = "DEV_OPS"
    target_artifact = kwargs.get("target_artifact", "latest")
    current_round = kwargs.get("round", 0)

    pipeline_id = f"ci_{len(ci_pipelines)+1}_{int(time.time()%100000)}"
    build_result = random.choice(["success", "success", "success", "failed"])
    ci_pipelines.append({"pipeline_id": pipeline_id, "type": "manual", "triggered_by": trigger_by, "status": build_result, "round": current_round})

    _emit_traffic(current_round, "INTERNAL", trigger_by, "CI_RUNNER", "manual_trigger", 512)
    _emit_traffic(current_round, "INTERNAL", "CI_RUNNER", "REPO_ADMIN", "build_result", 1024)
    _emit_event("CI_TRIGGERED", current_round, trigger_by, "CI_RUNNER", build_result, pipeline_id)

    return {"status": "success", "result": build_result, "data": {"pipeline_id": pipeline_id, "round": current_round}}
ToolRegistry.register("trigger_ci_cd_tool", trigger_ci_cd_tool)


# ============================================================
# 统一 Panel State — 供 /api/scenes/state 调用
# ============================================================

def get_panel_state():
    """返回场景自定义面板数据，由 /api/scenes/state 的 custom 字段透传"""
    import json
    from pathlib import Path as _Path

    _base = _Path(__file__).parent

    # ── 静态配置（只读一次，缓存为模块级） ──
    if not hasattr(get_panel_state, '_roles'):
        try:
            with open(_base / "meta_and_roles.json", "r", encoding="utf-8") as f:
                get_panel_state._roles = json.load(f).get("roles", {})
        except Exception:
            get_panel_state._roles = {}
    if not hasattr(get_panel_state, '_tools_map'):
        try:
            with open(_base / "instances_and_tools.json", "r", encoding="utf-8") as f:
                _inst = json.load(f)
            get_panel_state._tools_map = _inst.get("container_instances", {})
        except Exception:
            get_panel_state._tools_map = {}

    # ── 运行时流量统计 ──
    ew = [t for t in traffic_log if t["type"] == "EAST_WEST"]
    ns = [t for t in traffic_log if t["type"] == "NORTH_SOUTH"]
    it = [t for t in traffic_log if t["type"] == "INTERNAL"]

    # ── 任务统计 ──
    task_stats = {
        "git_commits": len(git_commits),
        "model_submissions": len(model_submissions),
        "design_submissions": len(design_submissions),
        "documents": len(documents),
        "reviews": len(reviews),
        "test_reports": len(test_reports),
        "external_api_calls": len(external_api_calls),
        "test_pass_rate": round(
            sum(1 for t in test_reports if t.get("passed")) / max(len(test_reports), 1) * 100, 1
        ) if test_reports else 0,
    }

    # ── CI/CD ──
    ci_status = {
        "total": len(ci_pipelines),
        "running": len([p for p in ci_pipelines if p.get("status") == "running"]),
        "success": len([p for p in ci_pipelines if p.get("status") == "success"]),
        "failed": len([p for p in ci_pipelines if p.get("status") == "failed"]),
    }

    # ── 最近事件 ──
    recent_events = event_log[-20:] if event_log else []

    # ── 统一的 Agent 进度：从 event_log.source 统计 ──
    from collections import Counter
    actor_counts = Counter(e.get("source", "").lower() for e in event_log)

    # CI 事件同时计入 repo_admin 和 dev_ops
    ci_events = sum(1 for e in event_log
                    if e.get("event_type") in ("CI_TRIGGERED", "PUSH_HANDLED", "IMAGE_PUSHED"))

    goals = {"dev_fe": 3, "dev_be": 2, "dev_fw": 3, "dev_ai": 1, "dev_ic": 1,
             "architect": 5, "pm": 3, "doc_writer": 4, "qa": 3,
             "repo_admin": 5, "dev_ops": 3}
    agent_progress = {}
    for aid, goal in goals.items():
        done = actor_counts.get(aid, 0)
        # CI 事件均分给 repo_admin 和 dev_ops
        if aid == "repo_admin":
            done = max(done, ci_events // 2)
        elif aid == "dev_ops":
            done = max(done, ci_events - ci_events // 2)
        agent_progress[aid.upper()] = {"done": min(done, goal), "goal": goal}

    return {
        "agent_progress": agent_progress,
        "traffic": {
            "EAST_WEST":   {"count": len(ew), "total_kb": sum(t["bytes"] for t in ew) // 1024},
            "NORTH_SOUTH": {"count": len(ns), "total_kb": sum(t["bytes"] for t in ns) // 1024},
            "INTERNAL":    {"count": len(it), "total_kb": sum(t["bytes"] for t in it) // 1024},
        },
        "task_stats": task_stats,
        "ci_status": ci_status,
        "recent_events": [{"round": e["round"], "type": e["event_type"],
                           "source": e["source"], "target": e["target"],
                           "action": e["action"], "detail": e["detail"]}
                          for e in recent_events],
    }


# ============================================================
# 前端可视化查询接口 — 供 panel.html 通过 API 调用
# ============================================================

def query_dashboard_tool(**kwargs):
    """
    获取面板全量数据。无参数，返回 agent/拓扑/流量/任务/事件的聚合快照。
    """
    current_round = kwargs.get("round", 0)

    # 通信拓扑节点（meta_and_roles 的角色）
    from pathlib import Path as _Path
    import json as _json
    _base = str(_Path(__file__).parent)
    try:
        with open(_Path(_base) / "meta_and_roles.json", "r", encoding="utf-8") as f:
            _meta = _json.load(f)
        _roles = _meta.get("roles", {})
    except Exception:
        _roles = {}

    # Agent 状态卡片
    agents = []
    for rid, rd in _roles.items():
        agents.append({
            "agent_id": rid,
            "name": rd.get("name", rid),
            "identity": rd.get("identity", ""),
            "category": rid,  # 小规模下 role 即 category
            "model_backbone": rd.get("model_backbone", ""),
            "skills": [],  # 由 instances_and_tools 注入
            "status": "idle",
            "core_goal": rd.get("core_goal", ""),
        })

    # 注入技能
    try:
        with open(_Path(_base) / "instances_and_tools.json", "r", encoding="utf-8") as f:
            _inst = _json.load(f)
        _containers = _inst.get("container_instances", {})
        for a in agents:
            ci = _containers.get(a["agent_id"], {})
            a["skills"] = ci.get("skills", [])
    except Exception:
        pass

    # 拓扑边
    topology_edges = []
    try:
        with open(_Path(_base) / "network_topology.json", "r", encoding="utf-8") as f:
            _topo = _json.load(f)
        for sn in _topo.get("sub_networks", []):
            for e in sn.get("edges", []):
                topology_edges.append({
                    "source": e["source"], "target": e["target"],
                    "paradigm": e["paradigm"],
                    "sub_id": sn["sub_id"],
                    "channel_id": e.get("channel_id", ""),
                })
    except Exception:
        pass

    # 业务拓扑
    biz_links = []
    try:
        with open(_Path(_base) / "business_topology.json", "r", encoding="utf-8") as f:
            _biz = _json.load(f)
        biz_links = _biz.get("links", [])
    except Exception:
        pass

    # 运行时流量统计
    ew = [t for t in traffic_log if t["type"] == "EAST_WEST"]
    ns = [t for t in traffic_log if t["type"] == "NORTH_SOUTH"]
    it = [t for t in traffic_log if t["type"] == "INTERNAL"]
    traffic_summary = {
        "total_events": len(traffic_log),
        "EAST_WEST":   {"count": len(ew), "total_kb": sum(t["bytes"] for t in ew) // 1024},
        "NORTH_SOUTH": {"count": len(ns), "total_kb": sum(t["bytes"] for t in ns) // 1024},
        "INTERNAL":    {"count": len(it), "total_kb": sum(t["bytes"] for t in it) // 1024},
        "recent": traffic_log[-10:] if traffic_log else [],
    }

    # 运行时事件
    recent_events = event_log[-20:] if event_log else []

    # CI/CD 流水线
    ci_status = {
        "total_pipelines": len(ci_pipelines),
        "running": len([p for p in ci_pipelines if p.get("status") == "running"]),
        "success": len([p for p in ci_pipelines if p.get("status") == "success"]),
        "failed": len([p for p in ci_pipelines if p.get("status") == "failed"]),
        "recent": ci_pipelines[-10:] if ci_pipelines else [],
    }

    # 文档/提交/测试统计
    task_stats = {
        "git_commits": len(git_commits),
        "model_submissions": len(model_submissions),
        "design_submissions": len(design_submissions),
        "documents": len(documents),
        "reviews": len(reviews),
        "test_reports": len(test_reports),
        "external_api_calls": len(external_api_calls),
        "test_pass_rate": round(
            sum(1 for t in test_reports if t.get("passed")) / max(len(test_reports), 1) * 100, 1
        ) if test_reports else 0,
    }

    return {
        "status": "success",
        "result": "dashboard_snapshot",
        "data": {
            "round": current_round,
            "agents": agents,
            "topology_edges": topology_edges,
            "biz_links": biz_links,
            "traffic": traffic_summary,
            "events": recent_events,
            "ci_status": ci_status,
            "task_stats": task_stats,
            "traffic_log": traffic_log,
            "event_log": event_log,
        }
    }
ToolRegistry.register("query_dashboard_tool", query_dashboard_tool)


def query_traffic_stream_tool(**kwargs):
    """实时流量流 — 返回最近的流量事件"""
    limit = kwargs.get("limit", 20)
    return {
        "status": "success",
        "data": {
            "traffic_log": traffic_log[-limit:] if traffic_log else [],
            "event_log": event_log[-limit:] if event_log else [],
        }
    }
ToolRegistry.register("query_traffic_stream_tool", query_traffic_stream_tool)


def query_task_progress_tool(**kwargs):
    """任务进度 — 各类提交/文档/测试的完成情况"""
    return {
        "status": "success",
        "data": {
            "git_commits": [{"id": c["commit_id"], "developer": c["developer"], "repo": c.get("repo",""), "files": c.get("files",0)} for c in git_commits[-20:]],
            "model_submissions": [{"id": m["model_id"], "name": m.get("model_name",""), "size_mb": m.get("size_mb",0)} for m in model_submissions[-10:]],
            "design_submissions": [{"id": d["design_id"], "name": d.get("design_name",""), "size_mb": d.get("size_mb",0)} for d in design_submissions[-10:]],
            "documents": [{"id": d["doc_id"], "author": d["author"], "title": d.get("title",""), "type": d.get("type","")} for d in documents[-20:]],
            "test_reports": [{"id": t["test_id"], "tester": t["tester"], "target": t.get("target",""), "passed": t.get("passed",False)} for t in test_reports[-20:]],
            "ci_pipelines": [{"id": p["pipeline_id"], "type": p.get("type",""), "status": p.get("status","")} for p in ci_pipelines[-10:]],
            "external_api_calls": [{"id": c["call_id"], "api": c.get("api_name",""), "latency_ms": c.get("latency_ms",0)} for c in external_api_calls[-20:]],
        }
    }
ToolRegistry.register("query_task_progress_tool", query_task_progress_tool)


# ============================================================
# panel.html 数据接口 — GET /api/scenes/{name}/state 调用
# ============================================================
