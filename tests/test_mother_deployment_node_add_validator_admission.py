from __future__ import annotations

import base64
import hashlib
import inspect
from types import SimpleNamespace

import yaml

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_validator_admission import (
    MotherDeploymentNodeAddValidatorAdmissionError,
    execute_node_add_validator_admission_release,
    adopt_node_add_validator_admission_live_proof,
    build_node_add_validator_admission_release,
    _candidate_activation_compose,
    _candidate_activation_proof_endpoint,
    _fetch_candidate_activation_proof_payload,
    _digest_without,
    _durable_validator_admission_proof_verified,
    _canonical_validator_history_proof_verified,
    _CANONICAL_HISTORY_PROOF_CONTRACT,
    _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS,
    _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD,
    _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD,
    _canonical_history_proof_payload_sha256,
    _bootnode_p2p_reachability_receipt,
    _candidate_validator_enode,
    _component_healthy,
    _component_proven,
    _find_conflicting_node_remove_voters,
    _install_voter_guardian,
    _http,
    _parse_bootnode_p2p_endpoint,
    _preflight_existing_validator_services,
    _recover_target_start_rejection,
    _restart_validator_services_for_qbft_transition,
    _service_uuid_hints_from_replica_sync_evidence,
    _wait_for_admission_proof_guardians,
    _validator_vote_address,
    _validator_admission_public_endpoint_policy_ok,
    _QBFT_STALE_VOTE_QUIET_SECONDS,
    _voter_guardian_script,
)


def test_add_node_validator_activation_compose_is_internal_and_uses_env_reference() -> None:
    genesis = b'{"config":{"chainId":42424240}}'
    genesis_b64 = base64.b64encode(genesis).decode("ascii")
    genesis_sha = hashlib.sha256(genesis).hexdigest()

    compose = _candidate_activation_compose(
        target_node="mainneta-super1",
        genesis_b64=genesis_b64,
        bootnode_enode="enode://" + "a" * 128 + "@159.203.184.182:30303",
        chain_id=42424240,
        genesis_sha256=genesis_sha,
        target_node_id="b" * 128,
        desired_validators=[
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
        ],
        candidate_p2p_port=30304,
        candidate_validator_route={"advertised_host": "10.116.0.3", "p2p_endpoint": "10.116.0.3:30304"},
        proof_public_host="198.199.75.153",
    )

    parsed = yaml.safe_load(compose)
    assert set(parsed["services"]) == {
        "mother-validator-activation-init",
        "mainneta-super1",
        "mother-add-node-validator-activation-guardian",
        "mother-replica-sync-guardian",
    }
    assert parsed["services"]["mainneta-super1"]["ports"] == ["30304:30304/tcp", "30304:30304/udp"]
    guardian_ports = parsed["services"]["mother-add-node-validator-activation-guardian"]["ports"]
    assert guardian_ports == ["39304:8797/tcp"]
    assert "10.116.0.3" not in guardian_ports[0]
    assert "198.199.75.153" not in guardian_ports[0]
    sentinel = parsed["services"]["mother-replica-sync-guardian"]
    assert sentinel["image"] == "python:3.12-alpine"
    assert sentinel["restart"] == "unless-stopped"
    assert sentinel["read_only"] is True
    assert "ports" not in sentinel
    assert "volumes" not in sentinel
    assert "environment" not in sentinel
    assert sentinel["labels"]["main_computer.mother.stage"] == "post-admission-retired-helper-sentinel"
    assert sentinel["labels"]["main_computer.mother.retired-helper"] == "mother-replica-sync-guardian"
    assert 'MC_MOTHER_VALIDATOR_PRIVATE_KEY: "${MC_MOTHER_VALIDATOR_PRIVATE_KEY}"' in compose
    assert "0x" + "1" * 64 not in compose
    assert "main_computer.mother.validator-activation: active" in compose
    assert "--p2p-port=30304" in compose
    assert "30303:30303" not in compose


def test_replica_sync_release_reference_uses_logical_self_digest_not_file_bytes() -> None:
    release = {
        "kind": "main_computer.mother.deployment_node_add_replica_sync_release.v1",
        "schema_version": 1,
        "network": "mainnet",
        "target": {"node": "mainneta-super1"},
        "node_add_replica_sync_release_sha256": None,
    }
    logical_digest = _digest_without(release, "node_add_replica_sync_release_sha256")
    release["node_add_replica_sync_release_sha256"] = logical_digest

    file_byte_digest = hashlib.sha256(canonical_json(release)).hexdigest()

    assert release["node_add_replica_sync_release_sha256"] == logical_digest
    assert file_byte_digest != logical_digest


def test_validator_admission_uses_checksummed_candidate_for_qbft_vote_rpc() -> None:
    lowercase = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
    checksummed = _validator_vote_address(lowercase, "candidate validator")
    request = {"jsonrpc": "2.0", "id": 1, "method": "qbft_proposeValidatorVote", "params": [checksummed, True]}
    script = _voter_guardian_script(
        voter="mainneta-super1",
        candidate=lowercase,
        candidate_enode="enode://" + "b" * 128 + "@10.116.0.2:30303",
        current_validators=["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
        desired_validators=[lowercase, "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
        chain_id=42424240,
        genesis_sha256="a" * 64,
        request_sha256=hashlib.sha256(canonical_json(request)).hexdigest(),
    )

    assert checksummed == "0x9B809F05F8D68Da17e697cD6Ab040d4320494611"
    assert checksummed in script
    assert '"params":["0x9b809f05f8d68da17e697cd6ab040d4320494611",true]' not in script
    assert '"params":["0x9B809F05F8D68Da17e697cD6Ab040d4320494611",true]' in script
    assert "CANDIDATE_ENODE = 'enode://" in script
    assert "admin_addPeer" in script
    assert "admin_peers" in script
    assert "already_connected_after_reject" in script
    assert "expected peer is not connected" in script
    assert "candidate_peer_connect_result" in script
    assert "candidate_enode_sha256" in script


def test_validator_admission_voter_guardian_auto_cleans_satisfied_stale_votes_after_quiet_window() -> None:
    c1 = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
    a1 = "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"
    c2 = "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876"
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [_validator_vote_address(c2, "candidate validator"), True],
    }

    script = _voter_guardian_script(
        voter="mainnetc-super1",
        candidate=c2,
        candidate_enode="enode://" + "b" * 128 + "@10.116.0.2:30304",
        current_validators=[c1, a1],
        desired_validators=[c2, c1, a1],
        chain_id=42424240,
        genesis_sha256="a" * 64,
        request_sha256=hashlib.sha256(canonical_json(request)).hexdigest(),
    )

    assert _QBFT_STALE_VOTE_QUIET_SECONDS == 35
    assert "STALE_VOTE_QUIET_SECONDS = 35" in script
    assert "qbft_getPendingVotes" in script
    assert "qbft_discardValidatorVote" in script
    assert "add_vote_already_in_validator_set" in script
    assert "remove_vote_already_absent_from_validator_set" in script
    assert "cleanup_satisfied_pending_votes(current, 'before-candidate-vote')" in script
    assert "cleanup_satisfied_pending_votes(final, 'after-desired-validator-set')" in script
    assert "stale_vote_cleanup" in script
    assert "final_pending_votes" in script
    assert script.index("cleanup_satisfied_pending_votes(current, 'before-candidate-vote')") < script.index("rpc(REQUEST['method'], REQUEST['params'])")
    assert _validator_vote_address(a1, "existing validator") in script



def test_validator_admission_voter_guardian_is_one_shot_and_no_restart() -> None:
    candidate = "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876"
    script = _voter_guardian_script(
        voter="mainnetc-super1",
        candidate=candidate,
        candidate_enode="enode://" + "a" * 128 + "@10.116.0.3:30304",
        current_validators=[
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        desired_validators=[
            candidate,
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        chain_id=42424240,
        genesis_sha256="d" * 64,
        request_sha256="e" * 64,
    )
    assert script.index("clear_health()") < script.index("if hashlib.sha256(encoded(REQUEST))")
    assert script.index("prove()") < script.index("time.sleep(3600)") < script.index("break")
    assert "time.sleep(6)" in script
    assert script.index("except Exception as exc:") < script.index("time.sleep(6)")

    compose, name = _install_voter_guardian(
        "services:\n  mainnetc-super1:\n    image: hyperledger/besu:latest\n",
        voter="mainnetc-super1",
        script=script,
    )
    parsed = yaml.safe_load(compose)
    assert name == "mother-add-node-validator-admission-voter-mainnetc-super1"
    assert parsed["services"][name]["restart"] == "no"


def test_validator_admission_conflict_detector_finds_same_candidate_remove_voter() -> None:
    candidate = "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876"
    compose = f"""
services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-node-remove-voter-mainnetc_super1:
    image: python:3.12-alpine
    labels:
      main_computer.mother.stage: node-remove-do
    command:
      - python
      - -u
      - -c
      - |
        TARGET_VALIDATOR = '{candidate}'
        REQUEST = json.loads('{{"id":1,"jsonrpc":"2.0","method":"qbft_proposeValidatorVote","params":["{candidate}",false]}}')
"""
    conflicts = _find_conflicting_node_remove_voters(compose, candidate_validator=candidate)
    assert conflicts == [
        {
            "helper_service": "mother-node-remove-voter-mainnetc_super1",
            "target_validator": candidate,
            "reason": "opposite-node-remove-voter-for-candidate",
        }
    ]


def test_validator_admission_proven_accepts_explicit_zero_terminal_voter_only() -> None:
    record = {
        "status": "running:healthy",
        "applications": [
            {"name": "mother-add-node-validator-admission-voter-mainnetc-super1", "status": "exited:0"},
        ],
    }
    assert _component_proven(
        record,
        names=["mother-add-node-validator-admission-voter-mainnetc-super1"],
        allow_terminal_completed=True,
    )
    assert not _component_proven(
        record,
        names=["mother-add-node-validator-admission-voter-mainnetc-super1"],
        allow_terminal_completed=False,
    )

    ambiguous = {
        "status": "running:healthy",
        "applications": [
            {"name": "mother-add-node-validator-admission-voter-mainnetc-super1", "status": "exited"},
        ],
    }
    assert not _component_proven(
        ambiguous,
        names=["mother-add-node-validator-admission-voter-mainnetc-super1"],
        allow_terminal_completed=True,
    )


def test_validator_admission_builds_candidate_enode_from_route() -> None:
    enode = _candidate_validator_enode(
        "b" * 128,
        {"advertised_host": "10.116.0.3", "p2p_port": 30304, "p2p_endpoint": "10.116.0.3:30304"},
    )

    assert enode == "enode://" + "b" * 128 + "@10.116.0.3:30304"


def test_validator_activation_guardian_explicitly_peers_bootnode() -> None:
    genesis = b'{"config":{"chainId":42424240}}'
    genesis_b64 = base64.b64encode(genesis).decode("ascii")
    genesis_sha = hashlib.sha256(genesis).hexdigest()
    bootnode = "enode://" + "a" * 128 + "@10.116.0.3:30303"

    compose = _candidate_activation_compose(
        target_node="mainneta-super2",
        genesis_b64=genesis_b64,
        bootnode_enode=bootnode,
        chain_id=42424240,
        genesis_sha256=genesis_sha,
        target_node_id="b" * 128,
        desired_validators=[
            "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        candidate_p2p_port=30304,
    )

    assert bootnode in compose
    assert "admin_addPeer" in compose
    assert "admin_peers" in compose
    assert "already_connected_after_reject" in compose
    assert "bootnode_peer_connect_result" in compose
    assert "expected peer is not connected" in compose
    assert "target is still syncing before admission proof" in compose
    assert "target has no bootnode peers before admission proof" in compose


def test_validator_activation_guardian_clears_stale_health_and_uses_operation_specific_probe() -> None:
    genesis = b'{"config":{"chainId":42424240}}'
    genesis_b64 = base64.b64encode(genesis).decode("ascii")
    genesis_sha = hashlib.sha256(genesis).hexdigest()
    bootnode = "enode://" + "a" * 128 + "@10.116.0.3:30303"

    compose = _candidate_activation_compose(
        target_node="mainneta-super2",
        genesis_b64=genesis_b64,
        bootnode_enode=bootnode,
        chain_id=42424240,
        genesis_sha256=genesis_sha,
        target_node_id="b" * 128,
        desired_validators=[
            "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        candidate_p2p_port=30304,
    )

    assert "def clear_health()" in compose
    assert compose.index("clear_health()") < compose.index("with open('/config/genesis.json'")
    assert "target-add-node-validator-admission-healthy';" not in compose
    assert "target-add-node-validator-admission-" in compose
    assert "        prove()\n        break" not in compose
    assert "    time.sleep(6)" in compose


def test_validator_admission_peer_guard_tolerates_already_connected_add_peer_false() -> None:
    lowercase = "0x72151668fe7a691eab99c4779d406380c1d0cfd0"
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [_validator_vote_address(lowercase, "candidate validator"), True],
    }
    script = _voter_guardian_script(
        voter="mainneta-super1",
        candidate=lowercase,
        candidate_enode="enode://" + "b" * 128 + "@10.116.0.3:30304",
        current_validators=["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
        desired_validators=[lowercase, "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
        chain_id=42424240,
        genesis_sha256="a" * 64,
        request_sha256=hashlib.sha256(canonical_json(request)).hexdigest(),
    )

    assert "peer_connected(enode)" in script
    assert "rpc('admin_peers', [])" in script
    assert "if result is True: return 'add_peer_accepted'" in script
    assert "if peer_connected(enode): return 'already_connected_after_reject'" in script
    assert "already_connected_after_reject_wait" in script
    assert "candidate_peer_connect_result = ensure_peer(CANDIDATE_ENODE, 'candidate')" in script
    assert script.index("candidate_peer_connect_result = ensure_peer") < script.index("rpc(REQUEST['method'], REQUEST['params'])")


def test_validator_admission_parses_bootnode_p2p_endpoint_from_enode() -> None:
    host, port, enode = _parse_bootnode_p2p_endpoint({
        "node": "mainnetc-super1",
        "advertised_host": "10.116.0.2",
        "p2p_port": 30304,
        "enode": "enode://" + "a" * 128 + "@10.116.0.2:30304",
    })

    assert host == "10.116.0.2"
    assert port == 30304
    assert enode.endswith("@10.116.0.2:30304")


def test_validator_admission_rejects_bootnode_p2p_metadata_mismatch() -> None:
    try:
        _parse_bootnode_p2p_endpoint({
            "advertised_host": "10.116.0.2",
            "p2p_port": 30303,
            "enode": "enode://" + "a" * 128 + "@10.116.0.2:30304",
        })
    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        assert exc.code == "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID"
        assert "disagrees" in str(exc)
    else:
        raise AssertionError("expected bootnode p2p invalid failure")


def test_validator_admission_p2p_reachability_receipt_uses_replica_sync_evidence_not_operator_tcp() -> None:
    receipt = _bootnode_p2p_reachability_receipt(
        {
            "bootnode": {
                "node": "mainnetc-super1",
                "controller_id": "coolify-c",
                "service_uuid": "voter-service",
                "advertised_host": "10.116.0.2",
                "p2p_port": 30303,
                "enode": "enode://" + "a" * 128 + "@10.116.0.2:30303",
            }
        },
        timeout=30.0,
    )

    assert receipt["name"] == "bootnode-p2p-reachability-before-validator-vote"
    assert receipt["method"] == "CANDIDATE_REPLICA_SYNC_EVIDENCE"
    assert receipt["endpoint"] == "10.116.0.2:30303"
    assert receipt["operator_local_tcp_connect_performed"] is False
    assert "timeout_ms" not in receipt
    assert "timeout_seconds" not in receipt
    assert receipt["verified"] is True
    assert receipt["verified_before_candidate_mutation"] is True
    assert receipt["verified_before_validator_vote"] is True
    assert "candidate-side replica-sync evidence" in receipt["reason"]
    assert receipt["bootnode_enode_sha256"] == hashlib.sha256(
        ("enode://" + "a" * 128 + "@10.116.0.2:30303").encode()
    ).hexdigest()
    canonical_json(receipt)


def test_validator_admission_health_requires_exact_guardian_component() -> None:
    record = {
        "name": "mainneta-super1",
        "status": "running:healthy",
        "applications": [
            {
                "name": "mainneta-super1",
                "status": "running:healthy",
            }
        ],
    }

    assert _component_healthy(
        record,
        names=["mother-add-node-validator-admission-voter-mainneta-super1"],
    ) is False


def test_validator_admission_health_accepts_exact_guardian_component() -> None:
    record = {
        "name": "mainneta-super1",
        "status": "running:unhealthy",
        "applications": [
            {
                "name": "mother-add-node-validator-admission-voter-mainneta-super1",
                "status": "running:healthy",
            }
        ],
    }

    assert _component_healthy(
        record,
        names=["mother-add-node-validator-admission-voter-mainneta-super1"],
    ) is True




class _FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return b'{"ok":true}'

    def getcode(self):
        return self.status


class _FakeOpener:
    def __init__(self):
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return _FakeResponse()



def _guardian_observation(node: str, guardian: str, sample: int, *, healthy: bool = True) -> dict[str, object]:
    return {
        "node": node,
        "proof_guardian_name": guardian,
        "proof_guardian_healthy": healthy,
        "observation_phase": "admission-proof-terminal-durable",
        "durable_sample_index": sample,
    }


def _canonical_history_payload(
    desired: list[str] | None = None,
    *,
    first: int = 100,
    second: int = 102,
    latest: int = 103,
) -> dict[str, object]:
    validator_set = desired or [
        "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    ]
    return {
        "canonical_history_proof_contract": _CANONICAL_HISTORY_PROOF_CONTRACT,
        "desired_validator_set": validator_set,
        "final_validator_set": validator_set,
        "first_block_number": first,
        "first_block_hash": "0x" + "1" * 64,
        "first_block_parent_hash": "0x" + "0" * 64,
        "first_block_validator_set": list(reversed(validator_set)),
        "second_block_number": second,
        "second_block_hash": "0x" + "2" * 64,
        "second_block_parent_hash": "0x" + "1" * 64,
        "second_block_validator_set": validator_set,
        "latest_block_number": latest,
        "latest_block_hash": "0x" + "3" * 64,
        "latest_block_parent_hash": "0x" + "2" * 64,
        "latest_validator_set": validator_set,
        "proved_at": "2026-08-17T19:16:57Z",
    }


def _canonical_history_document_fields(
    candidate: str = "mainneta-super2",
    desired: list[str] | None = None,
) -> dict[str, object]:
    body_sha = "a" * 64
    proof_payload = _canonical_history_payload(desired)
    proof_sha = _canonical_history_proof_payload_sha256(proof_payload)
    return {
        "canonical_validator_history_proof": {
            "contract": _CANONICAL_HISTORY_PROOF_CONTRACT,
            "target_guardian_name": "mother-add-node-validator-activation-guardian",
            "activation_compose_body_sha256": body_sha,
            "exact_block_history_required_before_health": True,
            "proof_payload_required": True,
            "proof_payload_observed": True,
            _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD: proof_sha,
            "required_guardian_proof_fields": list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS),
            "missing_guardian_proof_fields": [],
        },
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD: proof_payload,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD: proof_sha,
        "mutation_receipts": [
            {
                "node": candidate,
                "guardian_service": "mother-add-node-validator-activation-guardian",
                "body_sha256": body_sha,
                "status": "succeeded",
                "live_write_acknowledged": True,
            }
        ],
    }


def test_validator_admission_durable_proof_requires_materialized_final_validator_set() -> None:
    desired = [
        "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    ]
    observations = []
    for sample in (1, 2, 3):
        observations.append(_guardian_observation("mainneta-super2", "mother-add-node-validator-activation-guardian", sample))
        observations.append(_guardian_observation("mainneta-super1", "mother-add-node-validator-admission-voter-mainneta-super1", sample))

    assert _durable_validator_admission_proof_verified({
        "candidate_node": "mainneta-super2",
        "voter_nodes": ["mainneta-super1"],
        "desired_validator_set": desired,
        "final_validator_set": list(reversed(desired)),
        "health_observations": observations,
        **_canonical_history_document_fields("mainneta-super2"),
    }) is True

    assert _durable_validator_admission_proof_verified({
        "candidate_node": "mainneta-super2",
        "voter_nodes": ["mainneta-super1"],
        "desired_validator_set": desired,
        "final_validator_set": None,
        "health_observations": observations,
    }) is False


def test_validator_admission_durable_proof_rejects_transient_or_later_unhealthy_guardian() -> None:
    desired = [
        "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    ]

    one_sample = [
        _guardian_observation("mainneta-super2", "mother-add-node-validator-activation-guardian", 1),
        _guardian_observation("mainneta-super1", "mother-add-node-validator-admission-voter-mainneta-super1", 1),
    ]
    assert _durable_validator_admission_proof_verified({
        "candidate_node": "mainneta-super2",
        "voter_nodes": ["mainneta-super1"],
        "desired_validator_set": desired,
        "final_validator_set": desired,
        "health_observations": one_sample,
    }) is False

    observations = []
    for sample in (1, 2, 3):
        observations.append(_guardian_observation("mainneta-super2", "mother-add-node-validator-activation-guardian", sample))
        observations.append(_guardian_observation("mainneta-super1", "mother-add-node-validator-admission-voter-mainneta-super1", sample))
    observations.append(_guardian_observation("mainneta-super2", "mother-add-node-validator-activation-guardian", 4, healthy=False))
    observations.append(_guardian_observation("mainneta-super1", "mother-add-node-validator-admission-voter-mainneta-super1", 4))

    assert _durable_validator_admission_proof_verified({
        "candidate_node": "mainneta-super2",
        "voter_nodes": ["mainneta-super1"],
        "desired_validator_set": desired,
        "final_validator_set": desired,
        "health_observations": observations,
    }) is False


def test_validator_admission_execute_requires_terminal_durable_proof_before_clean() -> None:
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)

    assert "admission-proof-terminal-durable" in executor_source
    assert "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_DURABLE_PROOF_LOST" in executor_source
    assert "nodes=[candidate_node]" in executor_source
    assert '"final_validator_set": final_validator_set' in executor_source
    assert "_durable_validator_admission_proof_verified" in executor_source
    assert "admission_proven = True" in executor_source
    assert executor_source.index("admission-proof-terminal-durable") < executor_source.index("admission_proven = True")


def test_validator_admission_verify_rejects_legacy_summary_flags_without_durable_final_set() -> None:
    verifier_source = inspect.getsource(_durable_validator_admission_proof_verified)
    verify_source = inspect.getsource(__import__(
        "tools.mother.common.deployment_node_add_validator_admission",
        fromlist=["verify_node_add_validator_admission_evidence"],
    ).verify_node_add_validator_admission_evidence)

    assert "not isinstance(desired, list) or not isinstance(final, list)" in verifier_source
    assert "_durable_validator_admission_proof_verified(document)" in verify_source
    assert 'list(document["final_validator_set"])' in verify_source


def test_validator_admission_verify_allows_public_candidate_activation_proof_endpoint() -> None:
    document = {
        "candidate_activation_proof_endpoint": {
            "kind": "mother-add-node-validator-admission-public-proof-endpoint.v1",
            "transport": "http-public-controller",
            "public_http_endpoint_created": True,
            "url": "http://198.199.75.153:39303/proof",
        },
        "routing_or_topology_published": False,
    }
    summary = {
        "public_endpoint_created": True,
        "public_candidate_activation_proof_endpoint_created": True,
        "routing_or_topology_published": False,
    }
    policy = {
        "public_http_endpoint_created": True,
        "public_candidate_activation_proof_endpoint_created": True,
        "routing_or_topology_published": False,
    }

    assert _validator_admission_public_endpoint_policy_ok(document, summary, policy)


def test_validator_admission_verify_rejects_unscoped_public_endpoint() -> None:
    document = {"routing_or_topology_published": False}
    summary = {"public_endpoint_created": True, "routing_or_topology_published": False}
    policy = {"public_http_endpoint_created": True, "routing_or_topology_published": False}

    assert not _validator_admission_public_endpoint_policy_ok(document, summary, policy)


def test_validator_admission_requires_canonical_history_contract_for_durable_proof() -> None:
    desired = [
        "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    ]
    observations = [
        _guardian_observation("mainneta-super2", "mother-add-node-validator-activation-guardian", sample)
        for sample in (1, 2, 3)
    ]

    legacy_document = {
        "candidate_node": "mainneta-super2",
        "voter_nodes": [],
        "desired_validator_set": desired,
        "final_validator_set": desired,
        "health_observations": observations,
    }
    assert _durable_validator_admission_proof_verified(legacy_document) is False

    marker_only_document = {
        **legacy_document,
        "canonical_validator_history_proof": {
            "contract": _CANONICAL_HISTORY_PROOF_CONTRACT,
            "target_guardian_name": "mother-add-node-validator-activation-guardian",
            "activation_compose_body_sha256": "a" * 64,
            "exact_block_history_required_before_health": True,
            "proof_payload_required": True,
            "required_guardian_proof_fields": list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS),
        },
        "mutation_receipts": [
            {
                "node": "mainneta-super2",
                "guardian_service": "mother-add-node-validator-activation-guardian",
                "body_sha256": "a" * 64,
                "status": "succeeded",
                "live_write_acknowledged": True,
            }
        ],
    }
    assert _canonical_validator_history_proof_verified(marker_only_document) is False
    assert _durable_validator_admission_proof_verified(marker_only_document) is False

    bound_document = {
        **legacy_document,
        **_canonical_history_document_fields("mainneta-super2", desired),
    }
    assert _canonical_validator_history_proof_verified(bound_document) is True
    assert _durable_validator_admission_proof_verified(bound_document) is True


def test_validator_admission_guardian_scripts_bind_health_to_canonical_block_history() -> None:
    activation_source = _candidate_activation_compose(
        target_node="mainneta-super2",
        genesis_b64=base64.b64encode(b"{}").decode("ascii"),
        bootnode_enode="enode://" + "a" * 128 + "@10.0.0.1:30303",
        chain_id=42424240,
        genesis_sha256=hashlib.sha256(b"{}").hexdigest(),
        target_node_id="b" * 128,
        desired_validators=[
            "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        candidate_p2p_port=30303,
    )

    assert _CANONICAL_HISTORY_PROOF_CONTRACT in activation_source
    assert "canonical_history_entry(first)" in activation_source
    assert "canonical_history_entry(second)" in activation_source
    assert "canonical block validator set mismatch" in activation_source
    for field in _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS:
        assert field in activation_source


def test_validator_admission_http_accepts_coolify_controller_objects() -> None:
    opener = _FakeOpener()
    controller = SimpleNamespace(base_url="https://coolify.example", api_token="secret-token")

    result = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=3.0,
        max_response_bytes=1000,
        opener=opener,
    )

    assert result["status"] == 200
    assert result["ok"] is True
    assert opener.request.full_url == "https://coolify.example/api/v1/services"
    assert opener.request.get_header("Authorization") == "Bearer secret-token"
    assert opener.timeout == 3.0


def test_validator_admission_starts_services_with_lifecycle_endpoint() -> None:
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)
    release_source = inspect.getsource(build_node_add_validator_admission_release)

    assert "/api/v1/deploy" not in executor_source
    assert '"/api/v1/services/{urllib.parse.quote(target_uuid, safe=\'\')}/start"' in executor_source
    assert '"/api/v1/services/{urllib.parse.quote(uuid, safe=\'\')}/start"' in executor_source
    assert '_http(target_controller, "POST", start_endpoint' in executor_source
    assert '_http(controller, "POST", start_endpoint' in executor_source
    assert '"allowed_http_methods": ["GET", "PATCH", "POST"]' in release_source


def test_validator_admission_tolerates_existing_voter_start_400_until_guardian_proof() -> None:
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)

    assert "start_rejected_nonfatal = start[\"status\"] == 400" in executor_source
    assert "start_accepted = start_ok or start_rejected_nonfatal" in executor_source
    assert "\"coolify_start_rejected_nonfatal\"" in executor_source
    assert "exact voter guardian health proof remains required" in executor_source

    start_index = executor_source.index("coolify_start_rejected_nonfatal")
    proof_index = executor_source.index("_wait_for_admission_proof_guardians")
    assert start_index < proof_index


def test_validator_admission_recovers_target_start_400_when_activation_service_is_already_running() -> None:
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)
    recovery_source = inspect.getsource(_recover_target_start_rejection)

    assert "target_start_rejected_nonfatal = target_start_recovery.get(\"verified\") is True" in executor_source
    assert "\"coolify_target_start_rejected_nonfatal\"" in executor_source
    assert "_recover_target_start_rejection" in executor_source
    assert "exact target activation guardian proof remains required" in executor_source
    assert "mother-validator-activation-init" in recovery_source
    assert "main_computer.mother.validator-activation: active" in recovery_source
    assert "service_started" in recovery_source

    start_index = executor_source.index("coolify_target_start_rejected_nonfatal")
    proof_index = executor_source.index("_wait_for_admission_proof_guardians")
    assert start_index < proof_index


def test_validator_admission_does_not_post_start_after_successful_admission_proof() -> None:
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)

    assert "post-admission-validator-refresh-proof" not in executor_source
    assert "post-admission-qbft-transition-reverify" not in executor_source
    assert "restart-validator-after-admission-proof" not in executor_source
    assert "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_POST_ADMISSION_REFRESH" not in executor_source

    proof_index = executor_source.index("admission-proof-before-validator-refresh")
    cleanup_index = executor_source.index("execute_completed_mother_helper_cleanup")
    assert proof_index < cleanup_index
    assert "post_admission_cleanup_warning" in executor_source
    assert "POST_ADMISSION_HEALTH_UNCLEAN_NONFATAL" in executor_source


def test_validator_admission_restarts_and_reobserves_qbft_transition_before_failure() -> None:
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)

    assert "/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/restart" in inspect.getsource(
        _restart_validator_services_for_qbft_transition
    )
    assert "admission-proof-after-qbft-transition-restart" in executor_source
    assert "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_QBFT_TRANSITION_RESTART_FAILED" in executor_source
    assert "max(max_wait_seconds, _ADMISSION_PROOF_TRANSITION_RECOVERY_MAX_WAIT_SECONDS)" in executor_source


def test_validator_admission_reports_proof_timeout_after_patient_recovery_wait() -> None:
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)

    assert "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_PROOF_TIMEOUT" in executor_source
    assert "activation guardian did not reach durable proof before the proof wait deadline" in executor_source
    assert '"transient_voter_nodes": voter_nodes' in executor_source


def test_validator_admission_wait_emits_operator_progress_events() -> None:
    wait_source = inspect.getsource(_wait_for_admission_proof_guardians)
    executor_source = inspect.getsource(execute_node_add_validator_admission_release)

    assert "progress_callback" in wait_source
    assert "_ADMISSION_PROGRESS_EMIT_INTERVAL_SECONDS" in wait_source
    assert '"admission_proof_poll"' in wait_source
    assert '"admission_proof_satisfied"' in wait_source
    assert '"admission_proof_timeout"' in wait_source
    assert '"last_statuses"' in wait_source
    assert "progress_callback=progress_callback" in executor_source


class _AdmissionProofWaitOpener:
    def __init__(self):
        self.calls: list[str] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        if url.endswith("/api/v1/services/candidate-service"):
            payload = {
                "uuid": "candidate-service",
                "name": "mainneta-super1",
                "status": "running:healthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-activation-guardian",
                        "status": "running:healthy",
                        "proof": _canonical_history_payload([
                            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                        ]),
                    }
                ],
            }
        elif url.endswith("/api/v1/services/voter-service"):
            payload = {
                "uuid": "voter-service",
                "name": "mainnetc-super1",
                "status": "running:unhealthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-admission-voter-mainnetc-super1",
                        "status": "running:unhealthy:excluded",
                    }
                ],
            }
        else:
            payload = {"message": "not found"}
            return _StatusResponse(canonical_json(payload), status=404)
        return _StatusResponse(canonical_json(payload), status=200)




def test_candidate_activation_proof_endpoint_is_public_controller_bound() -> None:
    endpoint = _candidate_activation_proof_endpoint(
        {"advertised_host": "10.116.0.3", "p2p_endpoint": "10.116.0.3:30304"},
        candidate_p2p_port=30304,
        public_host="198.199.75.153",
    )

    assert endpoint["transport"] == "http-public-controller"
    assert endpoint["host"] == "198.199.75.153"
    assert endpoint["bind_host"] == "0.0.0.0"
    assert endpoint["host_port"] == 39304
    assert endpoint["container_port"] == 8797
    assert endpoint["url"] == "http://198.199.75.153:39304/proof"
    assert endpoint["public_http_endpoint_created"] is True


class _AdmissionProofEndpointOpener:
    def __init__(self):
        self.calls: list[str] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        if url.endswith("/api/v1/services/candidate-service"):
            payload = {
                "uuid": "candidate-service",
                "name": "mainneta-super1",
                "status": "running:healthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-activation-guardian",
                        "status": "running:healthy",
                    }
                ],
            }
        elif url == "http://198.199.75.153:39304/proof":
            payload = _canonical_history_payload([
                "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
            ])
        elif url.endswith("/api/v1/services/voter-service"):
            payload = {
                "uuid": "voter-service",
                "name": "mainnetc-super1",
                "status": "running:unhealthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-admission-voter-mainnetc-super1",
                        "status": "running:unhealthy:excluded",
                    }
                ],
            }
        else:
            payload = {"message": "not found"}
            return _StatusResponse(canonical_json(payload), status=404)
        return _StatusResponse(canonical_json(payload), status=200)


def test_validator_admission_wait_captures_mother_side_public_proof_endpoint() -> None:
    opener = _AdmissionProofEndpointOpener()
    observations: list[dict] = []
    controllers = {
        "coolify-a": SimpleNamespace(base_url="https://coolify-a.example", api_token="secret-token"),
        "coolify-c": SimpleNamespace(base_url="https://coolify-c.example", api_token="secret-token"),
    }

    healthy, last_statuses = _wait_for_admission_proof_guardians(
        nodes=["mainneta-super1", "mainnetc-super1"],
        candidate_node="mainneta-super1",
        desired_validator_set=[
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        controllers=controllers,
        node_to_controller={"mainneta-super1": "coolify-a", "mainnetc-super1": "coolify-c"},
        all_service_uuids={"mainneta-super1": "candidate-service", "mainnetc-super1": "voter-service"},
        voter_guardian_names={
            "mainnetc-super1": "mother-add-node-validator-admission-voter-mainnetc-super1",
        },
        target_guardian_name="mother-add-node-validator-activation-guardian",
        candidate_activation_proof_endpoint={
            "transport": "http-public-controller",
            "url": "http://198.199.75.153:39304/proof",
        },
        observations=observations,
        observation_phase="admission-proof-after-qbft-transition-restart",
        max_wait_seconds=5.0,
        poll_interval_seconds=0.0,
        timeout=3.0,
        max_response_bytes=10000,
        opener=opener,
        durable_sample_count=2,
    )

    assert healthy == {"mainneta-super1"}
    assert "canonical-proof-payload=observed" in last_statuses["mainneta-super1"]
    assert "proof-transport=mother-public-proof-endpoint" in last_statuses["mainneta-super1"]
    assert opener.calls.count("http://198.199.75.153:39304/proof") >= 2
    candidate_observations = [item for item in observations if item["node"] == "mainneta-super1"]
    assert candidate_observations[-1]["guardian_proof_payload_verified"] is True
    assert candidate_observations[-1]["guardian_proof_payload_transport"] == "mother-public-proof-endpoint"
    assert candidate_observations[-1]["candidate_activation_canonical_history_proof"]["latest_validator_set"] == [
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    ]


def test_validator_admission_wait_is_candidate_activation_driven_after_voter_exclusion() -> None:
    opener = _AdmissionProofWaitOpener()
    observations: list[dict] = []
    controllers = {
        "coolify-a": SimpleNamespace(base_url="https://coolify-a.example", api_token="secret-token"),
        "coolify-c": SimpleNamespace(base_url="https://coolify-c.example", api_token="secret-token"),
    }

    healthy, last_statuses = _wait_for_admission_proof_guardians(
        nodes=["mainneta-super1", "mainnetc-super1"],
        candidate_node="mainneta-super1",
        desired_validator_set=[
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        controllers=controllers,
        node_to_controller={"mainneta-super1": "coolify-a", "mainnetc-super1": "coolify-c"},
        all_service_uuids={"mainneta-super1": "candidate-service", "mainnetc-super1": "voter-service"},
        voter_guardian_names={
            "mainnetc-super1": "mother-add-node-validator-admission-voter-mainnetc-super1",
        },
        target_guardian_name="mother-add-node-validator-activation-guardian",
        candidate_activation_proof_endpoint=None,
        observations=observations,
        observation_phase="admission-proof-after-qbft-transition-restart",
        max_wait_seconds=5.0,
        poll_interval_seconds=0.0,
        timeout=3.0,
        max_response_bytes=10000,
        opener=opener,
        durable_sample_count=2,
    )

    assert healthy == {"mainneta-super1"}
    assert "running:unhealthy:excluded" in last_statuses["mainnetc-super1"]
    assert len([item for item in observations if item["node"] == "mainneta-super1"]) >= 2
    voter_observations = [item for item in observations if item["node"] == "mainnetc-super1"]
    assert voter_observations
    assert all(item["guardian_role"] == "transient_vote_helper" for item in voter_observations)
    assert all(item["durable_proof_required"] is False for item in voter_observations)
    assert all(item["nonblocking_after_activation"] is True for item in voter_observations)


def test_durable_validator_admission_proof_accepts_transient_voter_exclusion_after_activation() -> None:
    desired = [
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    ]
    observations = []
    for sample in range(1, 4):
        observations.extend([
            {
                "node": "mainneta-super1",
                "proof_guardian_name": "mother-add-node-validator-activation-guardian",
                "proof_guardian_status": "running:healthy",
                "proof_guardian_healthy": True,
                "guardian_role": "candidate_activation",
                "durable_proof_required": True,
                "observation_phase": "admission-proof-after-qbft-transition-restart",
                "durable_sample_index": sample,
            },
            {
                "node": "mainnetc-super1",
                "proof_guardian_name": "mother-add-node-validator-admission-voter-mainnetc-super1",
                "proof_guardian_status": "running:unhealthy:excluded",
                "proof_guardian_healthy": False,
                "guardian_role": "transient_vote_helper",
                "durable_proof_required": False,
                "observation_phase": "admission-proof-after-qbft-transition-restart",
                "durable_sample_index": sample,
            },
        ])

    assert _durable_validator_admission_proof_verified({
        "candidate_node": "mainneta-super1",
        "voter_nodes": ["mainnetc-super1"],
        "desired_validator_set": desired,
        "final_validator_set": list(reversed(desired)),
        "health_observations": observations,
        **_canonical_history_document_fields("mainneta-super1", desired),
    })


def test_validator_admission_live_proof_adoption_is_read_only_and_writes_new_evidence() -> None:
    adoption_source = inspect.getsource(adopt_node_add_validator_admission_live_proof)

    assert '"adopt-add-node-validator-admission-live-proof"' not in adoption_source
    assert '"read_only_live_proof_adoption": True' in adoption_source
    assert '"allowed_http_methods": ["GET"]' in adoption_source
    assert '"mutation_receipts": []' in adoption_source
    assert '"source_failed_validator_admission_evidence"' in adoption_source
    assert "_wait_for_admission_proof_guardians" in adoption_source
    assert "_write_evidence(paths, evidence, operation=operation)" in adoption_source
    assert "PATCH" not in adoption_source
    assert "POST" not in adoption_source
    assert "_restart_validator_services_for_qbft_transition" not in adoption_source


def test_validator_admission_prefers_service_uuid_hints_from_replica_sync_evidence() -> None:
    evidence = {
        "current_topology": {
            "services": {
                "mainnetc-super1": {"node": "mainnetc-super1", "service_uuid": "fbf9umvwxzooevlkve56acfm"},
                "mainnetc-super2": {"node": "mainnetc-super2", "service_uuid": "uwo6htc6zdraxk5hfm1cbxiv"},
            }
        },
        "standby_topology": {
            "standby_node": "mainneta-super1",
            "standby_service_uuid": "fv17odr6ha1l9vsksdr5d69j",
        },
        "proof_plan_summary": {
            "bootnode": {
                "node": "mainnetc-super1",
                "service_uuid": "fbf9umvwxzooevlkve56acfm",
            }
        },
    }

    hints = _service_uuid_hints_from_replica_sync_evidence(evidence)

    assert hints == {
        "mainneta-super1": "fv17odr6ha1l9vsksdr5d69j",
        "mainnetc-super1": "fbf9umvwxzooevlkve56acfm",
        "mainnetc-super2": "uwo6htc6zdraxk5hfm1cbxiv",
    }


class _StatusResponse:
    def __init__(self, payload: bytes, *, status: int = 200):
        self._payload = payload
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self._payload

    def getcode(self):
        return self.status


class _RecordingOpener:
    def __init__(self):
        self.methods: list[str] = []

    def open(self, request, timeout=None):
        self.methods.append(request.get_method())
        return _StatusResponse(b'{"message":"not found"}', status=404)


def test_validator_admission_preflights_selected_voters_before_candidate_mutation() -> None:
    opener = _RecordingOpener()
    controllers = {"coolify-c": SimpleNamespace(base_url="https://coolify-c.example", api_token="secret-token")}
    request_by_voter = {
        "mainnetc-super1": {
            "controller_id": "coolify-c",
            "rpc_request_sha256": "a" * 64,
            "voter_node": "mainnetc-super1",
        }
    }

    try:
        _preflight_existing_validator_services(
            voter_nodes=["mainnetc-super1"],
            request_by_voter=request_by_voter,
            service_uuid_hints={"mainnetc-super1": "missing-service"},
            controllers=controllers,
            timeout=3.0,
            max_response_bytes=1000,
            opener=opener,
        )
    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        assert exc.code == "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STALE_BASELINE"
        assert "before candidate mutation" in str(exc)
    else:
        raise AssertionError("expected stale-baseline failure")

    assert opener.methods == ["GET"]


class _ServiceDetailWithoutComposeOpener:
    def __init__(self):
        self.methods: list[str] = []

    def open(self, request, timeout=None):
        self.methods.append(request.get_method())
        payload = canonical_json({
            "uuid": "voter-service",
            "name": "mainneta-super1",
            "status": "running:healthy",
        })
        return _StatusResponse(payload, status=200)


def test_validator_admission_requires_voter_compose_before_candidate_mutation() -> None:
    opener = _ServiceDetailWithoutComposeOpener()
    controllers = {"coolify-a": SimpleNamespace(base_url="https://coolify-a.example", api_token="secret-token")}
    request_by_voter = {
        "mainneta-super1": {
            "controller_id": "coolify-a",
            "rpc_request_sha256": "a" * 64,
            "voter_node": "mainneta-super1",
        }
    }

    try:
        _preflight_existing_validator_services(
            voter_nodes=["mainneta-super1"],
            request_by_voter=request_by_voter,
            service_uuid_hints={"mainneta-super1": "voter-service"},
            controllers=controllers,
            timeout=3.0,
            max_response_bytes=1000,
            opener=opener,
        )
    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        assert exc.code == "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_MISSING"
        assert "Compose text" in str(exc)
    else:
        raise AssertionError("expected missing-compose failure")

    assert opener.methods == ["GET"]


class _ServiceDetailWithTopLevelComposeAndApplicationsOpener:
    def __init__(self):
        self.methods: list[str] = []

    def open(self, request, timeout=None):
        self.methods.append(request.get_method())
        payload = canonical_json({
            "uuid": "voter-service",
            "name": "mainneta-super1",
            "status": "running:healthy",
            "docker_compose_raw": "name: mainneta-super1\nservices:\n  mainneta-super1:\n    image: besu\n",
            "docker_compose": "name: mainneta-super1\nservices:\n  mainneta-super1:\n    image: besu\n",
            "applications": [
                {
                    "uuid": "child-application",
                    "name": "mainneta-super1",
                    "status": "running:healthy",
                }
            ],
        })
        return _StatusResponse(payload, status=200)


def test_validator_admission_prefers_top_level_service_detail_compose_over_applications() -> None:
    opener = _ServiceDetailWithTopLevelComposeAndApplicationsOpener()
    controllers = {"coolify-a": SimpleNamespace(base_url="https://coolify-a.example", api_token="secret-token")}
    request_by_voter = {
        "mainneta-super1": {
            "controller_id": "coolify-a",
            "rpc_request_sha256": "a" * 64,
            "voter_node": "mainneta-super1",
        }
    }

    preconditions, service_uuids, detail_records, compose_texts = _preflight_existing_validator_services(
        voter_nodes=["mainneta-super1"],
        request_by_voter=request_by_voter,
        service_uuid_hints={"mainneta-super1": "voter-service"},
        controllers=controllers,
        timeout=3.0,
        max_response_bytes=1000,
        opener=opener,
    )

    assert opener.methods == ["GET"]
    assert service_uuids == {"mainneta-super1": "voter-service"}
    assert detail_records["mainneta-super1"]["uuid"] == "voter-service"
    assert compose_texts["mainneta-super1"].startswith("name: mainneta-super1\nservices:")
    assert preconditions[0]["compose_text_available"] is True
    assert preconditions[0]["service_status"] == "running:healthy"


def test_identity_after_install_routes_empty_current_topology_to_single_node_bootstrap() -> None:
    from tools.mother.common.deployment_node_add_identity import _identity_after_install_routing

    route = _identity_after_install_routing({
        "network": "mainnet",
        "current_topology": {"validator_count": 0, "validator_set": []},
        "prepared_post_add_topology": {
            "validator_count": 1,
            "validator_set": ["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
        },
    })

    assert route["bootstrap_mode"] == "operator-directed-single-node"
    assert route["next_phase"] == "add-node-single-node-bootstrap-mainnet"
    assert route["single_node_bootstrap_required"] is True
    assert route["replica_sync_required"] is False
    assert route["validator_admission_required"] is False
    assert "sync-replica" not in route["remaining_phases"]
    assert "admit-validator" not in route["remaining_phases"]


def test_identity_after_install_routes_existing_validators_to_replica_sync() -> None:
    from tools.mother.common.deployment_node_add_identity import _identity_after_install_routing

    route = _identity_after_install_routing({
        "network": "mainnet",
        "current_topology": {
            "validator_count": 2,
            "validator_set": [
                "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
            ],
        },
        "prepared_post_add_topology": {
            "validator_count": 3,
            "validator_set": [
                "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
            ],
        },
    })

    assert route["bootstrap_mode"] == "join-existing-validator-set"
    assert route["next_phase"] == "add-node-replica-sync-mainnet"
    assert route["single_node_bootstrap_required"] is False
    assert route["replica_sync_required"] is True
    assert route["validator_admission_required"] is True
