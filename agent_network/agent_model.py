"""
Agent 数据模型与注册中心。

本模块只维护仿真控制面需要的 Agent 元数据：
- agent_id / role / name
- skill_refs / capability_scores
- status / container_url
- position / explicit runtime metadata
- pending task descriptions

本模块不负责单 Agent 执行、ReAct、Tool 调用或 Memory。单 Agent
执行必须通过 BackendAdapter 调用 Claude Code / OpenCLAW。
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass
import threading
import uuid


@dataclass
class Message:
    """Agent 间控制面消息模型。"""
    source: str
    target: str
    type: str = "task"
    payload: Dict[str, Any] = None
    message_id: str = ""

    def __post_init__(self):
        if self.message_id == "":
            self.message_id = str(uuid.uuid4())
        if self.payload is None:
            self.payload = {}


class Agent:
    """Agent metadata object used by the AgentNetwork control plane.

    This class deliberately does not implement ReAct, local reasoning, memory, or
    tool execution. Those responsibilities belong to Claude Code / OpenCLAW via
    BackendAdapter.
    """

    def __init__(
        self,
        agent_id: str = None,
        role: str = "generic",
        name: str = "",
        core_goal: str = "",
        backend: str = "openclaw",
        skill_refs: List[str] = None,
        allowed_tools: List[str] = None,
        capability_scores: Dict[str, float] = None,
    ):
        self.agent_id = agent_id or f"agent-{str(uuid.uuid4())}"
        self.role = role
        self.name = name or self.agent_id
        self.core_goal = core_goal
        self.backend = backend
        self.skill_refs = list(skill_refs or [])
        self.allowed_tools = list(allowed_tools or [])
        self.capability_scores = capability_scores or {}
        self.status = "idle"
        self.container_id = f"docker-{self.agent_id}"
        self.container_url = ""
        self.comm = None
        self.task_queue: List[Message] = []
        self.completed_tasks: List[Dict[str, Any]] = []
        self.pending_task_descs: List[str] = []
        self._created_at = datetime.now().isoformat(timespec="seconds")

        # Frontend layout position.
        self.x: float = 0.0
        self.y: float = 0.0
        self.speed: float = 1.0
        self._target_x: Optional[float] = None
        self._target_y: Optional[float] = None

    def set_comm(self, comm, registry=None):
        """Attach the control-plane communication layer."""
        self.comm = comm
        self._registry = registry

    def send_task(self, task: str, target: "Agent" = None, **kwargs) -> Message:
        """Create or forward a control-plane task message.

        This is a communication helper only. It does not execute the task.
        """
        target_id = target.agent_id if target else self.agent_id
        msg = Message(
            source=self.agent_id,
            target=target_id,
            type="task",
            payload={"action": task, **kwargs},
        )

        if self.comm and target and target_id != self.agent_id:
            self.comm.send(self.agent_id, self.name, target_id, task)
        else:
            self.task_queue.append(msg)

        return msg

    def send_response(self, target: "Agent", result: Any, **kwargs) -> Message:
        """Create or forward a control-plane response message."""
        msg = Message(
            source=self.agent_id,
            target=target.agent_id,
            type="response",
            payload={"result": result, **kwargs},
        )
        if self.comm and target:
            self.comm.send(self.agent_id, self.name, target.agent_id, str(result))
        return msg

    def receive_task(self, message: Message):
        """Receive a control-plane message into the local queue."""
        self.task_queue.append(message)

    def execute_task(self, message: Message) -> Dict[str, Any]:
        raise RuntimeError(
            "Agent.execute_task has been removed. "
            "Single-Agent execution must go through BackendAdapter and /run."
        )

    def call_tool(self, tool_name: str, **kwargs) -> Any:
        raise RuntimeError(
            "Agent.call_tool has been removed. "
            "Tool execution must go through backend-native MCP tool calling."
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "core_goal": self.core_goal,
            "backend": self.backend,
            "allowed_tools": self.allowed_tools,
            "url": getattr(self, "container_url", ""),
            "container_id": self.container_id,
            "status": self.status,
            "skill_refs": self.skill_refs,
            "capability_scores": self.capability_scores,
            "pending_tasks": len(self.task_queue),
            "pending_task_descs": self.pending_task_descs,
            "completed_tasks": len(self.completed_tasks),
            "created_at": self._created_at,
            "x": self.x,
            "y": self.y,
        }

    def start(self):
        self.status = "idle"

    def stop(self):
        self.status = "error"

    def error(self, reason: str = ""):
        self.status = "error"

    def __repr__(self):
        return f"Agent(id={self.agent_id}, role={self.role}, status={self.status})"


class AgentRegistry:
    """Control-plane Agent registry."""

    _instance: Optional["AgentRegistry"] = None
    _agents: Dict[str, Agent] = {}
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, agent: Agent):
        with cls._lock:
            cls._agents[agent.agent_id] = agent

    @classmethod
    def unregister(cls, agent_id: str):
        with cls._lock:
            cls._agents.pop(agent_id, None)

    @classmethod
    def get(cls, agent_id: str) -> Optional[Agent]:
        with cls._lock:
            return cls._agents.get(agent_id)

    @classmethod
    def find_agent(
        cls,
        role: str = None,
        skill_ref: str = None,
    ) -> List[Agent]:
        with cls._lock:
            agents_snapshot = list(cls._agents.values())
        results = []
        for agent in agents_snapshot:
            if role and agent.role != role:
                continue
            if skill_ref and skill_ref not in agent.skill_refs:
                continue
            results.append(agent)
        return results

    @classmethod
    def find_best_agent(cls, skill_ref: str) -> Optional[Agent]:
        with cls._lock:
            agents_snapshot = list(cls._agents.values())
        candidates = [a for a in agents_snapshot if skill_ref in a.skill_refs]
        if not candidates:
            candidates = [a for a in agents_snapshot if skill_ref in a.capability_scores]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.capability_scores.get(skill_ref, 0))

    @classmethod
    def list_all(cls) -> List[Agent]:
        with cls._lock:
            return list(cls._agents.values())

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        agents = cls.list_all()
        roles = {}
        statuses = {}
        for agent in agents:
            roles[agent.role] = roles.get(agent.role, 0) + 1
            statuses[agent.status] = statuses.get(agent.status, 0) + 1
        return {
            "total_agents": len(agents),
            "by_role": roles,
            "by_status": statuses,
        }

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._agents.clear()
