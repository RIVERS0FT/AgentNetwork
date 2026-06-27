import time
import json
import importlib.util
from pathlib import Path
from typing import Dict, Any, List
from agent_network.skill_md_loader import LocalSkillRegistry

class LocalToolRuntime:
    def __init__(
        self,
        scene_key: str,
        scenes_root: str,
        skill_registry: LocalSkillRegistry,
        agent_id: str,
        agent_name: str,
        server_url: str,
        allowed_tools: List[str] = None
    ):
        self.scene_key = scene_key
        self.scenes_root = Path(scenes_root)
        self.skill_registry = skill_registry
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.server_url = server_url
        self.allowed_tools = set(allowed_tools or [])
        self._tools_module = None
        self._tool_registry = None
        
        self._load_tools()

    def _load_tools(self):
        tools_path = self.scenes_root / self.scene_key / "tools.py"
        if not tools_path.exists():
            return
            
        try:
            spec = importlib.util.spec_from_file_location(f"tools_{self.scene_key}_{self.agent_id}", tools_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._tools_module = mod
            if hasattr(mod, "ToolRegistry"):
                self._tool_registry = mod.ToolRegistry
        except Exception as e:
            print(f"[LocalToolRuntime] Failed to load tools.py for {self.scene_key}: {e}")

    def execute_skill(self, skill_name: str, arguments: dict, context: dict) -> dict:
        start_time = time.time()
        
        # 1. Check if skill exists
        skill = self.skill_registry.get(skill_name)
        if not skill:
            return {
                "skill": skill_name,
                "status": "error",
                "execution_mode": "agent_local",
                "arguments": arguments,
                "error": "unauthorized_skill",
                "tool_results": [],
                "result": {},
                "latency_ms": 0,
                "traffic": {}
            }

        tools_to_run = skill.tools
        
        # 2. Check tool authorization
        if self.allowed_tools:
            for t in tools_to_run:
                if t not in self.allowed_tools:
                    return {
                        "skill": skill_name,
                        "status": "error",
                        "execution_mode": "agent_local",
                        "arguments": arguments,
                        "error": f"unauthorized_tool: {t}",
                        "tool_results": [],
                        "result": {},
                        "latency_ms": 0,
                        "traffic": {}
                    }

        tool_results = []
        overall_status = "success"
        combined_result = {}
        
        # 3. If tools is empty, return SOP
        if not tools_to_run:
            combined_result = {"sop": skill.sop_content}
        else:
            if not self._tool_registry:
                overall_status = "error"
                combined_result = {"error": "tools.py or ToolRegistry not found"}
            else:
                for tool_name in tools_to_run:
                    t_res = {"tool": tool_name}
                    try:
                        res = self._tool_registry.execute(tool_name, **arguments)
                        t_res["status"] = "success"
                        t_res["result"] = res
                        combined_result[tool_name] = res
                    except Exception as e:
                        t_res["status"] = "error"
                        t_res["error"] = str(e)
                        combined_result[tool_name] = {"error": str(e)}
                        overall_status = "error"
                    tool_results.append(t_res)

        latency_ms = (time.time() - start_time) * 1000
        
        # 4. Estimate traffic
        traffic = self._estimate_traffic(skill_name, arguments, combined_result)

        return {
            "skill": skill_name,
            "status": overall_status,
            "execution_mode": "agent_local",
            "arguments": arguments,
            "tool_results": tool_results,
            "result": combined_result,
            "latency_ms": latency_ms,
            "traffic": traffic,
            "events": []
        }

    def _estimate_traffic(self, skill_name: str, arguments: dict, result: dict) -> dict:
        # Check if result contains explicit traffic
        if isinstance(result, dict) and len(result) == 1:
            first_key = list(result.keys())[0]
            if isinstance(result[first_key], dict) and "traffic" in result[first_key]:
                return result[first_key]["traffic"]
                
        # Estimate
        arg_bytes = len(json.dumps(arguments, ensure_ascii=False).encode('utf-8'))
        res_bytes = len(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        return {
            "bytes": arg_bytes + res_bytes,
            "direction": "internal",
            "source": self.agent_id,
            "target": skill_name,
            "action": skill_name
        }
