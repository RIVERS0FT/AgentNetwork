"""
Unified Agent management for the AgentNetwork control plane.

This module owns:
- Agent metadata and control-plane messages
- Agent registration, discovery, status, and statistics
- Agent container assignment and scheduling
- Agent round execution and lifecycle coordination

Single-Agent reasoning, ReAct, memory, and tool execution remain delegated to
the selected backend adapter inside services/agent_server.py.
"""

import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

import requests


SYSTEM_TOOLS = ("send_message", "broadcast")


@dataclass
class Message:
    """Agent-to-Agent control-plane message."""

    source: str
    target: str
    type: str = "task"
    payload: Dict[str, Any] = None
    message_id: str = ""

    def __post_init__(self):
        if not self.message_id:
            self.message_id = str(uuid.uuid4())
        if self.payload is None:
            self.payload = {}


class Agent:
    """Control-plane Agent metadata and lifecycle state."""

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
        self.agent_id = agent_id or f"agent-{uuid.uuid4()}"
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

    def set_comm(self, comm, registry=None):
        self.comm = comm
        self._registry = registry

    def send_task(self, task: str, target: "Agent" = None, **kwargs) -> Message:
        target_id = target.agent_id if target else self.agent_id
        message = Message(
            source=self.agent_id,
            target=target_id,
            type="task",
            payload={"action": task, **kwargs},
        )
        if self.comm and target and target_id != self.agent_id:
            self.comm.send(self.agent_id, self.name, target_id, task)
        else:
            self.task_queue.append(message)
        return message

    def send_response(self, target: "Agent", result: Any, **kwargs) -> Message:
        message = Message(
            source=self.agent_id,
            target=target.agent_id,
            type="response",
            payload={"result": result, **kwargs},
        )
        if self.comm and target:
            self.comm.send(self.agent_id, self.name, target.agent_id, str(result))
        return message

    def receive_task(self, message: Message):
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
            "url": self.container_url,
            "container_id": self.container_id,
            "status": self.status,
            "skill_refs": self.skill_refs,
            "capability_scores": self.capability_scores,
            "pending_tasks": len(self.task_queue),
            "pending_task_descs": self.pending_task_descs,
            "completed_tasks": len(self.completed_tasks),
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
    """Thread-safe control-plane Agent registry."""

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
            snapshot = list(cls._agents.values())
        results = []
        for agent in snapshot:
            if role and agent.role != role:
                continue
            if skill_ref and skill_ref not in agent.skill_refs:
                continue
            results.append(agent)
        return results

    @classmethod
    def find_best_agent(cls, skill_ref: str) -> Optional[Agent]:
        with cls._lock:
            snapshot = list(cls._agents.values())
        candidates = [agent for agent in snapshot if skill_ref in agent.skill_refs]
        if not candidates:
            candidates = [
                agent for agent in snapshot
                if skill_ref in agent.capability_scores
            ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda agent: agent.capability_scores.get(skill_ref, 0),
        )

    @classmethod
    def list_all(cls) -> List[Agent]:
        with cls._lock:
            return list(cls._agents.values())

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        agents = cls.list_all()
        roles: Dict[str, int] = {}
        statuses: Dict[str, int] = {}
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


@dataclass
class ContainerAgent:
    """Runtime assignment for one logical Agent."""

    agent_id: str
    name: str
    role: str
    core_goal: str = ""
    backend: str = "openclaw"
    skill_refs: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    scene_key: str = ""
    container_id: str = ""
    container_name: str = ""
    container_ip: str = ""
    image_id: str = ""
    port: int = 8000
    status: str = "idle"
    url: str = ""
    assign_error: str = ""

    def to_dict(self):
        return self.__dict__


class ContainerRuntime:
    """Container scheduling and round execution for direct network mode."""

    BACKEND_CONFIG = {
        "claude-code": {
            "image": "agentnetwork-ag-c1",
            "cmd": "python3 services/agent_server.py",
            "prefix": "ag-c",
        },
        "openclaw": {
            "image": "agentnetwork-ag-o1",
            "cmd": "/app/start-openclaw-agent.sh",
            "prefix": "ag-o",
        },
    }
    DEFAULT_BACKEND = "openclaw"
    NETWORK_NAME = "an"
    INTERNAL_PORT = 8000

    def __init__(self, message_bus_url: str = ""):
        self.message_bus_url = ""
        self.agents: Dict[str, ContainerAgent] = {}
        self._docker_client = None
        self._used_containers: Set[str] = set()
        self._status_listener: Optional[
            Callable[[ContainerAgent, str, Optional[Dict[str, Any]]], None]
        ] = None
        self._init_docker()

    def set_status_listener(self, callback):
        self._status_listener = callback

    def _sync_control_plane(self, ca: ContainerAgent, status: str):
        agent = AgentRegistry.get(ca.agent_id)
        if not agent:
            return
        agent.status = status
        if ca.container_id:
            agent.container_id = ca.container_id
        if ca.url:
            agent.container_url = ca.url

    def _set_status(
        self,
        ca: ContainerAgent,
        status: str,
        detail: Dict[str, Any] = None,
    ):
        ca.status = status
        self._sync_control_plane(ca, status)
        if self._status_listener:
            try:
                self._status_listener(ca, status, detail)
            except Exception:
                pass
        callback = getattr(self, "on_status_change", None)
        if callback:
            try:
                callback(ca.agent_id, status)
            except Exception:
                pass

    def _normalize_backend(self, backend: str) -> str:
        backend = (backend or self.DEFAULT_BACKEND).strip()
        if backend == "brain":
            raise RuntimeError("Backend 'brain' has been removed.")
        if backend not in self.BACKEND_CONFIG:
            raise RuntimeError(f"Unsupported backend '{backend}'.")
        return backend

    def _init_docker(self):
        try:
            import docker
            self._docker_client = docker.from_env()
            self._docker_client.ping()
            print("[Runtime] Docker OK")
        except Exception:
            self._docker_client = None
            print("[Runtime] No Docker SDK, container discovery only")

    def _get_running_containers(self, backend: str) -> List[str]:
        backend = self._normalize_backend(backend)
        prefix = self.BACKEND_CONFIG[backend]["prefix"]
        if not self._docker_client:
            return []
        try:
            return sorted(
                container.name
                for container in self._docker_client.containers.list()
                if container.name.startswith(prefix)
            )
        except Exception:
            return []

    def _dynamic_volumes(self) -> Dict[str, Dict[str, str]]:
        root = (
            os.environ.get("AGENT_HOST_PROJECT_ROOT")
            or os.environ.get("HOST_PROJECT_ROOT")
            or os.getcwd()
        )
        destinations = {
            "/app/services/agent_server.py": (
                os.path.join(root, "services", "agent_server.py"),
                "rw",
            ),
            "/app/agent_network": (
                os.path.join(root, "agent_network"),
                "rw",
            ),
            "/app/scenes": (
                os.path.join(root, "scenes"),
                "ro",
            ),
            "/app/data/pcap": (
                os.environ.get("AGENT_PCAP_HOST_PATH")
                or os.path.join(root, "data", "pcap"),
                "rw",
            ),
        }

        mounted_sources = {}
        if self._docker_client and os.path.exists("/.dockerenv"):
            try:
                current = self._docker_client.containers.get(socket.gethostname())
                mounted_sources = {
                    mount.get("Destination"): mount.get("Source")
                    for mount in current.attrs.get("Mounts", [])
                    if mount.get("Destination") and mount.get("Source")
                }
            except Exception:
                mounted_sources = {}

        volumes = {}
        for destination, (fallback, mode) in destinations.items():
            source = mounted_sources.get(destination) or fallback
            volumes[source] = {"bind": destination, "mode": mode}
        return volumes

    def _get_or_create_container(self, backend: str) -> str:
        backend = self._normalize_backend(backend)
        config = self.BACKEND_CONFIG[backend]
        for name in self._get_running_containers(backend):
            if name not in self._used_containers:
                self._used_containers.add(name)
                return name

        if not self._docker_client:
            raise RuntimeError(
                f"No {backend} containers available and Docker SDK unavailable."
            )

        auto_number = (
            len([
                name for name in self._used_containers
                if name.startswith(config["prefix"])
            ])
            + 10
        )
        auto_name = f"{config['prefix']}{auto_number}"
        try:
            try:
                old = self._docker_client.containers.get(auto_name)
                old.remove(force=True)
            except Exception:
                pass

            env = {
                "TZ": os.environ.get("TZ", "Asia/Shanghai"),
                "AGENT_ID": auto_name,
                "AGENT_NAME": auto_name,
                "AGENT_ROLE": backend,
                "AGENT_BACKEND": backend,
                "PORT": str(self.INTERNAL_PORT),
                "SERVER_URL": os.environ.get("SERVER_URL", "http://srv:8000"),
                "AGENT_COMM_MODE": "direct",
                "LOG_FULL_PCAP": os.environ.get("LOG_FULL_PCAP", "1"),
                "AGENT_CAPTURE_INCLUDE_CONTROL_PLANE": os.environ.get(
                    "AGENT_CAPTURE_INCLUDE_CONTROL_PLANE", "0"
                ),
                "AGENT_NETWORK_EMULATION": os.environ.get(
                    "AGENT_NETWORK_EMULATION", "1"
                ),
                "PCAP_DIR": os.environ.get("PCAP_DIR", "/app/data/pcap"),
                "PCAP_MAX_BYTES": os.environ.get(
                    "PCAP_MAX_BYTES", str(1024 * 1024 * 1024)
                ),
                "PCAP_SHA256": os.environ.get("PCAP_SHA256", "1"),
                "MOCK_LLM": os.environ.get("MOCK_LLM", "0"),
            }
            for key in (
                "LLM_API_KEY", "LLM_MODEL", "LLM_API_BASE", "LLM_PROVIDER",
                "LLM_MAX_TOKENS", "LLM_TEMPERATURE", "LLM_TIMEOUT_SECONDS",
                "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_BASE",
                "OPENAI_API_KEY", "AGENT_STRICT_BACKEND_SDK",
                "OPENCLAW_START_GATEWAY", "OPENCLAW_GATEWAY_HOST",
                "OPENCLAW_GATEWAY_PORT", "OPENCLAW_GATEWAY_WS_URL",
                "OPENCLAW_GATEWAY_CMD", "OPENCLAW_GATEWAY_READY_TIMEOUT",
                "OPENCLAW_API_KEY", "OPENCLAW_OPENAI_BASE_URL",
                "OPENCLAW_DEFAULT_AGENT_ID", "OPENCLAW_SESSION_NAME",
            ):
                if os.environ.get(key):
                    env[key] = os.environ[key]

            container = self._docker_client.containers.run(
                config["image"],
                name=auto_name,
                detach=True,
                command=config["cmd"],
                environment=env,
                network=self.NETWORK_NAME,
                cap_add=["NET_RAW", "NET_ADMIN"],
                volumes=self._dynamic_volumes(),
            )
            self._used_containers.add(auto_name)
            print(
                f"[Runtime] Created {auto_name} ({backend}) "
                f"container={container.id[:12]}"
            )
            return auto_name
        except Exception as exc:
            raise RuntimeError(
                f"Pool exhausted for backend '{backend}': {exc}"
            ) from exc

    def assign_agent(
        self,
        agent_id: str,
        role: str,
        name: str,
        core_goal: str = "",
        backend: str = "openclaw",
        skill_refs: List[str] = None,
        allowed_tools: List[str] = None,
        scene_key: str = "",
    ) -> ContainerAgent:
        try:
            backend = self._normalize_backend(backend)
            container_name = self._get_or_create_container(backend)
            url = f"http://{container_name}:{self.INTERNAL_PORT}"
            status = "idle"
            assign_error = ""
        except RuntimeError as exc:
            container_name = ""
            url = ""
            status = "error"
            assign_error = str(exc)

        container_id = ""
        container_ip = ""
        image_id = ""
        if container_name and self._docker_client:
            try:
                container = self._docker_client.containers.get(container_name)
                container_id = container.id
                image_id = getattr(getattr(container, "image", None), "id", "") or ""
                networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
                network = networks.get(self.NETWORK_NAME) or next(iter(networks.values()), {})
                container_ip = network.get("IPAddress", "")
            except Exception:
                pass

        assignment = ContainerAgent(
            agent_id=agent_id,
            name=name,
            role=role,
            core_goal=core_goal,
            backend=backend,
            skill_refs=list(skill_refs or []),
            allowed_tools=list(allowed_tools or []),
            scene_key=scene_key,
            container_id=container_id,
            container_name=container_name,
            container_ip=container_ip,
            image_id=image_id,
            port=self.INTERNAL_PORT,
            url=url,
            status=status,
            assign_error=assign_error,
        )
        self.agents[agent_id] = assignment
        self._set_status(assignment, status, {"phase": "assign"})
        return assignment

    def run_round(self, context: Dict = None) -> Dict:
        return {"results": self.run_all(context)}

    def list_containers(self) -> List[Dict[str, Any]]:
        return [agent.to_dict() for agent in self.agents.values()]

    def run_all(self, context: Dict = None) -> List[Dict]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = []
        assignments = list(self.agents.values())
        if not assignments:
            return results

        def run_agent(assignment: ContainerAgent):
            if not assignment.url or assignment.status == "error":
                self._set_status(
                    assignment,
                    "error",
                    {"phase": "run", "error": "agent_url_unavailable"},
                )
                return {
                    "agent_id": assignment.agent_id,
                    "error": "agent_url_unavailable",
                }

            self._set_status(assignment, "acting", {"phase": "run:start"})
            try:
                request_context = dict(context or {})
                request_context["agent_id"] = assignment.agent_id
                request_context["agent_name"] = assignment.name
                request_context["role"] = assignment.role
                request_context["core_goal"] = assignment.core_goal
                request_context["skill_refs"] = list(assignment.skill_refs)
                request_context["allowed_tools"] = list(
                    dict.fromkeys([*SYSTEM_TOOLS, *assignment.allowed_tools])
                )
                request_context["scene_key"] = assignment.scene_key

                tasks = (request_context.get("tasks") or {}).get(
                    assignment.agent_id, []
                )
                if tasks:
                    request_context["task"] = "\n".join(
                        task for task in tasks if task
                    )

                if not request_context.get("task") and not request_context.get("messages"):
                    try:
                        status_response = requests.get(
                            f"{assignment.url}/status", timeout=3
                        )
                        status_response.raise_for_status()
                        inbox_size = status_response.json().get("inbox_size", 0)
                    except Exception as exc:
                        return {
                            "agent_id": assignment.agent_id,
                            "error": f"inbox_status_unavailable: {exc}",
                        }
                    if inbox_size <= 0:
                        self._set_status(
                            assignment,
                            "idle",
                            {"phase": "run:skip", "reason": "no_task_or_message"},
                        )
                        return {
                            "agent_id": assignment.agent_id,
                            "status": "skipped",
                            "reason": "no_task_or_message",
                            "outbound_messages": [],
                            "tool_events": [],
                            "state_changes": [],
                        }

                response = requests.post(
                    f"{assignment.url}/run",
                    json=request_context,
                    timeout=300,
                )
                response.raise_for_status()
                result = response.json()
                if result.get("status") == "error" or result.get("error"):
                    error = result.get("error") or "agent adapter returned status=error"
                    self._set_status(
                        assignment,
                        "error",
                        {"phase": "run:adapter", "error": error},
                    )
                    result.setdefault("agent_id", assignment.agent_id)
                    result["error"] = error
                    return result

                self._set_status(assignment, "idle", {"phase": "run:done"})
                return result
            except Exception as exc:
                self._set_status(
                    assignment,
                    "error",
                    {"phase": "run:exception", "error": str(exc)},
                )
                return {"agent_id": assignment.agent_id, "error": str(exc)}

        with ThreadPoolExecutor(max_workers=min(10, len(assignments))) as pool:
            futures = {
                pool.submit(run_agent, assignment): assignment
                for assignment in assignments
            }
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def stop_agent(self, agent_id: str) -> bool:
        assignment = self.agents.pop(agent_id, None)
        if not assignment:
            return False
        self._set_status(assignment, "error", {"phase": "stop"})
        self._used_containers.discard(assignment.container_name)
        return True

    def stop_all(self):
        for assignment in list(self.agents.values()):
            if assignment.status != "error":
                self._set_status(assignment, "error", {"phase": "stop"})
        self.agents.clear()

    def reset(self):
        self.stop_all()
        self._used_containers.clear()


class AgentManagement:
    """Facade that coordinates metadata, registry, containers, and execution."""

    def __init__(self, runtime: ContainerRuntime = None):
        self.runtime = runtime or get_runtime()

    def create_agent(
        self,
        agent_id: str = None,
        role: str = "generic",
        name: str = "",
        core_goal: str = "",
        backend: str = "openclaw",
        skill_refs: List[str] = None,
        allowed_tools: List[str] = None,
        capability_scores: Dict[str, float] = None,
        comm=None,
    ) -> Agent:
        agent = Agent(
            agent_id=agent_id,
            role=role,
            name=name,
            core_goal=core_goal,
            backend=backend,
            skill_refs=skill_refs,
            allowed_tools=allowed_tools,
            capability_scores=capability_scores,
        )
        if comm is not None:
            agent.set_comm(comm)
        AgentRegistry.register(agent)
        agent.start()
        return agent

    def assign_agent(
        self,
        agent_id: str,
        role: str = "generic",
        name: str = "",
        core_goal: str = "",
        backend: str = "openclaw",
        skill_refs: List[str] = None,
        allowed_tools: List[str] = None,
        scene_key: str = "",
    ) -> ContainerAgent:
        agent = AgentRegistry.get(agent_id)
        if not agent:
            agent = self.create_agent(
                agent_id=agent_id,
                role=role,
                name=name or agent_id,
                core_goal=core_goal,
                backend=backend,
                skill_refs=skill_refs,
                allowed_tools=allowed_tools,
            )
        assignment = self.runtime.assign_agent(
            agent_id=agent.agent_id,
            role=agent.role,
            name=agent.name,
            core_goal=agent.core_goal,
            backend=agent.backend,
            skill_refs=agent.skill_refs,
            allowed_tools=agent.allowed_tools,
            scene_key=scene_key,
        )
        agent.container_id = assignment.container_id or agent.container_id
        agent.container_url = assignment.url
        agent.status = assignment.status
        return assignment

    def send_task(
        self,
        agent_id: str,
        task: str,
        target_id: str = None,
        **kwargs,
    ) -> Message:
        agent = AgentRegistry.get(agent_id)
        if not agent:
            raise KeyError(f"Agent '{agent_id}' not found")
        target = AgentRegistry.get(target_id) if target_id else None
        return agent.send_task(task, target=target, **kwargs)

    def run_round(self, context: Dict = None) -> Dict:
        return self.runtime.run_round(context)

    def unregister_agent(self, agent_id: str) -> bool:
        agent = AgentRegistry.get(agent_id)
        runtime_removed = self.runtime.stop_agent(agent_id)
        if agent:
            agent.stop()
            AgentRegistry.unregister(agent_id)
        return bool(agent or runtime_removed)

    def list_agents(self) -> List[Dict[str, Any]]:
        return [agent.get_status() for agent in AgentRegistry.list_all()]

    def get_stats(self) -> Dict[str, Any]:
        stats = AgentRegistry.get_stats()
        stats["runtime_assignments"] = len(self.runtime.agents)
        return stats

    def reset(self):
        self.runtime.reset()
        AgentRegistry.reset()


_runtime: Optional[ContainerRuntime] = None
_management: Optional[AgentManagement] = None


def get_runtime() -> ContainerRuntime:
    global _runtime
    if _runtime is None:
        _runtime = ContainerRuntime()
    return _runtime


def get_management() -> AgentManagement:
    global _management
    if _management is None:
        _management = AgentManagement(get_runtime())
    return _management
