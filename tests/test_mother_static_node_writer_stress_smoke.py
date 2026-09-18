from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import tools.mother_bootnode_precleanup as bootnode
import tools.mother_static_node_writer_stress_smoke as smoke


def _snapshot(service_status: str, app_status: str = "exited") -> dict:
    return {
        "service_status": service_status,
        "service_records": [
            {
                "path": "$",
                "uuid": "writer-1",
                "name": "writer",
                "status": service_status,
            },
            {
                "path": "$.applications[0]",
                "uuid": "app-1",
                "name": "writer",
                "image": bootnode.WRITER_IMAGE,
                "status": app_status,
            },
        ],
    }


def test_source_has_no_ssh_or_host_shell_dependency() -> None:
    source = Path(smoke.__file__).read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert "from subprocess" not in source
    assert "['ssh'" not in source
    assert '["ssh"' not in source
    assert "--ssh-host" not in source


def test_build_action_uses_exact_production_writer_shape() -> None:
    action = smoke._build_action(
        iteration=7,
        target_container="mother-writer-stress-container-test",
        controller_id="coolify-c",
    )
    compose = bootnode._writer_service_compose(
        "mother-static-node-writer-stress-test",
        action,
    )

    assert action["action"] == "write-static-nodes"
    assert action["path"] == bootnode.STATIC_NODES_PATH
    assert action["static_node_count"] == 2
    assert len(action["static_nodes_sha256"]) == 64
    assert f"image: {bootnode.WRITER_IMAGE}" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose
    assert bootnode.WRITER_HEALTH_PATH in compose
    assert action["container_name"] in compose
    assert "write-static-nodes-failed" in compose


def test_observer_is_separate_and_does_not_modify_production_writer_compose() -> None:
    writer_name = "mother-static-node-writer-stress-test"
    observer_name = "mother-writer-stress-observer-test"
    action = smoke._build_action(
        iteration=3,
        target_container="mother-writer-stress-container-test",
        controller_id="coolify-c",
    )

    production_compose = bootnode._writer_service_compose(writer_name, action)
    writer_parsed = yaml.safe_load(production_compose)
    observer_parsed = yaml.safe_load(
        smoke._observer_compose(
            observer_service_name=observer_name,
            writer_service_name=writer_name,
        )
    )

    assert list(writer_parsed["services"]) == [writer_name]
    assert list(observer_parsed["services"]) == [observer_name]
    observer = observer_parsed["services"][observer_name]
    assert observer["image"] == bootnode.WRITER_IMAGE
    assert "/var/run/docker.sock:/var/run/docker.sock" in observer["volumes"]
    command = observer["command"][2]
    assert "com.docker.compose.service=$$writer_service" in command
    assert "docker cp" in command
    assert "/proof/failed.json" in command
    assert "/proof/result.json" in command
    assert "docker logs" in command
    assert writer_name in command


def test_observer_parser_decodes_writer_logs_and_proof() -> None:
    import base64

    writer_log = "MOTHER_BOOTNODE_PRECLEANUP_WRITER_DIAGNOSTIC phase=script_failed reason=target-container-not-found\n"
    failed_json = '{"status":"failed","reason":"target-container-not-found"}\n'
    encoded_log = base64.b64encode(writer_log.encode()).decode()
    encoded_failed = base64.b64encode(failed_json.encode()).decode()
    logs = "\n".join(
        [
            f"{smoke.OBSERVER_LOG_MARKER} phase=writer-state cid=abc status=exited exit_code=20 oom_killed=false state_b64=",
            f"{smoke.OBSERVER_LOG_MARKER} phase=writer-logs cid=abc content_b64={encoded_log}",
            f"{smoke.OBSERVER_LOG_MARKER} phase=proof file=failed.json content_b64={encoded_failed}",
        ]
    )

    parsed = smoke._parse_observer_logs(logs)

    assert parsed["writer_logs"] == [writer_log]
    assert parsed["proof_files"]["failed.json"] == failed_json
    assert parsed["events"][0]["phase"] == "writer-state"
    assert parsed["events"][0]["exit_code"] == "20"


def test_disposable_target_is_api_visible_and_verifies_exact_payload_sha() -> None:
    compose = smoke._target_compose(
        target_service_name="mother-writer-stress-target-test",
        target_container="mother-writer-stress-container-test",
        expected_sha256="a" * 64,
    )
    parsed = yaml.safe_load(compose)

    services = parsed["services"]
    target = services["mother-writer-stress-target-test"]
    verifier = services[smoke._verifier_name("mother-writer-stress-target-test")]

    assert target["container_name"] == "mother-writer-stress-container-test"
    assert "data:/var/lib/besu" in target["volumes"]
    assert "data:/var/lib/besu:ro" in verifier["volumes"]
    verifier_command = verifier["command"][2]
    assert "sha256sum /var/lib/besu/static-nodes.json" in verifier_command
    assert smoke.VERIFY_LOG_MARKER in verifier_command
    assert "ports" not in target
    assert "ports" not in verifier


def test_target_materialization_uses_application_identity_not_container_name() -> None:
    service_name = "mother-writer-stress-target-test"
    snapshot = {
        "service_status": "running:healthy:excluded",
        "service_records": [
            {
                "path": "$",
                "uuid": "service-1",
                "name": service_name,
                "container_name": None,
                "status": "running:healthy:excluded",
            },
            {
                "path": "$.applications[0]",
                "uuid": "app-1",
                "name": service_name,
                "image": smoke.TARGET_IMAGE,
                "container_name": None,
                "status": "running:healthy:excluded",
            },
        ],
    }

    assert smoke._target_materialized(snapshot, service_name) is True


def test_target_materialization_rejects_parent_only_health() -> None:
    service_name = "mother-writer-stress-target-test"
    snapshot = {
        "service_status": "running:healthy:excluded",
        "service_records": [
            {
                "path": "$",
                "uuid": "service-1",
                "name": service_name,
                "status": "running:healthy:excluded",
            },
            {
                "path": "$.applications[0]",
                "uuid": "app-1",
                "name": service_name,
                "image": smoke.TARGET_IMAGE,
                "status": "exited",
            },
        ],
    }

    assert smoke._target_materialized(snapshot, service_name) is False


def test_target_readiness_uses_verifier_marker_even_when_coolify_status_is_stale_exited() -> None:
    service_name = "mother-writer-stress-target-test"
    verifier_name = smoke._verifier_name(service_name)
    original_detail = smoke._detail
    original_runtime_logs = smoke._runtime_logs
    captured_application_names: list[str | None] = []
    try:
        smoke._detail = lambda **_kwargs: (
            {"payload": {"applications": []}},
            {
                "observed_at": "2026-09-17T22:06:48Z",
                "service_status": "exited",
                "service_records": [
                    {
                        "path": "$.applications[1]",
                        "uuid": "verifier-app-1",
                        "name": verifier_name,
                        "image": smoke.TARGET_IMAGE,
                        "status": "exited",
                    }
                ],
            },
        )

        def fake_runtime_logs(**kwargs: Any) -> dict[str, Any]:
            captured_application_names.append(kwargs.get("application_name"))
            return {
                "attempts": [
                    {
                        "status": 200,
                        "logs": (
                            "2026-09-17T22:06:36Z "
                            f"{smoke.VERIFY_LOG_MARKER}missing"
                        ),
                    }
                ]
            }

        smoke._runtime_logs = fake_runtime_logs

        ready, observations = smoke._wait_for_target_start_marker(
            controller=object(),
            service_uuid="target-uuid",
            service_name=service_name,
            timeout=1.0,
            max_response_bytes=1024,
            wait_seconds=0.0,
            poll_interval_seconds=0.0,
            opener=object(),
            sleeper=lambda _seconds: None,
        )
    finally:
        smoke._detail = original_detail
        smoke._runtime_logs = original_runtime_logs

    assert ready is True
    assert captured_application_names == [verifier_name]
    assert len(observations) == 1
    assert observations[0]["service_status"] == "exited"
    assert observations[0]["expected_marker"] == smoke.VERIFY_LOG_MARKER
    assert observations[0]["expected_marker_observed"] is True


def test_target_readiness_fails_closed_without_verifier_marker() -> None:
    service_name = "mother-writer-stress-target-test"
    verifier_name = smoke._verifier_name(service_name)
    original_detail = smoke._detail
    original_runtime_logs = smoke._runtime_logs
    try:
        smoke._detail = lambda **_kwargs: (
            {"payload": {"applications": []}},
            {
                "observed_at": "2026-09-17T22:08:08Z",
                "service_status": "running:healthy:excluded",
                "service_records": [
                    {
                        "path": "$.applications[1]",
                        "uuid": "verifier-app-1",
                        "name": verifier_name,
                        "image": smoke.TARGET_IMAGE,
                        "status": "running:healthy:excluded",
                    }
                ],
            },
        )
        smoke._runtime_logs = lambda **_kwargs: {
            "attempts": [{"status": 200, "logs": "verifier log without SHA marker"}]
        }

        ready, observations = smoke._wait_for_target_start_marker(
            controller=object(),
            service_uuid="target-uuid",
            service_name=service_name,
            timeout=1.0,
            max_response_bytes=1024,
            wait_seconds=0.0,
            poll_interval_seconds=0.0,
            opener=object(),
            sleeper=lambda _seconds: None,
        )
    finally:
        smoke._detail = original_detail
        smoke._runtime_logs = original_runtime_logs

    assert ready is False
    assert len(observations) == 1
    assert observations[0]["service_status"] == "running:healthy:excluded"
    assert observations[0]["expected_marker_observed"] is False



def test_finalized_target_container_name_comes_from_coolify_compose() -> None:
    service_name = "mother-writer-stress-target-test"
    payload = {
        "docker_compose": yaml.safe_dump(
            {
                "services": {
                    service_name: {
                        "image": smoke.TARGET_IMAGE,
                        "container_name": f"{service_name}-coolifyuuid",
                    }
                }
            },
            sort_keys=False,
        ),
        "docker_compose_raw": yaml.safe_dump(
            {
                "services": {
                    service_name: {
                        "image": smoke.TARGET_IMAGE,
                        "container_name": "mother-writer-stress-container-requested",
                    }
                }
            },
            sort_keys=False,
        ),
    }

    assert smoke._container_name_from_finalized_compose(
        payload,
        target_service_name=service_name,
    ) == f"{service_name}-coolifyuuid"


def test_finalized_target_container_name_never_falls_back_to_raw_compose() -> None:
    service_name = "mother-writer-stress-target-test"
    payload = {
        "docker_compose_raw": yaml.safe_dump(
            {
                "services": {
                    service_name: {
                        "container_name": "mother-writer-stress-container-requested",
                    }
                }
            },
            sort_keys=False,
        )
    }

    assert smoke._container_name_from_finalized_compose(
        payload,
        target_service_name=service_name,
    ) is None


def test_production_writer_uses_resolved_target_container_identity() -> None:
    resolved = "mother-writer-stress-target-test-coolifyuuid"
    action = smoke._build_action(
        iteration=1,
        target_container=resolved,
        controller_id="coolify-c",
    )
    compose = bootnode._writer_service_compose(
        "mother-static-node-writer-stress-test",
        action,
    )

    assert action["container_name"] == resolved
    assert resolved in compose
    assert "mother-writer-stress-container-requested" not in compose

def test_failure_signature_matches_observed_incident() -> None:
    polls = [
        {"service_status": "exited", "service_detail_snapshot": _snapshot("exited")},
        {
            "service_status": "starting:unhealthy",
            "service_detail_snapshot": _snapshot("starting:unhealthy"),
        },
        {"service_status": "exited", "service_detail_snapshot": _snapshot("exited")},
    ]

    signature = smoke._failure_signature(polls)

    assert signature["saw_starting_unhealthy"] is True
    assert signature["saw_application_exited"] is True
    assert signature["saw_application_running"] is False
    assert signature["saw_running_healthy"] is False
    assert signature["matches_observed_incident"] is True


def test_failure_signature_uses_transition_inventory_between_polls() -> None:
    polls = [
        {"service_status": "exited", "service_detail_snapshot": _snapshot("exited")},
        {"service_status": "exited", "service_detail_snapshot": _snapshot("exited")},
    ]
    diagnostics = [
        {
            "inventory": [
                {
                    "channel": "server-resources",
                    "matches": [
                        {
                            "uuid": "writer-1",
                            "name": "writer",
                            "type": "service",
                            "status": "starting:unhealthy",
                        }
                    ],
                }
            ]
        }
    ]

    signature = smoke._failure_signature(polls, diagnostics)

    assert signature["service_statuses"] == ["exited", "exited"]
    assert signature["diagnostic_service_statuses"] == ["starting:unhealthy"]
    assert signature["saw_starting_unhealthy"] is True
    assert signature["matches_observed_incident"] is True


def test_failure_signature_rejects_eventual_running_application() -> None:
    polls = [
        {
            "service_status": "starting:unhealthy",
            "service_detail_snapshot": _snapshot("starting:unhealthy"),
        },
        {
            "service_status": "running:healthy",
            "service_detail_snapshot": _snapshot("running:healthy", "running:healthy"),
        },
    ]

    signature = smoke._failure_signature(polls)

    assert signature["saw_application_running"] is True
    assert signature["saw_running_healthy"] is True
    assert signature["matches_observed_incident"] is False


def test_parser_preserves_failed_resources_by_default() -> None:
    args = smoke._build_parser().parse_args([])

    assert args.cleanup_failed is False
    assert args.continue_after_failure is False
    assert args.iterations == smoke.DEFAULT_ITERATIONS
    assert args.controller == "coolify-c"
    assert not hasattr(args, "ssh_host")


def test_live_run_requires_explicit_mutation_gate() -> None:
    try:
        smoke.run_stress(
            network="mainnet",
            controller_id="coolify-c",
            runtime_state_root="runtime/state",
            iterations=1,
            execute=False,
            allow_mutation=False,
            cleanup_failed=False,
            continue_after_failure=False,
            timeout=15.0,
            max_response_bytes=1024,
            target_wait_seconds=1.0,
            writer_wait_seconds=1.0,
            verify_wait_seconds=1.0,
            poll_interval_seconds=0.1,
        )
    except smoke.WriterStressSmokeError as exc:
        assert "--execute and --allow-mutation" in str(exc)
    else:
        raise AssertionError("expected mutation gate failure")


def test_failure_followup_points_to_twiddle_and_read_only_host_inspection() -> None:
    result = {
        "iteration": 1,
        "started_at": "2026-09-17T21:15:38Z",
        "completed_at": "2026-09-17T21:16:40Z",
        "status": "failed",
        "reason": "target-not-materialized",
        "target": {
            "service_name": "mother-writer-stress-target-test",
        },
        "observer": {
            "service_name": "mother-writer-stress-observer-test",
        },
        "writer": {
            "service_name": "mother-static-node-writer-stress-test",
        },
        "failed_resources_preserved": {
            "target_service_uuid": "target-uuid",
            "target_service_name": "mother-writer-stress-target-test",
            "observer_service_uuid": None,
            "observer_service_name": None,
            "writer_service_uuid": None,
            "writer_service_name": None,
        },
    }

    followup = smoke._build_failure_followup(
        result=result,
        evidence_path=Path("runtime/state/mother/evidence/mother-static-node-writer-stress-smoke/run.json"),
        controller_id="coolify-c",
    )

    assert followup["primary_role"] == "target"
    assert "mother_writer_stress_failure_twiddle.py" in followup["twiddle"]["command"]
    assert "--role auto" in followup["twiddle"]["command"]
    assert followup["coolify_host"]["ssh_invoked_by_smoke"] is False
    commands = "\n".join(followup["coolify_host"]["commands"])
    assert "/data/coolify/services/target-uuid" in commands
    assert "com.docker.compose.service=mother-writer-stress-target-test" in commands
    assert "com.docker.compose.service=mother-writer-stress-target-test-verify" in commands
    assert "docker inspect" in commands
    assert "docker logs" in commands
    assert "docker events" in commands
    assert "journalctl -u docker.service" in commands
    assert "ssh " not in commands.lower()


def test_failure_focus_prefers_writer_for_writer_failure() -> None:
    result = {
        "reason": "writer-not-healthy",
        "failed_resources_preserved": {
            "target_service_uuid": "target-uuid",
            "observer_service_uuid": "observer-uuid",
            "writer_service_uuid": "writer-uuid",
        },
    }

    assert smoke._failure_focus_role(result) == "writer"


def test_observer_readiness_uses_runtime_marker_even_when_coolify_status_is_stale_exited() -> None:
    original_detail = smoke._detail
    original_runtime_logs = smoke._runtime_logs
    try:
        smoke._detail = lambda **_kwargs: (
            {"payload": {"applications": []}},
            {
                "observed_at": "2026-09-17T21:45:30Z",
                "service_status": "exited",
                "service_records": [],
            },
        )
        smoke._runtime_logs = lambda **_kwargs: {
            "attempts": [
                {
                    "status": 200,
                    "logs": (
                        "2026-09-17T21:45:28Z "
                        f"{smoke.OBSERVER_LOG_MARKER} "
                        "phase=observer-start writer_service=writer-test"
                    ),
                }
            ]
        }

        ready, observations = smoke._wait_for_observer_start_marker(
            controller=object(),
            service_uuid="observer-uuid",
            service_name="observer-test",
            timeout=1.0,
            max_response_bytes=1024,
            wait_seconds=0.0,
            poll_interval_seconds=0.0,
            opener=object(),
            sleeper=lambda _seconds: None,
        )
    finally:
        smoke._detail = original_detail
        smoke._runtime_logs = original_runtime_logs

    assert ready is True
    assert len(observations) == 1
    assert observations[0]["service_status"] == "exited"
    assert observations[0]["expected_marker_observed"] is True


def test_observer_readiness_fails_closed_without_runtime_marker() -> None:
    original_detail = smoke._detail
    original_runtime_logs = smoke._runtime_logs
    try:
        smoke._detail = lambda **_kwargs: (
            {"payload": {"applications": []}},
            {
                "observed_at": "2026-09-17T21:45:30Z",
                "service_status": "running:healthy:excluded",
                "service_records": [],
            },
        )
        smoke._runtime_logs = lambda **_kwargs: {
            "attempts": [{"status": 200, "logs": "observer log without readiness marker"}]
        }

        ready, observations = smoke._wait_for_observer_start_marker(
            controller=object(),
            service_uuid="observer-uuid",
            service_name="observer-test",
            timeout=1.0,
            max_response_bytes=1024,
            wait_seconds=0.0,
            poll_interval_seconds=0.0,
            opener=object(),
            sleeper=lambda _seconds: None,
        )
    finally:
        smoke._detail = original_detail
        smoke._runtime_logs = original_runtime_logs

    assert ready is False
    assert len(observations) == 1
    assert observations[0]["expected_marker_observed"] is False
