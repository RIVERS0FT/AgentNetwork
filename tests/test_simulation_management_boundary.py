from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "agent_network" / "api" / "managed_simulations.py").read_text(
    encoding="utf-8"
)
EXECUTION_SOURCE = (ROOT / "agent_network" / "api" / "simulations.py").read_text(
    encoding="utf-8"
)
SERVER_SOURCE = (ROOT / "services" / "server.py").read_text(encoding="utf-8")
RUNTIME_SOURCE = (ROOT / "agent_network" / "agent_management.py").read_text(
    encoding="utf-8"
)


def test_simulation_management_is_a_first_class_package():
    package = ROOT / "agent_network" / "simulation_management"
    assert (package / "__init__.py").is_file()
    assert (package / "models.py").is_file()
    assert (package / "resource_allocator.py").is_file()
    assert (package / "simulation_manager.py").is_file()
    assert (package / "event_scheduler.py").is_file()


def test_authoritative_runtime_has_no_removed_round_scheduler_contract():
    paths = [
        ROOT / "agent_network" / "simulation_management",
        ROOT / "agent_network" / "api" / "managed_simulations.py",
        ROOT / "agent_network" / "agent_management.py",
        ROOT / "services" / "agent_server.py",
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        if path.is_file()
        else "\n".join(
            item.read_text(encoding="utf-8") for item in path.glob("*.py")
        )
        for path in paths
    )
    for removed in (
        "max_rounds",
        "stalemate_rounds",
        "current_turn",
        "event_driven_rounds",
        "def run_round",
    ):
        assert removed not in source


def test_managed_api_owns_all_simulation_lifecycle_operations():
    assert "simulation_manager = SimulationManager(" in API_SOURCE
    assert '@router.post("/simulations/configure")' in API_SOURCE
    assert '@router.post("/simulations/{simulation_id}/start")' in API_SOURCE
    assert '@router.post("/simulations/{simulation_id}/stop")' in API_SOURCE
    assert '@router.post("/simulations/{simulation_id}/force-stop")' in API_SOURCE
    assert '@router.get("/simulations/{simulation_id}")' in API_SOURCE
    assert "managed_simulations.router" in SERVER_SOURCE


def test_managed_api_does_not_read_or_patch_legacy_private_state():
    assert "execution._" not in API_SOURCE
    for removed in (
        "_pending_seed",
        "_pending_config",
        "_pending_scene_def",
        "orchestration._capture",
        "orchestration._capture_health",
    ):
        assert removed not in API_SOURCE
    assert "run.scene_definition" in API_SOURCE
    assert "run.execution_config" in API_SOURCE
    assert '"running": _simulation_is_active(current)' in API_SOURCE
    assert '"scene": current.scene if current else ""' in API_SOURCE
    for removed in (
        "_pending_seed",
        "_pending_config",
        "_pending_scene_def",
        "_comm_matrix",
    ):
        assert removed not in EXECUTION_SOURCE
    assert "def prepare_scene(" in EXECUTION_SOURCE
    assert "def run_simulation(" in EXECUTION_SOURCE


def test_container_runtime_enforces_simulation_resources_and_concurrency():
    assert 'kwargs["nano_cpus"]' in RUNTIME_SOURCE
    assert 'kwargs["mem_limit"]' in RUNTIME_SOURCE
    assert 'kwargs["pids_limit"]' in RUNTIME_SOURCE
    assert "container.kill()" in RUNTIME_SOURCE
    assert "max_parallel_agents" in RUNTIME_SOURCE


def test_agent_readiness_precedes_control_plane_configuration():
    readiness = EXECUTION_SOURCE.index("runtime.wait_for_agents_ready(")
    reset = EXECUTION_SOURCE.index('f"{assignment.url}/reset"')
    configure = EXECUTION_SOURCE.index(
        'f"{assignment.url}/communication/configure"'
    )

    assert readiness < reset < configure
    assert "time.sleep(1)" not in EXECUTION_SOURCE
    assert "agent_startup_timeout_seconds" in EXECUTION_SOURCE
    assert "agent_startup_timeout_seconds" in API_SOURCE
