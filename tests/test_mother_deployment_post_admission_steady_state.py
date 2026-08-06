from __future__ import annotations

import base64
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_genesis_birth import _compose_semantic_sha256
from tools.mother.common import deployment_post_admission_steady_state as steady_state
from tools.mother.common.deployment_post_admission_steady_state import (
    MotherDeploymentPostAdmissionSteadyStateError,
    build_post_admission_steady_state_release,
    build_post_admission_steady_state_transaction,
    execute_post_admission_steady_state_release,
    inspect_post_admission_steady_state_release,
    reconcile_post_admission_steady_state,
    verify_post_admission_steady_state_evidence,
    verify_post_admission_steady_state_reconciliation,
    verify_post_admission_steady_state_release,
    verify_post_admission_steady_state_transaction,
    write_post_admission_steady_state_release,
    write_post_admission_steady_state_transaction,
)
from tools.mother.common.deployment_validator_quorum_recovery import (
    execute_validator_quorum_recovery_release,
    reconcile_validator_quorum_recovery,
)
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C, _operation
from tests.test_mother_deployment_validator_admission import _AdmissionResponse
from tests.test_mother_deployment_validator_quorum_recovery import (
    _QuorumReconciliationOpener,
    _QuorumRecoveryOpener,
    _failed_recovery_for_reconciliation,
    _fixture as _quorum_fixture,
)


def _fixture(tmp_path: Path):
    paths, private_state, quorum_release, failed = _failed_recovery_for_reconciliation(tmp_path)
    reconciliation = reconcile_validator_quorum_recovery(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        selected_nodes=("mainnetc-super1",),
        opener=_QuorumReconciliationOpener(quorum_release),
        operation=_operation("post-admission-steady-state-reconciliation"),
    )
    reconciliation_path = Path(reconciliation["reconciliation_artifact"]["path"])

    # The production checkpoint is generation 2.  The older deployment fixtures
    # construct generation 1, so promote only the binding and the two canonical
    # source artifacts used by this phase.
    private_state = replace(
        private_state,
        binding=replace(private_state.binding, generation=2),
    )
    reconciliation_document = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    source_path = paths.root / Path(reconciliation_document["source_release"]["locator"])
    source_document = json.loads(source_path.read_text(encoding="utf-8"))
    source_document["mother_binding"]["generation"] = 2
    source_document["validator_quorum_recovery_release_sha256"] = hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in source_document.items()
                if key != "validator_quorum_recovery_release_sha256"
            }
        )
    ).hexdigest()
    source_path.write_bytes(canonical_json(source_document))
    reconciliation_document["mother_binding"]["generation"] = 2
    reconciliation_document["source_release"]["sha256"] = source_document[
        "validator_quorum_recovery_release_sha256"
    ]
    reconciliation_document["source_release"]["file_sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    reconciliation_path.write_bytes(canonical_json(reconciliation_document))

    transaction = build_post_admission_steady_state_transaction(
        paths,
        private_state,
        reconciliation_path,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    transaction_path, transaction_digest = write_post_admission_steady_state_transaction(
        paths,
        transaction,
        operation=_operation("post-admission-steady-state-transaction"),
    )
    release = build_post_admission_steady_state_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    release_path, release_digest = write_post_admission_steady_state_release(
        paths,
        release,
        operation=_operation("post-admission-steady-state-release"),
    )
    return (
        paths,
        private_state,
        reconciliation_path,
        transaction,
        transaction_path,
        transaction_digest,
        release,
        release_path,
        release_digest,
    )



def _passing_quorum_evidence_fixture(tmp_path: Path):
    (
        paths,
        private_state,
        _,
        _,
        quorum_release,
        quorum_release_path,
        quorum_release_digest,
    ) = _quorum_fixture(tmp_path)
    result = execute_validator_quorum_recovery_release(
        paths,
        private_state,
        quorum_release_path,
        acknowledged_release_sha256=quorum_release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=_QuorumRecoveryOpener(
            quorum_release,
            aggregate_degraded_components_healthy=True,
        ),
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("post-admission-steady-state-passing-quorum-source"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["component_scoped_health_accepted"] is True
    evidence_path = Path(result["evidence"]["path"])

    private_state = replace(
        private_state,
        binding=replace(private_state.binding, generation=2),
    )
    release_document = json.loads(quorum_release_path.read_text(encoding="utf-8"))
    release_document["mother_binding"]["generation"] = 2
    release_document["validator_quorum_recovery_release_sha256"] = hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in release_document.items()
                if key != "validator_quorum_recovery_release_sha256"
            }
        )
    ).hexdigest()
    quorum_release_path.write_bytes(canonical_json(release_document))

    evidence_document = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence_document["mother_binding"]["generation"] = 2
    evidence_document["release"]["sha256"] = release_document[
        "validator_quorum_recovery_release_sha256"
    ]
    evidence_path.write_bytes(canonical_json(evidence_document))
    return paths, private_state, evidence_path, release_document



def test_transaction_accepts_passing_quorum_recovery_evidence(tmp_path: Path) -> None:
    paths, private_state, evidence_path, _ = _passing_quorum_evidence_fixture(tmp_path)
    transaction = build_post_admission_steady_state_transaction(
        paths,
        private_state,
        quorum_evidence_path=evidence_path,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    assert "reconciliation" not in transaction
    assert transaction["quorum_recovery_evidence"]["locator"].startswith(
        "evidence/deployment-validator-quorum-recovery/"
    )
    assert transaction["summary"]["quorum_source_kind"] == "passing-quorum-recovery-evidence"
    assert transaction["summary"]["blocks_advancing_verified_by_quorum_evidence"] is True
    assert transaction["summary"]["blocks_advancing_verified_by_reconciliation"] is False

    transaction_path, transaction_digest = write_post_admission_steady_state_transaction(
        paths,
        transaction,
        operation=_operation("post-admission-steady-state-direct-evidence-transaction"),
    )
    verified_transaction = verify_post_admission_steady_state_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    assert verified_transaction["post_admission_steady_state_transaction_sha256"] == transaction_digest

    release = build_post_admission_steady_state_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    assert "reconciliation" not in release
    assert release["quorum_recovery_evidence"] == transaction["quorum_recovery_evidence"]
    release_path, release_digest = write_post_admission_steady_state_release(
        paths,
        release,
        operation=_operation("post-admission-steady-state-direct-evidence-release"),
    )
    verified_release = verify_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    assert verified_release["post_admission_steady_state_release_sha256"] == release_digest


def test_transaction_rejects_tampered_passing_quorum_recovery_evidence(
    tmp_path: Path,
) -> None:
    paths, private_state, evidence_path, _ = _passing_quorum_evidence_fixture(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["mutation_receipts"][0]["body_sha256"] = "0" * 64
    evidence_path.write_bytes(canonical_json(evidence))

    with pytest.raises(MotherDeploymentPostAdmissionSteadyStateError) as caught:
        build_post_admission_steady_state_transaction(
            paths,
            private_state,
            quorum_evidence_path=evidence_path,
            selected_nodes=("mainnetc-super1", "mainneta-super1"),
        )
    assert (
        caught.value.code
        == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_QUORUM_EVIDENCE_INVALID"
    )


def test_cli_dispatches_post_admission_steady_state_from_quorum_evidence(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    paths, _, _, _, _, _, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    evidence_path = (
        paths.root
        / "evidence"
        / "deployment-validator-quorum-recovery"
        / "passing.json"
    )
    captured: dict[str, Any] = {}

    def fake_build(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["reconciliation_path"] = args[2]
        captured["quorum_evidence_path"] = kwargs["quorum_evidence_path"]
        return {
            "kind": "test",
            "summary": {"next_phase": "release-post-admission-steady-state"},
        }

    monkeypatch.setattr(
        mother_deploy,
        "build_post_admission_steady_state_transaction",
        fake_build,
    )
    code = mother_deploy.main(
        [
            "stage-post-admission-steady-state",
            "--runtime-state-root",
            str(runtime_root),
            "--quorum-evidence",
            str(evidence_path),
            "--node",
            "mainnetc-super1",
            "--node",
            "mainneta-super1",
        ]
    )
    assert code == 0
    assert captured["reconciliation_path"] is None
    assert captured["quorum_evidence_path"] == evidence_path
    assert json.loads(capsys.readouterr().out)["summary"]["next_phase"] == (
        "release-post-admission-steady-state"
    )


class _FakeClock:
    def __init__(self, on_sleep=None) -> None:  # noqa: ANN001
        self.value = 0.0
        self.sleeps: list[float] = []
        self.on_sleep = on_sleep

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += max(0.0, seconds)
        if self.on_sleep is not None:
            self.on_sleep(seconds)


def _install_fake_clock(monkeypatch, *, on_sleep=None) -> _FakeClock:  # noqa: ANN001
    clock = _FakeClock(on_sleep=on_sleep)
    monkeypatch.setattr(steady_state, "_MONOTONIC", clock.monotonic)
    monkeypatch.setattr(steady_state, "_SLEEP", clock.sleep)
    return clock


class _SteadyStateOpener:
    def __init__(
        self,
        release: dict[str, Any],
        *,
        keep_obsolete_c: bool = False,
        require_guardian_refresh: bool = False,
        blank_inventory_status_after_c: bool = False,
        degraded_aggregate_with_obsolete_c: bool = False,
        obsolete_c_status: str = "exited",
        include_a_retained_services: bool = False,
    ) -> None:
        self.release = release
        self.keep_obsolete_c = keep_obsolete_c
        self.require_guardian_refresh = require_guardian_refresh
        self.blank_inventory_status_after_c = blank_inventory_status_after_c
        self.degraded_aggregate_with_obsolete_c = degraded_aggregate_with_obsolete_c
        self.obsolete_c_status = obsolete_c_status
        self.include_a_retained_services = include_a_retained_services
        self.guardian_refreshed = False
        self.a_post_c_detail_observations = 0
        self.a_patch_after_guardian_refresh = False
        self.patched = {"mainneta-super1": False, "mainnetc-super1": False}
        self.deployed = {"mainneta-super1": False, "mainnetc-super1": False}
        self.requests: list[tuple[str, str, str]] = []

    def _node(self, host: str) -> str:
        return "mainneta-super1" if host == "coolify-a.invalid" else "mainnetc-super1"

    def _applications(self, node: str) -> list[dict[str, str]]:
        target = self.release["targets"][node]
        guardian = target["required_healthy_components"][1]
        applications = [
            {
                "uuid": node + "-besu",
                "name": node,
                "status": "running:healthy",
                "image": "hyperledger/besu:latest",
            },
            {
                "uuid": guardian + "-uuid",
                "name": guardian,
                "status": "running:healthy",
                "image": "python:3.12-alpine",
            },
        ]
        if (
            node == "mainneta-super1"
            and self.include_a_retained_services
            and not self.patched[node]
        ):
            recovered = yaml.safe_load(target["recovered_compose"]["canonical_text"])
            for index, name in enumerate(sorted(recovered["services"])):
                if name in {node, guardian} or name in target["recognized_obsolete_components"]:
                    continue
                applications.append(
                    {
                        "uuid": f"{node}-retained-{index}",
                        "name": name,
                        "status": "running:healthy",
                        "image": "retained-service:test",
                    }
                )
        if not self.deployed[node] or (node == "mainnetc-super1" and self.keep_obsolete_c):
            for index, name in enumerate(target["recognized_obsolete_components"]):
                applications.append(
                    {
                        "uuid": f"{node}-obsolete-{index}",
                        "name": name,
                        "status": (
                            self.obsolete_c_status
                            if node == "mainnetc-super1"
                            else "exited"
                        ),
                        "image": "python:3.12-alpine",
                    }
                )
        return applications

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        method = request.get_method()
        path = parsed.path
        node = self._node(host)
        target = self.release["targets"][node]
        self.requests.append((host, method, path))
        assert timeout > 0
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"

        if method == "GET" and path == "/api/v1/services":
            status = "running:healthy" if self.deployed[node] else "degraded:unhealthy"
            if (
                node == "mainnetc-super1"
                and self.deployed[node]
                and self.keep_obsolete_c
                and self.degraded_aggregate_with_obsolete_c
            ):
                status = "degraded:unhealthy"
            if (
                self.blank_inventory_status_after_c
                and node == "mainnetc-super1"
                and self.deployed[node]
            ):
                status = ""
            return _AdmissionResponse(
                [{"uuid": target["service_uuid"], "name": node, "status": status}]
            )

        if method == "GET" and path == f"/api/v1/services/{target['service_uuid']}":
            if (
                node == "mainneta-super1"
                and self.deployed["mainnetc-super1"]
                and not self.patched["mainneta-super1"]
            ):
                self.a_post_c_detail_observations += 1
            compose = (
                target["steady_state_compose"]["canonical_text"]
                if self.patched[node]
                else target["recovered_compose"]["canonical_text"]
            )
            status = "running:healthy" if self.deployed[node] else "degraded:unhealthy"
            if (
                node == "mainnetc-super1"
                and self.deployed[node]
                and self.keep_obsolete_c
                and self.degraded_aggregate_with_obsolete_c
            ):
                status = "degraded:unhealthy"
            return _AdmissionResponse(
                {
                    "service": {
                        "uuid": target["service_uuid"],
                        "name": node,
                        "status": status,
                        "docker_compose_raw": compose,
                        "applications": self._applications(node),
                    }
                }
            )

        if method == "PATCH" and path == f"/api/v1/services/{target['service_uuid']}":
            body = json.loads(request.data.decode("utf-8"))
            compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            assert body["name"] == node
            assert compose == target["steady_state_compose"]["canonical_text"]
            if node == "mainneta-super1" and self.require_guardian_refresh:
                assert self.guardian_refreshed
                assert self.a_post_c_detail_observations >= 2
                self.a_patch_after_guardian_refresh = True
            self.patched[node] = True
            return _AdmissionResponse({"uuid": target["service_uuid"]}, status=200)

        if method == "GET" and path == "/api/v1/deploy":
            assert self.patched[node]
            self.deployed[node] = True
            return _AdmissionResponse({"deployment_uuid": f"steady-{node}"}, status=200)

        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_transaction_compiles_exact_two_service_steady_state_documents(tmp_path: Path) -> None:
    (
        paths,
        private_state,
        _,
        transaction,
        transaction_path,
        transaction_digest,
        release,
        release_path,
        release_digest,
    ) = _fixture(tmp_path)

    assert transaction["staged_scope"] == "replace-exact-recovered-compose-with-post-admission-steady-state"
    assert transaction["chain"]["quorum_recovered"] is True
    assert transaction["chain"]["blocks_advancing"] is True
    assert len(transaction["chain"]["validator_set"]) == 2
    assert transaction["authority"]["validator_vote_authorized"] is False
    assert transaction["policy"]["volume_deletion"] is False
    assert transaction["policy"]["besu_data_deletion"] is False
    assert transaction["policy"]["manual_ssh_required"] is False
    assert transaction["policy"]["public_http_endpoint_created"] is False

    for node, target in transaction["targets"].items():
        compose = target["steady_state_compose"]["canonical_text"]
        parsed = yaml.safe_load(compose)
        assert set(parsed["services"]) == set(target["steady_state_compose"]["retained_services"])
        assert target["removed_compose_services"][0] not in parsed["services"]
        assert "MC_MOTHER_VALIDATOR_PRIVATE_KEY" not in compose
        assert "--static-nodes-file=/config/static-nodes.json" in compose
        assert "mother-data:/var/lib/besu" in compose
        assert "8545:8545" not in compose
        assert "qbft_proposeValidatorVote" not in compose
        assert parsed["services"][node]["labels"]["main_computer.mother.stage"] == "post-admission-steady-state"
    assert (
        yaml.safe_load(
            transaction["targets"]["mainnetc-super1"]["steady_state_compose"]["canonical_text"]
        )["services"]["mainnetc-super1"]["labels"]["main_computer.mother.validator-activation"]
        == "active"
    )
    assert [item["mutation_id"] for item in transaction["execution_plan"]["mutations"]] == [
        "mainnetc-super1.install-post-admission-steady-state",
        "mainnetc-super1.deploy-post-admission-steady-state",
        "mainneta-super1.install-post-admission-steady-state",
        "mainneta-super1.deploy-post-admission-steady-state",
    ]
    assert [item["method"] for item in transaction["execution_plan"]["mutations"]] == [
        "PATCH",
        "GET",
        "PATCH",
        "GET",
    ]

    verified_transaction = verify_post_admission_steady_state_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    assert verified_transaction["post_admission_steady_state_transaction_sha256"] == transaction_digest
    verified_release = verify_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    assert verified_release["post_admission_steady_state_release_sha256"] == release_digest
    inspected = inspect_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    assert inspected["network_access_performed"] is False
    assert inspected["live_mutation_performed"] is False
    assert inspected["validator_vote_performed"] is False
    assert release["execution_plan"]["restart_strategy"] == "C-health-and-joint-block-proof-before-A"


def test_executor_cleans_c_then_proves_joint_health_then_cleans_a(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    clock = _install_fake_clock(monkeypatch)
    opener = _SteadyStateOpener(release)
    result = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("post-admission-steady-state-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["post_admission_steady_state_installed"] is True
    assert result["summary"]["obsolete_phase_components_absent"] is True
    assert result["summary"]["initial_aggregate_service_running_healthy"] is True
    assert result["summary"]["replica_aggregate_service_running_healthy"] is True
    assert result["summary"]["joint_blocks_resumed_before_A_restart"] is True
    assert result["summary"]["validator_vote_performed"] is False
    assert result["guardian_refresh_gate"]["required_wait_seconds"] == 50
    assert result["guardian_refresh_gate"]["observed_wait_seconds"] >= 50
    assert 50 in clock.sleeps
    mutation_requests = [
        (host, method, path)
        for host, method, path in opener.requests
        if method == "PATCH" or path == "/api/v1/deploy"
    ]
    assert mutation_requests == [
        ("coolify-c.invalid", "PATCH", f"/api/v1/services/{release['targets']['mainnetc-super1']['service_uuid']}"),
        ("coolify-c.invalid", "GET", "/api/v1/deploy"),
        ("coolify-a.invalid", "PATCH", f"/api/v1/services/{release['targets']['mainneta-super1']['service_uuid']}"),
        ("coolify-a.invalid", "GET", "/api/v1/deploy"),
    ]
    verified = verify_post_admission_steady_state_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    assert verified["aggregate_services_running_healthy"] is True
    assert verified["obsolete_phase_components_absent"] is True
    assert verified["blocks_advancing"] is True
    assert verified["validator_vote_performed"] is False


def test_executor_waits_for_fresh_a_guardian_cycle_before_restarting_a(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SteadyStateOpener(release, require_guardian_refresh=True)

    def mark_refreshed(seconds: float) -> None:
        if seconds >= 50:
            opener.guardian_refreshed = True

    clock = _install_fake_clock(monkeypatch, on_sleep=mark_refreshed)
    result = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("post-admission-steady-state-refresh-gate"),
    )

    assert result["status"] == "pass"
    assert opener.a_post_c_detail_observations >= 2
    assert opener.a_patch_after_guardian_refresh is True
    assert clock.sleeps == [50]
    successful_phases = [
        item["phase"]
        for item in result["health_observations"]
        if item.get("verified") is True
    ]
    assert successful_phases.index("A-guardian-before-refresh-window") < successful_phases.index(
        "A-guardian-after-refresh-window"
    )
    assert successful_phases.index("A-guardian-after-refresh-window") < successful_phases.index(
        "mainneta-super1-final-steady-state"
    )


def test_evidence_verifier_binds_release_chain_and_observation_phases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    _install_fake_clock(monkeypatch)
    result = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=_SteadyStateOpener(release),
        poll_interval_seconds=0,
        max_wait_seconds=1,
        operation=_operation("post-admission-steady-state-evidence-binding"),
    )
    assert result["status"] == "pass"
    evidence_path = Path(result["evidence"]["path"])
    original = json.loads(evidence_path.read_text(encoding="utf-8"))

    tampered_documents: list[dict[str, Any]] = []

    changed_validators = copy.deepcopy(original)
    changed_validators["validator_set"] = ["0x" + ("1" * 40), "0x" + ("2" * 40)]
    tampered_documents.append(changed_validators)

    changed_chain_id = copy.deepcopy(original)
    changed_chain_id["chain_id"] = original["chain_id"] + 1
    tampered_documents.append(changed_chain_id)

    changed_genesis = copy.deepcopy(original)
    changed_genesis["genesis_sha256"] = "0" * 64
    tampered_documents.append(changed_genesis)

    changed_phase = copy.deepcopy(original)
    post_refresh = next(
        item
        for item in changed_phase["health_observations"]
        if item.get("phase") == "A-guardian-after-refresh-window"
    )
    post_refresh["phase"] = "A-guardian-after-refresh-window-tampered"
    tampered_documents.append(changed_phase)

    for document in tampered_documents:
        evidence_path.write_bytes(canonical_json(document))
        with pytest.raises(MotherDeploymentPostAdmissionSteadyStateError) as caught:
            verify_post_admission_steady_state_evidence(
                paths,
                private_state,
                evidence_path,
                selected_nodes=("mainneta-super1", "mainnetc-super1"),
            )
        assert caught.value.code == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID"

    evidence_path.write_bytes(canonical_json(original))


def test_executor_fails_when_recognized_obsolete_component_remains(tmp_path: Path) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    result = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=_SteadyStateOpener(release, keep_obsolete_c=True),
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("post-admission-steady-state-obsolete-remains"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_NOT_HEALTHY"
    assert result["summary"]["complete"] is False
    assert result["summary"]["validator_vote_performed"] is False



def test_compiler_preserves_known_a1_genesis_services(tmp_path: Path) -> None:
    paths, private_state, reconciliation_path, _, _, _, _, _, _ = _fixture(tmp_path)
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    source_path = paths.root / Path(reconciliation["source_release"]["locator"])
    source = json.loads(source_path.read_text(encoding="utf-8"))
    compose = source["execution_plan"]["initial_quorum_compose"]["canonical_text"]
    retained = (
        "  mother-superseded-service-cleanup:\n"
        "    image: docker:27-cli\n\n"
    )
    changed = compose.replace("\nvolumes:\n", "\n" + retained + "volumes:\n", 1)
    changed = changed.replace(
        "    depends_on:\n"
        "      mother-genesis-init:\n"
        "        condition: service_completed_successfully\n",
        "    depends_on:\n"
        "      mother-genesis-init:\n"
        "        condition: service_completed_successfully\n"
        "      mother-super-node-fdb:\n"
        "        condition: service_healthy\n",
        1,
    )
    source["execution_plan"]["initial_quorum_compose"]["canonical_text"] = changed
    source["execution_plan"]["initial_quorum_compose"]["sha256"] = hashlib.sha256(
        changed.encode("utf-8")
    ).hexdigest()
    source["execution_plan"]["initial_quorum_compose"]["semantic_sha256"] = _compose_semantic_sha256(
        changed, "changed"
    )
    source["validator_quorum_recovery_release_sha256"] = hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in source.items()
                if key != "validator_quorum_recovery_release_sha256"
            }
        )
    ).hexdigest()
    source_path.write_bytes(canonical_json(source))
    reconciliation["source_release"]["sha256"] = source["validator_quorum_recovery_release_sha256"]
    reconciliation["source_release"]["file_sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    for target in reconciliation["targets"]:
        if target["node"] == "mainneta-super1":
            target["compose_binding"]["semantic_sha256"] = source["execution_plan"][
                "initial_quorum_compose"
            ]["semantic_sha256"]
    reconciliation_path.write_bytes(canonical_json(reconciliation))

    transaction = build_post_admission_steady_state_transaction(
        paths,
        private_state,
        reconciliation_path,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    a1_compose = transaction["targets"]["mainneta-super1"]["steady_state_compose"][
        "canonical_text"
    ]
    assert "mother-super-node-fdb:" in a1_compose
    assert "mother-super-node-hub:" in a1_compose
    assert "mother-superseded-service-cleanup:" in a1_compose
    assert "mother-super-node-fdb" in yaml.safe_load(a1_compose)["services"]["mainneta-super1"]["depends_on"]


def test_compiler_rejects_unexpected_recovered_service_lineage(tmp_path: Path) -> None:
    paths, private_state, reconciliation_path, _, _, _, _, _, _ = _fixture(tmp_path)
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    source_locator = reconciliation["source_release"]["locator"]
    source_path = paths.root / Path(source_locator)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    compose = source["execution_plan"]["replica_readiness_compose"]["canonical_text"]
    source["execution_plan"]["replica_readiness_compose"]["canonical_text"] = compose.replace(
        "\nvolumes:\n",
        "\n  unexpected-proof-helper:\n    image: python:3.12-alpine\n\nvolumes:\n",
        1,
    )
    changed = source["execution_plan"]["replica_readiness_compose"]["canonical_text"]
    source["execution_plan"]["replica_readiness_compose"]["sha256"] = hashlib.sha256(
        changed.encode("utf-8")
    ).hexdigest()
    source["execution_plan"]["replica_readiness_compose"]["semantic_sha256"] = _compose_semantic_sha256(
        changed, "changed"
    )
    source["validator_quorum_recovery_release_sha256"] = hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in source.items()
                if key != "validator_quorum_recovery_release_sha256"
            }
        )
    ).hexdigest()
    source_path.write_bytes(canonical_json(source))
    reconciliation["source_release"]["sha256"] = source["validator_quorum_recovery_release_sha256"]
    reconciliation["source_release"]["file_sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    for target in reconciliation["targets"]:
        if target["node"] == "mainnetc-super1":
            target["compose_binding"]["semantic_sha256"] = source["execution_plan"][
                "replica_readiness_compose"
            ]["semantic_sha256"]
    reconciliation_path.write_bytes(canonical_json(reconciliation))

    with pytest.raises(MotherDeploymentPostAdmissionSteadyStateError) as caught:
        build_post_admission_steady_state_transaction(
            paths,
            private_state,
            reconciliation_path,
            selected_nodes=("mainnetc-super1", "mainneta-super1"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_LINEAGE_MISMATCH"


def test_cli_dispatches_post_admission_steady_state_stage(tmp_path: Path, capsys, monkeypatch) -> None:
    paths, _, _, _, _, _, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    monkeypatch.setattr(
        mother_deploy,
        "build_post_admission_steady_state_transaction",
        lambda *args, **kwargs: {
            "kind": "test",
            "summary": {"next_phase": "release-post-admission-steady-state"},
        },
    )
    code = mother_deploy.main(
        [
            "stage-post-admission-steady-state",
            "--runtime-state-root",
            str(runtime_root),
            "--reconciliation",
            str(paths.root / "evidence" / "deployment-validator-quorum-recovery-reconciliations" / "x.json"),
            "--node",
            "mainnetc-super1",
            "--node",
            "mainneta-super1",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["summary"]["next_phase"] == "release-post-admission-steady-state"



def test_cli_compacts_persisted_post_admission_stage_output(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    paths, _, _, _, _, _, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    fake_transaction = {
        "kind": "mother-deployment-post-admission-steady-state-transaction",
        "targets": {
            "mainnetc-super1": {
                "recovered_compose": {
                    "canonical_text": "RECOVERED-COMPOSE-SPAM",
                    "sha256": "1" * 64,
                },
                "steady_state_compose": {
                    "canonical_text": "STEADY-COMPOSE-SPAM",
                    "sha256": "2" * 64,
                },
            }
        },
        "execution_plan": {
            "mutations": [
                {
                    "mutation_id": "mainnetc-super1.install-post-admission-steady-state",
                    "canonical_request_body": {
                        "name": "mainnetc-super1",
                        "docker_compose_raw": "BASE64-SPAM",
                    },
                    "body_sha256": "3" * 64,
                }
            ]
        },
        "summary": {"next_phase": "release-post-admission-steady-state"},
    }
    monkeypatch.setattr(
        mother_deploy,
        "build_post_admission_steady_state_transaction",
        lambda *args, **kwargs: fake_transaction,
    )
    monkeypatch.setattr(
        mother_deploy,
        "write_post_admission_steady_state_transaction",
        lambda *args, **kwargs: (tmp_path / "transaction.json", "4" * 64),
    )

    code = mother_deploy.main(
        [
            "stage-post-admission-steady-state",
            "--runtime-state-root",
            str(runtime_root),
            "--reconciliation",
            str(paths.root / "evidence" / "deployment-validator-quorum-recovery-reconciliations" / "x.json"),
            "--write-transaction",
        ]
    )

    assert code == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert "RECOVERED-COMPOSE-SPAM" not in output_text
    assert "STEADY-COMPOSE-SPAM" not in output_text
    assert "BASE64-SPAM" not in output_text
    assert output["targets"]["mainnetc-super1"]["recovered_compose"]["sha256"] == "1" * 64
    assert output["execution_plan"]["mutations"][0]["body_sha256"] == "3" * 64
    assert output["transaction_artifact"]["sha256"] == "4" * 64


def test_cli_full_output_restores_persisted_post_admission_payloads(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    paths, _, _, _, _, _, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    fake_transaction = {
        "kind": "test",
        "targets": {
            "mainnetc-super1": {
                "steady_state_compose": {"canonical_text": "FULL-COMPOSE"}
            }
        },
        "execution_plan": {
            "mutations": [
                {"canonical_request_body": {"docker_compose_raw": "FULL-BODY"}}
            ]
        },
        "summary": {"next_phase": "release-post-admission-steady-state"},
    }
    monkeypatch.setattr(
        mother_deploy,
        "build_post_admission_steady_state_transaction",
        lambda *args, **kwargs: fake_transaction,
    )
    monkeypatch.setattr(
        mother_deploy,
        "write_post_admission_steady_state_transaction",
        lambda *args, **kwargs: (tmp_path / "transaction.json", "5" * 64),
    )

    code = mother_deploy.main(
        [
            "stage-post-admission-steady-state",
            "--runtime-state-root",
            str(runtime_root),
            "--reconciliation",
            str(paths.root / "evidence" / "deployment-validator-quorum-recovery-reconciliations" / "x.json"),
            "--write-transaction",
            "--full-output",
        ]
    )

    assert code == 0
    output_text = capsys.readouterr().out
    assert "FULL-COMPOSE" in output_text
    assert "FULL-BODY" in output_text


def test_compact_execution_output_keeps_verdict_and_artifact_only() -> None:
    result = {
        "kind": "mother-deployment-post-admission-steady-state-evidence",
        "status": "pass",
        "summary": {"complete": True},
        "mutation_receipts": [{"response": {"status": 200}}],
        "health_observations": [{"phase": "poll-1"}],
        "evidence": {"path": "evidence.json", "sha256": "6" * 64},
    }

    compact = mother_deploy._compact_post_admission_execution(result)

    assert compact == {
        "kind": "mother-deployment-post-admission-steady-state-evidence",
        "status": "pass",
        "summary": {"complete": True},
        "evidence": {"path": "evidence.json", "sha256": "6" * 64},
    }



def test_cli_compacts_persisted_post_admission_release_output(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    paths, _, _, _, _, _, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    fake_release = {
        "kind": "mother-deployment-post-admission-steady-state-release",
        "targets": {
            "mainneta-super1": {
                "steady_state_compose": {
                    "canonical_text": "RELEASE-COMPOSE-SPAM",
                    "sha256": "7" * 64,
                }
            }
        },
        "execution_plan": {
            "mutations": [
                {
                    "canonical_request_body": {"docker_compose_raw": "RELEASE-BODY-SPAM"},
                    "body_sha256": "8" * 64,
                }
            ]
        },
        "summary": {"next_phase_after_apply": "verify-post-admission-steady-state-evidence"},
    }
    monkeypatch.setattr(
        mother_deploy,
        "build_post_admission_steady_state_release",
        lambda *args, **kwargs: fake_release,
    )
    monkeypatch.setattr(
        mother_deploy,
        "write_post_admission_steady_state_release",
        lambda *args, **kwargs: (tmp_path / "release.json", "9" * 64),
    )

    code = mother_deploy.main(
        [
            "release-post-admission-steady-state",
            "--runtime-state-root",
            str(runtime_root),
            "--transaction",
            str(tmp_path / "transaction.json"),
            "--acknowledge-post-admission-steady-state-transaction-sha256",
            "a" * 64,
            "--write-release",
        ]
    )

    assert code == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert "RELEASE-COMPOSE-SPAM" not in output_text
    assert "RELEASE-BODY-SPAM" not in output_text
    assert output["targets"]["mainneta-super1"]["steady_state_compose"]["sha256"] == "7" * 64
    assert output["release_artifact"]["sha256"] == "9" * 64


def test_cli_compacts_post_admission_execution_output(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    paths, _, _, _, _, _, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    monkeypatch.setattr(
        mother_deploy,
        "execute_post_admission_steady_state_release",
        lambda *args, **kwargs: {
            "kind": "mother-deployment-post-admission-steady-state-evidence",
            "status": "pass",
            "network": "mainnet",
            "summary": {"complete": True},
            "mutation_receipts": [{"response": {"status": 200}}],
            "health_observations": [{"phase": "poll-1"}],
            "evidence": {"path": "evidence.json", "sha256": "b" * 64},
        },
    )

    code = mother_deploy.main(
        [
            "apply-post-admission-steady-state",
            "--runtime-state-root",
            str(runtime_root),
            "--release",
            str(tmp_path / "release.json"),
            "--acknowledge-release-sha256",
            "c" * 64,
            "--execute",
        ]
    )

    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert "mutation_receipts" not in output
    assert "health_observations" not in output
    assert output["summary"]["complete"] is True
    assert output["evidence"]["path"] == "evidence.json"



def test_read_only_reconciliation_proves_C_steady_A_recovered_after_blank_inventory_status(
    tmp_path: Path,
) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SteadyStateOpener(release, blank_inventory_status_after_c=True)
    failed = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("post-admission-steady-state-blank-inventory"),
    )
    assert failed["status"] == "failed"
    assert failed["failure"]["code"] == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_NOT_HEALTHY"
    assert [item["mutation_id"] for item in failed["mutation_receipts"]] == [
        "mainnetc-super1.install-post-admission-steady-state",
        "mainnetc-super1.deploy-post-admission-steady-state",
    ]

    request_count = len(opener.requests)
    reconciled = reconcile_post_admission_steady_state(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        operation=_operation("post-admission-steady-state-read-only-reconciliation"),
    )

    assert reconciled["status"] == "pass"
    assert reconciled["summary"]["C_steady_state_verified"] is True
    assert reconciled["summary"]["A_recovered_state_verified"] is True
    assert reconciled["summary"]["chain_continuity_verified"] is True
    assert reconciled["summary"]["next_phase"] == "stage-post-admission-steady-state-continuation"
    target_map = {item["node"]: item for item in reconciled["targets"]}
    assert target_map["mainnetc-super1"]["inventory_status"] == ""
    assert target_map["mainnetc-super1"]["detail_status"] == "running:healthy"
    assert target_map["mainnetc-super1"]["aggregate_status_source"] == "detail"
    assert target_map["mainnetc-super1"]["obsolete_components_absent"] is True
    assert target_map["mainneta-super1"]["expected_mode"] == "recovered"
    reconciliation_requests = opener.requests[request_count:]
    assert reconciliation_requests
    assert all(method == "GET" for _, method, _ in reconciliation_requests)
    assert all(path != "/api/v1/deploy" for _, _, path in reconciliation_requests)
    assert all("/logs" not in path for _, _, path in reconciliation_requests)

    verified = verify_post_admission_steady_state_reconciliation(
        paths,
        private_state,
        Path(reconciled["reconciliation_artifact"]["path"]),
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    assert verified["clean"] is True
    assert verified["C_steady_state_verified"] is True
    assert verified["A_recovered_state_verified"] is True
    assert verified["live_mutation_performed"] is False


def test_runtime_observation_accepts_services_from_exact_recovered_compose() -> None:
    expected_compose = yaml.safe_dump(
        {
            "services": {
                "mainneta-super1": {"image": "hyperledger/besu:latest"},
                "mother-validator-quorum-recovery-initial-guardian": {
                    "image": "python:3.12-alpine"
                },
                "mother-super-node-fdb": {"image": "foundationdb:7.4.6"},
                "mother-super-node-hub": {"image": "hub:test"},
                "mother-superseded-service-cleanup": {"image": "docker:27-cli"},
            }
        },
        sort_keys=True,
    )
    target = {
        "node": "mainneta-super1",
        "controller_id": "coolify-a",
        "service_uuid": "service-a",
        "required_healthy_components": [
            "mainneta-super1",
            "mother-validator-quorum-recovery-initial-guardian",
        ],
        "recognized_obsolete_components": [
            "mother-genesis-init",
            "mother-genesis-proof-guardian",
            "mother-validator-admission-guardian",
        ],
    }

    class _ObservationOpener:
        def open(self, request, timeout: float):  # noqa: ANN001
            parsed = urlsplit(request.full_url)
            if parsed.path == "/api/v1/services":
                return _AdmissionResponse(
                    [{"uuid": "service-a", "name": "mainneta-super1", "status": "degraded:unhealthy"}]
                )
            if parsed.path == "/api/v1/services/service-a":
                applications = [
                    {
                        "uuid": name + "-uuid",
                        "name": name,
                        "status": "exited" if name.startswith("mother-genesis") or name == "mother-validator-admission-guardian" or name == "mother-superseded-service-cleanup" else "running:healthy",
                        "image": "test",
                    }
                    for name in yaml.safe_load(expected_compose)["services"]
                ]
                applications.extend(
                    [
                        {
                            "uuid": "obsolete-proof",
                            "name": "mother-genesis-proof-guardian",
                            "status": "exited",
                            "image": "python:3.12-alpine",
                        },
                        {
                            "uuid": "obsolete-admission",
                            "name": "mother-validator-admission-guardian",
                            "status": "exited",
                            "image": "python:3.12-alpine",
                        },
                    ]
                )
                return _AdmissionResponse(
                    {
                        "service": {
                            "uuid": "service-a",
                            "name": "mainneta-super1",
                            "status": "degraded:unhealthy",
                            "docker_compose_raw": expected_compose,
                            "applications": applications,
                        }
                    }
                )
            raise AssertionError(parsed.path)

    observation = steady_state._runtime_target_observation(
        controller=SimpleNamespace(base_url="https://coolify-a.invalid", api_token=TOKEN_A),
        target=target,
        expected_compose=expected_compose,
        expected_mode="recovered",
        require_aggregate_healthy=False,
        require_obsolete_absent=False,
        timeout=30,
        max_response_bytes=1024 * 1024,
        opener=_ObservationOpener(),
    )

    assert observation["compose_matches"] is True
    assert observation["required_components_healthy"] is True
    assert observation["unexpected_component_records_present"] == []
    assert observation["verified"] is True


def test_read_only_reconciliation_accepts_recognized_exited_coolify_phantom_records(
    tmp_path: Path,
) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SteadyStateOpener(
        release,
        keep_obsolete_c=True,
        degraded_aggregate_with_obsolete_c=True,
    )
    failed = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("post-admission-steady-state-phantom-record-source"),
    )
    assert failed["status"] == "failed"

    reconciled = reconcile_post_admission_steady_state(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        operation=_operation("post-admission-steady-state-phantom-record-reconciliation"),
    )

    assert reconciled["schema_version"] == 2
    assert reconciled["status"] == "pass"
    assert reconciled["summary"]["C_steady_state_verified"] is True
    assert reconciled["summary"]["C_aggregate_service_healthy"] is False
    assert reconciled["summary"]["C_obsolete_components_absent"] is False
    assert reconciled["summary"]["C_obsolete_compose_services_absent"] is True
    assert reconciled["summary"]["C_stale_component_records_all_exited"] is True
    assert reconciled["summary"]["C_strict_aggregate_cleanup_complete"] is False
    assert (
        reconciled["summary"]["aggregate_badge_non_authoritative_for_reconciliation"]
        is True
    )
    target_map = {item["node"]: item for item in reconciled["targets"]}
    c_target = target_map["mainnetc-super1"]
    assert c_target["effective_aggregate_status"] == "degraded:unhealthy"
    assert c_target["compose_matches"] is True
    assert c_target["required_components_healthy"] is True
    assert c_target["obsolete_compose_services_absent"] is True
    assert c_target["recognized_obsolete_component_records_all_exited"] is True
    assert c_target["unexpected_component_records_present"] == []
    assert c_target["component_scoped_verified"] is True
    assert c_target["aggregate_badge_non_authoritative"] is True
    assert c_target["verified"] is True

    verified = verify_post_admission_steady_state_reconciliation(
        paths,
        private_state,
        Path(reconciled["reconciliation_artifact"]["path"]),
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    assert verified["clean"] is True
    assert verified["C_steady_state_verified"] is True
    assert verified["chain_continuity_verified"] is True


def test_read_only_reconciliation_rejects_running_obsolete_component_records(
    tmp_path: Path,
) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SteadyStateOpener(
        release,
        keep_obsolete_c=True,
        degraded_aggregate_with_obsolete_c=True,
        obsolete_c_status="running:healthy",
    )
    failed = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("post-admission-steady-state-running-obsolete-source"),
    )
    reconciled = reconcile_post_admission_steady_state(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        operation=_operation("post-admission-steady-state-running-obsolete-reconciliation"),
    )

    assert reconciled["status"] == "manual-review-required"
    target_map = {item["node"]: item for item in reconciled["targets"]}
    c_target = target_map["mainnetc-super1"]
    assert c_target["recognized_obsolete_component_records_all_exited"] is False
    assert c_target["component_scoped_verified"] is False
    assert c_target["verified"] is False

    with pytest.raises(MotherDeploymentPostAdmissionSteadyStateError) as caught:
        verify_post_admission_steady_state_reconciliation(
            paths,
            private_state,
            Path(reconciled["reconciliation_artifact"]["path"]),
        )
    assert (
        caught.value.code
        == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID"
    )


def test_reconciliation_verifier_rejects_chain_commitment_tampering(tmp_path: Path) -> None:
    paths, private_state, _, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _SteadyStateOpener(release, blank_inventory_status_after_c=True)
    failed = execute_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("post-admission-steady-state-tamper-source"),
    )
    reconciled = reconcile_post_admission_steady_state(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        opener=opener,
        operation=_operation("post-admission-steady-state-tamper-reconciliation"),
    )
    path = Path(reconciled["reconciliation_artifact"]["path"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["chain_id"] += 1
    path.write_bytes(canonical_json(document))

    with pytest.raises(MotherDeploymentPostAdmissionSteadyStateError) as caught:
        verify_post_admission_steady_state_reconciliation(
            paths,
            private_state,
            path,
        )
    assert (
        caught.value.code
        == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_BINDING_MISMATCH"
    )


def test_cli_exposes_read_only_steady_state_reconciliation_commands() -> None:
    parser = mother_deploy._parser()
    reconcile_args = parser.parse_args(
        [
            "reconcile-post-admission-steady-state",
            "--evidence",
            "failed.json",
        ]
    )
    assert reconcile_args.command == "reconcile-post-admission-steady-state"
    verify_args = parser.parse_args(
        [
            "verify-post-admission-steady-state-reconciliation",
            "--reconciliation",
            "reconciliation.json",
        ]
    )
    assert verify_args.command == "verify-post-admission-steady-state-reconciliation"
