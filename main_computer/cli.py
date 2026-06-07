from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

from main_computer.catalog import ProjectCatalog
from main_computer.config import MainComputerConfig
from main_computer.diagnostics import LEVELS as DIAGNOSTIC_LEVELS
from main_computer.diagnostics import run_from_args as run_diagnostics_from_args
from main_computer.harness import run_from_args as run_harness_from_args
from main_computer.log_rotator import run_from_args as run_log_rotator_from_args
from main_computer.heartbeat import HeartbeatConfig, serve as serve_heartbeat
from main_computer.hub import DEFAULT_HUB_PORT, DEFAULT_HUB_WORKER_PORT, register_worker_with_hub, serve_hub, serve_hub_worker
from main_computer.hub_networks import (
    HubNetworkConfigError,
    env_chain_id_override,
    env_chain_rpc_url_override,
    env_hub_host_override,
    env_hub_network_name,
    env_hub_port_override,
    env_hub_runtime_dir_override,
    load_hub_network_registry,
)
from main_computer.openclaw_bridge import DEFAULT_OPENCLAW_BRIDGE_PORT, serve as serve_openclaw_bridge
from main_computer.recurrent_thinking import run_from_args as run_recurrent_thinking_from_args
from main_computer.static_code_analyzer import emit_report as emit_code_stats_report
from main_computer.static_code_analyzer import add_arguments as add_code_stats_arguments
from main_computer.static_code_analyzer import run_from_args as run_code_stats_from_args
from main_computer.router import MainComputer
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
        hub_host=env_hub_host_override(),
        hub_port=env_hub_port_override(),
        hub_runtime_dir=env_runtime_dir,
        chain_rpc_url=env_chain_rpc_url,
        chain_id=env_chain_id if env_chain_id is not None else profile.chain_id,
    )
    profile = profile.with_overrides(
        hub_host=getattr(args, "host", None),
        hub_port=getattr(args, "port", None),
        hub_runtime_dir=getattr(args, "hub_runtime_dir", None) or getattr(args, "hub_root", None),
        chain_rpc_url=getattr(args, "chain_rpc_url", None),
        chain_id=getattr(args, "chain_id", None) if getattr(args, "chain_id", None) is not None else profile.chain_id,
    )
    profile.validate_runnable()

    source = f"hub-network:{profile.network_key}"
    return replace(
        config,
        hub_network=profile.network_key,
        hub_network_display_name=profile.display_name,
        hub_network_kind=profile.kind,
        hub_network_config_path=registry.source_path,
        hub_bind_host=profile.hub_host,
        hub_bind_port=profile.hub_port,
        hub_root=profile.hub_runtime_dir,
        hub_url=getattr(args, "hub_url", None) or f"http://{profile.hub_host}:{profile.hub_port}",
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
        token=args.token,
        verbose=args.verbose,
    )
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

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
