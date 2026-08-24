from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

from tools import mother_deploy
from tools.mother.common.deployment_node_add_single_node_bootstrap import (
    verify_node_add_single_node_bootstrap_evidence as verify_v1_evidence,
)
from tools.mother.common.deployment_node_add_single_node_bootstrap_v2 import (
    build_node_add_single_node_bootstrap_release,
    execute_node_add_single_node_bootstrap_release,
    verify_node_add_single_node_bootstrap_evidence,
    write_node_add_single_node_bootstrap_release,
)
from tests.test_mother_deployment_executor import _Response, _operation
from tests.test_mother_deployment_node_add_single_node_bootstrap import (
    _SingleNodeBootstrapOpener,
    _write_empty_topology_identity_evidence,
)


class _ForcedDeployBootstrapOpener(_SingleNodeBootstrapOpener):
    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        if request.get_method() == "POST" and parsed.path == "/api/v1/deploy":
            body = json.loads(request.data.decode("utf-8"))
            self.requests.append(
                {
                    "method": "POST",
                    "host": parsed.hostname,
                    "path": parsed.path,
                    "query": parsed.query,
                    "body": body,
                }
            )
            assert parsed.hostname == "coolify-a.invalid"
            assert parsed.query == ""
            assert body == {"uuid": "svc-a1", "force": True}
            self.service["status"] = self.deploy_status
            return _Response({"deployment_uuid": "deploy-single-node-bootstrap-v2"})
        return super().open(request, timeout)


def _release(tmp_path: Path):
    paths, private_state, identity_evidence_path, identity_evidence_sha = (
        _write_empty_topology_identity_evidence(tmp_path)
    )
    release = build_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-12T01:10:00Z",
        now=datetime(2026, 8, 12, 1, 10, 1, tzinfo=timezone.utc),
    )
    return paths, private_state, release


def test_mother_deploy_selects_single_node_bootstrap_v2_executor() -> None:
    assert (
        mother_deploy.execute_node_add_single_node_bootstrap_release.__module__
        == "tools.mother.common.deployment_node_add_single_node_bootstrap_v2"
    )


def test_single_node_bootstrap_v2_release_forces_deploy_instead_of_start(tmp_path: Path) -> None:
    _paths, _private_state, release = _release(tmp_path)

    mutations = release["bootstrap_plan"]["mutations"]
    assert release["policy"]["compiler"] == "mother-native-add-node-single-node-bootstrap-v2"
    assert mutations[0]["method"] == "PATCH"
    assert mutations[0]["endpoint"] == "/api/v1/services/svc-a1"
    assert mutations[1]["method"] == "POST"
    assert mutations[1]["endpoint"] == "/api/v1/deploy"
    assert mutations[1]["canonical_request_body"] == {"uuid": "svc-a1", "force": True}
    assert mutations[1]["body_sha256"]
    assert not any(item["endpoint"].endswith("/start") for item in mutations)

    compose = release["bootstrap_plan"]["compose"]["canonical_text"]
    guardian = compose.split("  mother-genesis-proof-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    assert "mother-super-node-hub:" in guardian
    assert "condition: service_started" in guardian
    assert "condition: service_healthy" not in guardian


def test_single_node_bootstrap_v2_executes_forced_deploy_and_keeps_v1_evidence_contract(
    tmp_path: Path,
) -> None:
    paths, private_state, release = _release(tmp_path)
    release_path, release_sha = write_node_add_single_node_bootstrap_release(
        paths,
        release,
        operation=_operation("write-single-node-bootstrap-v2-release"),
    )
    opener = _ForcedDeployBootstrapOpener()
    result = execute_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=opener,
        now=datetime(2026, 8, 12, 1, 11, 0, tzinfo=timezone.utc),
        operation=_operation("execute-single-node-bootstrap-v2-release"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["single_node_bootstrap_proven"] is True
    assert not any(
        request["method"] == "POST" and request["path"].endswith("/start")
        for request in opener.requests
    )
    deploy_requests = [
        request
        for request in opener.requests
        if request["method"] == "POST" and request["path"] == "/api/v1/deploy"
    ]
    assert len(deploy_requests) == 1
    assert deploy_requests[0]["query"] == ""
    assert deploy_requests[0]["body"] == {"uuid": "svc-a1", "force": True}
    assert result["mutation_receipts"][1]["method"] == "POST"
    assert result["mutation_receipts"][1]["endpoint"] == "/api/v1/deploy"

    v2_verified = verify_node_add_single_node_bootstrap_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 12, 1, 11, 1, tzinfo=timezone.utc),
    )
    assert v2_verified["clean"] is True

    v1_verified = verify_v1_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 12, 1, 11, 1, tzinfo=timezone.utc),
    )
    assert v1_verified["clean"] is True
