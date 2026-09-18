from __future__ import annotations

import json
from pathlib import Path

import tools.mother_writer_stress_failure_twiddle as twiddle


def _evidence(tmp_path: Path) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(
            {
                "network": "mainnet",
                "controller_id": "coolify-c",
                "completed_at": "2026-09-17T21:16:40Z",
                "results": [
                    {
                        "iteration": 1,
                        "started_at": "2026-09-17T21:15:38Z",
                        "completed_at": "2026-09-17T21:16:40Z",
                        "status": "failed",
                        "reason": "target-not-materialized",
                        "target": {"service_name": "mother-writer-stress-target-test"},
                        "observer": {"service_name": "mother-writer-stress-observer-test"},
                        "writer": {"service_name": "mother-static-node-writer-stress-test"},
                        "failed_resources_preserved": {
                            "target_service_uuid": "target-uuid",
                            "target_service_name": "mother-writer-stress-target-test",
                            "observer_service_uuid": None,
                            "observer_service_name": None,
                            "writer_service_uuid": None,
                            "writer_service_name": None,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_resource_auto_focuses_preserved_target(tmp_path: Path) -> None:
    evidence = twiddle._load_evidence(_evidence(tmp_path))
    result = twiddle._failed_result(evidence, None)

    role, uuid, name = twiddle._resource_for_role(result, "auto")

    assert role == "target"
    assert uuid == "target-uuid"
    assert name == "mother-writer-stress-target-test"


def test_application_names_prefers_live_service_records() -> None:
    snapshot = {
        "service_records": [
            {
                "path": "$.applications[0]",
                "name": "target-live",
                "uuid": "app-1",
            },
            {
                "path": "$.applications[1]",
                "name": "target-live-verify",
                "uuid": "app-2",
            },
        ]
    }

    assert twiddle._application_names(snapshot, fallback=["fallback"]) == [
        "target-live",
        "target-live-verify",
        "fallback",
    ]


def test_inspect_failure_is_api_read_only_and_emits_host_commands(
    monkeypatch,
    tmp_path: Path,
) -> None:
    evidence_path = _evidence(tmp_path)

    monkeypatch.setattr(twiddle.bootnode, "_load_private_state", lambda *a, **k: object())
    monkeypatch.setattr(twiddle.bootnode, "_controller", lambda *a, **k: object())
    monkeypatch.setattr(
        twiddle.bootnode,
        "_controller_config",
        lambda *a, **k: {"server_uuid": "server-uuid"},
    )
    monkeypatch.setattr(
        twiddle.smoke,
        "_detail",
        lambda **kwargs: (
            {"payload": {"uuid": "target-uuid"}},
            {
                "service_status": "exited",
                "service_records": [
                    {
                        "path": "$.applications[0]",
                        "name": "mother-writer-stress-target-test",
                        "uuid": "app-1",
                        "status": "exited",
                    },
                    {
                        "path": "$.applications[1]",
                        "name": "mother-writer-stress-target-test-verify",
                        "uuid": "app-2",
                        "status": "exited",
                    },
                ],
            },
        ),
    )
    monkeypatch.setattr(
        twiddle.smoke,
        "_runtime_logs",
        lambda **kwargs: {
            "attempts": [
                {
                    "endpoint_kind": "service-application",
                    "status": 200,
                    "logs": "",
                }
            ]
        },
    )
    monkeypatch.setattr(
        twiddle.smoke,
        "_matching_inventory",
        lambda **kwargs: [
            {
                "channel": "server-resources",
                "status": 200,
                "matches": [{"uuid": "target-uuid", "status": "exited"}],
            }
        ],
    )

    payload = twiddle.inspect_failure(
        evidence_path=evidence_path,
        runtime_state_root=tmp_path / "runtime/state",
        role="auto",
        iteration=None,
        timeout=1.0,
        max_response_bytes=100000,
    )

    assert payload["clean"] is True
    assert payload["role"] == "target"
    assert payload["service_uuid"] == "target-uuid"
    assert payload["server_uuid"] == "server-uuid"
    assert payload["policy"] == {
        "coolify_api_requests": "GET-only",
        "ssh_used": False,
        "host_shell_used": False,
        "live_mutation_performed": False,
    }
    commands = "\n".join(payload["coolify_host_inspection"]["commands"])
    assert "/data/coolify/services/target-uuid" in commands
    assert "docker inspect" in commands
    assert "docker logs" in commands
    assert "journalctl -u docker.service" in commands
    assert "ssh " not in commands.lower()


def test_twiddle_source_does_not_execute_host_shell_or_ssh() -> None:
    source = Path(twiddle.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "--ssh" not in source
