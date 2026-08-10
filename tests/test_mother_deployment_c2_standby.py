from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_c2_state_extension import (
    build_c2_state_extension_release,
    execute_c2_state_extension_release,
    stage_c2_state_extension,
    write_c2_state_extension_release,
    write_c2_state_extension_transaction,
)
from tools.mother.common.deployment_c2_standby import (
    build_c2_standby_identity_release,
    build_c2_standby_service_release,
    execute_c2_standby_identity_release,
    execute_c2_standby_service_release,
    inspect_c2_standby_identity_release,
    inspect_c2_standby_service_release,
    stage_c2_standby_identity_transaction,
    stage_c2_standby_service_transaction,
    verify_c2_standby_identity,
    verify_c2_standby_identity_release,
    verify_c2_standby_identity_transaction,
    verify_c2_standby_service,
    verify_c2_standby_service_release,
    verify_c2_standby_service_transaction,
    write_c2_standby_identity_release,
    write_c2_standby_service_release,
)
from tools.mother.common.deployment_preflight import (
    run_starter_deployment_preflight,
    write_deployment_preflight_evidence,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.private_state import read_private_state
from tests.test_mother_deployment_c2_state_extension import (
    _canary_file,
    _keys,
    _mature_state,
    _mock_canary,
)
from tests.test_mother_deployment_executor import _CoolifyOpener, TOKEN_C


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


class _C2IdentityOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.envs: list[dict] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": host, "path": path, "body": body})
        assert host == "coolify-c.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_C}"
        assert timeout > 0
        base = "/api/v1/services/svc-mainnetc-super2/envs"
        assert path == base or path.startswith(base + "/")
        if method == "GET":
            return _Response({"envs": list(self.envs)})
        if method == "POST":
            item = {
                "uuid": f"env-{len(self.envs) + 1}",
                "key": body["key"],
                "value": body["value"],
            }
            self.envs.append(item)
            return _Response(item, status=201)
        if method == "DELETE":
            env_uuid = path.rsplit("/", 1)[-1]
            self.envs = [item for item in self.envs if item.get("uuid") != env_uuid]
            return _Response({"message": "deleted"}, status=200)
        raise AssertionError(f"unexpected method: {method}")


def _install_c2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths, mature, _ = _mature_state(tmp_path)
    canary = _canary_file(paths)
    _mock_canary(monkeypatch, canary)
    now = datetime(2026, 8, 10, 12, 0, 5, tzinfo=timezone.utc)
    staged = stage_c2_state_extension(
        paths,
        mature,
        canary,
        created_at="2026-08-10T12:00:00Z",
        now=now,
        operation=_operation("stage-c2"),
        key_factory=_keys(),
    )
    tx_path, tx_sha = write_c2_state_extension_transaction(
        paths,
        staged,
        operation=_operation("write-c2"),
    )
    release = build_c2_state_extension_release(
        paths,
        mature,
        tx_path,
        acknowledge_transaction_sha256=tx_sha,
        created_at="2026-08-10T12:00:01Z",
        now=now,
        operation=_operation("release-c2"),
    )
    release_path, release_sha = write_c2_state_extension_release(
        paths,
        release,
        operation=_operation("write-release-c2"),
    )
    result = execute_c2_state_extension_release(
        paths,
        mature,
        release_path,
        acknowledge_release_sha256=release_sha,
        now=now,
        operation=_operation("apply-c2"),
    )
    assert result["status"] == "pass"
    current = read_private_state(paths, operation=_operation("read-c2"))
    return paths, current, Path(result["evidence"]["path"])


def _preflight(paths, private_state):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    opener = _CoolifyOpener()
    report = run_starter_deployment_preflight(
        private_state,
        selected_nodes=("mainnetc-super2",),
        observed_at=_stamp(now),
        opener=opener,
    )
    assert report["summary"]["clean"] is True
    evidence_path, _ = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("preflight-c2"),
    )
    return evidence_path, now


def _service_release(paths, private_state, c2_evidence, preflight_evidence, now):
    staged = stage_c2_standby_service_transaction(
        paths,
        private_state,
        preflight_evidence,
        c2_evidence,
        created_at=_stamp(now),
        now=now,
        operation=_operation("stage-c2-standby-service"),
    )
    assert staged["nodes"][0]["node"] == "mainnetc-super2"
    assert staged["c2_summary"]["identity_mutation_count"] == 0
    assert staged["c2_standby_scope"]["replica_sync_authorized"] is False
    transaction_path = Path(staged["transaction_artifact"]["path"])
    transaction_sha = staged["transaction_artifact"]["sha256"]

    verified = verify_c2_standby_service_transaction(
        paths,
        private_state,
        transaction_path,
        c2_evidence,
        now=now,
        operation=_operation("verify-c2-service"),
    )
    assert verified["node"] == "mainnetc-super2"
    assert verified["service_mutation_count"] in {1, 2}
    assert verified["validator_vote_performed"] is False

    release = build_c2_standby_service_release(
        paths,
        private_state,
        transaction_path,
        c2_evidence,
        acknowledged_transaction_sha256=transaction_sha,
        created_at=_stamp(now),
        now=now,
        operation=_operation("release-c2-service"),
    )
    release_path, release_sha = write_c2_standby_service_release(
        paths,
        release,
        operation=_operation("write-c2-service-release"),
    )
    verified_release = verify_c2_standby_service_release(
        paths,
        private_state,
        release_path,
        c2_evidence,
        now=now,
        operation=_operation("verify-c2-service-release"),
    )
    assert verified_release["transaction_apply_authorized"] is True
    inspection = inspect_c2_standby_service_release(
        paths,
        private_state,
        release_path,
        c2_evidence,
        acknowledged_release_sha256=release_sha,
        now=now,
        operation=_operation("inspect-c2-service"),
    )
    assert inspection["release_already_claimed"] is False
    assert inspection["live_mutation_performed"] is False
    return release_path, release_sha


def test_c2_standby_service_and_identity_flow_stops_before_replica_or_vote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, private_state, c2_evidence = _install_c2(tmp_path, monkeypatch)
    preflight_evidence, now = _preflight(paths, private_state)
    release_path, release_sha = _service_release(paths, private_state, c2_evidence, preflight_evidence, now)

    live = _CoolifyOpener()
    service_result = execute_c2_standby_service_release(
        paths,
        private_state,
        release_path,
        c2_evidence,
        acknowledged_release_sha256=release_sha,
        now=now,
        opener=live,
        max_response_bytes=4 * 1024 * 1024,
        operation=_operation("apply-c2-service"),
    )
    assert service_result["status"] == "pass"
    assert service_result["summary"]["complete"] is True
    assert service_result["validator_vote_performed"] is False
    assert service_result["chain_mutation_count"] == 0

    standby = verify_c2_standby_service(
        paths,
        private_state,
        Path(service_result["result_artifact"]["path"]),
        opener=live,
        max_response_bytes=4 * 1024 * 1024,
        observed_at=_stamp(now),
        write_evidence=True,
        operation=_operation("verify-c2-standby"),
    )
    assert standby["summary"]["clean"] is True
    standby_evidence = Path(standby["evidence"]["path"])

    identity_tx = stage_c2_standby_identity_transaction(
        paths,
        private_state,
        standby_evidence,
        created_at=_stamp(now),
        now=now,
        operation=_operation("stage-c2-identity"),
    )
    assert identity_tx["c2_summary"]["identity_mutation_count"] == 2
    assert identity_tx["c2_identity_scope"]["replica_sync_authorized"] is False
    identity_tx_path = Path(identity_tx["transaction_artifact"]["path"])
    identity_tx_sha = identity_tx["transaction_artifact"]["sha256"]

    verified_identity_tx = verify_c2_standby_identity_transaction(
        paths,
        private_state,
        identity_tx_path,
        now=now,
    )
    assert verified_identity_tx["identity_mutation_count"] == 2
    identity_release = build_c2_standby_identity_release(
        paths,
        private_state,
        identity_tx_path,
        acknowledged_identity_transaction_sha256=identity_tx_sha,
        created_at=_stamp(now),
        now=now,
    )
    identity_release_path, identity_release_sha = write_c2_standby_identity_release(
        paths,
        identity_release,
        operation=_operation("write-c2-identity-release"),
    )
    verified_identity_release = verify_c2_standby_identity_release(
        paths,
        private_state,
        identity_release_path,
        now=now,
    )
    assert verified_identity_release["transaction_apply_authorized"] is True
    identity_inspection = inspect_c2_standby_identity_release(
        paths,
        private_state,
        identity_release_path,
        acknowledged_release_sha256=identity_release_sha,
        now=now,
    )
    assert identity_inspection["release_already_claimed"] is False
    assert identity_inspection["live_mutation_performed"] is False

    identity_live = _C2IdentityOpener()
    identity_result = execute_c2_standby_identity_release(
        paths,
        private_state,
        identity_release_path,
        acknowledged_release_sha256=identity_release_sha,
        now=now,
        opener=identity_live,
        max_response_bytes=4 * 1024 * 1024,
        operation=_operation("apply-c2-identity"),
    )
    assert identity_result["status"] == "pass"
    assert identity_result["summary"]["complete"] is True
    assert identity_result["identity_mutation_count"] == 2
    assert identity_result["validator_vote_performed"] is False
    assert identity_result["chain_mutation_count"] == 0
    assert {item["body"]["key"] for item in identity_live.requests if item["method"] == "POST"} == {
        "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
        "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
    }
    rendered = json.dumps(identity_result)
    assert "a1" * 32 not in rendered
    assert "b2" * 32 not in rendered
    assert identity_result["policy"]["secrets_in_output"] is False

    identity_verified = verify_c2_standby_identity(
        paths,
        private_state,
        Path(identity_result["result_artifact"]["path"]),
        opener=identity_live,
        max_response_bytes=4 * 1024 * 1024,
        observed_at=_stamp(now),
        write_evidence=True,
        operation=_operation("verify-c2-identity"),
    )
    assert identity_verified["summary"]["clean"] is True
    assert identity_verified["summary"]["verified_identity_key_count"] == 2
    assert identity_verified["identity_mutation_count"] == 0
    assert identity_verified["chain_mutation_count"] == 0
    assert identity_verified["validator_vote_performed"] is False
    assert identity_verified["next_phase"] == "prove-identity-rollback-cycle-before-genesis"
    assert "a1" * 32 not in json.dumps(identity_verified)
    assert "b2" * 32 not in json.dumps(identity_verified)
    assert Path(identity_verified["evidence"]["path"]).exists()


def test_cli_registers_c2_standby_commands() -> None:
    parser = mother_deploy._parser()
    commands = {
        "stage-c2-standby-service": [
            "--preflight-evidence", "p.json",
            "--c2-state-extension-evidence", "c2.json",
        ],
        "verify-c2-standby-service-transaction": [
            "--transaction", "t.json",
            "--c2-state-extension-evidence", "c2.json",
        ],
        "release-c2-standby-service": [
            "--transaction", "t.json",
            "--c2-state-extension-evidence", "c2.json",
            "--acknowledge-c2-standby-service-transaction-sha256", "a" * 64,
        ],
        "verify-c2-standby-service-release": [
            "--release", "r.json",
            "--c2-state-extension-evidence", "c2.json",
        ],
        "apply-c2-standby-service": [
            "--release", "r.json",
            "--c2-state-extension-evidence", "c2.json",
            "--acknowledge-release-sha256", "a" * 64,
        ],
        "verify-c2-standby-service": ["--execution", "e.json"],
        "stage-c2-standby-identity": ["--standby-evidence", "s.json"],
        "verify-c2-standby-identity-transaction": ["--transaction", "t.json"],
        "release-c2-standby-identity": [
            "--transaction", "t.json",
            "--acknowledge-c2-standby-identity-transaction-sha256", "b" * 64,
        ],
        "verify-c2-standby-identity-release": ["--release", "r.json"],
        "apply-c2-standby-identity": [
            "--release", "r.json",
            "--acknowledge-release-sha256", "b" * 64,
        ],
        "verify-c2-standby-identity": ["--execution", "e.json"],
    }
    for command, extra in commands.items():
        args = parser.parse_args([command, "--node", "mainnetc-super2", *extra])
        assert args.command == command
