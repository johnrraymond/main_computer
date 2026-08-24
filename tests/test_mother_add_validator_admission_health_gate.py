from __future__ import annotations

from types import SimpleNamespace

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_validator_admission import (
    MotherDeploymentNodeAddValidatorAdmissionError,
    _preflight_existing_validator_services,
)


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


class _UnhealthyServiceDetailOpener:
    def __init__(self):
        self.methods: list[str] = []

    def open(self, request, timeout=None):
        self.methods.append(request.get_method())
        payload = canonical_json({
            "uuid": "voter-service",
            "name": "mainnetc-super1",
            "status": "running:unhealthy",
            "docker_compose_raw": "name: mainnetc-super1\nservices:\n  mainnetc-super1:\n    image: besu\n",
        })
        return _StatusResponse(payload, status=200)


def test_validator_admission_rejects_unhealthy_existing_voter_before_candidate_mutation() -> None:
    opener = _UnhealthyServiceDetailOpener()
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
            service_uuid_hints={"mainnetc-super1": "voter-service"},
            controllers=controllers,
            timeout=3.0,
            max_response_bytes=1000,
            opener=opener,
        )
    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        assert exc.code == "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_REQUIRED_VALIDATOR_UNHEALTHY"
        assert "running:unhealthy" in str(exc)
        assert "before candidate mutation" in str(exc)
    else:
        raise AssertionError("expected unhealthy existing-validator failure")

    assert opener.methods == ["GET"]
