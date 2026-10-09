from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from main_computer.hub_networks import load_hub_network_registry
from main_computer.hub_admin_runtime import HUB_ADMIN_BUNDLE_ENV, HUB_ADMIN_BUNDLE_SCHEMA
from tools import coolify_hub_service as legacy

from .canonical import canonical_bytes
from .errors import HubControlError
from .models import DependencyContract, HubContext, HubPlacement
from .privates import controller_coordinates

FDB_CLUSTER_ENV = "MAIN_COMPUTER_HUB_CONTROL_FDB_CLUSTER_CONTENTS"
TOPOLOGY_B64_ENV = "MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_B64"
FDB_CONTRACT_ENV = "MAIN_COMPUTER_HUB_CONTROL_FDB_CONTRACT_SHA256"
CHAIN_CONTRACT_ENV = "MAIN_COMPUTER_HUB_CONTROL_CHAIN_CONTRACT_SHA256"
BOOTSTRAP_FDB_ENV = "MCF"
BOOTSTRAP_TOPOLOGY_ENV = "MCT"
PROGRESS_ENV = "MAIN_COMPUTER_HUB_PROGRESS"
BRIDGE_SIGNER_ENV = "MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64"
CHAIN_RPC_USER_AGENT = "main-computer-chain-rpc/1.0 (+https://greatlibrary.io)"
IS_BRIDGE_CONTROLLER_SELECTOR = "fbe5c12e"

# Heuristic source set for the Coolify Hub image.  Do not use the whole
# ``main_computer`` package here: the repository contains many applications
# that are copied into the image but are not imported by the Hub runtime.
# Instead, the guard starts from the actual Hub entry modules and follows their
# local Python imports transitively.  Static build/config inputs that can change
# the resulting Hub image are added explicitly.
HUB_GIT_STATIC_PATHS = (
    ".dockerignore",
    "Dockerfile.hub.exp-fdb",
    "exp-fdb-hub.py",
    "run-exp-fdb-hub.py",
    "pyproject.toml",
    "main_computer/config/hub_networks.json",
)
HUB_GIT_ENTRY_MODULES = (
    "main_computer.exp_fdb_hub",
    "main_computer.hub_networks",
    "main_computer.runtime_env_file",
)


def _progress(message: str) -> None:
    raw = str(os.environ.get(PROGRESS_ENV) or "").strip().lower()
    if raw in {"", "0", "false", "no", "off"}:
        return
    print(f"HUB_PROGRESS: {message}", file=sys.stderr, flush=True)


def _powershell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _render_git_commit_push_command(changes: list[dict[str, str]]) -> str:
    paths: list[str] = []
    seen: set[str] = set()
    for item in changes:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    if not paths:
        return ""
    rendered_paths = " ".join(_powershell_single_quote(path) for path in paths)
    return (
        f"git add -- {rendered_paths}; "
        "if ($LASTEXITCODE -eq 0) { git commit -m 'Update Hub deployment source' }; "
        "if ($LASTEXITCODE -eq 0) { git push origin HEAD }"
    )


def _emit_git_commit_push_command(changes: list[dict[str, str]]) -> str:
    command = _render_git_commit_push_command(changes)
    if command:
        print("HUB_GIT_COMMIT_PUSH_COMMAND:", file=sys.stderr, flush=True)
        print(command, file=sys.stderr, flush=True)
    return command


def _module_name_for_path(path: Path, repo_root: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    if rel.suffix != ".py":
        return None
    parts = list(rel.with_suffix("").parts)
    if not parts or parts[0] != "main_computer":
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_local_imports(module_name: str, source: str, module_paths: Mapping[str, str]) -> set[str]:
    """Return local ``main_computer`` modules imported by *source*.

    This is intentionally static and conservative.  The deployment check is a
    heuristic safety gate, not a Python import implementation.
    """

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        # A syntax-broken relevant module is itself dirty and will be caught by
        # Git.  Do not turn dependency discovery into a second parser failure.
        return set()

    discovered: set[str] = set()
    current_package = module_name.rsplit(".", 1)[0] if "." in module_name else module_name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "main_computer" or alias.name.startswith("main_computer."):
                    discovered.add(alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            package_parts = current_package.split(".") if current_package else []
            climb = max(0, int(node.level) - 1)
            if climb:
                package_parts = package_parts[:-climb]
            if node.module:
                package_parts.extend(str(node.module).split("."))
            base = ".".join(package_parts)
        else:
            base = str(node.module or "")
        if not (base == "main_computer" or base.startswith("main_computer.")):
            continue

        discovered.add(base)
        for alias in node.names:
            if alias.name == "*":
                continue
            candidate = f"{base}.{alias.name}" if base else alias.name
            if candidate in module_paths:
                discovered.add(candidate)
    return discovered


def _hub_git_source_paths(repo_root: Path, *, network: str = "mainnet") -> tuple[str, ...]:
    """Compute the Hub runtime/build dependency surface for the Git guard.

    The closure follows local Python imports from the deployed Hub entrypoints.
    This keeps unrelated applications (for example ``game_web_loader.py``) out
    of the deployment gate while automatically picking up newly imported Hub
    modules as the runtime evolves.
    """

    root = Path(repo_root).resolve()
    relevant: set[str] = set(HUB_GIT_STATIC_PATHS)
    clean_network = str(network or "").strip()
    if clean_network:
        relevant.add(f"main_computer/config/{clean_network}_contracts.json")

    package_root = root / "main_computer"
    module_paths: dict[str, str] = {}
    if package_root.is_dir():
        for path in package_root.rglob("*.py"):
            module_name = _module_name_for_path(path, root)
            if module_name:
                module_paths[module_name] = path.relative_to(root).as_posix()

    # Package __init__ files can execute code and therefore belong to the same
    # runtime surface when present.
    init_path = package_root / "__init__.py"
    if init_path.is_file():
        relevant.add(init_path.relative_to(root).as_posix())

    queue = list(HUB_GIT_ENTRY_MODULES)
    seen: set[str] = set()
    while queue:
        module_name = queue.pop(0)
        if module_name in seen:
            continue
        seen.add(module_name)

        # Keep the conventional module path relevant even when the file was
        # deleted locally; Git can then report that deletion.
        if module_name.startswith("main_computer."):
            conventional = module_name.replace(".", "/") + ".py"
            relevant.add(conventional)

        rel = module_paths.get(module_name)
        if not rel:
            continue
        relevant.add(rel)
        path = root / rel

        # Parent package initializers are import-time inputs too.
        parent = path.parent
        while parent != root and parent.is_relative_to(package_root):
            candidate = parent / "__init__.py"
            if candidate.is_file():
                relevant.add(candidate.relative_to(root).as_posix())
            if parent == package_root:
                break
            parent = parent.parent

        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for imported in sorted(_resolve_local_imports(module_name, source, module_paths)):
            if imported not in seen:
                queue.append(imported)

    return tuple(sorted(relevant))


def _git_source_status(repo_root: Path, *, network: str = "mainnet") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    source_paths = _hub_git_source_paths(root, network=network)
    command = [
        "git",
        "-C",
        str(root),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--no-renames",
        "--",
        *source_paths,
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "checked": False,
            "dirty": None,
            "repo_root": str(root),
            "paths": [],
            "relevant_path_count": len(source_paths),
            "error": f"could not execute git: {exc}",
        }
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git status failed").strip()
        return {
            "checked": False,
            "dirty": None,
            "repo_root": str(root),
            "paths": [],
            "relevant_path_count": len(source_paths),
            "error": detail,
        }

    changes: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].strip()
        if path:
            changes.append({"status": status, "path": path})
    return {
        "checked": True,
        "dirty": bool(changes),
        "repo_root": str(root),
        "paths": changes,
        "relevant_path_count": len(source_paths),
        "error": None,
    }


def _check_deployment_git_source(target: Mapping[str, Any]) -> dict[str, Any]:
    """Guard Git-backed Hub birth against relevant uncommitted local code.

    The local context keys are injected by add-hub immediately before invoking
    the deployer and are deliberately not persisted into the frozen operation
    target. Direct low-level deployment tests/callers that omit the local root
    retain their existing behavior.
    """

    repo_root_raw = str(target.get("_local_repo_root") or "").strip()
    force_git = bool(target.get("_force_git", True))
    if not repo_root_raw:
        return {
            "checked": False,
            "dirty": None,
            "blocked": False,
            "force_git": force_git,
            "paths": [],
            "reason": "local-repo-root-unavailable",
        }

    status = _git_source_status(Path(repo_root_raw), network=str(target.get("network") or "mainnet"))
    result = {
        **status,
        "blocked": False,
        "force_git": force_git,
        "reason": "clean" if status.get("checked") and not status.get("dirty") else None,
    }
    if status.get("checked") is not True:
        message = f"Hub deployment Git source could not be inspected: {status.get('error')}"
        if force_git:
            raise HubControlError("HUB_DEPLOY_GIT_INSPECTION_FAILED", message)
        result["reason"] = "inspection-failed-override"
        _progress(f"WARNING: {message}; proceeding because --no-force-git was specified")
        return result

    changes = list(status.get("paths") or [])
    if not changes:
        _progress("deployment source: relevant Hub Git worktree clean")
        return result

    rendered = ", ".join(
        f"{str(item.get('status') or '').strip() or '??'} {item.get('path')}"
        for item in changes[:20]
        if isinstance(item, Mapping)
    )
    if len(changes) > 20:
        rendered += f", ... (+{len(changes) - 20} more)"
    if force_git:
        result["blocked"] = True
        result["reason"] = "relevant-uncommitted-changes"
        commit_push_command = _emit_git_commit_push_command(changes)
        result["commit_push_command"] = commit_push_command
        raise HubControlError(
            "HUB_DEPLOY_GIT_DIRTY",
            "Hub deployment source has relevant uncommitted changes; "
            "copy/paste the emitted HUB_GIT_COMMIT_PUSH_COMMAND, commit/stash them manually, "
            f"or rerun add-hub with --no-force-git. Dirty paths: {rendered}",
        )

    result["reason"] = "dirty-override"
    _progress(
        "WARNING: deployment source has relevant uncommitted changes, but --no-force-git allows deployment; "
        f"Coolify will still build committed Git source. Dirty paths: {rendered}"
    )
    return result


def render_runtime_topology(
    ctx: HubContext,
    *,
    network: str,
    placement: HubPlacement,
    accepted_hubs: list[dict[str, Any]],
    fdb_contract: DependencyContract,
    chain_contract: DependencyContract,
) -> dict[str, Any]:
    profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    network_ingress_url = str(profile.hub_url or "").strip().rstrip("/")
    if not network_ingress_url:
        raise HubControlError(
            "HUB_NETWORK_INGRESS_MISSING",
            f"network {network!r} has no public Hub ingress URL",
        )
    if network_ingress_url == str(placement.public_url).strip().rstrip("/"):
        raise HubControlError(
            "HUB_PUBLIC_URL_IS_NETWORK_INGRESS",
            f"Hub {placement.hub_id!r} public URL aliases network ingress {network_ingress_url!r}",
        )
    hubs: list[dict[str, Any]] = []
    seen_public_urls: set[str] = {str(placement.public_url).strip().rstrip("/")}
    for item in accepted_hubs:
        if not isinstance(item, Mapping):
            continue
        hub_id = str(item.get("hub_id") or "").strip()
        public_url = str(item.get("public_url") or item.get("hub_url") or "").strip().rstrip("/")
        if not hub_id or not public_url:
            continue
        if public_url == network_ingress_url:
            raise HubControlError(
                "HUB_ACCEPTED_PUBLIC_URL_IS_NETWORK_INGRESS",
                f"accepted Hub {hub_id!r} aliases network ingress {network_ingress_url!r}; "
                "remove/rectify that legacy Hub before expanding membership",
            )
        if public_url in seen_public_urls:
            raise HubControlError(
                "HUB_PUBLIC_URL_DUPLICATE",
                f"accepted Hub {hub_id!r} duplicates concrete Hub URL {public_url!r}",
            )
        seen_public_urls.add(public_url)
        hubs.append({"hub_id": hub_id, "hub_url": public_url, "public_url": public_url, "roles": ["entry", "execution"]})
    hubs.append(
        {
            "hub_id": placement.hub_id,
            "hub_url": placement.public_url,
            "public_url": placement.public_url,
            "roles": ["entry", "execution"],
        }
    )
    hubs.sort(key=lambda item: str(item["hub_id"]).encode("utf-8"))
    return {
        "kind": "main_computer.stable_hub_topology.v1",
        "cluster_id": f"main-computer-{network}-hubs",
        "network": {
            "network_key": network,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "chain_id": str(chain_contract.payload["chain_id"]),
            "chain_rpc_url": str(chain_contract.payload["rpc_url"]),
        },
        "storage": {
            "backend": "foundationdb",
            "cluster_file": placement.cluster_file_path,
            "namespace": str(fdb_contract.payload["namespace"]),
            "api_version": int(fdb_contract.payload.get("api_version", 740)),
        },
        # Clients enter through the stable network alias; that frontend then
        # hands them off to one of the concrete enumerated Hub URLs below.
        "entry_urls": [network_ingress_url],
        "hubs": hubs,
    }


def deployment_target(
    ctx: HubContext,
    private: Mapping[str, Any],
    *,
    network: str,
    placement: HubPlacement,
    fdb_contract: DependencyContract,
    chain_contract: DependencyContract,
    accepted_hubs: list[dict[str, Any]],
) -> dict[str, Any]:
    coords = controller_coordinates(private, network, placement.controller_id, base_dir=ctx.mother_private_path.parent)
    topology = render_runtime_topology(
        ctx,
        network=network,
        placement=placement,
        accepted_hubs=accepted_hubs,
        fdb_contract=fdb_contract,
        chain_contract=chain_contract,
    )
    profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    network_ingress_url = str(profile.hub_url or "").strip().rstrip("/")
    controller_has_ingress_hub = any(
        isinstance(item, Mapping)
        and str(item.get("controller_id") or "").strip() == placement.controller_id
        for item in accepted_hubs
    )
    return {
        "controller_id": placement.controller_id,
        "host_id": placement.host_id,
        "coolify": coords,
        "environment_name": f"{network}-hubs",
        "application_name": placement.application_name,
        "legacy_application_name": f"main-computer-{network}-hub",
        "public_url": placement.public_url,
        "network_ingress_url": network_ingress_url,
        "serve_network_ingress": not controller_has_ingress_hub,
        "runtime_dir": placement.runtime_dir,
        "cluster_file_path": placement.cluster_file_path,
        "topology_path": placement.topology_path,
        "hub_bind_port": int(profile.hub_bind_port),
        "network_display_name": profile.display_name,
        "network_kind": profile.kind,
        "bridge_signer_required": profile.kind == "mainnet",
        "bridge_signer_remote_path": f"{placement.runtime_dir.rstrip('/')}/private/bridge-signer/bridge-signer-bundle.json",
        "topology": topology,
        "fdb_contract": fdb_contract.payload,
        "chain_contract": chain_contract.payload,
        "git_repository": "https://github.com/johnrraymond/main_computer",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
    }


def _client(target: Mapping[str, Any], client_factory=legacy.CoolifyClient):
    coolify = target["coolify"]
    return client_factory(str(coolify["url"]), str(coolify["api_token"]))



def _application_environment_name(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    direct = str(payload.get("environment_name") or "").strip()
    if direct:
        return direct
    environment = payload.get("environment")
    if isinstance(environment, Mapping):
        nested = str(environment.get("name") or environment.get("environment_name") or "").strip()
        if nested:
            return nested
    application = payload.get("application")
    if isinstance(application, Mapping):
        return _application_environment_name(application)
    return ""


def _inspect_application_environment(client: Any, application_uuid: str, resolution: Mapping[str, Any] | None = None) -> str:
    if isinstance(resolution, Mapping):
        matches = resolution.get("matches")
        if isinstance(matches, list) and len(matches) == 1:
            from_match = _application_environment_name(matches[0])
            if from_match:
                return from_match
    response = client.request("GET", f"/api/v1/applications/{urllib.parse.quote(application_uuid)}")
    if response.ok:
        return _application_environment_name(response.body)
    return ""


def _environment_mismatch(actual: str, desired: str) -> bool:
    return bool(actual and desired and actual.strip().lower() != desired.strip().lower())


def _ensure_coolify_environment(client: Any, target: Mapping[str, Any], tried: list[dict[str, Any]]) -> dict[str, Any]:
    coolify = target["coolify"]
    project_uuid = str(coolify["project_uuid"]).strip()
    environment_name = str(
        target.get("environment_name") or f"{str(target.get('network') or 'mainnet')}-hubs"
    ).strip()
    if not project_uuid:
        raise HubControlError("HUB_COOLIFY_PROJECT_MISSING", "Hub deployment target has no Coolify project UUID")
    if not environment_name:
        raise HubControlError("HUB_COOLIFY_ENVIRONMENT_MISSING", "Hub deployment target has no Coolify environment name")

    path = f"/api/v1/projects/{urllib.parse.quote(project_uuid)}/environments"

    def resolve() -> tuple[str, list[dict[str, Any]]]:
        _progress(f"Coolify environment: inspect {environment_name!r}")
        response = client.request("GET", path)
        tried.append({"operation": "inspect-environment", "status": response.status})
        if not response.ok:
            raise HubControlError(
                "HUB_COOLIFY_ENVIRONMENT_INSPECT_FAILED",
                f"Coolify environment inspection failed: HTTP {response.status}: {response.body}",
            )
        matches = [
            item
            for item in legacy.body_items(response.body, "environments")
            if str(item.get("name") or "").strip() == environment_name
        ]
        if len(matches) > 1:
            raise HubControlError(
                "HUB_COOLIFY_ENVIRONMENT_AMBIGUOUS",
                f"multiple Coolify environments named {environment_name!r} exist in project {project_uuid!r}",
            )
        return (legacy.item_uuid(matches[0]) if matches else ""), matches

    environment_uuid, matches = resolve()
    if environment_uuid or matches:
        _progress(
            f"Coolify environment: ready name={environment_name!r} uuid={environment_uuid or 'unreported'}"
        )
        return {
            "environment_name": environment_name,
            "environment_uuid": environment_uuid or None,
            "created": False,
        }

    _progress(f"Coolify environment: create {environment_name!r}")
    response = client.request("POST", path, {"name": environment_name})
    tried.append({"operation": "create-environment", "status": response.status})
    if not response.ok and response.status not in {409, 422}:
        raise HubControlError(
            "HUB_COOLIFY_ENVIRONMENT_CREATE_FAILED",
            f"Coolify environment create failed: HTTP {response.status}: {response.body}",
        )

    environment_uuid, matches = resolve()
    if not environment_uuid and not matches:
        raise HubControlError(
            "HUB_COOLIFY_ENVIRONMENT_MISSING",
            f"could not resolve Coolify environment {environment_name!r} after create",
        )
    _progress(
        f"Coolify environment: ready name={environment_name!r} uuid={environment_uuid or 'unreported'} "
        f"created={'yes' if response.ok else 'no'}"
    )
    return {
        "environment_name": environment_name,
        "environment_uuid": environment_uuid or None,
        "created": bool(response.ok),
    }

def inspect_deployment(target: Mapping[str, Any], *, client_factory=legacy.CoolifyClient) -> dict[str, Any]:
    client = _client(target, client_factory)
    tried: list[dict[str, Any]] = []
    _progress(
        "deployment inspection: begin "
        f"hub={target.get('hub_id') or target.get('application_name')} "
        f"application={target.get('application_name')} "
        f"environment={target.get('environment_name')}"
    )
    try:
        _progress(f"deployment inspection: resolve application name={target['application_name']!r}")
        uuid, detail = legacy.find_application(
            client,
            service_name=str(target["application_name"]),
            explicit_uuid="",
            tried=tried,
        )
        if uuid:
            resolution = {"source": "hub-control-name", **detail}
            desired_environment = str(target.get("environment_name") or f"{str(target.get('network') or 'mainnet')}-hubs")
            actual_environment = _inspect_application_environment(client, uuid, resolution)
            mismatch = _environment_mismatch(actual_environment, desired_environment)
            return {
                "application_uuid": uuid,
                "present": True,
                "environment_name": actual_environment or None,
                "desired_environment_name": desired_environment,
                "placement_mismatch": mismatch,
                "resolution": resolution,
            }
        legacy_name = str(target.get("legacy_application_name") or "").strip()
        if legacy_name and legacy_name != str(target["application_name"]):
            _progress(f"deployment inspection: resolve legacy application name={legacy_name!r}")
            legacy_uuid, legacy_detail = legacy.find_application(
                client,
                service_name=legacy_name,
                explicit_uuid="",
                tried=tried,
            )
            if legacy_uuid:
                resolution = {"source": "legacy-hub-application", "legacy_name": legacy_name, **legacy_detail}
                desired_environment = str(target.get("environment_name") or f"{str(target.get('network') or 'mainnet')}-hubs")
                actual_environment = _inspect_application_environment(client, legacy_uuid, resolution)
                mismatch = _environment_mismatch(actual_environment, desired_environment)
                return {
                    "application_uuid": legacy_uuid,
                    "present": True,
                    "migration_candidate": True,
                    "environment_name": actual_environment or None,
                    "desired_environment_name": desired_environment,
                    "placement_mismatch": mismatch,
                    "resolution": resolution,
                }
    except Exception as exc:
        raise HubControlError("HUB_COOLIFY_INSPECT_FAILED", str(exc)) from exc
    return {"application_uuid": None, "present": False, "resolution": {"source": "missing"}}


def _application_name(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    direct = str(payload.get("name") or payload.get("application_name") or "").strip()
    if direct:
        return direct
    application = payload.get("application")
    if isinstance(application, Mapping):
        return _application_name(application)
    return ""


def _inspect_frozen_application(client: Any, target: Mapping[str, Any], application_uuid: str) -> dict[str, Any]:
    path = f"/api/v1/applications/{urllib.parse.quote(application_uuid)}"
    response = client.request("GET", path)
    if response.status == 404:
        return {"present": False, "application_uuid": application_uuid}
    if not response.ok:
        raise HubControlError(
            "HUB_COOLIFY_INSPECT_FAILED",
            f"could not inspect frozen Hub application {application_uuid!r}: HTTP {response.status}: {response.body}",
        )
    actual_name = _application_name(response.body)
    expected_name = str(target.get("application_name") or "").strip()
    if actual_name and expected_name and actual_name != expected_name:
        legacy_name = str(target.get("legacy_application_name") or "").strip()
        migration_candidate = bool(target.get("migration_candidate"))
        if not (migration_candidate and actual_name == legacy_name):
            raise HubControlError(
                "HUB_COOLIFY_APPLICATION_IDENTITY_CHANGED",
                f"frozen Coolify application {application_uuid!r} is now named {actual_name!r}; expected {expected_name!r}",
            )
    actual_environment = _application_environment_name(response.body)
    desired_environment = str(target.get("environment_name") or "").strip()
    if _environment_mismatch(actual_environment, desired_environment):
        raise HubControlError(
            "HUB_COOLIFY_ENVIRONMENT_MISMATCH",
            f"frozen Hub application {application_uuid!r} is in Coolify environment {actual_environment!r}; "
            f"Hub Control requires {desired_environment!r}",
        )
    return {
        "present": True,
        "application_uuid": application_uuid,
        "application_name": actual_name or None,
        "environment_name": actual_environment or None,
    }


def inspect_removed_deployment(
    target: Mapping[str, Any],
    *,
    client_factory=legacy.CoolifyClient,
) -> dict[str, Any]:
    client = _client(target, client_factory)
    frozen_uuid = str(target.get("application_uuid") or "").strip()
    if frozen_uuid:
        frozen = _inspect_frozen_application(client, target, frozen_uuid)
        if frozen.get("present"):
            return {
                "verified": False,
                "verified_absent": False,
                "reason": "frozen-hub-application-still-present",
                "application_uuid": frozen_uuid,
            }

    tried: list[dict[str, Any]] = []
    try:
        current_uuid, detail = legacy.find_application(
            client,
            service_name=str(target["application_name"]),
            explicit_uuid="",
            tried=tried,
        )
    except Exception as exc:
        raise HubControlError("HUB_COOLIFY_INSPECT_FAILED", str(exc)) from exc
    if current_uuid:
        return {
            "verified": False,
            "verified_absent": False,
            "reason": "hub-application-name-still-present",
            "application_uuid": current_uuid,
            "resolution": detail,
        }
    return {
        "verified": True,
        "verified_absent": True,
        "reason": "hub-deployment-absent",
        "application_uuid": frozen_uuid or None,
    }


def remove_deployment(
    target: Mapping[str, Any],
    *,
    client_factory=legacy.CoolifyClient,
    absence_wait_timeout_s: float = 120.0,
    absence_poll_s: float = 2.0,
) -> dict[str, Any]:
    target = dict(target)
    client = _client(target, client_factory)
    frozen_uuid = str(target.get("application_uuid") or "").strip()
    _progress(
        f"removal: begin hub={target.get('hub_id')} application={target.get('application_name')} "
        f"uuid={frozen_uuid or 'absent-at-prep'}"
    )

    if not frozen_uuid:
        tried: list[dict[str, Any]] = []
        try:
            current_uuid, detail = legacy.find_application(
                client,
                service_name=str(target["application_name"]),
                explicit_uuid="",
                tried=tried,
            )
        except Exception as exc:
            raise HubControlError("HUB_COOLIFY_INSPECT_FAILED", str(exc)) from exc
        if current_uuid:
            raise HubControlError(
                "HUB_COOLIFY_DEPLOYMENT_CHANGED",
                f"Hub application {target['application_name']!r} appeared after prep as UUID {current_uuid!r}; "
                "refusing to delete an identity that was not frozen by prep",
            )
        _progress("removal: deployment was already absent at prep and remains absent")
        return {
            "action": "already-absent",
            "application_uuid": None,
            "deployment_deleted": True,
            "verified_absent": True,
            "reason": "hub-deployment-already-absent",
        }

    frozen = _inspect_frozen_application(client, target, frozen_uuid)
    if not frozen.get("present"):
        _progress(f"removal: frozen application uuid={frozen_uuid} already absent")
        verification = inspect_removed_deployment(target, client_factory=lambda *_args: client)
        return {
            "action": "already-absent",
            "application_uuid": frozen_uuid,
            "deployment_deleted": True,
            **verification,
        }

    path = f"/api/v1/applications/{urllib.parse.quote(frozen_uuid)}"
    _progress(f"removal: delete exact frozen application uuid={frozen_uuid}")
    response = client.request("DELETE", path)
    if not response.ok and response.status != 404:
        raise HubControlError(
            "HUB_COOLIFY_DELETE_FAILED",
            f"Hub application delete failed for {frozen_uuid!r}: HTTP {response.status}: {response.body}",
        )

    deadline = time.monotonic() + max(0.0, float(absence_wait_timeout_s))
    next_heartbeat = time.monotonic()
    while True:
        verification = inspect_removed_deployment(target, client_factory=lambda *_args: client)
        if verification.get("verified") is True:
            _progress(f"removal: verified absent uuid={frozen_uuid}")
            return {
                "action": "deleted" if response.status != 404 else "already-absent",
                "application_uuid": frozen_uuid,
                "delete_status": response.status,
                "deployment_deleted": True,
                **verification,
            }
        now = time.monotonic()
        if now >= deadline:
            raise HubControlError(
                "HUB_COOLIFY_DELETE_TIMEOUT",
                f"Hub application {frozen_uuid!r} was not proven absent within {int(absence_wait_timeout_s)} seconds; "
                f"last reason={verification.get('reason')}",
            )
        if now >= next_heartbeat:
            _progress(
                f"removal: waiting for absence uuid={frozen_uuid} reason={verification.get('reason')}"
            )
            next_heartbeat = now + 30.0
        time.sleep(max(0.0, float(absence_poll_s)))


def _domain(public_url: str, port: int) -> str:
    try:
        parsed = urllib.parse.urlsplit(public_url)
    except ValueError:
        return public_url
    if not parsed.hostname or parsed.port is not None:
        return public_url
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return urllib.parse.urlunsplit((parsed.scheme, f"{host}:{port}", parsed.path, parsed.query, parsed.fragment))


def _bootstrap_command(target: Mapping[str, Any]) -> str:
    runtime_dir = str(target["runtime_dir"]).rstrip("/")
    cluster_file = str(target["cluster_file_path"])
    topology_file = str(target["topology_path"])
    command = (
        "sh -lc '"
        f"mkdir -p {runtime_dir};"
        f"printf \"%s\\n\" \"${BOOTSTRAP_FDB_ENV}\">{cluster_file};"
        f"printf %s \"${BOOTSTRAP_TOPOLOGY_ENV}\"|base64 -d>{topology_file};"
        "exec python /app/run-exp-fdb-hub.py'"
    )
    if len(command) > 255:
        raise HubControlError(
            "HUB_BOOTSTRAP_COMMAND_TOO_LONG",
            f"Hub bootstrap command exceeds Coolify start-command limit: {len(command)} bytes",
        )
    return command


def _application_domains(target: Mapping[str, Any]) -> str:
    """Return concrete Hub identity plus the shared ingress alias when this Hub owns ingress on its controller.

    ``public_url`` is always the non-fungible enumerated Hub identity used for
    direct worker/requester continuation. ``network_ingress_url`` is the
    fungible bootstrap hostname. Hub Control assigns the shared ingress alias
    to at most one accepted/new Hub per logical controller so Coolify never has
    two applications on the same controller competing for the same Host rule.
    """

    port = int(target["hub_bind_port"])
    values = [_domain(str(target["public_url"]), port)]
    ingress = str(target.get("network_ingress_url") or "").strip().rstrip("/")
    if bool(target.get("serve_network_ingress")) and ingress:
        ingress_domain = _domain(ingress, port)
        if ingress_domain not in values:
            values.append(ingress_domain)
    return ",".join(values)


def _application_payload(target: Mapping[str, Any]) -> dict[str, Any]:
    coolify = target["coolify"]
    return {
        "name": str(target["application_name"]),
        "description": f"Main Computer {target['application_name']} Hub Control deployment",
        "project_uuid": str(coolify["project_uuid"]),
        "server_uuid": str(coolify["server_uuid"]),
        "environment_name": str(target.get("environment_name") or f"{str(target.get('network') or 'mainnet')}-hubs"),
        "git_repository": str(target["git_repository"]),
        "git_branch": str(target["git_branch"]),
        "build_pack": "dockerfile",
        "base_directory": "/",
        "dockerfile_location": str(target["dockerfile_location"]),
        "ports_exposes": str(target["hub_bind_port"]),
        "domains": _application_domains(target),
        "start_command": _bootstrap_command(target),
        # Hub Control performs the authoritative post-deploy readiness proof.
        # Keep Coolify's application-level rolling health gate disabled here so
        # a slow or failing newborn Hub remains alive long enough for the Hub
        # observer to report the actual FDB/Chain/runtime failure. The image's
        # temporary Docker HEALTHCHECK remains the transport-level keepalive.
        "health_check_enabled": False,
        "health_check_path": "/api/hub/v1/health",
        "instant_deploy": False,
    }


def _ensure_storage(client: Any, target: Mapping[str, Any], application_uuid: str, tried: list[dict[str, Any]]) -> None:
    path = f"/api/v1/applications/{urllib.parse.quote(application_uuid)}/storages"
    response = client.request("GET", path)
    items = legacy.body_items(response.body, "storages", "persistent_storages") if response.ok else []
    mount = str(target["runtime_dir"])
    name = f"{str(target['application_name']).replace('_', '-')}-data"
    for item in items:
        if str(item.get("mount_path") or item.get("mountPath") or "") == mount:
            return
    payload = {"type": "persistent", "name": name, "mount_path": mount, "host_path": mount}
    response = client.request("POST", path, payload)
    tried.append({"operation": "create-storage", "status": response.status})
    if not response.ok:
        raise HubControlError("HUB_COOLIFY_STORAGE_FAILED", f"persistent Hub storage create failed: HTTP {response.status}: {response.body}")



def _deployment_uuid_from_trigger(payload: object) -> str:
    def from_value(value: object) -> str:
        if isinstance(value, Mapping):
            direct = str(value.get("deployment_uuid") or "").strip()
            if direct:
                return direct
            deployments = value.get("deployments")
            if isinstance(deployments, list):
                for item in deployments:
                    found = from_value(item)
                    if found:
                        return found
            body = value.get("body")
            if body is not None and body is not value:
                return from_value(body)
            return ""
        if isinstance(value, list):
            for item in value:
                found = from_value(item)
                if found:
                    return found
        return ""

    return from_value(payload)

def _clean_status(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _deployment_state(payload: Mapping[str, Any]) -> str:
    return _clean_status(payload.get("status"))


def _deployment_log_tail(payload: Mapping[str, Any], *, limit: int = 2400) -> str:
    text = str(payload.get("logs") or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _wait_for_coolify_deployment(
    client: Any,
    deployment_uuid: str,
    *,
    timeout_s: float = 900.0,
    poll_s: float = 5.0,
) -> dict[str, Any]:
    """Wait for the exact queued Coolify deployment to reach a terminal state.

    Hub runtime observation is intentionally a second proof. This wait only proves
    that Coolify finished materializing the requested application revision, so a
    slow image build is not misreported as a Hub/FDB/Chain verification failure.
    """

    clean_uuid = str(deployment_uuid or "").strip()
    if not clean_uuid:
        _progress("Coolify deployment: no deployment UUID returned; exact deployment wait skipped")
        return {"waited": False, "status": "unknown", "reason": "deployment-uuid-unavailable"}
    started = time.monotonic()
    deadline = started + max(0.0, float(timeout_s))
    last_payload: dict[str, Any] = {}
    last_reported_status = ""
    last_report_at = -1.0
    success_states = {"finished", "success", "succeeded", "completed", "complete"}
    failure_states = {"failed", "failure", "error", "cancelled", "canceled"}
    _progress(
        f"Coolify deployment: waiting uuid={clean_uuid} timeout={int(timeout_s)}s poll={float(poll_s):g}s"
    )
    while True:
        path = f"/api/v1/deployments/{urllib.parse.quote(clean_uuid)}"
        response = client.request("GET", path)
        now = time.monotonic()
        elapsed = max(0.0, now - started)
        report_status = "not-found-yet" if response.status == 404 else f"http-{response.status}"
        if response.ok and isinstance(response.body, Mapping):
            last_payload = dict(response.body)
            status = _deployment_state(last_payload)
            report_status = status or "unknown"
            if status in success_states:
                _progress(f"Coolify deployment: finished status={status} elapsed={int(elapsed)}s")
                return {
                    "waited": True,
                    "deployment_uuid": clean_uuid,
                    "status": status,
                    "commit": str(last_payload.get("commit") or "").strip(),
                    "updated_at": last_payload.get("updated_at"),
                }
            if status in failure_states:
                _progress(f"Coolify deployment: terminal failure status={status} elapsed={int(elapsed)}s")
                tail = _deployment_log_tail(last_payload)
                detail = f"Coolify deployment {clean_uuid} ended with status {status!r}"
                if tail:
                    detail += f"; log tail: {tail}"
                raise HubControlError("HUB_COOLIFY_DEPLOYMENT_FAILED", detail)
        elif response.status not in {404}:
            raise HubControlError(
                "HUB_COOLIFY_DEPLOYMENT_INSPECT_FAILED",
                f"could not inspect Coolify deployment {clean_uuid}: HTTP {response.status}: {response.body}",
            )
        if report_status != last_reported_status or last_report_at < 0 or elapsed - last_report_at >= 30.0:
            _progress(f"Coolify deployment: status={report_status} elapsed={int(elapsed)}s")
            last_reported_status = report_status
            last_report_at = elapsed
        if now >= deadline:
            status = _deployment_state(last_payload) or "unknown"
            tail = _deployment_log_tail(last_payload)
            detail = f"Coolify deployment {clean_uuid} did not complete within {int(timeout_s)} seconds; last status={status!r}"
            if tail:
                detail += f"; log tail: {tail}"
            raise HubControlError("HUB_COOLIFY_DEPLOYMENT_TIMEOUT", detail)
        time.sleep(max(0.0, float(poll_s)))



def _bridge_signer_remote_path(target: Mapping[str, Any]) -> str:
    runtime_dir = str(target.get("runtime_dir") or "").strip().rstrip("/")
    if not runtime_dir:
        raise HubControlError("HUB_BRIDGE_SIGNER_TARGET_INVALID", "Hub runtime_dir is required for bridge signer materialization")
    return f"{runtime_dir}/private/bridge-signer/bridge-signer-bundle.json"


def _bridge_signer_required(target: Mapping[str, Any]) -> bool:
    explicit = target.get("bridge_signer_required")
    if explicit is not None:
        return bool(explicit)
    return str(target.get("network_kind") or "").strip().lower() == "mainnet"


def _render_contract_deployer_command(target: Mapping[str, Any]) -> str:
    """Return the existing mainnet contract deployer as one copy/paste PowerShell line.

    The command reads private authority from the established private-state file
    at execution time.  No private key is embedded in or printed by this helper.
    ``mainnet-operator deploy-contracts`` intentionally deploys the complete root
    contract set so the public deployment manifest remains complete.
    """

    network = str(target.get("network") or "mainnet").strip() or "mainnet"
    chain = target.get("chain_contract") if isinstance(target.get("chain_contract"), Mapping) else {}
    chain_id = int(chain.get("chain_id") or 0)
    rpc_url = str(chain.get("rpc_url") or "").strip()
    if not chain_id or not rpc_url:
        return ""

    env_name = f"{network.upper().replace('-', '_')}_DEPLOYER_PRIVATE_KEY"
    state_path = "runtime/state/main_computer.private.yaml"
    key_expr = (
        "import yaml; "
        f"s=yaml.safe_load(open(r'{state_path}',encoding='utf-8')); "
        f"print(s['networks']['{network}']['wallets']['deployer']['private_key'])"
    )
    office_expr = (
        "import yaml; "
        f"s=yaml.safe_load(open(r'{state_path}',encoding='utf-8')); "
        f"w=s['networks']['{network}']['wallets']; "
        "print(','.join(str(w[r]['address']) for r in ('captain','o1','o2','o3')))"
    )
    return (
        f'$env:{env_name} = python -c "{key_expr}"; '
        f'$offices = python -c "{office_expr}"; '
        "python .\\tools\\mainnet-operator.py deploy-contracts "
        f"--target-environment {network} "
        f"--chain-id {chain_id} "
        f"--rpc-url {_powershell_single_quote(rpc_url)} "
        f"--container-rpc-url {_powershell_single_quote(rpc_url)} "
        "--external-docker-network bridge "
        f"--private-key-env {env_name} "
        "--offices $offices --yes"
    )


def _emit_contract_deployer_command(target: Mapping[str, Any]) -> str:
    command = _render_contract_deployer_command(target)
    if command:
        print("HUB_CONTRACT_DEPLOYER_COMMAND:", file=sys.stderr, flush=True)
        print(command, file=sys.stderr, flush=True)
        print(
            "HUB_CONTRACT_DEPLOYER_NOTE: replace --yes with --dry-run to preview first; "
            "after a successful deploy, rerun the same add-hub command so prep can freeze the new Chain contract authority.",
            file=sys.stderr,
            flush=True,
        )
    return command


def _hub_chain_rpc(rpc_url: str, method: str, params: list[Any], *, timeout_s: float = 12.0) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    request = urllib.request.Request(
        rpc_url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": CHAIN_RPC_USER_AGENT,
            "X-Main-Computer-Client": "chain-rpc",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - converted to operator-facing Hub error
        raise HubControlError(
            "HUB_BRIDGE_ESCROW_PREFLIGHT_RPC_FAILED",
            f"could not verify the HubCreditBridgeEscrow through {rpc_url}: {type(exc).__name__}: {exc}",
        ) from exc
    if not isinstance(payload, Mapping) or payload.get("error") is not None:
        raise HubControlError(
            "HUB_BRIDGE_ESCROW_PREFLIGHT_RPC_FAILED",
            f"JSON-RPC {method} failed while verifying HubCreditBridgeEscrow: {payload}",
        )
    return payload.get("result")


def _bridge_controller_call_data(address: str) -> str:
    raw = str(address or "").strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) != 40 or any(ch not in "0123456789abcdef" for ch in raw):
        raise HubControlError(
            "HUB_BRIDGE_SIGNER_SOURCE_INVALID",
            f"bridge controller address is invalid: {address!r}",
        )
    return "0x" + IS_BRIDGE_CONTROLLER_SELECTOR + raw.rjust(64, "0")


def _verify_live_bridge_signer_contract(
    target: Mapping[str, Any],
    bridge_signer: Mapping[str, Any],
    *,
    timeout_s: float = 12.0,
) -> dict[str, Any]:
    """Fail before Coolify mutation unless the signer contract is live and authorized."""

    chain = target.get("chain_contract") if isinstance(target.get("chain_contract"), Mapping) else {}
    rpc_url = str(chain.get("rpc_url") or "").strip()
    expected_chain_id = int(chain.get("chain_id") or 0)
    escrow = str(bridge_signer.get("escrow_address") or "").strip()
    controller = str(bridge_signer.get("bridge_controller_address") or "").strip()
    if not rpc_url or not expected_chain_id or not escrow or not controller:
        raise HubControlError(
            "HUB_BRIDGE_SIGNER_SOURCE_INVALID",
            "bridge signer live preflight requires chain RPC, chain id, escrow address, and controller address",
        )

    _progress(f"deployment signer preflight: verify escrow={escrow} controller={controller}")
    raw_chain_id = _hub_chain_rpc(rpc_url, "eth_chainId", [], timeout_s=timeout_s)
    try:
        actual_chain_id = int(str(raw_chain_id), 16)
    except Exception as exc:
        raise HubControlError(
            "HUB_BRIDGE_ESCROW_PREFLIGHT_RPC_FAILED",
            f"chain RPC returned invalid eth_chainId while verifying HubCreditBridgeEscrow: {raw_chain_id!r}",
        ) from exc
    if actual_chain_id != expected_chain_id:
        raise HubControlError(
            "HUB_BRIDGE_ESCROW_PREFLIGHT_CHAIN_MISMATCH",
            f"bridge signer preflight reached chain id {actual_chain_id}, expected {expected_chain_id}",
        )

    code = str(_hub_chain_rpc(rpc_url, "eth_getCode", [escrow, "latest"], timeout_s=timeout_s) or "").strip()
    if code.lower() in {"", "0x", "0x0"}:
        _emit_contract_deployer_command(target)
        raise HubControlError(
            "HUB_BRIDGE_ESCROW_NOT_LIVE",
            f"HubCreditBridgeEscrow has no bytecode at {escrow} on chain {expected_chain_id}; "
            "run the emitted HUB_CONTRACT_DEPLOYER_COMMAND, then rerun add-hub",
        )

    call_data = _bridge_controller_call_data(controller)
    result = str(
        _hub_chain_rpc(
            rpc_url,
            "eth_call",
            [{"to": escrow, "data": call_data}, "latest"],
            timeout_s=timeout_s,
        )
        or ""
    ).strip()
    hex_data = result[2:] if result.startswith("0x") else result
    if len(hex_data) < 64 or any(ch not in "0123456789abcdefABCDEF" for ch in hex_data):
        _emit_contract_deployer_command(target)
        raise HubControlError(
            "HUB_BRIDGE_ESCROW_INTERFACE_INVALID",
            f"HubCreditBridgeEscrow at {escrow} did not return a valid isBridgeController(address) result; "
            "run the emitted HUB_CONTRACT_DEPLOYER_COMMAND, then rerun add-hub",
        )
    authorized = int(hex_data[-64:], 16) != 0
    if not authorized:
        raise HubControlError(
            "HUB_BRIDGE_CONTROLLER_NOT_AUTHORIZED",
            f"assigned Hub admin {controller} is not authorized by HubCreditBridgeEscrow {escrow}; "
            "rerun add-hub to check or establish authorization through the contract owner",
        )

    _progress(
        "deployment signer preflight: verified "
        f"chain_id={actual_chain_id} escrow={escrow} controller={controller}"
    )
    return {
        "verified": True,
        "chain_id": actual_chain_id,
        "rpc_url": rpc_url,
        "escrow_address": escrow,
        "bridge_controller_address": controller,
        "code_bytes": max(0, (len(code) - 2) // 2) if code.startswith("0x") else len(code) // 2,
        "bridge_controller_authorized": True,
    }


def _build_bridge_signer_for_deployment(target: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build the existing canonical Hub bridge-controller bundle without logging secrets.

    Hub Control reuses the signer-bundle builder from ``coolify_hub_service``,
    but supplies its already-reserved Hub administrator wallet explicitly.
    Legacy deployment manifests never select the bridge-controller identity.
    """

    if not _bridge_signer_required(target):
        return None
    repo_root_text = str(target.get("_local_repo_root") or "").strip()
    if not repo_root_text:
        raise HubControlError(
            "HUB_BRIDGE_SIGNER_SOURCE_UNAVAILABLE",
            "Hub bridge signing is required but the local repository root was not supplied to the deployer",
        )
    repo_root = Path(repo_root_text).resolve()
    network = str(target.get("network") or "").strip()
    if not network:
        raise HubControlError("HUB_BRIDGE_SIGNER_SOURCE_UNAVAILABLE", "Hub bridge signing requires a network key")
    admin = target.get("_hub_admin_wallet")
    expected_admin = str(target.get("hub_admin_address") or "").strip()
    if not isinstance(admin, Mapping) or not expected_admin or str(admin.get("address") or "").lower() != expected_admin.lower():
        raise HubControlError("HUB_BRIDGE_SIGNER_SOURCE_INVALID", "bridge signer must be the Hub's committed admin identity")
    chain = target.get("chain_contract") or {}
    escrow = str((chain.get("contracts") or {}).get("hub_credit_bridge_escrow") or "")
    try:
        profile = load_hub_network_registry(repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
        args = argparse.Namespace(bridge_signer_env_key=BRIDGE_SIGNER_ENV)
        bundle = legacy.build_bridge_signer_bundle(
            profile, args, wallet_override=admin,
            chain_override={"chain_id": chain.get("chain_id"), "chain_rpc_url": chain.get("rpc_url"),
                            "escrow_address": escrow, "private_state_path": target.get("hub_admin_private_state_path")},
        )
    except Exception as exc:
        raise HubControlError("HUB_BRIDGE_SIGNER_SOURCE_INVALID", f"could not build bridge signer from assigned Hub admin: {type(exc).__name__}: {exc}") from exc
    expected_chain_id = int((target.get("chain_contract") or {}).get("chain_id") or 0)
    actual_chain_id = int(bundle.get("chain_id") or 0)
    if expected_chain_id and actual_chain_id and actual_chain_id != expected_chain_id:
        raise HubControlError(
            "HUB_BRIDGE_SIGNER_CHAIN_MISMATCH",
            f"bridge signer bundle chain id {actual_chain_id} does not match frozen Hub chain id {expected_chain_id}",
        )
    expected_escrow = str(((target.get("chain_contract") or {}).get("contracts") or {}).get("hub_credit_bridge_escrow") or "").strip()
    actual_escrow = str(bundle.get("escrow_address") or "").strip()
    if expected_escrow and actual_escrow and expected_escrow.lower() != actual_escrow.lower():
        raise HubControlError(
            "HUB_BRIDGE_SIGNER_ESCROW_MISMATCH",
            f"bridge signer bundle escrow {actual_escrow} does not match frozen Hub escrow {expected_escrow}",
        )

    encoded = str(bundle.get("bundle_b64") or "").strip()
    if not encoded:
        raise HubControlError("HUB_BRIDGE_SIGNER_SOURCE_INVALID", "bridge signer builder returned no signer bundle")
    return {
        "env_key": BRIDGE_SIGNER_ENV,
        "bundle_b64": encoded,
        "bundle_sha256": str(bundle.get("bundle_sha256") or ""),
        "bundle_bytes": int(bundle.get("bundle_bytes") or 0),
        "source_manifest": str(bundle.get("source_manifest") or "mother-private-state"),
        "bridge_controller_address": str(bundle.get("bridge_controller_address") or ""),
        "escrow_address": actual_escrow,
        "chain_id": actual_chain_id,
        "remote_path": _bridge_signer_remote_path(target),
    }


def apply_deployment(
    target: Mapping[str, Any],
    *,
    client_factory=legacy.CoolifyClient,
    deployment_wait_timeout_s: float = 900.0,
    deployment_poll_s: float = 5.0,
) -> dict[str, Any]:
    target = dict(target)
    target.setdefault("network", str(target.get("chain_contract", {}).get("network") or "mainnet"))
    git_source_check = _check_deployment_git_source(target)
    bridge_signer = _build_bridge_signer_for_deployment(target)
    bridge_signer_preflight: dict[str, Any] | None = None
    if bridge_signer is not None:
        bridge_signer_preflight = _verify_live_bridge_signer_contract(target, bridge_signer)
        _progress(
            "deployment signer: ready "
            f"controller={bridge_signer.get('bridge_controller_address')} "
            f"bundle_sha256={bridge_signer.get('bundle_sha256')}"
        )
    client = _client(target, client_factory)
    tried: list[dict[str, Any]] = []
    _progress(
        "deployment: begin "
        f"hub={target.get('hub_id') or target.get('application_name')} "
        f"application={target.get('application_name')} "
        f"environment={target.get('environment_name')}"
    )
    try:
        frozen_uuid = str(target.get("application_uuid") or "").strip()
        if frozen_uuid:
            _progress(f"deployment: inspect frozen application uuid={frozen_uuid}")
            app_uuid, _detail = frozen_uuid, {"source": "frozen-prep", "uuid": frozen_uuid}
            desired_environment = str(target.get("environment_name") or f"{str(target.get('network') or 'mainnet')}-hubs")
            actual_environment = _inspect_application_environment(client, app_uuid)
            if _environment_mismatch(actual_environment, desired_environment):
                raise HubControlError(
                    "HUB_COOLIFY_ENVIRONMENT_MISMATCH",
                    f"Hub application {app_uuid!r} is in Coolify environment {actual_environment!r}; "
                    f"Hub Control requires {desired_environment!r}. Remove or explicitly migrate the misplaced application, then run prep again.",
                )
        else:
            _progress(f"deployment: resolve existing application name={target['application_name']!r}")
            app_uuid, _detail = legacy.find_application(
                client,
                service_name=str(target["application_name"]),
                explicit_uuid="",
                tried=tried,
            )
        payload = _application_payload(target)
        action = "migrated" if frozen_uuid and target.get("migration_candidate") else "updated"
        environment_result: dict[str, Any] | None = None
        if not app_uuid:
            _progress("deployment: application absent; preparing dedicated Hub environment")
            environment_result = _ensure_coolify_environment(client, target, tried)
            _progress(f"deployment: create application name={target['application_name']!r}")
            response = client.request("POST", "/api/v1/applications/public", payload)
            if not response.ok:
                raise HubControlError("HUB_COOLIFY_CREATE_FAILED", f"Hub application create failed: HTTP {response.status}: {response.body}")
            app_uuid = legacy.item_uuid(response.body) if isinstance(response.body, dict) else ""
            if not app_uuid and isinstance(response.body, dict) and isinstance(response.body.get("application"), dict):
                app_uuid = legacy.item_uuid(response.body["application"])
            if not app_uuid:
                raise HubControlError("HUB_COOLIFY_CREATE_FAILED", "Hub application create succeeded without returning a UUID")
            action = "created"
            _progress(f"deployment: application created uuid={app_uuid}")
        else:
            _progress(f"deployment: update application uuid={app_uuid}")
            update = {k: v for k, v in payload.items() if k not in {"project_uuid", "server_uuid", "environment_name", "git_repository"}}
            response = client.request("PATCH", f"/api/v1/applications/{urllib.parse.quote(app_uuid)}", update)
            if not response.ok and response.status not in {405}:
                raise HubControlError("HUB_COOLIFY_UPDATE_FAILED", f"Hub application update failed: HTTP {response.status}: {response.body}")
            if not response.ok:
                response = client.request("PUT", f"/api/v1/applications/{urllib.parse.quote(app_uuid)}", update)
                if not response.ok:
                    raise HubControlError("HUB_COOLIFY_UPDATE_FAILED", f"Hub application update failed: HTTP {response.status}: {response.body}")

        _progress(f"deployment: ensure persistent storage application={app_uuid}")
        _ensure_storage(client, target, app_uuid, tried)
        _progress("deployment: persistent storage ready")
        topology_b64 = base64.b64encode(canonical_bytes(target["topology"])).decode("ascii")
        env_values = {
            # Long-form projection values remain the canonical launcher contract.
            FDB_CLUSTER_ENV: str(target["fdb_contract"]["connection_string"]),
            TOPOLOGY_B64_ENV: topology_b64,
            # Short aliases exist only for the Coolify start-command bootstrap.
            # They let Hub Control materialize the frozen artifacts even when
            # the remote Git revision predates launcher-side projection support.
            BOOTSTRAP_FDB_ENV: str(target["fdb_contract"]["connection_string"]),
            BOOTSTRAP_TOPOLOGY_ENV: topology_b64,
            "MAIN_COMPUTER_HUB_NETWORK": str(target["network"]),
            "MAIN_COMPUTER_HUB_PORT": str(target["hub_bind_port"]),
            "MAIN_COMPUTER_HUB_URL": str(target["public_url"]),
            "MAIN_COMPUTER_HUB_ROOT": str(target["runtime_dir"]),
            "MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE": str(target["cluster_file_path"]),
            "MAIN_COMPUTER_HUB_FDB_NAMESPACE": str(target["fdb_contract"]["namespace"]),
            "MAIN_COMPUTER_HUB_CHAIN_ID": str(target["chain_contract"]["chain_id"]),
            "MAIN_COMPUTER_HUB_CHAIN_RPC_URL": str(target["chain_contract"]["rpc_url"]),
            "MAIN_COMPUTER_HUB_CONTRACTS_PATH": f"/app/main_computer/config/{target['network']}_contracts.json",
            "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER": "false" if bridge_signer is not None else "true",
            "MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES": "true" if bridge_signer is not None else "false",
            "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH": (
                str(bridge_signer["remote_path"]) if bridge_signer is not None else ""
            ),
            "MAIN_COMPUTER_HUB_BRIDGE_BACKEND": "dev-chain",
            FDB_CONTRACT_ENV: str(target["fdb_contract"]["sha256"]),
            CHAIN_CONTRACT_ENV: str(target["chain_contract"]["sha256"]),
            "MAIN_COMPUTER_HUB_CONTROL_HUB_ID": str(target["hub_id"]),
            "MAIN_COMPUTER_HUB_CONTROL_PUBLIC_URL": str(target["public_url"]),
            "MAIN_COMPUTER_HUB_CONTROL_RUNTIME_DIR": str(target["runtime_dir"]),
            "MAIN_COMPUTER_HUB_CONTROL_CLUSTER_FILE": str(target["cluster_file_path"]),
            "MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH": str(target["topology_path"]),
            "MAIN_COMPUTER_HUB_CONTROL_NETWORK": str(target["network"]),
            "MAIN_COMPUTER_HUB_CONTROL_NETWORK_DISPLAY_NAME": str(target["network_display_name"]),
            "MAIN_COMPUTER_HUB_CONTROL_NETWORK_KIND": str(target["network_kind"]),
            "MAIN_COMPUTER_HUB_CONTROL_CHAIN_ID": str(target["chain_contract"]["chain_id"]),
            "MAIN_COMPUTER_HUB_CONTROL_CHAIN_RPC_URL": str(target["chain_contract"]["rpc_url"]),
            "MAIN_COMPUTER_HUB_CONTROL_FDB_NAMESPACE": str(target["fdb_contract"]["namespace"]),
            "MAIN_COMPUTER_HUB_CONTROL_FDB_API_VERSION": str(target["fdb_contract"].get("api_version", 740)),
        }
        admin = target.get("_hub_admin_wallet")
        expected_admin = str(target.get("hub_admin_address") or "").strip()
        if expected_admin:
            if not isinstance(admin, Mapping) or str(admin.get("address") or "").lower() != expected_admin.lower():
                raise HubControlError("HUB_ADMIN_INSTALL_MISSING", "committed Hub administrator signer is missing from deployment")
            bundle = {
                "schema": HUB_ADMIN_BUNDLE_SCHEMA,
                "network": str(target["network"]),
                "hub_id": str(target["hub_id"]),
                "address": expected_admin,
                "private_key": str(admin["private_key"]),
            }
            env_values[HUB_ADMIN_BUNDLE_ENV] = base64.b64encode(canonical_bytes(bundle)).decode("ascii")
        _progress(f"deployment: synchronize {len(env_values)} environment variables")
        for index, (key, value) in enumerate(env_values.items(), start=1):
            _progress(f"deployment: env {index}/{len(env_values)} key={key}")
            legacy.sync_application_env_var(client, application_uuid=app_uuid, key=key, value=value, tried=tried)
        _progress(f"deployment: trigger forced deploy application={app_uuid}")
        deploy_trigger = legacy.trigger_deploy(client, application_uuid=app_uuid, force=True, tried=tried)
        deployment_uuid = _deployment_uuid_from_trigger(deploy_trigger)
        _progress(f"deployment: deploy queued uuid={deployment_uuid or 'unavailable'}")
        deployment_wait = _wait_for_coolify_deployment(
            client,
            deployment_uuid,
            timeout_s=deployment_wait_timeout_s,
            poll_s=deployment_poll_s,
        )
        _progress(
            f"deployment: materialization complete application={app_uuid} "
            f"status={deployment_wait.get('status')}"
        )
        return {
            "application_uuid": app_uuid,
            "action": action,
            "environment_variables": sorted(env_values),
            "deployment_uuid": deployment_uuid or None,
            "deployment_status": deployment_wait.get("status"),
            "deployment_commit": deployment_wait.get("commit") or None,
            "deployment_waited": bool(deployment_wait.get("waited")),
            "environment_name": str(payload["environment_name"]),
            "environment_created": bool(environment_result and environment_result.get("created")),
            "git_source_check": git_source_check,
            "bridge_signer": (
                {
                    "required": True,
                    "configured": True,
                    "bundle_sha256": bridge_signer.get("bundle_sha256"),
                    "bundle_bytes": bridge_signer.get("bundle_bytes"),
                    "source_manifest": bridge_signer.get("source_manifest"),
                    "bridge_controller_address": bridge_signer.get("bridge_controller_address"),
                    "escrow_address": bridge_signer.get("escrow_address"),
                    "chain_id": bridge_signer.get("chain_id"),
                    "remote_path": bridge_signer.get("remote_path"),
                    "live_preflight": bridge_signer_preflight,
                }
                if bridge_signer is not None
                else {"required": False, "configured": False}
            ),
        }
    except HubControlError:
        raise
    except Exception as exc:
        raise HubControlError("HUB_COOLIFY_DEPLOY_FAILED", str(exc)) from exc


def _get_json(url: str, *, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MainComputerHubControl/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise HubControlError("HUB_OBSERVER_INVALID", f"Hub observer returned non-object JSON from {url}")
    return payload


def observe_hub(target: Mapping[str, Any], *, wait_timeout_s: float = 300.0, request_timeout_s: float = 12.0) -> dict[str, Any]:
    base = str(target["public_url"]).rstrip("/")
    started = time.monotonic()
    deadline = started + max(0.0, wait_timeout_s)
    last_error: object = None
    last_observation: dict[str, Any] = {}
    last_report_signature: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    last_report_at = -1.0
    attempt = 0
    _progress(
        f"runtime verification: begin hub={target.get('hub_id')} url={base} timeout={int(wait_timeout_s)}s"
    )
    while True:
        attempt += 1
        health: dict[str, Any] | None = None
        identity: dict[str, Any] | None = None
        status: dict[str, Any] | None = None
        endpoint_errors: dict[str, str] = {}
        for label, path in (
            ("health", "/api/hub/v1/health"),
            ("identity", "/api/hub/v1/hub-identity"),
            ("status", "/api/hub/v1/status"),
        ):
            try:
                payload = _get_json(base + path, timeout_s=request_timeout_s)
                if label == "health":
                    health = payload
                elif label == "identity":
                    identity = payload
                else:
                    status = payload
            except Exception as exc:  # noqa: BLE001
                endpoint_errors[label] = f"{type(exc).__name__}: {exc}"

        expected_fdb = target["fdb_contract"]
        expected_chain = target["chain_contract"]
        network = identity.get("network") if isinstance(identity, Mapping) and isinstance(identity.get("network"), Mapping) else {}
        storage = identity.get("storage") if isinstance(identity, Mapping) and isinstance(identity.get("storage"), Mapping) else {}
        status_network = status.get("network") if isinstance(status, Mapping) and isinstance(status.get("network"), Mapping) else {}
        bridge_backend = status.get("bridge_backend") if isinstance(status, Mapping) and isinstance(status.get("bridge_backend"), Mapping) else {}
        signer_required = _bridge_signer_required(target)

        def int_matches(raw: object, expected: object) -> bool:
            try:
                return int(raw) == int(expected)
            except (TypeError, ValueError):
                return False

        hub_running = isinstance(health, Mapping) and health.get("ok") is True
        checks = {
            "health": hub_running,
            "hub_identity": isinstance(identity, Mapping) and identity.get("hub_id") == target["hub_id"],
            "fdb_backend": storage.get("backend") == "foundationdb",
            "fdb_cluster_file": str(storage.get("cluster_file") or "") == str(target["cluster_file_path"]),
            "fdb_namespace": str(storage.get("namespace") or "") == str(expected_fdb["namespace"]),
            "chain_id": int_matches(network.get("chain_id"), expected_chain["chain_id"]),
            "chain_rpc": str(network.get("chain_rpc_url") or "").rstrip("/") == str(expected_chain["rpc_url"]).rstrip("/"),
            "status_chain_id": int_matches(status_network.get("chain_id"), expected_chain["chain_id"]),
            "status_rpc": str(status_network.get("chain_rpc_url") or "").rstrip("/") == str(expected_chain["rpc_url"]).rstrip("/"),
        }
        if signer_required:
            checks["bridge_signer_configured"] = bridge_backend.get("signer_configured") is True
            checks["bridge_controller_authorized"] = bridge_backend.get("bridge_controller_authorized") is True
            checks["bridge_write_operations"] = bridge_backend.get("write_operations_enabled") is True
            checks["bridge_signer_mode"] = str(bridge_backend.get("mode") or "") == "bridge-signer"
        expected_admin = str(target.get("hub_admin_address") or "").strip()
        if expected_admin:
            hub_admin = identity.get("hub_admin") if isinstance(identity, Mapping) and isinstance(identity.get("hub_admin"), Mapping) else {}
            checks["hub_admin_wallet_loaded"] = hub_admin.get("wallet_loaded") is True
            checks["hub_admin_address"] = str(hub_admin.get("address") or "").lower() == expected_admin.lower()
        fdb_verified = all(checks[key] for key in ("hub_identity", "fdb_backend", "fdb_cluster_file", "fdb_namespace"))
        chain_verified = all(checks[key] for key in ("hub_identity", "chain_id", "chain_rpc", "status_chain_id", "status_rpc"))
        bridge_signer_verified = (
            not signer_required
            or all(
                checks[key]
                for key in (
                    "bridge_signer_configured",
                    "bridge_controller_authorized",
                    "bridge_write_operations",
                    "bridge_signer_mode",
                )
            )
        )
        last_observation = {
            "checks": checks,
            "endpoint_errors": endpoint_errors,
            "health": health,
            "identity": identity,
            "status": status,
        }
        if all(checks.values()):
            elapsed = max(0.0, time.monotonic() - started)
            _progress(f"runtime verification: verified attempt={attempt} elapsed={int(elapsed)}s")
            return {
                "verified": True,
                "reason": (
                    "hub-fdb-chain-and-bridge-signer-consumption-verified"
                    if signer_required
                    else "hub-fdb-and-chain-consumption-verified"
                ),
                "hub_running": True,
                "fdb_adoption_verified": True,
                "chain_adoption_verified": True,
                "bridge_signer_verified": bridge_signer_verified,
                "hub_admin_verified": (not expected_admin or (checks["hub_admin_wallet_loaded"] and checks["hub_admin_address"])),
                **last_observation,
            }
        failed_checks = [key for key, value in checks.items() if not value]
        last_error = {
            "failed_checks": failed_checks,
            "endpoint_errors": endpoint_errors,
        }
        now = time.monotonic()
        elapsed = max(0.0, now - started)
        signature = (tuple(failed_checks), tuple(sorted(endpoint_errors)))
        if signature != last_report_signature or last_report_at < 0 or elapsed - last_report_at >= 30.0:
            errors = ",".join(sorted(endpoint_errors)) or "none"
            failed = ",".join(failed_checks) or "none"
            _progress(
                f"runtime verification: pending attempt={attempt} elapsed={int(elapsed)}s "
                f"failed={failed} endpoint_errors={errors}"
            )
            last_report_signature = signature
            last_report_at = elapsed
        if now >= deadline:
            _progress(
                f"runtime verification: timeout elapsed={int(elapsed)}s "
                f"hub_running={'yes' if hub_running else 'no'} "
                f"fdb={'verified' if fdb_verified else 'not-verified'} "
                f"chain={'verified' if chain_verified else 'not-verified'} "
                f"bridge_signer={'verified' if bridge_signer_verified else 'not-verified'}"
            )
            return {
                "verified": False,
                "reason": "hub-runtime-verification-timeout",
                "hub_running": hub_running,
                "fdb_adoption_verified": fdb_verified,
                "chain_adoption_verified": chain_verified,
                "bridge_signer_verified": bridge_signer_verified,
                "hub_admin_verified": (not expected_admin or (checks["hub_admin_wallet_loaded"] and checks["hub_admin_address"])),
                "last_error": last_error,
                **last_observation,
            }
        time.sleep(5.0)

