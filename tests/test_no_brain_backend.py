import subprocess
import os

def test_brain_backend_removed():
    """
    Test that starting agent_server.py with AGENT_BACKEND=brain
    raises a RuntimeError and fails.
    """
    env = os.environ.copy()
    env["AGENT_BACKEND"] = "brain"
    
    server_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "services", "agent_server.py")
    
    import sys
    result = subprocess.run(
        [sys.executable, server_script],
        env=env,
        capture_output=True,
        text=True
    )
    
    # Process should fail
    assert result.returncode != 0
    # RuntimeError should be raised with specific message
    assert "Unsupported AGENT_BACKEND='brain'" in result.stderr
    assert "The brain backend has been removed" in result.stderr

def test_no_brain_backend():
    test_brain_backend_removed()

if __name__ == "__main__":
    test_brain_backend_removed()
    print("Test passed: brain backend correctly triggers RuntimeError")
