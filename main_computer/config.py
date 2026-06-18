from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PATCH_LEVEL = "0.1.0"
DEFAULT_OLLAMA_MODEL = "gemma4:26b"
DEFAULT_OLLAMA_THINK = False
DEFAULT_ENERGY_CHAIN_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_ENERGY_CHAIN_ID = 42424242
DEFAULT_XLAG_CHAIN_ID = 42424242
DEFAULT_HUB_URL = "http://127.0.0.1:8770"
DEFAULT_HUB_ROOT = Path("runtime/hub")
DEFAULT_HUB_NETWORK = "dev"
DEFAULT_HUB_NETWORK_KIND = "dev"
DEFAULT_HUB_BIND_HOST = "127.0.0.1"
DEFAULT_HUB_BIND_PORT = 8770
DEFAULT_HUB_BRIDGE_BACKEND = "dev-chain"
DEFAULT_CHAIN_RPC_URL = DEFAULT_ENERGY_CHAIN_RPC_URL
DEFAULT_CHAIN_ID = DEFAULT_ENERGY_CHAIN_ID
DEFAULT_ONLYOFFICE_MODE = "docker"
DEFAULT_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:18085"
DEFAULT_ONLYOFFICE_INTERNAL_URL = DEFAULT_ONLYOFFICE_PUBLIC_URL
DEFAULT_ONLYOFFICE_DOCUMENT_SERVER_URL = DEFAULT_ONLYOFFICE_PUBLIC_URL
DEFAULT_ONLYOFFICE_BROWSER_PUBLIC_URL: str | None = None
DEFAULT_ONLYOFFICE_STORAGE_ROOT = Path("runtime/onlyoffice/workbooks")
DEFAULT_ONLYOFFICE_JWT_SECRET: str | None = None


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_ollama_think(value: str | None) -> bool | str | None:
    if value is None:
        return DEFAULT_OLLAMA_THINK
    normalized = value.strip().lower()
    if normalized in {"", "none", "null", "default-empty"}:
        return None
    if normalized in {"off", "false", "0", "no"}:
        return False
    if normalized in {"on", "true", "1", "yes"}:
        return True
    if normalized in {"low", "medium", "high"}:
        return normalized
    return DEFAULT_OLLAMA_THINK


def read_patch_level(project_root: Path | None = None) -> str:
    root = project_root or Path.cwd()
    pyproject = root / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                _, value = stripped.split("=", 1)
                return value.strip().strip('"')
    except OSError:
        return DEFAULT_PATCH_LEVEL
    return DEFAULT_PATCH_LEVEL


def _energy_chain_id_from_env() -> tuple[int, str]:
    value = os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_ID")
    if not value:
        return DEFAULT_ENERGY_CHAIN_ID, "default"
    try:
        return int(value, 0), "env"
    except ValueError:
        return DEFAULT_ENERGY_CHAIN_ID, "default-invalid-env"


def _xlag_chain_id_from_env() -> tuple[int, str]:
    value = os.environ.get("MAIN_COMPUTER_XLAG_CHAIN_ID")
    if not value:
        return DEFAULT_XLAG_CHAIN_ID, "default"
    try:
        return int(value, 0), "env"
    except ValueError:
        return DEFAULT_XLAG_CHAIN_ID, "default-invalid-env"


def _env_text(name: str) -> tuple[str | None, str]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None, "default"
    return value.strip(), "env"


def _dev_chain_offices_from_env() -> tuple[dict[str, str | None], ...]:
    offices: list[dict[str, str | None]] = []
    for index in range(4):
        address = os.environ.get(f"MAIN_COMPUTER_DEV_OFFICE_{index}_ADDRESS")
        if not address or not address.strip():
            continue
        offices.append(
            {
                "office": f"O{index}",
                "title": None,
                "address": address.strip(),
            }
        )
    return tuple(offices)


@dataclass(frozen=True)
class MainComputerConfig:
    workspace: Path
    provider: str = "ollama"
    model: str = DEFAULT_OLLAMA_MODEL
    patch_level: str = DEFAULT_PATCH_LEVEL
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_s: float = 600.0
    ollama_think: bool | str | None = DEFAULT_OLLAMA_THINK
    openai_base_url: str | None = None
    ollama_debug_passcode: str | None = None
    energy_admin_passcode: str | None = None
    energy_chain_rpc_url: str | None = DEFAULT_ENERGY_CHAIN_RPC_URL
    energy_chain_id: int | None = DEFAULT_ENERGY_CHAIN_ID
    energy_chain_rpc_url_source: str = "default"
    energy_chain_id_source: str = "default"
    xlag_contract_address: str | None = None
    xlag_contract_address_source: str = "default"
    xlag_chain_id: int = DEFAULT_XLAG_CHAIN_ID
    xlag_chain_id_source: str = "default"
    alpha_beta_lockout_contract_address: str | None = None
    alpha_beta_lockout_contract_address_source: str = "default"
    dev_chain_run_id: str | None = None
    dev_chain_runtime_path: Path | None = None
    dev_chain_runtime_source: str = "missing"
    dev_chain_runtime_error: str | None = None
    dev_chain_offices: tuple[dict[str, str | None], ...] = ()
    hub_url: str = DEFAULT_HUB_URL
    hub_timeout_s: float = 600.0
    hub_client_node_id: str = "main-computer-client"
    hub_high_security: bool = True
    hub_allow_insecure_dev_network: bool = False
    hub_worker_node_id: str = "main-computer-worker"
    hub_worker_endpoint: str | None = None
    hub_credits_per_request: int = 1
    hub_bridge_backend: str = DEFAULT_HUB_BRIDGE_BACKEND
    hub_dev_chain_deployment_path: Path | None = None
    hub_root: Path = DEFAULT_HUB_ROOT
    hub_network: str = DEFAULT_HUB_NETWORK
    hub_network_display_name: str = "Main Computer Local Devnet"
    hub_network_kind: str = DEFAULT_HUB_NETWORK_KIND
    hub_network_config_path: Path | None = None
    hub_ring_config_path: Path | None = None
    hub_bind_host: str = DEFAULT_HUB_BIND_HOST
    hub_bind_port: int = DEFAULT_HUB_BIND_PORT
    chain_rpc_url: str | None = DEFAULT_CHAIN_RPC_URL
    chain_id: int | None = DEFAULT_CHAIN_ID
    chain_rpc_url_source: str = "default"
    chain_id_source: str = "default"
    onlyoffice_enabled: bool = False
    onlyoffice_mode: str = DEFAULT_ONLYOFFICE_MODE
    onlyoffice_public_url: str = DEFAULT_ONLYOFFICE_PUBLIC_URL
    onlyoffice_internal_url: str = DEFAULT_ONLYOFFICE_INTERNAL_URL
    onlyoffice_callback_base_url: str | None = None
    # Browser-facing override used by Docker/Desktop localhost smoke tests.
    # This does not change WSL startup/bridge management.
    onlyoffice_browser_public_url: str | None = DEFAULT_ONLYOFFICE_BROWSER_PUBLIC_URL
    # Backward-compatible alias used by the first ONLYOFFICE app spike.
    onlyoffice_document_server_url: str = DEFAULT_ONLYOFFICE_DOCUMENT_SERVER_URL
    onlyoffice_public_base_url: str | None = None
    onlyoffice_jwt_enabled: bool = True
    onlyoffice_jwt_secret: str | None = DEFAULT_ONLYOFFICE_JWT_SECRET
    onlyoffice_storage_root: Path = DEFAULT_ONLYOFFICE_STORAGE_ROOT
    fallback: bool = False
    install_mode: str = "unleashed"
    mode_label: str = "Unleashed Mode"
    guidance_level: str = "developer"
    safe_mode: bool = False
    executor_enabled: bool = False
    executor_backend: str = "docker"
    executor_image: str = "main-computer-executor:latest"
    executor_wsl_distribution: str = "MainComputerExecutorTest"
    executor_wsl_command: str = "wsl.exe"
    executor_root: Path = Path("runtime/executor")
    executor_timeout_s: float = 120.0
    executor_max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    executor_max_output_chars: int = 128_000
    executor_tool_loop_enabled: bool = True
    rag_docker_enabled: bool = True
    executor_ai_auto_run: bool = False
    executor_ai_allow_network: bool = False
    executor_ai_max_steps: int = 4
    path_mode: str = "local"
    host_os: str = "auto"
    host_drive_root: Path = Path("/host")
    windows_drive_mounts: str = ""
    windows_drive_mounts_file: Path | None = None

    @classmethod
    def from_env(cls) -> "MainComputerConfig":
        workspace_value = os.environ.get("MAIN_COMPUTER_WORKSPACE")
        workspace = Path(workspace_value) if workspace_value else Path.home() / "dsl"
        provider = os.environ.get("MAIN_COMPUTER_PROVIDER", "ollama")
        model = os.environ.get("MAIN_COMPUTER_MODEL")

        if not model:
            if provider == "openai":
                model = os.environ.get("OPENAI_MODEL", "gpt-5.2")
            else:
                model = DEFAULT_OLLAMA_MODEL
        try:
            ollama_timeout_s = float(os.environ.get("MAIN_COMPUTER_OLLAMA_TIMEOUT_S", "600"))
        except ValueError:
            ollama_timeout_s = 600.0
        try:
            executor_timeout_s = float(os.environ.get("MAIN_COMPUTER_EXECUTOR_TIMEOUT_S", "120"))
        except ValueError:
            executor_timeout_s = 120.0
        try:
            executor_max_upload_bytes = int(os.environ.get("MAIN_COMPUTER_EXECUTOR_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))
        except ValueError:
            executor_max_upload_bytes = 2 * 1024 * 1024 * 1024
        try:
            executor_max_output_chars = int(os.environ.get("MAIN_COMPUTER_EXECUTOR_MAX_OUTPUT_CHARS", "128000"))
        except ValueError:
            executor_max_output_chars = 128_000
        try:
            executor_ai_max_steps = int(os.environ.get("MAIN_COMPUTER_EXECUTOR_AI_MAX_STEPS", "4"))
        except ValueError:
            executor_ai_max_steps = 4
        try:
            hub_timeout_s = float(os.environ.get("MAIN_COMPUTER_HUB_TIMEOUT_S", "600"))
        except ValueError:
            hub_timeout_s = 600.0
        try:
            hub_credits_per_request = int(os.environ.get("MAIN_COMPUTER_HUB_CREDITS_PER_REQUEST", "1"))
        except ValueError:
            hub_credits_per_request = 1
        try:
            hub_bind_port = int(os.environ.get("MAIN_COMPUTER_HUB_PORT", str(DEFAULT_HUB_BIND_PORT)))
        except ValueError:
            hub_bind_port = DEFAULT_HUB_BIND_PORT
        energy_chain_rpc_url = os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL") or DEFAULT_ENERGY_CHAIN_RPC_URL
        energy_chain_rpc_url_source = "env" if os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL") else "default"
        energy_chain_id, energy_chain_id_source = _energy_chain_id_from_env()
        chain_rpc_url = os.environ.get("MAIN_COMPUTER_CHAIN_RPC_URL") or energy_chain_rpc_url
        chain_rpc_url_source = "env" if os.environ.get("MAIN_COMPUTER_CHAIN_RPC_URL") else energy_chain_rpc_url_source
        chain_id_env = os.environ.get("MAIN_COMPUTER_CHAIN_ID")
        if chain_id_env:
            try:
                chain_id = int(chain_id_env, 0)
                chain_id_source = "env"
            except ValueError:
                chain_id = energy_chain_id
                chain_id_source = "default-invalid-env"
        else:
            chain_id = energy_chain_id
            chain_id_source = energy_chain_id_source
        xlag_contract_address, xlag_contract_address_source = _env_text("MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS")
        xlag_chain_id, xlag_chain_id_source = _xlag_chain_id_from_env()
        alpha_beta_lockout_contract_address, alpha_beta_lockout_contract_address_source = _env_text(
            "MAIN_COMPUTER_ALPHA_BETA_LOCKOUT_CONTRACT_ADDRESS"
        )
        dev_chain_run_id, _dev_chain_run_id_source = _env_text("MAIN_COMPUTER_DEV_CHAIN_RUN_ID")
        dev_chain_offices = _dev_chain_offices_from_env()

        onlyoffice_mode = (
            os.environ.get("MAIN_COMPUTER_ONLYOFFICE_MODE", DEFAULT_ONLYOFFICE_MODE).strip().lower()
            or DEFAULT_ONLYOFFICE_MODE
        )
        if onlyoffice_mode not in {"docker", "external", "disabled"}:
            onlyoffice_mode = DEFAULT_ONLYOFFICE_MODE

        onlyoffice_jwt_enabled = env_flag(
            "MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED",
            onlyoffice_mode not in {"docker", "external", "disabled"},
        )
        onlyoffice_jwt_secret = os.environ.get("MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET", "").strip() or DEFAULT_ONLYOFFICE_JWT_SECRET
        if not onlyoffice_jwt_enabled:
            onlyoffice_jwt_secret = None

        return cls(
            workspace=workspace,
            provider=provider,
            model=model,
            patch_level=os.environ.get("MAIN_COMPUTER_PATCH_LEVEL", read_patch_level()),
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_timeout_s=max(1.0, ollama_timeout_s),
            ollama_think=parse_ollama_think(os.environ.get("MAIN_COMPUTER_OLLAMA_THINK")),
            openai_base_url=os.environ.get("OPENAI_BASE_URL"),
            ollama_debug_passcode=os.environ.get("MAIN_COMPUTER_OLLAMA_DEBUG_PASSCODE"),
            energy_admin_passcode=os.environ.get("MAIN_COMPUTER_ENERGY_ADMIN_PASSCODE"),
            energy_chain_rpc_url=energy_chain_rpc_url,
            energy_chain_id=energy_chain_id,
            energy_chain_rpc_url_source=energy_chain_rpc_url_source,
            energy_chain_id_source=energy_chain_id_source,
            xlag_contract_address=xlag_contract_address,
            xlag_contract_address_source=xlag_contract_address_source,
            xlag_chain_id=xlag_chain_id,
            xlag_chain_id_source=xlag_chain_id_source,
            alpha_beta_lockout_contract_address=alpha_beta_lockout_contract_address,
            alpha_beta_lockout_contract_address_source=alpha_beta_lockout_contract_address_source,
            dev_chain_run_id=dev_chain_run_id,
            dev_chain_runtime_source="env" if dev_chain_run_id or dev_chain_offices else "missing",
            dev_chain_offices=dev_chain_offices,
            hub_url=os.environ.get("MAIN_COMPUTER_HUB_URL", DEFAULT_HUB_URL),
            hub_timeout_s=max(1.0, hub_timeout_s),
            hub_client_node_id=os.environ.get("MAIN_COMPUTER_HUB_CLIENT_NODE_ID", "main-computer-client"),
            hub_high_security=env_flag("MAIN_COMPUTER_HUB_HIGH_SECURITY", True),
            hub_allow_insecure_dev_network=env_flag("MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK", False),
            hub_worker_node_id=os.environ.get("MAIN_COMPUTER_HUB_WORKER_NODE_ID", "main-computer-worker"),
            hub_worker_endpoint=os.environ.get("MAIN_COMPUTER_HUB_WORKER_ENDPOINT") or None,
            hub_credits_per_request=max(1, hub_credits_per_request),
            hub_bridge_backend=os.environ.get("MAIN_COMPUTER_HUB_BRIDGE_BACKEND", DEFAULT_HUB_BRIDGE_BACKEND).strip().lower() or DEFAULT_HUB_BRIDGE_BACKEND,
            hub_dev_chain_deployment_path=Path(os.environ["MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH"])
            if os.environ.get("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH")
            else None,
            hub_root=Path(os.environ.get("MAIN_COMPUTER_HUB_ROOT", str(DEFAULT_HUB_ROOT))),
            hub_network=os.environ.get("MAIN_COMPUTER_HUB_NETWORK", DEFAULT_HUB_NETWORK).strip() or DEFAULT_HUB_NETWORK,
            hub_network_display_name=os.environ.get("MAIN_COMPUTER_HUB_NETWORK_DISPLAY_NAME", "Main Computer Local Devnet").strip() or "Main Computer Local Devnet",
            hub_network_kind=os.environ.get("MAIN_COMPUTER_HUB_NETWORK_KIND", DEFAULT_HUB_NETWORK_KIND).strip() or DEFAULT_HUB_NETWORK_KIND,
            hub_network_config_path=Path(os.environ["MAIN_COMPUTER_HUB_NETWORKS_FILE"])
            if os.environ.get("MAIN_COMPUTER_HUB_NETWORKS_FILE")
            else None,
            hub_ring_config_path=Path(os.environ["MAIN_COMPUTER_HUB_RING_CONFIG_PATH"])
            if os.environ.get("MAIN_COMPUTER_HUB_RING_CONFIG_PATH")
            else None,
            hub_bind_host=os.environ.get("MAIN_COMPUTER_HUB_HOST", DEFAULT_HUB_BIND_HOST).strip() or DEFAULT_HUB_BIND_HOST,
            hub_bind_port=max(1, min(65535, hub_bind_port)),
            chain_rpc_url=chain_rpc_url,
            chain_id=chain_id,
            chain_rpc_url_source=chain_rpc_url_source,
            chain_id_source=chain_id_source,
            onlyoffice_enabled=env_flag("MAIN_COMPUTER_ONLYOFFICE_ENABLED"),
            onlyoffice_mode=onlyoffice_mode,
            onlyoffice_public_url=(
                os.environ.get("MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL", "").strip().rstrip("/")
                or os.environ.get("MAIN_COMPUTER_ONLYOFFICE_DOCUMENT_SERVER_URL", "").strip().rstrip("/")
                or DEFAULT_ONLYOFFICE_PUBLIC_URL
            ),
            onlyoffice_internal_url=(
                os.environ.get("MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL", "").strip().rstrip("/")
                or os.environ.get("MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL", "").strip().rstrip("/")
                or os.environ.get("MAIN_COMPUTER_ONLYOFFICE_DOCUMENT_SERVER_URL", "").strip().rstrip("/")
                or DEFAULT_ONLYOFFICE_INTERNAL_URL
            ),
            onlyoffice_callback_base_url=(
                os.environ.get("MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL", "").strip().rstrip("/")
                or os.environ.get("MAIN_COMPUTER_ONLYOFFICE_PUBLIC_BASE_URL", "").strip().rstrip("/")
                or None
            ),
            onlyoffice_browser_public_url=(
                os.environ.get("MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL", "").strip().rstrip("/")
                or os.environ.get("MAIN_COMPUTER_ONLYOFFICE_TWIDDLE_PUBLIC_URL", "").strip().rstrip("/")
                or DEFAULT_ONLYOFFICE_BROWSER_PUBLIC_URL
            ),
            onlyoffice_document_server_url=(
                os.environ.get("MAIN_COMPUTER_ONLYOFFICE_DOCUMENT_SERVER_URL", "").strip().rstrip("/")
                or os.environ.get("MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL", "").strip().rstrip("/")
                or DEFAULT_ONLYOFFICE_DOCUMENT_SERVER_URL
            ),
            onlyoffice_public_base_url=(
                os.environ.get("MAIN_COMPUTER_ONLYOFFICE_PUBLIC_BASE_URL", "").strip().rstrip("/") or None
            ),
            onlyoffice_jwt_enabled=onlyoffice_jwt_enabled,
            onlyoffice_jwt_secret=onlyoffice_jwt_secret,
            onlyoffice_storage_root=Path(
                os.environ.get("MAIN_COMPUTER_ONLYOFFICE_STORAGE_ROOT", str(DEFAULT_ONLYOFFICE_STORAGE_ROOT))
            ),
            fallback=env_flag("MAIN_COMPUTER_FALLBACK"),
            install_mode=(os.environ.get("MAIN_COMPUTER_INSTALL_MODE", "unleashed").strip().lower() or "unleashed"),
            mode_label=(os.environ.get("MAIN_COMPUTER_MODE_LABEL", "Unleashed Mode").strip() or "Unleashed Mode"),
            guidance_level=(os.environ.get("MAIN_COMPUTER_GUIDANCE_LEVEL", "developer").strip().lower() or "developer"),
            safe_mode=env_flag("MAIN_COMPUTER_SAFE_MODE"),
            executor_enabled=env_flag("MAIN_COMPUTER_EXECUTOR_ENABLED", True),
            executor_backend=os.environ.get("MAIN_COMPUTER_EXECUTOR_BACKEND", "docker").strip().lower() or "docker",
            executor_image=os.environ.get("MAIN_COMPUTER_EXECUTOR_IMAGE", "main-computer-executor:latest"),
            executor_wsl_distribution=os.environ.get("MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION", "MainComputerExecutorTest").strip() or "MainComputerExecutorTest",
            executor_wsl_command=os.environ.get("MAIN_COMPUTER_EXECUTOR_WSL_COMMAND", "wsl.exe").strip() or "wsl.exe",
            executor_root=Path(os.environ.get("MAIN_COMPUTER_EXECUTOR_ROOT", "runtime/executor")),
            executor_timeout_s=max(1.0, executor_timeout_s),
            executor_max_upload_bytes=max(1, executor_max_upload_bytes),
            executor_max_output_chars=max(1000, executor_max_output_chars),
            executor_tool_loop_enabled=env_flag("MAIN_COMPUTER_EXECUTOR_TOOL_LOOP_ENABLED", True),
            rag_docker_enabled=env_flag("MAIN_COMPUTER_RAG_DOCKER_ENABLED", True),
            executor_ai_auto_run=env_flag("MAIN_COMPUTER_EXECUTOR_AI_AUTO_RUN"),
            executor_ai_allow_network=env_flag("MAIN_COMPUTER_EXECUTOR_AI_ALLOW_NETWORK"),
            executor_ai_max_steps=max(1, min(12, executor_ai_max_steps)),
            path_mode=os.environ.get("MAIN_COMPUTER_PATH_MODE", "local").strip().lower() or "local",
            host_os=os.environ.get("MAIN_COMPUTER_HOST_OS", "auto").strip().lower() or "auto",
            host_drive_root=Path(os.environ.get("MAIN_COMPUTER_HOST_DRIVE_ROOT", "/host")),
            windows_drive_mounts=os.environ.get("MAIN_COMPUTER_WINDOWS_DRIVE_MOUNTS", ""),
            windows_drive_mounts_file=Path(os.environ["MAIN_COMPUTER_WINDOWS_DRIVE_MOUNTS_FILE"])
            if os.environ.get("MAIN_COMPUTER_WINDOWS_DRIVE_MOUNTS_FILE")
            else None,
        )
