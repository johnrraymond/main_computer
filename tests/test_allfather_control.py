from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import allfather_control as control


def write_private_state(tmp_path: Path) -> Path:
    path = tmp_path / "all_father.private.yaml"
    path.write_text(
        """
kind: main_computer.all_father.private_state.v1
coolify:
  hosts:
    a:
      name: coolify-a
      url: https://coolify-a.example.invalid
      api_token: token-a
      vpn_ip: 10.116.0.3
    b:
      name: coolify-b
      url: https://coolify-b.example.invalid
      api_token_env: COOLIFY_B_TOKEN
      vpn_ip: 10.124.0.3
""".lstrip(),
        encoding="utf-8",
    )
    return path


def test_private_state_only_loads_coolify_host_seeds(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)

    hosts = control.load_private_hosts(path)

    assert [host.name for host in hosts] == ["coolify-a", "coolify-b"]
    assert hosts[0].publish_host() == "10.116.0.3"
    assert hosts[1].token_source == "private-state:coolify.hosts.b.api_token_env->env:COOLIFY_B_TOKEN"


def test_head_plan_is_control_surface_only(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    assert plan.desired_counts == {
        "allfather_heads": 2,
        "super_nodes": 0,
        "foundationdb": 0,
        "hub": 0,
        "qbft_validator_rpc": 0,
        "hub_admin": 0,
        "contracts": 0,
    }
    assert plan.guardrails["private_state_is_topology"] is False
    assert plan.guardrails["hub_admin_requires_live_qbft_validator_rpc"] is True
    assert plan.guardrails["contracts_require_live_qbft_validator_rpc"] is True
    assert [head.guard_url for head in plan.heads] == [
        "http://10.116.0.3:41400",
        "http://10.124.0.3:41401",
    ]


def test_head_manifest_has_no_workload_processes_or_network_topology(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    manifest = control.head_manifest(plan, plan.heads[0])

    assert manifest["control_plane_kind"] == "main_computer.allfather_head.v1"
    assert manifest["network_key"] == "control-plane"
    assert manifest["processes"] == []
    assert manifest["desired_counts"]["hub"] == 0
    assert manifest["desired_counts"]["foundationdb"] == 0
    assert manifest["desired_counts"]["qbft"] == 0
    assert manifest["desired_counts"]["hub_admin"] == 0
    assert manifest["desired_counts"]["contracts"] == 0
    assert manifest["topology"]["source"] == "coolify-host-seed-only"
    assert "Mainnet/testnet topology must be discovered" in manifest["topology"]["note"]


def test_head_compose_publishes_only_guard_surface(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    compose = control.render_head_compose(plan, plan.heads[0])

    assert "MC_ALLFATHER_CONTROL_PLANE" in compose
    assert "MC_ALLFATHER_HEAD_ONLY" in compose
    assert "image: \"python:3.12-slim\"" in compose
    assert "build:" not in compose
    assert "main-computer-allfather-head:latest" not in compose
    assert "10.116.0.3:41400:41414/tcp" in compose
    assert "/identity" in compose
    assert "/topology" in compose
    assert "mainneta-hub1" not in compose
    assert "mainneta-rpc1" not in compose
    assert "traefik.http.routers" not in compose

    encoded = compose.split("MC_ALLFATHER_MANIFEST_B64: ", 1)[1].splitlines()[0].strip().strip('"')
    decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert decoded["processes"] == []



def test_head_service_name_is_not_derived_from_container_image(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    hosts = control.load_private_hosts(path)
    plan = control.build_head_plan(hosts, private_state_path=path, image="python:3.12-slim")

    assert [head.service_name for head in plan.heads] == [
        "allfather-head-coolify-a",
        "allfather-head-coolify-b",
    ]
    assert ":" not in plan.heads[0].service_name


def test_bootstrap_heads_defaults_to_coolify_first_project_without_operator_arg(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)

    args = control.parse_args(["bootstrap-heads", "--dry-run", "--private-state", str(path)])

    assert args.coolify_project_name == "My first project"


def test_cli_bootstrap_heads_dry_run_uses_private_state_without_topology(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "tools/allfather_control.py",
            "bootstrap-heads",
            "--dry-run",
            "--private-state",
            str(path),
        ],
        cwd=control.REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["operation"] == "bootstrap-heads"
    assert payload["coolify_project_name"] == "My first project"
    assert payload["plan"]["desired_counts"]["allfather_heads"] == 2
    assert payload["plan"]["desired_counts"]["hub"] == 0
    assert payload["plan"]["desired_counts"]["contracts"] == 0
    assert len(payload["coolify_payloads"]) == 2


def test_write_heads_outputs_one_manifest_and_compose_per_coolify_host(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    out = tmp_path / "heads"

    result = subprocess.run(
        [
            sys.executable,
            "tools/allfather_control.py",
            "write-heads",
            "--private-state",
            str(path),
            "--out",
            str(out),
        ],
        cwd=control.REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    written = json.loads(result.stdout)["written"]
    assert len(written) == 5
    assert (out / "allfather-heads-plan.json").exists()
    assert (out / "allfather-head-coolify-a.manifest.json").exists()
    assert (out / "allfather-head-coolify-a.compose.yml").exists()
    assert (out / "allfather-head-coolify-b.manifest.json").exists()
    assert (out / "allfather-head-coolify-b.compose.yml").exists()


def test_probe_compose_is_private_and_left_running(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    compose = control.render_probe_compose(plan, plan.heads[0])

    assert "MC_ALLFATHER_PROBE" in compose
    assert "ALLFATHER_PROBE_RESULT" in compose
    assert "10.116.0.3:41400" not in compose  # encoded, not a public/host port mapping
    assert "ports:" not in compose
    assert "expose:" in compose
    assert "traefik.http.routers" not in compose
    assert "fqdn" not in compose.lower()
    assert "restart: unless-stopped" in compose
    assert "latest-result.json" in compose


def test_discover_uses_coolify_patch_probe_and_leaves_probe_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    def fake_sync_and_query_probe_for_head(plan: control.HeadPlan, head: control.HeadNode, args: object) -> dict[str, object]:
        return {
            "ok": True,
            "method": "coolify-patch-probe",
            "head_service_uuid": f"head-{head.head_id}",
            "probe_service_uuid": f"probe-{head.head_id}",
            "probe_left_running": True,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe_result": {
                "ok": True,
                "result": {
                    "ok": True,
                    "targets": [
                        {
                            "guard_url": head.guard_url,
                            "identity": {"ok": True, "network_key": "control-plane"},
                            "topology": {"ok": True, "network_key": "control-plane"},
                            "status": {"ok": True, "network_key": "control-plane"},
                        }
                    ],
                },
            },
        }

    monkeypatch.setattr(control, "sync_and_query_probe_for_head", fake_sync_and_query_probe_for_head)
    args = type("Args", (), {"dry_run": False})()

    payload = control.discover_from_heads(plan, args)

    assert payload["ok"] is True
    assert payload["operator_transport"] == "coolify-patch-probe"
    assert payload["public_guard_routes"] is False
    assert payload["ssh_used"] is False
    assert payload["direct_vpn_used"] is False
    assert payload["probe_services_left_running"] is True
    assert payload["summary"]["probe_services_synced"] == 2
    assert payload["summary"]["probe_results_observed"] == 2
    assert payload["summary"]["topology_ready"] is True
    assert payload["networks"] == {}
    assert payload["heads"][0]["peer_guard_url_scope"] == "remote-vpn-peer-only"


def test_discover_does_not_probe_vpn_urls_from_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    def fail_fetch_json(url: str, *, timeout_s: float) -> dict[str, object]:
        raise AssertionError(f"VPN URL should not be probed from local discovery: {url}")

    def fake_sync_and_query_probe_for_head(plan: control.HeadPlan, head: control.HeadNode, args: object) -> dict[str, object]:
        return {
            "ok": True,
            "method": "coolify-patch-probe",
            "head_service_uuid": f"head-{head.head_id}",
            "probe_service_uuid": f"probe-{head.head_id}",
            "probe_left_running": True,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe_result": {"ok": False, "error": "probe has not logged a result yet"},
        }

    monkeypatch.setattr(control, "fetch_json", fail_fetch_json)
    monkeypatch.setattr(control, "sync_and_query_probe_for_head", fake_sync_and_query_probe_for_head)
    args = type("Args", (), {"dry_run": False})()

    payload = control.discover_from_heads(plan, args)

    assert payload["ok"] is False
    assert payload["summary"]["probe_services_synced"] == 2
    assert payload["summary"]["probe_results_observed"] == 0
    assert payload["summary"]["topology_ready"] is False
    assert "no probe result" in payload["reason"]
    assert payload["heads"][0]["probe"]["method"] == "coolify-patch-probe"


def test_discover_dry_run_renders_private_probe_payloads(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "tools/allfather_control.py",
            "discover",
            "--dry-run",
            "--include-probe-compose",
            "--private-state",
            str(path),
        ],
        cwd=control.REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["operator_transport"] == "coolify-patch-probe"
    assert payload["public_guard_routes"] is False
    assert payload["ssh_used"] is False
    assert payload["direct_vpn_used"] is False
    assert payload["summary"]["probe_services_synced"] == 2
    assert payload["summary"]["probe_results_observed"] == 0
    assert payload["summary"]["topology_ready"] is False
    probe = payload["heads"][0]["probe"]
    assert probe["dry_run"] is True
    assert probe["probe_left_running"] is True
    assert "traefik.http.routers" not in probe["probe_compose"]
    assert "ports:" not in probe["probe_compose"]


def test_probe_log_parser_extracts_latest_probe_result() -> None:
    first = {"ok": False, "updated_at": 1, "targets": []}
    second = {"ok": True, "updated_at": 2, "targets": [{"identity": {"network_key": "control-plane"}}]}
    body = {
        "logs": [
            "noise",
            "ALLFATHER_PROBE_RESULT " + json.dumps(first),
            {"message": "ALLFATHER_PROBE_RESULT " + json.dumps(second)},
        ]
    }

    results = control.probe_results_from_logs_body(body)
    latest = control.latest_probe_result({"body": body})

    assert results == [first, second]
    assert latest["ok"] is True
    assert latest["result"] == second


def test_probe_metadata_parser_extracts_callback_result() -> None:
    result = {
        "ok": True,
        "service": "main-computer-allfather-control-probe",
        "targets": [{"guard_url": "http://10.116.0.3:41400", "ok": True}],
    }
    encoded = base64.b64encode(json.dumps(result).encode("utf-8")).decode("ascii")
    detail = {
        "body": {
            "description": "probe service\n\n" + control.PROBE_CALLBACK_MARKER + encoded,
        }
    }

    parsed = control.probe_result_from_service_metadata(detail)

    assert parsed["ok"] is True
    assert parsed["source"] == "coolify-service-description"
    assert parsed["result"] == result


def test_probe_compose_can_publish_result_back_to_coolify_metadata(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    compose = control.render_probe_compose(
        plan,
        plan.heads[0],
        callback_api_url="https://coolify-a.example.invalid",
        callback_token="token-a",
        callback_service_uuid="probe-service-uuid",
    )

    assert "MC_ALLFATHER_PROBE_CALLBACK_API_URL" in compose
    assert "MC_ALLFATHER_PROBE_CALLBACK_TOKEN" in compose
    assert "MC_ALLFATHER_PROBE_CALLBACK_SERVICE_UUID" in compose
    assert "probe-service-uuid" in compose
    assert "traefik.http.routers" not in compose
    assert "ports:" not in compose



def test_discover_compacts_raw_coolify_api_records_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    huge_compose = "services:\n  noisy:\n" + ("    image: x\n" * 200)

    def fake_sync_and_query_probe_for_head(plan: control.HeadPlan, head: control.HeadNode, args: object) -> dict[str, object]:
        return {
            "ok": True,
            "method": "coolify-patch-probe",
            "head_service_uuid": f"head-{head.head_id}",
            "probe_service_uuid": f"probe-{head.head_id}",
            "probe_left_running": True,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe_result": {"ok": False, "error": "probe has not logged a result yet"},
            "tried": [
                {
                    "operation": "get-service-detail",
                    "response": {
                        "ok": True,
                        "status": 200,
                        "path": "/api/v1/services/noisy",
                        "body": {
                            "uuid": "service-uuid",
                            "name": "allfather-control-probe",
                            "status": "running:healthy",
                            "docker_compose": huge_compose,
                        },
                    },
                }
            ],
        }

    monkeypatch.setattr(control, "sync_and_query_probe_for_head", fake_sync_and_query_probe_for_head)
    args = type("Args", (), {"dry_run": False, "verbose": False})()

    payload = control.discover_from_heads(plan, args)

    probe = payload["heads"][0]["probe"]
    serialized = json.dumps(probe)
    assert "docker_compose" not in serialized
    assert huge_compose not in serialized
    assert probe["coolify_api"]["attempt_count"] == 1
    assert probe["coolify_api"]["failed_count"] == 0
    assert probe["coolify_api"]["operations"] == ["get-service-detail"]
    assert "coolify_attempts" not in probe

    assert "service-uuid" not in serialized


def test_discover_default_summarizes_failed_coolify_attempts_without_lists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    def fake_sync_and_query_probe_for_head(plan: control.HeadPlan, head: control.HeadNode, args: object) -> dict[str, object]:
        return {
            "ok": True,
            "method": "coolify-patch-probe",
            "probe_left_running": True,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe_result": {"ok": False, "error": "no ALLFATHER_PROBE_RESULT entry found"},
            "tried": [
                {
                    "operation": "get-allfather-probe-logs",
                    "response": {
                        "ok": False,
                        "status": 404,
                        "path": "/api/v1/services/probe/logs",
                        "body": {"message": "Not found.", "docs": "https://coolify.io/docs"},
                    },
                },
                {
                    "operation": "get-allfather-probe-logs",
                    "response": {
                        "ok": False,
                        "status": 404,
                        "path": "/api/v1/services/probe/docker/logs",
                        "body": {"message": "Not found.", "docs": "https://coolify.io/docs"},
                    },
                },
            ],
        }

    monkeypatch.setattr(control, "sync_and_query_probe_for_head", fake_sync_and_query_probe_for_head)
    args = type("Args", (), {"dry_run": False, "verbose": False})()

    payload = control.discover_from_heads(plan, args)

    probe = payload["heads"][0]["probe"]
    serialized = json.dumps(probe)
    assert "coolify_attempts" not in probe
    assert "/api/v1/services/probe/logs" not in serialized
    assert probe["coolify_api"]["attempt_count"] == 2
    assert probe["coolify_api"]["failed_count"] == 2
    assert probe["coolify_api"]["last_error"]["status"] == 404


def test_operator_url_extraction_prefers_public_coolify_routes() -> None:
    record = {
        "fqdn": "https://allfather-head-coolify-a.example.com",
        "nested": {"domains": "head-a.example.com, https://head-b.example.com"},
        "ignored": "10.116.0.3",
    }

    assert control.operator_urls_from_service_record(record) == [
        "https://allfather-head-coolify-a.example.com",
        "https://head-a.example.com",
        "https://head-b.example.com",
    ]


def test_discover_is_not_ok_until_probe_result_is_observed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    def fake_sync_and_query_probe_for_head(plan: control.HeadPlan, head: control.HeadNode, args: object) -> dict[str, object]:
        return {
            "ok": True,
            "method": "coolify-patch-probe",
            "head_service_name": f"head-{head.head_id}",
            "head_service_uuid": f"head-uuid-{head.head_id}",
            "probe_service_name": f"probe-{head.head_id}",
            "probe_service_uuid": f"probe-uuid-{head.head_id}",
            "probe_deployed": True,
            "probe_left_running": True,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe_targets": [head.guard_url],
            "probe_logs": {"ok": False, "source": "coolify-api", "error": "no known Coolify logs endpoint returned probe logs"},
            "probe_result": {"ok": False, "source": "coolify-probe-logs", "error": "no ALLFATHER_PROBE_RESULT entry found"},
            "tried": [],
        }

    monkeypatch.setattr(control, "sync_and_query_probe_for_head", fake_sync_and_query_probe_for_head)
    args = type("Args", (), {"dry_run": False, "verbose": False})()

    payload = control.discover_from_heads(plan, args)

    assert payload["ok"] is False
    assert payload["summary"]["probe_services_synced"] == 2
    assert payload["summary"]["probe_results_observed"] == 0
    assert payload["summary"]["topology_ready"] is False
    assert "no probe result" in payload["reason"]


def test_default_discover_operator_summary_hides_probe_internals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    def fake_sync_and_query_probe_for_head(plan: control.HeadPlan, head: control.HeadNode, args: object) -> dict[str, object]:
        return {
            "ok": True,
            "method": "coolify-patch-probe",
            "token_source": "private-state:coolify.hosts.a.api_token",
            "head_service_name": f"head-{head.head_id}",
            "head_service_uuid": f"head-uuid-{head.head_id}",
            "probe_service_name": f"probe-{head.head_id}",
            "probe_service_uuid": f"probe-uuid-{head.head_id}",
            "probe_action": "updated",
            "probe_deployed": True,
            "probe_left_running": True,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "probe_targets": ["http://10.116.0.3:41400", "http://10.124.0.3:41401"],
            "probe_logs": {"ok": False, "source": "coolify-api", "error": "no known Coolify logs endpoint returned probe logs"},
            "probe_result": {"ok": False, "source": "coolify-probe-logs", "error": "no ALLFATHER_PROBE_RESULT entry found"},
            "tried": [
                {
                    "operation": "get-allfather-probe-logs",
                    "response": {
                        "ok": False,
                        "status": 404,
                        "path": "/api/v1/services/probe/docker/logs",
                        "body": {"message": "Not found.", "docs": "https://coolify.io/docs"},
                    },
                }
            ],
        }

    monkeypatch.setattr(control, "sync_and_query_probe_for_head", fake_sync_and_query_probe_for_head)
    args = type("Args", (), {"dry_run": False, "verbose": False})()

    payload = control.discover_from_heads(plan, args)
    summary = control.compact_discover_for_operator(payload)
    serialized = json.dumps(summary)

    assert summary["ok"] is False
    assert summary["heads"][0]["probe_result_observed"] is False
    assert summary["heads"][0]["probe_logs_available"] is False
    assert summary["heads"][0]["probe_target_count"] == 2
    assert "probe_targets" not in serialized
    assert "coolify_api" in summary["heads"][0]
    assert "operations" not in serialized
    assert "/api/v1/services/probe/docker/logs" not in serialized
    assert "token_source" not in serialized


def test_fetch_probe_logs_prefers_application_uuid_before_service_fallback() -> None:
    class Response:
        def __init__(self, status: int, body: object) -> None:
            self.status = status
            self.body = body
            self.ok = 200 <= status < 300
            self.method = "GET"
            self.path = ""

    class Client:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def request(self, method: str, path: str) -> Response:
            self.paths.append(path)
            response = Response(200, "ALLFATHER_PROBE_RESULT {\"ok\": true}\n")
            response.method = method
            response.path = path
            return response

    client = Client()
    tried: list[dict[str, object]] = []

    logs = control.fetch_probe_logs(client, "service-uuid", tried, application_uuid="app-uuid")

    assert logs["ok"] is True
    assert logs["source"] == "/api/v1/applications/app-uuid/logs?lines=500"
    assert client.paths == ["/api/v1/applications/app-uuid/logs?lines=500"]
    assert tried[0]["operation"] == "get-allfather-probe-application-logs"


def test_application_records_from_service_detail_reads_embedded_app_uuid() -> None:
    detail = {
        "body": {
            "applications": [
                {"name": "allfather-control-probe-coolify-a", "uuid": "app-uuid-a", "status": "running:healthy"}
            ]
        }
    }

    records = control.application_records_from_service_detail(detail)

    assert records == [{"uuid": "app-uuid-a", "name": "allfather-control-probe-coolify-a"}]


def write_private_state_with_wallets(tmp_path: Path) -> Path:
    path = tmp_path / "all_father.private.yaml"
    path.write_text(
        """
kind: main_computer.all_father.private_state.v1
coolify:
  hosts:
    a:
      name: coolify-a
      url: https://coolify-a.example.invalid
      api_token: token-a
      vpn_ip: 10.116.0.3
    b:
      name: coolify-b
      url: https://coolify-b.example.invalid
      api_token: token-b
      vpn_ip: 10.124.0.3
networks:
  testnet:
    wallets:
      hub_admin:
        address: "0x1111111111111111111111111111111111111111"
        private_key: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      deployer:
        address: "0x2222222222222222222222222222222222222222"
        private_key: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  mainnet:
    wallets:
      hub_admin:
        address: "0x3333333333333333333333333333333333333333"
        private_key: "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      deployer:
        address: "0x4444444444444444444444444444444444444444"
        private_key: "0xdddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
""".lstrip(),
        encoding="utf-8",
    )
    return path


def test_add_node_dry_run_creates_first_host_local_super_node_with_contracts(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "tools/allfather_control.py",
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "0",
            "--include-compose",
        ],
        cwd=control.REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["operation"] == "add-node"
    assert payload["service_name"] == "testneta-super1"
    assert payload["component_names"]["hub"] == "testneta-hub1"
    assert payload["component_names"]["validator_rpc"] == "testneta-validator-rpc1"
    assert payload["contracts_requested"] is True
    assert payload["hub_admin_requested"] is True
    assert payload["public_guard_routes"] is False
    assert payload["hub_public_cutover_deferred"] is True
    assert "0xaaaaaaaa" not in payload["compose"]
    assert "0xbbbbbbbb" not in payload["compose"]
    assert "MC_ALLFATHER_BOOTSTRAP_CONTRACTS" in payload["compose"]
    assert "traefik.http.routers" not in payload["compose"]


def test_add_node_no_contracts_disables_first_node_contract_bootstrap(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "A",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "0",
            "--no-contracts",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    assert payload["service_name"] == "testneta-super1"
    assert payload["contracts_requested"] is False
    assert payload["manifest"]["desired_counts"]["contracts"] == 0
    assert payload["manifest"]["bootstrap"]["no_contracts"] is True


def test_add_node_missing_hub_admin_does_not_block_node_creation(tmp_path: Path) -> None:
    path = tmp_path / "all_father.private.yaml"
    path.write_text(
        """
kind: main_computer.all_father.private_state.v1
coolify:
  hosts:
    a:
      name: coolify-a
      url: https://coolify-a.example.invalid
      api_token: token-a
      vpn_ip: 10.116.0.3
networks:
  testnet:
    wallets:
      deployer:
        address: "0x2222222222222222222222222222222222222222"
        private_key: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
""".lstrip(),
        encoding="utf-8",
    )
    args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "0",
            "--include-compose",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    assert payload["ok"] is True
    assert payload["service_name"] == "testneta-super1"
    assert payload["contracts_requested"] is True
    assert payload["hub_admin_requested"] is True
    assert payload["hub_admin_create_requested"] is False
    assert payload["hub_admin_private_key_required_for_node_add"] is False
    assert payload["private_state_updates"]["wallets_generated"] == ["hub_admin"]
    assert payload["manifest"]["bootstrap"]["hub_admin_create_requested"] is False
    assert payload["manifest"]["bootstrap"]["contracts_deferred_until_hub_admin_ready"] is False
    assert payload["manifest"]["wallets"]["hub_admin"]["private_key_present"] is True
    assert "MC_ALLFATHER_HUB_ADMIN_CREATE_IF_MISSING" in payload["compose"]


def test_add_node_uses_global_wallet_defaults_as_network_fallback(tmp_path: Path) -> None:
    path = tmp_path / "all_father.private.yaml"
    path.write_text(
        """
kind: main_computer.all_father.private_state.v1
coolify:
  hosts:
    a:
      name: coolify-a
      url: https://coolify-a.example.invalid
      api_token: token-a
      vpn_ip: 10.116.0.3
wallets:
  defaults:
    deployer:
      address: "0x2222222222222222222222222222222222222222"
      private_key: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
networks:
  testnet:
    wallets:
      hub_admin:
        address: null
        private_key: null
""".lstrip(),
        encoding="utf-8",
    )
    args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "0",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    assert payload["ok"] is True
    assert payload["contracts_requested"] is True
    assert payload["manifest"]["wallets"]["deployer"]["private_key_present"] is True
    assert payload["manifest"]["wallets"]["hub_admin"]["create_requested"] is False
    assert payload["manifest"]["wallets"]["hub_admin"]["private_key_present"] is True






def test_add_node_generates_missing_first_node_deployer_and_fdb_identity(tmp_path: Path) -> None:
    path = tmp_path / "all_father.private.yaml"
    path.write_text(
        """
kind: main_computer.all_father.private_state.v1
coolify:
  hosts:
    a:
      name: coolify-a
      url: https://coolify-a.example.invalid
      api_token: token-a
      vpn_ip: 10.116.0.3
networks:
  testnet:
    wallets: {}
""".lstrip(),
        encoding="utf-8",
    )
    args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "0",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    assert payload["ok"] is True
    assert payload["contracts_requested"] is True
    assert payload["private_state_updates"]["dry_run"] is True
    assert sorted(payload["private_state_updates"]["wallets_generated"]) == ["deployer", "hub_admin"]
    assert payload["private_state_updates"]["fdb_identity_generated"] is True
    assert payload["manifest"]["wallets"]["deployer"]["private_key_present"] is True
    assert payload["manifest"]["wallets"]["hub_admin"]["private_key_present"] is True
    assert payload["manifest"]["foundationdb"]["action"] == "initialize-new-cluster"
    assert payload["manifest"]["foundationdb"]["current_coordinators"] == ["10.116.0.3:44550"]
    assert payload["fdb"]["existing_node_count"] == 0


def test_add_node_second_fdb_node_joins_existing_cluster_before_reconfigure(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "1",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    fdb = payload["manifest"]["foundationdb"]
    assert payload["service_name"] == "testneta-super2"
    assert fdb["action"] == "join-existing-cluster"
    assert fdb["current_coordinators"] == ["10.116.0.3:44550"]
    assert fdb["target_coordinators"] == ["10.116.0.3:44550", "10.116.0.3:44551"]
    assert fdb["coordinator_reconfigure_required"] is True
    assert payload["fdb"]["existing_node_count"] == 1


def test_add_node_uses_coolify_inventory_count_for_next_ordinal(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "add-node",
            "mainnet",
            "--allow-mainnet",
            "--host",
            "coolify-b",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "2",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    assert payload["service_name"] == "mainnetb-super3"
    assert payload["component_names"]["hub"] == "mainnetb-hub3"
    assert payload["component_names"]["fdb"] == "mainnetb-fdb3"
    assert payload["component_names"]["validator_rpc"] == "mainnetb-validator-rpc3"
    assert payload["contracts_requested"] is False


def test_add_node_requires_mainnet_confirmation(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "add-node",
            "mainnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "0",
        ]
    )
    plan = control.build_plan_from_args(args)

    with pytest.raises(control.AllfatherControlError, match="--allow-mainnet"):
        control.add_node(plan, args)
