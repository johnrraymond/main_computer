from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_node_remove import (
    MotherDeploymentNodeRemoveError,
    acknowledgement_for,
    execute_node_removal,
    inspect_node_removal,
)
from tests.test_mother_deployment_executor import (
    TOKEN_A,
    TOKEN_C,
    _Response,
    _install,
    _operation,
)


A_NODE = "mainneta-super1"
A_UUID = "pc20bsxvq3ykjnpzque08l63"
C_NODE = "mainnetc-super1"
C_UUID = "t125pkvf4z1v3ipzwtgn2t38"


class _NodeRemoveOpener:
    def __init__(
        self,
        *,
        host: str,
        node: str,
        service_uuid: str,
        present: bool = True,
        observed_name: str | None = None,
        delete_status: int = 200,
    ) -> None:
        self.host = host
        self.node = node
        self.service_uuid = service_uuid
        self.present = present
        self.observed_name = observed_name or node
        self.delete_status = delete_status
        self.requests: list[tuple[str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        assert parsed.hostname == self.host
        expected_token = TOKEN_A if self.host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        assert timeout > 0
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        expected_path = f"/api/v1/services/{self.service_uuid}"
        assert path == expected_path

        if method == "GET":
            if not self.present:
                return _Response({"message": "not found"}, status=404)
            return _Response(
                {
                    "uuid": self.service_uuid,
                    "name": self.observed_name,
                    "status": "running:healthy",
                }
            )

        if method == "DELETE":
            if self.delete_status not in {200, 202, 204}:
                return _Response({"message": "delete failed"}, status=self.delete_status)
            self.present = False
            return _Response({"message": "deleted"}, status=self.delete_status)

        raise AssertionError(f"unexpected request: {method} {path}")


class _FailIfOpened:
    def open(self, request, timeout: float):  # noqa: ANN001
        raise AssertionError("network access was not expected")


def test_inspection_is_offline_and_returns_exact_acknowledgement(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    result = inspect_node_removal(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        node=C_NODE,
        service_uuid=C_UUID,
        operation=_operation("inspect-node-remove"),
    )
    assert result["status"] == "inspection"
    assert result["required_acknowledgement"] == f"REMOVE:{C_NODE}:{C_UUID}"
    assert result["authority"]["network_access_authorized"] is False
    assert result["authority"]["live_mutation_authorized"] is False
    assert result["authority"]["requested_mutation_count"] == 1
    assert result["planned_mutation"] == {
        "method": "DELETE",
        "endpoint": f"/api/v1/services/{C_UUID}",
        "expected_service_name": C_NODE,
    }


def test_execute_removes_only_the_acknowledged_service(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    opener = _NodeRemoveOpener(
        host="coolify-c.invalid",
        node=C_NODE,
        service_uuid=C_UUID,
    )
    result = execute_node_removal(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        node=C_NODE,
        service_uuid=C_UUID,
        acknowledged_node_removal=acknowledgement_for(C_NODE, C_UUID),
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("execute-node-remove"),
        opener=opener,
    )
    endpoint = f"/api/v1/services/{C_UUID}"
    assert opener.requests == [
        ("GET", endpoint),
        ("DELETE", endpoint),
        ("GET", endpoint),
    ]
    assert result["status"] == "pass"
    assert result["clean"] is True
    assert result["already_absent"] is False
    assert result["live_mutation_performed"] is True
    assert result["mutation_count"] == 1
    assert result["node"] == C_NODE
    assert result["service_uuid"] == C_UUID
    rendered = json.dumps(result)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered


def test_acknowledgement_mismatch_blocks_before_network_access(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    with pytest.raises(
        MotherDeploymentNodeRemoveError,
        match="acknowledge-node-removal",
    ) as raised:
        execute_node_removal(
            private_state,
            network="mainnet",
            controller_id="coolify-a",
            node=A_NODE,
            service_uuid=A_UUID,
            acknowledged_node_removal="REMOVE:wrong:uuid",
            operation=_operation("wrong-ack"),
            opener=_FailIfOpened(),
        )
    assert raised.value.code == "MOTHER_DEPLOY_NODE_REMOVE_ACKNOWLEDGEMENT_REQUIRED"


def test_service_name_mismatch_refuses_delete(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    opener = _NodeRemoveOpener(
        host="coolify-a.invalid",
        node=A_NODE,
        service_uuid=A_UUID,
        observed_name="some-other-service",
    )
    with pytest.raises(
        MotherDeploymentNodeRemoveError,
        match="does not belong",
    ) as raised:
        execute_node_removal(
            private_state,
            network="mainnet",
            controller_id="coolify-a",
            node=A_NODE,
            service_uuid=A_UUID,
            acknowledged_node_removal=acknowledgement_for(A_NODE, A_UUID),
            operation=_operation("service-mismatch"),
            opener=opener,
        )
    assert raised.value.code == "MOTHER_DEPLOY_NODE_REMOVE_SERVICE_MISMATCH"
    assert all(method != "DELETE" for method, _ in opener.requests)


def test_allow_missing_is_idempotent_and_performs_no_mutation(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    opener = _NodeRemoveOpener(
        host="coolify-a.invalid",
        node=A_NODE,
        service_uuid=A_UUID,
        present=False,
    )
    result = execute_node_removal(
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        node=A_NODE,
        service_uuid=A_UUID,
        acknowledged_node_removal=acknowledgement_for(A_NODE, A_UUID),
        allow_missing=True,
        operation=_operation("already-absent"),
        opener=opener,
    )
    assert result["status"] == "pass"
    assert result["already_absent"] is True
    assert result["live_mutation_performed"] is False
    assert opener.requests == [("GET", f"/api/v1/services/{A_UUID}")]


def test_cli_exposes_both_remove_node_spellings() -> None:
    canonical = mother_deploy._parser().parse_args(
        [
            "remove-node",
            "--node",
            C_NODE,
            "--controller-id",
            "coolify-c",
            "--service-uuid",
            C_UUID,
        ]
    )
    alias = mother_deploy._parser().parse_args(
        [
            "node-remove",
            "--node",
            C_NODE,
            "--controller-id",
            "coolify-c",
            "--service-uuid",
            C_UUID,
        ]
    )
    assert canonical.command == "remove-node"
    assert alias.command == "node-remove"
