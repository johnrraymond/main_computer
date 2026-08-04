from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_identity_executor import (
    MotherDeploymentIdentityExecutorError,
    execute_released_identity,
    inspect_released_identity,
)
from tools.mother.common.deployment_identity_install import (
    build_deployment_identity_install_transaction,
    write_deployment_identity_install_transaction,
)
from tools.mother.common.deployment_identity_rollback import (
    execute_identity_journal_rollback,
    execute_identity_mutation_rollback,
    identity_rollback_journal_path,
    inspect_identity_mutation_rollback,
    verify_identity_mutation_rollback,
    write_identity_mutation_rollback_verification,
)
from tools.mother.common.deployment_identity_release import (
    MotherDeploymentIdentityReleaseError,
    build_deployment_identity_release,
    verify_deployment_identity_release,
    write_deployment_identity_release,
)
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C, _operation
from tests.test_mother_deployment_identity_install import _evidence


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


class _IdentityOpener:
    def __init__(
        self,
        *,
        fail_key: str | None = None,
        existing_key: str | None = None,
        mask_after_post: bool = False,
        hide_post_value: bool = False,
        stale_delete_observations: int = 0,
    ) -> None:
        self.fail_key = fail_key
        self.mask_after_post = mask_after_post
        self.hide_post_value = hide_post_value
        self.stale_delete_observations = stale_delete_observations
        self.pending_deletes: dict[tuple[str, str], int] = {}
        self.requests: list[dict] = []
        self.envs = {
            "coolify-a.invalid": [],
            "coolify-c.invalid": [],
        }
        if existing_key:
            self.envs["coolify-a.invalid"].append(
                {"uuid": "existing-env", "key": existing_key, "value": "0x" + "f" * 64}
            )

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": host, "path": path, "body": body})
        assert timeout > 0
        token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {token}"
        expected_service = "svc-mainneta-super1" if host == "coolify-a.invalid" else "svc-mainnetc-super1"
        base_path = f"/api/v1/services/{expected_service}/envs"
        assert path == base_path or path.startswith(base_path + "/")
        if method == "GET":
            for (pending_host, env_uuid), remaining in list(self.pending_deletes.items()):
                if pending_host != host:
                    continue
                if remaining <= 0:
                    self.envs[host] = [
                        item for item in self.envs[host] if item.get("uuid") != env_uuid
                    ]
                    del self.pending_deletes[(pending_host, env_uuid)]
                else:
                    self.pending_deletes[(pending_host, env_uuid)] = remaining - 1
            items = list(self.envs[host])
            if self.mask_after_post and items:
                items = [{**item, "value": "********"} for item in items]
            return _Response({"envs": items})
        if method == "DELETE":
            env_uuid = path.rsplit("/", 1)[-1]
            if any(item.get("uuid") == env_uuid for item in self.envs[host]):
                self.pending_deletes[(host, env_uuid)] = self.stale_delete_observations
                if self.stale_delete_observations == 0:
                    self.envs[host] = [
                        item for item in self.envs[host] if item.get("uuid") != env_uuid
                    ]
                    self.pending_deletes.pop((host, env_uuid), None)
                return _Response({"message": "deleted"}, status=200)
            return _Response({"message": "not found"}, status=404)
        assert method == "POST"
        assert body is not None
        if body["key"] == self.fail_key:
            return _Response({"message": "failed"}, status=500)
        item = {
            "uuid": f"env-{host[8]}-{len(self.envs[host]) + 1}",
            "key": body["key"],
            "value": body["value"],
        }
        self.envs[host].append(item)
        response_item = {key: value for key, value in item.items() if key != "value"} if self.hide_post_value else item
        return _Response(response_item, status=201)


def _identity_transaction(tmp_path: Path, *, now: datetime):
    stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    paths, private_state, evidence_path, _ = _evidence(tmp_path, observed_at=stamp)
    transaction = build_deployment_identity_install_transaction(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at=stamp,
        now=now,
    )
    transaction_path, digest = write_deployment_identity_install_transaction(
        paths,
        transaction,
        operation=_operation("identity-executor-transaction"),
    )
    return paths, private_state, transaction_path, digest


def _identity_release(tmp_path: Path, *, now: datetime):
    paths, private_state, transaction_path, transaction_digest = _identity_transaction(tmp_path, now=now)
    stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    release = build_deployment_identity_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_identity_transaction_sha256=transaction_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at=stamp,
        now=now,
    )
    release_path, release_digest = write_deployment_identity_release(
        paths,
        release,
        operation=_operation("identity-executor-release"),
    )
    return paths, private_state, transaction_path, transaction_digest, release_path, release_digest


def test_identity_release_is_secret_free_exact_and_verifiable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 31, 19, 40, tzinfo=timezone.utc)
    paths, private_state, transaction_path, transaction_digest = _identity_transaction(tmp_path, now=now)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("identity release must not perform network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    release = build_deployment_identity_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_identity_transaction_sha256=transaction_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T19:40:05Z",
        now=now + timedelta(seconds=5),
    )
    assert release["resolved_blocker_codes"] == ["MOTHER_DEPLOY_IDENTITY_RELEASE_REQUIRED"]
    assert release["summary"]["remaining_blocker_codes"] == []
    assert release["summary"]["persisted_secret_value_count"] == 0
    rendered = json.dumps(release)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    assert "0x" not in rendered

    release_path, digest = write_deployment_identity_release(
        paths,
        release,
        operation=_operation("identity-release-write"),
    )
    verified = verify_deployment_identity_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        now=now + timedelta(seconds=10),
    )
    assert verified["clean"] is True
    assert verified["identity_release_sha256"] == digest
    assert verified["identity_transaction_sha256"] == transaction_digest


def test_identity_release_rejects_wrong_acknowledgement(tmp_path: Path) -> None:
    now = datetime(2026, 7, 31, 19, 40, tzinfo=timezone.utc)
    paths, private_state, transaction_path, _ = _identity_transaction(tmp_path, now=now)
    with pytest.raises(MotherDeploymentIdentityReleaseError) as caught:
        build_deployment_identity_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_identity_transaction_sha256="0" * 64,
            created_at="2026-07-31T19:40:05Z",
            now=now + timedelta(seconds=5),
        )
    assert caught.value.code == "MOTHER_DEPLOY_IDENTITY_RELEASE_ACKNOWLEDGEMENT_MISMATCH"


def test_identity_executor_dry_run_is_network_free_and_resolves_blocker(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    result = inspect_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        now=now + timedelta(seconds=1),
    )
    assert result["clean"] is True
    assert result["executor_implemented"] is True
    assert result["release_already_claimed"] is False
    assert result["live_execution_authorized"] is True
    assert result["remaining_blocker_codes"] == []


def test_identity_executor_materializes_in_memory_writes_four_keys_and_proves_readback(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    live = _IdentityOpener()

    result = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        operation=_operation("identity-executor-live"),
    )

    posts = [item for item in live.requests if item["method"] == "POST"]
    assert [(item["host"], item["body"]["key"]) for item in posts] == [
        ("coolify-a.invalid", "MC_MOTHER_VALIDATOR_PRIVATE_KEY"),
        ("coolify-a.invalid", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"),
        ("coolify-c.invalid", "MC_MOTHER_VALIDATOR_PRIVATE_KEY"),
        ("coolify-c.invalid", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"),
    ]
    assert result["status"] == "pass"
    assert result["summary"]["succeeded_mutation_count"] == 4
    assert result["summary"]["commitment_verified_count"] == 4
    assert result["summary"]["persisted_secret_value_count"] == 0
    assert Path(result["result_artifact"]["path"]).is_file()
    rendered = json.dumps(result)
    for post in posts:
        assert post["body"]["value"] not in rendered
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered


def test_identity_executor_refuses_existing_key_before_any_post(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    live = _IdentityOpener(existing_key="MC_MOTHER_VALIDATOR_PRIVATE_KEY")

    result = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("identity-executor-existing"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_IDENTITY_ENV_ALREADY_EXISTS"
    assert not [item for item in live.requests if item["method"] == "POST"]
    assert result["summary"]["live_mutation_performed"] is False




def test_identity_executor_records_acknowledged_write_when_commitment_proof_fails(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    live = _IdentityOpener(mask_after_post=True, hide_post_value=True)
    result = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("identity-executor-unverified-write"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_IDENTITY_COMMITMENT_NOT_PROVEN"
    assert result["summary"]["live_mutation_performed"] is True
    assert result["summary"]["succeeded_mutation_count"] == 0
    assert result["mutation_receipts"][0]["status"] == "succeeded-unverified"
    assert result["mutation_receipts"][0]["live_write_acknowledged"] is True

def test_identity_executor_is_one_shot_and_records_partial_failure(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    live = _IdentityOpener(fail_key="MC_MOTHER_HUB_ADMIN_PRIVATE_KEY")
    result = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("identity-executor-partial"),
    )
    assert result["status"] == "failed"
    assert result["summary"]["succeeded_mutation_count"] == 1
    assert result["summary"]["live_mutation_performed"] is True
    assert result["failure"]["code"] == "MOTHER_DEPLOY_IDENTITY_EXECUTOR_MUTATION_FAILED"

    with pytest.raises(MotherDeploymentIdentityExecutorError) as caught:
        execute_released_identity(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            opener=_IdentityOpener(),
            operation=_operation("identity-executor-replay"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_IDENTITY_RELEASE_ALREADY_CONSUMED"


def test_identity_release_and_dry_run_cli(tmp_path: Path, capsys) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, _, transaction_path, transaction_digest = _identity_transaction(tmp_path, now=now)
    runtime_root = paths.root.parent
    code = mother_deploy.main(
        [
            "release-identity",
            "--runtime-state-root",
            str(runtime_root),
            "--transaction",
            str(transaction_path),
            "--acknowledge-identity-transaction-sha256",
            transaction_digest,
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
            "--write-release",
        ]
    )
    assert code == 0
    release = json.loads(capsys.readouterr().out)
    release_path = release["release_artifact"]["path"]
    release_digest = release["release_artifact"]["sha256"]

    code = mother_deploy.main(
        [
            "verify-identity-release",
            "--runtime-state-root",
            str(runtime_root),
            "--release",
            release_path,
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True

    code = mother_deploy.main(
        [
            "apply-identity",
            "--runtime-state-root",
            str(runtime_root),
            "--release",
            release_path,
            "--acknowledge-release-sha256",
            release_digest,
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
        ]
    )
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["execute_requested"] is False
    assert inspected["live_execution_authorized"] is True


def test_identity_execution_can_be_rolled_back_verified_and_retried_idempotently(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    live = _IdentityOpener(stale_delete_observations=2)

    execution = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        operation=_operation("identity-live-for-rollback"),
    )
    assert execution["status"] == "pass"
    assert execution["summary"]["rollback_available"] is True
    assert execution["summary"]["genesis_blocked_pending_identity_rollback_cycle"] is True

    inspected = inspect_identity_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
        opener=live,
        operation=_operation("identity-rollback-inspect"),
    )
    assert inspected["rollback_boundary_open"] is True
    assert inspected["rollback_operation_count"] == 4

    rolled_back = execute_identity_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
        opener=live,
        operation=_operation("identity-rollback-live"),
    )
    assert rolled_back["status"] == "pass"
    assert rolled_back["summary"]["complete"] is True
    assert rolled_back["summary"]["absent_count"] == 4
    assert all(not items for items in live.envs.values())

    verified = verify_identity_mutation_rollback(
        paths,
        private_state,
        Path(rolled_back["result_artifact"]["path"]),
        opener=live,
    )
    assert verified["clean"] is True
    assert verified["summary"]["absent_count"] == 4
    evidence_path, evidence_digest = write_identity_mutation_rollback_verification(
        paths,
        verified,
        operation=_operation("identity-rollback-evidence"),
    )
    assert evidence_path.is_file()
    assert len(evidence_digest) == 64

    retry = execute_identity_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
        opener=live,
        operation=_operation("identity-rollback-retry"),
    )
    assert retry["status"] == "pass"
    assert retry["summary"]["complete"] is True


def test_identity_partial_failure_is_automatically_compensated(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    live = _IdentityOpener(fail_key="MC_MOTHER_HUB_ADMIN_PRIVATE_KEY")

    result = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("identity-auto-rollback"),
    )
    assert result["status"] == "failed"
    assert result["summary"]["automatic_rollback_complete"] is True
    assert result["automatic_rollback"]["status"] == "pass"
    assert all(not items for items in live.envs.values())


def test_identity_crash_after_post_is_recoverable_from_durable_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    live = _IdentityOpener()
    from tools.mother.common import deployment_identity_executor as executor_module

    real_http_json = executor_module._http_json
    crashed = {"done": False}

    def crash_after_first_post(*args, **kwargs):  # noqa: ANN002, ANN003
        result = real_http_json(*args, **kwargs)
        if len(args) >= 2 and args[1] == "POST" and not crashed["done"]:
            crashed["done"] = True
            raise KeyboardInterrupt("simulated process termination after accepted POST")
        return result

    monkeypatch.setattr(executor_module, "_http_json", crash_after_first_post)
    with pytest.raises(KeyboardInterrupt):
        execute_released_identity(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            opener=live,
            operation=_operation("identity-crash-after-post"),
        )

    journal_path = identity_rollback_journal_path(paths, release_digest)
    assert journal_path.is_file()
    journal_digest = __import__("hashlib").sha256(journal_path.read_bytes()).hexdigest()
    assert sum(len(items) for items in live.envs.values()) == 1

    recovered = execute_identity_journal_rollback(
        paths,
        private_state,
        journal_path,
        acknowledged_journal_sha256=journal_digest,
        opener=live,
        operation=_operation("identity-crash-recovery"),
    )
    assert recovered["status"] == "pass"
    assert recovered["summary"]["complete"] is True
    assert all(not items for items in live.envs.values())
