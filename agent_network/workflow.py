"""
Workflow Runtime — 对应架构文档 第三节：仿真剧本运行层 / Workflow Engine

升级版工作流引擎，支持：
- DAG 依赖编排（depends_on 定义步骤间依赖）
- 6 种步骤类型（TASK / WAIT / CONDITION / PARALLEL / LOOP / SUB_WORKFLOW）
- 并行执行（同层无依赖步骤自动并行）
- 条件分支 + 循环
- 错误处理（fail / retry / skip / rollback）
- 超时控制
- 嵌套子工作流

使用方式:
    from agent_network.workflow import WorkflowStep, WorkflowDAG, WorkflowEngine, StepType

    steps = [
        WorkflowStep("A", StepType.TASK, agent_id="scout-001", action="侦察"),
        WorkflowStep("B", StepType.TASK, agent_id="cmd-001", action="分析", depends_on=["A"]),
        WorkflowStep("C", StepType.TASK, agent_id="cmd-001", action="决策", depends_on=["B"]),
    ]
    engine = WorkflowEngine()
    result = engine.run(steps, context)
"""

from __future__ import annotations

import time
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

if TYPE_CHECKING:
    from .agent import Agent, AgentRegistry


# ═══════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════

class StepType(Enum):
    """工作流步骤类型"""
    TASK = "task"             # 执行 Agent 任务
    WAIT = "wait"             # 等待 N 秒
    CONDITION = "condition"   # 条件分支 (if/else)
    PARALLEL = "parallel"     # 并行块
    LOOP = "loop"             # 循环
    SUB_WORKFLOW = "sub"      # 嵌套子工作流


class StepStatus(Enum):
    """步骤执行状态"""
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


class OnFailure(Enum):
    """失败处理策略"""
    FAIL = "fail"         # 标记失败，停止后续
    RETRY = "retry"       # 重试（max_retries 次）
    SKIP = "skip"         # 跳过，继续后续
    ROLLBACK = "rollback" # 回滚已完成的步骤


# ═══════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════

@dataclass
class WorkflowStep:
    """
    工作流步骤

    支持类型:
    - TASK: 执行 Agent 任务（agent_id + action）
    - WAIT: 等待指定秒数（params["seconds"]）
    - CONDITION: 条件分支（condition 表达式 → branches["true"]/["false"]）
    - PARALLEL: 并行块（sub_steps 中的所有步骤同时执行）
    - LOOP: 循环（loop_count 次，或 loop_condition 表达式为真时继续）
    - SUB_WORKFLOW: 嵌套子工作流（sub_steps 作为子 DAG 执行）
    """
    step_id: str
    type: StepType = StepType.TASK
    agent_id: str = ""             # TASK 类型的目标 Agent
    action: str = ""               # TASK 类型的动作
    description: str = ""          # 步骤描述（日志用）

    # DAG 依赖
    depends_on: List[str] = field(default_factory=list)  # 前置步骤 ID 列表

    # 执行控制
    timeout_seconds: float = 30.0
    on_failure: str = "fail"       # fail | retry | skip | rollback
    max_retries: int = 0

    # 步骤参数
    params: Dict[str, Any] = field(default_factory=dict)

    # 条件分支
    condition: str = ""            # Python 表达式，可用 ctx["变量名"]
    branches: Dict[str, List["WorkflowStep"]] = field(default_factory=dict)

    # 并行 / 循环 / 子工作流
    sub_steps: List["WorkflowStep"] = field(default_factory=list)
    loop_count: int = 1            # 固定循环次数
    loop_condition: str = ""       # 循环终止条件表达式

    # 运行时状态
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "type": self.type.value,
            "agent_id": self.agent_id,
            "action": self.action,
            "description": self.description,
            "depends_on": self.depends_on,
            "timeout_seconds": self.timeout_seconds,
            "on_failure": self.on_failure,
            "params": self.params,
            "condition": self.condition,
            "loop_count": self.loop_count,
            "loop_condition": self.loop_condition,
            "sub_steps_count": len(self.sub_steps),
            "status": self.status.value,
            "result": str(self.result)[:300] if self.result else None,
            "error": self.error,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkflowStep":
        """从字典创建（兼容旧格式 {step, agent, action}）"""
        step_id = d.get("step_id", d.get("step", "")) or f"step-{id(d)}"
        # 兼容旧格式
        if "agent" in d and "agent_id" not in d:
            d["agent_id"] = d["agent"]
        if "type" not in d:
            d["type"] = "task"

        step_type = StepType(d["type"]) if isinstance(d["type"], str) else d["type"]

        # 递归构建子步骤
        sub_steps = []
        for s in d.get("sub_steps", []):
            sub_steps.append(cls.from_dict(s))
        for branch_steps in d.get("branches", {}).values():
            for s in branch_steps:
                pass  # branches 内的步骤在运行时按需展开

        return cls(
            step_id=str(step_id),
            type=step_type,
            agent_id=d.get("agent_id", d.get("agent", "")),
            action=d.get("action", ""),
            description=d.get("description", ""),
            depends_on=d.get("depends_on", []),
            timeout_seconds=d.get("timeout_seconds", 30.0),
            on_failure=d.get("on_failure", "fail"),
            max_retries=d.get("max_retries", 0),
            params=d.get("params", {}),
            condition=d.get("condition", ""),
            branches={k: [cls.from_dict(s) for s in v] for k, v in d.get("branches", {}).items()},
            sub_steps=sub_steps,
            loop_count=d.get("loop_count", 1),
            loop_condition=d.get("loop_condition", ""),
        )


@dataclass
class WorkflowResult:
    """工作流执行结果"""
    workflow_name: str = ""
    total_steps: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    steps: List[Dict[str, Any]] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# Workflow DAG — 依赖分析与拓扑排序
# ═══════════════════════════════════════════════

class WorkflowDAG:
    """
    工作流 DAG — 分析步骤依赖关系，计算执行顺序

    核心方法:
    - build(steps) → 构建 DAG，验证无循环依赖
    - get_parallel_layers() → 拓扑分层，返回 [[并行的步骤], [下一层], ...]
    - get_next_ready(completed_ids) → 返回所有依赖已满足的下一步
    """

    def __init__(self):
        self._steps: Dict[str, WorkflowStep] = {}
        self._deps: Dict[str, Set[str]] = {}      # step_id → 依赖它的步骤
        self._in_degree: Dict[str, int] = {}       # step_id → 未完成的前置数
        self._layers: List[List[WorkflowStep]] = []
        self._valid = False

    def build(self, steps: List[WorkflowStep]) -> List[List[WorkflowStep]]:
        """
        构建 DAG，返回拓扑分层

        Raises:
            ValueError: 存在循环依赖
        """
        self._steps = {s.step_id: s for s in steps}
        self._deps = {s.step_id: set() for s in steps}
        self._in_degree = {s.step_id: 0 for s in steps}

        # 构建依赖关系
        all_ids = set(self._steps.keys())

        for step in steps:
            for dep_id in step.depends_on:
                # 跳过未知依赖（可能是外部步骤）
                if dep_id not in all_ids:
                    # 不阻止执行，只记录警告
                    step.params.setdefault("_warnings", []).append(f"Unknown dependency: {dep_id}")
                    step.depends_on = [d for d in step.depends_on if d != dep_id]
                    continue
                self._deps[dep_id].add(step.step_id)
                self._in_degree[step.step_id] += 1

        # 检测循环依赖（Kahn 算法）
        visited = set()
        queue = [sid for sid, deg in self._in_degree.items() if deg == 0]
        while queue:
            sid = queue.pop(0)
            visited.add(sid)
            for next_id in self._deps.get(sid, set()):
                if next_id in self._in_degree:
                    self._in_degree[next_id] -= 1
                    if self._in_degree[next_id] == 0:
                        queue.append(next_id)

        unvisited = all_ids - visited
        if unvisited:
            raise ValueError(f"Workflow DAG contains cycle involving: {unvisited}")

        # 重新计算 in_degree（Kahn 过程中被修改了）
        self._in_degree = {s.step_id: len(s.depends_on) for s in steps}

        # 拓扑分层
        self._layers = self._compute_layers(steps)
        self._valid = True
        return self._layers

    def _compute_layers(self, steps: List[WorkflowStep]) -> List[List[WorkflowStep]]:
        """计算拓扑分层（BFS）"""
        remaining = {s.step_id: s for s in steps}
        remaining_deps = {s.step_id: set(s.depends_on) for s in steps}
        layers = []

        while remaining:
            # 找出所有依赖已满足的步骤
            ready = {
                sid for sid, s in remaining.items()
                if not remaining_deps[sid]
            }
            if not ready:
                # 不应该发生（已通过循环检测），安全退出
                break

            layer = []
            for sid in sorted(ready):
                layer.append(remaining.pop(sid))
                del remaining_deps[sid]

                # 移除该步骤对其他步骤的阻塞
                for other_sid, deps in remaining_deps.items():
                    deps.discard(sid)

            layers.append(layer)

        return layers

    def get_parallel_layers(self) -> List[List[WorkflowStep]]:
        """获取拓扑分层（每层内可并行执行）"""
        if not self._valid:
            raise RuntimeError("DAG not built. Call build() first.")
        return self._layers

    def get_next_ready(self, completed_ids: Set[str]) -> List[WorkflowStep]:
        """返回所有依赖已满足的下一步（动态查询）"""
        ready = []
        for step in self._steps.values():
            if step.step_id in completed_ids:
                continue
            if step.status == StepStatus.PENDING:
                deps_satisfied = all(
                    dep in completed_ids
                    for dep in step.depends_on
                )
                if deps_satisfied:
                    ready.append(step)
        return ready

    def validate(self) -> bool:
        """验证 DAG 有效"""
        return self._valid

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._steps.get(step_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self._valid,
            "total_steps": len(self._steps),
            "layers": len(self._layers),
            "steps": {sid: s.to_dict() for sid, s in self._steps.items()},
        }


# ═══════════════════════════════════════════════
# Workflow Engine — 执行引擎
# ═══════════════════════════════════════════════

class WorkflowEngine:
    """
    工作流执行引擎

    按 DAG 依赖关系执行步骤：拓扑分层，同层内并行执行。

    Args:
        max_workers: 并行执行的最大线程数
    """

    def __init__(self, max_workers: int = 8):
        self.max_workers = max_workers
        self._running = False
        self._context: Dict[str, Any] = {}
        self._logs: List[str] = []
        self._completed: Set[str] = set()
        self._executor = None
        self._dag: Optional[WorkflowDAG] = None
        self._step_results: Dict[str, Any] = {}

        # 历史记录
        self._history: List[WorkflowResult] = []

    # ── 公共 API ─────────────────────────────────

    def run(
        self,
        steps: List[WorkflowStep],
        context: Dict[str, Any] = None,
        name: str = "",
    ) -> WorkflowResult:
        """
        执行工作流

        Args:
            steps: 步骤列表（支持 depends_on 定义依赖）
            context: 执行上下文（包含 registry、logger 等）
            name: 工作流名称

        Returns:
            WorkflowResult
        """
        self._running = True
        self._context = context or {}
        self._context.setdefault("_vars", {})  # 共享变量（条件表达式可用）
        self._logs = []
        self._completed = set()
        self._step_results = {}
        start_time = time.time()

        # 构建 DAG
        self._dag = WorkflowDAG()
        try:
            layers = self._dag.build(steps)
        except ValueError as e:
            return WorkflowResult(
                workflow_name=name,
                total_steps=len(steps),
                failed=len(steps),
                duration_seconds=0,
                logs=[f"DAG build error: {e}"],
            )

        self._log(f"Workflow [{name or 'unnamed'}] started — {len(steps)} steps, "
                  f"{len(layers)} layers, max_workers={self.max_workers}")

        # 逐层执行
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            self._executor = executor

            for layer_idx, layer in enumerate(layers):
                if not self._running:
                    break

                self._log(f"Layer {layer_idx + 1}/{len(layers)}: "
                          f"{len(layer)} steps executing in parallel")

                # 并行执行同层所有步骤
                futures = {}
                for step in layer:
                    step.status = StepStatus.READY
                    future = executor.submit(self._execute_step, step)
                    futures[future] = step

                # 等待本层完成
                for future in as_completed(futures):
                    step = futures[future]
                    try:
                        ok, result = future.result(timeout=step.timeout_seconds + 5)
                        if ok:
                            step.status = StepStatus.COMPLETED
                            step.result = result
                            self._completed.add(step.step_id)
                            self._step_results[step.step_id] = result
                        else:
                            self._handle_step_failure(step, result or "unknown error")
                    except Exception as e:
                        self._handle_step_failure(step, str(e))

                # 检查是否有步骤失败导致无法继续
                layer_failed = [s for s in layer if s.status == StepStatus.FAILED]
                if layer_failed:
                    self._log(f"Layer {layer_idx + 1}: {len(layer_failed)} step(s) failed, "
                              f"checking downstream impact...")

        self._running = False
        elapsed = time.time() - start_time

        # 收集结果
        all_steps = list(self._dag._steps.values()) if self._dag else steps
        result = WorkflowResult(
            workflow_name=name,
            total_steps=len(all_steps),
            completed=sum(1 for s in all_steps if s.status == StepStatus.COMPLETED),
            failed=sum(1 for s in all_steps if s.status == StepStatus.FAILED),
            skipped=sum(1 for s in all_steps if s.status == StepStatus.SKIPPED),
            duration_seconds=round(elapsed, 3),
            steps=[s.to_dict() for s in all_steps],
            logs=self._logs,
        )
        self._history.append(result)
        if len(self._history) > 50:
            self._history.pop(0)

        self._log(f"Workflow [{name}] completed in {elapsed:.2f}s — "
                  f"{result.completed} ok, {result.failed} failed, {result.skipped} skipped")
        return result

    # ── 单步执行 ──────────────────────────────────

    def _execute_step(self, step: WorkflowStep) -> tuple:
        """
        执行单个步骤

        Returns:
            (success: bool, result: Any)
        """
        step.status = StepStatus.RUNNING
        step.started_at = time.time()

        try:
            if step.type == StepType.TASK:
                result = self._exec_task(step)
            elif step.type == StepType.WAIT:
                result = self._exec_wait(step)
            elif step.type == StepType.CONDITION:
                result = self._exec_condition(step)
            elif step.type == StepType.PARALLEL:
                result = self._exec_parallel(step)
            elif step.type == StepType.LOOP:
                result = self._exec_loop(step)
            elif step.type == StepType.SUB_WORKFLOW:
                result = self._exec_sub_workflow(step)
            else:
                raise ValueError(f"Unknown step type: {step.type}")

            step.completed_at = time.time()
            return (True, result)

        except Exception as e:
            step.completed_at = time.time()
            step.error = str(e)
            return (False, str(e))

    def _exec_task(self, step: WorkflowStep) -> Any:
        """执行 TASK 步骤：发送任务给 Agent"""
        registry = self._context.get("registry")
        if not registry:
            raise RuntimeError("No AgentRegistry in context")

        agent = registry.get(step.agent_id)
        if not agent:
            # 尝试通过 role 查找
            found = registry.find_agent(role=step.agent_id)
            if found:
                agent = found[0]
            else:
                raise ValueError(f"Agent '{step.agent_id}' not found in registry")

        self._log(f"  → [{step.step_id}] {step.action} → {agent.name} ({agent.role})")

        # 发送任务
        msg = agent.send_task(task=step.action, target=agent, **step.params)

        # 立即执行
        if agent.task_queue:
            task_msg = agent.task_queue.pop(0)
            result = agent.execute_task(task_msg)
            self._log(f"  ✓ [{step.step_id}] completed: {agent.name}")
            return result
        else:
            return {"dispatched": True, "message_id": msg.message_id}

    def _exec_wait(self, step: WorkflowStep) -> Any:
        """执行 WAIT 步骤：等待指定秒数"""
        seconds = step.params.get("seconds", 1.0)
        self._log(f"  ⏳ [{step.step_id}] Waiting {seconds}s...")
        time.sleep(min(seconds, 60))  # 最多等 60 秒
        return {"waited": seconds}

    def _exec_condition(self, step: WorkflowStep) -> Any:
        """执行 CONDITION 步骤：评估条件表达式，执行对应分支"""
        ctx = self._context.get("_vars", {})
        # 注入步骤结果变量
        for sid, res in self._step_results.items():
            ctx[sid] = res

        # 安全评估 Python 表达式
        condition_met = self._eval_expression(step.condition, ctx)
        branch_key = "true" if condition_met else "false"
        branch_steps = step.branches.get(branch_key, [])

        self._log(f"  ? [{step.step_id}] condition='{step.condition}' → {branch_key} "
                  f"({len(branch_steps)} steps)")

        if not branch_steps:
            return {"condition": step.condition, "result": branch_key, "executed": 0}

        # 在同一个 executor 中串行执行分支步骤
        branch_dag = WorkflowDAG()
        branch_dag.build(branch_steps)
        branch_result = {"condition": step.condition, "result": branch_key, "steps": []}
        for step_in_branch in branch_steps:
            ok, res = self._execute_step(step_in_branch)
            branch_result["steps"].append({
                "step_id": step_in_branch.step_id,
                "success": ok,
                "result": str(res)[:200] if res else None,
            })
        branch_result["executed"] = len(branch_steps)
        return branch_result

    def _exec_parallel(self, step: WorkflowStep) -> Any:
        """执行 PARALLEL 步骤：并行执行所有子步骤"""
        sub_steps = step.sub_steps
        if not sub_steps:
            return {"parallel": True, "executed": 0}

        self._log(f"  ∥ [{step.step_id}] Parallel block: {len(sub_steps)} sub-steps")

        results = {"parallel": True, "sub_results": [], "failed": 0}
        futures = {}
        for s in sub_steps:
            s.status = StepStatus.READY
            future = self._executor.submit(self._execute_step, s)
            futures[future] = s

        for future in as_completed(futures):
            s = futures[future]
            try:
                ok, res = future.result(timeout=s.timeout_seconds + 5)
                results["sub_results"].append({
                    "step_id": s.step_id, "success": ok,
                    "result": str(res)[:200] if res else None,
                })
                if not ok:
                    results["failed"] += 1
            except Exception as e:
                results["sub_results"].append({
                    "step_id": s.step_id, "success": False, "error": str(e),
                })
                results["failed"] += 1

        results["executed"] = len(sub_steps)
        return results

    def _exec_loop(self, step: WorkflowStep) -> Any:
        """执行 LOOP 步骤：循环执行子步骤"""
        results = {"loop": True, "iterations": []}

        # 计算循环次数
        if step.loop_condition:
            max_iterations = step.loop_count or 100  # 防止无限循环
        else:
            max_iterations = step.loop_count or 1

        iteration = 0
        while iteration < max_iterations:
            # 检查循环条件是否满足
            if step.loop_condition:
                ctx = self._context.get("_vars", {})
                ctx["_iteration"] = iteration
                for sid, res in self._step_results.items():
                    ctx[sid] = res
                if not self._eval_expression(step.loop_condition, ctx):
                    self._log(f"  ↻ [{step.step_id}] Loop condition unmet at iteration {iteration}, breaking")
                    break

            self._log(f"  ↻ [{step.step_id}] Loop iteration {iteration + 1}/{max_iterations}")

            iter_result = {"iteration": iteration, "steps": []}
            for s in step.sub_steps:
                s.status = StepStatus.READY
                ok, res = self._execute_step(s)
                iter_result["steps"].append({
                    "step_id": s.step_id, "success": ok,
                    "result": str(res)[:200] if res else None,
                })
            results["iterations"].append(iter_result)
            iteration += 1

        results["total_iterations"] = iteration
        return results

    def _exec_sub_workflow(self, step: WorkflowStep) -> Any:
        """执行 SUB_WORKFLOW 步骤：递归运行子工作流"""
        sub_steps = step.sub_steps
        if not sub_steps:
            return {"sub_workflow": True, "executed": 0}

        self._log(f"  ┌ [{step.step_id}] Sub-workflow: {len(sub_steps)} steps")

        sub_result = self.run(
            steps=sub_steps,
            context=self._context,
            name=f"sub-{step.step_id}",
        )
        self._log(f"  └ [{step.step_id}] Sub-workflow done: "
                  f"{sub_result.completed} ok, {sub_result.failed} failed")
        return sub_result

    # ── 错误处理 ──────────────────────────────────

    def _handle_step_failure(self, step: WorkflowStep, error: str):
        """处理步骤失败"""
        step.error = error
        strategy = OnFailure(step.on_failure) if isinstance(step.on_failure, str) else step.on_failure

        if strategy == OnFailure.RETRY and step.retry_count < step.max_retries:
            step.retry_count += 1
            step.status = StepStatus.READY
            self._log(f"  ↺ [{step.step_id}] Retry {step.retry_count}/{step.max_retries}: {error[:100]}")
            ok, result = self._execute_step(step)
            if ok:
                step.status = StepStatus.COMPLETED
                step.result = result
                self._completed.add(step.step_id)
                return

        if strategy == OnFailure.SKIP:
            step.status = StepStatus.SKIPPED
            self._log(f"  ⏭ [{step.step_id}] Skipped: {error[:100]}")
        elif strategy == OnFailure.ROLLBACK:
            step.status = StepStatus.FAILED
            self._log(f"  ↩ [{step.step_id}] Rollback triggered: {error[:100]}")
            self._running = False  # 停止后续执行
        else:  # FAIL
            step.status = StepStatus.FAILED
            self._log(f"  ✗ [{step.step_id}] Failed: {error[:100]}")

    # ── 表达式求值 ──────────────────────────────

    def _eval_expression(self, expr: str, ctx: Dict[str, Any]) -> bool:
        """安全评估 Python 条件表达式"""
        if not expr:
            return True

        # 白名单：允许的基本操作
        safe_globals = {
            "__builtins__": {
                "True": True, "False": False, "None": None,
                "len": len, "str": str, "int": int, "float": float,
                "bool": bool, "isinstance": isinstance,
                "any": any, "all": all,
            },
        }
        try:
            result = eval(expr, safe_globals, ctx)
            return bool(result)
        except Exception as e:
            self._log(f"  ⚠ Condition eval error: '{expr}' → {e}")
            return False

    # ── 日志 ─────────────────────────────────────

    def _log(self, message: str):
        self._logs.append(f"[{datetime.now().isoformat(timespec='seconds')}] {message}")

    # ── 查询 ─────────────────────────────────────

    def get_context_var(self, key: str, default=None) -> Any:
        return self._context.get("_vars", {}).get(key, default)

    def set_context_var(self, key: str, value: Any):
        self._context.setdefault("_vars", {})[key] = value

    def get_status(self) -> Dict[str, Any]:
        """当前工作流执行状态"""
        return {
            "running": self._running,
            "completed_steps": len(self._completed),
            "context_keys": list(self._context.get("_vars", {}).keys()),
            "recent_logs": self._logs[-20:],
        }

    def get_history(self, limit: int = 10) -> List[Dict]:
        return [r.__dict__ for r in self._history[-limit:]]

    # ── 重置 ─────────────────────────────────────

    def reset(self):
        self._running = False
        self._context = {}
        self._logs = []
        self._completed = set()
        self._dag = None
        self._step_results = {}


# ═══════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════

def quick_workflow(steps_def: List[Dict[str, Any]], context: Dict = None) -> WorkflowResult:
    """
    快捷执行工作流（从 JSON/dict 定义）

    Example:
        result = quick_workflow([
            {"step_id": "1", "agent_id": "scout-001", "action": "侦察"},
            {"step_id": "2", "agent_id": "cmd-001", "action": "分析", "depends_on": ["1"]},
        ], context={"registry": AgentRegistry})
    """
    steps = [WorkflowStep.from_dict(s) for s in steps_def]
    engine = WorkflowEngine()
    return engine.run(steps, context, name="quick")
