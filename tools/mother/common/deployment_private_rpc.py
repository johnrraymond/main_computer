"""Offline compiler for one private, non-validator Mother mainnet RPC service.

This module performs no network access and authorizes no live mutation.  It
consumes canonical successful mainnet soak evidence, reopens the exact completed
steady-state release lineage, derives the committed genesis and validator enodes,
and writes a transaction describing one future Coolify service creation plus its
future deploy request.

The compiled Compose has no host ports, URLs, FQDNs, Traefik labels, validator
vote operations, or validator credentials.  A separately supplied secret
environment variable is required at execution time; only its public node id and
derived account address are committed here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_mainnet_soak import (
    _EVIDENCE_DIRECTORY as _SOAK_EVIDENCE_DIRECTORY,
    _load_baseline as _load_continuation_baseline,
    verify_mainnet_steady_state_soak_evidence,
)
from .deployment_post_admission_steady_state import (
    _binding,
    _canonical_under,
    _contains_sensitive,
    _ensure_root,
    _mapping,
    _parse_utc,
    _relative,
    _resolve,
    _timestamp,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_private_rpc_transaction.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-private-rpc-transactions")
_A = "mainneta-super1"
_C = "mainnetc-super1"
_ALLOWED_CONTROLLERS = {"coolify-a", "coolify-c"}
_SECRET_ENV = "MC_MOTHER_RPC_NODE_PRIVATE_KEY"
_SERVICE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NODE_ID_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{128}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ENV_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ENODE_RE = re.compile(r"enode://([0-9a-fA-F]{128})@([A-Za-z0-9._:-]+):([0-9]{1,5})")


class MotherDeploymentPrivateRpcError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentPrivateRpcError:
    return MotherDeploymentPrivateRpcError(code, message)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(
        canonical_json({key: value for key, value in document.items() if key != field})
    ).hexdigest()


def _service_name(value: Any) -> str:
    if type(value) is not str or _SERVICE_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_INVALID",
            "service_name must be a lowercase DNS-safe label",
        )
    if value in {_A, _C} or not value.startswith("mainnet-rpc"):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_INVALID",
            "service_name must begin with mainnet-rpc and must not reuse a validator name",
        )
    return value


def _node_id(value: Any) -> str:
    if type(value) is not str or _NODE_ID_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_INVALID",
            "rpc_node_id must be a 128-hex secp256k1 public node id",
        )
    return value.lower().removeprefix("0x")


def _address(value: Any) -> str:
    if type(value) is not str or _ADDRESS_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_INVALID",
            "rpc_node_address must be a 20-byte Ethereum address",
        )
    return value.lower()


def _environment_name(value: Any) -> str:
    if type(value) is not str or _ENV_RE.fullmatch(value) is None:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_INVALID",
            "environment_name is invalid",
        )
    return value


def _controller_config(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
) -> dict[str, Any]:
    if controller_id not in _ALLOWED_CONTROLLERS:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_CONTROLLER_REJECTED",
            "private RPC placement is limited to coolify-a or coolify-c",
        )
    try:
        document = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except Exception as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_PRIVATE_STATE_INVALID",
            "Mother private state is not canonical JSON",
        ) from exc
    networks = _mapping(document.get("networks"), "private_state.networks")
    body = _mapping(networks.get(network), f"private_state.networks.{network}")
    coolify = _mapping(body.get("coolify"), "private_state.coolify")
    if coolify.get("mutation_authority") != "observe-only":
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_PRIVATE_STATE_INVALID",
            "Coolify private state must remain observe-only",
        )
    controllers = _mapping(coolify.get("controllers"), "private_state.controllers")
    wire = _mapping(controllers.get(controller_id), f"controller {controller_id}")
    project_uuid = wire.get("project_uuid")
    server_uuid = wire.get("server_uuid")
    enabled = wire.get("enabled", True)
    if not (
        type(project_uuid) is str
        and bool(project_uuid)
        and type(server_uuid) is str
        and bool(server_uuid)
        and enabled is True
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_PRIVATE_STATE_INVALID",
            f"{controller_id} lacks an enabled project/server binding",
        )
    result = {
        "controller_id": controller_id,
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
    }
    observed = wire.get("observed_environments")
    if isinstance(observed, Mapping):
        result["observed_environments"] = dict(observed)
    return result


def _load_soak(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    network: str,
    selected_nodes: Iterable[str],
    max_age_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    verified = verify_mainnet_steady_state_soak_evidence(
        paths,
        private_state,
        Path(evidence_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        baseline_max_age_seconds=max_age_seconds,
    )
    if not (
        verified.get("clean") is True
        and verified.get("network") == network
        and verified.get("blocks_advancing") is True
        and verified.get("latest_block_fresh") is True
        and verified.get("live_mutation_performed") is False
        and verified.get("validator_vote_performed") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_SOAK_INVALID",
            "source soak evidence is not a clean completed mainnet proof",
        )
    canonical_path = Path(evidence_path).resolve(strict=False)
    soak, _, file_sha = _canonical_under(
        paths,
        canonical_path,
        _SOAK_EVIDENCE_DIRECTORY,
        "mainnet soak evidence",
    )
    baseline_ref = _mapping(soak.get("baseline"), "soak.baseline")
    continuation_path = _resolve(
        paths,
        baseline_ref.get("locator"),
        ("evidence", "deployment-post-admission-steady-state-continuation"),
        "steady-state continuation evidence",
    )
    _, release, _, _ = _load_continuation_baseline(
        paths,
        private_state,
        continuation_path,
        selected_nodes=selected_nodes,
        baseline_max_age_seconds=max_age_seconds,
    )
    return soak, release, canonical_path, file_sha


def _extract_b64_to(compose: str, destination: str) -> bytes:
    pattern = re.compile(
        r"printf '%s' '([A-Za-z0-9+/=]+)' \| base64 -d > "
        + re.escape(destination)
    )
    match = pattern.search(compose)
    if match is None:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_LINEAGE_INVALID",
            f"source Compose does not contain canonical {destination} material",
        )
    try:
        return base64.b64decode(match.group(1), validate=True)
    except Exception as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_LINEAGE_INVALID",
            f"source {destination} material is invalid",
        ) from exc


def _source_material(
    release: Mapping[str, Any],
) -> tuple[bytes, tuple[str, str], tuple[str, str]]:
    targets = _mapping(release.get("targets"), "continuation release targets")
    a = _mapping(targets.get(_A), "A target")
    c = _mapping(targets.get(_C), "C target")
    recovered = _mapping(a.get("recovered_compose"), "A recovered Compose")
    a_steady = _mapping(a.get("steady_state_compose"), "A steady Compose")
    c_steady = _mapping(c.get("steady_state_compose"), "C steady Compose")
    recovered_text = recovered.get("canonical_text")
    if type(recovered_text) is not str:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_LINEAGE_INVALID",
            "A recovered Compose text is missing",
        )
    genesis = _extract_b64_to(recovered_text, "/config/genesis.json")
    static_nodes = _extract_b64_to(recovered_text, "/config/static-nodes.json")
    try:
        static_values = json.loads(static_nodes.decode("utf-8"))
    except Exception as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_LINEAGE_INVALID",
            "source static-nodes.json is invalid",
        ) from exc
    if not isinstance(static_values, list):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_LINEAGE_INVALID",
            "source static-nodes.json is not a list",
        )
    enodes: dict[str, str] = {}
    for value in static_values:
        if type(value) is str:
            match = _ENODE_RE.fullmatch(value)
            if match is not None:
                enodes[match.group(1).lower()] = value
    for text in (
        recovered_text,
        str(a_steady.get("canonical_text") or ""),
        str(c_steady.get("canonical_text") or ""),
    ):
        for match in _ENODE_RE.finditer(text):
            node_id = match.group(1).lower()
            enodes[node_id] = match.group(0)
    if len(enodes) != 2:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_LINEAGE_INVALID",
            "exactly two validator enodes were not recovered from the released lineage",
        )
    ordered = tuple(enodes[key] for key in sorted(enodes))
    node_ids = tuple(sorted(enodes))
    return genesis, ordered, node_ids


def _compose(
    *,
    service_name: str,
    chain_id: int,
    genesis: bytes,
    genesis_sha256: str,
    validator_set: list[str],
    validator_enodes: tuple[str, str],
    validator_node_ids: tuple[str, str],
    rpc_node_id: str,
    rpc_node_address: str,
) -> str:
    genesis_b64 = base64.b64encode(genesis).decode("ascii")
    static_b64 = base64.b64encode(
        json.dumps(list(validator_enodes), separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    validators_json = json.dumps(validator_set, separators=(",", ":"))
    peer_ids_json = json.dumps(list(validator_node_ids), separators=(",", ":"))
    guardian = "mother-private-rpc-guardian"
    init = "mother-private-rpc-init"
    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {init}:",
        "    image: alpine:3.20",
        '    restart: "no"',
        "    environment:",
        f'      {_SECRET_ENV}: "${{{_SECRET_ENV}}}"',
        "    volumes:",
        "      - mother-rpc-config:/config",
        "    command:",
        "      - sh",
        "      - -ec",
        "      - |",
        "        umask 077",
        f"        printf '%s' '{genesis_b64}' | base64 -d > /config/genesis.json",
        f'        key="$${{{_SECRET_ENV}#0x}}"',
        '        test "$${#key}" -eq 64',
        '        printf \'%s\' "$${key}" > /config/nodekey',
        f"        printf '%s' '{static_b64}' | base64 -d > /config/static-nodes.json",
        "        chmod 0400 /config/nodekey",
        "        chmod 0444 /config/genesis.json /config/static-nodes.json",
        "",
        f"  {service_name}:",
        "    image: hyperledger/besu:latest",
        "    restart: unless-stopped",
        "    depends_on:",
        f"      {init}:",
        "        condition: service_completed_successfully",
        "    command:",
        "      - --data-path=/var/lib/besu",
        "      - --genesis-file=/config/genesis.json",
        "      - --node-private-key-file=/config/nodekey",
        f"      - --network-id={chain_id}",
        "      - --sync-mode=FULL",
        "      - --data-storage-format=BONSAI",
        "      - --p2p-enabled=true",
        "      - --p2p-port=30303",
        "      - --discovery-enabled=true",
        "      - --static-nodes-file=/config/static-nodes.json",
        "      - --rpc-http-enabled=true",
        "      - --rpc-http-host=0.0.0.0",
        "      - --rpc-http-port=8545",
        "      - --rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN",
        f"      - --host-allowlist=localhost,127.0.0.1,{service_name}",
        "      - --min-gas-price=0",
        "    volumes:",
        "      - mother-rpc-config:/config:ro",
        "      - mother-rpc-data:/var/lib/besu",
        "    labels:",
        "      main_computer.mother.stage: private-non-validator-rpc",
        f"      main_computer.mother.node: {service_name}",
        "      main_computer.mother.validator: \"false\"",
        "",
        f"  {guardian}:",
        "    image: python:3.12-alpine",
        "    restart: unless-stopped",
        "    read_only: true",
        "    depends_on:",
        f"      {service_name}:",
        "        condition: service_started",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        "        import hashlib,json,os,time,traceback,urllib.request",
        f"        RPC='http://{service_name}:8545'",
        f"        EXPECTED_CHAIN_ID={chain_id}",
        f"        EXPECTED_GENESIS_SHA256='{genesis_sha256}'",
        f"        EXPECTED_VALIDATORS={validators_json}",
        f"        EXPECTED_NODE_ID='{rpc_node_id}'",
        f"        EXPECTED_NODE_ADDRESS='{rpc_node_address}'",
        f"        EXPECTED_PEER_IDS={peer_ids_json}",
        "        PROOF='/proof/private-rpc-ready.json'",
        "        HEALTHY='/proof/private-rpc-ready'",
        "        def rpc(method,params):",
        "            body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params},separators=(',',':')).encode()",
        "            req=urllib.request.Request(RPC,data=body,headers={'Content-Type':'application/json','Host':'localhost'},method='POST')",
        "            with urllib.request.urlopen(req,timeout=5) as response: value=json.loads(response.read(1048576).decode())",
        "            if value.get('error') is not None or 'result' not in value: raise RuntimeError(method+' failed')",
        "            return value['result']",
        "        def norm(value):",
        "            text=str(value or '').lower()",
        "            return text[2:] if text.startswith('0x') else text",
        "        def prove():",
        "            with open('/config/genesis.json','rb') as handle: digest=hashlib.sha256(handle.read()).hexdigest()",
        "            if digest != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "            if int(rpc('eth_chainId',[]),16) != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "            info=rpc('admin_nodeInfo',[])",
        "            if not isinstance(info,dict) or norm(info.get('id')) != EXPECTED_NODE_ID: raise RuntimeError('rpc node identity mismatch')",
        "            peers=rpc('admin_peers',[])",
        "            packed=json.dumps(peers,sort_keys=True).lower()",
        "            if not isinstance(peers,list) or any(item not in packed for item in EXPECTED_PEER_IDS): raise RuntimeError('validator peer missing')",
        "            if int(rpc('net_peerCount',[]),16) < 2: raise RuntimeError('peer count below two')",
        "            if rpc('eth_syncing',[]) is not False: raise RuntimeError('rpc node is syncing')",
        "            current=sorted(set(str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber',['latest'])))",
        "            if current != EXPECTED_VALIDATORS or EXPECTED_NODE_ADDRESS in current: raise RuntimeError('validator set mismatch')",
        "            first=int(rpc('eth_blockNumber',[]),16)",
        "            deadline=time.time()+120",
        "            second=first",
        "            while time.time()<deadline and second<=first:",
        "                time.sleep(2)",
        "                second=int(rpc('eth_blockNumber',[]),16)",
        "            if second<=first: raise RuntimeError('block height did not advance')",
        "            latest=rpc('eth_getBlockByNumber',['latest',False])",
        "            block_time=int(latest.get('timestamp','0x0'),16) if isinstance(latest,dict) else 0",
        "            now=int(time.time())",
        "            if block_time>now+15 or now-block_time>60: raise RuntimeError('latest block is stale')",
        "            proof={'chain_id':EXPECTED_CHAIN_ID,'genesis_sha256':digest,'validator_set':current,'rpc_node_id':EXPECTED_NODE_ID,'rpc_node_address':EXPECTED_NODE_ADDRESS,'validator_peer_ids':EXPECTED_PEER_IDS,'peer_count':len(peers),'syncing':False,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}",
        "            temporary=PROOF+'.tmp'",
        "            with open(temporary,'w',encoding='utf-8') as handle: json.dump(proof,handle,sort_keys=True,separators=(',',':'))",
        "            os.replace(temporary,PROOF)",
        "            with open(HEALTHY,'w',encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "        while True:",
        "            try: prove()",
        "            except Exception:",
        "                traceback.print_exc()",
        "                try: os.unlink(HEALTHY)",
        "                except FileNotFoundError: pass",
        "            time.sleep(6)",
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        "        - import os,time; p='/proof/private-rpc-ready'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 36",
        "      start_period: 30s",
        "    volumes:",
        "      - mother-rpc-config:/config:ro",
        "      - mother-rpc-proof:/proof",
        "",
        "volumes:",
        "  mother-rpc-config:",
        "  mother-rpc-data:",
        "  mother-rpc-proof:",
        "",
    ]
    return "\n".join(lines)


def _semantic_sha256(compose: str) -> str:
    try:
        value = yaml.safe_load(compose)
    except yaml.YAMLError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_COMPOSE_INVALID",
            "compiled Compose is invalid YAML",
        ) from exc
    return hashlib.sha256(canonical_json(value)).hexdigest()


def build_private_rpc_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    soak_evidence_path: Path,
    *,
    controller_id: str,
    rpc_node_id: str,
    rpc_node_address: str,
    service_name: str = "mainnet-rpc1",
    environment_name: str = "mainnet",
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    soak_max_age_seconds: int = 86400,
    created_at: str | None = None,
) -> dict[str, Any]:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_NETWORK_REJECTED",
            "private RPC compiler currently accepts mainnet only",
        )
    name = _service_name(service_name)
    node_id = _node_id(rpc_node_id)
    node_address = _address(rpc_node_address)
    environment = _environment_name(environment_name)
    placement = _controller_config(
        private_state,
        network=network,
        controller_id=controller_id,
    )
    soak, release, soak_path, soak_file_sha = _load_soak(
        paths,
        private_state,
        Path(soak_evidence_path),
        network=network,
        selected_nodes=selected_nodes,
        max_age_seconds=soak_max_age_seconds,
    )
    validator_set = sorted(_address(item) for item in soak["validator_set"])
    if len(validator_set) != 2 or node_address in validator_set:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_REJECTED",
            "RPC node address must be distinct from the exact two-validator set",
        )
    genesis, validator_enodes, validator_node_ids = _source_material(release)
    if node_id in validator_node_ids:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_REJECTED",
            "RPC node id must be distinct from both validator node ids",
        )
    genesis_sha = hashlib.sha256(genesis).hexdigest()
    if (
        soak.get("chain_id") != 42424240
        or soak.get("genesis_sha256") != genesis_sha
        or release.get("chain", {}).get("genesis_sha256") != genesis_sha
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_LINEAGE_INVALID",
            "genesis or chain commitment does not match the verified soak lineage",
        )
    compose = _compose(
        service_name=name,
        chain_id=42424240,
        genesis=genesis,
        genesis_sha256=genesis_sha,
        validator_set=validator_set,
        validator_enodes=validator_enodes,
        validator_node_ids=validator_node_ids,
        rpc_node_id=node_id,
        rpc_node_address=node_address,
    )
    compose_sha = hashlib.sha256(compose.encode("utf-8")).hexdigest()
    semantic_sha = _semantic_sha256(compose)
    body = {
        "server_uuid": placement["server_uuid"],
        "project_uuid": placement["project_uuid"],
        "environment_name": environment,
        "name": name,
        "description": (
            "Main Computer private non-validator mainnet RPC follower; "
            "no public endpoint and no validator authority"
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "connect_to_docker_network": True,
        "instant_deploy": False,
    }
    create_id = f"{name}.create-private-rpc-service"
    deploy_id = f"{name}.deploy-private-rpc-service"
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": _timestamp(created_at),
        "network": network,
        "mother_binding": _binding(private_state),
        "staged_scope": "offline-private-non-validator-rpc-compiler",
        "soak_evidence": {
            "locator": _relative(paths, soak_path, "mainnet soak evidence"),
            "file_sha256": soak_file_sha,
        },
        "chain": {
            "chain_id": 42424240,
            "genesis_sha256": genesis_sha,
            "validator_set": validator_set,
            "blocks_advancing": True,
            "latest_block_fresh": True,
        },
        "placement": {
            **placement,
            "environment_name": environment,
            "service_name": name,
            "connect_to_docker_network": True,
            "public_endpoint": None,
            "host_rpc_port": None,
            "host_p2p_port": None,
            "private_rpc_url_after_deployment": f"http://{name}:8545",
        },
        "identity": {
            "expected_node_id": node_id,
            "expected_node_address": node_address,
            "validator_identity": False,
            "private_key_environment_variable": _SECRET_ENV,
            "private_key_material_in_transaction": False,
        },
        "validator_peers": {
            "enodes": list(validator_enodes),
            "node_ids": list(validator_node_ids),
            "minimum_peer_count": 2,
        },
        "compose": {
            "canonical_text": compose,
            "sha256": compose_sha,
            "semantic_sha256": semantic_sha,
            "services": [
                "mother-private-rpc-init",
                name,
                "mother-private-rpc-guardian",
            ],
            "public_rpc_exposed": False,
            "host_ports_published": False,
            "validator_vote_capability_used": False,
        },
        "required_secret_bindings": [
            {
                "name": _SECRET_ENV,
                "purpose": "non-validator Besu node key",
                "expected_public_node_id": node_id,
                "expected_node_address": node_address,
                "value_in_transaction": False,
            }
        ],
        "execution_plan": {
            "mutations": [
                {
                    "ordinal": 1,
                    "mutation_id": create_id,
                    "controller_id": controller_id,
                    "method": "POST",
                    "endpoint": "/api/v1/services",
                    "canonical_request_body": body,
                    "body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
                    "success_statuses": [200, 201, 202],
                    "bind_result": "service_uuid",
                    "deployment_started": False,
                },
                {
                    "ordinal": 2,
                    "mutation_id": deploy_id,
                    "controller_id": controller_id,
                    "method": "GET",
                    "endpoint_template": (
                        f"/api/v1/deploy?uuid=${{result.{create_id}.service_uuid}}&force=false"
                    ),
                    "canonical_request_body": None,
                    "body_sha256": None,
                    "success_statuses": [200, 201, 202],
                    "depends_on": [create_id],
                },
            ],
            "preconditions": [
                "service name is absent on the selected controller",
                f"{_SECRET_ENV} is bound outside this transaction",
                "A and C remain exact steady-state validators",
                "no public URL, FQDN, host port, or Traefik route is configured",
            ],
        },
        "authority": {
            "offline_compilation_only": True,
            "network_access_authorized": False,
            "live_execution_authorized": False,
            "release_authorized": False,
            "validator_vote_authorized": False,
            "validator_identity_authorized": False,
            "validator_mutation_authorized": False,
            "public_endpoint_authorized": False,
            "ssh_authorized": False,
            "requested_use_limit": 0,
        },
        "summary": {
            "clean": True,
            "mutation_count": 2,
            "validator_mutation_count": 0,
            "public_endpoint_count": 0,
            "host_port_count": 0,
            "non_validator_rpc_compiled": True,
            "live_mutation_performed": False,
            "next_phase": "verify-private-rpc-transaction",
        },
    }
    if _contains_sensitive(transaction):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_SENSITIVE",
            "compiled transaction contains a forbidden sensitive field",
        )
    transaction["private_rpc_transaction_sha256"] = _digest_without(
        transaction,
        "private_rpc_transaction_sha256",
    )
    return transaction


def write_private_rpc_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(document, "private_rpc_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("private_rpc_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "private RPC transaction is malformed or sensitive",
        )
    root = _ensure_root(paths, _TRANSACTION_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "transaction"
    destination = root / f"{stamp}-{digest[:16]}.json"
    payload = canonical_json(document)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _error(
                "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_CONFLICT",
                "transaction destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_WRITE_FAILED",
            "transaction reread mismatch",
        )
    return destination, digest


def _validate_compose(document: Mapping[str, Any]) -> None:
    compose = _mapping(document.get("compose"), "transaction.compose")
    text = compose.get("canonical_text")
    if type(text) is not str:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "Compose text is missing",
        )
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != compose.get("sha256"):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "Compose byte commitment mismatch",
        )
    if _semantic_sha256(text) != compose.get("semantic_sha256"):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "Compose semantic commitment mismatch",
        )
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, Mapping):
        raise _error("MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID", "Compose is not an object")
    services = parsed.get("services")
    expected_name = document["placement"]["service_name"]
    expected_services = {
        "mother-private-rpc-init",
        expected_name,
        "mother-private-rpc-guardian",
    }
    if not isinstance(services, Mapping) or set(services) != expected_services:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "Compose service set is not exact",
        )
    lowered = text.lower()
    forbidden = (
        "\n    ports:",
        "\n    expose:",
        "traefik.",
        "qbft_proposevalidatorvote",
        "--rpc-http-cors-origins=*",
        "--host-allowlist=*",
    )
    if any(item in lowered for item in forbidden):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_PUBLIC_EXPOSURE",
            "Compose contains public exposure or validator-vote material",
        )
    for service in services.values():
        if isinstance(service, Mapping) and ("ports" in service or "expose" in service):
            raise _error(
                "MOTHER_DEPLOY_PRIVATE_RPC_PUBLIC_EXPOSURE",
                "Compose publishes a host port",
            )


def verify_private_rpc_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    soak_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = (paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1]).resolve(
        strict=False
    )
    candidate = Path(transaction_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_PATH_UNSAFE",
            "transaction is outside the canonical private RPC transaction directory",
        ) from exc
    document, _, file_sha = _canonical_under(
        paths,
        candidate,
        _TRANSACTION_DIRECTORY,
        "private RPC transaction",
    )
    digest = _digest_without(document, "private_rpc_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("schema_version") != 1
        or document.get("private_rpc_transaction_sha256") != digest
        or _contains_sensitive(document)
        or document.get("mother_binding") != _binding(private_state)
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "transaction is modified, stale, or sensitive",
        )
    created = _parse_utc(document.get("created_at"), "transaction.created_at")
    current = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    age = int((current - created).total_seconds())
    if age < -15 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_EXPIRED",
            "transaction age is outside the accepted window",
        )
    soak_ref = _mapping(document.get("soak_evidence"), "transaction.soak_evidence")
    soak_path = _resolve(
        paths,
        soak_ref.get("locator"),
        _SOAK_EVIDENCE_DIRECTORY,
        "mainnet soak evidence",
    )
    soak, release, _, soak_file_sha = _load_soak(
        paths,
        private_state,
        soak_path,
        network=document.get("network"),
        selected_nodes=selected_nodes,
        max_age_seconds=soak_max_age_seconds,
    )
    if soak_ref.get("file_sha256") != soak_file_sha:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "soak evidence file binding mismatch",
        )
    chain = _mapping(document.get("chain"), "transaction.chain")
    expected_validator_set = sorted(_address(item) for item in soak["validator_set"])
    if not (
        chain.get("chain_id") == soak.get("chain_id") == 42424240
        and chain.get("genesis_sha256") == soak.get("genesis_sha256")
        and chain.get("validator_set") == expected_validator_set
        and chain.get("blocks_advancing") is True
        and chain.get("latest_block_fresh") is True
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "chain binding does not match the verified soak",
        )
    placement = _mapping(document.get("placement"), "transaction.placement")
    controller = _controller_config(
        private_state,
        network=document["network"],
        controller_id=placement.get("controller_id"),
    )
    if not (
        placement.get("project_uuid") == controller["project_uuid"]
        and placement.get("server_uuid") == controller["server_uuid"]
        and placement.get("connect_to_docker_network") is True
        and placement.get("public_endpoint") is None
        and placement.get("host_rpc_port") is None
        and placement.get("host_p2p_port") is None
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "placement is not private or does not match Mother state",
        )
    identity = _mapping(document.get("identity"), "transaction.identity")
    node_id = _node_id(identity.get("expected_node_id"))
    node_address = _address(identity.get("expected_node_address"))
    if (
        identity.get("validator_identity") is not False
        or node_address in expected_validator_set
        or identity.get("private_key_environment_variable") != _SECRET_ENV
        or identity.get("private_key_material_in_transaction") is not False
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_REJECTED",
            "identity is not an isolated non-validator identity",
        )
    genesis, enodes, peer_ids = _source_material(release)
    peers = _mapping(document.get("validator_peers"), "transaction.validator_peers")
    if (
        peers.get("enodes") != list(enodes)
        or peers.get("node_ids") != list(peer_ids)
        or peers.get("minimum_peer_count") != 2
        or node_id in peer_ids
        or hashlib.sha256(genesis).hexdigest() != chain["genesis_sha256"]
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "validator peer or genesis lineage mismatch",
        )
    _validate_compose(document)
    plan = _mapping(document.get("execution_plan"), "transaction.execution_plan")
    mutations = plan.get("mutations")
    authority = _mapping(document.get("authority"), "transaction.authority")
    if not (
        isinstance(mutations, list)
        and len(mutations) == 2
        and [item.get("method") for item in mutations] == ["POST", "GET"]
        and all(item.get("controller_id") == placement["controller_id"] for item in mutations)
        and authority
        == {
            "offline_compilation_only": True,
            "network_access_authorized": False,
            "live_execution_authorized": False,
            "release_authorized": False,
            "validator_vote_authorized": False,
            "validator_identity_authorized": False,
            "validator_mutation_authorized": False,
            "public_endpoint_authorized": False,
            "ssh_authorized": False,
            "requested_use_limit": 0,
        }
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "mutation scope or authority is not exact",
        )
    create_id = f"{placement['service_name']}.create-private-rpc-service"
    deploy_id = f"{placement['service_name']}.deploy-private-rpc-service"
    body = _mapping(mutations[0].get("canonical_request_body"), "create request body")
    try:
        body_compose = base64.b64decode(
            str(body.get("docker_compose_raw") or ""),
            validate=True,
        ).decode("utf-8")
    except Exception as exc:
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "create-service Compose body is invalid",
        ) from exc
    expected_deploy_endpoint = (
        f"/api/v1/deploy?uuid=${{result.{create_id}.service_uuid}}&force=false"
    )
    if not (
        [item.get("ordinal") for item in mutations] == [1, 2]
        and [item.get("mutation_id") for item in mutations] == [create_id, deploy_id]
        and mutations[0].get("endpoint") == "/api/v1/services"
        and mutations[1].get("endpoint_template") == expected_deploy_endpoint
        and mutations[1].get("depends_on") == [create_id]
        and mutations[1].get("canonical_request_body") is None
        and mutations[1].get("body_sha256") is None
        and body.get("server_uuid") == placement["server_uuid"]
        and body.get("project_uuid") == placement["project_uuid"]
        and body.get("environment_name") == placement["environment_name"]
        and body.get("name") == placement["service_name"]
        and body.get("connect_to_docker_network") is True
        and body.get("instant_deploy") is False
        and body_compose == document["compose"]["canonical_text"]
        and "urls" not in body
        and "fqdn" not in body
        and "domains" not in body
        and hashlib.sha256(canonical_json(body)).hexdigest()
        == mutations[0].get("body_sha256")
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "create/deploy plan is not exact, private, and canonical",
        )
    secret_bindings = document.get("required_secret_bindings")
    if not (
        type(secret_bindings) is list
        and len(secret_bindings) == 1
        and secret_bindings[0]
        == {
            "name": _SECRET_ENV,
            "purpose": "non-validator Besu node key",
            "expected_public_node_id": node_id,
            "expected_node_address": node_address,
            "value_in_transaction": False,
        }
    ):
        raise _error(
            "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID",
            "required secret binding is not exact",
        )
    return {
        "clean": True,
        "network": document["network"],
        "transaction_path": str(candidate),
        "transaction_sha256": digest,
        "transaction_file_sha256": file_sha,
        "age_seconds": age,
        "controller_id": placement["controller_id"],
        "service_name": placement["service_name"],
        "private_rpc_url_after_deployment": placement[
            "private_rpc_url_after_deployment"
        ],
        "rpc_node_id": node_id,
        "rpc_node_address": node_address,
        "chain_id": chain["chain_id"],
        "genesis_sha256": chain["genesis_sha256"],
        "validator_set": expected_validator_set,
        "validator_peer_count": 2,
        "mutation_count": 2,
        "validator_mutation_count": 0,
        "public_endpoint_count": 0,
        "host_port_count": 0,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "next_phase": "private-rpc-release-not-yet-authorized",
    }
