from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_coolify_service_lifecycle_probe import (
    MotherDeploymentCoolifyServiceLifecycleProbeError,
    execute_coolify_service_lifecycle_probe,
    inspect_coolify_service_lifecycle_probe,
    verify_coolify_service_lifecycle_probe_evidence,
)
from tests.test_mother_deployment_executor import TOKEN_A, _operation
from tests.test_mother_deployment_validator_admission import _AdmissionResponse
from tests.test_mother_deployment_validator_rpc_canary import _fixture


class _LifecycleOpener:
    def __init__(
        self,
        *,
        started_status: str = "running:healthy",
        service_list_status_after_start: str | None = None,
        service_detail_status_after_start: str | None = None,
        server_resource_status_after_start: str | None = None,
        emit_runtime_log_marker: bool = True,
        subresource_name: str | None = None,
        include_subresources: bool = True,
        runtime_log_status_after_start: int = 200,
        service_application_log_status_after_start: int | None = None,
        application_log_status_after_start: int | None = None,
        parent_log_status_after_start: int | None = None,
        runtime_log_error_message: str = "Container not found.",
    ) -> None:
        self.started = False
        self.started_status = started_status
        self.service_list_status_after_start = (
            started_status
            if service_list_status_after_start is None
            else service_list_status_after_start
        )
        self.service_detail_status_after_start = (
            started_status
            if service_detail_status_after_start is None
            else service_detail_status_after_start
        )
        self.server_resource_status_after_start = (
            started_status
            if server_resource_status_after_start is None
            else server_resource_status_after_start
        )
        self.emit_runtime_log_marker = emit_runtime_log_marker
        self.subresource_name = subresource_name
        self.include_subresources = include_subresources
        self.runtime_log_status_after_start = runtime_log_status_after_start
        self.service_application_log_status_after_start = (
            runtime_log_status_after_start
            if service_application_log_status_after_start is None
            else service_application_log_status_after_start
        )
        self.application_log_status_after_start = (
            runtime_log_status_after_start
            if application_log_status_after_start is None
            else application_log_status_after_start
        )
        self.parent_log_status_after_start = (
            runtime_log_status_after_start
            if parent_log_status_after_start is None
            else parent_log_status_after_start
        )
        self.runtime_log_error_message = runtime_log_error_message
        self.deleted = False
        self.requests: list[tuple[str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"
        assert timeout > 0
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))

        if method == "GET" and path == "/api/v1/projects/project-a/environments":
            return _AdmissionResponse(
                {"environments": [{"name": "mainnet", "uuid": "mainnet-env-a"}]}
            )

        if method == "POST" and path == "/api/v1/services":
            body = json.loads(request.data.decode("utf-8"))
            assert body["environment_name"] == "mainnet"
            assert body["environment_uuid"] == "mainnet-env-a"
            assert body["instant_deploy"] is False
            assert body["name"].startswith("mother-lifecycle-probe-a-")
            self._probe_name = body["name"]
            compose = base64.b64decode(
                body["docker_compose_raw"], validate=True
            ).decode("utf-8")
            assert "MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY=" in compose
            assert "entrypoint:" in compose
            assert "      - /bin/sh" in compose
            assert "      - -ec" in compose
            assert "exec /bin/sleep " in compose
            assert "healthcheck:" in compose
            assert "kill -0 1" in compose
            assert "/run/mother-probe/ready" in compose
            assert "tmpfs:" in compose
            assert "/run/mother-probe:size=64k,mode=0700" in compose
            assert "ports:" not in compose
            assert "volumes:" not in compose
            assert "secrets:" not in compose
            assert "http://" not in compose
            assert "private_key" not in compose
            return _AdmissionResponse({"uuid": "probe-service-uuid"}, status=201)

        if method == "GET" and path == "/api/v1/services":
            status = (
                self.service_list_status_after_start
                if self.started
                else "exited:unhealthy"
            )
            return _AdmissionResponse(
                [
                    {
                        "uuid": "probe-service-uuid",
                        "name": next(
                            (
                                value
                                for m, value in []
                            ),
                            "mother-lifecycle-probe-a-placeholder",
                        ),
                        "status": status,
                    }
                ]
            )

        if method == "GET" and path == "/api/v1/services/probe-service-uuid":
            status = (
                self.service_detail_status_after_start
                if self.started
                else "exited:unhealthy"
            )
            payload = {
                "uuid": "probe-service-uuid",
                "name": self.probe_name,
                "status": status,
                "config_hash": "config-hash",
            }
            if self.include_subresources:
                payload["applications"] = [
                    {
                        "name": self.expected_sub_service_name,
                        "uuid": "probe-application-uuid",
                        "status": status,
                        "type": "application",
                    }
                ]
                payload["databases"] = []
            return _AdmissionResponse(payload)

        if (
            method == "GET"
            and path
            == (
                "/api/v1/services/probe-service-uuid/applications/"
                "probe-application-uuid/logs"
            )
        ):
            query = parse_qs(parsed.query)
            assert query["lines"] == ["100"]
            assert query["show_timestamps"] == ["false"]
            if not self.started:
                return _AdmissionResponse({"message": "Container not found."}, status=404)
            if self.service_application_log_status_after_start != 200:
                return _AdmissionResponse(
                    {"message": self.runtime_log_error_message},
                    status=self.service_application_log_status_after_start,
                )
            logs = (
                f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={self.probe_name}\n"
                if self.emit_runtime_log_marker
                else "probe started without expected marker\n"
            )
            return _AdmissionResponse({"logs": logs}, status=200)

        if method == "GET" and path == "/api/v1/applications/probe-application-uuid/logs":
            query = parse_qs(parsed.query)
            assert query["lines"] == ["100"]
            if not self.started:
                return _AdmissionResponse({"message": "Container not found."}, status=404)
            if self.application_log_status_after_start != 200:
                return _AdmissionResponse(
                    {"message": self.runtime_log_error_message},
                    status=self.application_log_status_after_start,
                )
            logs = (
                f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={self.probe_name}\n"
                if self.emit_runtime_log_marker
                else "probe started without expected marker\n"
            )
            return _AdmissionResponse({"logs": logs}, status=200)

        if method == "GET" and path == "/api/v1/services/probe-service-uuid/logs":
            query = parse_qs(parsed.query)
            assert query["sub_service_name"] == [self.expected_sub_service_name]
            assert query["lines"] == ["100"]
            assert query["show_timestamps"] == ["false"]
            if not self.started:
                return _AdmissionResponse({"message": "Container not found."}, status=404)
            if self.parent_log_status_after_start != 200:
                return _AdmissionResponse(
                    {"message": self.runtime_log_error_message},
                    status=self.parent_log_status_after_start,
                )
            logs = (
                f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={self.probe_name}\n"
                if self.emit_runtime_log_marker
                else "probe started without expected marker\n"
            )
            return _AdmissionResponse({"logs": logs}, status=200)

        if method == "GET" and path == "/api/v1/deployments":
            return _AdmissionResponse([])

        if method == "GET" and path == "/api/v1/servers/server-a/resources":
            status = (
                self.server_resource_status_after_start
                if self.started
                else "exited:unhealthy"
            )
            return _AdmissionResponse(
                [
                    {
                        "uuid": "probe-service-uuid",
                        "name": self.probe_name,
                        "type": "service",
                        "status": status,
                    }
                ]
            )

        if method == "POST" and path == "/api/v1/services/probe-service-uuid/start":
            self.started = True
            return _AdmissionResponse(
                {"message": "Service starting request queued."},
                status=200,
            )

        if method == "DELETE" and path == "/api/v1/services/probe-service-uuid":
            self.deleted = True
            return _AdmissionResponse({"message": "Service deleted."}, status=200)

        raise AssertionError(f"unexpected request: {method} {path}")

    @property
    def expected_sub_service_name(self) -> str:
        return self.subresource_name or self.probe_name

    @property
    def probe_name(self) -> str:
        create_request = next(
            (
                item
                for item in self.requests
                if item == ("POST", "/api/v1/services")
            ),
            None,
        )
        assert create_request is not None
        # The service name is stable in the fixture; the exact value is provided
        # by the request body assertion, and this placeholder is replaced below.
        return self._probe_name

    _probe_name = "mother-lifecycle-probe-a-placeholder"


def test_lifecycle_probe_inspection_is_no_secret_no_chain(tmp_path: Path, monkeypatch) -> None:
    _, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = inspect_coolify_service_lifecycle_probe(
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        observe_seconds=60,
        poll_interval_seconds=5,
        operation=_operation("coolify-service-lifecycle-inspect"),
    )
    assert result["status"] == "inspection"
    assert result["authority"]["requested_mutation_count"] == 3
    assert result["authority"]["secret_binding_authorized"] is False
    assert result["authority"]["chain_access_authorized"] is False
    assert result["compose"]["entrypoint"] == ["/bin/sh", "-ec"]
    assert result["compose"]["single_script_argument"] is True
    assert result["compose"]["healthcheck_required"] is True
    assert result["compose"]["runtime_log_marker_required"] is True
    assert result["compose"]["runtime_result_channel_required"] is True
    assert result["compose"]["health_bound_marker_fallback_allowed"] is True
    assert result["compose"]["health_bound_marker_file"] == "/run/mother-probe/ready"
    assert result["compose"]["tmpfs"] == ["/run/mother-probe:size=64k,mode=0700"]
    assert result["compose"]["image"] == "alpine:3.20"
    assert result["compose"]["runtime_log_endpoint_template"] == (
        "/api/v1/services/{service_uuid}/applications/{application_uuid}/logs"
        "?lines=100&show_timestamps=false"
    )
    assert result["compose"]["runtime_log_alternate_endpoint_template"] == (
        "/api/v1/applications/{application_uuid}/logs?lines=100"
    )
    assert result["compose"]["runtime_log_fallback_endpoint_template"] == (
        "/api/v1/services/{service_uuid}/logs?sub_service_name={service_name}"
    )
    assert result["compose"]["ports"] == []
    assert result["compose"]["volumes"] == []
    assert result["compose"]["secrets"] == []
    assert result["summary"]["live_mutation_performed"] is False
    assert result["required_acknowledgement"] == (
        "NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE"
    )


def test_lifecycle_probe_executes_and_persists_observation_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    opener = _LifecycleOpener()
    operation = _operation("coolify-service-lifecycle-execute")
    expected_name = (
        "mother-lifecycle-probe-a-"
        + __import__("hashlib").sha256(
            operation.operation_id.encode("utf-8")
        ).hexdigest()[:10]
    )
    opener._probe_name = expected_name

    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=operation,
    )
    assert result["status"] == "pass"
    assert result["probe_name"] == expected_name
    assert result["service_uuid"] == "probe-service-uuid"
    assert result["summary"]["temporary_service_deleted"] is True
    assert result["summary"]["service_detail_record_observed"] is True
    assert result["summary"]["server_resource_record_observed"] is True
    assert result["summary"]["deployment_record_observed"] is False
    assert "running:healthy" in result["summary"]["observed_status_values"]
    assert result["summary"]["healthy_running_observed"] is True
    assert result["summary"]["healthy_running_status_values"] == ["running:healthy"]
    assert result["summary"]["image_entrypoint_override_verified"] is True
    assert result["summary"]["single_script_argument_verified"] is True
    assert result["summary"]["runtime_result_channel_observed"] is True
    assert result["summary"]["runtime_result_channel"] == "runtime-log-marker"
    assert result["summary"]["runtime_health_marker_contract_verified"] is True
    assert result["summary"]["health_bound_runtime_marker_observed"] is True
    assert result["summary"]["health_bound_marker_file"] == "/run/mother-probe/ready"
    assert result["summary"]["service_runtime_log_endpoint_observed"] is True
    assert result["summary"]["service_runtime_log_marker_observed"] is True
    assert result["summary"]["runtime_log_marker"] == (
        f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={expected_name}"
    )
    assert opener.deleted is True

    evidence_path = Path(result["evidence"]["path"])
    raw = evidence_path.read_text(encoding="utf-8")
    assert TOKEN_A not in raw
    assert "private_key" not in raw
    evidence = json.loads(raw)
    assert evidence["schema_version"] == 7
    assert evidence["kind"].endswith(".v7")
    assert [item["mutation_id"].rsplit(".", 1)[-1] for item in evidence["mutation_receipts"]] == [
        "create",
        "start",
        "delete",
    ]
    runtime_channels = [
        channel
        for observation in evidence["observations"]
        for channel in observation["channels"]
        if channel.get("channel") == "service-runtime-logs"
    ]
    post_start_runtime = next(
        channel
        for channel in runtime_channels
        if channel["observation_sequence"] == 1
    )
    assert post_start_runtime["observation_started_at"]
    assert post_start_runtime["service_detail_http_status"] == 200
    assert post_start_runtime["service_detail_status"] == "running:healthy"
    assert post_start_runtime["subresource_collection_shapes"] == {
        "applications": "list",
        "databases": "list",
    }
    assert post_start_runtime["subresources"]["applications"] == [
        {
            "name": expected_name,
            "status": "running:healthy",
            "type": "application",
            "uuid": "probe-application-uuid",
        }
    ]
    assert post_start_runtime["subresources"]["databases"] == []
    assert post_start_runtime["candidate_sub_service_names"] == [expected_name]
    assert post_start_runtime["selected_sub_service_name"] == expected_name
    assert post_start_runtime["selected_application_uuid"] == "probe-application-uuid"
    assert post_start_runtime["selection_reason"] == "expected-name-match"
    assert post_start_runtime["endpoint_kind"] == "service-application"
    assert post_start_runtime["query_parameters"] == {
        "lines": "100",
        "show_timestamps": "false",
    }
    assert post_start_runtime["request_path"] == (
        "/api/v1/services/probe-service-uuid/applications/"
        "probe-application-uuid/logs?lines=100&show_timestamps=false"
    )
    assert post_start_runtime["attempts"][-1]["endpoint_kind"] == "service-application"
    assert post_start_runtime["response_classification"] == "ok-logs"
    assert post_start_runtime["logs_field_present"] is True

    verified = verify_coolify_service_lifecycle_probe_evidence(
        paths,
        private_state,
        evidence_path,
    )
    assert verified["clean"] is True
    assert verified["temporary_service_deleted"] is True
    assert verified["deployment_record_observed"] is False
    assert verified["healthy_running_observed"] is True
    assert verified["healthy_running_status_values"] == ["running:healthy"]
    assert verified["image_entrypoint_override_verified"] is True
    assert verified["single_script_argument_verified"] is True
    assert verified["runtime_result_channel_observed"] is True
    assert verified["runtime_result_channel"] == "runtime-log-marker"
    assert verified["runtime_health_marker_contract_verified"] is True
    assert verified["health_bound_runtime_marker_observed"] is True
    assert verified["health_bound_marker_file"] == "/run/mother-probe/ready"
    assert verified["service_runtime_log_endpoint_observed"] is True
    assert verified["service_runtime_log_marker_observed"] is True
    assert verified["service_runtime_log_response_classifications"] == [
        "container-not-found",
        "ok-logs",
    ]
    assert verified["runtime_log_marker"] == (
        f"MOTHER_COOLIFY_SERVICE_LIFECYCLE_PROBE_READY={expected_name}"
    )
    assert verified["validator_mutation_count"] == 0


def test_lifecycle_probe_verifier_keeps_schema_v3_evidence_compatible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(),
        operation=_operation("coolify-service-lifecycle-legacy-v3"),
    )
    evidence_path = Path(result["evidence"]["path"])
    legacy = json.loads(evidence_path.read_text(encoding="utf-8"))
    legacy["kind"] = (
        "main_computer.mother."
        "deployment_coolify_service_lifecycle_probe_evidence.v3"
    )
    legacy["schema_version"] = 3
    legacy["summary"].pop("service_runtime_log_response_classifications")
    legacy.pop("coolify_service_lifecycle_probe_evidence_sha256")
    legacy_digest = hashlib.sha256(canonical_json(legacy)).hexdigest()
    legacy["coolify_service_lifecycle_probe_evidence_sha256"] = legacy_digest
    legacy_path = evidence_path.with_name("legacy-schema-v3.json")
    legacy_path.write_bytes(canonical_json(legacy))

    verified = verify_coolify_service_lifecycle_probe_evidence(
        paths,
        private_state,
        legacy_path,
    )
    assert verified["clean"] is True
    assert verified["service_runtime_log_response_classifications"] is None


def test_lifecycle_probe_verifier_keeps_schema_v4_evidence_compatible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(),
        operation=_operation("coolify-service-lifecycle-legacy-v4"),
    )
    evidence_path = Path(result["evidence"]["path"])
    legacy = json.loads(evidence_path.read_text(encoding="utf-8"))
    legacy["kind"] = (
        "main_computer.mother."
        "deployment_coolify_service_lifecycle_probe_evidence.v4"
    )
    legacy["schema_version"] = 4
    legacy.pop("coolify_service_lifecycle_probe_evidence_sha256")
    legacy_digest = hashlib.sha256(canonical_json(legacy)).hexdigest()
    legacy["coolify_service_lifecycle_probe_evidence_sha256"] = legacy_digest
    legacy_path = evidence_path.with_name("legacy-schema-v4.json")
    legacy_path.write_bytes(canonical_json(legacy))

    verified = verify_coolify_service_lifecycle_probe_evidence(
        paths,
        private_state,
        legacy_path,
    )
    assert verified["clean"] is True
    assert verified["service_runtime_log_response_classifications"] == [
        "container-not-found",
        "ok-logs",
    ]


def test_lifecycle_probe_verifier_keeps_schema_v5_evidence_compatible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(),
        operation=_operation("coolify-service-lifecycle-legacy-v5"),
    )
    evidence_path = Path(result["evidence"]["path"])
    legacy = json.loads(evidence_path.read_text(encoding="utf-8"))
    legacy["kind"] = (
        "main_computer.mother."
        "deployment_coolify_service_lifecycle_probe_evidence.v5"
    )
    legacy["schema_version"] = 5
    legacy.pop("coolify_service_lifecycle_probe_evidence_sha256")
    legacy_digest = hashlib.sha256(canonical_json(legacy)).hexdigest()
    legacy["coolify_service_lifecycle_probe_evidence_sha256"] = legacy_digest
    legacy_path = evidence_path.with_name("legacy-schema-v5.json")
    legacy_path.write_bytes(canonical_json(legacy))

    verified = verify_coolify_service_lifecycle_probe_evidence(
        paths,
        private_state,
        legacy_path,
    )
    assert verified["clean"] is True
    assert verified["service_runtime_log_response_classifications"] == [
        "container-not-found",
        "ok-logs",
    ]


def test_lifecycle_probe_verifier_keeps_schema_v6_evidence_compatible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(),
        operation=_operation("coolify-service-lifecycle-legacy-v6"),
    )
    evidence_path = Path(result["evidence"]["path"])
    legacy = json.loads(evidence_path.read_text(encoding="utf-8"))
    legacy["kind"] = (
        "main_computer.mother."
        "deployment_coolify_service_lifecycle_probe_evidence.v6"
    )
    legacy["schema_version"] = 6
    legacy.pop("coolify_service_lifecycle_probe_evidence_sha256")
    legacy_digest = hashlib.sha256(canonical_json(legacy)).hexdigest()
    legacy["coolify_service_lifecycle_probe_evidence_sha256"] = legacy_digest
    legacy_path = evidence_path.with_name("legacy-schema-v6.json")
    legacy_path.write_bytes(canonical_json(legacy))

    verified = verify_coolify_service_lifecycle_probe_evidence(
        paths,
        private_state,
        legacy_path,
    )
    assert verified["clean"] is True
    assert verified["service_runtime_log_marker_observed"] is True


def test_lifecycle_probe_uses_the_single_service_detail_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    opener = _LifecycleOpener(subresource_name="runtime-container-name")
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("coolify-service-lifecycle-single-candidate"),
    )
    assert result["status"] == "pass"

    evidence = json.loads(Path(result["evidence"]["path"]).read_text(encoding="utf-8"))
    runtime_channels = [
        channel
        for observation in evidence["observations"]
        for channel in observation["channels"]
        if channel.get("channel") == "service-runtime-logs"
    ]
    assert all(
        channel["candidate_sub_service_names"] == ["runtime-container-name"]
        for channel in runtime_channels
    )
    assert all(
        channel["selected_sub_service_name"] == "runtime-container-name"
        for channel in runtime_channels
    )
    assert all(
        channel["selection_reason"] == "single-candidate"
        for channel in runtime_channels
    )


def test_lifecycle_probe_falls_back_to_application_resource_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    opener = _LifecycleOpener(
        service_application_log_status_after_start=404,
        application_log_status_after_start=200,
    )
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("coolify-service-lifecycle-application-fallback"),
    )
    assert result["status"] == "pass"
    evidence = json.loads(Path(result["evidence"]["path"]).read_text(encoding="utf-8"))
    post_start_runtime = next(
        channel
        for observation in evidence["observations"]
        for channel in observation["channels"]
        if channel.get("channel") == "service-runtime-logs"
        and channel["observation_sequence"] == 1
    )
    assert [attempt["endpoint_kind"] for attempt in post_start_runtime["attempts"]] == [
        "service-application",
        "application-resource",
    ]
    assert post_start_runtime["endpoint_kind"] == "application-resource"
    assert post_start_runtime["marker_observed"] is True


def test_lifecycle_probe_uses_health_bound_marker_when_all_log_routes_return_404(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    opener = _LifecycleOpener(
        runtime_log_status_after_start=404,
        runtime_log_error_message="Container not found.",
    )
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("coolify-service-lifecycle-log-404"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["healthy_running_observed"] is True
    assert result["summary"]["runtime_result_channel_observed"] is True
    assert result["summary"]["runtime_result_channel"] == "health-bound-marker"
    assert result["summary"]["runtime_health_marker_contract_verified"] is True
    assert result["summary"]["health_bound_runtime_marker_observed"] is True
    assert result["summary"]["health_bound_marker_file"] == "/run/mother-probe/ready"
    assert result["summary"]["service_list_healthy_running_observed"] is True
    assert result["summary"]["service_detail_healthy_running_observed"] is True
    assert result["summary"]["server_resource_healthy_running_observed"] is True
    assert result["summary"]["service_runtime_log_endpoint_observed"] is False
    assert result["summary"]["service_runtime_log_marker_observed"] is False
    assert result["summary"]["service_runtime_log_response_classifications"] == [
        "container-not-found"
    ]
    assert result["summary"]["temporary_service_deleted"] is True

    raw = Path(result["evidence"]["path"]).read_text(encoding="utf-8")
    assert "Container not found." not in raw
    evidence = json.loads(raw)
    post_start_runtime = next(
        channel
        for observation in evidence["observations"]
        for channel in observation["channels"]
        if channel.get("channel") == "service-runtime-logs"
        and channel["observation_sequence"] == 1
    )
    assert post_start_runtime["service_detail_status"] == "running:healthy"
    assert post_start_runtime["selected_sub_service_name"] == opener.probe_name
    assert post_start_runtime["selected_application_uuid"] == "probe-application-uuid"
    assert post_start_runtime["http_status"] == 404
    assert post_start_runtime["response_classification"] == "container-not-found"
    assert [attempt["endpoint_kind"] for attempt in post_start_runtime["attempts"]] == [
        "service-application",
        "application-resource",
        "parent-service-fallback",
    ]
    assert post_start_runtime["response_sha256"]
    assert post_start_runtime["byte_length"] > 0
    assert post_start_runtime["logs_field_present"] is False
    assert post_start_runtime["marker_observed"] is False

    verified = verify_coolify_service_lifecycle_probe_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
    )
    assert verified["clean"] is True
    assert verified["runtime_result_channel"] == "health-bound-marker"
    assert verified["health_bound_runtime_marker_observed"] is True


def test_lifecycle_probe_verifier_rejects_tampered_health_bound_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(runtime_log_status_after_start=404),
        operation=_operation("coolify-service-lifecycle-health-bound-tamper"),
    )
    evidence_path = Path(result["evidence"]["path"])
    tampered = json.loads(evidence_path.read_text(encoding="utf-8"))
    tampered["summary"]["tmpfs_mount_count"] = 0
    tampered.pop("coolify_service_lifecycle_probe_evidence_sha256")
    tampered_digest = hashlib.sha256(canonical_json(tampered)).hexdigest()
    tampered["coolify_service_lifecycle_probe_evidence_sha256"] = tampered_digest
    tampered_path = evidence_path.with_name("tampered-health-bound.json")
    tampered_path.write_bytes(canonical_json(tampered))

    with pytest.raises(
        MotherDeploymentCoolifyServiceLifecycleProbeError,
        match="cleanup contract",
    ):
        verify_coolify_service_lifecycle_probe_evidence(
            paths,
            private_state,
            tampered_path,
        )


def test_lifecycle_probe_records_missing_service_detail_subresources_without_log_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    opener = _LifecycleOpener(include_subresources=False)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("coolify-service-lifecycle-no-subresources"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["healthy_running_observed"] is True
    assert result["summary"]["runtime_result_channel"] == "health-bound-marker"
    assert result["summary"]["health_bound_runtime_marker_observed"] is True
    assert result["summary"]["service_runtime_log_endpoint_observed"] is False
    assert result["summary"]["service_runtime_log_response_classifications"] == [
        "sub-service-name-unresolved"
    ]
    assert not any(
        method == "GET" and path.endswith("/logs")
        for method, path in opener.requests
    )

    evidence = json.loads(Path(result["evidence"]["path"]).read_text(encoding="utf-8"))
    post_start_runtime = next(
        channel
        for observation in evidence["observations"]
        for channel in observation["channels"]
        if channel.get("channel") == "service-runtime-logs"
        and channel["observation_sequence"] == 1
    )
    assert post_start_runtime["subresource_collection_shapes"] == {
        "applications": "missing-or-null",
        "databases": "missing-or-null",
    }
    assert post_start_runtime["candidate_sub_service_names"] == []
    assert post_start_runtime["selected_sub_service_name"] is None
    assert post_start_runtime["selection_reason"] == "no-candidates"
    assert post_start_runtime["request_path"] is None
    assert post_start_runtime["response_classification"] == (
        "sub-service-name-unresolved"
    )


def test_lifecycle_probe_accepts_coolify_excluded_healthy_running_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(started_status="running:healthy:excluded"),
        operation=_operation("coolify-service-lifecycle-excluded-healthy"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["healthy_running_observed"] is True
    assert result["summary"]["healthy_running_status_values"] == [
        "running:healthy:excluded"
    ]
    assert "running:healthy:excluded" in result["summary"]["observed_status_values"]
    assert result["summary"]["temporary_service_deleted"] is True


def test_lifecycle_probe_fails_closed_without_healthy_running_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(started_status="exited"),
        operation=_operation("coolify-service-lifecycle-unhealthy"),
    )
    assert result["status"] == "manual-review-required"
    assert result["summary"]["healthy_running_observed"] is False
    assert result["summary"]["temporary_service_deleted"] is True



def test_lifecycle_probe_health_bound_marker_requires_all_inventory_channels_healthy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(
            runtime_log_status_after_start=404,
            service_detail_status_after_start="running:unhealthy",
        ),
        operation=_operation("coolify-service-lifecycle-partial-health"),
    )
    assert result["status"] == "manual-review-required"
    assert result["summary"]["healthy_running_observed"] is True
    assert result["summary"]["service_list_healthy_running_observed"] is True
    assert result["summary"]["service_detail_healthy_running_observed"] is False
    assert result["summary"]["server_resource_healthy_running_observed"] is True
    assert result["summary"]["health_bound_runtime_marker_observed"] is False
    assert result["summary"]["runtime_result_channel_observed"] is False
    assert result["summary"]["temporary_service_deleted"] is True


def test_lifecycle_probe_uses_health_bound_marker_when_log_body_lacks_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    result = execute_coolify_service_lifecycle_probe(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        environment_name="mainnet",
        acknowledged_probe="NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE",
        observe_seconds=0,
        poll_interval_seconds=0,
        opener=_LifecycleOpener(emit_runtime_log_marker=False),
        operation=_operation("coolify-service-lifecycle-missing-runtime-marker"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["healthy_running_observed"] is True
    assert result["summary"]["runtime_result_channel"] == "health-bound-marker"
    assert result["summary"]["health_bound_runtime_marker_observed"] is True
    assert result["summary"]["service_runtime_log_endpoint_observed"] is True
    assert result["summary"]["service_runtime_log_marker_observed"] is False
    assert result["summary"]["temporary_service_deleted"] is True


def test_lifecycle_probe_rejects_missing_acknowledgement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, *_ = _fixture(tmp_path, monkeypatch)
    with pytest.raises(
        MotherDeploymentCoolifyServiceLifecycleProbeError,
        match="acknowledgement",
    ):
        execute_coolify_service_lifecycle_probe(
            paths,
            private_state,
            network="mainnet",
            controller_id="coolify-a",
            environment_name="mainnet",
            acknowledged_probe="",
            observe_seconds=0,
            poll_interval_seconds=0,
            opener=_LifecycleOpener(),
            operation=_operation("coolify-service-lifecycle-no-ack"),
        )


def test_lifecycle_probe_cli_is_exposed(capsys) -> None:
    with pytest.raises(SystemExit) as probe_exit:
        mother_deploy.main(["probe-coolify-service-lifecycle", "--help"])
    assert probe_exit.value.code == 0
    probe_help = capsys.readouterr().out
    assert "--controller-id" in probe_help
    assert "--acknowledge-live-service-probe" in probe_help

    with pytest.raises(SystemExit) as verify_exit:
        mother_deploy.main(
            ["verify-coolify-service-lifecycle-probe-evidence", "--help"]
        )
    assert verify_exit.value.code == 0
    assert "--evidence" in capsys.readouterr().out
