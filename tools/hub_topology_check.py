#!/usr/bin/env python3
"""Check Mother-private-state Hub membership against verified Coolify reality.

Mother networks.<network>.hubs is the sole desired membership authority.
A Hub's private wallet record or reserve record carries its association. An absent
Coolify application must NEVER cause the wallet to be released.

PASS: state active Hubs and the accepted projection match verified live Hubs.
DRIFT: complete inventory proves state/live membership or accepted-projection drift.
UNKNOWN: remote inventory, private-state identity, or live Hub is unverified.

This command never writes state, redeploys, or frees a wallet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

# Work both as ``python tools/hub_topology_check.py`` and as an import in seal.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.hub_networks import load_hub_network_registry
from tools import coolify_hub_service as coolify
from tools.hub_control.common.canonical import canonical_bytes
from tools.hub_control.common.chain_contract import load_current_chain_contract
from tools.hub_control.common.deployment import observe_hub
from tools.hub_control.common.fdb_contract import load_current_fdb_contract
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.placement import resolve_public_url
from tools.hub_control.common.state import read_accepted
from tools.hub_control.common.privates import controller_coordinates, load_private, network_doc
from tools.mother.common.hub_admin_pool import inspect_pool
from tools.mother.common.ethereum_identity import is_address

SCHEMA = "main-computer.hub-topology-check.v2"
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _normalized_items(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        raise ValueError("Coolify applications response is not JSON object/list")
    # Missing a recognized list is not proof that the controller has zero apps.
    for key in ("applications", "data", "items"):
        if isinstance(body.get(key), list):
            return [item for item in body[key] if isinstance(item, dict)]
    raise ValueError("Coolify applications response has no complete application list")


def _pagination_unknown(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    links = body.get("links")
    meta = body.get("meta")
    if body.get("next_page_url") or (isinstance(links, dict) and links.get("next")):
        return True
    if isinstance(meta, dict):
        try:
            current = int(meta.get("current_page") or 1)
            last = int(meta.get("last_page") or current)
            if last > current:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _application_environment(raw: Mapping[str, Any]) -> str:
    name = str(raw.get("environment_name") or "").strip()
    environment = raw.get("environment")
    if not name and isinstance(environment, Mapping):
        name = str(environment.get("name") or environment.get("environment_name") or "").strip()
    return name


def _application_project(raw: Mapping[str, Any]) -> str:
    direct = str(raw.get("project_uuid") or "").strip()
    if direct:
        return direct
    project = raw.get("project")
    if isinstance(project, Mapping):
        return str(project.get("uuid") or "").strip()
    environment = raw.get("environment")
    if isinstance(environment, Mapping):
        project = environment.get("project")
        if isinstance(project, Mapping):
            return str(project.get("uuid") or "").strip()
    return ""


def _hub_candidates(network: str, state_ids: set[str], applications: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    result = []
    pattern = re.compile(r"^main-computer-(" + re.escape(network) + r"[a-z]-hub[1-9][0-9]*)$")
    legacy = f"main-computer-{network}-hub"
    for app in applications:
        name = str(app.get("name") or "").strip()
        match = pattern.fullmatch(name)
        hub_id = match.group(1) if match else ""
        if not hub_id and name == legacy:
            hub_id = "<legacy-ambiguous>"
        if not hub_id and name in {f"main-computer-{value}" for value in state_ids}:
            hub_id = name.removeprefix("main-computer-")
        if hub_id:
            result.append((hub_id, name, app))
    return result


def observe_topology(
    ctx: HubContext,
    network: str,
    *,
    client_factory=coolify.CoolifyClient,
    hub_observer=observe_hub,
) -> dict[str, Any]:
    """Perform fresh, read-only, fail-closed observation of the live Hub membership."""
    unknown: list[dict[str, str]] = []
    observed: list[dict[str, Any]] = []
    inventories: list[dict[str, Any]] = []
    try:
        private = load_private(ctx)
        config = network_doc(private, network)
        # A missing hubs mapping means no Hub membership is yet configured in
        # Mother, not a reason to trust the obsolete Hub accepted.json file.
        raw_hubs = config.get("hubs", {})
        if not isinstance(raw_hubs, Mapping):
            raise ValueError("Mother networks.<network>.hubs must be a mapping")
        inspected = inspect_pool(config, enforce_birth_size=False)
        reserve = ((config.get("wallets") or {}).get("hub_admin_reserve") or {})
        associations = {}
        for label, wallet in reserve.items():
            if wallet.get("associated_hub"):
                hub_key = wallet["associated_hub"]
                if hub_key in associations:
                    raise ValueError(f"duplicate reserved wallet associations for {hub_key}")
                associations[hub_key] = wallet
        locked = []
        for hub_key, entry in raw_hubs.items():
            wallet = entry.get("hub_admin") if isinstance(entry, Mapping) else None
            if isinstance(wallet, Mapping):
                locked.append({"hub_id": hub_key, "address": wallet["address"]})
        reserved_associations = sorted(
            ({"hub_id": hub_key, "address": wallet["address"]} for hub_key, wallet in associations.items()),
            key=lambda x: x["hub_id"],
        )
        hub_records: dict[str, Mapping[str, Any]] = {}
        for hub_id, entry in raw_hubs.items():
            if not isinstance(hub_id, str) or not ID_RE.fullmatch(hub_id) or not isinstance(entry, Mapping):
                raise ValueError("invalid Mother Hub identity record")
            if entry.get("status") not in {"active", "inactive"}:
                raise ValueError(f"Mother Hub {hub_id} requires status active or inactive")
            wallet = entry.get("hub_admin")
            if wallet is not None:
                address = str(wallet.get("address") or "") if isinstance(wallet, Mapping) else ""
                if not is_address(address) or wallet.get("associated_hub") != hub_id:
                    raise ValueError(f"Mother Hub {hub_id} has an invalid wallet or association")
            else:
                known = associations.get(hub_id)
                recorded = str(entry.get("hub_admin_address") or "")
                if recorded and (not known or recorded.lower() != str(known.get("address") or "").lower()):
                    raise ValueError(f"Mother Hub {hub_id} records a conflicting administrator address")
                if entry.get("status") == "active" and known is None:
                    raise ValueError(f"active Mother Hub {hub_id} has no wallet or historical reserve association")
            hub_records[hub_id] = entry
        state_active = sorted(hub_id for hub_id, value in hub_records.items() if value["status"] == "active")
        state_inactive = sorted(hub_id for hub_id, value in hub_records.items() if value["status"] == "inactive")
        location_drift = sorted(hub_id for hub_id in state_active if not hub_records[hub_id].get("hub_admin"))
        state_sha256 = _digest({"hubs": raw_hubs, "hub_admin_reserve": reserve})
        coolify_config = config.get("coolify") or {}
        controllers = coolify_config.get("controllers") if isinstance(coolify_config, Mapping) else None
        if not isinstance(controllers, Mapping) or not controllers:
            raise ValueError("no Coolify controllers configured in Mother private state")
        fdb = load_current_fdb_contract(ctx, network)
        chain = load_current_chain_contract(ctx, network)
        profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    except Exception as exc:
        return {"schema": SCHEMA, "network": network, "status": "UNKNOWN",
                "errors": [_error("HUB_MOTHER_STATE_UNVERIFIED", f"{type(exc).__name__}: {exc}")],
                "state_hubs_active": [], "state_hubs_inactive": [], "observed_hubs": []}

    # Query ALL configured controllers, not merely controllers mentioned by
    # accepted state: otherwise undiscovered live Hubs would be invisible.
    for controller_id in sorted(controllers):
        try:
            coords = controller_coordinates(private, network, controller_id, base_dir=ctx.mother_private_path.parent)
            client = client_factory(coords["url"], coords["api_token"])
            response = client.request("GET", "/api/v1/applications")
            if not response.ok:
                raise RuntimeError(f"Coolify applications GET returned HTTP {response.status}")
            if _pagination_unknown(response.body):
                raise RuntimeError("Coolify application inventory is paginated/incomplete")
            apps = _normalized_items(response.body)
            names_uuids = sorted((str(item.get("name") or ""), coolify.item_uuid(item)) for item in apps)
            inventories.append({"controller_id": controller_id, "inventory_sha256": _digest(names_uuids),
                                "application_count": len(apps)})
        except Exception as exc:
            unknown.append(_error("HUB_COOLIFY_INVENTORY_UNAVAILABLE", f"{controller_id}: {type(exc).__name__}: {exc}"))
            continue
        for hub_id, app_name, item in _hub_candidates(network, set(hub_records) | set(associations), apps):
            uuid = coolify.item_uuid(item)
            if not uuid or hub_id == "<legacy-ambiguous>":
                unknown.append(_error("HUB_COOLIFY_AMBIGUOUS_IDENTITY", f"{controller_id}: {app_name}: cannot uniquely associate with Hub ID"))
                continue
            try:
                detail_response = client.request("GET", "/api/v1/applications/" + urllib.parse.quote(uuid, safe=""))
                if not detail_response.ok or not isinstance(detail_response.body, dict):
                    raise ValueError("application detail not verified")
            except Exception as exc:
                unknown.append(_error("HUB_COOLIFY_APP_DETAIL_UNAVAILABLE", f"{controller_id}: {app_name}: {type(exc).__name__}: {exc}"))
                continue
            detail = detail_response.body
            actual_name = str(detail.get("name") or "").strip()
            environment_name = _application_environment(detail) or _application_environment(item)
            project_uuid = _application_project(detail) or _application_project(item)
            if actual_name != app_name or (environment_name and environment_name != f"{network}-hubs") or (project_uuid and project_uuid != coords["project_uuid"]):
                unknown.append(_error("HUB_COOLIFY_APP_PLACEMENT_MISMATCH", f"{controller_id}: application {uuid} identity/environment/project mismatch"))
                continue
            if not environment_name:
                unknown.append(_error("HUB_COOLIFY_ENVIRONMENT_UNVERIFIED", f"{controller_id}: application {uuid} does not disclose its environment"))
                continue
            # Determine direct identity URL, never use shared network ingress.
            state_entry = hub_records.get(hub_id, {})
            try:
                url = str(state_entry.get("public_url") or "") or resolve_public_url(
                    ctx, private, network=network, hub_id=hub_id,
                    accepted_hubs=[{"hub_id": key, **dict(rec)} for key, rec in hub_records.items()])
                if not url or url.rstrip("/") == str(profile.hub_url).rstrip("/"):
                    raise ValueError("Hub identity URL missing or aliases network ingress")
                rt_dir = f"/data/main-computer/hub/{hub_id}"
                active_wallet = hub_records.get(hub_id, {}).get("hub_admin") or associations.get(hub_id) or {}
                if not active_wallet:
                    raise ValueError("live Hub has no matching wallet or historical association")
                admin = str(active_wallet.get("address") or "")
                target = {"network": network, "hub_id": hub_id, "public_url": url,
                          "runtime_dir": rt_dir, "cluster_file_path": f"{rt_dir}/fdb.cluster",
                          "fdb_contract": fdb.payload, "chain_contract": chain.payload,
                          "bridge_signer_required": profile.kind == "mainnet",
                          "hub_admin_address": admin}
                result = hub_observer(target, wait_timeout_s=0.0)
                if result.get("verified") is not True:
                    reason = str(result.get("reason") or "live endpoints failed")
                    raise ValueError(reason)
            except Exception as exc:
                unknown.append(_error("HUB_LIVE_IDENTITY_UNVERIFIED", f"{controller_id}: {hub_id}: {type(exc).__name__}: {exc}"))
                continue
            observed.append({"hub_id": hub_id, "controller_id": controller_id, "host_id": controller_id,
                             "public_url": url.rstrip("/"), "application_uuid": uuid,
                             "environment_name": environment_name, "runtime_verified": True})

    if len(inventories) != len(controllers):
        unknown.append(_error("HUB_COOLIFY_INVENTORY_INCOMPLETE", "not every configured controller returned a complete application inventory"))
    observed_ids = [value["hub_id"] for value in observed]
    if len(observed_ids) != len(set(observed_ids)):
        unknown.append(_error("HUB_DUPLICATE_LIVE_IDENTITY", "multiple Coolify applications claim the same Hub identity"))
    by_id = {value["hub_id"]: value for value in observed}
    for hub_id, hub in hub_records.items():
        actual = by_id.get(hub_id)
        if actual and hub.get("status") == "active" and (
            (hub.get("controller_id") and hub["controller_id"] != actual["controller_id"]) or
            (hub.get("host_id") and hub["host_id"] != actual["host_id"]) or
            (hub.get("public_url") and str(hub["public_url"]).rstrip("/") != actual["public_url"])):
            unknown.append(_error("HUB_PLACEMENT_IDENTITY_MISMATCH", f"{hub_id}: live placement disagrees with Mother state"))
    # All inventories were read and all discovered live identities verified.
    # Do not infer an absent Hub merely from a failed HTTP health check.
    missing = sorted(set(state_active) - set(observed_ids))
    extra = sorted(set(observed_ids) - set(state_active))
    # accepted.json is a derived, secret-free receipt consumed by Hub Control.
    # Compare it ONLY after all live inventories/Hub identities are verified;
    # otherwise absence is not proven and resealing must not be recommended.
    projection = {"status": "unverified", "generation": None, "hub_ids": [],
                  "stale_hubs": [], "unprojected_hubs": []}
    if not unknown:
        try:
            accepted = read_accepted(ctx, network)
            if accepted is None:
                if observed_ids:
                    # The existing seal cannot create a missing first-birth
                    # accepted receipt. Do not offer a command that won't fix it.
                    projection["status"] = "missing"
                    unknown.append(_error("HUB_ACCEPTED_PROJECTION_MISSING",
                                          "live Hubs exist but there is no accepted Hub receipt"))
                else:
                    projection["status"] = "unborn"
            else:
                projection["generation"] = accepted["generation"]
                raw = accepted["hubs"]
                ids = [entry.get("hub_id") if isinstance(entry, Mapping) else None
                       for entry in raw]
                if (any(not isinstance(hub_id, str) or not ID_RE.fullmatch(hub_id)
                        for hub_id in ids) or len(set(ids)) != len(ids)):
                    raise ValueError("accepted Hub receipt contains malformed or duplicate Hub IDs")
                projection["hub_ids"] = sorted(ids)
                projection["stale_hubs"] = sorted(set(ids) - set(observed_ids))
                projection["unprojected_hubs"] = sorted(set(observed_ids) - set(ids))
                projection["status"] = ("stale" if projection["stale_hubs"] or
                                        projection["unprojected_hubs"] else "current")
        except Exception as exc:
            unknown.append(_error("HUB_ACCEPTED_PROJECTION_UNVERIFIED",
                                  f"{type(exc).__name__}: {exc}"))
    status = "UNKNOWN" if unknown else ("DRIFT" if missing or extra or location_drift or
                                        projection["status"] == "stale" else "PASS")
    report = {"schema": SCHEMA, "network": network, "status": status,
            "mother_state_sha256": state_sha256,
            "state_hubs_active": state_active, "state_hubs_inactive": state_inactive,
            "locked_hub_admins": sorted(locked, key=lambda x: x["hub_id"]),
            "reserved_associations": reserved_associations,
            "admin_location_drift": location_drift,
            "observed_hubs": sorted(observed, key=lambda x: x["hub_id"]),
            "missing_hubs": missing, "extra_hubs": extra, "inventories": inventories,
            "accepted_projection": projection, "errors": unknown}
    if status == "DRIFT":
        report["seal_command"] = (
            f"python .\\tools\\hub_topology_seal.py --network {network} --apply-state"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        report = observe_topology(HubContext.from_repo(args.repo_root), args.network)
    except Exception as exc:
        report = {"schema": SCHEMA, "network": args.network, "status": "UNKNOWN",
                  "errors": [_error("HUB_TOPOLOGY_CHECK_FAILED", f"{type(exc).__name__}: {exc}")]}
    print(json.dumps(report, indent=2, sort_keys=True))
    return {"PASS": 0, "DRIFT": 1, "UNKNOWN": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
