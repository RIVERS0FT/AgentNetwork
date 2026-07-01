"""
Agent 容器运行时 — 预创建池 + 动态扩容。

ContainerRuntime 是多 Agent 控制面组件，只负责：
- 分配/创建容器
- 向 Agent 容器下发 AgentContext
- 收集 BackendAdapter 返回结果
- 维护容器状态

它不实现单 Agent ReAct、记忆或 Tool 选择。
"""

import os
import time
import requests
from typing import Callable, Dict, List, Any, Optional, Set
from dataclasses import dataclass


@dataclass
class ContainerAgent:
    agent_id: str
    name: str
    role: str
    container_id: str = ""
    container_name: str = ""
    port: int = 8000
    status: str = "idle"
    url: str = ""

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "container_id": self.container_id,
            "container_name": self.container_name,
            "port": self.port,
            "status": self.status,
            "url": self.url,
        }


class ContainerRuntime:
    """Agent container manager and outer tick scheduler."""

    BACKEND_CONFIG = {
        "claude-code": {"image": "agentnetwork-ag-c1", "cmd": "python3 services/agent_server.py", "prefix": "ag-c"},
        # OpenCLAW agents are self-contained: the entrypoint script starts the
        # local OpenCLAW gateway first, waits for it, then starts agent_server.py.
        "openclaw": {"image": "agentnetwork-ag-o1", "cmd": "/app/start-openclaw-agent.sh", "prefix": "ag-o"},
    }
    DEFAULT_BACKEND = "openclaw"
    NETWORK_NAME = "an"
    INTERNAL_PORT = 8000

    def __init__(self, message_bus_url: str = "http://message-bus:9000"):
        self.message_bus_url = message_bus_url
        self.agents: Dict[str, ContainerAgent] = {}
        self._docker_client = None
        self._used_containers: Set[str] = set()
        self._status_listener: Optional[Callable[[ContainerAgent, str, Optional[Dict[str, Any]]], None]] = None
        self._init_docker()

    def set_status_listener(self, callback: Optional[Callable[[ContainerAgent, str, Optional[Dict[str, Any]]], None]]):
        self._status_listener = callback

    def _set_status(self, ca: ContainerAgent, status: str, detail: Dict[str, Any] = None):
        ca.status = status
        if self._status_listener:
            try:
                self._status_listener(ca, status, detail)
            except Exception as exc:
                print(f"[Runtime] status listener failed for {ca.agent_id}: {exc}")
        callback = getattr(self, "on_status_change", None)
        if callback:
            try:
                callback(ca.agent_id, status)
            except Exception as exc:
                print(f"[Runtime] on_status_change failed for {ca.agent_id}: {exc}")

    def _normalize_backend(self, backend: str) -> str:
        backend = (backend or self.DEFAULT_BACKEND).strip()
        if backend == "claudecode":
            return "claude-code"
        if backend == "brain":
            raise RuntimeError("Backend 'brain' has been removed. Use 'openclaw' or 'claude-code'.")
        if backend not in self.BACKEND_CONFIG:
            supported = ", ".join(sorted(self.BACKEND_CONFIG.keys()))
            raise RuntimeError(f"Unsupported backend '{backend}'. Supported backends: {supported}")
        return backend

    def _init_docker(self):
        try:
            import docker
            for _ in range(2):
                try:
                    self._docker_client = docker.from_env()
                    self._docker_client.ping()
                    print("[Runtime] Docker OK")
                    return
                except Exception:
                    time.sleep(1)
        except ImportError:
            pass
        print("[Runtime] No Docker SDK, container discovery only")

    def _get_running_containers(self, backend: str) -> List[str]:
        backend = self._normalize_backend(backend)
        cfg = self.BACKEND_CONFIG[backend]
        prefix = cfg["prefix"]
        containers = []
        if self._docker_client:
            try:
                for c in self._docker_client.containers.list():
                    if c.name.startswith(prefix):
                        containers.append(c.name)
            except Exception:
                pass
        return sorted(containers)

    def _is_running_inside_container(self) -> bool:
        return os.path.exists("/.dockerenv")

    def _self_mount_sources(self) -> Dict[str, str]:
        if not self._docker_client:
            return {}
        candidates = [
            os.environ.get("HOSTNAME"),
            os.environ.get("CONTAINER_NAME"),
            os.environ.get("AGENT_RUNTIME_CONTAINER"),
            "srv",
        ]
        for name in [c for c in candidates if c]:
            try:
                container = self._docker_client.containers.get(name)
                mounts = container.attrs.get("Mounts", [])
                sources = {}
                for mount in mounts:
                    dest = mount.get("Destination")
                    source = mount.get("Source")
                    if dest and source:
                        sources[dest.rstrip("/")] = source
                if sources:
                    return sources
            except Exception:
                continue
        return {}

    def _dynamic_code_volumes(self) -> Dict[str, Dict[str, str]]:
        enabled = os.environ.get("AGENT_DYNAMIC_CODE_MOUNTS", "1").lower()
        if enabled in {"0", "false", "no", "off"}:
            return {}

        root = os.environ.get("AGENT_HOST_PROJECT_ROOT") or os.environ.get("HOST_PROJECT_ROOT")
        sources = {
            "/app/services/agent_server.py": os.environ.get("AGENT_SERVER_HOST_PATH"),
            "/app/agent_network": os.environ.get("AGENT_NETWORK_HOST_PATH"),
            "/app/scenes": os.environ.get("AGENT_SCENES_HOST_PATH"),
        }
        if root:
            sources["/app/services/agent_server.py"] = sources["/app/services/agent_server.py"] or os.path.join(root, "services", "agent_server.py")
            sources["/app/agent_network"] = sources["/app/agent_network"] or os.path.join(root, "agent_network")
            sources["/app/scenes"] = sources["/app/scenes"] or os.path.join(root, "scenes")

        mount_sources = self._self_mount_sources()
        for dest in list(sources.keys()):
            sources[dest] = sources[dest] or mount_sources.get(dest)

        if not self._is_running_inside_container():
            cwd = os.getcwd()
            sources["/app/services/agent_server.py"] = sources["/app/services/agent_server.py"] or os.path.join(cwd, "services", "agent_server.py")
            sources["/app/agent_network"] = sources["/app/agent_network"] or os.path.join(cwd, "agent_network")
            sources["/app/scenes"] = sources["/app/scenes"] or os.path.join(cwd, "scenes")

        volumes = {}
        for dest, source in sources.items():
            if source:
                mode = "ro" if dest == "/app/scenes" else "rw"
                volumes[source] = {"bind": dest, "mode": mode}
        return volumes

    def _get_or_create_container(self, backend: str) -> str:
        backend = self._normalize_backend(backend)
        cfg = self.BACKEND_CONFIG[backend]
        prefix = cfg["prefix"]

        running = self._get_running_containers(backend)
        for name in running:
            if name not in self._used_containers:
                self._used_containers.add(name)
                print(f"[Runtime] Assign {name} (from pool)")
                return name

        if not self._docker_client:
            raise RuntimeError(
                f"No {backend} containers available and Docker SDK unavailable. "
                f"Pool size: {len(running)}, all {len(self._used_containers)} in use."
            )

        auto_n = len([c for c in self._used_containers if c.startswith(prefix)]) + 10
        auto_name = f"{prefix}{auto_n}"
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
                "MESSAGE_BUS_URL": self.message_bus_url,
                "SERVER_URL": os.environ.get("SERVER_URL", "http://srv:8000"),
                "LOG_DOCKER_HTTP": os.environ.get("LOG_DOCKER_HTTP", "0"),
                "LOG_LLM_API": os.environ.get("LOG_LLM_API", "1"),
                "LOG_TRAFFIC": os.environ.get("LOG_TRAFFIC", "0"),
                "MOCK_LLM": os.environ.get("MOCK_LLM", "0"),
            }
            for key in (
                "LLM_API_KEY", "LLM_MODEL", "LLM_API_BASE", "LLM_PROVIDER",
                "LLM_MAX_TOKENS", "LLM_TEMPERATURE", "LLM_TIMEOUT_SECONDS",
                "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_BASE",
                "OPENAI_API_KEY", "AGENT_STRICT_BACKEND_SDK",
                "AGENT_MESSAGE_HISTORY_TURNS", "AGENT_RECENT_HISTORY_TURNS",
                "OPENCLAW_START_GATEWAY", "OPENCLAW_GATEWAY_HOST",
                "OPENCLAW_GATEWAY_PORT", "OPENCLAW_GATEWAY_WS_URL",
                "OPENCLAW_GATEWAY_CMD", "OPENCLAW_GATEWAY_READY_TIMEOUT",
                "OPENCLAW_API_KEY", "OPENCLAW_OPENAI_BASE_URL",
                "OPENCLAW_DEFAULT_AGENT_ID", "OPENCLAW_SESSION_NAME",
            ):
                if os.environ.get(key):
                    env[key] = os.environ[key]

            run_kwargs = {
                "name": auto_name,
                "detach": True,
                "command": cfg["cmd"],
                "environment": env,
                "network": self.NETWORK_NAME,
                "cap_add": ["NET_RAW", "NET_ADMIN"],
            }
            volumes = self._dynamic_code_volumes()
            if volumes:
                run_kwargs["volumes"] = volumes
            container = self._docker_client.containers.run(cfg["image"], **run_kwargs)
            self._used_containers.add(auto_name)
            print(f"[Runtime] Created {auto_name} ({backend}) container={container.id[:12]} code_mounts={len(volumes)}")
            return auto_name
        except Exception as exc:
            raise RuntimeError(
                f"Pool exhausted for backend '{backend}': {len(running)} pool containers, "
                f"dynamic creation failed: {exc}"
            )

    def assign_agent(self, agent_id: str, role: str, name: str, extra_meta: Dict = None) -> ContainerAgent:
        extra_meta = extra_meta or {}
        try:
            backend = self._normalize_backend(extra_meta.get("backend", self.DEFAULT_BACKEND))
            container_name = self._get_or_create_container(backend)
            url = f"http://{container_name}:{self.INTERNAL_PORT}"
            status = "idle"
            assign_error = None
        except RuntimeError as exc:
            container_name = ""
            url = ""
            status = "error"
            assign_error = str(exc)
            print(f"[Runtime] assign_agent failed for {agent_id} ({name}): {exc}")

        ca = ContainerAgent(
            agent_id=agent_id,
            name=name,
            role=role,
            container_name=container_name,
            port=self.INTERNAL_PORT,
            url=url,
            status=status,
        )
        ca._extra_meta = extra_meta
        ca._assign_error = assign_error
        self.agents[agent_id] = ca
        self._set_status(ca, status, {"phase": "assign"})
        return ca

    def run_round(self, context: Dict = None) -> Dict:
        return {"results": self.run_all(context)}

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

                extra_meta = getattr(ca, "_extra_meta", {}) or {}
                if extra_meta.get("skills_list"):
                    ctx["skills"] = extra_meta["skills_list"]
                if extra_meta.get("core_goal"):
                    ctx["core_goal"] = extra_meta["core_goal"]
                if extra_meta.get("action_space"):
                    ctx["allowed_tools"] = extra_meta["action_space"]
                if extra_meta.get("scene_key"):
                    ctx["scene_key"] = extra_meta["scene_key"]
                if extra_meta.get("allowed_skills"):
                    ctx["allowed_skills"] = extra_meta["allowed_skills"]
                if extra_meta.get("allowed_tools"):
                    ctx["allowed_tools"] = list(dict.fromkeys((ctx.get("allowed_tools") or []) + extra_meta["allowed_tools"]))

                agent_tasks = (ctx.get("tasks") or {}).get(ca.agent_id, [])
                if agent_tasks:
                    ctx["task"] = "\n".join([t for t in agent_tasks if t])

                if not ctx.get("task") and not ctx.get("messages"):
                    self._set_status(ca, "idle", {"phase": "run:skip", "reason": "no_task_or_message"})
                    return {
                        "agent_id": ca.agent_id,
                        "status": "skipped",
                        "reason": "no_task_or_message",
                        "outbound_messages": [],
                        "tool_events": [],
                        "state_changes": [],
                    }

                response = requests.post(f"{ca.url}/run", json=ctx, timeout=300)
                response.raise_for_status()
                result = response.json()
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
        self._cleanup_orphan_containers()

    def _cleanup_orphan_containers(self):
        if not self._docker_client:
            return
        pool_names = set()
        for cfg in self.BACKEND_CONFIG.values():
            for i in range(1, 10):
                pool_names.add(f"{cfg['prefix']}{i}")
        try:
            for container in self._docker_client.containers.list(all=True):
                for prefix in [cfg["prefix"] for cfg in self.BACKEND_CONFIG.values()]:
                    if container.name.startswith(prefix) and container.name not in pool_names:
                        try:
                            container.remove(force=True)
                        except Exception:
                            pass
                        break
        except Exception:
            pass


_runtime: Optional[ContainerRuntime] = None


def get_runtime() -> ContainerRuntime:
    global _runtime
    if _runtime is None:
        bus_url = os.environ.get("MESSAGE_BUS_URL", "http://message-bus:9000")
        _runtime = ContainerRuntime(message_bus_url=bus_url)
    return _runtime
