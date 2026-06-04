"""
Docker 容器运行时 — 管理 Agent 容器的生命周期

功能:
- 创建/启动/停止 Agent 容器
- 向容器发送指令
- 收集容器状态和日志
- 配合消息总线实现 Agent 间通信

依赖: docker SDK (pip install docker)
"""

import os
import json
import time
import requests
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


@dataclass
class ContainerAgent:
    """运行在 Docker 容器中的 Agent"""
    agent_id: str
    name: str
    role: str
    container_id: str = ""
    container_name: str = ""
    port: int = 8000
    status: str = "created"
    url: str = ""

    def to_dict(self):
        return {
            "agent_id": self.agent_id, "name": self.name, "role": self.role,
            "container_id": self.container_id, "port": self.port,
            "status": self.status, "url": self.url,
        }


class ContainerRuntime:
    """
    Agent 容器运行时管理器

    支持两种模式:
    - docker: 使用 Docker SDK 管理真实容器
    - process: 使用子进程模拟（无需 Docker）
    """

    def __init__(self, mode: str = "process", message_bus_url: str = "http://localhost:9000"):
        self.mode = mode
        self.message_bus_url = message_bus_url
        self.agents: Dict[str, ContainerAgent] = {}
        self._processes: List[Any] = []

        if mode == "docker":
            try:
                import docker
                self.docker_client = docker.from_env()
            except Exception as e:
                print(f"[Runtime] Docker not available: {e}, falling back to process mode")
                self.mode = "process"

    def create_agent(self, agent_id: str, role: str, name: str,
                     port: int = 0, llm_config: Dict = None) -> ContainerAgent:
        """创建并启动一个 Agent 容器"""
        # 自动分配端口
        if port == 0:
            used_ports = {a.port for a in self.agents.values()}
            port = 8100 + len(self.agents)
            while port in used_ports:
                port += 1

        ca = ContainerAgent(
            agent_id=agent_id, name=name, role=role, port=port,
            container_name=f"agent-{agent_id}",
            url=f"http://localhost:{port}",
        )

        if self.mode == "docker":
            self._start_docker_container(ca, llm_config)
        else:
            self._start_process(ca, llm_config)

        self.agents[agent_id] = ca
        return ca

    def _start_docker_container(self, ca: ContainerAgent, llm_config: Dict = None):
        """启动 Docker 容器"""
        import docker
        env = {
            "AGENT_ID": ca.agent_id,
            "AGENT_ROLE": ca.role,
            "AGENT_NAME": ca.name,
            "PORT": str(ca.port),
            "MESSAGE_BUS_URL": self.message_bus_url,
        }
        if llm_config:
            if llm_config.get("api_key"):
                env["LLM_API_KEY"] = llm_config["api_key"]
                env["LLM_MODEL"] = llm_config.get("model", "")
                env["LLM_PROVIDER"] = llm_config.get("provider", "auto")

        container = self.docker_client.containers.run(
            "agent-network:latest",
            name=ca.container_name,
            detach=True,
            ports={f"{ca.port}/tcp": ca.port},
            environment=env,
            network_mode="bridge",
        )
        ca.container_id = container.id
        ca.status = "running"
        time.sleep(1)  # 等待容器启动
        self._register_with_bus(ca)

    def _start_process(self, ca: ContainerAgent, llm_config: Dict = None):
        """使用子进程模拟容器"""
        import subprocess
        env = os.environ.copy()
        env["AGENT_ID"] = ca.agent_id
        env["AGENT_ROLE"] = ca.role
        env["AGENT_NAME"] = ca.name
        env["PORT"] = str(ca.port)
        env["MESSAGE_BUS_URL"] = self.message_bus_url
        if llm_config:
            if llm_config.get("api_key"):
                env["LLM_API_KEY"] = llm_config["api_key"]
                env["LLM_MODEL"] = llm_config.get("model", "")

        proc = subprocess.Popen(
            [sys.executable, "agent_server.py"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._processes.append(proc)
        ca.status = "running"
        time.sleep(0.5)
        self._register_with_bus(ca)

    def _register_with_bus(self, ca: ContainerAgent):
        """向消息总线注册"""
        try:
            requests.post(f"{self.message_bus_url}/register",
                         params={"agent_id": ca.agent_id, "url": ca.url}, timeout=3)
        except Exception as e:
            print(f"[Runtime] Failed to register {ca.agent_id}: {e}")

    def stop_agent(self, agent_id: str):
        """停止 Agent"""
        ca = self.agents.get(agent_id)
        if not ca:
            return
        try:
            requests.post(f"{self.message_bus_url}/unregister", params={"agent_id": agent_id}, timeout=2)
        except Exception:
            pass
        if self.mode == "docker" and ca.container_id:
            try:
                container = self.docker_client.containers.get(ca.container_id)
                container.stop()
                container.remove()
            except Exception:
                pass
        ca.status = "stopped"

    def stop_all(self):
        for aid in list(self.agents.keys()):
            self.stop_agent(aid)
        for proc in self._processes:
            try:
                proc.terminate()
            except Exception:
                pass
        self._processes.clear()

    def decide_all(self, context: Dict = None) -> List[Dict]:
        """触发所有 Agent 决策，返回决策结果"""
        results = []
        for ca in self.agents.values():
            try:
                resp = requests.post(f"{ca.url}/decide",
                                    json={"context": context or {}}, timeout=30)
                results.append(resp.json())
            except Exception as e:
                results.append({"agent_id": ca.agent_id, "error": str(e)})
        return results

    def act_all(self) -> List[Dict]:
        """触发所有 Agent 执行决策"""
        results = []
        for ca in self.agents.values():
            try:
                resp = requests.post(f"{ca.url}/act", timeout=30)
                results.append(resp.json())
            except Exception as e:
                results.append({"agent_id": ca.agent_id, "error": str(e)})
        return results

    def run_round(self, context: Dict = None) -> Dict:
        """执行一轮：决策 → 执行 → 收集结果"""
        decisions = self.decide_all(context)
        actions = self.act_all()
        return {"decisions": decisions, "actions": actions}

    def get_all_status(self) -> List[Dict]:
        """获取所有 Agent 状态"""
        statuses = []
        for ca in self.agents.values():
            try:
                resp = requests.get(f"{ca.url}/status", timeout=3)
                statuses.append({**ca.to_dict(), **resp.json()})
            except Exception:
                statuses.append({**ca.to_dict(), "error": "unreachable"})
        return statuses


# 全局单例
import sys
_runtime: Optional[ContainerRuntime] = None


def get_runtime(mode: str = "process") -> ContainerRuntime:
    global _runtime
    if _runtime is None:
        bus_url = os.environ.get("MESSAGE_BUS_URL", "http://localhost:9000")
        _runtime = ContainerRuntime(mode=mode, message_bus_url=bus_url)
    return _runtime
