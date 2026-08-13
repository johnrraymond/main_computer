from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import yaml

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_validator_admission import (
    MotherDeploymentNodeAddValidatorAdmissionError,
    _candidate_activation_compose,
    _digest_without,
    _http,
    _preflight_existing_validator_services,
    _service_uuid_hints_from_replica_sync_evidence,
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
    )

    parsed = yaml.safe_load(compose)
    assert set(parsed["services"]) == {
        "mother-validator-activation-init",
        "mainneta-super1",
        "mother-add-node-validator-activation-guardian",
    }
    assert "ports" not in parsed["services"]["mainneta-super1"]
    assert "ports" not in parsed["services"]["mother-add-node-validator-activation-guardian"]
    assert 'MC_MOTHER_VALIDATOR_PRIVATE_KEY: "${MC_MOTHER_VALIDATOR_PRIVATE_KEY}"' in compose
    assert "0x" + "1" * 64 not in compose
    assert "main_computer.mother.validator-activation: active" in compose


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
