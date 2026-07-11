from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import time
from dataclasses import replace
from pathlib import Path

from main_computer.catalog import ProjectCatalog
from main_computer.config import MainComputerConfig
from main_computer.conductor import ConductorService
from main_computer.diagnostics import LEVELS as DIAGNOSTIC_LEVELS
from main_computer.diagnostics import run_from_args as run_diagnostics_from_args
from main_computer.harness import run_from_args as run_harness_from_args
from main_computer.log_rotator import run_from_args as run_log_rotator_from_args
from main_computer.log_metric_distribution import add_arguments as add_log_metric_arguments
from main_computer.log_metric_distribution import run_from_args as run_log_metric_distribution_from_args
from main_computer.heartbeat import HeartbeatConfig, serve as serve_heartbeat
from main_computer.hub import DEFAULT_HUB_PORT, DEFAULT_HUB_WORKER_PORT, register_worker_with_hub, serve_hub, serve_hub_worker, serve_hub_worker_pull
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_networks import (
    HubNetworkConfigError,
    env_chain_id_override,
    env_chain_rpc_url_override,
    env_hub_host_override,
    env_hub_network_name,
    env_hub_port_override,
    env_hub_public_url_override,
    env_hub_runtime_dir_override,
    load_hub_network_registry,
    resolve_profile_runtime_defaults,
)
from main_computer import openclaw_ops_smoke
from main_computer.captain_cli import CaptainCliError, run_captain
from main_computer.openclaw_bridge import DEFAULT_OPENCLAW_BRIDGE_PORT, serve as serve_openclaw_bridge
from main_computer.recurrent_thinking import run_from_args as run_recurrent_thinking_from_args
from main_computer.static_code_analyzer import emit_report as emit_code_stats_report
from main_computer.static_code_analyzer import add_arguments as add_code_stats_arguments
from main_computer.static_code_analyzer import run_from_args as run_code_stats_from_args
from main_computer.router import MainComputer
from main_computer.models import ChatResponse
from main_computer.viewport import serve


def _config_from_args(args: argparse.Namespace) -> MainComputerConfig:
    base = MainComputerConfig.from_env()
    config = MainComputerConfig(
        workspace=Path(args.workspace) if args.workspace else base.workspace,
        provider=args.provider or base.provider,
        model=args.model or base.model,
        patch_level=base.patch_level,
        ollama_base_url=args.ollama_base_url or base.ollama_base_url,
        ollama_timeout_s=args.ollama_timeout_s if args.ollama_timeout_s is not None else base.ollama_timeout_s,
        ollama_think=base.ollama_think,
        openai_base_url=args.openai_base_url or base.openai_base_url,
        ollama_debug_passcode=base.ollama_debug_passcode,
        energy_admin_passcode=base.energy_admin_passcode,
        energy_chain_rpc_url=base.energy_chain_rpc_url,
        energy_chain_id=base.energy_chain_id,
        energy_chain_rpc_url_source=base.energy_chain_rpc_url_source,
        energy_chain_id_source=base.energy_chain_id_source,
        chain_rpc_url=base.chain_rpc_url,
        chain_id=base.chain_id,
        chain_rpc_url_source=base.chain_rpc_url_source,
        chain_id_source=base.chain_id_source,
        xlag_contract_address=base.xlag_contract_address,
        xlag_contract_address_source=base.xlag_contract_address_source,
        xlag_chain_id=base.xlag_chain_id,
        xlag_chain_id_source=base.xlag_chain_id_source,
        alpha_beta_lockout_contract_address=base.alpha_beta_lockout_contract_address,
        alpha_beta_lockout_contract_address_source=base.alpha_beta_lockout_contract_address_source,
        dev_chain_run_id=base.dev_chain_run_id,
        dev_chain_runtime_path=base.dev_chain_runtime_path,
        dev_chain_runtime_source=base.dev_chain_runtime_source,
        dev_chain_runtime_error=base.dev_chain_runtime_error,
        dev_chain_offices=base.dev_chain_offices,
        hub_url=getattr(args, "hub_url", None) or base.hub_url,
        hub_timeout_s=getattr(args, "hub_timeout_s", None) if getattr(args, "hub_timeout_s", None) is not None else base.hub_timeout_s,
        hub_client_node_id=getattr(args, "hub_client_node_id", None) or base.hub_client_node_id,
        hub_high_security=base.hub_high_security,
        hub_allow_insecure_dev_network=base.hub_allow_insecure_dev_network,
        hub_worker_node_id=getattr(args, "hub_worker_node_id", None) or base.hub_worker_node_id,
        hub_worker_endpoint=getattr(args, "hub_worker_endpoint", None) or base.hub_worker_endpoint,
        hub_credits_per_request=getattr(args, "hub_credits_per_request", None) if getattr(args, "hub_credits_per_request", None) is not None else base.hub_credits_per_request,
        hub_bridge_backend=str(getattr(args, "bridge_backend", None) or base.hub_bridge_backend).strip().lower() or base.hub_bridge_backend,
        hub_dev_chain_deployment_path=Path(getattr(args, "dev_chain_deployment_path")) if getattr(args, "dev_chain_deployment_path", None) else base.hub_dev_chain_deployment_path,
        hub_contracts_path=Path(getattr(args, "contracts_path")) if getattr(args, "contracts_path", None) else base.hub_contracts_path,
        hub_allow_missing_bridge_signer=bool(getattr(args, "allow_missing_bridge_signer", False) or base.hub_allow_missing_bridge_signer),
        hub_enable_smoke_bridge=bool(getattr(args, "enable_smoke_bridge", False) or base.hub_enable_smoke_bridge),
        hub_root=getattr(args, "hub_root", None) or base.hub_root,
        hub_network=base.hub_network,
        hub_network_display_name=base.hub_network_display_name,
        hub_network_kind=base.hub_network_kind,
        hub_network_config_path=base.hub_network_config_path,
        hub_bind_host=base.hub_bind_host,
        hub_bind_port=base.hub_bind_port,
        fallback=bool(getattr(args, "fallback", False) or base.fallback),
        install_mode=base.install_mode,
        mode_label=base.mode_label,
        guidance_level=base.guidance_level,
        safe_mode=base.safe_mode,
        executor_enabled=base.executor_enabled,
        executor_backend=base.executor_backend,
        executor_image=base.executor_image,
        executor_wsl_distribution=base.executor_wsl_distribution,
        executor_wsl_command=base.executor_wsl_command,
        executor_root=base.executor_root,
        executor_timeout_s=base.executor_timeout_s,
        executor_max_upload_bytes=base.executor_max_upload_bytes,
        executor_max_output_chars=base.executor_max_output_chars,
        executor_tool_loop_enabled=base.executor_tool_loop_enabled,
        rag_docker_enabled=base.rag_docker_enabled,
        executor_ai_auto_run=base.executor_ai_auto_run,
        executor_ai_allow_network=base.executor_ai_allow_network,
        executor_ai_max_steps=base.executor_ai_max_steps,
        path_mode=base.path_mode,
        host_os=base.host_os,
        host_drive_root=base.host_drive_root,
        windows_drive_mounts=base.windows_drive_mounts,
        windows_drive_mounts_file=base.windows_drive_mounts_file,
        onlyoffice_enabled=base.onlyoffice_enabled,
        onlyoffice_mode=base.onlyoffice_mode,
        onlyoffice_public_url=base.onlyoffice_public_url,
        onlyoffice_internal_url=base.onlyoffice_internal_url,
        onlyoffice_callback_base_url=base.onlyoffice_callback_base_url,
        onlyoffice_browser_public_url=base.onlyoffice_browser_public_url,
        onlyoffice_document_server_url=base.onlyoffice_document_server_url,
        onlyoffice_public_base_url=base.onlyoffice_public_base_url,
        onlyoffice_jwt_enabled=base.onlyoffice_jwt_enabled,
        onlyoffice_jwt_secret=base.onlyoffice_jwt_secret,
        onlyoffice_storage_root=base.onlyoffice_storage_root,
    )

    should_resolve_hub_network = bool(
        getattr(args, "use_hub_network_defaults", False)
        or getattr(args, "network", None)
        or env_hub_network_name()
    )
    if not should_resolve_hub_network:
        return config

    registry = load_hub_network_registry(getattr(args, "network_config", None))
    selected_network = getattr(args, "network", None) or env_hub_network_name() or registry.default_network
    profile = registry.get(selected_network)

    env_runtime_dir = env_hub_runtime_dir_override()
    if env_runtime_dir is None and os.environ.get("MAIN_COMPUTER_HUB_ROOT"):
        env_runtime_dir = base.hub_root
    env_chain_rpc_url = env_chain_rpc_url_override()
    if env_chain_rpc_url is None and base.energy_chain_rpc_url_source == "env":
        env_chain_rpc_url = base.energy_chain_rpc_url
    env_chain_id = env_chain_id_override()
    if env_chain_id is None and base.energy_chain_id_source == "env":
        env_chain_id = base.energy_chain_id

    profile = profile.with_overrides(
        hub_bind_host=env_hub_host_override(),
        hub_bind_port=env_hub_port_override(),
        hub_public_url=env_hub_public_url_override(),
        hub_runtime_dir=env_runtime_dir,
        chain_rpc_url=env_chain_rpc_url,
        chain_id=env_chain_id if env_chain_id is not None else profile.chain_id,
    )
    profile = profile.with_overrides(
        hub_bind_host=getattr(args, "host", None),
        hub_bind_port=getattr(args, "port", None),
        hub_public_url=getattr(args, "hub_url", None),
        hub_runtime_dir=getattr(args, "hub_runtime_dir", None) or getattr(args, "hub_root", None),
        chain_rpc_url=getattr(args, "chain_rpc_url", None),
        chain_id=getattr(args, "chain_id", None) if getattr(args, "chain_id", None) is not None else profile.chain_id,
    )
    profile = resolve_profile_runtime_defaults(profile)
    profile.validate_runnable()

    source = f"hub-network:{profile.network_key}"
    bridge_backend = str(config.hub_bridge_backend or "dev-chain").strip().lower() or "dev-chain"
    dev_chain_deployment_path = config.hub_dev_chain_deployment_path
    if dev_chain_deployment_path is None and bridge_backend not in {"mock", "mock-chain", "mock-chain-lite"}:
        dev_chain_deployment_path = profile.deployment_manifest_path
    return replace(
        config,
        hub_network=profile.network_key,
        hub_network_display_name=profile.display_name,
        hub_network_kind=profile.kind,
        hub_network_config_path=registry.source_path,
        hub_bind_host=profile.hub_bind_host,
        hub_bind_port=profile.hub_bind_port,
        hub_root=profile.hub_runtime_dir,
        hub_url=profile.hub_url,
        hub_bridge_backend=bridge_backend,
        hub_dev_chain_deployment_path=dev_chain_deployment_path,
        energy_chain_rpc_url=profile.chain_rpc_url,
        energy_chain_id=profile.chain_id,
        energy_chain_rpc_url_source=source,
        energy_chain_id_source=source,
        chain_rpc_url=profile.chain_rpc_url,
        chain_id=profile.chain_id,
        chain_rpc_url_source=source,
        chain_id_source=source,
    )

def cmd_chat(args: argparse.Namespace) -> int:
    computer = MainComputer.build(_config_from_args(args))
    response = computer.chat(args.prompt)
    print(response.content)
    return 0


_CAPTAIN_ENGAGE_COMPUTER_PHRASE = ("smoke", "john", "luc", "picard", "engage", "computer")


def _split_captain_free_and_option_tokens(argv: list[str]) -> tuple[tuple[str, ...], list[str]]:
    free_tokens: list[str] = []
    option_tokens: list[str] = []
    in_options = False
    for token in argv:
        text = str(token)
        if not in_options and text.startswith("-"):
            in_options = True
        if in_options:
            option_tokens.append(text)
        else:
            stripped = text.strip()
            if stripped:
                free_tokens.append(stripped.lower())
    return tuple(free_tokens), option_tokens


def _build_captain_engage_options_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main-computer captain smoke john luc picard engage computer",
        description="Register the local AI as a priced ring-3 worker-pull worker for captain smoke requests.",
    )
    parser.add_argument("--hub-url", default="", help="Hub base URL. Defaults to the captain smoke mainnet hub when available.")
    parser.add_argument("--model", default="", help="Model id to advertise. Defaults to the configured local model.")
    parser.add_argument("--public-endpoint", default="", help="Optional public worker endpoint to advertise. Worker-pull mode does not require inbound access.")
    parser.add_argument("--ring", type=int, default=3, help="Worker ring to advertise. Defaults to 3.")
    parser.add_argument("--poll-interval-s", type=float, default=2.0, help="Seconds between empty worker-pull polls.")
    parser.add_argument("--heartbeat-interval-s", type=float, default=30.0, help="Seconds between worker availability heartbeats.")
    parser.add_argument("--lease-seconds", type=float, default=None, help="Optional requested lease duration for worker-pull work.")
    parser.add_argument("--max-requests", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "-noverbose",
        "--noverbose",
        dest="verbose",
        action="store_false",
        default=True,
        help="Suppress worker-pull status output.",
    )
    return parser


def _captain_smoke_hub_url(config: MainComputerConfig, explicit_hub_url: str = "") -> str:
    if str(explicit_hub_url or "").strip():
        return str(explicit_hub_url).strip().rstrip("/")
    try:
        profile = load_hub_network_registry().get("mainnet")
    except (HubNetworkConfigError, FileNotFoundError):
        profile = None
    if profile is not None and str(profile.hub_url or "").strip():
        return str(profile.hub_url).strip().rstrip("/")
    return str(config.hub_url).strip().rstrip("/")




def _worker_pull_chat_with_command_ring_metadata(
    chat_fn,
    *,
    assigned_ring: int,
    command_identity: str,
    worker_node_id: str = "",
    worker_instance_id: str = "",
):
    """Wrap a local chat provider so worker-pull answers carry command-layer ring proof.

    The Hub remains a generic router. The command that engages the local worker
    knows which ring it offered, so it stamps that ring into the response metadata
    that the requester can verify after pickup.
    """

    ring = int(assigned_ring)

    def wrapped(messages):
        response = chat_fn(messages)
        metadata = dict(response.metadata) if isinstance(response.metadata, dict) else {}
        metadata.setdefault("worker_pull_v0", True)
        metadata.setdefault("worker_pull_answer_contract", "command_layer_ring_answer_v1")
        metadata.setdefault("command_identity", command_identity)
        metadata.setdefault("worker_assigned_ring", ring)
        metadata.setdefault("answering_ring", ring)
        metadata.setdefault("effective_ring", ring)
        metadata.setdefault("assigned_ring", ring)
        metadata.setdefault("ring", ring)
        metadata.setdefault("requested_ring", ring)
        metadata.setdefault("required_ring", ring)
        metadata.setdefault("ring3_answer_verified", ring == 3)
        if worker_node_id:
            metadata.setdefault("worker_node_id", worker_node_id)
        if worker_instance_id:
            metadata.setdefault("worker_instance_id", worker_instance_id)
        return ChatResponse(
            content=response.content,
            provider=response.provider,
            model=response.model,
            metadata=metadata,
        )

    return wrapped


def _run_captain_engage_computer(args: argparse.Namespace, option_tokens: list[str]) -> int:
    engage_options = _build_captain_engage_options_parser().parse_args(option_tokens)
    worker_config = _config_from_args(args)
    if worker_config.provider == "hub":
        raise RuntimeError("captain smoke engage computer cannot use provider=hub because that would recurse back into the hub.")

    hub_url = _captain_smoke_hub_url(worker_config, engage_options.hub_url or getattr(args, "hub_url", "") or "")
    worker_config = replace(
        worker_config,
        hub_url=hub_url,
        model=str(engage_options.model or worker_config.model).strip() or worker_config.model,
    )
    worker = MainComputer.build(worker_config)
    if engage_options.verbose:
        print("Engaging local Main Computer AI as a ring 3 worker-pull worker.")
        print(f"Hub URL: {hub_url}")
        print(f"Worker node: {worker_config.hub_worker_node_id}")
        print(f"Model: {worker_config.model}")
        print("Run the captain smoke make-it-so request in another window while this worker stays open.")
    try:
        serve_hub_worker_pull(
            worker_config,
            _worker_pull_chat_with_command_ring_metadata(
                worker.provider.chat,
                assigned_ring=engage_options.ring,
                command_identity="captain-engage-computer",
                worker_node_id=worker_config.hub_worker_node_id,
                worker_instance_id=worker_config.hub_worker_node_id,
            ),
            hub_url=hub_url,
            public_endpoint=engage_options.public_endpoint or worker_config.hub_worker_endpoint,
            assigned_ring=engage_options.ring,
            poll_interval_s=engage_options.poll_interval_s,
            heartbeat_interval_s=engage_options.heartbeat_interval_s,
            lease_seconds=engage_options.lease_seconds,
            verbose=engage_options.verbose,
            max_requests=engage_options.max_requests,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0



_DATA_DEFAULT_OFFICER_SELECTOR = "o3"
_DATA_AGENT_MAINNET_NETWORK = "mainnet"
_DATA_AGENT_CLIENT_NODE_ID = "main-computer-data-agent-cli"
_DATA_ENGAGE_COMPUTER_PHRASE = ("engage", "computer")


def _data_mainnet_hub_url(explicit_hub_url: str = "", args: argparse.Namespace | None = None) -> str:
    """Resolve Data's agent Hub URL from explicit input, CLI config, network profile, or env.

    Data agent commands should not require operators to restate the mainnet Hub URL.
    The override order intentionally mirrors Captain smoke defaults.
    """

    explicit = str(explicit_hub_url or "").strip()
    if explicit:
        return explicit.rstrip("/")
    arg_hub_url = str(getattr(args, "hub_url", "") or "").strip() if args is not None else ""
    if arg_hub_url:
        return arg_hub_url.rstrip("/")
    try:
        config = _config_from_args(args) if args is not None else MainComputerConfig.from_env()
        config_hub_url = str(getattr(config, "hub_url", "") or "").strip()
    except Exception:
        config_hub_url = ""
    try:
        profile = load_hub_network_registry().get(_DATA_AGENT_MAINNET_NETWORK)
        if profile is not None and str(profile.hub_url).strip():
            return str(profile.hub_url).strip().rstrip("/")
    except (HubNetworkConfigError, FileNotFoundError, KeyError):
        pass
    if config_hub_url:
        return config_hub_url.rstrip("/")
    return "https://mainnet-hub.greatlibrary.io"


def _data_agent_model(args: argparse.Namespace | None = None, explicit_model: str = "") -> str:
    """Resolve Data's agent model from an explicit option or local Main Computer config.

    This keeps `data engage computer --agent` and `data ... --god-mode --agent`
    on the same inferred model without requiring `--model gemma4:26b`.
    """

    explicit = str(explicit_model or "").strip()
    if explicit:
        return explicit
    arg_model = str(getattr(args, "model", "") or "").strip() if args is not None else ""
    if arg_model:
        return arg_model
    try:
        config = _config_from_args(args) if args is not None else MainComputerConfig.from_env()
        config_model = str(getattr(config, "model", "") or "").strip()
    except Exception:
        config_model = ""
    return config_model




def _data_repo_root(args: argparse.Namespace | None = None) -> Path:
    """Return the repo/workspace root used for command-side deployment lookup."""

    workspace = str(getattr(args, "workspace", "") or "").strip() if args is not None else ""
    if workspace:
        return Path(workspace)
    return Path.cwd()


def _data_o3_wallet_address(args: argparse.Namespace | None = None, explicit_wallet_address: str = "") -> str:
    """Resolve Data/O3's wallet address from explicit input, env, or deployment state.

    Hub credit accounting is wallet/account based. Data's command identity is O3,
    so Data agent requests should spend O3's Hub account rather than the CLI node id.
    """

    explicit = str(explicit_wallet_address or "").strip()
    if explicit:
        return explicit
    for env_name in ("MAIN_COMPUTER_DATA_O3_WALLET_ADDRESS", "MAIN_COMPUTER_O3_WALLET_ADDRESS"):
        value = str(os.environ.get(env_name, "") or "").strip()
        if value:
            return value

    repo_root = _data_repo_root(args)
    candidates = [
        repo_root / "runtime" / "deployments" / _DATA_AGENT_MAINNET_NETWORK / "latest.json",
        Path("runtime") / "deployments" / _DATA_AGENT_MAINNET_NETWORK / "latest.json",
    ]
    for path in candidates:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        offices = payload.get("offices") if isinstance(payload, dict) else None
        if not isinstance(offices, list):
            continue
        fallback_address = ""
        for index, office in enumerate(offices):
            if not isinstance(office, dict):
                continue
            address = str(office.get("address") or "").strip()
            office_name = str(office.get("office") or "").strip().lower()
            if index == 3 and address:
                fallback_address = address
            if office_name == "o3" and address:
                return address
        if fallback_address:
            return fallback_address
    return ""


def _data_o3_hub_account_id(
    args: argparse.Namespace | None = None,
    explicit_account_id: str = "",
    *,
    explicit_wallet_address: str = "",
) -> str:
    """Resolve the Hub account id that Data/O3 should spend from."""

    explicit = str(explicit_account_id or "").strip()
    if explicit:
        return explicit
    env_account = str(os.environ.get("MAIN_COMPUTER_DATA_O3_HUB_ACCOUNT_ID", "") or "").strip()
    if env_account:
        return env_account
    wallet_address = _data_o3_wallet_address(args, explicit_wallet_address=explicit_wallet_address)
    if not wallet_address:
        return ""
    return wallet_account_id(wallet_address)


def _data_god_mode_option_values(data_args: list[str]) -> argparse.Namespace:
    """Parse Data god-mode options without interpreting the prompt."""

    _free_tokens, option_tokens = _split_data_free_and_option_tokens(data_args)
    parser = _build_data_god_mode_options_parser()
    options, unknown = parser.parse_known_args(option_tokens)
    if unknown:
        parser.error(f"unsupported --god-mode option(s): {' '.join(unknown)}")
    return options


def _data_smoke_arg_value(smoke_argv: list[str], option: str, default: str = "") -> str:
    if option in smoke_argv:
        idx = smoke_argv.index(option)
        if idx + 1 < len(smoke_argv):
            return str(smoke_argv[idx + 1])
    return default


def _data_smoke_has_flag(smoke_argv: list[str], option: str) -> bool:
    return option in smoke_argv


def _data_god_mode_worker_reviewer_counts(smoke_argv: list[str]) -> tuple[int, int]:
    worker = int(_data_smoke_arg_value(smoke_argv, "--real-agent-worker-count", "4") or "4")
    reviewer = int(_data_smoke_arg_value(smoke_argv, "--real-agent-reviewer-count", "4") or "4")
    return max(4, worker), max(4, reviewer)


def _data_god_mode_auto_bridge_credits(smoke_argv: list[str]) -> int:
    """Return a safe automatic bridge-credit floor for a live Data agent run.

    Data cannot know before the Byzantine action boundary whether the prompt will
    stop at answer_only or continue into planning/editor/retry phases.  The auto
    amount therefore funds the largest reference path Data can exercise:
    action, planning, editor, and retry, each with worker+reviewer fanout.
    """

    worker_count, reviewer_count = _data_god_mode_worker_reviewer_counts(smoke_argv)
    phase_count = 4
    return max(1, phase_count * (worker_count + reviewer_count))


def _data_credit_wei_from_balance(balance: object) -> int:
    if not isinstance(balance, dict):
        return 0
    account = balance.get("account") if isinstance(balance.get("account"), dict) else balance
    if not isinstance(account, dict):
        return 0
    for key in (
        "available_credit_wei",
        "available_credits_wei",
        "available_credit_units",
        "available_credits_units",
    ):
        value = account.get(key)
        if value not in (None, ""):
            try:
                return max(0, int(str(value)))
            except ValueError:
                pass
    for key in ("available_credits", "available"):
        value = account.get(key)
        if value not in (None, ""):
            try:
                return max(0, int(float(str(value))))
            except ValueError:
                pass
    return 0


def _data_god_mode_is_live_agent_worker_pull(smoke_argv: list[str]) -> bool:
    provider = _data_smoke_arg_value(smoke_argv, "--ai-provider")
    transport = _data_smoke_arg_value(smoke_argv, "--ai-hub-transport")
    return (
        provider == "hub"
        and transport.replace("-", "_") in {"worker_pull", "worker_pull_v0"}
        and not _data_smoke_has_flag(smoke_argv, "--scripted-ai-smoke")
    )


def _data_god_mode_bridge_prefund(
    args: argparse.Namespace,
    data_args: list[str],
    smoke_argv: list[str],
) -> dict[str, object] | None:
    """Fund Data/O3's Hub account before live worker-pull AI calls.

    This mirrors Captain's command-layer bridge funding flow, but it does not
    teach the Hub anything about Data, O3, Captain, or god-mode.  The Hub only
    sees a normal wallet-funded account and generic worker-pull requests.
    """

    if not _data_god_mode_is_live_agent_worker_pull(smoke_argv):
        return None

    options = _data_god_mode_option_values(data_args)
    if bool(getattr(options, "no_bridge", False)):
        return {
            "enabled": False,
            "skipped": True,
            "reason": "--no-bridge",
            "account_id": _data_smoke_arg_value(smoke_argv, "--ai-hub-account-id"),
        }

    from main_computer.captain_cli import (
        _apply_captain_live_defaults,
        _bridge_credit_wei,
        _fetch_hub_credit_balance,
        _is_missing_bridge_completion_metadata_error,
        _post_hub_json,
        build_bridge_wallet_funding_import_payload,
        build_captain_options_parser,
        build_captain_runtime,
        normalize_smoke_id,
        send_captain_bridge_deposit,
    )
    from main_computer.credit_units import credit_wei_to_decimal_text

    hub_url = _data_smoke_arg_value(smoke_argv, "--ai-hub-url") or _data_mainnet_hub_url(args=args)
    model = _data_smoke_arg_value(smoke_argv, "--ai-model") or _data_agent_model(args)
    client_node_id = _data_smoke_arg_value(smoke_argv, "--ai-hub-client-node-id") or _DATA_AGENT_CLIENT_NODE_ID
    account_id = _data_smoke_arg_value(smoke_argv, "--ai-hub-account-id")
    wallet_address = _data_o3_wallet_address(
        args,
        explicit_wallet_address=str(getattr(options, "ai_hub_wallet_address", "") or "").strip(),
    )
    selector = wallet_address or _DATA_DEFAULT_OFFICER_SELECTOR

    auto_credits = _data_god_mode_auto_bridge_credits(smoke_argv)
    bridge_credits_text = str(getattr(options, "bridge_credits", "") or "auto").strip()
    bridge_credits = str(auto_credits if bridge_credits_text.lower() in {"", "auto"} else bridge_credits_text)
    bridge_credit_wei = _bridge_credit_wei(bridge_credits)

    base_config = _config_from_args(args)
    captain_parser = build_captain_options_parser()
    captain_options = captain_parser.parse_args(
        [
            "--officer",
            selector,
            "--network",
            _DATA_AGENT_MAINNET_NETWORK,
            "--hub-url",
            hub_url,
            "--client-node-id",
            client_node_id,
            "--model",
            model,
            "--bridge-credits",
            bridge_credits,
            "--no-chain",
            "--poll-seconds",
            "0",
        ]
    )
    _apply_captain_live_defaults(captain_options, base_config=base_config, cwd=_data_repo_root(args))
    runtime = build_captain_runtime(
        base_config,
        options=captain_options,
        selector=selector,
        cwd=_data_repo_root(args),
    )
    resolved_account_id = wallet_account_id(runtime.wallet.address)
    if account_id and resolved_account_id.lower() != account_id.lower():
        raise CaptainCliError(
            "Data/O3 bridge funding resolved a different wallet account than the worker-pull request. "
            f"bridge_account={resolved_account_id} request_account={account_id}"
        )

    try:
        balance_start = _fetch_hub_credit_balance(
            runtime.config.hub_url,
            wallet_address=runtime.wallet.address,
            timeout_s=float(getattr(options, "ai_timeout_seconds", 300.0) or 300.0),
        )
    except Exception:
        balance_start = {}
    available_wei = _data_credit_wei_from_balance(balance_start)
    if available_wei >= bridge_credit_wei:
        return {
            "enabled": True,
            "skipped": True,
            "reason": "account_already_funded",
            "hub_url": runtime.config.hub_url,
            "wallet_address": runtime.wallet.address,
            "account_id": resolved_account_id,
            "bridge_credit_wei": str(bridge_credit_wei),
            "bridge_credits_display": credit_wei_to_decimal_text(bridge_credit_wei),
            "available_credit_wei": str(available_wei),
            "available_credits_display": credit_wei_to_decimal_text(available_wei),
        }

    prompt = _data_smoke_arg_value(smoke_argv, "--real-agent-prompt")
    smoke_id = normalize_smoke_id(
        f"main-computer data god-mode bridge:{runtime.wallet.address}:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()}:{time.time_ns()}"
    )
    timeout_s = float(getattr(options, "ai_timeout_seconds", 300.0) or 300.0)
    deposit = send_captain_bridge_deposit(
        runtime,
        deposit_credit_wei=bridge_credit_wei,
        smoke_id=smoke_id,
        timeout_s=timeout_s,
    )
    completion_payload = {
        "deposit_id": deposit["deposit_id"],
        "wallet_address": runtime.wallet.address,
        "tx_hash": deposit["transaction_hash"],
        "contract_address": runtime.bridge_escrow_address,
        "chain_id": runtime.chain_id,
    }
    try:
        completion = _post_hub_json(
            runtime.config.hub_url,
            "/api/hub/v1/credits/wallet-funding/complete",
            completion_payload,
            timeout_s=timeout_s,
        )
        completion_mode = "complete"
    except CaptainCliError as exc:
        if not _is_missing_bridge_completion_metadata_error(exc):
            raise
        completion = _post_hub_json(
            runtime.config.hub_url,
            "/api/hub/v1/credits/wallet-funding/import",
            build_bridge_wallet_funding_import_payload(runtime, deposit),
            timeout_s=timeout_s,
        )
        completion_mode = "import"

    try:
        balance_after_bridge = _fetch_hub_credit_balance(
            runtime.config.hub_url,
            wallet_address=runtime.wallet.address,
            timeout_s=timeout_s,
        )
    except Exception:
        balance_after_bridge = {}

    return {
        "enabled": True,
        "skipped": False,
        "hub_url": runtime.config.hub_url,
        "wallet_address": runtime.wallet.address,
        "account_id": resolved_account_id,
        "smoke_id": smoke_id,
        "deposit_id": deposit.get("deposit_id", ""),
        "transaction_hash": deposit.get("transaction_hash", ""),
        "bridge_credit_wei": str(bridge_credit_wei),
        "bridge_credits_display": credit_wei_to_decimal_text(bridge_credit_wei),
        "completion_mode": completion_mode,
        "completion": completion,
        "balance_start": balance_start,
        "balance_after_bridge": balance_after_bridge,
    }


def _data_god_mode_print_bridge_error(*, smoke_argv: list[str], exc: BaseException) -> None:
    hub_url = _data_smoke_arg_value(smoke_argv, "--ai-hub-url") or "configured hub"
    account_id = _data_smoke_arg_value(smoke_argv, "--ai-hub-account-id")
    print("Data god-mode: FAILED")
    print(f"agent: mainnet hub worker pool via {hub_url}")
    if account_id:
        print(f"account: {account_id} (Data/O3 wallet)")
    print("error: Data/O3 bridge funding failed before submitting Hub worker-pull requests.")
    print(f"detail: {exc}")
    print("next: verify O3's wallet/private key and mainnet bridge deployment, or use --no-bridge only if the O3 Hub account is already funded.")

def _data_args_enable_agent(argv: list[str]) -> bool:
    return any(str(token).strip() == "--agent" for token in argv)



def _data_args_enable_god_mode(argv: list[str]) -> bool:
    return any(str(token).strip() == "--god-mode" for token in argv)


def _split_data_free_and_option_tokens(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split Captain-style free prompt tokens from option tokens without lowercasing the prompt."""

    free_tokens: list[str] = []
    option_tokens: list[str] = []
    in_options = False
    for token in argv:
        text = str(token)
        if not in_options and text.startswith("-"):
            in_options = True
        if in_options:
            option_tokens.append(text)
        elif text.strip():
            free_tokens.append(text)
    return free_tokens, option_tokens


def _build_data_engage_options_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main-computer data engage computer --agent",
        description=(
            "Engage the local Main Computer model as Data/O3's ring-3 worker-pull worker. "
            "The Hub URL and model are inferred from the mainnet profile and local config unless overridden."
        ),
    )
    parser.add_argument("--agent", action="store_true", help="Engage the mainnet Hub worker-pull lane for Data/O3.")
    parser.add_argument("--hub-url", default="", help="Hub base URL override. Defaults to the inferred mainnet Hub.")
    parser.add_argument("--model", default="", help="Model override. Defaults to the configured local model.")
    parser.add_argument("--public-endpoint", default="", help="Optional public worker endpoint to advertise. Worker-pull mode does not require inbound access.")
    parser.add_argument(
        "--ring",
        type=int,
        default=3,
        help="Worker ring to advertise. Data/O3 agent mode requires ring 3.",
    )
    parser.add_argument("--poll-interval-s", type=float, default=2.0, help="Seconds between empty worker-pull polls.")
    parser.add_argument("--heartbeat-interval-s", type=float, default=30.0, help="Seconds between worker availability heartbeats.")
    parser.add_argument("--lease-seconds", type=float, default=None, help="Optional requested lease duration for worker-pull work.")
    parser.add_argument("--max-requests", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "-noverbose",
        "--noverbose",
        dest="verbose",
        action="store_false",
        default=True,
        help="Suppress worker-pull status output.",
    )
    return parser


def _run_data_engage_computer(args: argparse.Namespace, option_tokens: list[str]) -> int:
    engage_options = _build_data_engage_options_parser().parse_args(option_tokens)
    if not bool(engage_options.agent or getattr(args, "data_agent", False)):
        print("ERROR: Data engage computer currently requires --agent so it registers the worker-pull Hub lane.")
        print("Run: main-computer data engage computer --agent")
        return 2
    if int(getattr(engage_options, "ring", 3) or 3) != 3:
        print("ERROR: Data/O3 --agent workers must engage on ring 3.")
        print("Run: main-computer data engage computer --agent")
        return 2

    worker_config = _config_from_args(args)
    if worker_config.provider == "hub":
        print("ERROR: data engage computer --agent cannot use provider=hub because that would recurse back into the hub.")
        return 2

    hub_url = _data_mainnet_hub_url(engage_options.hub_url, args)
    model = _data_agent_model(args, engage_options.model) or worker_config.model
    worker_config = replace(
        worker_config,
        hub_url=hub_url,
        model=model,
    )
    worker = MainComputer.build(worker_config)
    if engage_options.verbose:
        print("Engaging Data/O3 as a ring 3 worker-pull worker.")
        print(f"Hub URL: {hub_url}")
        print(f"Worker node: {worker_config.hub_worker_node_id}")
        print(f"Model: {worker_config.model}")
        print("Ring: 3")
        print("Submit Data work in another window, for example:")
        print('  main-computer data "What is 4 + 9" --god-mode --agent')

    try:
        serve_hub_worker_pull(
            worker_config,
            _worker_pull_chat_with_command_ring_metadata(
                worker.provider.chat,
                assigned_ring=engage_options.ring,
                command_identity="data-engage-computer",
                worker_node_id=worker_config.hub_worker_node_id,
                worker_instance_id=worker_config.hub_worker_node_id,
            ),
            hub_url=hub_url,
            public_endpoint=engage_options.public_endpoint or worker_config.hub_worker_endpoint,
            assigned_ring=engage_options.ring,
            poll_interval_s=engage_options.poll_interval_s,
            heartbeat_interval_s=engage_options.heartbeat_interval_s,
            lease_seconds=engage_options.lease_seconds,
            verbose=engage_options.verbose,
            max_requests=engage_options.max_requests,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


def _build_data_god_mode_options_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main-computer data ... --god-mode",
        description=(
            "Run Data's O3 command-runner identity through the full Byzantine reference "
            "god-mode pathway. AI calls remain untrusted; each AI-derived phase collapses "
            "only at a deterministic controller boundary."
        ),
    )
    parser.add_argument("prompt_tail", nargs="*", help="Prompt tokens when the prompt is written after --god-mode/options.")
    parser.add_argument("--prompt", default="", help="Prompt override. Otherwise free text before --god-mode/options is used.")
    parser.add_argument("--god-mode", action="store_true", help="Required for the full Byzantine reference command-runner pathway.")
    parser.add_argument(
        "--agent",
        action="store_true",
        help="Force Data god-mode through the mainnet Hub worker pool instead of a local model.",
    )
    parser.add_argument("--ai-provider", "--provider", dest="ai_provider", default="", help="AI provider for the smoke path.")
    parser.add_argument("--ai-model", "--model", dest="ai_model", default="", help="AI model for the smoke path.")
    parser.add_argument("--ai-command", default="", help="Command provider adapter command.")
    parser.add_argument("--hub-url", "--ai-hub-url", dest="ai_hub_url", default="", help="Hub URL for --agent. Defaults to the mainnet hub profile.")
    parser.add_argument("--hub-client-node-id", "--ai-hub-client-node-id", dest="ai_hub_client_node_id", default="", help="Hub client node id for --agent.")
    parser.add_argument("--hub-account-id", "--ai-hub-account-id", dest="ai_hub_account_id", default="", help="Hub account id for --agent. Defaults to Data/O3's wallet account.")
    parser.add_argument("--hub-wallet-address", "--ai-hub-wallet-address", dest="ai_hub_wallet_address", default="", help="Wallet address used to derive the Hub account id for --agent. Defaults to Data/O3.")
    parser.add_argument(
        "--bridge-credits",
        dest="bridge_credits",
        default="auto",
        help="Credits to bridge before live --agent worker-pull calls. Defaults to an automatic full-path floor.",
    )
    parser.add_argument("--no-bridge", dest="no_bridge", action="store_true", help="Do not pre-fund Data/O3 bridge credits before --agent calls.")
    parser.add_argument("--no-bridge-refund", dest="bridge_refund", action="store_false", help=argparse.SUPPRESS)
    parser.set_defaults(bridge_refund=True)
    parser.add_argument("--bridge-controller-private-key", dest="bridge_controller_private_key", default="", help=argparse.SUPPRESS)
    parser.add_argument("--ai-hub-transport", dest="ai_hub_transport", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--hub-allow-insecure-dev-network",
        "--ai-hub-allow-insecure-dev-network",
        dest="ai_hub_allow_insecure_dev_network",
        action="store_true",
        help="Allow non-HTTPS non-loopback hub URLs for local development only.",
    )
    parser.add_argument("--ai-timeout-seconds", type=float, default=300.0, help="AI timeout in seconds.")
    parser.add_argument("--work-root", default=".smoke-runs", help="Smoke run root.")
    parser.add_argument("--run-id", default="", help="Smoke run id. Defaults inside the smoke harness.")
    parser.add_argument("--run-dir", default="", help="Explicit smoke run directory.")
    parser.add_argument("--report-path", default="", help="Explicit real-agent report path.")
    parser.add_argument("--ai-trace-path", default="", help="Explicit AI trace path.")
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Set both worker and reviewer counts. God mode floors this to 4; larger values such as 5 are preserved.",
    )
    parser.add_argument("--worker-count", "--real-agent-worker-count", dest="worker_count", type=int, default=0)
    parser.add_argument("--reviewer-count", "--real-agent-reviewer-count", dest="reviewer_count", type=int, default=0)
    parser.add_argument("--expected-endstate", "--real-agent-expected-endstate", dest="expected_endstate", default="")
    parser.add_argument("--expected-changed-files", "--real-agent-expected-changed-files", dest="expected_changed_files", default="")
    parser.add_argument("--expected-unchanged-files", "--real-agent-expected-unchanged-files", dest="expected_unchanged_files", default="")
    parser.add_argument(
        "--scripted-ai-smoke",
        action="store_true",
        help="Use deterministic scripted AI for local reference verification instead of live provider calls.",
    )
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help="Print the raw smoke JSONL event stream. By default Data prints a compact summary.",
    )
    return parser


def _data_god_mode_smoke_argv(args: argparse.Namespace, data_args: list[str]) -> list[str]:
    free_tokens, option_tokens = _split_data_free_and_option_tokens(data_args)
    parser = _build_data_god_mode_options_parser()
    data_options, unknown = parser.parse_known_args(option_tokens)
    if unknown:
        parser.error(f"unsupported --god-mode option(s): {' '.join(unknown)}")
    if not data_options.god_mode:
        parser.error("main-computer data god-mode path requires --god-mode")

    prompt = str(data_options.prompt or " ".join([*free_tokens, *data_options.prompt_tail])).strip()
    if not prompt:
        parser.error("main-computer data --god-mode requires a prompt.")

    agent_mode = bool(data_options.agent or _data_args_enable_agent(data_args))
    provider = str(data_options.ai_provider or getattr(args, "provider", "") or "ollama").strip()
    if agent_mode:
        provider = "hub"
    model = str(data_options.ai_model or getattr(args, "model", "") or "").strip()
    hub_url = str(data_options.ai_hub_url or "").strip()
    hub_client_node_id = str(data_options.ai_hub_client_node_id or "").strip()
    hub_account_id = str(getattr(data_options, "ai_hub_account_id", "") or "").strip()
    hub_wallet_address = str(getattr(data_options, "ai_hub_wallet_address", "") or "").strip()
    if agent_mode:
        model = _data_agent_model(args, model)
        hub_url = _data_mainnet_hub_url(hub_url, args)
        hub_client_node_id = hub_client_node_id or str(getattr(args, "hub_client_node_id", "") or "").strip() or _DATA_AGENT_CLIENT_NODE_ID
        hub_account_id = _data_o3_hub_account_id(
            args,
            hub_account_id,
            explicit_wallet_address=hub_wallet_address,
        )
        if not hub_account_id:
            parser.error(
                "Data --agent could not resolve O3's Hub account. "
                "Set MAIN_COMPUTER_DATA_O3_WALLET_ADDRESS or pass --hub-wallet-address."
            )
    count = max(0, int(data_options.count or 0))
    worker_count = max(count, int(data_options.worker_count or 0))
    reviewer_count = max(count, int(data_options.reviewer_count or 0))

    smoke_argv: list[str] = [
        "--real-agent-prompt",
        prompt,
        "--god-mode",
        "--ai-provider",
        provider,
        "--work-root",
        str(data_options.work_root),
        "--ai-timeout-seconds",
        str(float(data_options.ai_timeout_seconds)),
    ]
    if model:
        smoke_argv.extend(["--ai-model", model])
    if data_options.ai_command:
        smoke_argv.extend(["--ai-command", str(data_options.ai_command)])
    if hub_url:
        smoke_argv.extend(["--ai-hub-url", hub_url])
    if hub_client_node_id:
        smoke_argv.extend(["--ai-hub-client-node-id", hub_client_node_id])
    if hub_account_id:
        smoke_argv.extend(["--ai-hub-account-id", hub_account_id])
    if agent_mode:
        smoke_argv.extend(["--ai-hub-transport", "worker-pull"])
    elif str(getattr(data_options, "ai_hub_transport", "") or "").strip():
        smoke_argv.extend(["--ai-hub-transport", str(data_options.ai_hub_transport).strip()])
    if bool(data_options.ai_hub_allow_insecure_dev_network):
        smoke_argv.append("--ai-hub-allow-insecure-dev-network")
    if data_options.run_id:
        smoke_argv.extend(["--run-id", str(data_options.run_id)])
    if data_options.run_dir:
        smoke_argv.extend(["--run-dir", str(data_options.run_dir)])
    if data_options.report_path:
        smoke_argv.extend(["--report-path", str(data_options.report_path)])
    if data_options.ai_trace_path:
        smoke_argv.extend(["--ai-trace-path", str(data_options.ai_trace_path)])
    if worker_count > 0:
        smoke_argv.extend(["--real-agent-worker-count", str(worker_count)])
    if reviewer_count > 0:
        smoke_argv.extend(["--real-agent-reviewer-count", str(reviewer_count)])
    if data_options.expected_endstate:
        smoke_argv.extend(["--real-agent-expected-endstate", str(data_options.expected_endstate)])
    if data_options.expected_changed_files:
        smoke_argv.extend(["--real-agent-expected-changed-files", str(data_options.expected_changed_files)])
    if data_options.expected_unchanged_files:
        smoke_argv.extend(["--real-agent-expected-unchanged-files", str(data_options.expected_unchanged_files)])
    if data_options.scripted_ai_smoke:
        smoke_argv.append("--scripted-ai-smoke")
    if data_options.verbose_events:
        smoke_argv.append("--verbose-events")
    return smoke_argv



def _data_prefix_option_tokens(args: argparse.Namespace) -> list[str]:
    """Recreate data options parsed before the free prompt so the inner parser sees one orderly shape."""

    tokens: list[str] = []
    if bool(getattr(args, "data_god_mode", False)):
        tokens.append("--god-mode")
    if bool(getattr(args, "data_agent", False)):
        tokens.append("--agent")
    for attr, option in (
        ("data_ai_provider", "--ai-provider"),
        ("data_ai_model", "--ai-model"),
        ("data_ai_command", "--ai-command"),
        ("data_ai_hub_url", "--ai-hub-url"),
        ("data_ai_hub_client_node_id", "--ai-hub-client-node-id"),
        ("data_ai_hub_account_id", "--ai-hub-account-id"),
        ("data_ai_hub_wallet_address", "--ai-hub-wallet-address"),
        ("data_bridge_credits", "--bridge-credits"),
        ("data_bridge_controller_private_key", "--bridge-controller-private-key"),
        ("data_ai_hub_transport", "--ai-hub-transport"),
        ("data_work_root", "--work-root"),
        ("data_run_id", "--run-id"),
        ("data_run_dir", "--run-dir"),
        ("data_report_path", "--report-path"),
        ("data_ai_trace_path", "--ai-trace-path"),
        ("data_expected_endstate", "--expected-endstate"),
        ("data_expected_changed_files", "--expected-changed-files"),
        ("data_expected_unchanged_files", "--expected-unchanged-files"),
    ):
        value = str(getattr(args, attr, "") or "").strip()
        if value:
            tokens.extend([option, value])
    for attr, option in (
        ("data_ai_timeout_seconds", "--ai-timeout-seconds"),
        ("data_count", "--count"),
        ("data_worker_count", "--worker-count"),
        ("data_reviewer_count", "--reviewer-count"),
    ):
        value = getattr(args, attr, None)
        if value not in (None, "", 0, 0.0):
            tokens.extend([option, str(value)])
    if bool(getattr(args, "data_no_bridge", False)):
        tokens.append("--no-bridge")
    if bool(getattr(args, "data_no_bridge_refund", False)):
        tokens.append("--no-bridge-refund")
    if bool(getattr(args, "data_scripted_ai_smoke", False)):
        tokens.append("--scripted-ai-smoke")
    if bool(getattr(args, "data_ai_hub_allow_insecure_dev_network", False)):
        tokens.append("--ai-hub-allow-insecure-dev-network")
    if bool(getattr(args, "data_verbose_events", False)):
        tokens.append("--verbose-events")
    return tokens



def _data_god_mode_jsonl_objects(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _data_god_mode_report_path(smoke_argv: list[str], event_text: str) -> str:
    if "--report-path" in smoke_argv:
        idx = smoke_argv.index("--report-path")
        if idx + 1 < len(smoke_argv):
            return str(smoke_argv[idx + 1])
    for event in reversed(_data_god_mode_jsonl_objects(event_text)):
        report_path = str(event.get("report_path", "") or "").strip()
        if report_path:
            return report_path
    return ""


def _data_god_mode_decision_answer(report: dict[str, object]) -> str:
    decision = report.get("decision")
    if isinstance(decision, dict):
        for key in ("answer", "clarifying_question", "proposal_summary"):
            value = str(decision.get(key, "") or "").strip()
            if value:
                return value
    byzantine = report.get("byzantine")
    if isinstance(byzantine, dict):
        selected_payload = byzantine.get("selected_payload")
        if isinstance(selected_payload, dict):
            for key in ("answer", "clarifying_question", "proposal_summary"):
                value = str(selected_payload.get(key, "") or "").strip()
                if value:
                    return value
    return ""


def _data_god_mode_print_summary(
    *,
    rc: int,
    smoke_argv: list[str],
    event_text: str,
    bridge_summary: dict[str, object] | None = None,
) -> None:
    report_path = _data_god_mode_report_path(smoke_argv, event_text)
    report: dict[str, object] = {}
    if report_path:
        try:
            raw_report = json.loads(Path(report_path).read_text(encoding="utf-8"))
            if isinstance(raw_report, dict):
                report = raw_report
        except Exception:
            report = {}

    if not report:
        status = "OK" if rc == 0 else "FAILED"
        print(f"Data god-mode: {status}")
        if report_path:
            print(f"report: {report_path}")
        print("summary: report unavailable; rerun with --verbose-events for the raw JSONL event stream.")
        return

    ok = bool(report.get("ok")) and rc == 0
    final_endstate = str(report.get("final_endstate", "") or "unknown")
    live_calls = report.get("ai_call_summary", {})
    live_call_count = 0
    if isinstance(live_calls, dict):
        live_call_count = int(live_calls.get("finished_live_call_count", 0) or 0)
    if not live_call_count:
        live_call_count = int(report.get("live_ai_call_count", 0) or 0)
    worker_count = int(report.get("real_agent_worker_count", 0) or 0)
    reviewer_count = int(report.get("real_agent_reviewer_count", 0) or 0)
    expected_floor = int(report.get("expected_byzantine_ai_phase_call_floor", 0) or 0)
    changed_files = report.get("changed_files", [])
    if not isinstance(changed_files, list):
        changed_files = []
    reference = report.get("full_byzantine_reference_path")
    reference_ok = False
    single_ai_trust_points: list[str] = []
    if isinstance(reference, dict):
        reference_ok = bool(reference.get("full_byzantine_reference_path"))
        points = reference.get("single_ai_trust_points", [])
        if isinstance(points, list):
            single_ai_trust_points = [str(point) for point in points]
    failed_contracts = report.get("failed_contracts", [])
    if not isinstance(failed_contracts, list):
        failed_contracts = []

    print(f"Data god-mode: {'OK' if ok else 'FAILED'}")
    answer = _data_god_mode_decision_answer(report)
    if answer and final_endstate in {"answer_only", "needs_clarification", "proposal_created", "already_satisfied", "proposal_rejected_unsafe"}:
        print(f"answer: {answer}")
    print(f"endstate: {final_endstate}")
    print(f"calls: {live_call_count} live AI calls; {worker_count} workers / {reviewer_count} reviewers; floor {expected_floor}")
    provider = str(report.get("ai_provider", "") or "")
    hub_url = str(report.get("ai_hub_url", "") or "")
    if provider == "hub" or "--ai-provider" in smoke_argv and "hub" in smoke_argv:
        if not hub_url and "--ai-hub-url" in smoke_argv:
            hub_url = smoke_argv[smoke_argv.index("--ai-hub-url") + 1]
        print(f"agent: mainnet hub worker pool via {hub_url or 'configured hub'}")
        hub_account_id = str(report.get("ai_hub_account_id", "") or "")
        if not hub_account_id and "--ai-hub-account-id" in smoke_argv:
            hub_account_id = smoke_argv[smoke_argv.index("--ai-hub-account-id") + 1]
        if hub_account_id:
            print(f"account: {hub_account_id} (Data/O3 wallet)")
        if bridge_summary and bridge_summary.get("enabled"):
            if bridge_summary.get("skipped"):
                reason = str(bridge_summary.get("reason") or "skipped")
                available = str(bridge_summary.get("available_credits_display") or "").strip()
                if available:
                    print(f"bridge: already funded ({available} credits available)")
                else:
                    print(f"bridge: skipped ({reason})")
            else:
                amount = str(bridge_summary.get("bridge_credits_display") or "").strip()
                tx_hash = str(bridge_summary.get("transaction_hash") or "").strip()
                mode = str(bridge_summary.get("completion_mode") or "complete").strip()
                if amount:
                    print(f"bridge: pre-funded {amount} credits for Data/O3 via {mode}")
                if tx_hash:
                    print(f"bridge_tx: {tx_hash}")
        hub_ring_contract = report.get("hub_ring3_contract", {})
        if isinstance(hub_ring_contract, dict) and hub_ring_contract:
            if int(hub_ring_contract.get("checked_call_count", 0) or 0) > 0:
                ring_status = "verified" if bool(hub_ring_contract.get("ok")) else "not verified"
                print(f"ring: requested 3; answered on ring 3: {ring_status}")
            elif bool(report.get("scripted_ai_smoke")):
                print("ring: requested 3; live answer-ring verification skipped in scripted mode")
    if changed_files:
        print(f"changed: {', '.join(str(path) for path in changed_files)}")
    if reference_ok:
        print("reference: full Byzantine apply path; single_ai_trust_points=[]")
    elif final_endstate not in {"applied_verified", "retry_succeeded", "applied_verification_failed", "retry_required"}:
        print("reference: Byzantine action boundary only; no edit/apply path selected")
    else:
        print(f"reference: not clean; single_ai_trust_points={single_ai_trust_points}")
    if failed_contracts:
        shown = [str(name) for name in failed_contracts[:8]]
        print(f"failed_contracts: {', '.join(shown)}")
        if len(failed_contracts) > len(shown):
            print(f"failed_contracts_more: {len(failed_contracts) - len(shown)}")
    print(f"report: {report.get('report_path') or report_path}")
    print(f"trace: {report.get('ai_trace_path') or ''}")




def _data_god_mode_print_runtime_error(*, smoke_argv: list[str], event_text: str, exc: BaseException) -> None:
    report_path = _data_god_mode_report_path(smoke_argv, event_text)
    provider = ""
    hub_url = ""
    if "--ai-provider" in smoke_argv:
        idx = smoke_argv.index("--ai-provider")
        if idx + 1 < len(smoke_argv):
            provider = str(smoke_argv[idx + 1])
    if "--ai-hub-url" in smoke_argv:
        idx = smoke_argv.index("--ai-hub-url")
        if idx + 1 < len(smoke_argv):
            hub_url = str(smoke_argv[idx + 1])
    message = str(exc)
    print("Data god-mode: FAILED")
    if provider == "hub":
        print(f"agent: mainnet hub worker pool via {hub_url or 'configured hub'}")
    if "HTTP 403" in message and "1010" in message and provider == "hub":
        print("error: mainnet Hub rejected this client before assigning a worker.")
        print("cause: HTTP 403 / Cloudflare 1010 from the Hub session-start endpoint.")
        print("next: the Hub edge must allow this CLI client signature, or set MAIN_COMPUTER_HUB_USER_AGENT to an allowed API client signature.")
    elif (
        "No hub workers or upstream hubs are registered or available" in message
        or "No matching Data/O3 worker-pull worker answered" in message
        or "Hub worker-pull request" in message
        or "worker is unreachable" in message.lower()
    ) and provider == "hub":
        print("error: no matching Data/O3 agent worker-pull worker is available on the inferred Hub/model lane.")
        print("next: start or check a matching worker with: main-computer data engage computer --agent")
        print(f"detail: {message}")
    else:
        print(f"error: {message}")
    if report_path:
        print(f"report: {report_path}")
    trace_path = ""
    if "--ai-trace-path" in smoke_argv:
        idx = smoke_argv.index("--ai-trace-path")
        if idx + 1 < len(smoke_argv):
            trace_path = str(smoke_argv[idx + 1])
    if not trace_path:
        for event in reversed(_data_god_mode_jsonl_objects(event_text)):
            value = str(event.get("ai_trace_path", "") or "").strip()
            if value:
                trace_path = value
                break
    if trace_path:
        print(f"trace: {trace_path}")
    print("raw_error: rerun with --verbose-events for the captured event stream and provider stack context.")


def cmd_data(args: argparse.Namespace) -> int:
    """Run Data's O3 command path.

    Without --god-mode this is a Captain-style shortcut that submits the prompt as O3.
    With --god-mode it invokes the local full-Byzantine reference smoke so Data can
    command the agent/angel fanout without trusting any single AI call.
    """

    data_args = [*list(getattr(args, "data_args", []) or []), *_data_prefix_option_tokens(args)]
    data_free_tokens, data_option_tokens = _split_data_free_and_option_tokens(data_args)
    if tuple(str(token).strip().lower() for token in data_free_tokens) == _DATA_ENGAGE_COMPUTER_PHRASE:
        return _run_data_engage_computer(args, data_option_tokens)

    if _data_args_enable_god_mode(data_args):
        smoke_argv = _data_god_mode_smoke_argv(args, data_args)
        verbose_events = "--verbose-events" in smoke_argv
        if verbose_events:
            smoke_argv = [token for token in smoke_argv if token != "--verbose-events"]
        from main_computer.rag_code_edit_agent_guidance_smoke import main as run_guidance_smoke

        try:
            bridge_summary = _data_god_mode_bridge_prefund(args, data_args, smoke_argv)
        except Exception as exc:
            _data_god_mode_print_bridge_error(smoke_argv=smoke_argv, exc=exc)
            return 2

        if verbose_events:
            try:
                return run_guidance_smoke(smoke_argv)
            except Exception as exc:
                _data_god_mode_print_runtime_error(smoke_argv=smoke_argv, event_text="", exc=exc)
                return 2

        event_buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(event_buffer):
                rc = run_guidance_smoke(smoke_argv)
        except Exception as exc:
            _data_god_mode_print_runtime_error(smoke_argv=smoke_argv, event_text=event_buffer.getvalue(), exc=exc)
            return 2
        _data_god_mode_print_summary(
            rc=rc,
            smoke_argv=smoke_argv,
            event_text=event_buffer.getvalue(),
            bridge_summary=bridge_summary,
        )
        return rc

    # Captain-style fallback: Data is the O3/third-officer command identity.
    # --agent is a Data-side routing flag; Captain already defaults this smoke path
    # to the mainnet Hub worker pool, so do not pass the flag through to Captain's parser.
    captain_data_args = [token for token in data_args if str(token).strip() != "--agent"]
    return run_captain(["smoke", _DATA_DEFAULT_OFFICER_SELECTOR, *captain_data_args], config=_config_from_args(args), cwd=Path.cwd())

def cmd_captain(args: argparse.Namespace) -> int:
    captain_args = list(getattr(args, "captain_args", []) or [])
    free_tokens, option_tokens = _split_captain_free_and_option_tokens(captain_args)
    if free_tokens == _CAPTAIN_ENGAGE_COMPUTER_PHRASE:
        return _run_captain_engage_computer(args, option_tokens)

    try:
        return run_captain(captain_args, config=_config_from_args(args), cwd=Path.cwd())
    except CaptainCliError as exc:
        print(f"ERROR: {exc}")
        return 2


def cmd_projects(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    catalog = ProjectCatalog(config.workspace)
    for project in catalog.list_projects():
        markers = ", ".join(project.markers) if project.markers else "no root marker"
        print(f"{project.name} | {markers}")
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    project = ProjectCatalog(config.workspace).inspect(args.name)
    print(f"name: {project.name}")
    print(f"path: {project.path}")
    print(f"markers: {', '.join(project.markers) if project.markers else 'none'}")
    print(f"top_level_dirs: {project.child_count}")
    print(f"top_level_files: {project.file_count}")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    print("ollama | default | local HTTP API | model default: gemma4:26b")
    print("openai | optional | OpenAI Python SDK | requires OPENAI_API_KEY")
    print("hub | remote | Main Computer hub broker | set MAIN_COMPUTER_HUB_URL or --hub-url")
    return 0


def cmd_viewport(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    serve(config, host=args.host, port=args.port, verbose=bool(args.verbose or config.fallback))
    return 0


def cmd_openclaw_bridge(args: argparse.Namespace) -> int:
    serve_openclaw_bridge(
        _config_from_args(args),
        host=args.host,
        port=args.port,
        token=args.token or os.environ.get("MAIN_COMPUTER_OPENCLAW_TOKEN"),
        verbose=args.verbose,
    )
    return 0


def cmd_openclaw_ops(args: argparse.Namespace) -> int:
    args.openclaw_ops_func(args)
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace) if args.workspace else MainComputerConfig.from_env().workspace
    serve_heartbeat(
        HeartbeatConfig(
            workspace=workspace,
            bind_host=args.host,
            server_port=args.server_port,
            heartbeat_port=args.port,
            verbose=args.verbose,
        )
    )
    return 0


def cmd_hub(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    serve_hub(config, host=config.hub_bind_host, port=config.hub_bind_port, verbose=args.verbose)
    return 0


def cmd_hub_worker(args: argparse.Namespace) -> int:
    worker_config = _config_from_args(args)
    if worker_config.provider == "hub":
        raise RuntimeError("hub-worker cannot use provider=hub because that would recurse back into the hub.")
    worker = MainComputer.build(worker_config)
    serve_hub_worker(
        worker_config,
        worker.provider.chat,
        host=args.host,
        port=args.port,
        hub_url=args.hub_url,
        public_endpoint=args.public_endpoint or worker_config.hub_worker_endpoint,
        verbose=args.verbose,
    )
    return 0


def cmd_hub_register_worker(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    report = register_worker_with_hub(
        hub_url=args.hub_url or config.hub_url,
        node_id=args.node_id or config.hub_worker_node_id,
        endpoint=args.endpoint,
        model=args.model or config.model,
        credits_per_request=args.credits_per_request or config.hub_credits_per_request,
        timeout_s=config.hub_timeout_s,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_harness(args: argparse.Namespace) -> int:
    report = run_harness_from_args(args)
    print(f"Widget harness passed {len(report['checks'])} checks")
    print(f"Report: {report['output_dir']}\\widget_harness_report.json")
    return 0


def cmd_diagnostics(args: argparse.Namespace) -> int:
    report = run_diagnostics_from_args(args)
    print(f"Diagnostics passed {len(report['checks'])} checks at level {report['level']}")
    print(f"Report: {report['output_dir']}\\diagnostics_report.json")
    return 0


def cmd_rotate_logs(args: argparse.Namespace) -> int:
    report = run_log_rotator_from_args(args)
    action = "Would rotate" if args.dry_run else "Rotated"
    print(f"{action} {report.rotated_count} of {report.scanned_files} scanned files older than {report.max_age_days:g} days")
    print(f"Log root: {report.log_root}")
    print(f"Archive root: {report.archive_root}")
    for item in report.rotated:
        print(f"{item.source} -> {item.archive}")
    for error in report.errors:
        print(f"error: {error.source} -> {error.archive}: {error.message}")
    return 1 if report.errors else 0


def cmd_log_metrics(args: argparse.Namespace) -> int:
    report = run_log_metric_distribution_from_args(args)
    print(f"Read {report.records} record(s) from {len(report.paths)} input path(s)")
    print(f"Plotted {report.metric_count} metric distribution(s): {report.output_png}")
    if report.report_json:
        print(f"Report: {report.report_json}")
    for metric in report.metrics:
        failed = [test.name for test in metric.tests if not test.ok]
        status = "ok" if not failed else "check " + ", ".join(failed)
        print(
            f"{metric.metric}: n={metric.count} min={metric.min:g} "
            f"p50={metric.median:g} p95={metric.p95:g} max={metric.max:g} [{status}]"
        )
    return 0 if report.metric_count else 1


def cmd_code_stats(args: argparse.Namespace) -> int:
    report = run_code_stats_from_args(args)
    emit_code_stats_report(report, output_format=args.format, output=args.output, top=args.top)
    return 0


def cmd_recurrent_thinking(args: argparse.Namespace) -> int:
    result = run_recurrent_thinking_from_args(args)
    print(f"Recurrent thinking scanned {result.scanned_files} files")
    print(f"Recurring ideas: {len(result.ideas)}")
    print(f"Markdown: {args.out}")
    print(f"JSON: {args.json}")
    if args.fine_tune:
        print(f"Fine-tuning seed: {args.fine_tune}")
    return 0



def _payload_from_args(args: argparse.Namespace) -> dict[str, object]:
    if getattr(args, "payload_json", None):
        data = json.loads(args.payload_json)
        if not isinstance(data, dict):
            raise ValueError("--payload-json must decode to an object.")
        return data
    if getattr(args, "payload_file", None):
        data = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("--payload-file must contain a JSON object.")
        return data
    return {}


def cmd_conductor(args: argparse.Namespace) -> int:
    root = Path(getattr(args, "repo_root", None) or Path.cwd()).resolve()
    conductor = ConductorService(root)
    command = str(args.conductor_command or "status")
    if command == "status":
        result = conductor.status()
    elif command == "submit":
        result = conductor.submit(
            action=args.action,
            payload=_payload_from_args(args),
            run_at=args.run_at,
            confirm=bool(args.confirm),
            note=args.note or "",
        )
    elif command == "run-due":
        result = conductor.run_due(now=args.now, limit=args.limit)
    else:
        raise ValueError(f"Unsupported conductor command: {command}")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1

def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", help="Workspace root to scan.")
    parser.add_argument("--provider", choices=["ollama", "openai", "hub"], help="LLM provider.")
    parser.add_argument("--model", help="Provider model name.")
    parser.add_argument("--ollama-base-url", help="Ollama base URL.")
    parser.add_argument("--ollama-timeout-s", type=float, help="Ollama HTTP timeout in seconds.")
    parser.add_argument("--openai-base-url", help="OpenAI-compatible base URL.")
    parser.add_argument("--hub-url", help="Main Computer hub base URL.")
    parser.add_argument("--hub-timeout-s", type=float, help="Hub HTTP timeout in seconds.")
    parser.add_argument("--hub-client-node-id", help="Client node id used when calling the hub.")
    parser.add_argument("--hub-worker-node-id", help="Worker node id used for registration.")
    parser.add_argument("--hub-worker-endpoint", help="Public worker endpoint registered with the hub.")
    parser.add_argument("--hub-credits-per-request", type=int, help="Energy credits paid per completed hub request.")
    parser.add_argument("--hub-root", type=Path, help="Hub registry/runtime root.")
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="Enable hyper-verbose fallback logging and fastest visible model/Aider streaming.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main-computer")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Pass a prompt through the selected provider.")
    add_common_options(chat)
    chat.add_argument("prompt")
    chat.set_defaults(func=cmd_chat)

    captain = sub.add_parser(
        "captain",
        help="Connect as the Captain/officer backend wallet and run the ring-3 make-it-so smoke flow.",
    )
    add_common_options(captain)
    captain.add_argument(
        "captain_args",
        nargs=argparse.REMAINDER,
        help="Use: smoke [wallet-or-captain-or-officer] <free prompt> [--captain-options].",
    )
    captain.set_defaults(func=cmd_captain)

    data = sub.add_parser(
        "data",
        help=(
            "Run Data's O3 command path. Use --god-mode for the full Byzantine "
            "reference command-runner smoke."
        ),
    )
    add_common_options(data)
    data.add_argument("--god-mode", dest="data_god_mode", action="store_true", help="Run the full Byzantine reference god-mode pathway.")
    data.add_argument("--agent", dest="data_agent", action="store_true", help="Force Data through the inferred mainnet Hub worker pool.")
    data.add_argument("--ai-provider", dest="data_ai_provider", default="", help="AI provider for --god-mode.")
    data.add_argument("--ai-model", dest="data_ai_model", default="", help="AI model for --god-mode.")
    data.add_argument("--ai-command", dest="data_ai_command", default="", help="Command provider adapter command for --god-mode.")
    data.add_argument("--ai-hub-url", dest="data_ai_hub_url", default="", help="Hub URL for --agent. Defaults to the mainnet hub profile.")
    data.add_argument("--ai-hub-client-node-id", dest="data_ai_hub_client_node_id", default="", help="Hub client node id for --agent.")
    data.add_argument("--ai-hub-account-id", dest="data_ai_hub_account_id", default="", help="Hub account id for --agent. Defaults to Data/O3's wallet account.")
    data.add_argument("--ai-hub-wallet-address", dest="data_ai_hub_wallet_address", default="", help="Wallet address used to derive the Hub account id for --agent. Defaults to Data/O3.")
    data.add_argument("--bridge-credits", dest="data_bridge_credits", default="", help="Credits to bridge before live --agent worker-pull calls. Defaults to auto.")
    data.add_argument("--no-bridge", dest="data_no_bridge", action="store_true", help="Do not pre-fund Data/O3 bridge credits before --agent calls.")
    data.add_argument("--no-bridge-refund", dest="data_no_bridge_refund", action="store_true", help=argparse.SUPPRESS)
    data.add_argument("--bridge-controller-private-key", dest="data_bridge_controller_private_key", default="", help=argparse.SUPPRESS)
    data.add_argument("--ai-hub-allow-insecure-dev-network", dest="data_ai_hub_allow_insecure_dev_network", action="store_true", help="Allow non-HTTPS non-loopback hub URLs for local development only.")
    data.add_argument("--ai-timeout-seconds", dest="data_ai_timeout_seconds", type=float, default=0.0, help="AI timeout for --god-mode.")
    data.add_argument("--work-root", dest="data_work_root", default="", help="Smoke work root for --god-mode.")
    data.add_argument("--run-id", dest="data_run_id", default="", help="Smoke run id for --god-mode.")
    data.add_argument("--run-dir", dest="data_run_dir", default="", help="Explicit smoke run dir for --god-mode.")
    data.add_argument("--report-path", dest="data_report_path", default="", help="Explicit report path for --god-mode.")
    data.add_argument("--ai-trace-path", dest="data_ai_trace_path", default="", help="Explicit AI trace path for --god-mode.")
    data.add_argument("--count", dest="data_count", type=int, default=0, help="Set both worker and reviewer counts for --god-mode.")
    data.add_argument("--worker-count", "--real-agent-worker-count", dest="data_worker_count", type=int, default=0)
    data.add_argument("--reviewer-count", "--real-agent-reviewer-count", dest="data_reviewer_count", type=int, default=0)
    data.add_argument("--expected-endstate", "--real-agent-expected-endstate", dest="data_expected_endstate", default="")
    data.add_argument("--expected-changed-files", "--real-agent-expected-changed-files", dest="data_expected_changed_files", default="")
    data.add_argument("--expected-unchanged-files", "--real-agent-expected-unchanged-files", dest="data_expected_unchanged_files", default="")
    data.add_argument("--scripted-ai-smoke", dest="data_scripted_ai_smoke", action="store_true", help="Use deterministic scripted AI for local verification.")
    data.add_argument("--verbose-events", dest="data_verbose_events", action="store_true", help="Print the raw smoke JSONL event stream instead of only the compact Data summary.")
    data.add_argument(
        "data_args",
        nargs=argparse.REMAINDER,
        help=(
            "Captain-style free prompt/options. Without --god-mode this delegates as O3; "
            "with --god-mode it runs the full Byzantine reference pathway."
        ),
    )
    data.set_defaults(func=cmd_data)

    projects = sub.add_parser("projects", help="List local project folders.")
    add_common_options(projects)
    projects.set_defaults(func=cmd_projects)

    project = sub.add_parser("project", help="Inspect one local project folder.")
    add_common_options(project)
    project.add_argument("name")
    project.set_defaults(func=cmd_project)

    providers = sub.add_parser("providers", help="List available LLM providers.")
    add_common_options(providers)
    providers.set_defaults(func=cmd_providers)

    viewport = sub.add_parser("viewport", help="Start the local interactive console viewport.")
    add_common_options(viewport)
    viewport.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    viewport.add_argument("--port", type=int, default=8765, help="Port to bind.")
    viewport.add_argument(
        "-noverbose",
        "--noverbose",
        dest="verbose",
        action="store_false",
        default=True,
        help="Suppress server signal output.",
    )
    viewport.set_defaults(func=cmd_viewport)

    openclaw_bridge = sub.add_parser(
        "openclaw-bridge",
        help="Start the loopback bridge used by the OpenClaw local plugin.",
    )
    add_common_options(openclaw_bridge)
    openclaw_bridge.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    openclaw_bridge.add_argument(
        "--port",
        type=int,
        default=DEFAULT_OPENCLAW_BRIDGE_PORT,
        help="Port to bind.",
    )
    openclaw_bridge.add_argument("--token", help="Optional bearer token for bridge requests.")
    openclaw_bridge.add_argument(
        "-noverbose",
        "--noverbose",
        dest="verbose",
        action="store_false",
        default=True,
        help="Suppress bridge signal output.",
    )
    openclaw_bridge.set_defaults(func=cmd_openclaw_bridge)

    openclaw_ops = sub.add_parser(
        "openclaw-ops",
        help="Run the token-protected OpenClaw read-only ops smoke bridge.",
    )
    openclaw_ops_sub = openclaw_ops.add_subparsers(dest="openclaw_ops_command", required=True)

    openclaw_ops_serve = openclaw_ops_sub.add_parser("serve", help="Run the local operations bridge.")
    openclaw_ops_serve.add_argument("--host", default=openclaw_ops_smoke.DEFAULT_HOST)
    openclaw_ops_serve.add_argument("--port", type=int, default=openclaw_ops_smoke.DEFAULT_PORT)
    openclaw_ops_serve.add_argument("--token", default=None)
    openclaw_ops_serve.add_argument(
        "--root",
        default=None,
        help="Read-only root. Defaults to OPENCLAW_OPS_ROOT or the repository/package root containing this script.",
    )
    openclaw_ops_serve.add_argument("--max-read-bytes", type=int, default=openclaw_ops_smoke.DEFAULT_MAX_READ_BYTES)
    openclaw_ops_serve.set_defaults(func=cmd_openclaw_ops, openclaw_ops_func=openclaw_ops_smoke.serve)

    openclaw_ops_smoke_cmd = openclaw_ops_sub.add_parser("smoke", help="Run smoke checks against the bridge.")
    openclaw_ops_smoke_cmd.add_argument("--base-url", default=f"http://{openclaw_ops_smoke.DEFAULT_HOST}:{openclaw_ops_smoke.DEFAULT_PORT}")
    openclaw_ops_smoke_cmd.add_argument("--token", default=None)
    openclaw_ops_smoke_cmd.add_argument("--timeout", type=int, default=10)
    openclaw_ops_smoke_cmd.add_argument("--verbose", action="store_true")
    openclaw_ops_smoke_cmd.set_defaults(func=cmd_openclaw_ops, openclaw_ops_func=openclaw_ops_smoke.smoke)

    heartbeat = sub.add_parser("heartbeat", help="Start the external heartbeat control service.")
    heartbeat.add_argument("--workspace", help="Workspace root where pid files and logs live.")
    heartbeat.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    heartbeat.add_argument("--port", type=int, default=8766, help="Heartbeat port to bind.")
    heartbeat.add_argument("--server-port", type=int, default=8765, help="Viewport server port to supervise.")
    heartbeat.add_argument(
        "-noverbose",
        "--noverbose",
        dest="verbose",
        action="store_false",
        default=True,
        help="Suppress heartbeat signal output.",
    )
    heartbeat.set_defaults(func=cmd_heartbeat)

    hub = sub.add_parser("hub", help="Start the Main Computer hub broker.")
    add_common_options(hub)
    hub.add_argument("--network", help="Hub network key from the configured network registry. Defaults to the registry default.")
    hub.add_argument("--network-config", type=Path, help="Path to a Hub network registry JSON file.")
    hub.add_argument("--host", default=None, help="Hub bind host override. Defaults to the selected network profile.")
    hub.add_argument("--port", type=int, default=None, help="Hub bind port override. Defaults to the selected network profile.")
    hub.add_argument("--hub-runtime-dir", type=Path, help="Hub runtime root override. Alias for the profile hub_runtime_dir.")
    hub.add_argument("--chain-rpc-url", help="Chain RPC URL override for the selected network.")
    hub.add_argument("--chain-id", help="Chain id override for the selected network. Accepts decimal or 0x-prefixed hex.")
    hub.add_argument(
        "--bridge-backend",
        choices=["dev-chain", "credit-bridge-contract", "mock-chain"],
        help="Hub bridge backend. Defaults to dev-chain/contract mode; use mock-chain only for explicit lab/fake-chain runs.",
    )
    hub.add_argument(
        "--dev-chain-deployment-path",
        type=Path,
        help="Deployment metadata JSON used by the dev-chain/contract bridge backend. Defaults to the selected network manifest.",
    )
    hub.add_argument(
        "--contracts-path",
        type=Path,
        help="Public contract config JSON used for contract-address-only Hub startup.",
    )
    hub.add_argument(
        "--allow-missing-bridge-signer",
        action="store_true",
        help="Allow read/status-only contract-address startup without private bridge signer metadata.",
    )
    hub.add_argument(
        "--enable-smoke-bridge",
        action="store_true",
        help="Explicitly enable the admin-only smoke bridge path that may load smoke_client wallet metadata.",
    )
    hub.add_argument(
        "-noverbose",
        "--noverbose",
        dest="verbose",
        action="store_false",
        default=True,
        help="Suppress hub request logging.",
    )
    hub.set_defaults(func=cmd_hub, use_hub_network_defaults=True)

    hub_worker = sub.add_parser("hub-worker", help="Start a local model worker and optionally register it with a hub.")
    add_common_options(hub_worker)
    hub_worker.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    hub_worker.add_argument("--port", type=int, default=DEFAULT_HUB_WORKER_PORT, help="Port to bind.")
    hub_worker.add_argument("--public-endpoint", help="Externally reachable worker endpoint. Defaults to bound host/port.")
    hub_worker.add_argument(
        "-noverbose",
        "--noverbose",
        dest="verbose",
        action="store_false",
        default=True,
        help="Suppress worker request logging.",
    )
    hub_worker.set_defaults(func=cmd_hub_worker)

    hub_register = sub.add_parser("hub-register-worker", help="Register an already-running worker endpoint with a hub.")
    add_common_options(hub_register)
    hub_register.add_argument("--node-id", help="Worker node id.")
    hub_register.add_argument("--endpoint", required=True, help="Worker base endpoint, for example http://host:8771.")
    hub_register.add_argument("--credits-per-request", type=int, help="Energy credits paid for each completed request.")
    hub_register.set_defaults(func=cmd_hub_register_worker)

    harness = sub.add_parser("harness", help="Run the browser widget test harness.")
    harness.add_argument("--url", help="Connect to an already running viewport instead of starting a harness server.")
    harness.add_argument("--host", default="127.0.0.1", help="Host for the disposable harness server.")
    harness.add_argument("--port", type=int, default=0, help="Port for the disposable harness server. Use 0 for any free port.")
    harness.add_argument("--output-dir", default="harness_output", help="Where to write screenshots and the JSON report.")
    harness.add_argument("--headed", action="store_true", help="Show the browser while the harness runs.")
    harness.set_defaults(func=cmd_harness)

    diagnostics = sub.add_parser("diagnostics", help="Run layered main computer diagnostics.")
    diagnostics.add_argument("--level", choices=DIAGNOSTIC_LEVELS, default="widgets", help="Diagnostic depth.")
    diagnostics.add_argument("--workspace", help="Workspace root to scan.")
    diagnostics.add_argument("--provider", choices=["ollama", "openai", "hub"], help="Provider for build/live diagnostics.")
    diagnostics.add_argument("--model", help="Provider model name.")
    diagnostics.add_argument("--ollama-base-url", help="Ollama base URL.")
    diagnostics.add_argument("--ollama-timeout-s", type=float, help="Ollama HTTP timeout in seconds.")
    diagnostics.add_argument("--openai-base-url", help="OpenAI-compatible base URL.")
    diagnostics.add_argument("--url", help="Use an already running viewport for server/widget diagnostics.")
    diagnostics.add_argument("--output-dir", default="diagnostics_output", help="Where to write reports and screenshots.")
    diagnostics.add_argument("--headed", action="store_true", help="Show the browser during widget diagnostics.")
    diagnostics.set_defaults(func=cmd_diagnostics)

    rotate_logs = sub.add_parser(
        "rotate-logs",
        help="Compress and move old log files into ../archive/logs by default.",
    )
    rotate_logs.add_argument(
        "log_root",
        nargs="?",
        default="logs",
        help="Current log directory to rotate. Defaults to logs.",
    )
    rotate_logs.add_argument(
        "archive_root",
        nargs="?",
        default=None,
        help="Optional archive root, for example ../archive/logs.",
    )
    rotate_logs.add_argument(
        "--archive-root",
        dest="archive_root_option",
        default=None,
        help="Archive root override. Defaults to ../archive/logs relative to log_root.",
    )
    rotate_logs.add_argument(
        "--max-age-days",
        type=float,
        default=3.0,
        help="Rotate files older than this many days. Defaults to 3.",
    )
    rotate_logs.add_argument("--dry-run", action="store_true", help="Show files that would rotate without moving them.")
    rotate_logs.set_defaults(func=cmd_rotate_logs)

    log_metrics = sub.add_parser(
        "log-metrics",
        help="Read log files, calculate metric distributions, and write a PNG graph.",
    )
    add_log_metric_arguments(log_metrics)
    log_metrics.set_defaults(func=cmd_log_metrics)

    code_stats = sub.add_parser(
        "code-stats",
        aliases=["static-code-stats"],
        help="Analyze static code statistics such as line counts.",
    )
    add_code_stats_arguments(code_stats)
    code_stats.set_defaults(func=cmd_code_stats)

    recurrent = sub.add_parser(
        "recurrent-thinking",
        help="Mine visible AI artifacts for recurring project context/preload memory.",
    )
    recurrent.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Optional artifact files/directories. Defaults to Main Computer AI artifact roots.",
    )
    recurrent.add_argument("--repo-dir", type=Path, default=Path.cwd(), help="Main Computer repository root.")
    recurrent.add_argument(
        "--out",
        type=Path,
        default=Path("debug_assets") / "recurrent_thoughts.md",
        help="Markdown preload output path.",
    )
    recurrent.add_argument(
        "--json",
        type=Path,
        default=Path("debug_assets") / "recurrent_thoughts.json",
        help="Structured JSON output path.",
    )
    recurrent.add_argument("--fine-tune", type=Path, default=None, help="Optional JSONL fine-tuning seed output path.")
    recurrent.add_argument("--project-name", default="Main Computer", help="Project name for fine-tuning seed examples.")
    recurrent.add_argument("--min-files", type=int, default=2, help="Minimum distinct files a concept must appear in.")
    recurrent.add_argument("--min-occurrences", type=int, default=2, help="Minimum total occurrences a concept must have.")
    recurrent.add_argument("--top", type=int, default=50, help="Maximum number of recurrent ideas to emit.")
    recurrent.add_argument("--max-file-bytes", type=int, default=1_500_000, help="Skip files larger than this many bytes.")
    recurrent.set_defaults(func=cmd_recurrent_thinking)


    conductor = sub.add_parser(
        "conductor",
        help="Plan, schedule, and run local subprocess-backed conductor actions.",
    )
    conductor.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository/runtime root. Defaults to the current directory.")
    conductor_sub = conductor.add_subparsers(dest="conductor_command", required=True)

    conductor_status = conductor_sub.add_parser("status", help="Show conductor state, scheduled jobs, and public key/DNS records.")
    conductor_status.set_defaults(func=cmd_conductor)

    conductor_submit = conductor_sub.add_parser("submit", help="Submit a conductor action now or schedule it for later.")
    conductor_submit.add_argument("--action", required=True, help="Action id, for example dns.record.upsert or local.secret.generate.")
    conductor_submit.add_argument("--payload-json", default="", help="JSON object payload for the action.")
    conductor_submit.add_argument("--payload-file", type=Path, help="Path to a JSON object payload file.")
    conductor_submit.add_argument("--run-at", default="", help="Optional ISO datetime. Future values schedule the action.")
    conductor_submit.add_argument("--confirm", action="store_true", help="Apply the side effect. Omit for dry-run/planning.")
    conductor_submit.add_argument("--note", default="", help="Operator note recorded with the job.")
    conductor_submit.set_defaults(func=cmd_conductor)

    conductor_due = conductor_sub.add_parser("run-due", help="Run due scheduled conductor jobs.")
    conductor_due.add_argument("--now", default="", help="Optional ISO datetime used for due-job testing.")
    conductor_due.add_argument("--limit", type=int, default=10, help="Maximum due jobs to run.")
    conductor_due.set_defaults(func=cmd_conductor)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
