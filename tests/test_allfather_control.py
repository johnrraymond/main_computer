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




def test_discover_includes_super_nodes_from_coolify_service_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    def fake_sync_and_query_probe_for_head(plan: control.HeadPlan, head: control.HeadNode, args: object) -> dict[str, object]:
        inventory: list[dict[str, object]] = []
        if head.coolify_server == "coolify-a":
            node = control.super_inventory_entry("testnet", head, 1, source="coolify-service-list")
            node["service_uuid"] = "testneta-super1-uuid"
            node["status"] = "running:healthy"
            node["topology_source"] = "coolify-service-inventory"
            inventory.append(node)
        return {
            "ok": True,
            "method": "coolify-patch-probe",
            "head_service_uuid": f"head-{head.head_id}",
            "probe_service_uuid": f"probe-{head.head_id}",
            "probe_left_running": True,
            "public_guard_routes": False,
            "ssh_used": False,
            "direct_vpn_used": False,
            "super_inventory": inventory,
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
    args = type("Args", (), {"dry_run": False, "verbose": False})()

    payload = control.discover_from_heads(plan, args)

    assert payload["ok"] is True
    assert payload["summary"]["coolify_seen_super_nodes"] == 1
    testnet = payload["networks"]["testnet"]
    assert testnet["super_node_count"] == 1
    assert list(testnet["hosts"]) == ["coolify-a"]
    host = testnet["hosts"]["coolify-a"]
    assert host["host_prefix"] == "testneta"
    assert host["super_node_count"] == 1
    assert host["super_nodes"][0]["service_name"] == "testneta-super1"
    assert host["super_nodes"][0]["components"]["hub"] == "testneta-hub1"
    assert host["super_nodes"][0]["components"]["validator_rpc"] == "testneta-validator-rpc1"
    assert host["super_nodes"][0]["service_uuid"] == "testneta-super1-uuid"
    assert host["super_nodes"][0]["status"] == "running:healthy"
    assert host["super_nodes"][0]["topology_source"] == "coolify-service-inventory"


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




def test_super_inventory_from_service_items_extracts_status_and_app_summary(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    services = [
        {
            "name": "testneta-super1",
            "uuid": "service-uuid",
            "status": "running:healthy",
            "applications": [
                {
                    "name": "testneta-super1",
                    "uuid": "app-uuid",
                    "status": "running:healthy",
                    "ports": "10.116.0.3:41500:41414/tcp",
                }
            ],
        },
        {"name": "testnetb-super1", "uuid": "other-host"},
        {"name": "mainneta-super1", "uuid": "other-network"},
    ]

    inventory = control.super_inventory_from_service_items(services, "testnet", head)

    assert len(inventory) == 1
    assert inventory[0]["service_name"] == "testneta-super1"
    assert inventory[0]["service_uuid"] == "service-uuid"
    assert inventory[0]["status"] == "running:healthy"
    assert inventory[0]["application"]["uuid"] == "app-uuid"
    assert inventory[0]["source"] == "coolify-service-list"


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
    assert "entrypoint: null" in payload["compose"]
    assert "entrypoint: []" not in payload["compose"]
    assert "image: \"main-computer-allfather-super-testneta-super1:latest\"" not in payload["compose"]
    assert "pull access denied" not in payload["compose"]
    assert "FROM hyperledger/besu:latest" in payload["compose"]
    assert "$$arch" not in payload["compose"]
    assert "$${FDB_VERSION}" not in payload["compose"]
    assert "$${deb_arch}" not in payload["compose"]
    assert "$${FDB_PYTHON_VERSION}" not in payload["compose"]
    assert "foundationdb-server_7.4.6-1_amd64.deb" in payload["compose"]
    assert "foundationdb-server_7.4.6-1_arm64.deb" in payload["compose"]
    assert "ln -sf /usr/sbin/fdbserver /usr/local/bin/fdbserver" in payload["compose"]
    assert "test -x /usr/bin/fdbserver" not in payload["compose"]
    assert "MC_ALLFATHER_IMAGE_KIND=besu-qbft-fdb-allfather-super" in payload["compose"]
    assert "MC_ALLFATHER_IMAGE_CAPABILITIES=guard,supervisor,hub-bootstrap,fdb,validator-rpc,besu,qbft,traefik-targets" in payload["compose"]
    assert "ENTRYPOINT [\"python\", \"-u\", \"/usr/local/bin/allfather-super-guard.py\"]" in payload["compose"]
    assert "/usr/local/bin/allfather-super-guard.py" in payload["compose"]
    assert "command:" not in payload["compose"]
    assert "python:3.12-slim" not in payload["compose"]
    assert "10.116.0.3:41500:41414/tcp" in payload["compose"]
    assert "MC_ALLFATHER_COMPONENTS" in payload["compose"]
    assert "MC_ALLFATHER_IMAGE_ENTRYPOINT" in payload["compose"]
    assert "traefik.http.routers" not in payload["compose"]


def test_super_guard_script_supervises_fdb_besu_and_hub() -> None:
    script = control.super_server_command_script()

    assert "def ensure_fdb" in script
    assert "fdbserver" in script
    assert "configure new single ssd" in script
    assert "def ensure_validator_rpc" in script
    assert "generate-blockchain-config" in script
    assert "rpc-http-enabled=true" in script
    assert "def ensure_hub" in script
    assert "running-bootstrap-listener" in script
    assert "deferred-until-live-validator-rpc" in script
    assert "0.0.0.0" in script


def test_super_guard_status_helper_accepts_component_name_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = control.super_server_command_script()
    manifest = {
        "cell_id": "testneta-super1",
        "network_key": "testnet",
        "state_root": str(tmp_path / "super-state"),
        "components": {"fdb": "testneta-fdb1"},
        "ports": {"fdb_container": 4550},
        "foundationdb": {"action": "initialize-new-cluster"},
    }
    encoded = base64.b64encode(json.dumps(manifest).encode("utf-8")).decode("ascii")
    monkeypatch.setenv("MC_ALLFATHER_SUPER_MANIFEST_B64", encoded)
    namespace: dict[str, object] = {}

    prefix = script.split("\nsignal.signal", 1)[0]
    exec(compile(prefix, "<allfather-super-guard-prefix>", "exec"), namespace)

    assert namespace["ensure_fdb"]() is False
    component_state = namespace["component_state"]
    assert component_state["foundationdb"]["name"] == "testneta-fdb1"
    assert component_state["foundationdb"]["status"] == "missing-fdbserver"


def test_super_compose_is_build_only_and_escapes_inline_dockerfile(tmp_path: Path) -> None:
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
            "--include-compose",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)
    compose = payload["compose"]

    assert "    image:" not in compose
    assert "    build:" in compose
    assert "      dockerfile_inline: |" in compose
    assert "$${TARGETARCH" not in compose
    assert "$${FDB_VERSION}" not in compose
    assert "$$arch" not in compose
    assert "$${deb_arch}" not in compose
    assert "$${FDB_PYTHON_VERSION}" not in compose
    assert "foundationdb-clients_7.4.6-1_amd64.deb" in compose
    assert "foundationdb-server_7.4.6-1_arm64.deb" in compose
    assert "ln -sf /usr/sbin/fdbserver /usr/local/bin/fdbserver" in compose


def test_add_node_publish_routes_labels_only_hub_and_rpc(tmp_path: Path) -> None:
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
            "--publish-routes",
            "--include-compose",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)
    compose = payload["compose"]

    assert "traefik.http.routers.testneta-hub1.rule=Host(`testneta-hub1.greatlibrary.io`)" in compose
    assert "traefik.http.routers.testneta-rpc1.rule=Host(`testneta-rpc1.greatlibrary.io`)" in compose
    assert "traefik.http.routers.testneta-hub1.tls.certresolver=letsencrypt" in compose
    assert "traefik.http.routers.testneta-rpc1.tls.certresolver=letsencrypt" in compose
    assert "traefik.http.services.testneta-hub1-svc.loadbalancer.server.port=8785" in compose
    assert "traefik.http.services.testneta-rpc1-svc.loadbalancer.server.port=8545" in compose
    assert "testneta-guard1.greatlibrary.io" not in compose
    assert "testneta-fdb1.greatlibrary.io" not in compose

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
    fdb = payload["manifest"]["foundationdb"]
    assert fdb["action"] == "initialize-new-cluster"
    assert fdb["cluster_description"] == "main_computer_testnet_allfather"
    assert fdb["cluster_file"].startswith("main_computer_testnet_allfather:")
    assert "-" not in fdb["cluster_description"]
    assert fdb["current_coordinators"] == ["10.116.0.3:44550"]
    assert payload["fdb"]["existing_node_count"] == 0


def test_add_node_normalizes_legacy_hyphenated_fdb_description(tmp_path: Path) -> None:
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
      hub_admin:
        private_key: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      deployer:
        private_key: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    foundationdb:
      cluster_description: main-computer-testnet-allfather
      cluster_id: abcdef1234567890
      coordinator_policy: first-node-then-expand
      reconfigure_after_join: true
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

    fdb = payload["manifest"]["foundationdb"]
    assert fdb["cluster_description"] == "main_computer_testnet_allfather"
    assert fdb["cluster_file"] == "main_computer_testnet_allfather:abcdef1234567890@10.116.0.3:44550"
    assert payload["private_state_updates"]["fdb_cluster_description"] == "main_computer_testnet_allfather"
    assert payload["private_state_updates"]["fdb_identity_generated"] is True
    assert payload["private_state_updates"]["generated"] == [
        {
            "kind": "fdb_cluster_description_normalized",
            "network": "testnet",
            "from": "main-computer-testnet-allfather",
            "to": "main_computer_testnet_allfather",
        }
    ]


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


def test_live_add_node_rejects_non_contiguous_super_inventory(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]

    with pytest.raises(control.AllfatherControlError, match="Non-contiguous testnet super-node inventory"):
        control.require_contiguous_super_ordinals([2], "testnet", head)


def test_live_add_node_rejects_existing_inventory_without_fdb_seed(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    state = control.load_yaml_mapping(path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    existing = [control.super_inventory_entry("testnet", head, 1, source="coolify-inventory")]

    with pytest.raises(control.AllfatherControlError, match="has no FoundationDB seed identity"):
        control.require_fdb_seed_for_existing_super_nodes(state, path, "testnet", head, existing)


def test_remove_node_waits_until_deleted_service_leaves_coolify_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def fake_service_items_for_client(client: object, tried: list[dict[str, object]]) -> list[dict[str, object]]:
        calls["count"] += 1
        if calls["count"] == 1:
            return [{"name": "testneta-super1", "uuid": "still-visible", "status": "deleting"}]
        return []

    monkeypatch.setattr(control, "service_items_for_client", fake_service_items_for_client)

    result = control.wait_for_coolify_service_absent(
        object(),
        service_name="testneta-super1",
        tried=[],
        wait_s=1,
        poll_s=0.1,
    )

    assert result["confirmed_absent"] is True
    assert result["attempt_count"] == 2


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


def test_remove_node_dry_run_removes_last_host_local_super_node_and_cleans_pristine_seed(tmp_path: Path) -> None:
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
      hub_admin:
        private_key: "0x1111111111111111111111111111111111111111111111111111111111111111"
        metadata:
          generated_by: tools/allfather_control.py:add-node
      deployer:
        private_key: "0x2222222222222222222222222222222222222222222222222222222222222222"
        metadata:
          generated_by: tools/allfather_control.py:add-node
    foundationdb:
      cluster_description: main-computer-testnet-allfather
      cluster_id: abcdef1234567890
      coordinator_policy: first-node-then-expand
      reconfigure_after_join: true
""".lstrip(),
        encoding="utf-8",
    )
    args = control.parse_args(
        [
            "remove-node",
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

    payload = control.remove_node(plan, args)

    assert payload["ok"] is True
    assert payload["operation"] == "remove-node"
    assert payload["service_name"] == "testneta-super1"
    assert payload["ordinal"] == 1
    assert payload["service_deleted"] is False
    assert payload["network_pristine_after_remove"] is True
    assert payload["private_state_updates"]["dry_run"] is True
    assert payload["private_state_updates"]["seed_material_cleaned"] is True
    removed_kinds = {item["kind"] for item in payload["private_state_updates"]["removed"]}
    assert {"wallet_private_key", "foundationdb_seed", "network_seed"} <= removed_kinds
    # Dry-run must not write secrets/state.
    assert "cluster_description: main-computer-testnet-allfather" in path.read_text(encoding="utf-8")


def test_remove_node_dry_run_removes_highest_existing_super_node_without_renumbering(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "3",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.remove_node(plan, args)

    assert payload["service_name"] == "testneta-super3"
    assert payload["ordinal"] == 3
    assert payload["remaining_host_super_nodes"] == 2
    assert payload["network_pristine_after_remove"] is False
    assert payload["private_state_updates"]["seed_material_cleaned"] is False


def test_remove_node_requires_mainnet_confirmation(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "mainnet",
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

    with pytest.raises(control.AllfatherControlError, match="--allow-mainnet"):
        control.remove_node(plan, args)


def test_remove_node_errors_when_no_super_node_exists(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
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

    with pytest.raises(control.AllfatherControlError, match="nothing to remove"):
        control.remove_node(plan, args)


def test_probe_targets_include_heads_and_super_nodes(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    node = control.super_inventory_entry("testnet", plan.heads[0], 1, source="test")

    targets = control.probe_target_records_for_plan(plan, super_inventory=[node])

    assert [target["kind"] for target in targets] == ["head", "head", "super-node"]
    assert targets[-1]["service_name"] == "testneta-super1"
    assert targets[-1]["guard_url"] == "http://10.116.0.3:41500"
    assert targets[-1]["network_key"] == "testnet"


def test_discover_can_enrich_super_inventory_from_private_probe_result(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    node = control.super_inventory_entry("testnet", plan.heads[0], 1, source="coolify-service-list")
    probe_result = {
        "ok": True,
        "result": {
            "targets": [
                {
                    "kind": "super-node",
                    "service_name": "testneta-super1",
                    "guard_url": "http://10.116.0.3:41500",
                    "ok": True,
                    "identity_ok": True,
                    "topology_ok": True,
                    "status_ok": True,
                    "healthz_ok": True,
                    "functions": {
                        "guard": {"running": True, "status": "running"},
                        "validator_rpc": {"running": False, "status": "pending-supervisor"},
                    },
                }
            ]
        },
    }

    enriched = control.enrich_super_inventory_with_probe_status([node], probe_result)

    assert enriched[0]["internal_status"]["observed"] is True
    assert enriched[0]["internal_status"]["ok"] is True
    assert enriched[0]["internal_status"]["functions"]["guard"]["running"] is True
    assert enriched[0]["internal_status"]["functions"]["validator_rpc"]["status"] == "pending-supervisor"


def test_discover_marks_super_internal_status_unobserved_until_probe_reports(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    node = control.super_inventory_entry("testnet", plan.heads[0], 1, source="coolify-service-list")

    enriched = control.enrich_super_inventory_with_probe_status([node], {"ok": False})

    assert enriched[0]["internal_status"]["observed"] is False
    assert enriched[0]["internal_status"]["source"] == "coolify-private-probe"


def test_probe_result_covers_expected_super_targets_when_result_mentions_each_super() -> None:
    targets = [
        {"kind": "head", "service_name": "allfather-head-coolify-a"},
        {"kind": "super-node", "service_name": "testneta-super1"},
        {"kind": "super-node", "service_name": "testneta-super2"},
    ]
    probe_result = {
        "ok": True,
        "result": {
            "targets": [
                {"kind": "head", "service_name": "allfather-head-coolify-a", "ok": True},
                {"kind": "super-node", "service_name": "testneta-super1", "ok": False, "error": "Connection refused"},
                {"kind": "super-node", "service_name": "testneta-super2", "ok": True},
            ]
        },
    }

    assert control.probe_result_covers_expected_super_targets(probe_result, targets) is True


def test_probe_result_does_not_cover_expected_super_target_when_callback_is_stale() -> None:
    targets = [
        {"kind": "head", "service_name": "allfather-head-coolify-a"},
        {"kind": "super-node", "service_name": "testneta-super1"},
    ]
    stale_result = {
        "ok": True,
        "result": {
            "targets": [
                {"kind": "head", "service_name": "allfather-head-coolify-a", "ok": True},
            ]
        },
    }

    assert control.probe_result_covers_expected_super_targets(stale_result, targets) is False


def test_super_internal_status_counts_reports_observed_and_healthy_nodes() -> None:
    networks = {
        "testnet": {
            "hosts": {
                "coolify-a": {
                    "super_nodes": [
                        {"service_name": "testneta-super1", "internal_status": {"observed": True, "ok": True}},
                        {"service_name": "testneta-super2", "internal_status": {"observed": True, "ok": False}},
                        {"service_name": "testneta-super3", "internal_status": {"observed": False, "ok": False}},
                    ]
                }
            }
        }
    }

    assert control.super_internal_status_counts(networks) == {
        "super_nodes_internal_observed": 2,
        "super_nodes_internal_healthy": 1,
    }
