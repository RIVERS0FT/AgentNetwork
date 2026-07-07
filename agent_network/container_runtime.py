"""Agent container runtime for direct network mode."""

import os
import socket
import time
import requests
from typing import Callable, Dict, List, Any, Optional, Set
from dataclasses import dataclass, field


SYSTEM_TOOLS = ("send_message", "broadcast")


@dataclass
class ContainerAgent:
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
    BACKEND_CONFIG = {
        "claude-code": {"image": "agentnetwork-ag-c1", "cmd": "python3 services/agent_server.py", "prefix": "ag-c"},
        "openclaw": {"image": "agentnetwork-ag-o1", "cmd": "/app/start-openclaw-agent.sh", "prefix": "ag-o"},
    }
    DEFAULT_BACKEND = "openclaw"
    NETWORK_NAME = "an"
    INTERNAL_PORT = 8000

    def __init__(self, message_bus_url: str = ""):
        self.message_bus_url = ""
        self.agents: Dict[str, ContainerAgent] = {}
        self._docker_client = None
        self._used_containers: Set[str] = set()
        self._status_listener: Optional[Callable[[ContainerAgent, str, Optional[Dict[str, Any]]], None]] = None
        self._init_docker()

    def set_status_listener(self, callback):
        self._status_listener = callback

    def _set_status(self, ca: ContainerAgent, status: str, detail: Dict[str, Any] = None):
        ca.status = status
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
            return sorted([c.name for c in self._docker_client.containers.list() if c.name.startswith(prefix)])
        except Exception:
            return []

    def _dynamic_volumes(self) -> Dict[str, Dict[str, str]]:
        """Return host-side sources for mounts inherited by dynamic Agents.

        Docker interprets bind sources in the daemon host namespace. When srv
        itself runs in Docker, paths such as /app/data/pcap are container paths
        and cannot safely be reused as bind sources. Resolve their actual host
        sources from srv's mount metadata instead.
        """
        root = os.environ.get("AGENT_HOST_PROJECT_ROOT") or os.environ.get("HOST_PROJECT_ROOT") or os.getcwd()
        destinations = {
            "/app/services/agent_server.py": (os.path.join(root, "services", "agent_server.py"), "rw"),
            "/app/agent_network": (os.path.join(root, "agent_network"), "rw"),
            "/app/scenes": (os.path.join(root, "scenes"), "ro"),
            "/app/data/pcap": (
                os.environ.get("AGENT_PCAP_HOST_PATH") or os.path.join(root, "data", "pcap"),
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
        cfg = self.BACKEND_CONFIG[backend]
        for name in self._get_running_containers(backend):
            if name not in self._used_containers:
                self._used_containers.add(name)
                return name
        if not self._docker_client:
            raise RuntimeError(f"No {backend} containers available and Docker SDK unavailable.")
        auto_n = len([c for c in self._used_containers if c.startswith(cfg["prefix"])]) + 10
        auto_name = f"{cfg['prefix']}{auto_n}"
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
                "AGENT_CAPTURE_INCLUDE_CONTROL_PLANE": os.environ.get("AGENT_CAPTURE_INCLUDE_CONTROL_PLANE", "0"),
                "AGENT_NETWORK_EMULATION": os.environ.get("AGENT_NETWORK_EMULATION", "1"),
                "PCAP_DIR": os.environ.get("PCAP_DIR", "/app/data/pcap"),
                "PCAP_MAX_BYTES": os.environ.get("PCAP_MAX_BYTES", str(1024 * 1024 * 1024)),
                "PCAP_SHA256": os.environ.get("PCAP_SHA256", "1"),
                "MOCK_LLM": os.environ.get("MOCK_LLM", "0"),
            }
            for key in (
                "LLM_API_KEY", "LLM_MODEL", "LLM_API_BASE", "LLM_PROVIDER",
                "LLM_MAX_TOKENS", "LLM_TEMPERATURE", "LLM_TIMEOUT_SECONDS",
                "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_BASE",
                "OPENAI_API_KEY", "AGENT_STRICT_BACKEND_SDK",
                "OPENCLAW_START_GATEWAY", "OPENCLAW_GATEWAY_HOST", "OPENCLAW_GATEWAY_PORT",
                "OPENCLAW_GATEWAY_WS_URL", "OPENCLAW_GATEWAY_CMD", "OPENCLAW_GATEWAY_READY_TIMEOUT",
                "OPENCLAW_API_KEY", "OPENCLAW_OPENAI_BASE_URL", "OPENCLAW_DEFAULT_AGENT_ID", "OPENCLAW_SESSION_NAME",
            ):
                if os.environ.get(key):
                    env[key] = os.environ[key]
            container = self._docker_client.containers.run(
                cfg["image"],
                name=auto_name,
                detach=True,
                command=cfg["cmd"],
                environment=env,
                network=self.NETWORK_NAME,
                cap_add=["NET_RAW", "NET_ADMIN"],
                volumes=self._dynamic_volumes(),
            )
            self._used_containers.add(auto_name)
            print(f"[Runtime] Created {auto_name} ({backend}) container={container.id[:12]}")
            return auto_name
        except Exception as exc:
            raise RuntimeError(f"Pool exhausted for backend '{backend}': {exc}")

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
            assign_error = None
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
        ca = ContainerAgent(
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
            assign_error=assign_error or "",
        )
        self.agents[agent_id] = ca
        self._set_status(ca, status, {"phase": "assign"})
        return ca

    def run_round(self, context: Dict = None) -> Dict:
        return {"results": self.run_all(context)}

    def list_containers(self) -> List[Dict[str, Any]]:
        return [agent.to_dict() for agent in self.agents.values()]

    def run_all(self, context: Dict = None) -> List[Dict]:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = []
        agents_list = list(self.agents.values())
        if not agents_list:
            return results

        def _run_agent(ca: ContainerAgent):
            if not ca.url or ca.status == "error":
                self._set_status(ca, "error", {"phase": "run", "error": "agent_url_unavailable"})
                return {"agent_id": ca.agent_id, "error": "agent_url_unavailable"}
            self._set_status(ca, "acting", {"phase": "run:start"})
            try:
                ctx = dict(context or {})
                ctx["agent_id"] = ca.agent_id
                ctx["agent_name"] = ca.name
                ctx["role"] = ca.role
                ctx["core_goal"] = ca.core_goal
                ctx["skill_refs"] = list(ca.skill_refs)
                ctx["allowed_tools"] = list(
                    dict.fromkeys([*SYSTEM_TOOLS, *ca.allowed_tools])
                )
                ctx["scene_key"] = ca.scene_key
                agent_tasks = (ctx.get("tasks") or {}).get(ca.agent_id, [])
                if agent_tasks:
                    ctx["task"] = "\n".join([t for t in agent_tasks if t])
                if not ctx.get("task") and not ctx.get("messages"):
                    try:
                        status_response = requests.get(f"{ca.url}/status", timeout=3)
                        status_response.raise_for_status()
                        inbox_size = status_response.json().get("inbox_size", 0)
                    except Exception as exc:
                        return {"agent_id": ca.agent_id, "error": f"inbox_status_unavailable: {exc}"}
                    if inbox_size <= 0:
                        self._set_status(ca, "idle", {"phase": "run:skip", "reason": "no_task_or_message"})
                        return {"agent_id": ca.agent_id, "status": "skipped", "reason": "no_task_or_message", "outbound_messages": [], "tool_events": [], "state_changes": []}
                response = requests.post(f"{ca.url}/run", json=ctx, timeout=300)
                response.raise_for_status()
                result = response.json()
                if result.get("status") == "error" or result.get("error"):
                    error = result.get("error") or "agent adapter returned status=error"
                    self._set_status(ca, "error", {"phase": "run:adapter", "error": error})
                    result.setdefault("agent_id", ca.agent_id)
                    result["error"] = error
                    return result
                self._set_status(ca, "idle", {"phase": "run:done"})
                return result
            except Exception as exc:
                self._set_status(ca, "error", {"phase": "run:exception", "error": str(exc)})
                return {"agent_id": ca.agent_id, "error": str(exc)}

        with ThreadPoolExecutor(max_workers=min(10, len(agents_list))) as pool:
            futures = {pool.submit(_run_agent, ca): ca for ca in agents_list}
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def stop_all(self):
        for ca in list(self.agents.values()):
            if ca.status != "error":
                self._set_status(ca, "error", {"phase": "stop"})
        self.agents.clear()

    def reset(self):
        self.stop_all()
        self._used_containers.clear()


_runtime: Optional[ContainerRuntime] = None


def get_runtime() -> ContainerRuntime:
    global _runtime
    if _runtime is None:
        _runtime = ContainerRuntime()
    return _runtime
