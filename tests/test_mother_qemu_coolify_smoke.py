from __future__ import annotations

import base64
from pathlib import Path

import pytest
import yaml

from tools import mother_qemu_coolify_smoke as smoke


def credentials() -> dict[str, str]:
    return {
        "username": "SmokeAdmin",
        "email": "smoke-admin@example.com",
        "password": "Sm0ke-test-password-Aa1",
    }


class FakeApi:
    def __init__(self, responses: dict[str, smoke.ApiResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, endpoint: str, body=None) -> smoke.ApiResponse:
        self.calls.append((method, endpoint))
        return self.responses[endpoint]


def response(payload, status: int = 200) -> smoke.ApiResponse:
    return smoke.ApiResponse(status=status, ok=200 <= status < 300, payload=payload, response_sha256="0" * 64)


def test_separate_environment_rejects_mainnet() -> None:
    with pytest.raises(smoke.SmokeError) as exc:
        smoke._separate_environment("mainnet")
    assert exc.value.code == "MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_NOT_ISOLATED"


def test_compose_proves_nested_kvm_qemu_and_inner_coolify_route() -> None:
    text = smoke.render_compose(
        service_name="mother-qemu-coolify-smoke",
        host_port=18000,
        credentials=credentials(),
        vm_cpus=4,
        vm_ram_mib=4096,
        vm_disk_gib=40,
        ubuntu_image_url=smoke.DEFAULT_UBUNTU_IMAGE,
    )
    payload = yaml.safe_load(text)
    service = payload["services"]["mother-qemu-coolify-smoke"]
    assert service["devices"] == ["/dev/kvm:/dev/kvm"]
    assert service["ports"] == ["18000:8000"]
    command = service["command"][2]
    assert "qemu-system-x86_64" in command
    assert "accel=kvm" in command
    assert "hostfwd=tcp:0.0.0.0:8000-:8000" in command
    assert "cdn.coollabs.io/coolify/install.sh" in command
    assert "DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14" in command
    assert "$$BASE" in command


def test_guest_cloud_init_preconfigures_admin_without_using_mainnet_network_pool() -> None:
    text = smoke.render_guest_user_data(credentials())
    assert "ROOT_USERNAME=SmokeAdmin" in text
    assert "ROOT_USER_EMAIL=smoke-admin@example.com" in text
    assert "ROOT_USER_PASSWORD=Sm0ke-test-password-Aa1" in text
    assert "DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14" in text
    assert "10.0.0.0/8" not in text


def test_guest_cloud_init_executes_bootstrap_with_bash_not_bin_sh() -> None:
    payload = yaml.safe_load(smoke.render_guest_user_data(credentials()))
    command = payload["runcmd"][0]
    assert command[:2] == ["bash", "-lc"]
    assert command[2].startswith("set -Eeuo pipefail\n")
    assert "cdn.coollabs.io/coolify/install.sh | bash" in command[2]


def test_service_body_uses_resolved_project_server_and_separate_environment() -> None:
    body = smoke.build_service_body(
        controller_config={"project_uuid": "mother-project-uuid", "server_uuid": "mother-server-uuid"},
        environment_name="qemu-coolify-smoke",
        environment_uuid="smoke-environment-uuid",
        service_name="mother-qemu-coolify-smoke",
        compose="services: {}\n",
    )
    assert body["project_uuid"] == "mother-project-uuid"
    assert body["server_uuid"] == "mother-server-uuid"
    assert body["environment_name"] == "qemu-coolify-smoke"
    assert body["environment_uuid"] == "smoke-environment-uuid"
    assert body["name"] == "mother-qemu-coolify-smoke"
    assert base64.b64decode(body["docker_compose_raw"]).decode("utf-8") == "services: {}\n"
    assert body["instant_deploy"] is False


def test_service_body_cannot_be_redirected_to_mainnet() -> None:
    with pytest.raises(smoke.SmokeError) as exc:
        smoke.build_service_body(
            controller_config={"project_uuid": "p", "server_uuid": "s"},
            environment_name="mainnet",
            environment_uuid="env",
            service_name="mother-qemu-coolify-smoke",
            compose="services: {}\n",
        )
    assert exc.value.code == "MOTHER_QEMU_COOLIFY_SMOKE_ENVIRONMENT_NOT_ISOLATED"


def test_probe_url_uses_mother_controller_host_by_default() -> None:
    controller = smoke.CoolifyController(
        network=smoke.STATE_SCOPE,
        controller_id="coolify-c",
        base_url="http://203.0.113.10:8000",
        api_token="1|abcdefghijklmnopqrstuvwxyz",
        enabled=True,
        project_name_hint="mother",
        mutation_authority="observe-only",
    )
    assert smoke._probe_url(controller, host_port=18000, probe_host="") == "http://203.0.113.10:18000/"
    assert smoke._probe_url(controller, host_port=18000, probe_host="10.9.8.7") == "http://10.9.8.7:18000/"




class TimeoutApiPlaceholder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, endpoint: str, body=None):
        self.calls.append((method, endpoint))
        raise AssertionError("explicit identity UUIDs must avoid live placement discovery")


def test_default_private_state_candidate_is_mother_identity_private_yaml() -> None:
    args = smoke.build_parser().parse_args([])
    expected_root = smoke.REPO_ROOT / "runtime" / "state"
    assert args.runtime_state_root == str(expected_root)
    candidates = smoke._private_state_candidates(args.runtime_state_root, args.private_state)
    assert candidates == [expected_root / "mother" / "identity.private.yaml"]


def test_load_runtime_private_document_reads_mother_identity_private_yaml(tmp_path: Path) -> None:
    target = tmp_path / "mother" / "identity.private.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "kind": "main_computer.mother.private_state.v1",
            "networks": {"mainnet": {"coolify": {"controllers": {}}}},
        }),
        encoding="utf-8",
    )
    document, path, digest = smoke._load_runtime_private_document(tmp_path)
    assert path == target
    assert "mainnet" in document["networks"]
    assert len(digest) == 64


def test_global_controller_binding_uses_canonical_mother_network_controller_record_without_network_selector() -> None:
    controller, config = smoke._global_coolify_binding_from_document(
        {
            "schema_version": 1,
            "kind": "main_computer.mother.private_state.v1",
            "networks": {
                "mainnet": {
                    "coolify": {
                        "mutation_authority": "observe-only",
                        "controllers": {
                            "coolify-c": {
                                "url": "https://coolify-c.example.test/",
                                "api_token": "1|test-token",
                                "enabled": True,
                                "project_uuid": "project-c",
                                "server_uuid": "server-c",
                            }
                        },
                    }
                }
            },
        },
        "coolify-c",
    )
    assert controller.controller_id == "coolify-c"
    assert controller.network == "mainnet"
    assert controller.base_url == "https://coolify-c.example.test"
    assert config == {
        "controller_id": "coolify-c",
        "source_network": "mainnet",
        "controller_path": "networks.mainnet.coolify.controllers.coolify-c",
        "project_uuid": "project-c",
        "server_uuid": "server-c",
    }


def test_controller_binding_can_use_project_name_when_project_uuid_is_absent() -> None:
    controller, config = smoke._global_coolify_binding_from_document(
        {
            "networks": {
                "bootstrap": {
                    "coolify": {
                        "mutation_authority": "observe-only",
                        "controllers": {
                            "coolify-b": {
                                "url": "https://coolify-b.example.test",
                                "api_token": "1|test-token",
                                "project_name": "My first project",
                                "server_uuid": "server-b",
                            }
                        },
                    }
                }
            }
        },
        "coolify-b",
    )
    assert controller.network == "bootstrap"
    assert controller.project_name_hint == "My first project"
    assert config["project_name"] == "My first project"
    assert config["server_uuid"] == "server-b"


def test_project_uuid_is_discovered_by_exact_project_name() -> None:
    api = FakeApi(
        {
            "/api/v1/projects": response(
                {"projects": [
                    {"uuid": "project-other", "name": "Other"},
                    {"uuid": "project-b", "name": "My first project"},
                ]}
            )
        }
    )
    assert smoke._resolve_project_uuid(api, {"project_name": "My first project"}) == "project-b"


def test_identity_project_and_server_uuid_hints_require_no_live_discovery() -> None:
    api = TimeoutApiPlaceholder()
    resolved = smoke._resolve_placement(
        api,
        {
            "controller_id": "coolify-c",
            "project_uuid": "project-c",
            "server_uuid": "server-c",
        },
    )
    assert resolved["project_uuid"] == "project-c"
    assert resolved["server_uuid"] == "server-c"
    assert api.calls == []


def test_server_uuid_matches_physical_host_identity_from_identity_record() -> None:
    api = FakeApi(
        {
            "/api/v1/servers": response(
                {"servers": [
                    {"uuid": "server-a", "name": "testnet", "ip": "203.0.113.11"},
                    {"uuid": "server-b", "name": "coolify", "ip": "203.0.113.22"},
                ]}
            )
        }
    )
    cfg = {
        "controller_id": "coolify-b",
        "droplet_hostname": "coolify",
        "public_ip": "203.0.113.22",
        "vpn_ip": "10.124.0.3",
    }
    assert smoke._resolve_server_uuid(api, cfg) == "server-b"


def test_server_uuid_infers_only_server_when_identity_fields_do_not_match() -> None:
    api = FakeApi({"/api/v1/servers": response({"servers": [{"uuid": "server-only", "name": "localhost"}]})})
    assert smoke._resolve_server_uuid(api, {"controller_id": "coolify-b"}) == "server-only"


def test_controller_binding_fails_when_controller_is_absent_from_identity_networks() -> None:
    with pytest.raises(smoke.SmokeError) as exc:
        smoke._global_coolify_binding_from_document(
            {"networks": {"mainnet": {"coolify": {"mutation_authority": "observe-only", "controllers": {}}}}},
            "coolify-b",
        )
    assert exc.value.code == "MOTHER_QEMU_COOLIFY_SMOKE_CONTROLLER_NOT_FOUND"


def test_controller_binding_fails_closed_when_same_controller_id_appears_in_multiple_networks() -> None:
    record = {
        "url": "https://coolify-c.example.test",
        "api_token": "1|test-token",
        "project_uuid": "project-c",
        "server_uuid": "server-c",
    }
    with pytest.raises(smoke.SmokeError) as exc:
        smoke._global_coolify_binding_from_document(
            {
                "networks": {
                    "mainnet": {"coolify": {"mutation_authority": "observe-only", "controllers": {"coolify-c": record}}},
                    "testnet": {"coolify": {"mutation_authority": "observe-only", "controllers": {"coolify-c": record}}},
                }
            },
            "coolify-c",
        )
    assert exc.value.code == "MOTHER_QEMU_COOLIFY_SMOKE_CONTROLLER_AMBIGUOUS"


def test_parser_has_no_network_selector() -> None:
    args = smoke.build_parser().parse_args(["--controller-id", "coolify-c", "--dry-run"])
    assert args.controller_id == "coolify-c"
    assert not hasattr(args, "network")


def test_controller_binding_strips_trailing_slash_from_identity_url() -> None:
    controller, _ = smoke._global_coolify_binding_from_document(
        {
            "networks": {
                "mainnet": {
                    "coolify": {
                        "mutation_authority": "observe-only",
                        "controllers": {
                            "coolify-c": {
                                "url": "https://coolify-c.example.test/",
                                "api_token": "1|test-token",
                                "project_uuid": "project-c",
                                "server_uuid": "server-c",
                            }
                        },
                    }
                }
            }
        },
        "coolify-c",
    )
    assert controller.base_url == "https://coolify-c.example.test"


class TimeoutApi:
    def request(self, method: str, endpoint: str, body=None) -> smoke.ApiResponse:
        raise smoke.SmokeError(
            "MOTHER_QEMU_COOLIFY_SMOKE_REQUEST_FAILED",
            f"Coolify request failed for {method} {endpoint}: timed out",
        )


def test_service_status_timeout_is_diagnostic_only() -> None:
    status = smoke._best_effort_service_status(TimeoutApi(), "service-uuid")
    assert status.startswith("unavailable:MOTHER_QEMU_COOLIFY_SMOKE_REQUEST_FAILED:")
    assert "timed out" in status


def test_service_logs_timeout_is_diagnostic_only() -> None:
    logs = smoke._service_logs(TimeoutApi(), "service-uuid", "mother-qemu-coolify-smoke")
    assert logs.startswith("[Coolify logs unavailable: MOTHER_QEMU_COOLIFY_SMOKE_REQUEST_FAILED:")
    assert "timed out" in logs


def test_default_wait_allows_slow_first_boot() -> None:
    args = smoke.build_parser().parse_args([])
    assert args.wait_seconds == 5400.0


def test_parser_supports_receipt_gated_resume() -> None:
    args = smoke.build_parser().parse_args(["--controller-id", "coolify-b", "--resume"])
    assert args.resume is True
    assert args.replace is False
    assert args.cleanup is False


def test_resume_receipt_requires_exact_existing_smoke_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "REPO_ROOT", tmp_path)
    service_name = "mother-qemu-coolify-smoke"
    controller = smoke.CoolifyController(
        network=smoke.STATE_SCOPE,
        controller_id="coolify-b",
        base_url="https://coolify-b.example.test",
        api_token="1|test-token",
        enabled=True,
        project_name_hint="My first project",
        mutation_authority="observe-only",
    )
    smoke._write_private_json(
        smoke._receipt_path(tmp_path, service_name),
        {
            "kind": smoke.KIND,
            "controller_id": "coolify-b",
            "coolify_url": "https://coolify-b.example.test/",
            "project_uuid": "project-b",
            "server_uuid": "server-b",
            "environment_name": "qemu-coolify-smoke",
            "service_name": service_name,
            "service_uuid": "service-b",
            "host_port": 18000,
            "created_environment": True,
        },
    )
    receipt = smoke._load_resume_receipt(
        tmp_path,
        service_name,
        controller_id="coolify-b",
        controller=controller,
        environment_name="qemu-coolify-smoke",
        host_port=18000,
    )
    assert receipt["service_uuid"] == "service-b"
    assert receipt["created_environment"] is True


def test_resume_receipt_rejects_missing_stored_service_uuid(tmp_path: Path) -> None:
    service_name = "mother-qemu-coolify-smoke"
    controller = smoke.CoolifyController(
        network=smoke.STATE_SCOPE,
        controller_id="coolify-b",
        base_url="https://coolify-b.example.test",
        api_token="1|test-token",
        enabled=True,
        project_name_hint="My first project",
        mutation_authority="observe-only",
    )
    smoke._write_private_json(
        smoke._receipt_path(tmp_path, service_name),
        {
            "kind": smoke.KIND,
            "controller_id": "coolify-b",
            "coolify_url": "https://coolify-b.example.test",
            "project_uuid": "project-b",
            "server_uuid": "server-b",
            "environment_name": "qemu-coolify-smoke",
            "service_name": service_name,
            "service_uuid": "",
            "host_port": 18000,
        },
    )
    with pytest.raises(smoke.SmokeError) as exc:
        smoke._load_resume_receipt(
            tmp_path,
            service_name,
            controller_id="coolify-b",
            controller=controller,
            environment_name="qemu-coolify-smoke",
            host_port=18000,
        )
    assert exc.value.code == "MOTHER_QEMU_COOLIFY_SMOKE_RESUME_RECEIPT_MISMATCH"


def test_run_resume_uses_receipt_before_live_placement_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(smoke, "REPO_ROOT", tmp_path)
    controller = smoke.CoolifyController(
        network=smoke.STATE_SCOPE,
        controller_id="coolify-b",
        base_url="https://coolify-b.example.test",
        api_token="1|test-token",
        enabled=True,
        project_name_hint="My first project",
        mutation_authority="observe-only",
    )
    smoke._write_private_json(
        smoke._receipt_path(tmp_path, "mother-qemu-coolify-smoke"),
        {
            "kind": smoke.KIND,
            "controller_id": "coolify-b",
            "coolify_url": "https://coolify-b.example.test",
            "project_uuid": "project-b",
            "server_uuid": "server-b",
            "environment_name": "qemu-coolify-smoke",
            "environment_uuid": "environment-b",
            "created_environment": True,
            "service_name": "mother-qemu-coolify-smoke",
            "service_uuid": "service-b",
            "host_port": 18000,
        },
    )
    monkeypatch.setattr(
        smoke,
        "_load_runtime_private_document",
        lambda *args, **kwargs: ({"networks": {}}, tmp_path / "mother" / "identity.private.yaml", "a" * 64),
    )
    monkeypatch.setattr(
        smoke,
        "_global_coolify_binding_from_document",
        lambda document, controller_id: (controller, {"controller_id": "coolify-b", "source_network": "mainnet", "controller_path": "networks.mainnet.coolify.controllers.coolify-b"}),
    )

    def forbidden_resolve(*args, **kwargs):
        raise AssertionError("resume must not call live project/server placement discovery")

    monkeypatch.setattr(smoke, "_resolve_placement", forbidden_resolve)
    monkeypatch.setattr(
        smoke,
        "_wait_for_inner_coolify",
        lambda **kwargs: ("http://203.0.113.22:18000/", "running:healthy", {"ok": True, "status": 200, "error": ""}),
    )
    args = smoke.build_parser().parse_args(["--controller-id", "coolify-b", "--resume"])
    assert smoke.run(args) == 0
    output = capsys.readouterr().out
    assert '"placement_source": "receipt"' in output
    assert '"uuid": "service-b"' in output
