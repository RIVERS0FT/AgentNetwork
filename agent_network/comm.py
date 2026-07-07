import requests
from typing import Dict, List, Any
from abc import ABC, abstractmethod


class CommLayer(ABC):
    inbox: List[Dict[str, Any]]

    @abstractmethod
    def send(self, from_id: str, from_name: str, target: str, content: str,
             channel_id: str = "", talk: str = "") -> bool:
        ...

    @abstractmethod
    def broadcast(self, from_id: str, from_name: str, content: str, allowed: set = None,
                  channel_id: str = "", talk: str = "") -> bool:
        ...

    @abstractmethod
    def register_agent(self, agent_id: str, name: str, url: str = "") -> None:
        ...


class DirectBus(CommLayer):
    def __init__(self, agent_directory: Dict[str, str] = None, comm_matrix: Dict[str, Any] = None):
        self.agent_directory = {str(k).lower(): v for k, v in (agent_directory or {}).items() if v}
        self.comm_matrix = {
            str(k).lower(): {str(item).lower() for item in (v or [])}
            for k, v in (comm_matrix or {}).items()
        }
        self.inbox: List[Dict[str, Any]] = []

    def update_directory(self, agent_directory: Dict[str, str] = None, comm_matrix: Dict[str, Any] = None):
        if agent_directory is not None:
            self.agent_directory = {str(k).lower(): v for k, v in agent_directory.items() if v}
        if comm_matrix is not None:
            self.comm_matrix = {
                str(k).lower(): {str(item).lower() for item in (v or [])}
                for k, v in comm_matrix.items()
            }

    def _allowed(self, from_id: str, target: str) -> bool:
        if not self.comm_matrix:
            return True
        return str(target).lower() in self.comm_matrix.get(str(from_id).lower(), set())

    def register_agent(self, agent_id: str, name: str, url: str = "") -> None:
        if agent_id and url:
            self.agent_directory[str(agent_id).lower()] = url.rstrip("/")

    def send(self, from_id: str, from_name: str, target: str, content: str,
             channel_id: str = "", talk: str = "") -> bool:
        source_id = str(from_id).lower()
        target_id = str(target).lower()
        if not self._allowed(source_id, target_id):
            return False
        target_url = self.agent_directory.get(target_id)
        if not target_url:
            return False
        try:
            resp = requests.post(
                f"{target_url.rstrip('/')}/message",
                json={
                    "from_id": source_id,
                    "from_name": from_name,
                    "to": target_id,
                    "content": content,
                    "type": "direct",
                    "channel_id": channel_id,
                    "talk": talk,
                },
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    def broadcast(self, from_id: str, from_name: str, content: str, allowed: set = None,
                  channel_id: str = "", talk: str = "") -> bool:
        source_id = str(from_id).lower()
        explicit_allowed = {str(item).lower() for item in (allowed or set())}
        ok_all = True
        for target_id in sorted(self.agent_directory.keys()):
            if target_id == source_id:
                continue
            if explicit_allowed and target_id not in explicit_allowed:
                continue
            ok_all = self.send(source_id, from_name, target_id, content, channel_id, talk) and ok_all
        return ok_all
