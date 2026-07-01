#!/usr/bin/env python3
"""Runtime launcher for the experimental FoundationDB Hub image.

Coolify's Dockerfile application path may ignore or constrain the configured
application start command.  Keep Dockerfile CMD short and derive the real Hub
configuration from explicit flags, runtime environment, and the checked-in Hub
network registry.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.hub_networks import HubNetworkProfile, HubNetworkRegistry, load_hub_network_registry  # noqa: E402
from main_computer.runtime_env_file import load_runtime_env_file, merged_runtime_env  # noqa: E402


DEFAULT_BRIDGE_BACKEND = "dev-chain"
MOCK_BRIDGE_BACKENDS = {"mock", "mock-chain", "mock-chain-lite"}


def first_env(environ: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(environ.get(name) or "").strip()
        if value:
            return value
    return ""


def runtime_env_file_path(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    return str(
        getattr(args, "runtime_env_file", "")
        or first_env(environ, "MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE", "MAIN_COMPUTER_RUNTIME_ENV_FILE")
        or ""
    ).strip()


def environ_with_runtime_env_file(args: argparse.Namespace, environ: Mapping[str, str]) -> dict[str, str]:
    path = runtime_env_file_path(args, environ)
    if not path:
        return {str(key): str(value) for key, value in environ.items()}
    return merged_runtime_env(environ, load_runtime_env_file(path))


def env_flag(environ: Mapping[str, str], *names: str) -> bool:
    for name in names:
        value = str(environ.get(name) or "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return False


def parse_optional_port(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        port = int(text, 10)
    except ValueError:
        return None
    if 1 <= port <= 65535:
        return port
    return None


def profile_host(profile: HubNetworkProfile) -> str:
    try:
        parsed = urlsplit(profile.hub_url)
    except ValueError:
        return ""
    return (parsed.hostname or "").strip().lower()


def env_url_hosts(environ: Mapping[str, str]) -> set[str]:
    hosts: set[str] = set()
    for name in ("MAIN_COMPUTER_HUB_URL", "MAIN_COMPUTER_HUB_PUBLIC_URL", "COOLIFY_URL", "COOLIFY_FQDN"):
        raw = str(environ.get(name) or "").strip()
        if not raw:
            continue
        for item in raw.split(","):
            clean = item.strip()
            if not clean:
                continue
            if "://" not in clean:
                clean = f"https://{clean}"
            try:
                host = (urlsplit(clean).hostname or "").strip().lower()
            except ValueError:
                host = ""
            if host:
                hosts.add(host)
    return hosts


def infer_network_from_port(registry: HubNetworkRegistry, port: int | None) -> str:
    if port is None:
        return ""
    matches = [
        profile.network_key
        for profile in registry.networks.values()
        if int(profile.hub_bind_port) == int(port)
    ]
    return matches[0] if len(matches) == 1 else ""


def infer_network_from_url(registry: HubNetworkRegistry, environ: Mapping[str, str]) -> str:
    hosts = env_url_hosts(environ)
    if not hosts:
        return ""
    matches = [
        profile.network_key
        for profile in registry.networks.values()
        if profile_host(profile) in hosts
    ]
    return matches[0] if len(matches) == 1 else ""


def resolve_network(args: argparse.Namespace, registry: HubNetworkRegistry, environ: Mapping[str, str]) -> str:
    explicit = str(args.network or "").strip()
    if explicit:
        return explicit
    env_network = first_env(environ, "MAIN_COMPUTER_HUB_NETWORK", "MAIN_COMPUTER_NETWORK")
    if env_network:
        return env_network
    env_port = parse_optional_port(first_env(environ, "MAIN_COMPUTER_HUB_PORT", "MAIN_COMPUTER_HUB_BIND_PORT", "PORT"))
    port_network = infer_network_from_port(registry, env_port)
    if port_network:
        return port_network
    url_network = infer_network_from_url(registry, environ)
    if url_network:
        return url_network
    return registry.default_network


def resolve_port(args: argparse.Namespace, profile: HubNetworkProfile, environ: Mapping[str, str]) -> int:
    explicit = parse_optional_port(args.port)
    if explicit is not None:
        return explicit
    env_port = parse_optional_port(first_env(environ, "MAIN_COMPUTER_HUB_PORT", "MAIN_COMPUTER_HUB_BIND_PORT", "PORT"))
    if env_port is not None:
        return env_port
    return int(profile.hub_bind_port)


def default_runtime_root(network: str) -> str:
    return f"/data/main-computer/hub/{network}-exp-fdb"


def default_deployment_path(network: str) -> str:
    return f"/app/runtime/deployments/{network}/latest.json"


def default_contracts_path(network: str) -> str:
    return f"/app/main_computer/config/{network}_contracts.json"


def build_exp_fdb_hub_command(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    base_env = os.environ if environ is None else environ
    env = environ_with_runtime_env_file(args, base_env)
    registry = load_hub_network_registry(args.network_config)
    network = resolve_network(args, registry, env)
    profile = registry.get(network)

    port = resolve_port(args, profile, env)
    host = str(args.host or first_env(env, "MAIN_COMPUTER_HUB_BIND_HOST", "MAIN_COMPUTER_HUB_HOST", "HOST") or profile.hub_bind_host or "0.0.0.0").strip()
    hub_url = str(args.hub_url or first_env(env, "MAIN_COMPUTER_HUB_URL", "MAIN_COMPUTER_HUB_PUBLIC_URL") or profile.hub_url).strip()

    root = str(
        args.root
        or first_env(env, "MAIN_COMPUTER_HUB_ROOT", "MAIN_COMPUTER_HUB_RUNTIME_DIR", "HUB_ROOT")
        or default_runtime_root(profile.network_key)
    ).strip().rstrip("/")
    cluster_file = str(
        args.cluster_file
        or first_env(env, "MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE", "FDB_CLUSTER_FILE")
        or f"{root}/fdb.cluster"
    ).strip()
    namespace = str(
        args.namespace
        or first_env(env, "MAIN_COMPUTER_HUB_FDB_NAMESPACE", "MAIN_COMPUTER_FDB_NAMESPACE")
        or f"main-computer-{profile.network_key}-exp-fdb"
    ).strip()
    bridge_backend = str(
        args.bridge_backend
        or first_env(env, "MAIN_COMPUTER_HUB_BRIDGE_BACKEND", "MAIN_COMPUTER_BRIDGE_BACKEND")
        or DEFAULT_BRIDGE_BACKEND
    ).strip()

    command = [
        "python",
        "/app/exp-fdb-hub.py",
        "--host",
        host,
        "--port",
        str(port),
        "--hub-url",
        hub_url,
        "--hub-root",
        root,
        "--cluster-file",
        cluster_file,
        "--namespace",
        namespace,
        "--network-key",
        profile.network_key,
        "--network-display-name",
        profile.display_name,
        "--network-kind",
        profile.kind,
        "--no-fdb-autostart",
        "--no-activate-cached-native-client",
        "--require-multisession-auth",
        "--bridge-backend",
        bridge_backend,
    ]

    if bridge_backend.lower() not in MOCK_BRIDGE_BACKENDS:
        allow_missing_bridge_signer = args.allow_missing_bridge_signer or env_flag(
            env,
            "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER",
            "MAIN_COMPUTER_ALLOW_MISSING_BRIDGE_SIGNER",
        )
        enable_smoke_bridge = args.enable_smoke_bridge or env_flag(
            env,
            "MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE",
            "MAIN_COMPUTER_ENABLE_SMOKE_BRIDGE",
        )
        dev_chain_deployment_path = str(
            args.dev_chain_deployment_path
            or first_env(
                env,
                "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH",
                "MAIN_COMPUTER_DEV_CHAIN_DEPLOYMENT_PATH",
            )
        ).strip()
        if dev_chain_deployment_path or not allow_missing_bridge_signer:
            command.extend(
                [
                    "--dev-chain-deployment-path",
                    dev_chain_deployment_path or default_deployment_path(profile.network_key),
                ]
            )
        command.extend(
            [
                "--contracts-path",
                str(
                    args.contracts_path
                    or first_env(
                        env,
                        "MAIN_COMPUTER_HUB_CONTRACTS_PATH",
                        "MAIN_COMPUTER_CONTRACTS_PATH",
                    )
                    or default_contracts_path(profile.network_key)
                ).strip(),
            ]
        )
        if allow_missing_bridge_signer:
            command.append("--allow-missing-bridge-signer")
        if enable_smoke_bridge:
            command.append("--enable-smoke-bridge")
    chain_id = str(args.chain_id or first_env(env, "MAIN_COMPUTER_HUB_CHAIN_ID", "MAIN_COMPUTER_CHAIN_ID") or profile.chain_id or "").strip()
    if chain_id:
        command.extend(["--chain-id", chain_id])
    chain_rpc_url = str(
        args.chain_rpc_url
        or first_env(env, "MAIN_COMPUTER_HUB_CHAIN_RPC_URL", "MAIN_COMPUTER_CHAIN_RPC_URL")
        or profile.chain_rpc_url
        or ""
    ).strip()
    if chain_rpc_url:
        command.extend(["--chain-rpc-url", chain_rpc_url])

    return command


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the experimental FoundationDB Hub from runtime defaults.")
    parser.add_argument("--network", default="", help="Hub network key. Defaults to env, PORT inference, then registry default.")
    parser.add_argument("--network-config", default=None, help="Optional hub_networks.json path.")
    parser.add_argument(
        "--runtime-env-file",
        default="",
        help=(
            "Optional strict KEY=VALUE runtime env file. Values override the process environment "
            "for launcher defaults, while explicit CLI flags still win."
        ),
    )
    parser.add_argument("--host", default="", help="Bind host override.")
    parser.add_argument("--port", default="", help="Bind port override.")
    parser.add_argument("--hub-url", default="", help="Public Hub URL override.")
    parser.add_argument("--root", default="", help="Hub runtime root override.")
    parser.add_argument("--cluster-file", default="", help="FoundationDB cluster file override.")
    parser.add_argument("--ns", "--namespace", dest="namespace", default="", help="FoundationDB namespace override.")
    parser.add_argument("--backend", "--bridge-backend", dest="bridge_backend", default="", help="Bridge backend override.")
    parser.add_argument("--dev-chain-deployment-path", default="", help="Private deployment manifest path override for signing wallet paths.")
    parser.add_argument("--contracts-path", default="", help="Public contract discovery config path override.")
    parser.add_argument(
        "--allow-missing-bridge-signer",
        action="store_true",
        help="Allow startup from public contract config without mounted private bridge signer metadata.",
    )
    parser.add_argument(
        "--enable-smoke-bridge",
        action="store_true",
        help="Enable explicit admin-only smoke bridge mode that may load smoke_client wallet metadata.",
    )
    parser.add_argument("--chain-id", default="", help="Chain id override.")
    parser.add_argument("--chain-rpc-url", default="", help="Chain RPC URL override.")
    parser.add_argument("--print-command", action="store_true", help="Print the resolved exp-fDB Hub command instead of execing it.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    command = build_exp_fdb_hub_command(args)
    if args.print_command:
        print(shlex.join(command))
        return 0
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
