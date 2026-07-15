from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from main_computer.viewport_state import *  # noqa: F401,F403
from main_computer.viewport_http import ViewportHttpMixin
from main_computer.viewport_route_dispatch import dispatch_get, dispatch_post
from main_computer.viewport_routes_applications import ViewportApplicationRoutesMixin
from main_computer.viewport_routes_aider import ViewportAiderRoutesMixin
from main_computer.viewport_routes_astrometric import ViewportAstrometricRoutesMixin
from main_computer.astrometric_renderer_service import AstrometricRendererService
from main_computer.game_gpu_forge_service import GameGpuForgeService
from main_computer.viewport_routes_calculator import ViewportCalculatorRoutesMixin
from main_computer.viewport_routes_chat_console import ViewportChatConsoleRoutesMixin
from main_computer.viewport_routes_conductor import ViewportConductorRoutesMixin
from main_computer.viewport_routes_component_docs import ViewportComponentDocsRoutesMixin
from main_computer.viewport_routes_debug import ViewportDebugRoutesMixin
from main_computer.viewport_routes_docs import ViewportDocsRoutesMixin
from main_computer.viewport_routes_editor import ViewportEditorRoutesMixin
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin, WorkerRuntimeService
from main_computer.worker_runtime_supervisor import WorkerRuntimeSupervisor
from main_computer.viewport_routes_executor import ViewportExecutorRoutesMixin
from main_computer.viewport_routes_file_explorer import ViewportFileExplorerRoutesMixin
from main_computer.viewport_routes_game import ViewportGameRoutesMixin
from main_computer.viewport_routes_git import ViewportGitRoutesMixin
from main_computer.viewport_routes_mcel import ViewportMcelRoutesMixin
from main_computer.viewport_routes_onlyoffice import ViewportOnlyOfficeRoutesMixin
from main_computer.viewport_routes_rag_assisted_thinking import ViewportRagAssistedThinkingRoutesMixin
from main_computer.viewport_routes_spreadsheet import ViewportSpreadsheetRoutesMixin
from main_computer.viewport_routes_task import ViewportTaskRoutesMixin
from main_computer.viewport_routes_terminal import ViewportTerminalRoutesMixin
from main_computer.activity import ActivityBus
from main_computer.chat_ai_subprocess import ChatAISubprocessManager
from main_computer.conductor import ConductorService
from main_computer.dev_chain_runtime import apply_dev_chain_runtime_config
from main_computer.executor_backend import create_executor_backend
from main_computer.mounted_windows_paths import build_mounted_windows_path_resolver


HUB_CONFIGURATION_FILENAME = "hub_configuration.json"


def _load_saved_hub_runtime_config(config: MainComputerConfig, runtime_root: Path) -> MainComputerConfig:
    path = runtime_root / HUB_CONFIGURATION_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(data, dict):
        return config

    hub_url = str(data.get("hub_url") or config.hub_url).strip() or config.hub_url
    hub_client_node_id = str(data.get("hub_client_node_id") or config.hub_client_node_id).strip() or config.hub_client_node_id
    hub_high_security = bool(config.hub_high_security)
    if "hub_high_security" in data:
        value = str(data.get("hub_high_security")).strip().lower()
        hub_high_security = value in {"1", "true", "yes", "on"}
    try:
        hub_timeout_s = max(1.0, float(data.get("hub_timeout_s", config.hub_timeout_s)))
    except (TypeError, ValueError):
        hub_timeout_s = config.hub_timeout_s

    return replace(
        config,
        hub_url=hub_url.rstrip("/"),
        hub_client_node_id=hub_client_node_id,
        hub_high_security=hub_high_security,
        hub_timeout_s=hub_timeout_s,
    )

class ViewportServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: MainComputerConfig, *, verbose: bool = True) -> None:
        super().__init__(server_address, ViewportHandler)
        self.debug_root = Path.cwd().resolve()
        self.control_root = _control_root_path(self.debug_root)
        config = _load_saved_hub_runtime_config(config, self.debug_root)
        config = apply_dev_chain_runtime_config(config, self.debug_root)
        self.activity = ActivityBus(self.debug_root)
        self.chat_ai_processes = ChatAISubprocessManager()
        self.config = config
        self.mounted_windows_path_resolver = build_mounted_windows_path_resolver(config)
        self.computer = MainComputer.build(config)
        self.fallback = bool(config.fallback)
        self.verbose = bool(verbose or self.fallback)
        self.ollama_debug_active = False
        self.ollama_ps_cache: dict[str, Any] = {"expires_at": 0.0, "payload": None}
        executor_root = config.executor_root
        if not executor_root.is_absolute():
            executor_root = self.debug_root / executor_root
        self.executor_backend = create_executor_backend(config, runtime_root=executor_root)
        # Backward-compatible alias for older tests/extensions that still refer
        # to server.docker_executor while the neutral executor framework rolls out.
        self.docker_executor = self.executor_backend
        self.debug_assets_root = self.debug_root / "debug_assets"
        self.aider_config = AiderAgentConfig(
            workspace=config.workspace,
            fallback=self.fallback,
            extra_env={
                "OLLAMA_API_BASE": config.ollama_base_url,
                "OLLAMA_BASE_URL": config.ollama_base_url,
            },
        )
        self.energy_ledger = EnergyCreditLedger(self.debug_root / "energy_credits")
        self.energy_chain = EnergyChainClient(
            rpc_url=config.energy_chain_rpc_url,
            expected_chain_id=config.energy_chain_id,
            rpc_url_source=config.energy_chain_rpc_url_source,
            expected_chain_id_source=config.energy_chain_id_source,
        )
        self.task_manager = TaskManagerService(self.debug_root, default_port=int(self.server_port), control_root=self.control_root)
        self.conductor = ConductorService(self.debug_root)
        self.git_tools = GitToolsService(self.debug_root)
        self.revisions = RevisionControl(self.debug_root, self.debug_root / "revision_control")
        self.debug_asset_revisions = DebugAssetRevisionControl(
            self.debug_assets_root,
            self.debug_root / "debug_asset_revisions",
        )
        self.aider_web_context = AiderWebContextStore(self.debug_root / "aider_web_context")
        self.astrometric_renderer = AstrometricRendererService(self.debug_root)
        self.game_gpu_forge = GameGpuForgeService(self.debug_root)
        self.aider_jobs = AiderActionJobRegistry(self)
        self.worker_runtime_lock = threading.RLock()
        self.worker_runtime_service = WorkerRuntimeService(self)
        self.worker_runtime_supervisor = WorkerRuntimeSupervisor(self.worker_runtime_service)

    def signal(self, name: str, **fields: Any) -> None:
        if name in {"api-activity-snapshot", "api-activity-ollama-ps"}:
            return
        if hasattr(self, "activity"):
            self.activity.record_signal(name, fields)
        if not self.verbose:
            return
        detail = " ".join(f"{key}={value}" for key, value in fields.items())
        if detail:
            print(f"[signal] {name} {detail}", flush=True)
        else:
            print(f"[signal] {name}", flush=True)

    def request_hard_halt(self, *, source: str = "unknown") -> None:
        """Stop the viewport serve loop after the HTTP response is sent."""

        self.signal("server-hard-halt-requested", source=source)

        def _shutdown() -> None:
            time.sleep(0.1)
            try:
                self.shutdown()
            except Exception as exc:
                self.signal("server-hard-halt-error", error=exc)

        thread = threading.Thread(
            target=_shutdown,
            name="main-computer-viewport-hard-halt",
            daemon=True,
        )
        thread.start()

class ViewportHandler(
    ViewportHttpMixin,
    ViewportApplicationRoutesMixin,
    ViewportAiderRoutesMixin,
    ViewportAstrometricRoutesMixin,
    ViewportCalculatorRoutesMixin,
    ViewportChatConsoleRoutesMixin,
    ViewportConductorRoutesMixin,
    ViewportComponentDocsRoutesMixin,
    ViewportDebugRoutesMixin,
    ViewportDocsRoutesMixin,
    ViewportEditorRoutesMixin,
    ViewportEnergyRoutesMixin,
    ViewportExecutorRoutesMixin,
    ViewportFileExplorerRoutesMixin,
    ViewportGameRoutesMixin,
    ViewportGitRoutesMixin,
    ViewportMcelRoutesMixin,
    ViewportOnlyOfficeRoutesMixin,
    ViewportRagAssistedThinkingRoutesMixin,
    ViewportSpreadsheetRoutesMixin,
    ViewportTaskRoutesMixin,
    ViewportTerminalRoutesMixin,
    BaseHTTPRequestHandler,
):
    server: ViewportServer

    def do_GET(self) -> None:
        dispatch_get(self)

    def do_POST(self) -> None:
        dispatch_post(self)

def _provider_name(self: ViewportServer) -> str:
    return self.computer.provider.name


ViewportServer.provider_name = property(_provider_name)  # type: ignore[attr-defined]


def serve(config: MainComputerConfig, host: str = "127.0.0.1", port: int = 8765, *, verbose: bool = True) -> None:
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
            verbose=verbose,
            control_root=control_root,
        )
    )
    server = ViewportServer((host, port), config, verbose=verbose)
    _write_viewport_pid_file(viewport_pid_file, os.getpid())
    server.signal(
        "server-start",
        url=f"http://{host}:{server.server_port}",
        provider=server.provider_name,
        model=config.model,
        patch_level=config.patch_level,
        workspace=config.workspace,
        pid_file=viewport_pid_file,
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
