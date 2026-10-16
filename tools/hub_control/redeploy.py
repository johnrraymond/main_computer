"""In-place Hub code redeployment helpers used by the operator harness.

This path does not call apply_deployment(): it must not recreate applications or
change membership/storage. It ensures assigned bridge-controller authorization
before installing signer material and rebuilding an existing Hub.
"""

from __future__ import annotations

import subprocess
import json
import os
from contextlib import contextmanager
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .common.deployment import (
    _client,
    _deployment_uuid_from_trigger,
    _emit_git_commit_push_command,
    _git_source_status,
    _inspect_frozen_application,
    _wait_for_coolify_deployment,
    _deployment_state,
    _build_bridge_signer_for_deployment,
    _verify_live_bridge_signer_contract,
    BRIDGE_SIGNER_ENV,
    inspect_deployment,
    observe_hub,
    private_https_routes,
)
from .common.errors import HubControlError
from .common.chain_contract import load_current_chain_contract
from .common.bridge_controller_authorization import ensure_bridge_controller
from .common.fdb_contract import load_current_fdb_contract
from .common.models import HubContext
from .common.privates import controller_coordinates, load_private
from .common.state import read_accepted


class RedeployError(RuntimeError):
    """An operator-visible read-only or deployment failure."""


class BuildSubmissionRejected(RedeployError):
    """Coolify definitively rejected the request without queuing a build."""


def accepted_targets(ctx: HubContext, network: str, hub: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    accepted = read_accepted(ctx, network)
    if accepted is None or not accepted.get("hubs"):
        raise RedeployError(f"no accepted Hubs to redeploy on {network}")
    hubs = accepted["hubs"]
    if not isinstance(hubs, list) or any(not isinstance(item, dict) for item in hubs):
        raise RedeployError("invalid accepted Hub entries")
    by_id: dict[str, dict[str, Any]] = {}
    for item in hubs:
        hub_id = str(item.get("hub_id") or "").strip()
        if not hub_id or hub_id in by_id:
            raise RedeployError("accepted Hub topology has missing or duplicate Hub ids")
        by_id[hub_id] = item
    if hub:
        if hub not in by_id:
            raise RedeployError(f"Hub {hub!r} is not in the accepted {network} topology")
        selected = [by_id[hub]]
    else:
        selected = [by_id[key] for key in sorted(by_id)]
    return accepted, selected


def git_revision(repo_root: Path, network: str) -> str:
    """Require that committed Hub inputs are the Git revision deployed from main."""
    status = _git_source_status(repo_root, network=network)
    if not status.get("checked"):
        raise RedeployError(f"could not verify Hub Git inputs: {status.get('error')}")
    if status.get("dirty"):
        changes = list(status.get("paths") or [])
        paths = ", ".join(str(item.get("path")) for item in changes[:12])
        _emit_git_commit_push_command(changes)
        raise RedeployError(
            f"Hub source has uncommitted changes: {paths}; "
            "copy/paste the emitted HUB_GIT_COMMIT_PUSH_COMMAND, then rerun redeploy"
        )

    def git(*arguments: str) -> str:
        try:
            process = subprocess.run(
                ["git", "-C", str(repo_root), *arguments],
                capture_output=True, text=True, check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RedeployError(f"cannot verify pushed Hub source: {exc}") from exc
        if process.returncode != 0:
            raise RedeployError(f"cannot verify pushed Hub source: {(process.stderr or process.stdout).strip()}")
        return process.stdout.strip()

    local = git("rev-parse", "HEAD")
    remote = git("ls-remote", "origin", "refs/heads/main").split()
    if len(remote) != 2 or remote[1] != "refs/heads/main" or len(local) != 40:
        raise RedeployError("cannot resolve origin/main Git revision")
    if local.lower() != remote[0].lower():
        raise RedeployError(
            "Hub source has no uncommitted Hub inputs, but local HEAD is not pushed "
            "to origin/main; no new commit is required for the current working tree. "
            "If HEAD is the intended revision, run: git push origin HEAD "
            "(resolve any remote divergence before retrying)"
        )
    return local.lower()


def resolve_existing_hub(ctx: HubContext, network: str, member: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve an existing application by accepted membership, never create it."""
    hub_id = str(member["hub_id"])
    controller_id = str(member.get("controller_id") or "").strip()
    public_url = str(member.get("public_url") or "").strip().rstrip("/")
    if not controller_id or not public_url:
        raise RedeployError(f"accepted Hub {hub_id} is missing controller or URL")
    ctx_private = load_private(ctx)
    coolify = controller_coordinates(
        ctx_private, network, controller_id, base_dir=ctx.mother_private_path.parent,
    )
    runtime_dir = f"/data/main-computer/hub/{hub_id}"
    target: dict[str, Any] = {
        "hub_id": hub_id,
        "network": network,
        "controller_id": controller_id,
        "public_url": public_url,
        "coolify": coolify,
        "application_name": f"main-computer-{hub_id}",
        "environment_name": f"{network}-hubs",
        "runtime_dir": runtime_dir,
        "cluster_file_path": f"{runtime_dir}/fdb.cluster",
        "hub_admin_address": str(member.get("hub_admin_address") or ""),
        "fdb_contract": load_current_fdb_contract(ctx, network).payload,
        "chain_contract": load_current_chain_contract(ctx, network).payload,
        "bridge_signer_required": network == "mainnet",
    }
    found = inspect_deployment(target)
    uuid = str(found.get("application_uuid") or "").strip()
    if not found.get("present") or not uuid:
        raise RedeployError(f"existing Coolify application for accepted Hub {hub_id} was not found; refusing to create one")
    if found.get("placement_mismatch") or found.get("migration_candidate"):
        raise RedeployError(f"existing Hub {hub_id} has unexpected Coolify placement; refusing to redeploy")
    target["application_uuid"] = uuid
    # Also check the frozen UUID against the actual application's identity.
    verified = _inspect_frozen_application(_client(target), target, uuid)
    if not verified.get("present"):
        raise RedeployError(f"Coolify application {uuid} disappeared during Hub preflight")
    image_dockerfile = str(verified.get("dockerfile_location") or "").strip().lstrip("/")
    if image_dockerfile and image_dockerfile != "Dockerfile.hub.stable":
        raise RedeployError(
            f"Hub {hub_id} is still configured with {image_dockerfile}; redeploy cannot convert "
            "a Coolify application to Stable Hub. Use the existing remove-hub/add-hub "
            "lifecycle to replace the application, then redeploy the Stable Hub."
        )
    # Never count an experimental image as a successfully deployed Stable Hub.
    # Runtime verification uses Stable Hub's actual identity/health endpoints.
    accepted, _ = accepted_targets(ctx, network)
    target["private_https_routes"] = private_https_routes(
        ctx_private, network, accepted["hubs"], str(target["chain_contract"]["rpc_url"]),
    )
    target["hub_runtime"] = "stable"
    return target



def deployment_history(target: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read the existing application's complete Coolify build history.

    Fail closed on an inaccessible, truncated, or malformed history: absence of
    evidence must not be mistaken for evidence that a POST was not accepted.
    """
    client = _client(target)
    app_uuid = urllib.parse.quote(str(target["application_uuid"]), safe="")
    records: list[dict[str, Any]] = []
    take = 50
    for skip in range(0, 1000, take):
        response = client.request(
            "GET", f"/api/v1/deployments/applications/{app_uuid}?skip={skip}&take={take}"
        )
        if not response.ok:
            raise RedeployError(
                f"cannot reconcile {target['hub_id']} build: Coolify deployment history "
                f"HTTP {response.status}: {response.body}; no additional build requested"
            )
        body = response.body
        if isinstance(body, list):
            page = body
        elif isinstance(body, Mapping) and isinstance(body.get("deployments"), list):
            # Coolify's application-deployment endpoint returns a JSON object
            # with a `deployments` collection, not the array advertised by
            # its generated OpenAPI response schema.
            page = body["deployments"]
        elif isinstance(body, Mapping) and isinstance(body.get("data"), list):
            page = body["data"]
        else:
            raise RedeployError(
                f"cannot reconcile {target['hub_id']} build: invalid Coolify deployment history; "
                "no additional build requested"
            )
        if len(page) > take or any(not isinstance(row, Mapping) for row in page):
            raise RedeployError("invalid or truncated Coolify deployment history; no additional build requested")
        for row in page:
            deployment_uuid = str(row.get("deployment_uuid") or "").strip()
            if not deployment_uuid:
                raise RedeployError("Coolify deployment history omits UUID; no additional build requested")
            records.append(dict(row))
        if len(page) < take:
            return records
    raise RedeployError(
        "Coolify deployment history exceeds the reconciliation limit; "
        "no additional build requested"
    )


def reconcile_build_submission(
    target: Mapping[str, Any], *, baseline_uuids: list[str] | None,
    started_after: str, revision: str,
) -> str | None:
    """Return the one build accepted after our trigger, or None if none exists.

    New runs use an exact pre-submit history snapshot. Legacy interrupted runs
    (including the earlier HTTP 405) use the persisted harness run start time.
    No arbitrary build is adopted just because it uses the same Git revision.
    """
    history = deployment_history(target)
    if baseline_uuids is not None:
        if not isinstance(baseline_uuids, list) or any(not isinstance(x, str) for x in baseline_uuids):
            raise RedeployError("invalid saved deployment baseline; no additional build requested")
        baseline = set(baseline_uuids)
        visible = {str(row['deployment_uuid']) for row in history}
        if not baseline.issubset(visible):
            raise RedeployError("Coolify no longer exposes the pre-submit history; no additional build requested")
        newer = [row for row in history if str(row['deployment_uuid']) not in baseline]
    else:
        try:
            stamp = datetime.fromisoformat(started_after.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("naive timestamp")
        except (ValueError, AttributeError) as exc:
            raise RedeployError("legacy rollout has no reliable start timestamp; no additional build requested") from exc
        # A conservative overlap avoids discarding builds just before a request
        # due to server clock resolution or small host/controller clock skew.
        from datetime import timedelta
        cutoff = stamp.astimezone(timezone.utc) - timedelta(minutes=5)
        newer = []
        for row in history:
            try:
                created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    raise ValueError("naive timestamp")
            except (KeyError, ValueError, TypeError) as exc:
                raise RedeployError("Coolify history has no reliable timestamps; no additional build requested") from exc
            if created.astimezone(timezone.utc) >= cutoff:
                newer.append(row)
    if not newer:
        return None
    if len(newer) != 1:
        raise RedeployError(
            f"{target['hub_id']} has {len(newer)} deployments after the attempted build; "
            "cannot safely identify one build; no additional build requested"
        )
    row = newer[0]
    if row.get("force_rebuild") is not True or row.get("is_api") is not True or row.get("restart_only") is True:
        raise RedeployError(
            f"new Coolify deployment {row['deployment_uuid']} cannot be attributed to "
            "a forced API build; no additional build requested"
        )
    actual_commit = str(row.get("commit") or "").strip().lower()
    if actual_commit and actual_commit != revision.lower():
        raise RedeployError(
            f"new Coolify deployment {row['deployment_uuid']} uses revision {actual_commit}, "
            f"not {revision}; no additional build requested"
        )
    return str(row["deployment_uuid"])

def force_rebuild(target: Mapping[str, Any]) -> str:
    """Force the build endpoint only; never fall back to start/restart."""
    client = _client(target)
    uuid = str(target["application_uuid"])
    query = urllib.parse.urlencode({"uuid": uuid, "force": "true"})
    response = client.request("POST", f"/api/v1/deploy?{query}")
    if not response.ok:
        message = f"forced Hub build failed for {target['hub_id']}: HTTP {response.status}: {response.body}"
        if 400 <= response.status < 500:
            raise BuildSubmissionRejected(message)
        raise RedeployError(message)
    deployment_uuid = _deployment_uuid_from_trigger({"body": response.body})
    if not deployment_uuid:
        raise RedeployError(
            f"Coolify accepted forced build for {target['hub_id']} but did not return a deployment UUID; "
            "inspect the Coolify deployment history before retrying to avoid a duplicate build"
        )
    return deployment_uuid


@contextmanager
def _live_progress():
    """Enable the existing Coolify/Hub poller's periodic progress for CLI runs."""
    old = os.environ.get("MAIN_COMPUTER_HUB_PROGRESS")
    os.environ["MAIN_COMPUTER_HUB_PROGRESS"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("MAIN_COMPUTER_HUB_PROGRESS", None)
        else:
            os.environ["MAIN_COMPUTER_HUB_PROGRESS"] = old


def failed_previous_build(target: Mapping[str, Any], deployment_uuid: str) -> bool:
    """Only a confirmed terminal failure can be retired for the next run."""
    client = _client(target)
    uuid = urllib.parse.quote(str(deployment_uuid), safe="")
    response = client.request("GET", f"/api/v1/deployments/{uuid}")
    if not response.ok or not isinstance(response.body, Mapping):
        raise RedeployError(
            f"cannot inspect prior Coolify build {deployment_uuid} for {target['hub_id']}; "
            "no additional build requested"
        )
    status = _deployment_state(response.body)
    if status in {"failed", "failure", "error", "cancelled", "canceled"}:
        return True
    if status in {"queued", "pending", "in_progress", "in-progress", "running", "building",
                  "finished", "success", "succeeded", "completed", "complete"}:
        return False
    raise RedeployError(
        f"unknown status {status!r} for prior Coolify build {deployment_uuid}; "
        "no additional build requested"
    )


def synchronize_bridge_signer(ctx: HubContext, target: Mapping[str, Any]) -> None:
    """Restore only the runtime signer env from verified Mother authority.

    A code rebuild must not strand a Hub with a stale persistent smoke signer.
    This updates the single existing signer environment variable, not application
    settings, membership, wallets, contracts or storage. No secrets enter receipts.
    """
    if not target.get("bridge_signer_required"):
        return
    import yaml
    from tools.mother.common.models import OperationIdentity
    from tools.mother.common.private_state import read_private_state
    from .common.admin_identity import _paths
    from .common.privates import network_doc
    from .common.deployment import legacy

    network = str(target["network"])
    hub_id = str(target["hub_id"])
    op_name = f"hub-redeploy-signer-{network}-{hub_id}"
    identity = OperationIdentity(
        operation_id=op_name, request_id=op_name, network=network,
        operation_kind="MOTHER-OP-UPGRADE-HUB",
    )
    try:
        verified = read_private_state(_paths(ctx), operation=identity)
        private = yaml.safe_load(verified.document_bytes)
        net = network_doc(private, network)
        assignment = net["hubs"][hub_id]["hub_admin"]
        address = str(assignment["address"]).strip()
        secret = str(assignment["private_key"]).strip()
    except Exception as exc:
        raise RedeployError(
            f"verified Mother admin signer unavailable for {hub_id}: {type(exc).__name__}; "
            "no build requested"
        ) from exc
    frozen = str(target.get("hub_admin_address") or "").strip()
    if not frozen or frozen.lower() != address.lower():
        raise RedeployError(f"{hub_id} accepted admin address disagrees with Mother; no build requested")
    build_target = dict(target)
    build_target["_local_repo_root"] = str(ctx.repo_root)
    build_target["_hub_admin_wallet"] = {"address": address, "private_key": secret}
    build_target["hub_admin_private_state_path"] = f"networks.{network}.hubs.{hub_id}.hub_admin"
    signer = _build_bridge_signer_for_deployment(build_target)
    if signer is None:
        raise RedeployError(f"required bridge signer unavailable for {hub_id}; no build requested")
    # The signer must be authorized on the live escrow before touching Coolify.
    # The same idempotent owner-signed boundary is already used by add-hub.
    print(f"Hub {hub_id}: check bridge controller authorization", flush=True)
    authorization = ensure_bridge_controller(
        ctx, network=network, target=build_target, admin_address=address,
    )
    if authorization.get("verified") is not True:
        raise RedeployError(f"bridge controller authorization not verified for {hub_id}; no build requested")
    if authorization.get("already_authorized"):
        print(f"Hub {hub_id}: bridge controller already authorized", flush=True)
    else:
        print(f"Hub {hub_id}: deployer authorization confirmed on-chain", flush=True)
    _verify_live_bridge_signer_contract(build_target, signer)
    try:
        client = _client(target)
        uuid = str(target["application_uuid"])
        routes = target.get("private_https_routes")
        if not isinstance(routes, dict) or (network == "mainnet" and not routes):
            raise RedeployError(f"Mother-derived private HTTPS routes missing for {hub_id}; no build requested")
        legacy.sync_application_env_var(
            client, application_uuid=uuid,
            key="MAIN_COMPUTER_HUB_PRIVATE_HTTPS_ROUTES",
            value=json.dumps(routes, sort_keys=True), tried=[],
        )
        legacy.sync_application_env_var(
            client, application_uuid=uuid,
            key="MAIN_COMPUTER_HUB_REQUIRE_PRIVATE_HTTPS_ROUTES",
            value="true" if routes else "false", tried=[],
        )
        legacy.sync_application_env_var(
            client, application_uuid=uuid,
            key=BRIDGE_SIGNER_ENV, value=str(signer["bundle_b64"]), tried=[],
        )
    except Exception as exc:
        raise RedeployError(
            f"Coolify signer sync failed for {hub_id}: {type(exc).__name__}; no build requested"
        ) from exc


def wait_for_build(target: Mapping[str, Any], deployment_uuid: str) -> dict[str, Any]:
    with _live_progress():
        result = _wait_for_coolify_deployment(_client(target), deployment_uuid)
    if not result.get("waited") or not result.get("commit"):
        raise RedeployError(
            f"Coolify did not confirm a completed build and revision for {target['hub_id']}"
        )
    return result


def verify_runtime(target: Mapping[str, Any]) -> None:
    with _live_progress():
        result = observe_hub(target, wait_timeout_s=300.0)
    if result.get("verified") is not True:
        raise RedeployError(f"Hub {target['hub_id']} runtime did not verify: {result.get('reason')}")
