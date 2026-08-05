"""Expiring release for one exact internal QBFT validator-admission vote."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any
import urllib.parse

from . import atomic_files
from .canonical import canonical_json
from .deployment_genesis_birth import _compose_semantic_sha256
from .deployment_validator_admission import verify_validator_admission_transaction
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path

_RELEASE_KIND = "main_computer.mother.deployment_validator_admission_release.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-validator-admission-releases")
_TRANSACTION_DIRECTORY = ("actions", "deployment-validator-admission-transactions")
_SYNC_EVIDENCE_DIRECTORY = ("evidence", "deployment-soft-replica-sync")
_SYNC_RELEASE_DIRECTORY = ("actions", "deployment-soft-replica-sync-releases")
_ADMISSION_EVIDENCE_DIRECTORY = ("evidence", "deployment-validator-admission")
_PROOF_IMAGE = "python:3.12-alpine"
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentValidatorAdmissionReleaseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()) is None:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{path} is invalid"
        )
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{path} must be SHA-256"
        )
    return value


def _address(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is None:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{path} must be an Ethereum address"
        )
    return value.lower()


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{path} must be UTC"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not _MIN_RELEASE_SECONDS <= value <= _MAX_RELEASE_SECONDS:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    return value


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


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in document.items() if key != field})).hexdigest()


def _resolve(paths: PrivateStatePaths, locator: Any, directory: tuple[str, str], label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    candidate = Path(locator)
    if candidate.is_absolute() or PureWindowsPath(locator).is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    result = (paths.root / candidate).resolve(strict=False)
    expected = (paths.root / directory[0] / directory[1]).resolve(strict=False)
    try:
        result.relative_to(expected)
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_PATH_UNSAFE", f"{label} is outside its canonical directory"
        ) from exc
    return result


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _canonical(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{label} is unreadable"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _historical_order_sensitive_admission_script(
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    initial_validator: str,
    candidate_validator: str,
    candidate_node_id: str,
    rpc_request_sha256: str,
    legacy_json_boolean_bug: bool = False,
) -> str:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [candidate_validator.lower(), True],
    }
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    # JSON booleans are lowercase and are not valid Python literals.  The
    # legacy form is retained only to bind the exact failed Compose during a
    # one-time recovery; new guardians always decode the committed JSON text.
    request_line = (
        f"REQUEST = {request_json}"
        if legacy_json_boolean_bug
        else f"REQUEST = json.loads({request_json!r})"
    )
    return "\n".join([
        "import hashlib, json, os, time, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"INITIAL_VALIDATOR = '{initial_validator.lower()}'",
        f"CANDIDATE_VALIDATOR = '{candidate_validator.lower()}'",
        f"CANDIDATE_NODE_ID = '{candidate_node_id.lower()}'",
        request_line,
        f"EXPECTED_REQUEST_SHA256 = '{rpc_request_sha256}'",
        "PROOF = '/proof/validator-admission.json'",
        "HEALTHY = '/proof/validator-admission-healthy'",
        "MAX_BLOCK_AGE_SECONDS = 45",
        "def encoded(value):",
        "    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
        "def rpc(method, params):",
        "    body = encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params})",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value:",
        "        raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def validator_set():",
        "    return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "def load_proof():",
        "    try:",
        "        with open(PROOF, 'r', encoding='utf-8') as handle: return json.load(handle)",
        "    except (FileNotFoundError, json.JSONDecodeError, OSError): return None",
        "def prove():",
        "    request_sha = hashlib.sha256(encoded(REQUEST)).hexdigest()",
        "    if request_sha != EXPECTED_REQUEST_SHA256:",
        "        raise RuntimeError('vote request commitment mismatch')",
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
        "    peers = rpc('admin_peers', [])",
        "    if not isinstance(peers, list) or CANDIDATE_NODE_ID not in json.dumps(peers, sort_keys=True).lower():",
        "        raise RuntimeError('candidate peer missing')",
        "    current = validator_set()",
        "    desired = [INITIAL_VALIDATOR, CANDIDATE_VALIDATOR]",
        "    existing = load_proof()",
        "    vote_cast = False",
        "    if current == [INITIAL_VALIDATOR]:",
        "        if rpc(REQUEST['method'], REQUEST['params']) is not True:",
        "            raise RuntimeError('validator vote rejected')",
        "        vote_cast = True",
        "        deadline = time.time() + 60",
        "        while time.time() < deadline:",
        "            current = validator_set()",
        "            if current == desired: break",
        "            if current != [INITIAL_VALIDATOR]: raise RuntimeError('unexpected validator transition')",
        "            time.sleep(2)",
        "    elif current == desired:",
        "        if not isinstance(existing, dict) or existing.get('rpc_request_sha256') != EXPECTED_REQUEST_SHA256 or existing.get('vote_cast') is not True:",
        "            raise RuntimeError('validator already active without this release proof')",
        "    else:",
        "        raise RuntimeError('unexpected starting validator set')",
        "    if current != desired:",
        "        raise RuntimeError('desired validator set not reached')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first:",
        "        raise RuntimeError('block height did not advance')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'):",
        "        raise RuntimeError('latest block missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS:",
        "        raise RuntimeError('latest block is stale')",
        "    proof = {'chain_id':chain_id,'genesis_sha256':genesis_digest,'rpc_request_sha256':request_sha,'vote_cast':True,'current_validator_set':[INITIAL_VALIDATOR],'desired_validator_set':desired,'final_validator_set':current,'candidate_peer_verified':True,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
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



def _admission_script(
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    initial_validator: str,
    candidate_validator: str,
    candidate_node_id: str,
    rpc_request_sha256: str,
    legacy_json_boolean_bug: bool = False,
    legacy_order_sensitive_bug: bool = False,
    legacy_silent_errors: bool = False,
    legacy_pre_order_recovery_guardian: bool = False,
) -> str:
    if legacy_pre_order_recovery_guardian:
        return _historical_order_sensitive_admission_script(
            node=node,
            chain_id=chain_id,
            genesis_sha256=genesis_sha256,
            initial_validator=initial_validator,
            candidate_validator=candidate_validator,
            candidate_node_id=candidate_node_id,
            rpc_request_sha256=rpc_request_sha256,
            legacy_json_boolean_bug=legacy_json_boolean_bug,
        )

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [candidate_validator.lower(), True],
    }
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    # JSON booleans are lowercase and are not valid Python literals.  The
    # legacy form is retained only to bind the exact failed Compose during a
    # one-time recovery; new guardians always decode the committed JSON text.
    request_line = (
        f"REQUEST = {request_json}"
        if legacy_json_boolean_bug
        else f"REQUEST = json.loads({request_json!r})"
    )
    import_line = (
        "import hashlib, json, os, time, urllib.request"
        if legacy_silent_errors
        else "import hashlib, json, os, time, traceback, urllib.request"
    )
    validator_set_line = (
        "    return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]"
        if legacy_order_sensitive_bug
        else "    return sorted(set(str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])))"
    )
    desired_line = (
        "    desired = [INITIAL_VALIDATOR, CANDIDATE_VALIDATOR]"
        if legacy_order_sensitive_bug
        else "    desired = sorted([INITIAL_VALIDATOR, CANDIDATE_VALIDATOR])"
    )
    except_lines = (
        [
            "    except Exception:",
            "        try: os.unlink(HEALTHY)",
            "        except FileNotFoundError: pass",
        ]
        if legacy_silent_errors
        else [
            "    except Exception:",
            "        traceback.print_exc()",
            "        try: os.unlink(HEALTHY)",
            "        except FileNotFoundError: pass",
        ]
    )
    lines = [
        import_line,
        f"RPC = 'http://{node}:8545'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"INITIAL_VALIDATOR = '{initial_validator.lower()}'",
        f"CANDIDATE_VALIDATOR = '{candidate_validator.lower()}'",
        f"CANDIDATE_NODE_ID = '{candidate_node_id.lower()}'",
        request_line,
        f"EXPECTED_REQUEST_SHA256 = '{rpc_request_sha256}'",
        "PROOF = '/proof/validator-admission.json'",
        "HEALTHY = '/proof/validator-admission-healthy'",
        "MAX_BLOCK_AGE_SECONDS = 45",
        "def encoded(value):",
        "    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
        "def rpc(method, params):",
        "    body = encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params})",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value:",
        "        raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def validator_set():",
        validator_set_line,
        "def load_proof():",
        "    try:",
        "        with open(PROOF, 'r', encoding='utf-8') as handle: return json.load(handle)",
        "    except (FileNotFoundError, json.JSONDecodeError, OSError): return None",
        "def prove():",
        "    request_sha = hashlib.sha256(encoded(REQUEST)).hexdigest()",
        "    if request_sha != EXPECTED_REQUEST_SHA256:",
        "        raise RuntimeError('vote request commitment mismatch')",
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
        "    peers = rpc('admin_peers', [])",
        "    if not isinstance(peers, list) or CANDIDATE_NODE_ID not in json.dumps(peers, sort_keys=True).lower():",
        "        raise RuntimeError('candidate peer missing')",
        "    current = validator_set()",
        desired_line,
        "    starting = list(current)",
        "    existing = load_proof()",
        "    vote_cast = False",
        "    activation_reconciled = False",
        "    if current == [INITIAL_VALIDATOR]:",
        "        if rpc(REQUEST['method'], REQUEST['params']) is not True:",
        "            raise RuntimeError('validator vote rejected')",
        "        vote_cast = True",
        "        deadline = time.time() + 60",
        "        while time.time() < deadline:",
        "            current = validator_set()",
        "            if current == desired: break",
        "            if current != [INITIAL_VALIDATOR]: raise RuntimeError('unexpected validator transition')",
        "            time.sleep(2)",
        "    elif current == desired:",
        "        activation_reconciled = not (isinstance(existing, dict) and existing.get('rpc_request_sha256') == EXPECTED_REQUEST_SHA256 and existing.get('vote_cast') is True)",
        "    else:",
        "        raise RuntimeError('unexpected starting validator set')",
        "    if current != desired:",
        "        raise RuntimeError('desired validator set not reached')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first:",
        "        raise RuntimeError('block height did not advance')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'):",
        "        raise RuntimeError('latest block missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS:",
        "        raise RuntimeError('latest block is stale')",
        "    proof = {'chain_id':chain_id,'genesis_sha256':genesis_digest,'rpc_request_sha256':request_sha,'vote_cast':vote_cast,'activation_reconciled':activation_reconciled,'starting_validator_set':starting,'current_validator_set':[INITIAL_VALIDATOR],'desired_validator_set':desired,'final_validator_set':current,'candidate_peer_verified':True,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    temporary = PROOF + '.tmp'",
        "    with open(temporary, 'w', encoding='utf-8') as handle:",
        "        json.dump(proof, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(temporary, PROOF)",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle:",
        "        handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
    ]
    lines.extend(except_lines)
    lines.extend([
        "    time.sleep(6)",
        "",
    ])
    return "\n".join(lines)


def _internal_admission_compose(
    original: str,
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    initial_validator: str,
    candidate_validator: str,
    candidate_node_id: str,
    rpc_request_sha256: str,
    legacy_json_boolean_bug: bool = False,
    legacy_order_sensitive_bug: bool = False,
    legacy_silent_errors: bool = False,
    legacy_pre_order_recovery_guardian: bool = False,
) -> str:
    start_marker = "  mother-genesis-proof-guardian:\n"
    end_marker = "\nvolumes:\n"
    if original.count(start_marker) != 1 or original.count(end_marker) != 1:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_COMPOSE_UNSUPPORTED",
            "A proof Compose does not match the supported internal-guardian template",
        )
    start = original.index(start_marker)
    end = original.index(end_marker, start)
    script = _admission_script(
        node=node,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        initial_validator=initial_validator,
        candidate_validator=candidate_validator,
        candidate_node_id=candidate_node_id,
        rpc_request_sha256=rpc_request_sha256,
        legacy_json_boolean_bug=legacy_json_boolean_bug,
        legacy_order_sensitive_bug=legacy_order_sensitive_bug,
        legacy_silent_errors=legacy_silent_errors,
        legacy_pre_order_recovery_guardian=legacy_pre_order_recovery_guardian,
    )
    indented = "\n".join("        " + line for line in script.splitlines())
    guardian = "\n".join([
        "  mother-validator-admission-guardian:",
        f"    image: {_PROOF_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    depends_on:",
        f"      {node}:",
        "        condition: service_started",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        indented,
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        "        - import os,time; p='/proof/validator-admission-healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 24",
        "      start_period: 30s",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-proof:/proof",
        "",
    ])
    updated = original[:start] + guardian + original[end:]
    section = updated.split("  mother-validator-admission-guardian:", 1)[1].split(end_marker, 1)[0]
    if any(item in section for item in ("ports:", "expose:", "traefik.", "domains:", "fqdn:")):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_GUARDIAN_EXPOSED",
            "validator-admission guardian must remain internal-only",
        )
    if "8545:8545" in updated:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_RPC_EXPOSED",
            "validator-admission Compose must not publish JSON-RPC",
        )
    return updated


def _chain(paths: PrivateStatePaths, transaction: Mapping[str, Any]) -> dict[str, Any]:
    evidence_ref = transaction.get("synchronization_evidence")
    if not isinstance(evidence_ref, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "synchronization evidence binding is missing"
        )
    evidence_path = _resolve(paths, evidence_ref.get("locator"), _SYNC_EVIDENCE_DIRECTORY, "synchronization evidence")
    evidence, _, evidence_sha = _canonical(evidence_path, "synchronization evidence")
    if evidence_sha != evidence_ref.get("sha256") or evidence.get("status") != "pass":
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "synchronization evidence is invalid"
        )
    release_ref = evidence.get("release")
    if not isinstance(release_ref, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "synchronization release binding is missing"
        )
    sync_release_path = _resolve(paths, release_ref.get("locator"), _SYNC_RELEASE_DIRECTORY, "synchronization release")
    sync_release, _, _ = _canonical(sync_release_path, "synchronization release")
    if sync_release.get("soft_replica_sync_release_sha256") != release_ref.get("sha256"):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "synchronization release digest mismatch"
        )
    initial = sync_release.get("initial_chain_precondition")
    plan = sync_release.get("proof_plan")
    if not isinstance(initial, Mapping) or not isinstance(plan, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "synchronization release chain bindings are missing"
        )
    initial_compose = initial.get("proof_compose")
    replica_compose = plan.get("proof_compose")
    if not isinstance(initial_compose, Mapping) or not isinstance(replica_compose, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "proof Compose bindings are missing"
        )
    if type(initial_compose.get("canonical_text")) is not str or type(replica_compose.get("canonical_text")) is not str:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "proof Compose text is missing"
        )
    return {
        "evidence_path": evidence_path,
        "evidence_sha256": evidence_sha,
        "initial": initial,
        "plan": plan,
        "initial_compose": initial_compose,
        "replica_compose": replica_compose,
        "candidate_node_id": _identifier(plan.get("replica_node_id"), "candidate node ID"),
    }



def _canonical_input_path(
    paths: PrivateStatePaths,
    path: Path,
    directory: tuple[str, str],
    label: str,
) -> tuple[Path, dict[str, Any], bytes, str]:
    candidate = Path(path).resolve(strict=False)
    expected = (paths.root / directory[0] / directory[1]).resolve(strict=False)
    try:
        candidate.relative_to(expected)
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_PATH_UNSAFE",
            f"{label} is outside its canonical directory",
        ) from exc
    document, raw, digest = _canonical(candidate, label)
    return candidate, document, raw, digest


def _exact_failed_release_recovery(
    paths: PrivateStatePaths,
    failed_evidence_path: Path,
    *,
    transaction_sha256: str,
    network: str,
    service_uuid: str,
    initial_validator: str,
    candidate_validator: str,
) -> dict[str, Any]:
    evidence_path, evidence, _, evidence_sha = _canonical_input_path(
        paths,
        failed_evidence_path,
        _ADMISSION_EVIDENCE_DIRECTORY,
        "failed validator-admission evidence",
    )
    summary = evidence.get("summary")
    failure = evidence.get("failure")
    release_ref = evidence.get("release")
    receipts = evidence.get("mutation_receipts")
    if not all([
        evidence.get("kind") == "main_computer.mother.deployment_validator_admission_evidence.v1",
        evidence.get("status") == "failed",
        evidence.get("network") == network,
        evidence.get("service_uuid") == service_uuid,
        evidence.get("validator_admission_transaction_sha256") == transaction_sha256,
        isinstance(summary, Mapping),
        summary.get("live_mutation_performed") is True,
        summary.get("succeeded_mutation_count") == 2,
        summary.get("failed_mutation_count") == 0,
        isinstance(failure, Mapping),
        failure.get("code") == "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_HEALTHY",
        isinstance(release_ref, Mapping),
        type(receipts) is list,
    ]):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission evidence is not an exact post-mutation unhealthy admission result",
        )

    prior_release_path = _resolve(
        paths,
        release_ref.get("locator"),
        _RELEASE_DIRECTORY,
        "failed validator-admission release",
    )
    prior_release, prior_raw, prior_byte_sha = _canonical(prior_release_path, "failed validator-admission release")
    prior_release_sha = prior_release.get("validator_admission_release_sha256")
    if (
        prior_release_sha != release_ref.get("sha256")
        or prior_release_sha != _digest_without(prior_release, "validator_admission_release_sha256")
    ):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission release digest does not match its evidence binding",
        )
    transaction_ref = prior_release.get("transaction")
    plan = prior_release.get("execution_plan")
    if (
        not isinstance(transaction_ref, Mapping)
        or transaction_ref.get("sha256") != transaction_sha256
        or not isinstance(plan, Mapping)
        or plan.get("service_uuid") != service_uuid
        or plan.get("current_validator_set") != [initial_validator]
        or plan.get("desired_validator_set") != [initial_validator, candidate_validator]
    ):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission release does not bind the current transaction and validator set",
        )

    mutations = plan.get("mutations")
    if type(mutations) is not list or len(mutations) != 2:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission release mutation plan is malformed",
        )
    patch_mutation = mutations[0]
    patch_receipt = next(
        (
            item for item in receipts
            if isinstance(item, Mapping)
            and item.get("mutation_id") == "mainneta-super1.install-validator-admission-guardian"
        ),
        None,
    )
    if not isinstance(patch_mutation, Mapping) or not isinstance(patch_receipt, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission PATCH binding is missing",
        )
    body = patch_mutation.get("canonical_request_body")
    if not isinstance(body, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission PATCH body is missing",
        )
    body_map = dict(body)
    body_sha = hashlib.sha256(canonical_json(body_map)).hexdigest()
    if not all([
        patch_mutation.get("body_sha256") == body_sha,
        patch_receipt.get("body_sha256") == body_sha,
        patch_receipt.get("live_write_acknowledged") is True,
        patch_receipt.get("status") == "succeeded",
    ]):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission PATCH body is not exactly proven by its execution receipt",
        )
    encoded = body_map.get("docker_compose_raw")
    if type(encoded) is not str:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission PATCH body lacks Compose",
        )
    try:
        compose_bytes = base64.b64decode(encoded, validate=True)
        compose_text = compose_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_FAILED_EVIDENCE_INVALID",
            "failed admission Compose is not valid canonical UTF-8 base64",
        ) from exc

    return {
        "allowed": True,
        "cause_code": "exact-prior-failed-release-post-mutation-unhealthy",
        "accepted_service_statuses": [
            "degraded:unhealthy",
            "exited",
            "running:unhealthy",
            "starting:unhealthy",
        ],
        "failed_evidence": {
            "locator": _relative(paths, evidence_path, "failed validator-admission evidence"),
            "sha256": evidence_sha,
        },
        "failed_release": {
            "locator": _relative(paths, prior_release_path, "failed validator-admission release"),
            "sha256": prior_release_sha,
            "byte_sha256": prior_byte_sha,
        },
        "failed_admission_compose": {
            "canonical_text": compose_text,
            "sha256": hashlib.sha256(compose_bytes).hexdigest(),
            "semantic_sha256": _compose_semantic_sha256(
                compose_text, "exact failed validator-admission Compose"
            ),
            "body_sha256": body_sha,
        },
        "scope": "reconcile-exact-prior-failed-release",
    }


def build_validator_admission_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    failed_evidence_path: Path | None = None,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_transaction_sha256, "acknowledged transaction SHA-256")
    verified = verify_validator_admission_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
    )
    if acknowledged != verified["validator_admission_transaction_sha256"]:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact admission transaction",
        )
    transaction_path = Path(verified["transaction_path"])
    transaction, _, transaction_byte_sha = _canonical(transaction_path, "validator-admission transaction")
    if transaction_byte_sha != verified["byte_sha256"]:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "transaction byte digest mismatch"
        )
    admission = transaction.get("admission")
    current = transaction.get("current_chain")
    if not isinstance(admission, Mapping) or not isinstance(current, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "admission transaction is incomplete"
        )
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != ("mainnetc-super1",):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_SELECTION_MISMATCH",
            "validator admission may target only mainnetc-super1",
        )
    chain = _chain(paths, transaction)
    initial = chain["initial"]
    plan = chain["plan"]
    initial_validator = _address(admission.get("current_validator_set", [None])[0], "initial validator")
    candidate_validator = _address(admission.get("candidate_validator_address"), "candidate validator")
    desired = [_address(item, "desired validator") for item in admission.get("desired_validator_set", [])]
    if desired != [initial_validator, candidate_validator]:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "desired validator set changed"
        )
    rpc_request = admission.get("rpc_request")
    if not isinstance(rpc_request, Mapping) or rpc_request.get("method") != "qbft_proposeValidatorVote":
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "admission RPC request is invalid"
        )
    rpc_sha = _sha256(admission.get("rpc_request_sha256"), "RPC request SHA-256")
    if hashlib.sha256(canonical_json(dict(rpc_request))).hexdigest() != rpc_sha:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "admission RPC request commitment changed"
        )
    initial_compose_text = chain["initial_compose"]["canonical_text"]
    admission_compose = _internal_admission_compose(
        initial_compose_text,
        node="mainneta-super1",
        chain_id=int(current.get("chain_id")),
        genesis_sha256=_sha256(current.get("genesis_sha256"), "genesis SHA-256"),
        initial_validator=initial_validator,
        candidate_validator=candidate_validator,
        candidate_node_id=chain["candidate_node_id"],
        rpc_request_sha256=rpc_sha,
    )
    legacy_broken_compose = _internal_admission_compose(
        initial_compose_text,
        node="mainneta-super1",
        chain_id=int(current.get("chain_id")),
        genesis_sha256=_sha256(current.get("genesis_sha256"), "genesis SHA-256"),
        initial_validator=initial_validator,
        candidate_validator=candidate_validator,
        candidate_node_id=chain["candidate_node_id"],
        rpc_request_sha256=rpc_sha,
        legacy_json_boolean_bug=True,
        legacy_pre_order_recovery_guardian=True,
    )
    order_sensitive_broken_compose = _internal_admission_compose(
        initial_compose_text,
        node="mainneta-super1",
        chain_id=int(current.get("chain_id")),
        genesis_sha256=_sha256(current.get("genesis_sha256"), "genesis SHA-256"),
        initial_validator=initial_validator,
        candidate_validator=candidate_validator,
        candidate_node_id=chain["candidate_node_id"],
        rpc_request_sha256=rpc_sha,
        legacy_pre_order_recovery_guardian=True,
    )
    compose_bytes = admission_compose.encode("utf-8")
    compose_sha = hashlib.sha256(compose_bytes).hexdigest()
    body = {
        "name": "mainneta-super1",
        "docker_compose_raw": base64.b64encode(compose_bytes).decode("ascii"),
    }
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    expires = created + timedelta(seconds=_duration(expires_in_seconds))
    service_uuid = _identifier(initial.get("service_uuid"), "initial service UUID")
    exact_failed_recovery = (
        _exact_failed_release_recovery(
            paths,
            Path(failed_evidence_path),
            transaction_sha256=verified["validator_admission_transaction_sha256"],
            network=transaction["network"],
            service_uuid=service_uuid,
            initial_validator=initial_validator,
            candidate_validator=candidate_validator,
        )
        if failed_evidence_path is not None
        else None
    )
    encoded_uuid = urllib.parse.quote(service_uuid, safe="")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": transaction["network"],
        "mother_binding": _binding(private_state),
        "staged_scope": "release-and-execute-validator-admission",
        "transaction": {
            "locator": _relative(paths, transaction_path, "validator-admission transaction"),
            "sha256": verified["validator_admission_transaction_sha256"],
            "byte_sha256": transaction_byte_sha,
        },
        "synchronization_evidence": {
            "locator": _relative(paths, chain["evidence_path"], "synchronization evidence"),
            "sha256": chain["evidence_sha256"],
        },
        "initial_chain_precondition": {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": service_uuid,
            "service_status": "running:healthy",
            "proof_compose": {
                "canonical_text": initial_compose_text,
                "sha256": _sha256(chain["initial_compose"].get("sha256"), "initial proof Compose SHA-256"),
                "semantic_sha256": _compose_semantic_sha256(initial_compose_text, "A proof Compose"),
            },
            "current_validator_set": [initial_validator],
            "read_only_until_released_mutation": True,
        },
        "known_failed_guardian_recovery": {
            "allowed": True,
            "bug_code": "json-boolean-literal-in-python-source",
            "failure_occurs_before_rpc": True,
            "accepted_service_statuses": [
                "degraded:unhealthy",
                "exited",
                "running:unhealthy",
                "starting:unhealthy",
            ],
            "broken_admission_compose": {
                "canonical_text": legacy_broken_compose,
                "sha256": hashlib.sha256(legacy_broken_compose.encode("utf-8")).hexdigest(),
                "semantic_sha256": _compose_semantic_sha256(
                    legacy_broken_compose, "known-broken validator-admission Compose"
                ),
            },
            "replacement_admission_compose_sha256": compose_sha,
            "scope": "replace-exact-known-broken-guardian-before-vote",
        },
        "known_order_sensitive_guardian_recovery": {
            "allowed": True,
            "bug_code": "validator-set-order-sensitive-comparison",
            "vote_may_have_been_cast": True,
            "historical_guardian_lineage": "boolean-fix-before-order-recovery",
            "accepted_service_statuses": [
                "degraded:unhealthy",
                "exited",
                "running:unhealthy",
                "starting:unhealthy",
            ],
            "broken_admission_compose": {
                "canonical_text": order_sensitive_broken_compose,
                "sha256": hashlib.sha256(order_sensitive_broken_compose.encode("utf-8")).hexdigest(),
                "semantic_sha256": _compose_semantic_sha256(
                    order_sensitive_broken_compose, "known-order-sensitive validator-admission Compose"
                ),
            },
            "replacement_admission_compose_sha256": compose_sha,
            "scope": "reconcile-exact-known-order-sensitive-guardian",
        },
        "exact_failed_release_recovery": exact_failed_recovery,
        "known_replica_post_admission_guardian_recovery": {
            "allowed": True,
            "cause_code": "sole-validator-sync-guardian-invalidated-by-candidate-activation",
            "requires_initial_precondition_mode": "known-validator-set-order-recovery",
            "requires_initial_precondition_modes": [
                "known-validator-set-order-recovery",
                "known-exact-failed-release-recovery",
            ],
            "accepted_service_statuses": [
                "degraded:unhealthy",
                "exited",
                "running:unhealthy",
                "starting:unhealthy",
            ],
            "stale_replica_compose": {
                "canonical_text": chain["replica_compose"]["canonical_text"],
                "sha256": _sha256(chain["replica_compose"].get("sha256"), "replica proof Compose SHA-256"),
                "semantic_sha256": _compose_semantic_sha256(
                    chain["replica_compose"]["canonical_text"], "C synchronization proof Compose"
                ),
            },
            "expected_pre_admission_validator_set": [initial_validator],
            "expected_post_admission_validator_set": desired,
            "candidate_node_id": chain["candidate_node_id"],
            "read_only": True,
            "scope": "accept-exact-stale-sync-guardian-after-possible-admission",
        },
        "replica_precondition": {
            "node": "mainnetc-super1",
            "controller_id": "coolify-c",
            "service_uuid": _identifier(plan.get("service_uuid"), "replica service UUID"),
            "service_status": "running:healthy",
            "proof_compose": {
                "canonical_text": chain["replica_compose"]["canonical_text"],
                "sha256": _sha256(chain["replica_compose"].get("sha256"), "replica proof Compose SHA-256"),
                "semantic_sha256": _compose_semantic_sha256(chain["replica_compose"]["canonical_text"], "C synchronization proof Compose"),
            },
            "candidate_node_id": chain["candidate_node_id"],
            "candidate_validator_address": candidate_validator,
            "read_only": True,
        },
        "execution_plan": {
            "vote_origin_node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": service_uuid,
            "chain_id": int(current.get("chain_id")),
            "genesis_sha256": _sha256(current.get("genesis_sha256"), "genesis SHA-256"),
            "current_validator_set": [initial_validator],
            "desired_validator_set": desired,
            "candidate_node": "mainnetc-super1",
            "candidate_node_id": chain["candidate_node_id"],
            "candidate_validator_address": candidate_validator,
            "rpc_request": dict(rpc_request),
            "rpc_request_sha256": rpc_sha,
            "admission_compose": {
                "canonical_text": admission_compose,
                "sha256": compose_sha,
                "semantic_sha256": _compose_semantic_sha256(admission_compose, "validator-admission Compose"),
                "byte_length": len(compose_bytes),
                "guardian_image": _PROOF_IMAGE,
                "guardian_internal_only": True,
                "guardian_public_ports": [],
                "guardian_domains": [],
                "host_rpc_mapping_present": False,
            },
            "mutations": [
                {
                    "ordinal": 1,
                    "mutation_id": "mainneta-super1.install-validator-admission-guardian",
                    "controller_id": "coolify-a",
                    "method": "PATCH",
                    "endpoint": f"/api/v1/services/{encoded_uuid}",
                    "canonical_request_body": body,
                    "body_sha256": body_sha,
                    "success_statuses": [200, 201, 202],
                },
                {
                    "ordinal": 2,
                    "mutation_id": "mainneta-super1.deploy-validator-admission-guardian",
                    "controller_id": "coolify-a",
                    "method": "GET",
                    "endpoint": f"/api/v1/deploy?uuid={encoded_uuid}&force=true",
                    "canonical_request_body": None,
                    "body_sha256": None,
                    "success_statuses": [200, 201, 202],
                },
            ],
            "proof": {
                "transport": "coolify-control-plane-only",
                "manual_ssh_required": False,
                "public_endpoint_created": False,
                "guardian_internal_only": True,
                "predicates": [
                    "exact-rpc-request-sha256",
                    "genesis-file-sha256",
                    "chain-id",
                    "genesis-block-present",
                    "candidate-peer-present",
                    "starting-validator-set-exactly-A",
                    "vote-accepted",
                    "final-validator-address-set-exactly-A-plus-C",
                    "fresh-block-height-advancing",
                ],
                "success_signal": "exact A service reports running:healthy under the exact validator-admission Compose",
            },
        },
        "authority": {
            "transaction_apply_authorized": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "live_execution_authorized": False,
            "requested_use_limit": 1,
            "authorization_source": "explicit-operator-release",
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "vote_origin_node_only": True,
            "replica_node_read_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "automatic_rollback_performed": False,
            "known_failed_guardian_recovery_allowed": True,
            "known_order_sensitive_guardian_recovery_allowed": True,
            "known_replica_post_admission_guardian_recovery_allowed": True,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED",
                "message": "the one-use internal admission executor must consume this exact release",
            }
        ],
        "summary": {
            "release_valid": True,
            "mutation_count": 2,
            "vote_origin_node": "mainneta-super1",
            "candidate_node": "mainnetc-super1",
            "current_validator_count": 1,
            "desired_validator_count": 2,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "known_failed_guardian_recovery_allowed": True,
            "known_order_sensitive_guardian_recovery_allowed": True,
            "known_replica_post_admission_guardian_recovery_allowed": True,
            "next_phase_after_apply": "stage-post-admission-steady-state",
        },
        "validator_admission_release_sha256": None,
    }
    release["validator_admission_release_sha256"] = _digest_without(release, "validator_admission_release_sha256")
    if _contains_sensitive(release):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "validator-admission release contains sensitive material"
        )
    return release


def write_validator_admission_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "validator_admission_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("validator_admission_release_sha256") != digest or _contains_sensitive(document):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "validator-admission release is malformed"
        )
    payload = canonical_json(document)
    current = paths.root
    for part in _RELEASE_DIRECTORY:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "admissionrelease"
    destination = current / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentValidatorAdmissionReleaseError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_CONFLICT", "release destination contains different bytes"
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_validator_admission_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    candidate = Path(release_path).resolve(strict=False)
    expected_root = (paths.root / _RELEASE_DIRECTORY[0] / _RELEASE_DIRECTORY[1]).resolve(strict=False)
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_PATH_UNSAFE", "release is outside its canonical directory"
        ) from exc
    document, raw, byte_sha = _canonical(candidate, "validator-admission release")
    digest = _digest_without(document, "validator_admission_release_sha256")
    if not all([
        document.get("kind") == _RELEASE_KIND,
        document.get("validator_admission_release_sha256") == digest,
        document.get("mother_binding") == _binding(private_state),
        not _contains_sensitive(document),
    ]):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "validator-admission release is invalid or stale"
        )
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(document.get("created_at"), "created_at")
    expires = _parse_utc(document.get("expires_at"), "expires_at")
    if reference < created - timedelta(seconds=1) or reference > expires or int((reference - created).total_seconds()) > max_age_seconds:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_EXPIRED", "release is outside its authority window"
        )
    transaction_ref = document.get("transaction")
    if not isinstance(transaction_ref, Mapping):
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "transaction binding is missing"
        )
    transaction_path = _resolve(paths, transaction_ref.get("locator"), _TRANSACTION_DIRECTORY, "validator-admission transaction")
    exact_recovery = document.get("exact_failed_release_recovery")
    failed_evidence_path = None
    if exact_recovery is not None:
        if not isinstance(exact_recovery, Mapping):
            raise MotherDeploymentValidatorAdmissionReleaseError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID",
                "exact failed-release recovery binding is malformed",
            )
        evidence_ref = exact_recovery.get("failed_evidence")
        if not isinstance(evidence_ref, Mapping):
            raise MotherDeploymentValidatorAdmissionReleaseError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID",
                "exact failed-release evidence binding is missing",
            )
        failed_evidence_path = _resolve(
            paths,
            evidence_ref.get("locator"),
            _ADMISSION_EVIDENCE_DIRECTORY,
            "failed validator-admission evidence",
        )
    expected = build_validator_admission_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=_sha256(transaction_ref.get("sha256"), "transaction SHA-256"),
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=document.get("created_at"),
        failed_evidence_path=failed_evidence_path,
    )
    if canonical_json(expected) != raw:
        raise MotherDeploymentValidatorAdmissionReleaseError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_INVALID", "release no longer matches its exact inputs"
        )
    plan = document["execution_plan"]
    return {
        "clean": True,
        "release_path": str(candidate),
        "validator_admission_release_sha256": digest,
        "byte_sha256": byte_sha,
        "validator_admission_transaction_sha256": transaction_ref["sha256"],
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": [plan["candidate_node"]],
        "initial_node": plan["vote_origin_node"],
        "candidate_node": plan["candidate_node"],
        "candidate_validator_address": plan["candidate_validator_address"],
        "controller_id": plan["controller_id"],
        "service_uuid": plan["service_uuid"],
        "chain_id": plan["chain_id"],
        "genesis_sha256": plan["genesis_sha256"],
        "current_validator_set": list(plan["current_validator_set"]),
        "desired_validator_set": list(plan["desired_validator_set"]),
        "rpc_method": plan["rpc_request"]["method"],
        "rpc_request_sha256": plan["rpc_request_sha256"],
        "admission_compose_sha256": plan["admission_compose"]["sha256"],
        "mutation_count": len(plan["mutations"]),
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "staged_scope": document["staged_scope"],
        "transaction_apply_authorized": True,
        "validator_vote_authorized": True,
        "validator_activation_authorized": True,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "known_failed_guardian_recovery_allowed": True,
        "exact_failed_release_recovery_allowed": exact_recovery is not None,
        "known_replica_post_admission_guardian_recovery_allowed": True,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED"],
    }


__all__ = [
    "MotherDeploymentValidatorAdmissionReleaseError",
    "build_validator_admission_release",
    "verify_validator_admission_release",
    "write_validator_admission_release",
]
