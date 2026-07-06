from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class AgentContext:
    trace_id: str
    agent_id: str
    agent_name: str
    role: str
    core_goal: str
    task: str
    messages: List[Dict[str, Any]]
    skill_refs: List[str]
    allowed_tools: List[str]
    permissions: Dict[str, Any]
    state_snapshot: Dict[str, Any]
    tick: int
    timeout_seconds: int
    max_turns: int
    scene_key: str = "default"
    agent_directory: Dict[str, str] = field(default_factory=dict)
    comm_matrix: Dict[str, List[str]] = field(default_factory=dict)
    simulation_seed: int = 0


@dataclass
class AgentRunResult:
    trace_id: str
    agent_id: str
    status: str
    final_message: str
    application_events: List[Dict[str, Any]] = field(default_factory=list)
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
    state_changes: List[Dict[str, Any]] = field(default_factory=list)
    outbound_messages: List[Dict[str, Any]] = field(default_factory=list)
    traffic_events: List[Dict[str, Any]] = field(default_factory=list)
    audit_events: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


class BackendAdapter:
    def run_agent_task(self, agent_context: AgentContext) -> AgentRunResult:
        raise NotImplementedError
