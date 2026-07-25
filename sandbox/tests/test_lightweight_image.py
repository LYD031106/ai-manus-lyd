from pathlib import Path


SANDBOX_ROOT = Path(__file__).resolve().parents[1]


def test_lightweight_dockerfile_excludes_browser_and_node_components():
    dockerfile = (SANDBOX_ROOT / "Dockerfile.lightweight").read_text(
        encoding="utf-8"
    ).lower()

    forbidden = (
        "chromium",
        "google-chrome",
        "nodejs",
        "xvfb",
        "x11vnc",
        "websockify",
        "socat",
        "xterm",
    )
    assert not any(component in dockerfile for component in forbidden)


def test_lightweight_supervisor_runs_only_the_api():
    supervisor = (
        SANDBOX_ROOT / "supervisord.lightweight.conf"
    ).read_text(encoding="utf-8").lower()

    assert "[program:app]" in supervisor
    assert "uvicorn app.main:app" in supervisor
    assert "[program:chrome]" not in supervisor
    assert "[program:xvfb]" not in supervisor
    assert "[program:x11vnc]" not in supervisor
