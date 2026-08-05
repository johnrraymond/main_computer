"""Internal-only first-super-node birth proof for Mother.

This boundary replaces manual SSH/RPC and Hub tunnelling.  An expiring release
binds a successful A-side genesis execution to one exact Compose update that
installs a non-routable proof guardian.  The guardian validates the genesis
file digest, chain id, advancing block height, sole QBFT validator, Hub health,
and the Hub's exact co-located RPC configuration entirely inside the Compose
network. Mother then proves the exact Compose commitment and the Coolify
service's authenticated ``running:healthy`` state.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_rollback import (
    MotherDeploymentGenesisRollbackError,
    verify_genesis_rollback_cycle_evidence,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_genesis_birth_release.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_genesis_birth_evidence.v1"
_CLAIM_KIND = "main_computer.mother.deployment_genesis_birth_execution_claim.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-genesis-birth-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-genesis-birth-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-genesis-birth")
_GENESIS_EXECUTION_DIRECTORY = ("actions", "deployment-genesis-executions")
_GENESIS_RELEASE_DIRECTORY = ("actions", "deployment-genesis-releases")
_GENESIS_TRANSACTION_DIRECTORY = ("actions", "deployment-genesis-transactions")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900
_PROOF_IMAGE = "python:3.12-alpine"
_HUB_SERVICE = "mother-super-node-hub"
_HUB_PORT = 8790


class MotherDeploymentGenesisBirthError(RuntimeError):
    """The internal genesis-birth proof failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be a non-empty string"
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} is not a safe identifier"
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be a lowercase SHA-256 digest"
        )
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be a UTC timestamp"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _contains_sensitive(value: Any) -> bool:
    forbidden = {
        "access_token", "api_token", "credential", "mnemonic", "password",
        "private_key", "refresh_token", "secret", "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, str], operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _resolve(paths: PrivateStatePaths, locator: Any, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{label} locator must be a relative POSIX path"
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    result = (paths.root / candidate).resolve(strict=False)
    try:
        result.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} locator escapes Mother state"
        ) from exc
    return result


def _load(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{label} is not readable canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: tuple[str, str], label: str):
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(_root(paths, directory).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} is outside its canonical root"
        ) from exc
    return _load(candidate, label)


class _StrictComposeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: _StrictComposeLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
                "Compose contains an unhashable mapping key",
            ) from exc
        if duplicate:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
                f"Compose contains duplicate mapping key {key!r}",
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictComposeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _compose_document(value: str, label: str) -> Mapping[str, Any]:
    try:
        document = yaml.load(value, Loader=_StrictComposeLoader)
    except MotherDeploymentGenesisBirthError:
        raise
    except yaml.YAMLError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            f"{label} is not valid safe YAML",
        ) from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("services"), Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            f"{label} is not a Docker Compose mapping with services",
        )
    return document


def _compose_semantic_sha256(value: str, label: str) -> str:
    document = _compose_document(value, label)
    try:
        return hashlib.sha256(canonical_json(dict(document))).hexdigest()
    except (TypeError, ValueError) as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            f"{label} cannot be represented canonically",
        ) from exc


def _compose_strings(payload: Any) -> list[str]:
    """Return Compose candidates from supported Coolify response wrappers.

    Coolify's public schema places the fields at the top level.  Some deployed
    versions and clients wrap the service record, so support only a small,
    explicit set of non-recursive wrappers rather than scanning arbitrary
    response text.
    """

    records: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        records.append(payload)
        for key in ("data", "resource", "service"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                records.append(nested)
    values: list[str] = []
    for record in records:
        for key in ("docker_compose_raw", "docker_compose"):
            value = record.get(key)
            if type(value) is not str or not value:
                continue
            values.append(value)
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            values.append(decoded)
    return values


def _match_service_compose(payload: Any, expected: str, label: str) -> dict[str, str]:
    expected_bytes = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    expected_normalized = hashlib.sha256(
        expected.replace("\r\n", "\n").rstrip().encode("utf-8")
    ).hexdigest()
    expected_semantic = _compose_semantic_sha256(expected, f"expected {label}")
    candidates = _compose_strings(payload)
    if not candidates:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNAVAILABLE",
            "Coolify service detail did not expose docker_compose_raw or docker_compose; "
            "the API token may require read:sensitive permission",
        )
    invalid = 0
    for candidate in candidates:
        if hashlib.sha256(candidate.encode("utf-8")).hexdigest() == expected_bytes:
            return {"mode": "exact-bytes", "semantic_sha256": expected_semantic}
        normalized = candidate.replace("\r\n", "\n").rstrip()
        if hashlib.sha256(normalized.encode("utf-8")).hexdigest() == expected_normalized:
            return {"mode": "normalized-text", "semantic_sha256": expected_semantic}
        try:
            candidate_semantic = _compose_semantic_sha256(candidate, f"live {label}")
        except MotherDeploymentGenesisBirthError:
            invalid += 1
            continue
        if candidate_semantic == expected_semantic:
            return {"mode": "canonical-compose-semantics", "semantic_sha256": expected_semantic}
    if invalid == len(candidates):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            "Coolify exposed Compose fields, but none contained valid safe Compose YAML",
        )
    raise MotherDeploymentGenesisBirthError(
        "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_MISMATCH",
        f"live {label} is not semantically equivalent to the released Compose",
    )


def _proof_script(*, node: str, chain_id: int, genesis_sha256: str, validator_address: str) -> str:
    expected_validator = validator_address.lower()
    return "\n".join([
        "import hashlib, json, os, time, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"HUB = 'http://{_HUB_SERVICE}:{_HUB_PORT}'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"EXPECTED_VALIDATOR = '{expected_validator}'",
        "PROOF = '/proof/proof.json'",
        "HEALTHY = '/proof/healthy'",
        "def read_json(request):",
        "    with urllib.request.urlopen(request, timeout=5) as response:",
        "        return json.loads(response.read(1048576).decode())",
        "def rpc(method, params):",
        "    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}, separators=(',', ':')).encode()",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    value = read_json(req)",
        "    if value.get('error') is not None or 'result' not in value:",
        "        raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def hub(path):",
        "    return read_json(urllib.request.Request(HUB + path, headers={'Accept':'application/json'}, method='GET'))",
        "def prove():",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        genesis_digest = hashlib.sha256(handle.read()).hexdigest()",
        "    if genesis_digest != EXPECTED_GENESIS_SHA256:",
        "        raise RuntimeError('genesis commitment mismatch')",
        "    chain_id = int(rpc('eth_chainId', []), 16)",
        "    if chain_id != EXPECTED_CHAIN_ID:",
        "        raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'):",
        "        raise RuntimeError('genesis block missing')",
        "    validators = [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "    if validators != [EXPECTED_VALIDATOR]:",
        "        raise RuntimeError('validator set mismatch')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first:",
        "        raise RuntimeError('block height did not advance')",
        "    health = hub('/api/hub/v1/health')",
        "    if health.get('ok') is not True or health.get('service') != 'main-computer-hub' or health.get('network_key') != 'mainnet':",
        "        raise RuntimeError('hub health mismatch')",
        "    status = hub('/api/hub/v1/status')",
        "    network = status.get('network') if isinstance(status, dict) else None",
        "    if not isinstance(network, dict):",
        "        raise RuntimeError('hub status network missing')",
        "    if network.get('chain_id') != EXPECTED_CHAIN_ID or network.get('chain_rpc_url') != RPC:",
        "        raise RuntimeError('hub local RPC binding mismatch')",
        "    proof = {'chain_id':chain_id,'genesis_block_present':True,'genesis_sha256':genesis_digest,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'validator_set':[EXPECTED_VALIDATOR],'hub_health':True,'hub_service':'main-computer-hub','hub_network_key':'mainnet','hub_chain_rpc_url':RPC,'hub_chain_id':EXPECTED_CHAIN_ID,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    temporary = PROOF + '.tmp'",
        "    with open(temporary, 'w', encoding='utf-8') as handle:",
        "        json.dump(proof, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(temporary, PROOF)",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle:",
        "        handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception:",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "    time.sleep(6)",
        "",
    ])


def _internal_proof_compose(
    original: str, *, node: str, chain_id: int, genesis_sha256: str, validator_address: str
) -> str:
    host_mapping = '      - "127.0.0.1:8545:8545/tcp"\n'
    allowlist = f"      - --host-allowlist=localhost,127.0.0.1,{node},{_HUB_SERVICE},mother-genesis-proof-guardian\n"
    marker = "\nvolumes:\n"
    if original.count(host_mapping) != 1 or original.count(allowlist) != 1 or original.count(marker) != 1:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNSUPPORTED",
            "released first-genesis Compose does not match the supported secure template",
        )
    updated = original.replace(host_mapping, "", 1)
    script = _proof_script(
        node=node,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        validator_address=validator_address,
    )
    indented_script = "\n".join("        " + line for line in script.splitlines())
    guardian = "\n".join([
        "  mother-genesis-proof-guardian:",
        f"    image: {_PROOF_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    depends_on:",
        f"      {node}:",
        "        condition: service_started",
        f"      {_HUB_SERVICE}:",
        "        condition: service_healthy",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        indented_script,
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        "        - import os,time; p='/proof/healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 12",
        "      start_period: 20s",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-proof:/proof",
        "",
    ])
    updated = updated.replace(marker, "\n" + guardian + marker, 1)
    updated = updated.replace("  mother-data:\n", "  mother-data:\n  mother-proof:\n", 1)
    forbidden = ("ports:", "expose:", "traefik.", "domains:", "fqdn:")
    guardian_section = updated.split("  mother-genesis-proof-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    if any(item in guardian_section for item in forbidden):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_GUARDIAN_EXPOSED",
            "proof guardian must not expose a port, URL, or proxy route",
        )
    if '127.0.0.1:8545:8545' in updated:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RPC_EXPOSED", "proof Compose must remove the host RPC mapping"
        )
    return updated


def _chain(paths: PrivateStatePaths, private_state: PrivateStateReadResult, execution_path: Path):
    execution, execution_raw, execution_sha = _canonical_under(
        paths, execution_path, _GENESIS_EXECUTION_DIRECTORY, "genesis execution"
    )
    if execution.get("kind") != "main_computer.mother.deployment_genesis_execution_result.v1" or execution.get("status") != "pass":
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution is not a successful canonical result"
        )
    if execution.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_STALE_BINDING", "genesis execution does not bind current Mother state"
        )
    summary = execution.get("summary")
    if not isinstance(summary, Mapping) or not all([
        summary.get("complete") is True,
        summary.get("compose_update_succeeded") is True,
        summary.get("deployment_requested") is True,
        summary.get("soft_replica_untouched") is True,
        summary.get("initial_chain_proven") is False,
        summary.get("rollback_available") is True,
        summary.get("genesis_birth_blocked_pending_genesis_rollback_cycle") is True,
        summary.get("next_phase") == "prove-genesis-rollback-cycle-before-birth",
    ]):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution does not authorize birth proof"
        )
    nodes = execution.get("nodes")
    if nodes != [execution.get("initial_node")]:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution must target only the initial node"
        )
    release_ref = execution.get("release")
    if not isinstance(release_ref, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution release binding is missing"
        )
    genesis_release_path = _resolve(paths, release_ref.get("locator"), "genesis release")
    genesis_release, _, _ = _canonical_under(
        paths, genesis_release_path, _GENESIS_RELEASE_DIRECTORY, "genesis release"
    )
    if _sha256(genesis_release.get("genesis_release_sha256"), "genesis release SHA-256") != _sha256(
        release_ref.get("sha256"), "genesis execution release SHA-256"
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis release digest does not match execution"
        )
    plan = genesis_release.get("execution_plan")
    if not isinstance(plan, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis release execution plan is missing"
        )
    transaction_ref = genesis_release.get("genesis_transaction")
    if not isinstance(transaction_ref, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis transaction binding is missing"
        )
    transaction_path = _resolve(paths, transaction_ref.get("locator"), "genesis transaction")
    transaction, _, _ = _canonical_under(
        paths, transaction_path, _GENESIS_TRANSACTION_DIRECTORY, "genesis transaction"
    )
    if _sha256(transaction.get("genesis_transaction_sha256"), "genesis transaction SHA-256") != _sha256(
        transaction_ref.get("sha256"), "genesis release transaction SHA-256"
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis transaction digest does not match release"
        )
    genesis = transaction.get("genesis")
    if not isinstance(genesis, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis transaction document is missing"
        )
    original_compose = plan.get("compose", {}).get("canonical_text")
    if type(original_compose) is not str or hashlib.sha256(original_compose.encode()).hexdigest() != execution.get("compose_sha256"):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "executed Compose commitment is missing or changed"
        )
    return {
        "execution": execution,
        "execution_path": execution_path.resolve(strict=False),
        "execution_sha256": execution_sha,
        "node": _identifier(execution.get("initial_node"), "initial node"),
        "controller_id": _identifier(execution.get("controller_id"), "controller_id"),
        "service_uuid": _identifier(execution.get("service_uuid"), "service_uuid"),
        "network": _identifier(execution.get("network"), "network"),
        "original_compose": original_compose,
        "original_compose_sha256": _sha256(execution.get("compose_sha256"), "Compose SHA-256"),
        "genesis_sha256": _sha256(execution.get("genesis_sha256"), "genesis SHA-256"),
        "chain_id": genesis.get("chain_id"),
        "validator_address": genesis.get("initial_validator_address"),
    }


def build_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_genesis_execution_sha256: str,
    genesis_rollback_verification_path: Path,
    selected_nodes: Iterable[str] = (),
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    chain = _chain(paths, private_state, Path(execution_path))
    acknowledged = _sha256(acknowledged_genesis_execution_sha256, "acknowledged genesis execution SHA-256")
    if acknowledged != chain["execution_sha256"]:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact genesis execution SHA-256",
        )
    try:
        rollback_cycle = verify_genesis_rollback_cycle_evidence(
            paths,
            private_state,
            Path(genesis_rollback_verification_path),
            network=chain["network"],
            node=chain["node"],
            service_uuid=chain["service_uuid"],
            genesis_sha256_value=chain["genesis_sha256"],
            before_execution_started_at=chain["execution"].get("started_at"),
            current_execution_sha256=chain["execution_sha256"],
        )
    except MotherDeploymentGenesisRollbackError as exc:
        raise MotherDeploymentGenesisBirthError(exc.code, str(exc)) from exc
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (chain["node"],):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_SELECTION_MISMATCH", "birth proof may target only the initial node"
        )
    if type(expires_in_seconds) is not int or not _MIN_RELEASE_SECONDS <= expires_in_seconds <= _MAX_RELEASE_SECONDS:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    if type(chain["chain_id"]) is not int or chain["chain_id"] <= 0:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis chain ID is invalid"
        )
    validator = str(chain["validator_address"] or "").lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", validator):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "initial validator address is invalid"
        )
    proof_compose = _internal_proof_compose(
        chain["original_compose"],
        node=chain["node"],
        chain_id=chain["chain_id"],
        genesis_sha256=chain["genesis_sha256"],
        validator_address=validator,
    )
    proof_bytes = proof_compose.encode("utf-8")
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    original_semantic_sha = _compose_semantic_sha256(
        chain["original_compose"], "released first-genesis Compose"
    )
    proof_semantic_sha = _compose_semantic_sha256(
        proof_compose, "released internal proof Compose"
    )
    body = {"name": chain["node"], "docker_compose_raw": base64.b64encode(proof_bytes).decode("ascii")}
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "release creation time is in the future"
        )
    expires_at = (created + timedelta(seconds=expires_in_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
    service_uuid = urllib.parse.quote(chain["service_uuid"], safe="")
    release = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires_at,
        "network": chain["network"],
        "mother_binding": _binding(private_state),
        "genesis_execution": {
            "locator": _relative(paths, chain["execution_path"], "genesis execution"),
            "sha256": chain["execution_sha256"],
        },
        "genesis_rollback_cycle": {
            "locator": _relative(
                paths,
                Path(rollback_cycle["verification_path"]),
                "genesis rollback verification",
            ),
            "sha256": rollback_cycle["verification_sha256"],
            "genesis_rollback_verification_sha256": rollback_cycle[
                "genesis_rollback_verification_sha256"
            ],
            "genesis_sha256": chain["genesis_sha256"],
            "rolled_back_execution_sha256": rollback_cycle[
                "rolled_back_execution_sha256"
            ],
            "reapplied_execution_sha256": chain["execution_sha256"],
            "verified_absent_at": rollback_cycle["observed_at"],
            "reapplied_after_verified_rollback": True,
            "persistent_volume_cleanup_performed": False,
        },
        "operator_release": {
            "intent": "install-internal-only-super-node-proof-guardian-and-prove-birth",
            "acknowledged_genesis_execution_sha256": acknowledged,
            "requested_use_limit": 1,
        },
        "proof_plan": {
            "initial_node": chain["node"],
            "controller_id": chain["controller_id"],
            "service_uuid": chain["service_uuid"],
            "chain_id": chain["chain_id"],
            "genesis_sha256": chain["genesis_sha256"],
            "validator_set": [validator],
            "hub": {
                "service": _HUB_SERVICE,
                "internal_port": _HUB_PORT,
                "health_url": f"http://{_HUB_SERVICE}:{_HUB_PORT}/api/hub/v1/health",
                "status_url": f"http://{_HUB_SERVICE}:{_HUB_PORT}/api/hub/v1/status",
                "local_rpc_url": f"http://{chain['node']}:8545",
                "public_endpoint_created": False,
            },
            "original_compose": {
                "sha256": chain["original_compose_sha256"],
                "semantic_sha256": original_semantic_sha,
                "canonical_text": chain["original_compose"],
            },
            "proof_compose": {
                "sha256": proof_sha,
                "semantic_sha256": proof_semantic_sha,
                "byte_length": len(proof_bytes),
                "canonical_text": proof_compose,
                "guardian_image": _PROOF_IMAGE,
                "guardian_public_ports": [],
                "guardian_domains": [],
                "host_rpc_mapping_present": False,
                "hub_service_present": True,
                "hub_public_endpoint_present": False,
            },
            "preconditions": [
                {"method": "GET", "endpoint": "/api/v1/services", "assertion": "exact A service exists"},
                {"method": "GET", "endpoint": f"/api/v1/services/{service_uuid}", "assertion": "live Compose matches executed first-genesis Compose"},
            ],
            "mutations": [
                {"ordinal": 1, "method": "PATCH", "endpoint": f"/api/v1/services/{service_uuid}", "canonical_request_body": body, "body_sha256": body_sha, "success_statuses": [200, 201, 202]},
                {"ordinal": 2, "method": "GET", "endpoint": f"/api/v1/deploy?uuid={service_uuid}&force=true", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202]},
            ],
            "proof": {
                "transport": "coolify-control-plane-only",
                "manual_ssh_required": False,
                "public_endpoint_created": False,
                "guardian_internal_only": True,
                "predicates": [
                    "genesis-file-sha256",
                    "chain-id",
                    "genesis-block-present",
                    "block-height-advancing",
                    "sole-qbft-validator",
                    "hub-health",
                    "hub-mainnet-binding",
                    "hub-local-rpc-binding",
                ],
                "success_signal": "exact service reports running:healthy under the exact proof Compose commitment",
            },
            "soft_replica_untouched": True,
        },
        "authority": {
            "transaction_apply_authorized": True,
            "live_execution_authorized": False,
            "authorization_source": "explicit-operator-release",
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_removed": True,
            "hub_internal_only": True,
            "hub_local_rpc_required": True,
            "soft_replica_untouched": True,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "genesis_rollback_cycle_proven": True,
            "genesis_reapplication_proven_after_rollback": True,
            "persistent_volume_cleanup_performed": False,
        },
        "release_sha256": None,
    }
    release["release_sha256"] = hashlib.sha256(canonical_json({k: v for k, v in release.items() if k != "release_sha256"})).hexdigest()
    if _contains_sensitive(release):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "birth release contains sensitive material"
        )
    return release


def write_genesis_birth_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity):
    document = dict(release)
    if document.get("kind") != _RELEASE_KIND or _contains_sensitive(document):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "birth release is malformed")
    payload = canonical_json(document)
    digest = _sha256(document.get("release_sha256"), "release SHA-256")
    expected = hashlib.sha256(canonical_json({k: v for k, v in document.items() if k != "release_sha256"})).hexdigest()
    if digest != expected:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "birth release digest is invalid")
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "birthrelease"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_CONFLICT", "birth release path contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    release, raw, byte_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "birth release")
    if release.get("kind") != _RELEASE_KIND or release.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "birth release kind or binding is invalid")
    digest = _sha256(release.get("release_sha256"), "release SHA-256")
    if digest != hashlib.sha256(canonical_json({k: v for k, v in release.items() if k != "release_sha256"})).hexdigest():
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "birth release digest does not match")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    if reference_now > expires or (reference_now - created).total_seconds() > max_age_seconds:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_EXPIRED", "birth release is expired")
    plan = release.get("proof_plan")
    if not isinstance(plan, Mapping):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof plan is missing")
    node = _identifier(plan.get("initial_node"), "initial node")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (node,):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_SELECTION_MISMATCH", "birth release targets only the initial node")
    proof = plan.get("proof")
    compose = plan.get("proof_compose")
    original = plan.get("original_compose")
    if not isinstance(proof, Mapping) or not isinstance(compose, Mapping) or not isinstance(original, Mapping) or not all([
        proof.get("manual_ssh_required") is False,
        proof.get("public_endpoint_created") is False,
        proof.get("guardian_internal_only") is True,
        compose.get("guardian_public_ports") == [],
        compose.get("guardian_domains") == [],
        compose.get("host_rpc_mapping_present") is False,
    ]):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof exposure policy is invalid")
    canonical_text = compose.get("canonical_text")
    if type(canonical_text) is not str or hashlib.sha256(canonical_text.encode()).hexdigest() != compose.get("sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof Compose commitment is invalid")
    if _compose_semantic_sha256(canonical_text, "released proof Compose") != compose.get("semantic_sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof Compose semantic commitment is invalid")
    original_text = original.get("canonical_text")
    if type(original_text) is not str or hashlib.sha256(original_text.encode()).hexdigest() != original.get("sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "original Compose commitment is invalid")
    if _compose_semantic_sha256(original_text, "released original Compose") != original.get("semantic_sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "original Compose semantic commitment is invalid")
    execution_ref = release.get("genesis_execution")
    if not isinstance(execution_ref, Mapping):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "genesis execution binding is missing")
    execution_path = _resolve(paths, execution_ref.get("locator"), "genesis execution")
    chain = _chain(paths, private_state, execution_path)
    if chain["execution_sha256"] != execution_ref.get("sha256") or chain["node"] != node:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "genesis execution binding changed")
    cycle_ref = release.get("genesis_rollback_cycle")
    if not isinstance(cycle_ref, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
            "genesis rollback-cycle binding is missing",
        )
    cycle_path = _resolve(
        paths,
        cycle_ref.get("locator"),
        "genesis rollback verification",
    )
    try:
        rollback_cycle = verify_genesis_rollback_cycle_evidence(
            paths,
            private_state,
            cycle_path,
            network=chain["network"],
            node=chain["node"],
            service_uuid=chain["service_uuid"],
            genesis_sha256_value=chain["genesis_sha256"],
            before_execution_started_at=chain["execution"].get("started_at"),
            current_execution_sha256=chain["execution_sha256"],
        )
    except MotherDeploymentGenesisRollbackError as exc:
        raise MotherDeploymentGenesisBirthError(exc.code, str(exc)) from exc
    if (
        rollback_cycle["verification_sha256"] != cycle_ref.get("sha256")
        or rollback_cycle["rolled_back_execution_sha256"]
        != cycle_ref.get("rolled_back_execution_sha256")
        or chain["execution_sha256"] != cycle_ref.get("reapplied_execution_sha256")
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
            "genesis rollback-cycle binding changed",
        )
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "genesis_birth_release_sha256": digest,
        "byte_sha256": byte_sha,
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "mother_binding": dict(release["mother_binding"]),
        "network": release["network"],
        "nodes": [node],
        "initial_node": node,
        "controller_id": plan["controller_id"],
        "service_uuid": plan["service_uuid"],
        "genesis_execution_sha256": chain["execution_sha256"],
        "genesis_sha256": plan["genesis_sha256"],
        "genesis_rollback_cycle_proven": True,
        "genesis_reapplication_proven_after_rollback": True,
        "genesis_rollback_verification_sha256": rollback_cycle[
            "genesis_rollback_verification_sha256"
        ],
        "rolled_back_genesis_execution_sha256": rollback_cycle[
            "rolled_back_execution_sha256"
        ],
        "persistent_volume_cleanup_performed": False,
        "proof_compose_sha256": compose["sha256"],
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "guardian_internal_only": True,
        "transaction_apply_authorized": True,
        "live_execution_authorized": False,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTOR_NOT_RUN"],
    }


def inspect_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    verified = verify_genesis_birth_release(
        paths, private_state, release_path, selected_nodes=selected_nodes, max_age_seconds=max_age_seconds
    )
    if _sha256(acknowledged_release_sha256, "acknowledged release SHA-256") != verified["genesis_birth_release_sha256"]:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "release acknowledgement does not match"
        )
    claim = _root(paths, _CLAIM_DIRECTORY) / f"{verified['genesis_birth_release_sha256']}.json"
    return {
        **verified,
        "executor_implemented": True,
        "execute_requested": False,
        "release_already_claimed": claim.exists(),
        "live_execution_authorized": True,
        "remaining_blocker_codes": [],
        "network_access_performed": False,
        "live_mutation_performed": False,
        "initial_chain_proven": False,
        "soft_replica_untouched": True,
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


def _http(controller: Any, method: str, endpoint: str, *, body: Mapping[str, Any] | None, timeout: float, max_response_bytes: int, opener: Any):
    data = canonical_json(dict(body)) if body is not None else None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {controller.api_token}", "User-Agent": "main-computer-mother-genesis-birth/1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, timeout)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return {"status": status, "ok": 200 <= status < 300, "payload": payload, "response_sha256": hashlib.sha256(raw).hexdigest(), "byte_length": len(raw), "elapsed_ms": int((time.monotonic() - started) * 1000)}


def _service_item(payload: Any, service_uuid: str, node: str) -> Mapping[str, Any]:
    items = payload if type(payload) is list else payload.get("services", []) if isinstance(payload, Mapping) else []
    matches = [item for item in items if isinstance(item, Mapping) and str(item.get("uuid") or item.get("id")) == service_uuid]
    if len(matches) != 1 or matches[0].get("name") != node:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_SERVICE_MISMATCH", "Coolify service binding does not match")
    return matches[0]


def _write_document(paths: PrivateStatePaths, parts: tuple[str, str], document: Mapping[str, Any], operation: OperationIdentity):
    value = dict(document)
    if _contains_sensitive(value):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "proof artifact contains sensitive material")
    payload = canonical_json(value)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, parts, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(value.get("completed_at", value.get("proved_at", ""))))[:32] or "birthproof"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_CONFLICT", "proof artifact path contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 180.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_genesis_birth_release(
        paths, private_state, release_path,
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_ALREADY_CONSUMED", "birth release already has a claim")
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "birth release")
    plan = release["proof_plan"]
    digest = inspected["genesis_birth_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), "birth release"), "sha256": digest},
        "node": inspected["initial_node"],
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_ALREADY_CONSUMED", "birth release already has a claim")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)
    controller = resolve_coolify_controller(private_state, inspected["network"], inspected["controller_id"])
    started = _timestamp()
    receipts: list[dict[str, Any]] = []
    preconditions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    try:
        inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        item = _service_item(inventory["payload"], inspected["service_uuid"], inspected["initial_node"])
        preconditions.append({"name": "initial-service-binding", "status": inventory["status"], "response_sha256": inventory["response_sha256"], "verified": inventory["ok"]})
        if not inventory["ok"]:
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_PRECONDITION_FAILED", "Coolify service inventory failed")
        detail_endpoint = f"/api/v1/services/{urllib.parse.quote(inspected['service_uuid'], safe='')}"
        detail = _http(controller, "GET", detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not detail["ok"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_PRECONDITION_FAILED",
                f"Coolify service detail GET failed with HTTP {detail['status']}",
            )
        original_binding = _match_service_compose(
            detail["payload"],
            plan["original_compose"]["canonical_text"],
            "executed first-genesis Compose",
        )
        if original_binding["semantic_sha256"] != plan["original_compose"]["semantic_sha256"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_PRECONDITION_FAILED",
                "released original Compose semantic commitment changed",
            )
        preconditions.append({
            "name": "executed-compose-binding",
            "status": detail["status"],
            "response_sha256": detail["response_sha256"],
            "verified": True,
            "binding_mode": original_binding["mode"],
            "semantic_sha256": original_binding["semantic_sha256"],
        })
        for mutation in plan["mutations"]:
            body = mutation.get("canonical_request_body")
            response = _http(controller, mutation["method"], mutation["endpoint"], body=dict(body) if isinstance(body, Mapping) else None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            ok = response["status"] in mutation["success_statuses"]
            receipts.append({"ordinal": mutation["ordinal"], "method": mutation["method"], "endpoint": mutation["endpoint"], "body_sha256": mutation["body_sha256"], "status": "succeeded" if ok else "failed", "live_write_acknowledged": ok, "response": {k: response[k] for k in ("status", "response_sha256", "byte_length", "elapsed_ms")}})
            if not ok:
                raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_MUTATION_FAILED", f"Coolify rejected proof mutation {mutation['ordinal']}")
        deadline = time.monotonic() + max_wait_seconds
        healthy = False
        last_status = ""
        while True:
            inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            if inventory["ok"]:
                service = _service_item(inventory["payload"], inspected["service_uuid"], inspected["initial_node"])
                last_status = str(service.get("status") or "")
                observations.append({"status": last_status, "response_sha256": inventory["response_sha256"], "observed_at": _timestamp()})
                if last_status == "running:healthy":
                    healthy = True
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not healthy:
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_NOT_HEALTHY", f"proof guardian did not reach running:healthy (last status {last_status!r})")
        detail = _http(controller, "GET", detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not detail["ok"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_POSTCONDITION_FAILED",
                f"Coolify proof service detail GET failed with HTTP {detail['status']}",
            )
        proof_binding = _match_service_compose(
            detail["payload"],
            plan["proof_compose"]["canonical_text"],
            "internal proof Compose",
        )
        if proof_binding["semantic_sha256"] != plan["proof_compose"]["semantic_sha256"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_POSTCONDITION_FAILED",
                "released proof Compose semantic commitment changed",
            )
        preconditions.append({
            "name": "proof-compose-binding",
            "status": detail["status"],
            "response_sha256": detail["response_sha256"],
            "verified": True,
            "binding_mode": proof_binding["mode"],
            "semantic_sha256": proof_binding["semantic_sha256"],
        })
    except MotherDeploymentGenesisBirthError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception:
        failure = {"code": "MOTHER_DEPLOY_GENESIS_BIRTH_UNEXPECTED_FAILURE", "message": "unexpected birth-proof failure"}
    completed = _timestamp()
    complete = failure is None and len(receipts) == 2 and all(item["status"] == "succeeded" for item in receipts)
    evidence = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": [inspected["initial_node"]],
        "initial_node": inspected["initial_node"],
        "controller_id": inspected["controller_id"],
        "service_uuid": inspected["service_uuid"],
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), "birth release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, "birth claim")},
        "genesis_execution_sha256": inspected["genesis_execution_sha256"],
        "genesis_sha256": inspected["genesis_sha256"],
        "proof_compose_sha256": inspected["proof_compose_sha256"],
        "proof": {
            "mode": "internal-health-assertion-bound-to-exact-compose",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "guardian_internal_only": True,
            "service_status": observations[-1]["status"] if observations else None,
            "predicates_proven_by_guardian": list(plan["proof"]["predicates"]),
            "chain_id": plan["chain_id"],
            "validator_set": list(plan["validator_set"]),
            "hub_service": plan["hub"]["service"],
            "hub_internal_port": plan["hub"]["internal_port"],
            "hub_health_url": plan["hub"]["health_url"],
            "hub_status_url": plan["hub"]["status_url"],
            "hub_local_rpc_url": plan["hub"]["local_rpc_url"],
            "hub_healthy": complete,
            "hub_local_rpc_verified": complete,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "soft_replica_untouched": True,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "failure": failure,
        "summary": {
            "clean": complete,
            "initial_chain_proven": complete,
            "compose_commitment_verified": complete,
            "service_running_healthy": complete,
            "chain_id_verified": complete,
            "genesis_file_commitment_verified": complete,
            "genesis_block_present": complete,
            "blocks_advancing": complete,
            "validator_set_verified": complete,
            "hub_healthy": complete,
            "hub_mainnet_binding_verified": complete,
            "hub_local_rpc_verified": complete,
            "complete_super_node_proven": complete,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "soft_replica_untouched": True,
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": any(item.get("live_write_acknowledged") for item in receipts),
            "complete": complete,
            "next_phase": "stage-soft-replica-configuration" if complete else "manual-review-required",
        },
    }
    evidence_path, evidence_sha = _write_document(paths, _EVIDENCE_DIRECTORY, evidence, operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_genesis_birth_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence, _, evidence_sha = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "birth evidence")
    if evidence.get("kind") != _EVIDENCE_KIND or evidence.get("status") != "pass" or evidence.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_EVIDENCE_INVALID", "birth evidence is not a clean current result")
    summary = evidence.get("summary")
    proof = evidence.get("proof")
    if not isinstance(summary, Mapping) or not isinstance(proof, Mapping) or not all([
        summary.get("clean") is True,
        summary.get("initial_chain_proven") is True,
        summary.get("manual_ssh_required") is False,
        summary.get("public_endpoint_created") is False,
        summary.get("soft_replica_untouched") is True,
        summary.get("hub_healthy") is True,
        summary.get("hub_mainnet_binding_verified") is True,
        summary.get("hub_local_rpc_verified") is True,
        summary.get("complete_super_node_proven") is True,
        summary.get("next_phase") == "stage-soft-replica-configuration",
        proof.get("guardian_internal_only") is True,
        proof.get("host_rpc_mapping_present") is False,
        proof.get("service_status") == "running:healthy",
        proof.get("hub_healthy") is True,
        proof.get("hub_local_rpc_verified") is True,
        proof.get("hub_service") == _HUB_SERVICE,
        proof.get("hub_internal_port") == _HUB_PORT,
        proof.get("hub_local_rpc_url") == f"http://{evidence.get('initial_node')}:8545",
    ]):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_EVIDENCE_INVALID", "birth evidence assertions are incomplete")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    completed = _parse_utc(evidence.get("completed_at"), "completed_at")
    age = int((reference_now - completed).total_seconds())
    if age < -1 or age > max_age_seconds:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_EVIDENCE_STALE", "birth evidence is outside the freshness window")
    node = _identifier(evidence.get("initial_node"), "initial node")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (node,):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_SELECTION_MISMATCH", "birth evidence targets only the initial node")
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": evidence_sha,
        "age_seconds": max(0, age),
        "mother_binding": dict(evidence["mother_binding"]),
        "network": evidence["network"],
        "nodes": [node],
        "initial_node": node,
        "chain_id": proof["chain_id"],
        "genesis_sha256": evidence["genesis_sha256"],
        "validator_set": list(proof["validator_set"]),
        "initial_chain_proven": True,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "guardian_internal_only": True,
        "hub_service": proof["hub_service"],
        "hub_internal_port": proof["hub_internal_port"],
        "hub_healthy": True,
        "hub_local_rpc_url": proof["hub_local_rpc_url"],
        "hub_local_rpc_verified": True,
        "complete_super_node_proven": True,
        "super_node_components": ["hub", "local-rpc", "besu", "qbft-validator", "foundationdb"],
        "soft_replica_untouched": True,
        "next_phase": "stage-soft-replica-configuration",
    }


__all__ = [
    "MotherDeploymentGenesisBirthError",
    "build_genesis_birth_release",
    "execute_genesis_birth_release",
    "inspect_genesis_birth_release",
    "verify_genesis_birth_evidence",
    "verify_genesis_birth_release",
    "write_genesis_birth_release",
]
