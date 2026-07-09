from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from main_computer.container_runtime import resolve_container_runtime
from typing import Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen

from .downloads import ensure_pip_wheel
from .install_root import copy_clean_tree, default_install_root, is_managed_install_root, safe_name
from .mathics import install_mathics_if_requested
from .process import run_command
from .python_runtime import tool_paths, verify_managed_python
from .venv import create_venv_without_pip, pip_install_project, seed_pip_from_wheel


MODE_DEFAULTS = {
    "unleashed": {
        "label": "Unleashed Mode",
        "runtime_profile": "test",
        "distribution_suffix": "unleashed",
        "port": 8765,
        "heartbeat_port": 8766,
        "onlyoffice_port": 18085,
        "docker_viewport_port": 18765,
        "hub_port": 18770,
        "hub_worker_port": 18771,
        "ethereum_rpc_port": 18545,
        "local_server_port_start": 18080,
        "local_server_generated_port_start": 18100,
        "local_server_generated_port_end": 18199,
        "guidance_level": "developer",
    },
    "debug": {
        "label": "Debug",
        "runtime_profile": "test",
        "distribution_suffix": "debug",
        "port": 28865,
        "heartbeat_port": 28866,
        "onlyoffice_port": 18085,
        "docker_viewport_port": 28765,
        "hub_port": 28770,
        "hub_worker_port": 28771,
        "ethereum_rpc_port": 28545,
        "local_server_port_start": 28080,
        "local_server_generated_port_start": 28100,
        "local_server_generated_port_end": 28199,
        "guidance_level": "debug",
    },
    "safe": {
        "label": "Safe Mode",
        "runtime_profile": "prod",
        "distribution_suffix": "safe",
        "port": 38865,
        "heartbeat_port": 38866,
        "onlyoffice_port": 18085,
        "docker_viewport_port": 38765,
        "hub_port": 38770,
        "hub_worker_port": 38771,
        "ethereum_rpc_port": 38545,
        "local_server_port_start": 38080,
        "local_server_generated_port_start": 38100,
        "local_server_generated_port_end": 38199,
        "guidance_level": "guided",
    },
}

MODE_ALIASES = {
    "unleashed": "unleashed",
    "unleashed mode": "unleashed",
    "unleashed-mode": "unleashed",
    "debug": "debug",
    "safe": "safe",
    "safe mode": "safe",
    "safe-mode": "safe",
}


def normalize_mode(value: str) -> tuple[str, str]:
    key = value.strip().lower().replace("_", " ")
    key = " ".join(key.split())
    if key not in MODE_ALIASES:
        raise argparse.ArgumentTypeError(f"Unsupported mode: {value}")
    mode_key = MODE_ALIASES[key]
    return mode_key, str(MODE_DEFAULTS[mode_key]["label"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap_main_computer.py",
        description="Python-owned golden path installer for Main Computer.",
    )
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--runtime-profile", choices=["test", "prod"], default="test")
    parser.add_argument("--mode", default="Unleashed")
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--runner-name", default="run-main-computer.ps1")
    parser.add_argument("--instance-name", default="")
    parser.add_argument("--instance-store-root", type=Path)
    parser.add_argument("--venv-path", type=Path)
    parser.add_argument("--wsl-command", default="wsl.exe")
    parser.add_argument("--executor-distribution", default="")
    parser.add_argument("--port", type=int)
    parser.add_argument("--heartbeat-port", type=int, default=0)
    parser.add_argument("--safe-port", type=int, default=38865)
    parser.add_argument("--safe-heartbeat-port", type=int, default=38866)
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--start-timeout-seconds", type=int, default=90)
    parser.add_argument("--onlyoffice-mode", choices=["auto", "disabled", "docker"], default="auto")
    parser.add_argument("--container-runtime", choices=["docker", "podman"], default="docker")
    parser.add_argument("--local-server-mode", choices=["auto", "disabled", "required"], default="auto")
    parser.add_argument("--local-coolify-mode", choices=["auto", "disabled", "required"], default="auto")
    parser.add_argument("--onlyoffice-port", type=int)
    parser.add_argument("--install-onlyoffice", action="store_true")
    parser.add_argument("--skip-wsl-runtime-install", action="store_true")
    parser.add_argument("--build-wsl-runtime-if-missing", action="store_true", default=True)
    parser.add_argument("--reset-wsl-runtime", action="store_true")
    parser.add_argument("--skip-executor-smoke", action="store_true")
    parser.add_argument("--wsl-firewall-mode", choices=["auto", "disabled", "required"], default="auto")
    parser.add_argument("--precheck-only", action="store_true")
    parser.add_argument("--mathics-install-mode", choices=["disabled", "auto", "required"], default="disabled")
    parser.add_argument("--managed-python", type=Path)
    parser.add_argument("--python-nuget-version", default="3.12.10")
    parser.add_argument("--pip-wheel-version", default="25.0.1")
    parser.add_argument("--no-python-download", action="store_true")
    parser.add_argument("--auto-force-install", action="store_true")
    parser.add_argument("--skip-install-root-copy", action="store_true")
    parser.add_argument("--skip-runner-creation", action="store_true")
    parser.add_argument("--skip-app-start", action="store_true")
    parser.add_argument(
        "--start-after-install",
        action="store_true",
        help="Opt in to launching the installed tree after preparation; default is to leave startup to start_v2.bat/start.bat.",
    )
    parser.add_argument("--skip-mathics-check", action="store_true")
    parser.add_argument("--allow-foreign-port-listener", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def default_instance_name(runtime_profile: str, mode_key: str, install_root: Path) -> str:
    """Return the default local identity for an installed tree.

    The default identity is product-scoped instead of checkout-name scoped.
    Directory labels such as ``test`` or legacy generated suffixes such as
    ``test-debug`` are build/source details, not runtime identity.  Explicit
    ``--instance-name`` still bypasses this helper when an operator needs a
    custom instance.
    """

    raw_install_root = os.fspath(install_root)
    install_leaf_text = raw_install_root.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    install_leaf = safe_name(install_leaf_text).replace("_", "-")
    profile_name = safe_name(runtime_profile).replace("_", "-") or "test"
    mode_name = safe_name(mode_key).replace("_", "-") or "unleashed"

    generated_suffix = f"-{profile_name}-{mode_name}"
    if install_leaf.endswith(generated_suffix):
        install_leaf = install_leaf[: -len(generated_suffix)].strip("-")

    mode_suffix = f"-{mode_name}"
    if install_leaf.endswith(mode_suffix):
        install_leaf = install_leaf[: -len(mode_suffix)].strip("-")

    generic_names = {
        "",
        "debug",
        "safe",
        "unleashed",
        "test",
        "prod",
        "main-computer",
        "main-computer-test",
        "main-computer-prod",
    }
    if install_leaf and install_leaf not in generic_names:
        return install_leaf

    return "main-computer"


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def resolve_install_root(args: argparse.Namespace, mode_key: str) -> tuple[Path, str, Path]:
    """Resolve the install target and record how it was selected."""

    managed_default = default_install_root(args.repo_root, args.runtime_profile, mode_key).resolve()

    if args.install_root is not None:
        return args.install_root.expanduser().resolve(), "--install-root", managed_default

    mc_install = _env_path("MC_INSTALL")
    if mc_install is not None:
        return mc_install, "env:MC_INSTALL", managed_default

    main_computer_install = _env_path("MAIN_COMPUTER_INSTALL_ROOT")
    if main_computer_install is not None:
        return main_computer_install, "env:MAIN_COMPUTER_INSTALL_ROOT", managed_default

    return managed_default, "managed-default", managed_default


def instance_paths(
    *,
    instance_store_root: Path | None,
    instance_name: str,
    mode_key: str,
    venv_path: Path | None = None,
) -> dict[str, Path]:
    base = instance_store_root.resolve() if instance_store_root else (tool_paths().root / "instances").resolve()
    instance_root = base / safe_name(instance_name)
    state_root = instance_root / mode_key
    venv_root = venv_path.expanduser().resolve() if venv_path else state_root / "venv"
    return {
        "base_store": base,
        "instance_root": instance_root,
        "state_root": state_root,
        "control_root": state_root / "control",
        "venv_root": venv_root,
        "log_dir": state_root / "logs",
        "manifest_dir": instance_root / "manifests",
    }


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ps_single_quoted(value: str | os.PathLike[str]) -> str:
    return "'" + os.fspath(value).replace("'", "''") + "'"


def _container_cli_available() -> bool:
    runtime = resolve_container_runtime(probe=False)
    command = runtime.container_command[0] if runtime.container_command else "docker"
    return shutil.which(command) is not None or Path(command).exists()


def _effective_onlyoffice_mode(value: str) -> str:
    """Resolve ONLYOFFICE auto mode the same way the legacy installer did."""

    requested = str(value or "auto").strip().lower()
    if requested == "disabled":
        return "disabled"
    if requested == "docker":
        if not _container_cli_available():
            raise RuntimeError("ONLYOFFICE mode 'docker' requires a Docker-compatible container CLI, but none was found on PATH.")
        return "docker"
    if requested == "auto":
        return "docker" if _container_cli_available() else "disabled"
    raise RuntimeError(f"Unsupported ONLYOFFICE mode: {value}")


def podman_compose_provider_path(venv_python: Path) -> Path:
    """Return the venv-owned podman-compose provider path for native Podman mode."""

    scripts_dir = venv_python.parent
    if scripts_dir.name.lower() == "scripts" or venv_python.name.lower().endswith(".exe"):
        return scripts_dir / "podman-compose.exe"
    return scripts_dir / "podman-compose"


def apply_podman_compose_provider_env(
    env: dict[str, str],
    *,
    container_runtime: str,
    venv_python: Path,
) -> None:
    """Pin Podman Compose to the tool installed into the Main Computer venv.

    Windows Podman may otherwise select Docker Desktop's docker-compose provider
    when Docker Desktop is installed.  Podman runtime must stay native Podman.
    """

    if str(container_runtime).strip().lower() != "podman":
        env.pop("PODMAN_COMPOSE_PROVIDER", None)
        return

    env["PODMAN_COMPOSE_PROVIDER"] = str(podman_compose_provider_path(venv_python))


def _bool_text(value: bool) -> str:
    return "1" if value else "0"


COOLIFY_MODE_PORTS = {
    "unleashed": {
        "app": 17056,
        "soketi": 17156,
        "soketi_terminal": 17256,
    },
    "debug": {
        "app": 27056,
        "soketi": 27156,
        "soketi_terminal": 27256,
    },
    "safe": {
        "app": 37056,
        "soketi": 37156,
        "soketi_terminal": 37256,
    },
}


def _docker_name(value: str, *, max_length: int = 63, fallback: str = "main-computer") -> str:
    candidate = safe_name(value).replace("_", "-").strip("-")
    if not candidate:
        candidate = fallback
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip("-")
    return candidate or fallback


def _docker_mode_key(mode_key: str) -> str:
    return _docker_name(mode_key, max_length=32, fallback="unleashed")


def _mode_scoped_coolify_project(instance_name: str, mode_key: str) -> str:
    del instance_name
    mode_segment = _docker_mode_key(mode_key)
    return _docker_name(
        f"main-computer-coolify-{mode_segment}",
        max_length=63,
        fallback="main-computer-coolify-unleashed",
    )


def _mode_scoped_dev_compose_project(instance_name: str, mode_key: str) -> str:
    del instance_name
    mode_segment = _docker_mode_key(mode_key)
    return _docker_name(
        f"main-computer-{mode_segment}",
        max_length=63,
        fallback="main-computer-unleashed",
    )


def _mode_scoped_local_platform_project(instance_name: str, mode_key: str) -> str:
    del instance_name
    mode_segment = _docker_mode_key(mode_key)
    return _docker_name(
        f"main-computer-local-platform-{mode_segment}",
        max_length=63,
        fallback="main-computer-local-platform-unleashed",
    )


def build_mode_profiles(
    *,
    args: argparse.Namespace,
    install_root: Path,
    instance_name: str,
    instance_store_root: Path,
    active_mode_key: str,
    active_venv_python: Path,
) -> dict[str, dict[str, object]]:
    """Build the runner's three-mode profile table from the regular installer shape."""

    shared_onlyoffice_port = args.onlyoffice_port if args.onlyoffice_port is not None else 18085
    profiles: dict[str, dict[str, object]] = {}
    for key in ("unleashed", "debug", "safe"):
        defaults = dict(MODE_DEFAULTS[key])
        defaults["onlyoffice_port"] = shared_onlyoffice_port
        if key == "safe":
            defaults["port"] = args.safe_port
            defaults["heartbeat_port"] = args.safe_heartbeat_port

        if key == active_mode_key:
            if args.port is not None:
                defaults["port"] = args.port
            if args.heartbeat_port and args.heartbeat_port > 0:
                defaults["heartbeat_port"] = args.heartbeat_port
        state_root = instance_store_root / safe_name(instance_name) / key
        venv_python = active_venv_python if key == active_mode_key else state_root / "venv" / "Scripts" / "python.exe"
        distribution = (
            args.executor_distribution.strip()
            if key == active_mode_key and args.executor_distribution.strip()
            else f"MainComputer-{safe_name(instance_name).replace('_', '-')}-{defaults['distribution_suffix']}"
        )

        coolify_ports = COOLIFY_MODE_PORTS[key]
        profiles[key] = {
            "key": key,
            "instance_name": instance_name,
            "label": defaults["label"],
            "runtime_profile": defaults["runtime_profile"],
            "guidance_level": defaults["guidance_level"],
            "port": defaults["port"],
            "heartbeat_port": defaults["heartbeat_port"],
            "python": venv_python,
            "distribution": distribution,
            "instance_root": instance_store_root / safe_name(instance_name),
            "state_root": state_root,
            "control_root": state_root / "control",
            "executor_root": state_root / "executor",
            "wsl_runtime_root": state_root / "wsl",
            "onlyoffice_port": defaults["onlyoffice_port"],
            "dev_compose_project": _mode_scoped_dev_compose_project(instance_name, key),
            "docker_viewport_port": defaults["docker_viewport_port"],
            "hub_port": defaults["hub_port"],
            "hub_worker_port": defaults["hub_worker_port"],
            "ethereum_rpc_port": defaults["ethereum_rpc_port"],
            "onlyoffice_project": "main-computer-onlyoffice",
            "local_server_project": _mode_scoped_local_platform_project(instance_name, key),
            "local_server_registry": state_root / "local-platform" / "sites.json",
            "local_server_compose": state_root / "local-platform" / "docker-compose.websites.yml",
            "local_server_port_start": defaults["local_server_port_start"],
            "local_server_generated_port_start": defaults["local_server_generated_port_start"],
            "local_server_generated_port_end": defaults["local_server_generated_port_end"],
            "coolify_project": _mode_scoped_coolify_project(instance_name, key),
            "coolify_state_root": state_root / "coolify-local-docker",
            "coolify_port": coolify_ports["app"],
            "coolify_soketi_port": coolify_ports["soketi"],
            "coolify_soketi_terminal_port": coolify_ports["soketi_terminal"],
            "firewall_rule": f"MainComputer-{safe_name(instance_name).replace('_', '-')}-{key}-WslOnly",
            "shared_dependencies": ["Ollama", "Gitea", "ONLYOFFICE", "Windows host services", "WSL host feature"],
        }

    return profiles


def _mode_assignment(prefix: str, profile: dict[str, object]) -> str:
    return "\n".join(
        [
            f"${prefix}Port = {profile['port']}",
            f"${prefix}HeartbeatPort = {profile['heartbeat_port']}",
            f"${prefix}DevComposeProject = {ps_single_quoted(profile['dev_compose_project'])}",
            f"${prefix}DockerViewportPort = {profile['docker_viewport_port']}",
            f"${prefix}HubPort = {profile['hub_port']}",
            f"${prefix}HubWorkerPort = {profile['hub_worker_port']}",
            f"${prefix}EthereumRpcPort = {profile['ethereum_rpc_port']}",
            f"${prefix}Python = {ps_single_quoted(profile['python'])}",
            f"${prefix}Distribution = {ps_single_quoted(profile['distribution'])}",
            f"${prefix}ControlRoot = {ps_single_quoted(profile['control_root'])}",
            f"${prefix}StateRoot = {ps_single_quoted(profile['state_root'])}",
            f"${prefix}ExecutorRoot = {ps_single_quoted(profile['executor_root'])}",
            f"${prefix}WslRuntimeRoot = {ps_single_quoted(profile['wsl_runtime_root'])}",
            f"${prefix}OnlyOfficePort = {profile['onlyoffice_port']}",
            f"${prefix}OnlyOfficeProject = {ps_single_quoted(profile['onlyoffice_project'])}",
            f"${prefix}LocalServerProject = {ps_single_quoted(profile['local_server_project'])}",
            f"${prefix}LocalServerRegistry = {ps_single_quoted(profile['local_server_registry'])}",
            f"${prefix}LocalServerCompose = {ps_single_quoted(profile['local_server_compose'])}",
            f"${prefix}LocalServerPortStart = {profile['local_server_port_start']}",
            f"${prefix}LocalServerGeneratedPortStart = {profile['local_server_generated_port_start']}",
            f"${prefix}LocalServerGeneratedPortEnd = {profile['local_server_generated_port_end']}",
            f"${prefix}CoolifyProject = {ps_single_quoted(profile['coolify_project'])}",
            f"${prefix}CoolifyStateRoot = {ps_single_quoted(profile['coolify_state_root'])}",
            f"${prefix}CoolifyPort = {profile['coolify_port']}",
            f"${prefix}CoolifySoketiPort = {profile['coolify_soketi_port']}",
            f"${prefix}CoolifySoketiTerminalPort = {profile['coolify_soketi_terminal_port']}",
        ]
    )


def write_runner(
    *,
    install_root: Path,
    runner_name: str,
    mode_key: str,
    mode_label: str,
    instance_name: str,
    mode_profiles: dict[str, dict[str, object]],
    wsl_command: str,
    workspace: Path,
    bind_host: str,
    start_timeout_seconds: int,
    onlyoffice_mode: str,
    container_runtime: str,
    local_server_mode: str,
    local_coolify_mode: str,
    skip_mathics_check: bool,
    skip_wsl_runtime_install: bool,
    build_wsl_runtime_if_missing: bool,
    reset_wsl_runtime: bool,
    skip_executor_smoke: bool,
    allow_foreign_port_listener: bool,
) -> Path:
    """Write a runner that duplicates the regular bootstrapper's mode/action surface."""

    runner_path = install_root / runner_name
    skip_mathics_default = _bool_text(skip_mathics_check)
    allow_foreign_default = _bool_text(allow_foreign_port_listener)
    effective_onlyoffice_mode = _effective_onlyoffice_mode(onlyoffice_mode)
    onlyoffice_enabled = "0" if effective_onlyoffice_mode == "disabled" else "1"
    local_server_enabled = "0" if local_server_mode == "disabled" else "1"
    local_coolify_enabled = "0" if local_coolify_mode == "disabled" else "1"
    skip_wsl_runtime_install_default = _bool_text(skip_wsl_runtime_install)
    build_wsl_runtime_if_missing_default = _bool_text(build_wsl_runtime_if_missing)
    reset_wsl_runtime_default = _bool_text(reset_wsl_runtime)
    skip_executor_smoke_default = _bool_text(skip_executor_smoke)

    runner = f"""# Generated by tools\\bootstrap_main_computer.py.
# This duplicates the regular bootstrap-main-computer-windows.ps1 runner shape
# while keeping the install/update brain in Python.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "run", "restart", "status", "stop", "shutdown", "install", "install-run", "smoke", "check")]
    [string]$Action = "start",

    [ValidateSet("", "Unleashed", "Unleashed Mode", "Debug", "Safe", "Safe Mode")]
    [string]$Mode = "",

    [switch]$SkipMathicsCheck,
    [switch]$AllowForeignPortListener
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PinnedMode = {ps_single_quoted(mode_label)}
$InstanceName = {ps_single_quoted(instance_name)}
$ConfiguredWsl = {ps_single_quoted(wsl_command)}
$ConfiguredWorkspace = {ps_single_quoted(workspace)}
$BindHost = {ps_single_quoted(bind_host)}
$StartTimeoutSeconds = {start_timeout_seconds}
$DefaultSkipMathicsCheck = {ps_single_quoted(skip_mathics_default)}
$DefaultAllowForeignPortListener = {ps_single_quoted(allow_foreign_default)}

{_mode_assignment("Unleashed", mode_profiles["unleashed"])}

{_mode_assignment("Debug", mode_profiles["debug"])}

{_mode_assignment("Safe", mode_profiles["safe"])}

$OnlyOfficeEnabled = {ps_single_quoted(onlyoffice_enabled)}
$OnlyOfficeMode = {ps_single_quoted(effective_onlyoffice_mode)}
$ContainerRuntime = {ps_single_quoted(container_runtime)}
$OnlyOfficeJwtSecret = {ps_single_quoted(os.environ.get("MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET", "main-computer-onlyoffice-local-secret"))}
$LocalServerEnabled = {ps_single_quoted(local_server_enabled)}
$LocalCoolifyEnabled = {ps_single_quoted(local_coolify_enabled)}
$SkipWslRuntimeInstallDefault = {ps_single_quoted(skip_wsl_runtime_install_default)}
$BuildWslRuntimeIfMissingDefault = {ps_single_quoted(build_wsl_runtime_if_missing_default)}
$ResetWslRuntimeDefault = {ps_single_quoted(reset_wsl_runtime_default)}
$SkipExecutorSmokeDefault = {ps_single_quoted(skip_executor_smoke_default)}

function Resolve-RunnerMode {{
    param([Parameter(Mandatory = $true)][string]$ModeName)

    $normalized = ($ModeName.Trim().ToLowerInvariant() -replace "\\s+", "-")
    switch ($normalized) {{
        "unleashed" {{ return [pscustomobject]@{{ Key = "unleashed"; Label = "Unleashed Mode"; GuidanceLevel = "developer"; Port = $UnleashedPort; HeartbeatPort = $UnleashedHeartbeatPort; DevComposeProject = $UnleashedDevComposeProject; DockerViewportPort = $UnleashedDockerViewportPort; HubPort = $UnleashedHubPort; HubWorkerPort = $UnleashedHubWorkerPort; EthereumRpcPort = $UnleashedEthereumRpcPort; PythonPath = $UnleashedPython; Distribution = $UnleashedDistribution; ControlRoot = $UnleashedControlRoot; StateRoot = $UnleashedStateRoot; ExecutorRoot = $UnleashedExecutorRoot; WslRuntimeRoot = $UnleashedWslRuntimeRoot; OnlyOfficePort = $UnleashedOnlyOfficePort; OnlyOfficeProject = $UnleashedOnlyOfficeProject; LocalServerProject = $UnleashedLocalServerProject; LocalServerRegistry = $UnleashedLocalServerRegistry; LocalServerCompose = $UnleashedLocalServerCompose; LocalServerPortStart = $UnleashedLocalServerPortStart; LocalServerGeneratedPortStart = $UnleashedLocalServerGeneratedPortStart; LocalServerGeneratedPortEnd = $UnleashedLocalServerGeneratedPortEnd; CoolifyProject = $UnleashedCoolifyProject; CoolifyStateRoot = $UnleashedCoolifyStateRoot; CoolifyPort = $UnleashedCoolifyPort; CoolifySoketiPort = $UnleashedCoolifySoketiPort; CoolifySoketiTerminalPort = $UnleashedCoolifySoketiTerminalPort }} }}
        "unleashed-mode" {{ return (Resolve-RunnerMode -ModeName "Unleashed") }}
        "debug" {{ return [pscustomobject]@{{ Key = "debug"; Label = "Debug"; GuidanceLevel = "debug"; Port = $DebugPort; HeartbeatPort = $DebugHeartbeatPort; DevComposeProject = $DebugDevComposeProject; DockerViewportPort = $DebugDockerViewportPort; HubPort = $DebugHubPort; HubWorkerPort = $DebugHubWorkerPort; EthereumRpcPort = $DebugEthereumRpcPort; PythonPath = $DebugPython; Distribution = $DebugDistribution; ControlRoot = $DebugControlRoot; StateRoot = $DebugStateRoot; ExecutorRoot = $DebugExecutorRoot; WslRuntimeRoot = $DebugWslRuntimeRoot; OnlyOfficePort = $DebugOnlyOfficePort; OnlyOfficeProject = $DebugOnlyOfficeProject; LocalServerProject = $DebugLocalServerProject; LocalServerRegistry = $DebugLocalServerRegistry; LocalServerCompose = $DebugLocalServerCompose; LocalServerPortStart = $DebugLocalServerPortStart; LocalServerGeneratedPortStart = $DebugLocalServerGeneratedPortStart; LocalServerGeneratedPortEnd = $DebugLocalServerGeneratedPortEnd; CoolifyProject = $DebugCoolifyProject; CoolifyStateRoot = $DebugCoolifyStateRoot; CoolifyPort = $DebugCoolifyPort; CoolifySoketiPort = $DebugCoolifySoketiPort; CoolifySoketiTerminalPort = $DebugCoolifySoketiTerminalPort }} }}
        "safe" {{ return [pscustomobject]@{{ Key = "safe"; Label = "Safe Mode"; GuidanceLevel = "guided"; Port = $SafePort; HeartbeatPort = $SafeHeartbeatPort; DevComposeProject = $SafeDevComposeProject; DockerViewportPort = $SafeDockerViewportPort; HubPort = $SafeHubPort; HubWorkerPort = $SafeHubWorkerPort; EthereumRpcPort = $SafeEthereumRpcPort; PythonPath = $SafePython; Distribution = $SafeDistribution; ControlRoot = $SafeControlRoot; StateRoot = $SafeStateRoot; ExecutorRoot = $SafeExecutorRoot; WslRuntimeRoot = $SafeWslRuntimeRoot; OnlyOfficePort = $SafeOnlyOfficePort; OnlyOfficeProject = $SafeOnlyOfficeProject; LocalServerProject = $SafeLocalServerProject; LocalServerRegistry = $SafeLocalServerRegistry; LocalServerCompose = $SafeLocalServerCompose; LocalServerPortStart = $SafeLocalServerPortStart; LocalServerGeneratedPortStart = $SafeLocalServerGeneratedPortStart; LocalServerGeneratedPortEnd = $SafeLocalServerGeneratedPortEnd; CoolifyProject = $SafeCoolifyProject; CoolifyStateRoot = $SafeCoolifyStateRoot; CoolifyPort = $SafeCoolifyPort; CoolifySoketiPort = $SafeCoolifySoketiPort; CoolifySoketiTerminalPort = $SafeCoolifySoketiTerminalPort }} }}
        "safe-mode" {{ return (Resolve-RunnerMode -ModeName "Safe") }}
    }}

    throw "Unknown Main Computer runner mode: $ModeName"
}}

function Set-RunnerEnvironment {{
    param([Parameter(Mandatory = $true)]$SelectedMode)

    $workspace = if ([string]::IsNullOrWhiteSpace($ConfiguredWorkspace)) {{ $InstallRoot }} else {{ $ConfiguredWorkspace }}

    $env:MAIN_COMPUTER_PYTHON = $SelectedMode.PythonPath
    $env:MAIN_COMPUTER_WORKSPACE = $workspace
    $env:MAIN_COMPUTER_INSTALL_MODE = $SelectedMode.Key
    $env:MAIN_COMPUTER_MODE_LABEL = $SelectedMode.Label
    $env:MAIN_COMPUTER_GUIDANCE_LEVEL = $SelectedMode.GuidanceLevel
    $env:MAIN_COMPUTER_SAFE_MODE = if ($SelectedMode.Key -eq "safe") {{ "1" }} else {{ "0" }}
    $env:MAIN_COMPUTER_CONTAINER_RUNTIME = $ContainerRuntime
    if ($ContainerRuntime -eq "podman") {{
        $env:PODMAN_COMPOSE_PROVIDER = Join-Path (Split-Path -Parent $SelectedMode.PythonPath) "podman-compose.exe"
    }}
    else {{
        Remove-Item -LiteralPath "Env:\PODMAN_COMPOSE_PROVIDER" -ErrorAction SilentlyContinue
    }}
    $env:MAIN_COMPUTER_INSTANCE_NAME = $InstanceName
    $env:MAIN_COMPUTER_STATE_ROOT = $SelectedMode.StateRoot
    $env:MAIN_COMPUTER_CONTROL_ROOT = $SelectedMode.ControlRoot
    $env:MAIN_COMPUTER_CONTROL_PORT = "$($SelectedMode.Port)"
    $env:MAIN_COMPUTER_HEARTBEAT_PORT = "$($SelectedMode.HeartbeatPort)"
    $env:MAIN_COMPUTER_EXECUTOR_ENABLED = "1"
    $env:MAIN_COMPUTER_EXECUTOR_BACKEND = "wsl"
    $env:MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION = $SelectedMode.Distribution
    $env:MAIN_COMPUTER_EXECUTOR_WSL_COMMAND = $ConfiguredWsl
    $env:MAIN_COMPUTER_EXECUTOR_WSL_RUNTIME_ROOT = $SelectedMode.WslRuntimeRoot
    $env:MAIN_COMPUTER_EXECUTOR_ROOT = $SelectedMode.ExecutorRoot
    $env:MAIN_COMPUTER_PATH_MODE = "local"
    $env:MAIN_COMPUTER_HOST_OS = "windows"
    $env:MAIN_COMPUTER_GITEA_SCOPE = "shared-machine"
    $env:MAIN_COMPUTER_GITEA_ROOT_URL = "http://127.0.0.1:3000/"
    $env:MAIN_COMPUTER_GITEA_HTTP_PORT = "3000"
    $env:MAIN_COMPUTER_GITEA_COMPOSE_PROJECT = "main-computer-gitea"
    $env:MAIN_COMPUTER_DEV_COMPOSE_PROJECT = $SelectedMode.DevComposeProject
    $env:MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT = $SelectedMode.DevComposeProject
    $env:MAIN_COMPUTER_DOCKER_VIEWPORT_PORT = "$($SelectedMode.DockerViewportPort)"
    $env:MAIN_COMPUTER_HUB_PORT = "$($SelectedMode.HubPort)"
    $env:MAIN_COMPUTER_HUB_WORKER_PORT = "$($SelectedMode.HubWorkerPort)"
    $env:MAIN_COMPUTER_HUB_URL = "http://127.0.0.1:$($SelectedMode.HubPort)"
    $env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
    $env:MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL = "http://127.0.0.1:$($SelectedMode.EthereumRpcPort)"
    $env:MAIN_COMPUTER_ENERGY_CHAIN_ID = "42424242"

    if ($LocalServerEnabled -eq "1") {{
        $env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED = "1"
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_MODE = $SelectedMode.Key
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT = $SelectedMode.LocalServerProject
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH = $SelectedMode.LocalServerRegistry
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH = $SelectedMode.LocalServerCompose
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START = "$($SelectedMode.LocalServerPortStart)"
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START = "$($SelectedMode.LocalServerGeneratedPortStart)"
        $env:MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END = "$($SelectedMode.LocalServerGeneratedPortEnd)"
        $env:MAIN_COMPUTER_LOCAL_SERVER_URL = "http://127.0.0.1:$($SelectedMode.LocalServerPortStart)/"
    }}
    else {{
        $env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED = "0"
    }}

    if ($LocalCoolifyEnabled -eq "1") {{
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "1"
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_URL = "http://127.0.0.1:$($SelectedMode.CoolifyPort)"
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_REF = "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE = Join-Path $SelectedMode.CoolifyStateRoot "api-token.txt"
        $env:MAIN_COMPUTER_COOLIFY_PROJECT = $SelectedMode.CoolifyProject
        $env:MAIN_COMPUTER_COOLIFY_STATE_DIR = $SelectedMode.CoolifyStateRoot
        $env:MAIN_COMPUTER_COOLIFY_APP_PORT = "$($SelectedMode.CoolifyPort)"
        $env:MAIN_COMPUTER_COOLIFY_SOKETI_PORT = "$($SelectedMode.CoolifySoketiPort)"
        $env:MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT = "$($SelectedMode.CoolifySoketiTerminalPort)"
        if (Test-Path -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE -PathType Leaf) {{
            $tokenValue = ""
            foreach ($line in (Get-Content -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE)) {{
                if ($line -match '^\\s*token\\s*=\\s*(.+?)\\s*$') {{
                    $tokenValue = $Matches[1].Trim()
                    break
                }}
            }}
            if ([string]::IsNullOrWhiteSpace($tokenValue)) {{
                $rawTokenFile = (Get-Content -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE -Raw).Trim()
                if (
                    -not [string]::IsNullOrWhiteSpace($rawTokenFile) -and
                    $rawTokenFile -notmatch "`n" -and
                    $rawTokenFile -notmatch "=" -and
                    $rawTokenFile -notmatch '^\\s*#'
                ) {{
                    $tokenValue = $rawTokenFile
                }}
            }}
            if (-not [string]::IsNullOrWhiteSpace($tokenValue)) {{
                $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN = $tokenValue
            }}
        }}
    }}
    else {{
        $env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED = "0"
    }}

    if ($OnlyOfficeEnabled -eq "1") {{
        $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED = "1"
        $env:MAIN_COMPUTER_ONLYOFFICE_MODE = $OnlyOfficeMode
        $env:MAIN_COMPUTER_ONLYOFFICE_PORT = "$($SelectedMode.OnlyOfficePort)"
        $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT = $SelectedMode.OnlyOfficeProject
        $env:MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME = "main-computer-onlyoffice-documentserver"
        $env:MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL = "http://127.0.0.1:$($SelectedMode.OnlyOfficePort)"
        $env:MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL = "http://127.0.0.1:$($SelectedMode.OnlyOfficePort)"
        $env:MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL = "http://host.docker.internal:$($SelectedMode.Port)"
        if (-not [string]::IsNullOrWhiteSpace($OnlyOfficeJwtSecret)) {{
            $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = $OnlyOfficeJwtSecret
        }}
    }}
    else {{
        $env:MAIN_COMPUTER_ONLYOFFICE_ENABLED = "0"
    }}
}}

function Resolve-ControlAction {{
    param([Parameter(Mandatory = $true)][string]$RequestedAction)
    switch ($RequestedAction) {{
        "run" {{ return "start" }}
        "stop" {{ return "shutdown" }}
        "install" {{ return "start" }}
        "install-run" {{ return "start" }}
        "smoke" {{ return "status" }}
        default {{ return $RequestedAction }}
    }}
}}

function Add-CommonControlSwitches {{
    param([Parameter(Mandatory = $true)][hashtable]$Params)

    if ($SkipMathicsCheck -or $DefaultSkipMathicsCheck -eq "1") {{
        $Params.SkipMathicsCheck = $true
    }}
    if ($AllowForeignPortListener -or $DefaultAllowForeignPortListener -eq "1") {{
        $Params.AllowForeignPortListener = $true
    }}
}}

function Test-LocalTcpPortOpen {{
    param([Parameter(Mandatory = $true)][int]$Port)

    try {{
        $client = [System.Net.Sockets.TcpClient]::new()
        try {{
            $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
            if (-not $async.AsyncWaitHandle.WaitOne(500, $false)) {{
                return $false
            }}
            $client.EndConnect($async)
            return $true
        }}
        finally {{
            $client.Close()
        }}
    }}
    catch {{
        return $false
    }}
}}

function Invoke-JsonPost {{
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [hashtable]$Body = @{{}}
    )

    try {{
        Invoke-RestMethod -Method POST -Uri $Uri -TimeoutSec 3 -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 8) | Out-Null
        return $true
    }}
    catch {{
        return $false
    }}
}}


function Add-ModeCheckResult {{
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$State,
        [string]$Details = ""
    )

    if ($State -eq "FAIL") {{
        $script:ModeCheckFailures += 1
    }}
    elseif ($State -eq "WARN") {{
        $script:ModeCheckWarnings += 1
    }}

    if ([string]::IsNullOrWhiteSpace($Details)) {{
        Write-Host ("{{0}}: {{1}}" -f $Name, $State)
    }}
    else {{
        Write-Host ("{{0}}: {{1}} - {{2}}" -f $Name, $State, $Details)
    }}
}}

function Test-QuickHttpGet {{
    param([Parameter(Mandatory = $true)][string]$Uri)

    try {{
        Invoke-WebRequest -UseBasicParsing -Method GET -Uri $Uri -TimeoutSec 2 | Out-Null
        return $true
    }}
    catch {{
        return $false
    }}
}}

function Test-WslDistributionKnown {{
    param([Parameter(Mandatory = $true)][string]$Distribution)

    try {{
        $listed = & $ConfiguredWsl --list --quiet 2>$null
        if ($LASTEXITCODE -ne 0) {{
            return $false
        }}
        foreach ($line in @($listed)) {{
            if (($line.Trim()) -eq $Distribution) {{
                return $true
            }}
        }}
    }}
    catch {{
        return $false
    }}
    return $false
}}

function Test-DockerResponding {{
    $docker = Get-Command "docker" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $docker) {{
        return $false
    }}
    try {{
        & $docker.Source ps --format "{{{{.Names}}}}" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }}
    catch {{
        return $false
    }}
}}

function Get-ConfiguredGiteaPort {{
    $rootUrl = $env:MAIN_COMPUTER_GITEA_ROOT_URL
    if ([string]::IsNullOrWhiteSpace($rootUrl)) {{
        $rootUrl = "http://127.0.0.1:3000/"
    }}
    try {{
        $uri = [uri]$rootUrl
        if ($uri.Port -gt 0) {{
            return [int]$uri.Port
        }}
    }}
    catch {{
    }}
    return 3000
}}

function Ensure-SharedGiteaInstalledIfMissing {{
    $giteaPort = Get-ConfiguredGiteaPort
    if (Test-LocalTcpPortOpen -Port $giteaPort) {{
        Write-Host ("Shared Gitea already present on port {{0}}; installer/start path will not recreate it." -f $giteaPort)
        return
    }}

    $compose = Join-Path $InstallRoot "docker-compose.gitea.yml"
    if (-not (Test-Path -LiteralPath $compose -PathType Leaf)) {{
        Write-Warning "Shared Gitea compose file is missing: $compose"
        return
    }}

    $docker = Get-Command "docker" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $docker) {{
        Write-Warning "Docker CLI is not available; shared Gitea cannot be prepared."
        return
    }}

    $projectName = $env:MAIN_COMPUTER_GITEA_COMPOSE_PROJECT
    if ([string]::IsNullOrWhiteSpace($projectName)) {{
        $projectName = "main-computer-gitea"
    }}

    $containerIds = @(& $docker.Source compose --project-name $projectName -f $compose ps -a -q gitea 2>$null)
    $containerExists = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($containerIds -join "").Trim()))
    if ($containerExists) {{
        Write-Host ("Shared Gitea container already exists but is not reachable on port {{0}}; starting existing container without reinstalling." -f $giteaPort)
        & $docker.Source compose --project-name $projectName -f $compose start gitea
    }}
    else {{
        Write-Host "Shared Gitea not found on this machine; installing machine-wide Gitea with docker-compose.gitea.yml."
        & $docker.Source compose --project-name $projectName -f $compose up -d gitea
    }}

    if ($LASTEXITCODE -ne 0) {{
        Write-Warning ("Shared Gitea preparation returned exit code {{0}}; Local Gitea publishing may be unavailable." -f $LASTEXITCODE)
    }}
}}

function Invoke-InstalledModeCheck {{
    param(
        [Parameter(Mandatory = $true)]$SelectedMode,
        [switch]$Soft
    )

    $script:ModeCheckFailures = 0
    $script:ModeCheckWarnings = 0

    Write-Host ""
    Write-Host "Main Computer quick installed environment check"
    Write-Host ("Mode: {{0}} [{{1}}]" -f $SelectedMode.Label, $SelectedMode.Key)
    Write-Host ("Install root: {{0}}" -f $InstallRoot)
    Write-Host "Shared services: Ollama, Gitea, and ONLYOFFICE are machine-wide. Mode services: WSL executor, Local Server, and Local Coolify."

    $manifest = Join-Path $InstallRoot "main-computer-install.json"
    $runtimeManifest = Join-Path $InstallRoot "runtime\main-computer-install.json"
    if ((Test-Path -LiteralPath $manifest -PathType Leaf) -or (Test-Path -LiteralPath $runtimeManifest -PathType Leaf)) {{
        Add-ModeCheckResult "Install manifest" "OK" "installed manifest found"
    }}
    else {{
        Add-ModeCheckResult "Install manifest" "FAIL" "main-computer-install.json is missing from the installed location"
    }}

    if (Test-Path -LiteralPath $SelectedMode.PythonPath -PathType Leaf) {{
        Add-ModeCheckResult "Python for mode" "OK" $SelectedMode.PythonPath
    }}
    else {{
        Add-ModeCheckResult "Python for mode" "FAIL" ("missing expected venv Python: {{0}}" -f $SelectedMode.PythonPath)
    }}

    $modeScript = switch ($SelectedMode.Key) {{
        "unleashed" {{ Join-Path $InstallRoot "dev-control.ps1" }}
        "debug" {{ Join-Path $InstallRoot "proto-dev\proto-dev.ps1" }}
        "safe" {{ Join-Path $InstallRoot "control-main-computer.ps1" }}
        default {{ "" }}
    }}
    if (-not [string]::IsNullOrWhiteSpace($modeScript) -and (Test-Path -LiteralPath $modeScript -PathType Leaf)) {{
        Add-ModeCheckResult "Mode runner dependency" "OK" $modeScript
    }}
    else {{
        Add-ModeCheckResult "Mode runner dependency" "FAIL" ("missing mode control script: {{0}}" -f $modeScript)
    }}

    $wslCommand = Get-Command $ConfiguredWsl -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $wslCommand -and (Test-Path -LiteralPath $ConfiguredWsl -PathType Leaf)) {{
        $wslCommand = [pscustomobject]@{{ Source = $ConfiguredWsl }}
    }}
    if ($null -eq $wslCommand) {{
        Add-ModeCheckResult "WSL command" "FAIL" ("not found: {{0}}" -f $ConfiguredWsl)
    }}
    elseif (Test-WslDistributionKnown -Distribution $SelectedMode.Distribution) {{
        Add-ModeCheckResult "WSL distro for mode" "OK" $SelectedMode.Distribution
    }}
    else {{
        Add-ModeCheckResult "WSL distro for mode" "FAIL" ("missing expected distro after install: {{0}}" -f $SelectedMode.Distribution)
    }}

    if (Test-DockerResponding) {{
        Add-ModeCheckResult "Docker" "OK" "docker is responding"
    }}
    else {{
        Add-ModeCheckResult "Docker" "FAIL" "docker is missing or not responding; mode-scoped Docker services cannot be checked"
    }}

    $ollamaBase = $env:OLLAMA_BASE_URL
    if ([string]::IsNullOrWhiteSpace($ollamaBase)) {{
        $ollamaBase = "http://127.0.0.1:11434"
    }}
    $ollamaBase = $ollamaBase.TrimEnd("/")
    if (Test-QuickHttpGet -Uri "$ollamaBase/api/tags") {{
        Add-ModeCheckResult "Ollama shared service" "OK" "$ollamaBase/api/tags"
    }}
    else {{
        Add-ModeCheckResult "Ollama shared service" "WARN" "not reachable; local model features will wait for the machine-wide Ollama service"
    }}

    $giteaPort = Get-ConfiguredGiteaPort
    $giteaRoot = $env:MAIN_COMPUTER_GITEA_ROOT_URL
    if ([string]::IsNullOrWhiteSpace($giteaRoot)) {{
        $giteaRoot = "http://127.0.0.1:3000/"
    }}
    $giteaHealth = $giteaRoot.TrimEnd("/") + "/api/healthz"
    if ((Test-QuickHttpGet -Uri $giteaHealth) -or (Test-LocalTcpPortOpen -Port $giteaPort)) {{
        Add-ModeCheckResult "Gitea shared service" "OK" ("machine-wide Gitea is reachable on port {{0}}" -f $giteaPort)
    }}
    else {{
        Add-ModeCheckResult "Gitea shared service" "FAIL" ("machine-wide Gitea is not reachable on port {{0}}; this should be one shared install, not one per Main Computer mode" -f $giteaPort)
    }}

    if ($env:MAIN_COMPUTER_ONLYOFFICE_ENABLED -eq "1") {{
        $onlyOfficePort = [int]$env:MAIN_COMPUTER_ONLYOFFICE_PORT
        if (Test-LocalTcpPortOpen -Port $onlyOfficePort) {{
            Add-ModeCheckResult "ONLYOFFICE shared service" "OK" ("machine-wide {{0}} is reachable on port {{1}}" -f $SelectedMode.OnlyOfficeProject, $onlyOfficePort)
        }}
        else {{
            Add-ModeCheckResult "ONLYOFFICE shared service" "FAIL" ("machine-wide ONLYOFFICE is not reachable on port {{0}}; this should be one shared install, not one per Main Computer mode" -f $onlyOfficePort)
        }}
    }}
    else {{
        Add-ModeCheckResult "ONLYOFFICE shared service" "SKIP" "disabled for this install"
    }}

    if ($env:MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED -eq "1") {{
        $coolifyPort = [int]$env:MAIN_COMPUTER_COOLIFY_APP_PORT
        if (Test-LocalTcpPortOpen -Port $coolifyPort) {{
            Add-ModeCheckResult "Local Coolify for mode" "OK" ("{{0}} on port {{1}}" -f $SelectedMode.CoolifyProject, $coolifyPort)
        }}
        else {{
            Add-ModeCheckResult "Local Coolify for mode" "FAIL" ("not reachable for {{0}}; expected project {{1}} on port {{2}}" -f $SelectedMode.Label, $SelectedMode.CoolifyProject, $coolifyPort)
        }}

        if (Test-Path -LiteralPath $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE -PathType Leaf) {{
            Add-ModeCheckResult "Local Coolify token" "OK" $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE
        }}
        else {{
            Add-ModeCheckResult "Local Coolify token" "WARN" ("missing token file: {{0}}" -f $env:MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE)
        }}
    }}
    else {{
        Add-ModeCheckResult "Local Coolify for mode" "SKIP" "disabled for this install"
    }}

    if ($env:MAIN_COMPUTER_LOCAL_SERVER_ENABLED -eq "1") {{
        $registryParent = Split-Path -Parent $env:MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH
        if (-not [string]::IsNullOrWhiteSpace($registryParent) -and (Test-Path -LiteralPath $registryParent -PathType Container)) {{
            Add-ModeCheckResult "Local Server state for mode" "OK" $registryParent
        }}
        else {{
            Add-ModeCheckResult "Local Server state for mode" "WARN" ("state directory not present yet: {{0}}" -f $registryParent)
        }}
    }}
    else {{
        Add-ModeCheckResult "Local Server state for mode" "SKIP" "disabled for this install"
    }}

    Write-Host ("Quick check summary: {{0}} failure(s), {{1}} warning(s)" -f $script:ModeCheckFailures, $script:ModeCheckWarnings)
    if ($script:ModeCheckFailures -gt 0 -and $Soft) {{
        Write-Warning "Quick check found missing prerequisites. Start will continue so install/start can repair services that are designed to be brought up on demand."
    }}

    return ($script:ModeCheckFailures -eq 0)
}}

function Request-ExistingAppShutdown {{
    param([Parameter(Mandatory = $true)]$SelectedMode)

    $appPort = [int]$SelectedMode.Port
    $heartbeatPort = [int]$SelectedMode.HeartbeatPort
    $appOpen = Test-LocalTcpPortOpen -Port $appPort
    $heartbeatOpen = Test-LocalTcpPortOpen -Port $heartbeatPort
    if (-not $appOpen -and -not $heartbeatOpen) {{
        Write-Host ("Target app ports are free: {{0}}, {{1}}" -f $appPort, $heartbeatPort)
        return
    }}

    Write-Host "Existing listener(s) detected on target app ports; requesting app shutdown before start."
    if ($heartbeatOpen) {{
        $heartbeatUri = "http://127.0.0.1:$heartbeatPort/api/heartbeat/control"
        Write-Host ("Requesting heartbeat shutdown: {{0}}" -f $heartbeatUri)
        Invoke-JsonPost -Uri $heartbeatUri -Body @{{ action = "shutdown" }} | Out-Null
    }}
    if ($appOpen) {{
        $appUri = "http://127.0.0.1:$appPort/system/hard-halt"
        Write-Host ("Requesting app hard halt: {{0}}" -f $appUri)
        Invoke-JsonPost -Uri $appUri | Out-Null
    }}

    $deadline = (Get-Date).AddSeconds(15)
    do {{
        Start-Sleep -Milliseconds 500
        $appOpen = Test-LocalTcpPortOpen -Port $appPort
        $heartbeatOpen = Test-LocalTcpPortOpen -Port $heartbeatPort
        if (-not $appOpen -and -not $heartbeatOpen) {{
            Write-Host "Existing app shutdown completed; target ports are free."
            return
        }}
    }} while ((Get-Date) -lt $deadline)

    $stillOpen = @()
    if ($appOpen) {{ $stillOpen += "app=127.0.0.1:$appPort" }}
    if ($heartbeatOpen) {{ $stillOpen += "heartbeat=127.0.0.1:$heartbeatPort" }}
    throw ("Existing app did not release the target ports after shutdown request. Still open: {{0}}. Stop the old debug instance and rerun." -f ($stillOpen -join ", "))
}}

function Invoke-UnleashedMode {{
    param([Parameter(Mandatory = $true)]$SelectedMode)
    $controlAction = Resolve-ControlAction -RequestedAction $Action
    $devControl = Join-Path $InstallRoot "dev-control.ps1"
    if (-not (Test-Path -LiteralPath $devControl -PathType Leaf)) {{
        throw "dev-control.ps1 is missing from install root: $devControl"
    }}
    $devControlParams = @{{
        Action = $controlAction
        Mode = "local"
        PythonPath = $SelectedMode.PythonPath
        BindHost = $BindHost
        LocalPort = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        ControlRoot = $SelectedMode.ControlRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }}
    Add-CommonControlSwitches -Params $devControlParams
    & $devControl @devControlParams
    exit $LASTEXITCODE
}}

function Test-WslDistributionInstalled {{
    param([Parameter(Mandatory = $true)][string]$Distribution)

    try {{
        $listed = & $ConfiguredWsl --list --quiet 2>$null
        if ($LASTEXITCODE -ne 0) {{
            return $false
        }}
        foreach ($line in @($listed)) {{
            if (($line.Trim()) -eq $Distribution) {{
                return $true
            }}
        }}
    }}
    catch {{
        return $false
    }}
    return $false
}}

function Invoke-DebugMode {{
    param([Parameter(Mandatory = $true)]$SelectedMode)
    $debugAction = switch ($Action) {{
        "start" {{ if (Test-WslDistributionInstalled -Distribution $SelectedMode.Distribution) {{ "run" }} else {{ "install-run" }} }}
        "run" {{ if (Test-WslDistributionInstalled -Distribution $SelectedMode.Distribution) {{ "run" }} else {{ "install-run" }} }}
        "stop" {{ "stop" }}
        "shutdown" {{ "stop" }}
        default {{ $Action }}
    }}
    $proto = Join-Path $InstallRoot "proto-dev\\proto-dev.ps1"
    if (-not (Test-Path -LiteralPath $proto -PathType Leaf)) {{
        throw "proto-dev\\proto-dev.ps1 is missing from install root: $proto"
    }}
    $protoParams = @{{
        Action = $debugAction
        RepoRoot = $InstallRoot
        StateRoot = $SelectedMode.StateRoot
        PythonCommand = $SelectedMode.PythonPath
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        BindHost = "127.0.0.1"
        Port = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        WslCommand = $ConfiguredWsl
        ExecutorDistribution = $SelectedMode.Distribution
        WslRuntimeRoot = $SelectedMode.WslRuntimeRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }}
    if ($debugAction -eq "install-run") {{
        $protoParams.SkipDependencyInstall = $true
    }}
    if ($BuildWslRuntimeIfMissingDefault -eq "1") {{
        $protoParams.BuildWslRuntimeIfMissing = $true
    }}
    if ($SkipWslRuntimeInstallDefault -eq "1") {{
        $protoParams.SkipWslRuntimeInstall = $true
    }}
    if ($ResetWslRuntimeDefault -eq "1") {{
        $protoParams.ResetWslRuntime = $true
    }}
    if ($SkipExecutorSmokeDefault -eq "1") {{
        $protoParams.SkipExecutorSmoke = $true
    }}
    Add-CommonControlSwitches -Params $protoParams
    & $proto @protoParams
    exit $LASTEXITCODE
}}

function Invoke-SafeMode {{
    param([Parameter(Mandatory = $true)]$SelectedMode)
    $controlAction = Resolve-ControlAction -RequestedAction $Action
    $control = Join-Path $InstallRoot "control-main-computer.ps1"
    if (-not (Test-Path -LiteralPath $control -PathType Leaf)) {{
        throw "control-main-computer.ps1 is missing from install root: $control"
    }}
    New-Item -ItemType Directory -Force -Path $SelectedMode.ControlRoot | Out-Null
    $controlParams = @{{
        Action = $controlAction
        AutoAllow = $true
        BindHost = $BindHost
        Port = [int]$SelectedMode.Port
        HeartbeatPort = [int]$SelectedMode.HeartbeatPort
        Workspace = $env:MAIN_COMPUTER_WORKSPACE
        PythonPath = $SelectedMode.PythonPath
        ControlRoot = $SelectedMode.ControlRoot
        StartTimeoutSeconds = [int]$StartTimeoutSeconds
    }}
    Add-CommonControlSwitches -Params $controlParams
    & $control @controlParams
    exit $LASTEXITCODE
}}

$modeToUse = if ([string]::IsNullOrWhiteSpace($Mode)) {{ $PinnedMode }} else {{ $Mode }}
$selectedMode = Resolve-RunnerMode -ModeName $modeToUse
Set-RunnerEnvironment -SelectedMode $selectedMode

Write-Host ("Main Computer {{0}}: {{1}} on http://127.0.0.1:{{2}} [{{3}}]" -f $Action, $selectedMode.Label, $selectedMode.Port, $InstanceName)

if ($Action -eq "check") {{
    $checkOk = Invoke-InstalledModeCheck -SelectedMode $selectedMode
    if ($checkOk) {{ exit 0 }}
    exit 2
}}

if (@("start", "run", "restart", "install", "install-run") -contains $Action) {{
    Invoke-InstalledModeCheck -SelectedMode $selectedMode -Soft | Out-Null
    Ensure-SharedGiteaInstalledIfMissing
    Request-ExistingAppShutdown -SelectedMode $selectedMode
}}

switch ($selectedMode.Key) {{
    "unleashed" {{ Invoke-UnleashedMode -SelectedMode $selectedMode }}
    "debug" {{ Invoke-DebugMode -SelectedMode $selectedMode }}
    "safe" {{ Invoke-SafeMode -SelectedMode $selectedMode }}
}}
"""
    runner_path.write_text(runner, encoding="utf-8", newline="\n")
    return runner_path


def powershell_status_command(runner_path: Path) -> str:
    return f'powershell -ExecutionPolicy Bypass -File "{runner_path}" -Action status'


def powershell_start_command(runner_path: Path) -> str:
    return f'powershell -ExecutionPolicy Bypass -File "{runner_path}" -Action start'


def powershell_check_command(runner_path: Path, mode: str = "") -> str:
    suffix = f' -Mode "{mode}"' if mode else ""
    return f'powershell -ExecutionPolicy Bypass -File "{runner_path}" -Action check{suffix}'


def select_batch_script(install_root: Path, action: str) -> Path:
    """Prefer experimental *_v2.bat launchers when present in the installed tree."""

    v2_path = install_root / f"{action}_v2.bat"
    if v2_path.exists():
        return v2_path
    return install_root / f"{action}.bat"


def start_bat_command(install_root: Path) -> str:
    return f'"{select_batch_script(install_root, "start")}"'


def stop_bat_command(install_root: Path) -> str:
    return f'"{select_batch_script(install_root, "stop")}"'

def powershell_env_header_command(env_header_path: Path) -> str:
    return f'. "{env_header_path}"'


def compact_status_command() -> str:
    return "& $env:MC_RUN -Action status"


def compact_start_command() -> str:
    return "& $env:MC_RUN -Action start"


def browser_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}/"


def read_key_value_file_value(path: Path, key: str) -> str:
    """Read a simple key=value file and return one named value.

    Local Coolify writes api-token.txt as a small human-readable file containing
    comments plus a token=... line.  The app needs the token value only, not the
    entire file contents.
    """
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    target = key.strip().lower()
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        if name.strip().lower() == target:
            return value.strip()
    return ""


def read_local_coolify_token_file(path: Path) -> str:
    token = read_key_value_file_value(path, "token")
    if token:
        return token
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if text and "\n" not in text and "=" not in text and not text.lstrip().startswith("#"):
        return text
    return ""


def start_installed_app(
    *,
    runner_path: Path,
    install_root: Path,
    app_port: int,
    log_dir: Path,
    timeout_seconds: int,
) -> None:
    """Start the freshly installed app through the tree-local launcher.

    The installer now prefers start_v2.bat when present so we can proof the
    location-aware launch flow without replacing the existing development
    start.bat yet. This path is opt-in via --start-after-install; the default
    installer behavior is to prepare the tree and print the command to run.
    """

    start_bat = select_batch_script(install_root, "start")
    if not start_bat.exists():
        raise RuntimeError(f"Installed start launcher is missing: {start_bat}")
    if not runner_path.exists():
        raise RuntimeError(f"Installed runner is missing before launcher handoff: {runner_path}")

    print("")
    print("Starting installed Main Computer app through the selected tree launcher.", flush=True)
    print(f"Start script: {start_bat}", flush=True)
    print(f"Installed runner: {runner_path}", flush=True)
    print(f"Browser: {browser_url(app_port)}", flush=True)
    run_command(
        [
            "cmd.exe",
            "/d",
            "/s",
            "/c",
            f'call "{start_bat}"',
        ],
        cwd=install_root,
        timeout_seconds=max(180, int(timeout_seconds) + 60),
        log_path=log_dir / "start-main-computer.log",
    )
    print("Installed Main Computer launcher command completed.", flush=True)


def _service_env(
    *,
    base_env: Mapping[str, str] | None = None,
    profile: dict[str, object],
    workspace: Path,
    wsl_command: str,
    onlyoffice_mode: str,
    container_runtime: str,
    local_server_mode: str,
    local_coolify_mode: str,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env["MAIN_COMPUTER_WORKSPACE"] = str(workspace)
    mode_key = str(profile["key"])
    mode_defaults = MODE_DEFAULTS.get(mode_key, MODE_DEFAULTS["unleashed"])
    instance_name = str(profile.get("instance_name") or safe_name(workspace.name))
    dev_compose_project = str(
        profile.get("dev_compose_project")
        or _mode_scoped_dev_compose_project(instance_name, mode_key)
    )

    env["MAIN_COMPUTER_INSTALL_MODE"] = mode_key
    env["MAIN_COMPUTER_INSTANCE_NAME"] = instance_name
    env["MAIN_COMPUTER_MODE_LABEL"] = str(profile["label"])
    env["MAIN_COMPUTER_GUIDANCE_LEVEL"] = str(profile["guidance_level"])
    env["MAIN_COMPUTER_SAFE_MODE"] = "1" if mode_key == "safe" else "0"
    env["MAIN_COMPUTER_CONTAINER_RUNTIME"] = str(container_runtime or "docker")
    env["MAIN_COMPUTER_STATE_ROOT"] = str(profile["state_root"])
    env["MAIN_COMPUTER_CONTROL_ROOT"] = str(profile["control_root"])
    env["MAIN_COMPUTER_CONTROL_PORT"] = str(profile["port"])
    env["MAIN_COMPUTER_HEARTBEAT_PORT"] = str(profile["heartbeat_port"])
    env["MAIN_COMPUTER_EXECUTOR_ENABLED"] = "1"
    env["MAIN_COMPUTER_EXECUTOR_BACKEND"] = "wsl"
    env["MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION"] = str(profile["distribution"])
    env["MAIN_COMPUTER_EXECUTOR_WSL_COMMAND"] = wsl_command
    env["MAIN_COMPUTER_EXECUTOR_ROOT"] = str(profile["executor_root"])
    env["MAIN_COMPUTER_PATH_MODE"] = "local"
    env["MAIN_COMPUTER_HOST_OS"] = "windows"
    env["MAIN_COMPUTER_GITEA_SCOPE"] = "shared-machine"
    env["MAIN_COMPUTER_GITEA_ROOT_URL"] = "http://127.0.0.1:3000/"
    env["MAIN_COMPUTER_GITEA_HTTP_PORT"] = "3000"
    env["MAIN_COMPUTER_GITEA_COMPOSE_PROJECT"] = "main-computer-gitea"
    env["MAIN_COMPUTER_DEV_COMPOSE_PROJECT"] = dev_compose_project
    env["MAIN_COMPUTER_EXECUTOR_COMPOSE_PROJECT"] = dev_compose_project
    env["MAIN_COMPUTER_DOCKER_VIEWPORT_PORT"] = str(profile.get("docker_viewport_port", mode_defaults["docker_viewport_port"]))
    env["MAIN_COMPUTER_HUB_PORT"] = str(profile.get("hub_port", mode_defaults["hub_port"]))
    env["MAIN_COMPUTER_HUB_WORKER_PORT"] = str(profile.get("hub_worker_port", mode_defaults["hub_worker_port"]))
    env["MAIN_COMPUTER_HUB_URL"] = f"http://127.0.0.1:{env['MAIN_COMPUTER_HUB_PORT']}"
    env["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
    env["MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL"] = f"http://127.0.0.1:{profile.get('ethereum_rpc_port', mode_defaults['ethereum_rpc_port'])}"
    env["MAIN_COMPUTER_ENERGY_CHAIN_ID"] = "42424242"

    if local_server_mode == "disabled":
        env["MAIN_COMPUTER_LOCAL_SERVER_ENABLED"] = "0"
    else:
        env["MAIN_COMPUTER_LOCAL_SERVER_ENABLED"] = "1"
        env["MAIN_COMPUTER_LOCAL_PLATFORM_MODE"] = str(profile["key"])
        env["MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT"] = str(profile["local_server_project"])
        env["MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH"] = str(profile["local_server_registry"])
        env["MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH"] = str(profile["local_server_compose"])
        env["MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START"] = str(profile["local_server_port_start"])
        env["MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START"] = str(profile["local_server_generated_port_start"])
        env["MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END"] = str(profile["local_server_generated_port_end"])
        env["MAIN_COMPUTER_LOCAL_SERVER_URL"] = f"http://127.0.0.1:{profile['local_server_port_start']}/"

    if local_coolify_mode == "disabled":
        env["MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED"] = "0"
    else:
        env["MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED"] = "1"
        env["MAIN_COMPUTER_COOLIFY_LOCAL_URL"] = f"http://127.0.0.1:{profile['coolify_port']}"
        env["MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_REF"] = "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"
        env["MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE"] = str(Path(profile["coolify_state_root"]) / "api-token.txt")
        env["MAIN_COMPUTER_COOLIFY_PROJECT"] = str(profile["coolify_project"])
        env["MAIN_COMPUTER_COOLIFY_STATE_DIR"] = str(profile["coolify_state_root"])
        env["MAIN_COMPUTER_COOLIFY_APP_PORT"] = str(profile["coolify_port"])
        env["MAIN_COMPUTER_COOLIFY_SOKETI_PORT"] = str(profile["coolify_soketi_port"])
        env["MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT"] = str(profile["coolify_soketi_terminal_port"])
        token_file = Path(env["MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE"])
        token_value = read_local_coolify_token_file(token_file)
        if token_value:
            env["MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"] = token_value

    effective_onlyoffice_mode = _effective_onlyoffice_mode(onlyoffice_mode)
    if effective_onlyoffice_mode == "disabled":
        env["MAIN_COMPUTER_ONLYOFFICE_ENABLED"] = "0"
    else:
        env["MAIN_COMPUTER_ONLYOFFICE_ENABLED"] = "1"
        env["MAIN_COMPUTER_ONLYOFFICE_MODE"] = effective_onlyoffice_mode
        env["MAIN_COMPUTER_ONLYOFFICE_PORT"] = str(profile["onlyoffice_port"])
        env["MAIN_COMPUTER_ONLYOFFICE_PROJECT"] = str(profile["onlyoffice_project"])
        env["MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME"] = "main-computer-onlyoffice-documentserver"
        env["MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL"] = f"http://127.0.0.1:{profile['onlyoffice_port']}"
        env["MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL"] = f"http://127.0.0.1:{profile['onlyoffice_port']}"
        env["MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL"] = f"http://host.docker.internal:{profile['port']}"
        env.setdefault("MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET", "main-computer-onlyoffice-local-secret")
    return env


def _launcher_environment(
    *,
    profile: dict[str, object],
    workspace: Path,
    venv_python: Path,
    wsl_command: str,
    onlyoffice_mode: str,
    container_runtime: str,
    local_server_mode: str,
    local_coolify_mode: str,
) -> dict[str, str]:
    """Build the non-secret environment start_v2.bat should apply at launch time."""

    env = _service_env(
        base_env={},
        profile=profile,
        workspace=workspace,
        wsl_command=wsl_command,
        onlyoffice_mode=onlyoffice_mode,
        container_runtime=container_runtime,
        local_server_mode=local_server_mode,
        local_coolify_mode=local_coolify_mode,
    )
    env.pop("MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN", None)
    env["MAIN_COMPUTER_PYTHON_COMMAND"] = str(venv_python)
    apply_podman_compose_provider_env(
        env,
        container_runtime=container_runtime,
        venv_python=venv_python,
    )
    return {name: str(value) for name, value in sorted(env.items())}


def write_launcher_context(
    *,
    install_root: Path,
    mode_key: str,
    mode_label: str,
    runtime_profile: str,
    instance_name: str,
    workspace: Path,
    venv_python: Path,
    profile: dict[str, object],
    wsl_command: str,
    onlyoffice_mode: str,
    container_runtime: str,
    local_server_mode: str,
    local_coolify_mode: str,
) -> Path:
    """Write the tiny installed-tree identity consumed by start_v2.bat/stop_v2.bat."""

    context_path = install_root / "runtime" / "start_stop" / "main-computer-launcher.json"
    payload = {
        "schema_version": 1,
        "tree_kind": "installed",
        "mode": mode_key,
        "mode_label": mode_label,
        "runtime_profile": runtime_profile,
        "container_runtime": container_runtime,
        "instance_name": instance_name,
        "install_tree_id": safe_name(install_root.name).replace("_", "-"),
        "install_root": str(install_root),
        "workspace": str(workspace),
        "python": str(venv_python),
        "venv_python": str(venv_python),
        "start_script": str(select_batch_script(install_root, "start")),
        "stop_script": str(select_batch_script(install_root, "stop")),
        "environment": _launcher_environment(
            profile=profile,
            workspace=workspace,
            venv_python=venv_python,
            wsl_command=wsl_command,
            onlyoffice_mode=onlyoffice_mode,
            container_runtime=container_runtime,
            local_server_mode=local_server_mode,
            local_coolify_mode=local_coolify_mode,
        ),
    }
    write_manifest(context_path, payload)
    return context_path


def warn_existing_app_listeners(
    *,
    app_port: int,
    heartbeat_port: int,
) -> None:
    ports = {"app": int(app_port), "heartbeat": int(heartbeat_port)}
    open_ports = {name: port for name, port in ports.items() if _tcp_port_open("127.0.0.1", port)}
    if not open_ports:
        print(f"Target app ports are free: {app_port}, {heartbeat_port}", flush=True)
        return

    details = ", ".join(f"{name}=127.0.0.1:{port}" for name, port in open_ports.items())
    print(
        "WARN - Existing listener(s) detected on target app ports; "
        "the installer will not stop them. Use the tree-local stop_v2.bat/stop.bat when you are ready. "
        f"Open: {details}",
        flush=True,
    )


def run_install_time_service_preparation(
    *,
    args: argparse.Namespace,
    install_root: Path,
    venv_python: Path,
    profile: dict[str, object],
    log_dir: Path,
    service_env: Mapping[str, str],
) -> None:
    """Keep the installer minimal: auto service startup is deferred to start_v2.bat.

    Explicit required modes still run here because the operator asked the
    installer to fail if that preparation cannot be completed.
    """

    if args.local_server_mode == "required":
        initialize_local_server_publishing_if_requested(
            args=args,
            install_root=install_root,
            venv_python=venv_python,
            profile=profile,
            log_dir=log_dir,
            service_env=service_env,
        )
    else:
        print("Local Server publishing startup deferred to the tree launcher.", flush=True)

    if args.local_coolify_mode == "required":
        start_local_coolify_if_requested(
            args=args,
            install_root=install_root,
            venv_python=venv_python,
            profile=profile,
            log_dir=log_dir,
            service_env=service_env,
        )
    else:
        print("Local Coolify startup deferred to the tree launcher.", flush=True)

    if args.onlyoffice_mode == "docker" or args.install_onlyoffice:
        start_onlyoffice_if_requested(
            args=args,
            install_root=install_root,
            profile=profile,
            log_dir=log_dir,
            service_env=service_env,
        )
    else:
        print("ONLYOFFICE startup deferred to the tree launcher.", flush=True)


def _docker_available() -> bool:
    return _container_cli_available()


def initialize_local_server_publishing_if_requested(
    *,
    args: argparse.Namespace,
    install_root: Path,
    venv_python: Path,
    profile: dict[str, object],
    log_dir: Path,
    service_env: Mapping[str, str],
) -> None:
    if args.local_server_mode == "disabled":
        print("Local Server publishing skipped because --local-server-mode disabled was set.", flush=True)
        return
    if not _docker_available():
        message = "Docker was not found; Local Server publishing containers cannot be started."
        if args.local_server_mode == "required":
            raise RuntimeError(message)
        print(f"WARN - {message}", flush=True)
        return

    script_path = install_root / "tools" / "local-platform" / "website-docker.py"
    if not script_path.exists():
        raise RuntimeError(f"Local Server publishing tool not found: {script_path}")

    print("")
    print("Starting Local Server publishing targets.", flush=True)
    for site_id in ("hub-site", "blog-site"):
        run_command(
            [venv_python, script_path, "install", site_id, "--repo-root", install_root],
            cwd=install_root,
            timeout_seconds=90,
            env=service_env,
            log_path=log_dir / f"local-platform-install-{site_id}.log",
        )

    publish_targets = (
        ("hub-site", "local"),
        ("blog-site", "local"),
        ("hub-site", "dev"),
        ("blog-site", "dev"),
    )
    for site_id, lane in publish_targets:
        run_command(
            [
                venv_python,
                script_path,
                "publish",
                site_id,
                "--lane",
                lane,
                "--repo-root",
                install_root,
                "--timeout",
                "180",
            ],
            cwd=install_root,
            timeout_seconds=240,
            env=service_env,
            log_path=log_dir / f"local-platform-publish-{site_id}-{lane}.log",
        )
    print(
        "Local Server publishing targets ready: "
        f"http://127.0.0.1:{profile['local_server_port_start']}/, "
        f"http://127.0.0.1:{int(profile['local_server_port_start']) + 1}/, "
        f"http://127.0.0.1:{int(profile['local_server_port_start']) + 2}/, "
        f"http://127.0.0.1:{int(profile['local_server_port_start']) + 3}/",
        flush=True,
    )


def start_local_coolify_if_requested(
    *,
    args: argparse.Namespace,
    install_root: Path,
    venv_python: Path,
    profile: dict[str, object],
    log_dir: Path,
    service_env: Mapping[str, str],
) -> None:
    if args.local_coolify_mode == "disabled":
        print("Local Coolify skipped because --local-coolify-mode disabled was set.", flush=True)
        return
    if not _docker_available():
        message = "Docker was not found; install-scoped Local Coolify cannot be started."
        if args.local_coolify_mode == "required":
            raise RuntimeError(message)
        print(f"WARN - {message}", flush=True)
        return

    script_path = install_root / "tools" / "local-prod" / "setup-local-coolify.py"
    if not script_path.exists():
        raise RuntimeError(f"Local Coolify setup tool not found: {script_path}")

    common_args = [
        "--project-name",
        str(profile["coolify_project"]),
        "--state-dir",
        str(profile["coolify_state_root"]),
        "--app-port",
        str(profile["coolify_port"]),
        "--soketi-port",
        str(profile["coolify_soketi_port"]),
        "--soketi-terminal-port",
        str(profile["coolify_soketi_terminal_port"]),
    ]

    print("")
    print("Starting install-scoped Local Coolify.", flush=True)
    run_command(
        [venv_python, script_path, "setup", *common_args],
        cwd=install_root,
        timeout_seconds=1200,
        env=service_env,
        log_path=log_dir / "setup-local-coolify.log",
    )
    token_file = Path(profile["coolify_state_root"]) / "api-token.txt"
    credentials_file = Path(profile["coolify_state_root"]) / "credentials.txt"
    print(f"Local Coolify ready: http://127.0.0.1:{profile['coolify_port']}", flush=True)
    print(f"Local Coolify credentials: {credentials_file}", flush=True)
    print(f"Local Coolify API token file: {token_file}", flush=True)
    print("Local Coolify remote-prod publish rehearsal passed.", flush=True)


def start_onlyoffice_if_requested(
    *,
    args: argparse.Namespace,
    install_root: Path,
    profile: dict[str, object],
    log_dir: Path,
    service_env: Mapping[str, str],
) -> None:
    effective_mode = _effective_onlyoffice_mode(args.onlyoffice_mode)
    if effective_mode == "disabled":
        if args.onlyoffice_mode == "auto":
            print("ONLYOFFICE skipped because Docker was not found for auto mode.", flush=True)
        else:
            print("ONLYOFFICE skipped because --onlyoffice-mode disabled was set.", flush=True)
        return

    control = install_root / "tools" / "onlyoffice" / "onlyoffice-control.ps1"
    if not control.exists():
        raise RuntimeError(f"ONLYOFFICE control script not found: {control}")

    common_args = [
        "-Mode",
        effective_mode,
        "-Port",
        str(profile["onlyoffice_port"]),
        "-ProjectName",
        str(profile["onlyoffice_project"]),
    ]

    print("")
    print("Starting ONLYOFFICE service.", flush=True)
    if args.install_onlyoffice:
        run_command(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", control, "install", *common_args],
            cwd=install_root,
            timeout_seconds=600,
            env=service_env,
            log_path=log_dir / "onlyoffice-install.log",
        )
    run_command(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", control, "start", *common_args],
        cwd=install_root,
        timeout_seconds=360,
        env=service_env,
        log_path=log_dir / "onlyoffice-start.log",
    )
    run_command(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", control, "status", *common_args],
        cwd=install_root,
        timeout_seconds=120,
        env=service_env,
        log_path=log_dir / "onlyoffice-status.log",
    )
    print(f"ONLYOFFICE ready: http://127.0.0.1:{profile['onlyoffice_port']}", flush=True)


def write_env_header(
    *,
    install_root: Path,
    runner_path: Path,
    instance_root: Path,
    control_root: Path,
    state_root: Path,
    venv_python: Path,
    mode_key: str,
    mode_label: str,
    instance_name: str,
    container_runtime: str,
) -> Path:
    env_header_path = install_root / "main-computer-env.ps1"
    if container_runtime == "podman":
        podman_compose_provider_header = (
            "$env:PODMAN_COMPOSE_PROVIDER = "
            + ps_single_quoted(podman_compose_provider_path(venv_python))
            + "\n"
        )
    else:
        podman_compose_provider_header = (
            "$env:PODMAN_COMPOSE_PROVIDER = \"\"\n"
            "Remove-Item -LiteralPath \"Env:\\PODMAN_COMPOSE_PROVIDER\" -ErrorAction SilentlyContinue\n"
        )
    env_header = f"""# Generated by tools\\bootstrap_main_computer.py.
# Dot-source this file in the current PowerShell session before using the
# compact MC_* command variables:
#
#   . "{env_header_path}"
#   & $env:MC_RUN -Action status

$env:MC_INSTALL = $PSScriptRoot
$env:MC_RUN = Join-Path $PSScriptRoot {ps_single_quoted(runner_path.name)}
$env:MC_INSTANCE = {ps_single_quoted(instance_root)}
$env:MC_STATE = {ps_single_quoted(state_root)}
$env:MC_CONTROL = {ps_single_quoted(control_root)}
$env:MC_VENV_PYTHON = {ps_single_quoted(venv_python)}
$env:MC_MODE = {ps_single_quoted(mode_key)}
$env:MC_MODE_LABEL = {ps_single_quoted(mode_label)}
$env:MC_INSTANCE_NAME = {ps_single_quoted(instance_name)}
$env:MC_CONTAINER_RUNTIME = {ps_single_quoted(container_runtime)}

$env:MAIN_COMPUTER_INSTALL_ROOT = $env:MC_INSTALL
$env:MAIN_COMPUTER_RUNNER = $env:MC_RUN
$env:MAIN_COMPUTER_INSTANCE_ROOT = $env:MC_INSTANCE
$env:MAIN_COMPUTER_STATE_ROOT = $env:MC_STATE
$env:MAIN_COMPUTER_CONTROL_ROOT = $env:MC_CONTROL
$env:MAIN_COMPUTER_VENV_PYTHON = $env:MC_VENV_PYTHON
$env:MAIN_COMPUTER_INSTALL_MODE = $env:MC_MODE
$env:MAIN_COMPUTER_MODE_LABEL = $env:MC_MODE_LABEL
$env:MAIN_COMPUTER_INSTANCE_NAME = $env:MC_INSTANCE_NAME
$env:MAIN_COMPUTER_CONTAINER_RUNTIME = $env:MC_CONTAINER_RUNTIME
{podman_compose_provider_header}
$env:MC_STATUS = "& `$env:MC_RUN -Action status"
$env:MC_START = "& `$env:MC_RUN -Action start"
$env:MC_CHECK = "& `$env:MC_RUN -Action check"

Write-Host "Main Computer compact env header loaded."
Write-Host "Status: & `$env:MC_RUN -Action status"
Write-Host "Start:  & `$env:MC_RUN -Action start"
Write-Host "Check:  & `$env:MC_RUN -Action check"
"""
    env_header_path.write_text(env_header, encoding="utf-8", newline="\n")
    return env_header_path



def active_control_ports(args: argparse.Namespace, mode_key: str) -> tuple[int, int]:
    """Return the active app and heartbeat ports for the install mode."""

    defaults = dict(MODE_DEFAULTS[mode_key])
    if mode_key == "safe":
        defaults["port"] = args.safe_port
        defaults["heartbeat_port"] = args.safe_heartbeat_port

    if args.port is not None:
        defaults["port"] = args.port
    if args.heartbeat_port and args.heartbeat_port > 0:
        defaults["heartbeat_port"] = args.heartbeat_port

    return int(defaults["port"]), int(defaults["heartbeat_port"])


def _tcp_port_open(host: str, port: int, *, timeout_seconds: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _post_json(url: str, payload: dict[str, object], *, timeout_seconds: float = 3.0) -> bool:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 500
    except (OSError, URLError):
        return False


def request_existing_app_shutdown(
    *,
    app_port: int,
    heartbeat_port: int,
    timeout_seconds: int = 15,
) -> None:
    """Ask any existing app on the target ports to shut down, then require the ports to clear."""

    ports = {"app": int(app_port), "heartbeat": int(heartbeat_port)}
    initially_open = {name: port for name, port in ports.items() if _tcp_port_open("127.0.0.1", port)}
    if not initially_open:
        print(f"Target app ports are free: {app_port}, {heartbeat_port}", flush=True)
        return

    print("Existing listener(s) detected on target app ports.", flush=True)
    for name, port in initially_open.items():
        print(f"  {name}: 127.0.0.1:{port}", flush=True)

    heartbeat_url = f"http://127.0.0.1:{heartbeat_port}/api/heartbeat/control"
    app_halt_url = f"http://127.0.0.1:{app_port}/system/hard-halt"

    if "heartbeat" in initially_open:
        print(f"Requesting heartbeat shutdown: {heartbeat_url}", flush=True)
        _post_json(heartbeat_url, {"action": "shutdown"}, timeout_seconds=3.0)

    if "app" in initially_open:
        print(f"Requesting app hard halt: {app_halt_url}", flush=True)
        _post_json(app_halt_url, {}, timeout_seconds=3.0)

    deadline = time.monotonic() + max(1, int(timeout_seconds))
    while time.monotonic() < deadline:
        still_open = {name: port for name, port in ports.items() if _tcp_port_open("127.0.0.1", port)}
        if not still_open:
            print("Existing app shutdown completed; target ports are free.", flush=True)
            return
        time.sleep(0.5)

    still_open = {name: port for name, port in ports.items() if _tcp_port_open("127.0.0.1", port)}
    if still_open:
        details = ", ".join(f"{name}=127.0.0.1:{port}" for name, port in still_open.items())
        raise RuntimeError(
            "Existing app did not release the target ports after shutdown request. "
            f"Still open: {details}. Stop the old debug instance and rerun the installer."
        )


def precheck(
    args: argparse.Namespace,
    install_root: Path,
    paths: dict[str, Path],
    mode_label: str,
    install_root_source: str,
    managed_default_root: Path,
) -> int:
    print("Precheck only: no install files, venvs, WSL distros, firewall rules, or runners were created.")
    print(f"Repo root:       {args.repo_root.resolve()}")
    print(f"Install root:    {install_root}")
    print(f"Target source:   {install_root_source}")
    if install_root != managed_default_root:
        print(f"Managed default: {managed_default_root}")
    env_header_path = install_root / "main-computer-env.ps1"
    print(f"Status command:  {powershell_status_command(install_root / args.runner_name)}")
    print(f"Shell header:    {powershell_env_header_command(env_header_path)}")
    print(f"After header:    {compact_status_command()}")
    print(f"Mode:            {mode_label}")
    print(f"Instance store:  {paths['base_store']}")
    print(f"Instance root:   {paths['instance_root']}")
    print(f"State root:      {paths['state_root']}")
    print(f"Venv root:       {paths['venv_root']}")
    print(f"Workspace:       {args.workspace.resolve() if args.workspace else install_root}")
    print(f"Wheelhouse:      {tool_paths().wheelhouse}")
    return 0


def _serializable_profiles(mode_profiles: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    serializable: dict[str, dict[str, object]] = {}
    for key, profile in mode_profiles.items():
        serializable[key] = {name: str(value) if isinstance(value, Path) else value for name, value in profile.items()}
    return serializable


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    args.repo_root = args.repo_root.resolve()
    if not args.repo_root.exists():
        parser.error(f"--repo-root does not exist: {args.repo_root}")

    mode_key, mode_label = normalize_mode(args.mode)
    install_root, install_root_source, managed_default_root = resolve_install_root(args, mode_key)
    destructive_replace_install_root = args.auto_force_install
    instance_name = args.instance_name.strip() or default_instance_name(args.runtime_profile, mode_key, install_root)
    paths = instance_paths(
        instance_store_root=args.instance_store_root,
        instance_name=instance_name,
        mode_key=mode_key,
        venv_path=args.venv_path,
    )

    verify_managed_python(args.managed_python)

    if args.precheck_only:
        return precheck(
            args,
            install_root,
            paths,
            mode_label,
            install_root_source,
            managed_default_root,
        )

    print("Starting Python-owned Main Computer golden path install.", flush=True)
    print(f"Repo root:       {args.repo_root}", flush=True)
    print(f"Install root:    {install_root}", flush=True)
    print(f"Target source:   {install_root_source}", flush=True)
    if install_root != managed_default_root:
        print(f"Managed default: {managed_default_root}", flush=True)
    if destructive_replace_install_root:
        print("Install refresh: --auto-force-install will remove any existing install root without an archive.", flush=True)
    else:
        print("Install refresh: any existing install root will be archived and moved aside before copying.", flush=True)
    print(f"Mode:            {mode_label}", flush=True)
    print(f"Instance name:   {instance_name}", flush=True)
    print(f"Instance root:   {paths['instance_root']}", flush=True)
    print(f"State root:      {paths['state_root']}", flush=True)

    app_port, heartbeat_port = active_control_ports(args, mode_key)
    warn_existing_app_listeners(
        app_port=app_port,
        heartbeat_port=heartbeat_port,
    )

    paths["log_dir"].mkdir(parents=True, exist_ok=True)
    paths["manifest_dir"].mkdir(parents=True, exist_ok=True)
    paths["state_root"].mkdir(parents=True, exist_ok=True)
    paths["control_root"].mkdir(parents=True, exist_ok=True)

    if args.skip_install_root_copy:
        print("Skipping install root copy because --skip-install-root-copy was set.", flush=True)
        if not install_root.exists():
            raise RuntimeError(f"--skip-install-root-copy requires an existing install root: {install_root}")
    else:
        copy_clean_tree(
            args.repo_root,
            install_root,
            auto_force=destructive_replace_install_root,
        )

    venv_python = create_venv_without_pip(Path(sys.executable), paths["venv_root"], timeout_seconds=120)
    pip_wheel = ensure_pip_wheel(
        tool_paths().wheelhouse,
        args.pip_wheel_version,
        no_download=args.no_python_download,
    )
    seed_pip_from_wheel(venv_python, paths["venv_root"], pip_wheel)

    pip_install_project(
        venv_python,
        install_root,
        paths["log_dir"] / "pip-install-main-computer.log",
    )

    install_mathics_if_requested(
        mode=args.mathics_install_mode,
        venv_python=venv_python,
        wheelhouse=tool_paths().wheelhouse,
        log_dir=paths["log_dir"],
    )

    run_command(
        [
            venv_python,
            "-c",
            "import main_computer; print('main_computer import ok')",
        ],
        cwd=install_root,
        timeout_seconds=30,
    )

    workspace = args.workspace.expanduser().resolve() if args.workspace else install_root
    mode_profiles = build_mode_profiles(
        args=args,
        install_root=install_root,
        instance_name=instance_name,
        instance_store_root=paths["base_store"],
        active_mode_key=mode_key,
        active_venv_python=venv_python,
    )

    if args.skip_runner_creation:
        print("Skipping runner/env header creation because --skip-runner-creation was set.", flush=True)
        runner_path = install_root / args.runner_name
        env_header_path = install_root / "main-computer-env.ps1"
    else:
        runner_path = write_runner(
            install_root=install_root,
            runner_name=args.runner_name,
            mode_key=mode_key,
            mode_label=mode_label,
            instance_name=instance_name,
            mode_profiles=mode_profiles,
            wsl_command=args.wsl_command,
            workspace=workspace,
            bind_host=args.bind_host,
            start_timeout_seconds=args.start_timeout_seconds,
            onlyoffice_mode=args.onlyoffice_mode,
            container_runtime=args.container_runtime,
            local_server_mode=args.local_server_mode,
            local_coolify_mode=args.local_coolify_mode,
            skip_mathics_check=args.skip_mathics_check,
            skip_wsl_runtime_install=args.skip_wsl_runtime_install,
            build_wsl_runtime_if_missing=args.build_wsl_runtime_if_missing,
            reset_wsl_runtime=args.reset_wsl_runtime,
            skip_executor_smoke=args.skip_executor_smoke,
            allow_foreign_port_listener=args.allow_foreign_port_listener,
        )
        env_header_path = write_env_header(
            install_root=install_root,
            runner_path=runner_path,
            instance_root=paths["instance_root"],
            control_root=paths["control_root"],
            state_root=paths["state_root"],
            venv_python=venv_python,
            mode_key=mode_key,
            mode_label=mode_label,
            instance_name=instance_name,
            container_runtime=args.container_runtime,
        )

    active_profile = mode_profiles[mode_key]
    service_env = _service_env(
        profile=active_profile,
        workspace=workspace,
        wsl_command=args.wsl_command,
        onlyoffice_mode=args.onlyoffice_mode,
        container_runtime=args.container_runtime,
        local_server_mode=args.local_server_mode,
        local_coolify_mode=args.local_coolify_mode,
    )
    service_env["MAIN_COMPUTER_PYTHON_COMMAND"] = str(venv_python)
    apply_podman_compose_provider_env(
        service_env,
        container_runtime=args.container_runtime,
        venv_python=venv_python,
    )

    launcher_context_path = write_launcher_context(
        install_root=install_root,
        mode_key=mode_key,
        mode_label=mode_label,
        runtime_profile=args.runtime_profile,
        instance_name=instance_name,
        workspace=workspace,
        venv_python=venv_python,
        profile=active_profile,
        wsl_command=args.wsl_command,
        onlyoffice_mode=args.onlyoffice_mode,
        container_runtime=args.container_runtime,
        local_server_mode=args.local_server_mode,
        local_coolify_mode=args.local_coolify_mode,
    )

    run_install_time_service_preparation(
        args=args,
        install_root=install_root,
        venv_python=venv_python,
        profile=active_profile,
        log_dir=paths["log_dir"],
        service_env=service_env,
    )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "installer": "bootstrap-main-computer-python-windows.ps1",
        "driver": "tools/bootstrap_main_computer.py",
        "runtime_profile": args.runtime_profile,
        "container_runtime": args.container_runtime,
        "podman_compose_provider": str(podman_compose_provider_path(venv_python)) if args.container_runtime == "podman" else "",
        "mode": mode_key,
        "mode_label": mode_label,
        "install_root": str(install_root),
        "install_root_source": install_root_source,
        "managed_default_root": str(managed_default_root),
        "repo_root": str(args.repo_root),
        "workspace": str(workspace),
        "instance_name": instance_name,
        "instance_root": str(paths["instance_root"]),
        "state_root": str(paths["state_root"]),
        "control_root": str(paths["control_root"]),
        "venv_root": str(paths["venv_root"]),
        "venv_python": str(venv_python),
        "managed_python": str(Path(sys.executable)),
        "python_nuget_version": args.python_nuget_version,
        "pip_wheel_version": args.pip_wheel_version,
        "pip_wheel": str(pip_wheel),
        "runner": str(runner_path),
        "launcher_context": str(launcher_context_path),
        "selected_start_script": str(select_batch_script(install_root, "start")),
        "selected_stop_script": str(select_batch_script(install_root, "stop")),
        "status_command": powershell_status_command(runner_path),
        "check_command": powershell_check_command(runner_path, mode_label),
        "start_command": start_bat_command(install_root),
        "stop_command": stop_bat_command(install_root),
        "browser_url": browser_url(app_port),
        "env_header": str(env_header_path),
        "env_header_command": powershell_env_header_command(env_header_path),
        "compact_status_command": compact_status_command(),
        "compact_start_command": compact_start_command(),
        "wsl_command": args.wsl_command,
        "wsl_firewall_mode": args.wsl_firewall_mode,
        "bind_host": args.bind_host,
        "start_timeout_seconds": args.start_timeout_seconds,
        "onlyoffice_mode": args.onlyoffice_mode,
        "effective_onlyoffice_mode": _effective_onlyoffice_mode(args.onlyoffice_mode),
        "install_onlyoffice": args.install_onlyoffice,
        "local_server_mode": args.local_server_mode,
        "local_coolify_mode": args.local_coolify_mode,
        "skip_wsl_runtime_install": args.skip_wsl_runtime_install,
        "build_wsl_runtime_if_missing": args.build_wsl_runtime_if_missing,
        "reset_wsl_runtime": args.reset_wsl_runtime,
        "skip_executor_smoke": args.skip_executor_smoke,
        "skip_app_start": args.skip_app_start,
        "start_after_install": args.start_after_install,
        "skip_mathics_check": args.skip_mathics_check,
        "allow_foreign_port_listener": args.allow_foreign_port_listener,
        "mathics_install_mode": args.mathics_install_mode,
        "modes": _serializable_profiles(mode_profiles),
    }
    write_manifest(install_root / "main-computer-install.json", manifest)
    write_manifest(install_root / "runtime" / "main-computer-install.json", manifest)
    write_manifest(paths["manifest_dir"] / "main-computer-install.json", manifest)
    tool_paths().manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(tool_paths().manifests / f"{safe_name(instance_name)}-{mode_key}.json", manifest)

    print("")
    print("Python-owned golden path install complete.", flush=True)
    print(f"Target source: {install_root_source}", flush=True)
    print(f"Runner: {runner_path}", flush=True)
    print(f"Launcher context: {launcher_context_path}", flush=True)
    print(f"Selected start script: {select_batch_script(install_root, 'start')}", flush=True)
    print(f"Selected stop script: {select_batch_script(install_root, 'stop')}", flush=True)
    print(f"Browser after start: {browser_url(app_port)}", flush=True)
    print(f"ONLYOFFICE after start: http://127.0.0.1:{active_profile['onlyoffice_port']}", flush=True)
    print(f"Local Coolify after start: http://127.0.0.1:{active_profile['coolify_port']}", flush=True)
    print(
        "Local Server built-ins: "
        f"http://127.0.0.1:{active_profile['local_server_port_start']}/, "
        f"http://127.0.0.1:{int(active_profile['local_server_port_start']) + 1}/, "
        f"http://127.0.0.1:{int(active_profile['local_server_port_start']) + 2}/, "
        f"http://127.0.0.1:{int(active_profile['local_server_port_start']) + 3}/",
        flush=True,
    )
    print(f"Status command: {powershell_status_command(runner_path)}", flush=True)
    print(f"Check command: {powershell_check_command(runner_path, mode_label)}", flush=True)
    print(f"Start command: {start_bat_command(install_root)}", flush=True)
    print(f"Stop command: {stop_bat_command(install_root)}", flush=True)
    print(f"Shell header: {powershell_env_header_command(env_header_path)}", flush=True)
    print(f"After header status: {compact_status_command()}", flush=True)
    print(f"After header start: {compact_start_command()}", flush=True)
    print(f"Manifest: {install_root / 'main-computer-install.json'}", flush=True)

    if args.start_after_install and not args.skip_app_start and not args.skip_runner_creation:
        start_installed_app(
            runner_path=runner_path,
            install_root=install_root,
            app_port=app_port,
            log_dir=paths["log_dir"],
            timeout_seconds=args.start_timeout_seconds,
        )
        print(f"Point your browser at: {browser_url(app_port)}", flush=True)
    else:
        if args.skip_app_start:
            print("App start skipped because --skip-app-start was set.", flush=True)
        elif args.skip_runner_creation:
            print("App start skipped because --skip-runner-creation was set.", flush=True)
        else:
            print("App start skipped by default; run the selected start command when ready.", flush=True)
        print(f"Point your browser at after starting: {browser_url(app_port)}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
