"""Validate and resolve per-Agent simulation resource allocations."""

from __future__ import annotations

from typing import Any, Iterable

from .models import AgentResourceLimit, SimulationResourceAllocation


class ResourceAllocationError(RuntimeError):
    pass


class SimulationResourceAllocator:
    def build_plan(
        self,
        agent_ids: Iterable[str],
        allocation: SimulationResourceAllocation,
        runtime: Any = None,
    ) -> dict[str, Any]:
        ordered = list(dict.fromkeys(str(item).lower() for item in agent_ids if item))
        if not ordered:
            raise ResourceAllocationError("simulation requires at least one Agent")
        unknown = set(allocation.agent_overrides) - set(ordered)
        if unknown:
            raise ResourceAllocationError(
                f"resource overrides reference unknown Agents: {sorted(unknown)}"
            )

        agents = {}
        total_cpu = 0.0
        total_memory = 0
        total_pids = 0
        for agent_id in ordered:
            limits: AgentResourceLimit = allocation.agent_overrides.get(
                agent_id, allocation.default_agent
            )
            item = {
                "cpu_cores": float(limits.cpu_cores),
                "memory_mb": int(limits.memory_mb),
                "pids_limit": int(limits.pids_limit),
            }
            agents[agent_id] = item
            total_cpu += item["cpu_cores"]
            total_memory += item["memory_mb"]
            total_pids += item["pids_limit"]

        capacity = self._runtime_capacity(runtime)
        cpu_concurrent = sorted(
            agents.values(), key=lambda item: item["cpu_cores"], reverse=True
        )[: int(allocation.max_parallel_agents)]
        memory_concurrent = sorted(
            agents.values(), key=lambda item: item["memory_mb"], reverse=True
        )[: int(allocation.max_parallel_agents)]
        concurrent_cpu = sum(item["cpu_cores"] for item in cpu_concurrent)
        concurrent_memory = sum(item["memory_mb"] for item in memory_concurrent)
        if capacity.get("cpu_cores") and concurrent_cpu > capacity["cpu_cores"]:
            raise ResourceAllocationError(
                "concurrent CPU allocation "
                f"{concurrent_cpu} exceeds host capacity {capacity['cpu_cores']}"
            )
        if capacity.get("memory_mb") and concurrent_memory > capacity["memory_mb"]:
            raise ResourceAllocationError(
                "concurrent memory allocation "
                f"{concurrent_memory}MB exceeds host capacity {capacity['memory_mb']}MB"
            )

        return {
            "agents": agents,
            "max_parallel_agents": min(
                int(allocation.max_parallel_agents), len(ordered)
            ),
            "totals": {
                "cpu_cores": total_cpu,
                "memory_mb": total_memory,
                "pids_limit": total_pids,
            },
            "concurrent_totals": {
                "cpu_cores": concurrent_cpu,
                "memory_mb": concurrent_memory,
            },
            "host_capacity": capacity,
        }

    @staticmethod
    def _runtime_capacity(runtime: Any) -> dict[str, Any]:
        client = getattr(runtime, "_docker_client", None)
        if not client:
            return {}
        try:
            info = client.info()
            return {
                "cpu_cores": float(info.get("NCPU") or 0),
                "memory_mb": int((info.get("MemTotal") or 0) / (1024 * 1024)),
            }
        except Exception:
            return {}
