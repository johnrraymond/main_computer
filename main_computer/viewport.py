"""Compatibility exports for the Main Computer viewport server.

The implementation lives in ``main_computer.viewport_server`` so Aider can work
with smaller focused files while existing imports from ``main_computer.viewport``
continue to work.
"""

import os
from pathlib import Path

from main_computer.heartbeat import HeartbeatConfig, ensure_heartbeat_service
from main_computer.config import MainComputerConfig
from main_computer.viewport_server import *  # noqa: F401,F403
from main_computer.viewport_server import _application_route_target  # noqa: F401
from main_computer.viewport_state import (
    _clear_viewport_pid_file,
    _control_root_path,
    _viewport_pid_path,
    _write_viewport_pid_file,
)


def serve(config: MainComputerConfig, host: str = "127.0.0.1", port: int = 8765, *, verbose: bool = True) -> None:
    """Compatibility wrapper whose globals remain patchable via main_computer.viewport."""

    runtime_root = Path.cwd().resolve()
    control_root = _control_root_path(runtime_root)
    heartbeat_port = int(os.environ.get("MAIN_COMPUTER_HEARTBEAT_PORT") or port + 1)
    viewport_pid_file = _viewport_pid_path(control_root)
    ensure_heartbeat_service(
        HeartbeatConfig(
            workspace=runtime_root,
            bind_host=host,
            server_port=port,
            heartbeat_port=heartbeat_port,
            verbose=bool(verbose or config.fallback),
            control_root=control_root,
        )
    )
    server = ViewportServer((host, port), config, verbose=bool(verbose or config.fallback))
    _write_viewport_pid_file(viewport_pid_file, os.getpid())
    server.signal(
        "server-start",
        url=f"http://{host}:{server.server_port}",
        provider=server.provider_name,
        model=config.model,
        patch_level=config.patch_level,
        workspace=config.workspace,
        pid_file=viewport_pid_file,
        fallback=config.fallback,
    )
    server.worker_runtime_supervisor.start(wait_for_initial_reconcile=True)
    print(f"Main Computer viewport: http://{host}:{server.server_port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.signal("server-interrupt")
        print("\nViewport stopped.")
    finally:
        server.worker_runtime_supervisor.stop()
        if _viewport_pid_path(control_root) == viewport_pid_file:
            _clear_viewport_pid_file(viewport_pid_file)
        server.signal("server-stop")
        server.server_close()
