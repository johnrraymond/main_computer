from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
import zipfile
import zlib
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
    assert "[allfather add-node] start: network=testnet host=coolify-a slot=A" in result.stderr
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
            "method": "coolify-head-agent",
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
    assert payload["operator_transport"] == "coolify-head-agent"
    assert payload["public_guard_routes"] is False
    assert payload["ssh_used"] is False
    assert payload["direct_vpn_used"] is False
    assert payload["probe_services_left_running"] is False
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
            "method": "coolify-head-agent",
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
            "method": "coolify-head-agent",
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
    assert "[allfather add-node] start: network=testnet host=coolify-a slot=A" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["operator_transport"] == "coolify-head-agent"
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


def test_probe_result_target_by_service_accepts_metadata_wrapped_result() -> None:
    wrapped = {
        "ok": True,
        "source": "coolify-service-description",
        "result": {
            "targets": [
                {"service_name": "allfather-super-base-builder-coolify-a", "status_ok": True, "healthz_ok": True}
            ]
        },
    }

    target = control.probe_result_target_by_service(wrapped, "allfather-super-base-builder-coolify-a")

    assert target["status_ok"] is True
    assert target["healthz_ok"] is True


def test_super_base_builder_status_from_logs_detects_ready_and_failure() -> None:
    target = "main-computer/allfather-super-base:besu-fdb-web3-solc-contracts-paris-20260715"

    ready = control.super_base_builder_status_from_logs_body(
        {"logs": f"2026-07-14T00:38:32Z allfather-super-base-builder: phase=ready target={target} already_exists=true"},
        target,
    )
    failed = control.super_base_builder_status_from_logs_body(
        {"logs": f"2026-07-14T00:38:32Z allfather-super-base-builder: phase=failed target={target} rc=1"},
        target,
    )

    assert ready["ready"] is True
    assert ready["observed"] is True
    assert failed["failed"] is True
    assert failed["ready"] is False


def test_super_base_builder_script_rechecks_host_image_after_ready() -> None:
    script = control.super_base_builder_command_script(
        target_image="main-computer/allfather-super-base:test",
        source_image="hyperledger/besu:latest",
        force_rebuild=False,
    )

    assert 'docker image inspect "$TARGET_IMAGE"' in script
    assert 'phase=missing target=$TARGET_IMAGE' in script
    assert 'target image is not present on the host Docker daemon' in script
    assert 'while true; do' in script
    assert 'ensure_target_image || true' in script


def test_application_records_from_service_detail_reads_service_applications_shape() -> None:
    detail = {
        "body": {
            "service_applications": [
                {"name": "allfather-super-base-builder-coolify-a", "uuid": "builder-app-uuid"}
            ]
        }
    }

    records = control.application_records_from_service_detail(detail)

    assert records == [{"uuid": "builder-app-uuid", "name": "allfather-super-base-builder-coolify-a"}]


def test_fetch_super_base_builder_status_reads_ready_from_service_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    target = "main-computer/allfather-super-base:besu-fdb-web3-solc-contracts-paris-20260715"

    def fake_fetch_service_detail(client: object, service_uuid: str, tried: list[dict[str, object]]) -> dict[str, object]:
        return {
            "body": {
                "logs": f"2026-07-14T00:38:32Z allfather-super-base-builder: phase=ready target={target} already_exists=true"
            }
        }

    def fail_fetch_probe_logs(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("logs endpoint should not be required when service detail already carries ready status")

    monkeypatch.setattr(control, "fetch_service_detail", fake_fetch_service_detail)
    monkeypatch.setattr(control, "fetch_probe_logs", fail_fetch_probe_logs)

    status = control.fetch_super_base_builder_log_status(object(), "builder-service-uuid", "allfather-super-base-builder-coolify-a", target, [])

    assert status["ready"] is True
    assert status["source"] == "coolify-service-detail"


def test_wait_for_super_base_builder_ready_uses_builder_logs_before_http_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    target = "main-computer/allfather-super-base:besu-fdb-web3-solc-contracts-paris-20260715"

    def fake_fetch_super_base_builder_log_status(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "observed": True,
            "ready": True,
            "failed": False,
            "source": "coolify-service-detail",
            "error": "",
        }

    def fail_sync_probe_service(*args: object, **kwargs: object) -> tuple[str, str, dict[str, object]]:
        raise AssertionError("ready builder logs should return before deploying the HTTP probe")

    monkeypatch.setattr(control, "fetch_super_base_builder_log_status", fake_fetch_super_base_builder_log_status)
    monkeypatch.setattr(control, "sync_probe_service", fail_sync_probe_service)
    args = type(
        "Args",
        (),
        {
            "quiet": True,
            "command": "add-node",
            "operator_log_interval_s": 5,
            "super_image": target,
            "super_base_source_image": "hyperledger/besu:latest",
        },
    )()

    result = control.wait_for_super_base_builder_ready(
        plan,
        head,
        object(),
        args,
        {},
        [],
        wait_s=30,
        builder_service_uuid="builder-service-uuid",
    )

    assert result["ready"] is True
    assert result["observed_by"] == "builder-logs"


def test_ensure_super_base_image_does_not_create_builder_helper_in_sprawl_free_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]

    monkeypatch.setattr(
        control,
        "sync_super_base_builder_service",
        lambda *args, **kwargs: pytest.fail("sprawl-free add-node must not create a base-builder helper service"),
    )

    args = type(
        "Args",
        (),
        {
            "quiet": True,
            "command": "add-node",
            "super_image": "main-computer/allfather-super-base:besu-fdb-web3-solc-contracts-paris-20260715",
            "super_base_source_image": "hyperledger/besu:latest",
            "force_super_base_rebuild": False,
            "no_super_base_ensure": False,
            "no_deploy": False,
            "super_base_wait_s": 1800.0,
            "super_base_predeploy_wait_s": 20.0,
            "verbose": False,
        },
    )()

    result = control.ensure_super_base_image(plan, head, object(), args, {}, [])

    assert result["ready"] is True
    assert result["service_action"] == "not-created"
    assert result["service_uuid"] == ""
    assert "no base-image builder service created" in result["reason"]




def test_super_node_dockerfile_is_self_contained_without_local_base_builder() -> None:
    dockerfile = control.super_node_dockerfile_inline("main-computer/allfather-super-base:local")

    assert "FROM hyperledger/besu:latest" in dockerfile
    assert "foundationdb-server_7.4.6-1_amd64.deb" in dockerfile
    assert "web3==6.20.4" in dockerfile
    assert "solc-static-linux" in dockerfile
    assert "contracts-artifacts.json" in dockerfile
    assert "allfather-super-base-builder" not in dockerfile
    assert "MC_ALLFATHER_SHARED_BASE_BUILDER=disabled" in dockerfile




def test_super_node_dockerfile_bundles_full_hub_runtime() -> None:
    dockerfile = control.super_node_dockerfile_inline("main-computer/allfather-super-base:local")

    assert "/opt/main-computer-src.zip" in dockerfile
    assert "/opt/main-computer-src/main_computer/hub.py" in dockerfile
    assert "PYTHONPATH=/opt/main-computer-src" in dockerfile
    assert "hub-full" in dockerfile


def test_super_node_dockerfile_cache_busts_full_hub_runtime_deployments() -> None:
    dockerfile = control.super_node_dockerfile_inline(
        "main-computer/allfather-super-base:local",
        build_id="deploy-cache-bust-123",
    )

    assert "main_computer.allfather.build_id=\"deploy-cache-bust-123\"" in dockerfile
    assert "/opt/allfather-build/deployment-id" in dockerfile
    assert "main_computer.allfather.full_hub_runtime_sha256" in dockerfile


def test_super_node_dockerfile_inline_escapes_metadata_printf_newlines() -> None:
    dockerfile = control.super_node_dockerfile_inline(
        control.DEFAULT_SUPER_IMAGE,
        guard_script="print('guard')\n",
        build_id="deploy-escape-test",
    )

    assert "printf '%s\\n' 'deploy-escape-test' > /opt/allfather-build/deployment-id" in dockerfile
    assert "printf '%s\n' 'deploy-escape-test' > /opt/allfather-build/deployment-id" not in dockerfile
    assert "printf '%s\\n' " in dockerfile
    assert "printf '%s\n' " not in dockerfile


def test_full_hub_runtime_archive_contains_hub_and_contract_config() -> None:
    raw_zip = zlib.decompress(base64.b64decode(control.allfather_full_hub_runtime_archive_b64()))
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        names = set(archive.namelist())

    assert "main_computer/hub.py" in names
    assert "main_computer/config/mainnet_contracts.json" in names
    assert "main_computer/hub_bridge_backend.py" in names


def test_full_hub_runtime_archive_contains_hub_remote_manifest() -> None:
    raw_zip = zlib.decompress(base64.b64decode(control.allfather_full_hub_runtime_archive_b64()))
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("main_computer/config/allfather_hub_remote_manifest.json").decode("utf-8"))

    assert "main_computer/config/allfather_hub_remote_manifest.json" in names
    assert manifest["kind"] == "main_computer.allfather.hub_remote_manifest.v1"
    assert "current_directory_dirty_in_hub_remote_manifest" in manifest["observed"]
    assert "current_directory_dirty_vs_remote_main" in manifest["comparison"]


def test_hub_remote_manifest_observes_dirty_current_directory_against_origin_main(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git_capture(repo_root: Path, args: list[str], *, timeout_s: float = 10.0) -> dict[str, object]:
        command = tuple(args)
        if command == ("rev-parse", "--show-toplevel"):
            return {"ok": True, "returncode": 0, "stdout": f"{control.REPO_ROOT}\n", "stderr": ""}
        if command == ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"):
            return {"ok": True, "returncode": 0, "stdout": "origin/main\n", "stderr": ""}
        if command == ("rev-parse", "--verify", "HEAD^{commit}"):
            return {"ok": True, "returncode": 0, "stdout": "localhead123\n", "stderr": ""}
        if command == ("rev-parse", "--verify", "origin/main^{commit}"):
            return {"ok": True, "returncode": 0, "stdout": "originmain456\n", "stderr": ""}
        if command == ("status", "--porcelain=v1", "--untracked-files=all"):
            return {"ok": True, "returncode": 0, "stdout": " M main_computer/hub.py\n?? local-only.txt\n", "stderr": ""}
        if command == ("diff", "--name-status", "origin/main", "--"):
            return {"ok": True, "returncode": 0, "stdout": "M\tmain_computer/hub.py\n", "stderr": ""}
        if command == ("rev-list", "--left-right", "--count", "origin/main...HEAD"):
            return {"ok": True, "returncode": 0, "stdout": "1\t2\n", "stderr": ""}
        return {"ok": False, "returncode": 1, "stdout": "", "stderr": "unexpected git command"}

    monkeypatch.setattr(control, "_git_capture", fake_git_capture)

    manifest = control.allfather_hub_remote_manifest()

    assert manifest["repository"]["selected_remote_main_ref"] == "origin/main"
    assert manifest["observed"]["current_directory_dirty_in_hub_remote_manifest"] is True
    assert manifest["observed"]["current_directory_compared_to_remote_main"] is True
    assert manifest["comparison"]["current_directory_dirty_vs_remote_main"] is True
    assert manifest["comparison"]["diff_name_status"] == ["M\tmain_computer/hub.py"]
    assert manifest["comparison"]["ahead"] == 2
    assert manifest["comparison"]["behind"] == 1


def test_super_server_prefers_full_hub_runtime_when_available() -> None:
    script = control.super_server_command_script()

    assert "def full_hub_runtime_available() -> bool:" in script
    assert "write_full_hub_launcher_script" in script
    assert "running-full-main-computer-hub" in script
    assert "MAIN_COMPUTER_HUB_CONTRACTS_PATH" in script
    assert "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER" in script


def test_full_hub_health_endpoint_reports_non_bootstrap_runtime() -> None:
    source = Path(control.REPO_ROOT / "main_computer" / "hub.py").read_text(encoding="utf-8")

    assert '"service": "main-computer-hub"' in source
    assert '"bootstrap_hub": False' in source
    assert '"full_main_computer_hub": True' in source
    assert '"hub_remote_manifest": load_allfather_hub_remote_manifest()' in source


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
            "method": "coolify-head-agent",
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
            "method": "coolify-head-agent",
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
            "method": "coolify-head-agent",
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
            "method": "coolify-head-agent",
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
    assert payload["hub_admin_scope"] == "node"
    assert payload["manifest"]["wallets"]["hub_admin"]["scope"] == "node"
    assert payload["private_state_updates"]["node_hub_admin_cell_id"] == "testneta-super1"
    assert payload["private_state_updates"]["node_hub_admin_private_key_present"] is True
    assert payload["public_guard_routes"] is False
    assert payload["hub_public_cutover_deferred"] is True
    assert "0xaaaaaaaa" not in payload["compose"]
    assert "0xbbbbbbbb" not in payload["compose"]
    assert "MC_ALLFATHER_BOOTSTRAP_CONTRACTS" in payload["compose"]
    assert "entrypoint: null" in payload["compose"]
    assert "entrypoint: []" not in payload["compose"]
    assert "image: \"main-computer-allfather-super-testneta-super1:latest\"" not in payload["compose"]
    assert "pull access denied" not in payload["compose"]
    assert f"FROM {control.DEFAULT_SUPER_IMAGE}" in payload["compose"]
    assert "$$arch" not in payload["compose"]
    assert "$${FDB_VERSION}" not in payload["compose"]
    assert "$${deb_arch}" not in payload["compose"]
    assert "$${FDB_PYTHON_VERSION}" not in payload["compose"]
    assert "foundationdb-server_7.4.6-1_amd64.deb" not in payload["compose"]
    assert "foundationdb-server_7.4.6-1_arm64.deb" not in payload["compose"]
    assert "web3==6.20.4" not in payload["compose"]
    assert "py-solc-x" not in payload["compose"]
    assert "solcx.install_solc" not in payload["compose"]
    assert "github.com/ethereum/solidity/releases/download/v0.8.24/solc-static-linux" not in payload["compose"]
    assert "solc --version" not in payload["compose"]
    assert "ln -sf /usr/sbin/fdbserver /usr/local/bin/fdbserver" not in payload["compose"]
    assert "test -x /usr/bin/fdbserver" not in payload["compose"]
    assert "MC_ALLFATHER_IMAGE_KIND=besu-qbft-fdb-allfather-super" in payload["compose"]
    assert "MC_ALLFATHER_IMAGE_CAPABILITIES=guard,supervisor,hub-bootstrap,hub-admin-bootstrap,contract-deploy,fdb,validator-rpc,besu,qbft,traefik-targets" in payload["compose"]
    assert "ENTRYPOINT [\"/opt/allfather-super-venv/bin/python\", \"-u\", \"/usr/local/bin/allfather-super-entrypoint.py\"]" in payload["compose"]
    assert "/usr/local/bin/allfather-super-guard.py" in payload["compose"]
    assert "/usr/local/bin/allfather-super-entrypoint.py" in payload["compose"]
    assert "command:" not in payload["compose"]
    assert "python:3.12-slim" not in payload["compose"]
    assert "10.116.0.3:41500:41414/tcp" in payload["compose"]
    assert "MC_ALLFATHER_COMPONENTS" in payload["compose"]
    assert "MC_ALLFATHER_IMAGE_ENTRYPOINT" in payload["compose"]
    assert "traefik.http.routers" not in payload["compose"]




def test_add_node_contains_resume_path_for_incomplete_existing_node() -> None:
    source = (control.REPO_ROOT / "tools" / "allfather_control.py").read_text(encoding="utf-8")

    assert "resume: existing" in source
    assert "resuming existing incomplete service" in source
    assert "resume_existing_network_nodes" in source
    assert "add_node_super_ready_check(resume_status, resume_manifest)" in source


def test_add_node_prepares_existing_validator_admission_endpoints() -> None:
    source = (control.REPO_ROOT / "tools" / "allfather_control.py").read_text(encoding="utf-8")

    assert "ensure_existing_validator_admission_endpoints" in source
    assert "validator-admission: updating existing validator service" in source
    assert "validator_admission_prep" in source
    assert "/qbft/propose-validator" in source


def test_super_guard_validator_admission_state_updates_do_not_duplicate_kwargs() -> None:
    script = control.super_server_command_script()

    assert "def validator_admission_state" in script
    assert 'state("validator_admission", **base,' not in script
    compile(script, "<allfather-super-guard>", "exec")


def test_add_node_quiet_suppresses_operator_progress_logs(tmp_path: Path) -> None:
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
            "--quiet",
        ],
        cwd=control.REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "[allfather add-node]" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["service_name"] == "testneta-super1"


def test_super_guard_script_supervises_fdb_besu_and_hub() -> None:
    script = control.super_server_command_script()

    assert "def ensure_fdb" in script
    assert "fdbserver" in script
    assert "configure new single ssd" in script
    assert "def ensure_validator_rpc" in script
    assert "generate-blockchain-config" in script
    assert "recovered-existing-operator-output" in script
    assert "MC_ALLFATHER_QBFT_GENERATE_TIMEOUT_S" in script
    assert "shutil.rmtree(out_dir, ignore_errors=True)" in script
    assert "rpc-http-enabled=true" in script
    assert "eth_blockNumber" in script
    assert "block_production_ok" in script
    assert "waiting-qbft-block-production" in script
    assert "waiting-validator-json-rpc-after-block-production" in script
    assert "latest_besu_log_block_number" in script
    assert "pipe_child_output" in script
    assert "allfather-child:{name}" in script
    assert "MC_ALLFATHER_STDOUT_CHILD_LOGS" in script
    assert "previous.log" in script
    assert "log_block_production_ok" in script
    assert "emptyblockperiodseconds" in script
    assert "def ensure_hub" in script
    assert "running-bootstrap-listener" in script
    assert "def ensure_hub_admin" in script
    assert "def ensure_contracts" in script
    assert "bootstrapped" in script
    assert "deployed" in script
    assert "contracts: submitted contract=" in script
    assert "contracts: waiting receipt" in script
    assert "def json_safe" in script
    assert "json.dumps(json_safe(payload)" in script
    assert "json.dumps(json_safe(body)" in script
    assert "failed_receipt = json_safe" in script
    assert "pending_age_s" in script
    assert "/qbft/bootstrap" in script
    assert "fetch_shared_qbft_config" in script
    assert "sync_joiner_shared_qbft_config" in script
    assert "reset-mismatched-genesis" in script
    assert "admin_addPeer" in script
    assert "waiting-qbft-peer" in script
    assert "peer_count" in script
    assert "/qbft/propose-validator" in script
    assert "qbft_proposeValidatorVote" in script
    assert "ensure_joiner_validator_admission" in script
    assert "validator_admission" in script
    assert "ready-for-bootstrap-command" not in script
    assert "0.0.0.0" in script


def test_super_guard_script_compiles_with_future_annotations() -> None:
    script = control.super_server_command_script()

    assert script.startswith("from __future__ import annotations")
    compile(script, "<allfather-super-guard>", "exec")


def test_super_guard_script_streams_selected_child_logs_without_http_probe_spam() -> None:
    script = control.super_server_command_script()

    assert "def echo_child_log_line" in script
    assert "produced empty block" in script
    assert "MC_ALLFATHER_BESU_BLOCK_LOG_INTERVAL_S" in script
    assert "get /healthz" in script
    assert "allfather-child:{name}" in script


def test_super_entrypoint_wrapper_binds_public_port_and_proxies_child_guard() -> None:
    wrapper = control.super_node_entrypoint_wrapper_script()

    assert "PUBLIC_GUARD_PORT" in wrapper
    assert "CHILD_GUARD_PORT" in wrapper
    assert "MC_ALLFATHER_GUARD_CHILD_PORT" in wrapper
    assert "guard-startup-failed" in wrapper
    assert "proxy_to_child" in wrapper
    assert "diagnostic_payload" in wrapper
    compile(wrapper, "<allfather-super-entrypoint>", "exec")


def test_super_dockerfile_uses_diagnostic_wrapper_entrypoint() -> None:
    dockerfile = control.super_node_dockerfile_inline("hyperledger/besu:latest")

    assert "/usr/local/bin/allfather-super-guard.py" in dockerfile
    assert "/usr/local/bin/allfather-super-entrypoint.py" in dockerfile
    assert "zlib.decompress(base64.b64decode" in dockerfile
    assert "RUN python - <<'PY'" not in dockerfile
    assert "<<'PY'" not in dockerfile
    assert "allfather-super-entrypoint" in dockerfile
    assert "MC_ALLFATHER_IMAGE_ENTRYPOINT=allfather-super-entrypoint" in dockerfile
    assert "python3-venv" not in dockerfile
    assert "build-essential" not in dockerfile
    assert "python3-dev" not in dockerfile
    assert "python -m pip install --no-cache-dir --break-system-packages" not in dockerfile
    assert "python3 -m venv /opt/allfather-super-venv" not in dockerfile
    assert "/opt/allfather-super-venv/bin/python -m pip install --no-cache-dir" not in dockerfile
    assert 'ENTRYPOINT ["/opt/allfather-super-venv/bin/python", "-u", "/usr/local/bin/allfather-super-entrypoint.py"]' in dockerfile
    assert 'ENTRYPOINT ["python", "-u", "/usr/local/bin/allfather-super-guard.py"]' not in dockerfile


def test_super_dockerfile_payload_writer_avoids_long_heredoc_lines() -> None:
    dockerfile = control.super_node_dockerfile_inline("hyperledger/besu:latest")

    assert "printf '%s\\n'" in dockerfile
    assert "unterminated heredoc" not in dockerfile
    assert "write_bytes(base64.b64decode" not in dockerfile
    assert "rm -f /tmp/allfather-payload-0.b64; \\" in dockerfile
    assert "rm -f /tmp/allfather-payload-0.b64 \\\n    {" not in dockerfile
    assert max(len(line) for line in dockerfile.splitlines()) < 300


def test_super_base_dockerfile_contains_heavy_dependency_layer() -> None:
    dockerfile = control.super_base_dockerfile_inline()

    assert "FROM hyperledger/besu:latest" in dockerfile
    assert "build-essential" in dockerfile
    assert "python3-dev" in dockerfile
    assert "foundationdb-server_7.4.6-1_amd64.deb" in dockerfile
    assert "web3==6.20.4" in dockerfile
    assert "solc-static-linux" in dockerfile
    assert "allfather-contract-sources-b64.json" in dockerfile
    assert "build-allfather-contract-artifacts.py" in dockerfile
    assert "contracts-artifacts.json" in dockerfile
    assert "py-solc-x" not in dockerfile
    assert "solcx.install_solc" not in dockerfile


def test_super_base_builder_compose_is_managed_coolify_service() -> None:
    head = control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="10.116.0.3",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/control-plane/coolify-a",
        peers=(),
    )

    compose = control.render_super_base_builder_compose(head)

    assert "allfather-super-base-builder-coolify-a" in compose
    assert "image: \"docker:27-cli\"" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose
    assert "docker build -t \"$$TARGET_IMAGE\"" in compose
    assert "10.116.0.3:41700:41616/tcp" in compose
    assert "phase=building target=$$TARGET_IMAGE" in compose
    assert "health_ok=true" in compose
    assert "if [ \"$$phase\" = \"failed\" ]; then" in compose
    assert "start_httpd_candidate" in compose
    assert "status_http=failed method=$$label" in compose
    assert "apk add --no-cache busybox-extras" in compose
    assert "httpd -f -p \"0.0.0.0:$$STATUS_PORT\"" in compose
    assert "test -f /work/www/healthz" in compose
    assert 'grep -q \'\\"ok\\": true\' /work/www/healthz' in compose
    assert "$$ok" in compose
    assert "$${3:-}" in compose
    assert "$$(date -u" in compose
    assert not re.search(r"(?<!\\$)\\$(?!\\$)", compose)
    assert "ssh root@" not in compose.lower()
    assert "scp " not in compose.lower()


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
    assert f"FROM {control.DEFAULT_SUPER_IMAGE}" in compose
    assert "foundationdb-clients_7.4.6-1_amd64.deb" not in compose
    assert "foundationdb-server_7.4.6-1_arm64.deb" not in compose
    assert "web3==6.20.4" not in compose
    assert "py-solc-x" not in compose
    assert "solcx.install_solc" not in compose
    assert "github.com/ethereum/solidity/releases/download/v0.8.24/solc-static-linux" not in compose
    assert "solc --version" not in compose
    assert "python3-venv" not in compose
    assert "python3 -m venv /opt/allfather-super-venv" not in compose
    assert "python -m pip install --no-cache-dir --break-system-packages" not in compose
    assert "ln -sf /usr/sbin/fdbserver /usr/local/bin/fdbserver" not in compose
    assert "allfather-super-entrypoint.py" in compose
    assert "MC_ALLFATHER_IMAGE_ENTRYPOINT=allfather-super-entrypoint" in compose


def test_super_guard_uses_static_solc_standard_json() -> None:
    script = control.super_server_command_script()

    assert "from solcx" not in script
    assert "install_solc" not in script
    assert "[\"solc\", \"--standard-json\"]" in script
    assert "capture_output=True" in script
    assert "solc binary is missing" in script

def test_super_guard_contract_deploy_is_resumable_and_uses_positive_gas_private_chain() -> None:
    script = control.super_server_command_script()

    assert "contracts-progress" in script
    assert "class PendingContractDeployment" in script
    assert "class ContractReceiptPending" in script
    assert "deployment-pending" in script
    assert "Known transaction" in script
    assert "MC_ALLFATHER_MIN_CONTRACT_GAS_PRICE_WEI" in script
    assert '"gasPrice": gas_price' in script
    assert "deployer balance insufficient" in script
    assert "balanceShortfall=" in script
    assert "contract_transaction_has_unpayable_upfront_cost" in script
    assert "contract_progress_recovery_deployer_label" in script
    assert "unmineable-tx-recovery" in script
    assert "contracts: rotating deployer" in script
    assert "deployerKeySource=" in script
    assert "w3.eth.get_block(\"latest\")" in script
    assert "install_web3_poa_middleware(w3)" in script
    assert "geth_poa_middleware" in script or "ExtraDataToPOAMiddleware" in script
    assert "ExtraDataLengthError" in script
    assert "wait_for_contract_deployment_receipt" in script
    assert "w3.eth.get_transaction_receipt" in script
    assert "inspect_contract_transaction" in script
    assert "w3.eth.get_transaction" in script
    assert "MC_ALLFATHER_CONTRACT_STALE_PENDING_S" in script
    assert "MC_ALLFATHER_CONTRACT_VISIBLE_PENDING_REPLACE_S" in script
    assert "MC_ALLFATHER_CONTRACT_VISIBLE_PENDING_REPLACE_BLOCKS" in script
    assert "MC_ALLFATHER_CONTRACT_DROPPED_PENDING_BLOCKS" in script
    assert "submitted_at_unix_s" in script
    assert "submitted_at_block_number" in script
    assert "invalid-future-submitted-at-uptime" in script
    assert "submitted_block_delta" in script
    assert "stale pending transaction" in script
    assert "visible pending transaction stuck" in script
    assert "replacement_nonce" in script
    assert "pending_block_delta" in script
    assert "stale_transactions" in script
    assert "tx_observed=" in script
    assert "tx_pending=" in script
    assert "chainGasPrice=" in script
    assert "normalize_transaction_hash" in script
    assert "Do not feed strings back into w3.to_hex()" in script
    assert "normalize_transaction_hash(w3, tx_hash)" in script
    assert "wait_for_transaction_receipt" not in script



def test_super_guard_besu_single_node_qbft_does_not_wait_for_sync_peers() -> None:
    script = control.super_server_command_script()

    assert '"--sync-min-peers=0"' in script
    assert '"--min-gas-price=1"' in script
    assert '"--api-gas-price-max=1000000000"' in script


def test_super_contract_artifacts_are_prebuilt_in_base_image_and_loaded_at_runtime() -> None:
    dockerfile = control.super_base_dockerfile_inline()
    builder_script = control.super_base_builder_command_script()
    super_script = control.super_server_command_script()

    assert "COPY allfather-contract-sources-b64.json" in dockerfile
    assert "COPY build-allfather-contract-artifacts.py" in dockerfile
    assert "/opt/allfather-contracts/contracts-artifacts.json" in dockerfile
    assert "build-allfather-contract-artifacts.py" in builder_script
    assert "allfather-contract-sources-b64.json" in builder_script
    assert "contracts: using prebuilt artifacts" in super_script
    assert "MC_ALLFATHER_CONTRACT_ARTIFACTS_PATH" in super_script
    assert "prebuilt artifacts unavailable; compiling contracts with solc fallback" in super_script
    assert '"evmVersion": contract_evm_version()' in super_script
    assert "bytecode_contains_push0" in super_script
    assert "missing-required-artifact-or-push0-opcode" in super_script
    assert "deployment-receipt-status-zero" in super_script
    assert '"evmVersion": CONTRACT_EVM_VERSION' in control.allfather_contract_artifact_builder_script()
    assert "compiled.setdefault(\"x-allfather\", {})[\"contractEvmVersion\"]" in control.allfather_contract_artifact_builder_script()


def test_super_compose_maps_besu_p2p_to_advertised_host_port_for_joiners(tmp_path: Path) -> None:
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
            "1",
            "--include-compose",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)
    manifest = payload["manifest"]
    compose = payload["compose"]

    assert payload["ordinal"] == 2
    assert manifest["ports"]["p2p_host"] == 45301
    assert manifest["foundationdb"]["existing_nodes"][0]["p2p_endpoint"] == "10.116.0.3:45300"
    assert manifest["foundationdb"]["existing_nodes"][0]["p2p_host_port"] == 45300
    assert '10.116.0.3:45301:45301/tcp' in compose
    assert '10.116.0.3:45301:45301/udp' in compose
    assert '10.116.0.3:45301:30303/tcp' not in compose


def test_super_guard_rewrites_qbft_bootnodes_to_inventory_p2p_port() -> None:
    script = control.super_server_command_script()

    assert "def advertised_p2p_port" in script
    assert "def rewrite_enode_endpoint" in script
    assert "normalize_bootnodes_for_inventory_node" in script
    assert "refresh_existing_joiner_bootnodes" in script
    assert 'bootnodes.append(rewrite_enode_endpoint(enode))' in script
    assert 'normalized_bootnodes = normalize_bootnodes_for_inventory_node' in script
    assert 'refresh_existing_joiner_bootnodes(config_dir)' in script
    assert '"p2p_host": advertised_p2p_host()' in script
    assert 'f"--p2p-port={p2p_port}"' in script


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



def test_traefik_dynamic_config_uses_live_allfather_super_topology(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    local_nodes = [
        control.super_inventory_entry("mainnet", head, 1, source="test"),
        control.super_inventory_entry("mainnet", head, 2, source="test"),
    ]

    config = control.render_allfather_hub_traefik_dynamic_config(
        "mainnet",
        head,
        local_nodes,
        domain_suffix="greatlibrary.io",
    )

    assert "Host(`mainneta-hub1.greatlibrary.io`)" in config
    assert "Host(`mainneta-hub2.greatlibrary.io`)" in config
    assert "Host(`mainnet-hub.greatlibrary.io`)" in config
    assert 'url: "http://mainneta-super1:8785"' in config
    assert 'url: "http://mainneta-super2:8785"' in config
    assert "https://mainnet-hub1.greatlibrary.io" not in config
    assert "https://mainnet-hub2.greatlibrary.io" not in config


def test_traefik_propagator_compose_disables_legacy_public_entry_files(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    local_nodes = [control.super_inventory_entry("mainnet", head, 1, source="test")]
    args = control.parse_args(
        [
            "traefik-propagate",
            "mainnet",
            "--allow-mainnet",
            "--dry-run",
            "--include-compose",
            "--private-state",
            str(path),
        ]
    )

    compose = control.render_traefik_propagator_compose(
        "mainnet",
        head,
        local_nodes,
        args,
        domain_suffix="greatlibrary.io",
    )

    assert "image: \"docker:27-cli\"" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose
    assert "docker exec -i coolify-proxy" in compose
    assert "/traefik/dynamic/main-computer-mainnet-hub-public-entry*.yml" in compose
    assert "/traefik/dynamic/manual-mainnet-hub*.yml" in compose
    assert "/traefik/dynamic/allfather-mainnet-hub-routes-*.yml" in compose
    assert "mainneta-super1:8785" in compose
    assert "ALLFATHER_TRAEFIK_PROPAGATE_RESULT_B64:" in compose



def test_traefik_propagate_attempt_summary_redacts_compose_and_log_spam() -> None:
    tried = [
        {
            "operation": "update-service",
            "payload": {"docker_compose_raw": "BASE64-SPAM"},
            "response": {"ok": True, "status": 200, "body": {"docker_compose_raw": "RAW-SPAM"}},
        },
        {
            "operation": "get-allfather-probe-service-logs-fallback",
            "response": {
                "ok": False,
                "status": 404,
                "path": "/api/v1/services/example/logs",
                "body": {"message": "Not found.", "docker_compose_raw": "MORE-SPAM"},
            },
        },
        {
            "operation": "get-allfather-probe-service-logs-fallback",
            "response": {
                "ok": False,
                "status": 404,
                "path": "/api/v1/services/example/docker/logs",
                "body": {"message": "Not found."},
            },
        },
    ]

    summary = control.traefik_propagate_attempt_summary(tried)
    rendered = json.dumps(summary, sort_keys=True)

    assert summary["attempt_count"] == 3
    assert summary["log_probe_attempt_count"] == 2
    assert summary["compose_payload_redacted"] is True
    assert "BASE64-SPAM" not in rendered
    assert "RAW-SPAM" not in rendered
    assert "MORE-SPAM" not in rendered



def test_traefik_propagate_request_runs_inside_head_agent(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    local_nodes = [control.super_inventory_entry("mainnet", head, 1, source="test")]
    request = control.traefik_propagate_request_payload(
        "mainnet",
        head,
        local_nodes,
        domain_suffix="greatlibrary.io",
    )

    compose = control.render_head_compose(
        plan,
        head,
        probe_targets=[],
        callback_api_url="https://coolify-a.example.invalid",
        callback_token="token-a",
        callback_service_uuid="head-uuid",
        traefik_propagate_request=request,
    )

    assert "MC_ALLFATHER_TRAEFIK_PROPAGATE_REQUEST_B64" in compose
    assert "ALLFATHER_TRAEFIK_PROPAGATE_RESULT_B64:" in compose
    assert "/traefik-propagate/status" in compose
    assert "docker_exec_sh" in compose
    assert "coolify-proxy" in compose
    assert "mainneta-super1:8785" in json.dumps(request, sort_keys=True)


def test_head_agent_traefik_cleanup_expands_and_verifies_legacy_globs() -> None:
    source = Path(control.__file__).read_text(encoding="utf-8")

    assert "for f in $pattern; do" in source
    assert "remaining_legacy" in source
    assert "stale Traefik hub dynamic files still active" in source
    assert "allfather-traefik-disabled-globs.txt" in source
    assert "for f in {quoted_globs}; do" not in source



def test_sync_head_service_keeps_traefik_request_when_updating_existing_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    local_nodes = [control.super_inventory_entry("mainnet", head, 1, source="test")]
    request = control.traefik_propagate_request_payload(
        "mainnet",
        head,
        local_nodes,
        domain_suffix="greatlibrary.io",
    )

    class FakeHubTool:
        def find_service(self, client: object, *, service_name: str, explicit_uuid: str, tried: list[dict[str, object]]) -> tuple[str, dict[str, object]]:
            return "head-uuid", {"name": service_name}

    class FakeFdbTool:
        def __init__(self) -> None:
            self.updated_compose = ""

        def parse_binding_map(self, values: object, option_name: str) -> dict[str, str]:
            return {}

        def update_service(self, client: object, service_uuid: str, service_name: str, compose: str, tried: list[dict[str, object]]) -> None:
            self.updated_compose = compose

    fake_fdb = FakeFdbTool()
    monkeypatch.setattr(control, "hub_service_tool", lambda: FakeHubTool())
    monkeypatch.setattr(control, "fdb_tool", lambda: fake_fdb)

    args = type(
        "Args",
        (),
        {
            "dockerfile": control.DEFAULT_DOCKERFILE,
            "image": control.DEFAULT_IMAGE,
            "set_coolify_service_uuid": [],
            "coolify_service_uuid": "",
        },
    )()
    client = type("Client", (), {"base_url": "https://coolify-a.example.invalid", "token": "token-a"})()

    service_uuid, action, _existing = control.sync_head_service(
        client,
        plan,
        head,
        args,
        context={},
        tried=[],
        probe_targets=[],
        traefik_propagate_request=request,
    )

    assert service_uuid == "head-uuid"
    assert action == "updated"
    line = next(
        item for item in fake_fdb.updated_compose.splitlines()
        if "MC_ALLFATHER_TRAEFIK_PROPAGATE_REQUEST_B64:" in item
    )
    encoded = json.loads(line.split(": ", 1)[1])
    decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert decoded["request_id"] == request["request_id"]
    assert decoded["target_path"].endswith("allfather-mainnet-hub-routes-coolify-a.yml")
    assert decoded["health_urls"] == ["http://mainneta-super1:8785/api/hub/v1/health"]


def test_wait_for_head_traefik_propagate_ready_ignores_stale_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    stale_payload = {"ok": True, "phase": "ready", "status": "ready", "request_id": "old"}
    current_payload = {"ok": True, "phase": "ready", "status": "ready", "request_id": "new"}
    encoded_stale = base64.b64encode(json.dumps(stale_payload, sort_keys=True).encode("utf-8")).decode("ascii")
    encoded_current = base64.b64encode(json.dumps(current_payload, sort_keys=True).encode("utf-8")).decode("ascii")
    details = [
        {"body": {"description": control.TRAEFIK_PROPAGATE_CALLBACK_MARKER + encoded_stale}},
        {"body": {"description": control.TRAEFIK_PROPAGATE_CALLBACK_MARKER + encoded_current}},
    ]

    def fake_fetch_service_detail(client: object, service_uuid: str, tried: list[dict[str, object]]) -> dict[str, object]:
        return details.pop(0)

    sleeps: list[float] = []
    monkeypatch.setattr(control, "fetch_service_detail", fake_fetch_service_detail)
    monkeypatch.setattr(control.time, "sleep", lambda seconds: sleeps.append(float(seconds)))

    result = control.wait_for_head_traefik_propagate_ready(
        object(),
        "head-uuid",
        [],
        wait_s=5,
        poll_s=0.1,
        expected_request_id="new",
    )

    assert result["observed"] is True
    assert result["ready"] is True
    assert result["result"]["request_id"] == "new"
    assert sleeps


def test_traefik_propagate_result_from_head_metadata() -> None:
    payload = {
        "ok": True,
        "phase": "ready",
        "status": "ready",
        "target_path": "/traefik/dynamic/allfather-mainnet-hub-routes-coolify-a.yml",
    }
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    detail = {"body": {"description": "x\n" + control.TRAEFIK_PROPAGATE_CALLBACK_MARKER + encoded + "\n"}}

    result = control.traefik_propagate_result_from_service_metadata(detail)

    assert result["ok"] is True
    assert result["source"] == "coolify-service-description"
    assert result["result"]["phase"] == "ready"
    assert result["result"]["target_path"].endswith("coolify-a.yml")

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
    assert payload["private_state_updates"]["node_hub_admin_cell_id"] == "testneta-super1"
    assert payload["manifest"]["wallets"]["hub_admin"]["scope"] == "node"
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
    assert payload["contracts_requested"] is False
    assert payload["hub_admin_scope"] == "node"
    assert payload["private_state_updates"]["node_hub_admin_cell_id"] == "testneta-super2"
    assert payload["private_state_updates"]["node_hub_admin_private_key_present"] is True
    assert payload["manifest"]["bootstrap"]["contracts_requested"] is False
    assert payload["manifest"]["wallets"]["hub_admin"]["scope"] == "node"


def test_network_inventory_collects_existing_super_nodes_across_hosts(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    inventory = control.network_super_inventory_from_services_by_head(
        plan,
        "testnet",
        {
            "coolify-a": [{"name": "testneta-super1"}, {"name": "testneta-super2"}],
            "coolify-b": [],
        },
    )

    assert [item["service_name"] for item in inventory] == ["testneta-super1", "testneta-super2"]
    assert {item["coolify_server"] for item in inventory} == {"coolify-a"}
    assert inventory[0]["guard_url"] == "http://10.116.0.3:41500"
    assert inventory[1]["p2p_endpoint"] == "10.116.0.3:45301"


def test_cross_host_first_local_node_joins_existing_network_instead_of_initializing(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    state = control.load_yaml_mapping(path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head_a = control.choose_head_for_host(plan, "coolify-a")
    head_b = control.choose_head_for_host(plan, "coolify-b")
    existing = [
        control.super_inventory_entry("testnet", head_a, 1, source="test-existing-network"),
        control.super_inventory_entry("testnet", head_a, 2, source="test-existing-network"),
    ]

    state, wallets, updates = control.materialize_private_state_for_add_node(
        state,
        path,
        "testnet",
        ordinal=1,
        cell_id="testnetb-super1",
        no_contracts=True,
        dry_run=True,
    )
    manifest = control.super_manifest(
        "testnet",
        head_b,
        1,
        wallets=wallets,
        private_state=state,
        existing_nodes=existing,
        no_contracts=True,
        publish_routes=False,
    )

    assert manifest["cell_id"] == "testnetb-super1"
    assert manifest["bootstrap"]["contracts_requested"] is False
    assert manifest["desired_counts"]["contracts"] == 0
    assert updates["node_hub_admin_cell_id"] == "testnetb-super1"
    fdb = manifest["foundationdb"]
    assert fdb["action"] == "join-existing-cluster"
    assert fdb["first_node"] is False
    assert fdb["current_coordinators"] == ["10.116.0.3:44550", "10.116.0.3:44551"]
    assert fdb["target_coordinators"] == ["10.116.0.3:44550", "10.116.0.3:44551", "10.124.0.3:44650"]
    assert [item["service_name"] for item in fdb["existing_nodes"]] == ["testneta-super1", "testneta-super2"]


def test_super_guard_treats_host_b_super1_as_joiner_when_fdb_joins_existing_network() -> None:
    script = control.super_server_command_script()

    assert 'return str(fdb_plan.get("action") or "") != "initialize-new-cluster"' in script
    assert 'return ordinal != 1 and str(fdb_plan.get("action") or "") != "initialize-new-cluster"' not in script


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

    def fake_service_items_for_client(client: object, tried: list[dict[str, object]], **kwargs: object) -> list[dict[str, object]]:
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

def test_service_items_for_client_retries_transient_coolify_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __init__(self, ok: bool, status: int, body: object) -> None:
            self.ok = ok
            self.status = status
            self.body = body
            self.method = "GET"
            self.path = "/api/v1/services"

    class FakeHubTool:
        def __init__(self) -> None:
            self.calls = 0

        def list_services(self, client: object) -> tuple[Response, list[dict[str, object]]]:
            self.calls += 1
            if self.calls == 1:
                return (
                    Response(
                        False,
                        0,
                        {
                            "error": "request_failed",
                            "message": "Coolify API request failed: timed out",
                            "error_type": "TimeoutError",
                        },
                    ),
                    [],
                )
            return Response(True, 200, {"services": []}), [{"name": "testneta-super1"}]

        def response_to_dict(self, response: Response) -> dict[str, object]:
            return {"ok": response.ok, "status": response.status, "body": response.body}

    fake = FakeHubTool()
    sleeps: list[float] = []
    monkeypatch.setattr(control, "hub_service_tool", lambda: fake)
    monkeypatch.setattr(control.time, "sleep", lambda seconds: sleeps.append(float(seconds)))

    tried: list[dict[str, object]] = []
    services = control.service_items_for_client(object(), tried, transient_attempts=2, transient_sleep_s=0.25)

    assert services == [{"name": "testneta-super1"}]
    assert fake.calls == 2
    assert sleeps == [0.25]
    assert tried[0]["transient_timeout"] is True
    assert tried[1]["transient_timeout"] is False


def test_service_items_for_client_does_not_retry_non_timeout_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        ok = False
        status = 401
        body = {"error": "unauthorized"}
        method = "GET"
        path = "/api/v1/services"

    class FakeHubTool:
        def __init__(self) -> None:
            self.calls = 0

        def list_services(self, client: object) -> tuple[Response, list[dict[str, object]]]:
            self.calls += 1
            return Response(), []

        def response_to_dict(self, response: Response) -> dict[str, object]:
            return {"ok": response.ok, "status": response.status, "body": response.body}

    fake = FakeHubTool()
    monkeypatch.setattr(control, "hub_service_tool", lambda: fake)

    with pytest.raises(control.AllfatherControlError, match="HTTP 401"):
        control.service_items_for_client(object(), [], transient_attempts=3, transient_sleep_s=0)

    assert fake.calls == 1



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


def test_remove_node_parse_requires_explicit_node(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)

    with pytest.raises(SystemExit):
        control.parse_args(
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


def test_remove_node_rejects_node_outside_requested_host(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "testnet",
            "--host",
            "coolify-a",
            "--node",
            "testnetb-super1",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "1",
        ]
    )
    plan = control.build_plan_from_args(args)

    with pytest.raises(control.AllfatherControlError, match="not a testnet super-node on coolify-a"):
        control.remove_node(plan, args)


def test_remove_node_dry_run_preserves_private_seed_material_by_default_when_network_goes_empty(tmp_path: Path) -> None:
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
            "--node",
            "testneta-super1",
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
    assert payload["private_state_updates"]["seed_material_cleaned"] is False
    assert payload["private_state_updates"]["identity_material_preserved"] is True
    assert payload["private_state_updates"]["removed"] == []
    assert payload["runtime_cleanup"]["enabled"] is True
    assert payload["runtime_cleanup"]["dry_run"] is True
    assert payload["runtime_cleanup"]["moves_runtime_state_dirs"] is True
    assert payload["runtime_cleanup"]["state_root_glob"].endswith("/testnet/coolify-a/testneta-super*")
    # Dry-run must not write secrets/state.
    assert "cluster_description: main-computer-testnet-allfather" in path.read_text(encoding="utf-8")


def test_remove_node_dry_run_prune_seed_material_rotates_identity_when_requested(tmp_path: Path) -> None:
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
        private_key: "0x2222222222222222222222222222222222222222222222222222222222222222"
        metadata:
          generated_by: tools/allfather_control.py:add-node
      captain:
        private_key: "0x3333333333333333333333333333333333333333333333333333333333333333"
        metadata:
          generated_by: tools/allfather_control.py:add-node
    node_seed_material:
      testneta-super1:
        wallets:
          hub_admin:
            private_key: "0x1111111111111111111111111111111111111111111111111111111111111111"
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
            "--node",
            "testneta-super1",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "1",
            "--prune-seed-material",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.remove_node(plan, args)

    assert payload["private_state_updates"]["dry_run"] is True
    assert payload["private_state_updates"]["identity_material_preserved"] is False
    assert payload["private_state_updates"]["seed_material_cleaned"] is True
    removed_kinds = {item["kind"] for item in payload["private_state_updates"]["removed"]}
    assert {"wallet_private_key", "node_seed_material", "foundationdb_seed"} <= removed_kinds


def test_remove_node_dry_run_removes_explicit_existing_super_node_without_renumbering(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "testnet",
            "--host",
            "coolify-a",
            "--node",
            "testneta-super3",
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
    assert payload["runtime_cleanup"]["enabled"] is False


def test_remove_node_keep_runtime_state_suppresses_last_node_runtime_cleanup(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "testnet",
            "--host",
            "coolify-a",
            "--node",
            "testneta-super1",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "1",
            "--keep-runtime-state",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.remove_node(plan, args)

    assert payload["network_pristine_after_remove"] is True
    assert payload["runtime_cleanup"]["enabled"] is False
    assert "--keep-runtime-state" in payload["runtime_cleanup"]["reason"]


def test_super_runtime_cleanup_compose_moves_only_matching_super_state(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]

    compose = control.render_super_runtime_cleanup_compose("testnet", head)

    assert "allfather-super-runtime-cleanup-testnet-coolify-a" in compose
    assert "/data/main-computer/allfather/supernodes:/host-supernodes" in compose
    assert "SUPER_PREFIX='testneta'" in compose
    assert '"$$NETWORK_ROOT"/"$$SUPER_PREFIX"-super[0-9]*' in compose
    assert "docker image rm" not in compose
    assert "main-computer/allfather-super-base" not in compose


def test_super_runtime_cleanup_probe_target_is_private_to_host(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]

    target = control.probe_target_for_super_runtime_cleanup("testnet", head)

    assert target["kind"] == "super-runtime-cleanup"
    assert target["service_name"] == "allfather-super-runtime-cleanup-testnet-coolify-a"
    assert target["guard_url"] == "http://10.116.0.3:41800"


def test_remove_node_requires_mainnet_confirmation(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "mainnet",
            "--host",
            "coolify-a",
            "--node",
            "mainneta-super1",
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


def test_remove_node_requires_delete_last_node_for_final_mainnet_node(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "mainnet",
            "--allow-mainnet",
            "--host",
            "coolify-a",
            "--node",
            "mainneta-super1",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "1",
        ]
    )
    plan = control.build_plan_from_args(args)

    with pytest.raises(control.AllfatherControlError, match="--delete-last-node"):
        control.remove_node(plan, args)


def test_remove_node_allows_final_mainnet_node_with_explicit_delete_last_node(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "mainnet",
            "--allow-mainnet",
            "--delete-last-node",
            "--host",
            "coolify-a",
            "--node",
            "mainneta-super1",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "1",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.remove_node(plan, args)

    assert payload["removed_last_network_node"] is True
    assert payload["remaining_network_super_nodes"] == 0
    assert payload["zero_node_cleanup"]["enabled"] is True
    assert payload["ok"] is True


def test_remove_node_errors_when_no_super_node_exists(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "testnet",
            "--host",
            "coolify-a",
            "--node",
            "testneta-super1",
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


def ready_internal_status_for_add_node(
    *,
    contracts_status: str = "deployed",
    validator_blocks: bool = True,
    validator_admitted: bool = True,
) -> dict[str, object]:
    return {
        "observed": True,
        "ok": True,
        "identity_ok": True,
        "topology_ok": True,
        "status_ok": True,
        "healthz_ok": True,
        "functions": {
            "foundationdb": {
                "running": True,
                "status": "running",
                "configured": True,
                "listening": True,
            },
            "validator_rpc": {
                "running": True,
                "status": "running" if validator_blocks else "waiting-qbft-block-production",
                "rpc_http_ok": True,
                "block_number": 7 if validator_blocks else 0,
                "block_production_ok": validator_blocks,
            },
            "validator_admission": {
                "desired": True,
                "required": True,
                "running": validator_admitted,
                "status": "admitted" if validator_admitted else "vote-requested",
                "admitted": validator_admitted,
                "validator_address": "0x31d79403d064ec1029b2472631a044fe7e3bf5a9",
            },
            "hub": {
                "running": True,
                "status": "running-bootstrap-listener",
                "health_ok": True,
            },
            "hub_admin": {
                "running": True,
                "status": "bootstrapped",
                "completed": True,
            },
            "contracts": {
                "running": contracts_status == "deployed",
                "status": contracts_status,
                "completed": contracts_status == "deployed",
            },
        },
    }


def test_add_node_ready_check_requires_first_node_contract_deployment() -> None:
    manifest = {
        "bootstrap": {"contracts_requested": True},
    }

    ready = control.add_node_super_ready_check(ready_internal_status_for_add_node(), manifest)
    assert ready["ready"] is True
    assert ready["components"]["contracts"] == "deployed"

    pending = ready_internal_status_for_add_node(contracts_status="deferred-until-hub-admin")
    not_ready = control.add_node_super_ready_check(pending, manifest)
    assert not_ready["ready"] is False
    assert "contracts not deployed" in not_ready["reason"]

    pending_tx = ready_internal_status_for_add_node(contracts_status="deployment-pending")
    pending_ready = control.add_node_super_ready_check(pending_tx, manifest)
    assert pending_ready["ready"] is False
    assert pending_ready["terminal"] is False
    assert "deployment-pending" in pending_ready["reason"]


def test_add_node_ready_check_requires_validator_block_production() -> None:
    manifest = {
        "bootstrap": {"contracts_requested": True},
    }

    pending = ready_internal_status_for_add_node(validator_blocks=False)

    not_ready = control.add_node_super_ready_check(pending, manifest)

    assert not_ready["ready"] is False
    assert "validator_rpc not producing blocks" in not_ready["reason"]
    assert "block_number=0" in not_ready["reason"]


def test_add_node_ready_check_reports_blocks_but_rpc_unreachable() -> None:
    manifest = {
        "bootstrap": {"contracts_requested": True},
    }
    pending = ready_internal_status_for_add_node()
    validator = pending["functions"]["validator_rpc"]
    validator.update(
        {
            "status": "waiting-validator-json-rpc-after-block-production",
            "rpc_http_ok": False,
            "json_rpc_ok": False,
            "block_production_ok": False,
            "log_block_production_ok": True,
            "block_number": 710,
            "block_production_error": "URLError: <urlopen error [Errno 111] Connection refused>",
            "shutdown_observed": True,
        }
    )

    not_ready = control.add_node_super_ready_check(pending, manifest)

    assert not_ready["ready"] is False
    assert "produced blocks but JSON-RPC is not reachable" in not_ready["reason"]
    assert "block_number=710" in not_ready["reason"]
    assert "shutdown_observed=true" in not_ready["reason"]


def test_add_node_ready_check_accepts_second_node_without_contract_redeploy() -> None:
    manifest = {
        "ordinal": 2,
        "bootstrap": {"contracts_requested": False},
    }
    status = ready_internal_status_for_add_node(contracts_status="not-required-existing-network")

    ready = control.add_node_super_ready_check(status, manifest)

    assert ready["ready"] is True
    assert ready["contracts_requested"] is False
    assert ready["components"]["hub_admin"] == "bootstrapped"
    assert ready["components"]["validator_admission"] == "admitted"


def test_add_node_ready_check_requires_second_node_validator_admission() -> None:
    manifest = {
        "ordinal": 2,
        "bootstrap": {"contracts_requested": False},
    }
    status = ready_internal_status_for_add_node(
        contracts_status="not-required-existing-network",
        validator_admitted=False,
    )

    not_ready = control.add_node_super_ready_check(status, manifest)

    assert not_ready["ready"] is False
    assert "validator admission not complete" in not_ready["reason"]
    assert "0x31d79403d064ec1029b2472631a044fe7e3bf5a9" in not_ready["reason"]


def test_add_node_parse_defaults_wait_for_remote_readiness(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)

    args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
        ]
    )

    assert args.preflight_wait_s == control.DEFAULT_ADD_NODE_PREFLIGHT_WAIT_S
    assert args.preflight_poll_s == control.DEFAULT_ADD_NODE_PREFLIGHT_POLL_S
    assert args.preflight_stable_s == control.DEFAULT_ADD_NODE_PREFLIGHT_STABLE_S
    assert args.deploy_wait_s == control.DEFAULT_ADD_NODE_READY_WAIT_S
    assert args.deploy_poll_s == control.DEFAULT_ADD_NODE_READY_POLL_S
    assert args.super_image == control.DEFAULT_SUPER_IMAGE
    assert args.super_base_source_image == control.DEFAULT_SUPER_BASE_SOURCE_IMAGE
    assert args.super_base_wait_s == control.DEFAULT_ADD_NODE_READY_WAIT_S
    assert args.no_super_base_ensure is False


def test_add_node_wait_uses_private_probe_ready_signal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    dry_args = control.parse_args(
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
    plan = control.build_plan_from_args(dry_args)
    manifest = control.add_node(plan, dry_args)["manifest"]
    head = control.choose_head_for_host(plan, "coolify-a")
    runtime_status = ready_internal_status_for_add_node()

    monkeypatch.setattr(
        control,
        "service_items_for_client",
        lambda client, tried: [{"name": "testneta-super1", "uuid": "service-uuid", "status": "running:healthy"}],
    )
    monkeypatch.setattr(
        control,
        "sync_probe_service",
        lambda client, plan, head, args, context, tried, super_inventory=None: ("probe-uuid", "updated", {}),
    )

    class FakeHubService:
        def trigger_deploy_service(self, client, *, service_uuid: str, force: bool, tried: list[dict[str, object]]) -> dict[str, object]:
            return {"ok": True, "service_uuid": service_uuid, "force": force}

    monkeypatch.setattr(control, "hub_service_tool", lambda: FakeHubService())

    def fake_wait_for_probe_metadata_result(client, service_uuid, tried, *, expected_targets, wait_s):
        target = next(item for item in expected_targets if item.get("kind") == "super-node")
        return {}, {
            "ok": True,
            "result": {
                "targets": [
                    {
                        "kind": "super-node",
                        "service_name": target["service_name"],
                        **runtime_status,
                    }
                ]
            },
        }

    monkeypatch.setattr(control, "wait_for_probe_metadata_result", fake_wait_for_probe_metadata_result)

    wait_args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--deploy-wait-s",
            "10",
            "--deploy-poll-s",
            "0.1",
        ]
    )

    result = control.wait_for_add_node_ready(
        plan,
        head,
        manifest,
        client=object(),
        args=wait_args,
        context={},
        tried=[],
        service_uuid="service-uuid",
    )

    assert result["ready"] is True
    assert result["private_probe_observed"] is True
    assert result["readiness"]["components"]["foundationdb"] == "running"
    assert result["ssh_used"] is False
    assert result["direct_vpn_used"] is False


def test_add_node_wait_does_not_treat_transient_exited_as_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    dry_args = control.parse_args(
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
    plan = control.build_plan_from_args(dry_args)
    manifest = control.add_node(plan, dry_args)["manifest"]
    head = control.choose_head_for_host(plan, "coolify-a")
    calls = {"services": 0, "probe": 0}

    def fake_service_items_for_client(client: object, tried: list[dict[str, object]], **kwargs: object) -> list[dict[str, object]]:
        calls["services"] += 1
        if calls["services"] == 1:
            return [{"name": "testneta-super1", "uuid": "service-uuid", "status": "exited"}]
        return [{"name": "testneta-super1", "uuid": "service-uuid", "status": "running:healthy"}]

    monkeypatch.setattr(control, "service_items_for_client", fake_service_items_for_client)
    monkeypatch.setattr(
        control,
        "sync_probe_service",
        lambda client, plan, head, args, context, tried, super_inventory=None: ("probe-uuid", "updated", {}),
    )

    class FakeHubService:
        def trigger_deploy_service(self, client, *, service_uuid: str, force: bool, tried: list[dict[str, object]]) -> dict[str, object]:
            return {"ok": True, "service_uuid": service_uuid, "force": force}

    monkeypatch.setattr(control, "hub_service_tool", lambda: FakeHubService())

    def fake_wait_for_probe_metadata_result(client, service_uuid, tried, *, expected_targets, wait_s):
        calls["probe"] += 1
        target = next(item for item in expected_targets if item.get("kind") == "super-node")
        if calls["probe"] == 1:
            runtime_status = {
                "observed": True,
                "ok": False,
                "healthz_ok": False,
                "identity_ok": False,
                "topology_ok": False,
                "status_ok": False,
                "error": "URLError: <urlopen error [Errno 111] Connection refused>",
                "functions": {},
            }
        else:
            runtime_status = ready_internal_status_for_add_node()
        return {}, {
            "ok": True,
            "result": {
                "targets": [
                    {
                        "kind": "super-node",
                        "service_name": target["service_name"],
                        **runtime_status,
                    }
                ]
            },
        }

    monkeypatch.setattr(control, "wait_for_probe_metadata_result", fake_wait_for_probe_metadata_result)

    wait_args = control.parse_args(
        [
            "add-node",
            "testnet",
            "--host",
            "coolify-a",
            "--private-state",
            str(path),
            "--deploy-wait-s",
            "10",
            "--deploy-poll-s",
            "0.1",
        ]
    )

    result = control.wait_for_add_node_ready(
        plan,
        head,
        manifest,
        client=object(),
        args=wait_args,
        context={},
        tried=[],
        service_uuid="service-uuid",
    )

    assert result["ready"] is True
    assert result["coolify_status"] == "running:healthy"
    assert calls["probe"] >= 2


def test_add_node_preflight_waits_for_stable_clean_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    calls = {"count": 0}

    def fake_service_items_for_client(client: object, tried: list[dict[str, object]], **kwargs: object) -> list[dict[str, object]]:
        calls["count"] += 1
        if calls["count"] == 1:
            return [{"name": "testneta-super1", "uuid": "stale-service", "status": "deleting"}]
        return []

    monkeypatch.setattr(control, "service_items_for_client", fake_service_items_for_client)
    monkeypatch.setattr(control.time, "sleep", lambda seconds: None)
    now = {"value": 0.0}

    def fake_monotonic() -> float:
        now["value"] += 1.0
        return now["value"]

    monkeypatch.setattr(control.time, "monotonic", fake_monotonic)

    result = control.wait_for_add_node_slot_preflight(
        object(),
        network_key="testnet",
        head=head,
        service_name="testneta-super1",
        expected_existing_ordinals=[],
        tried=[],
        wait_s=10,
        poll_s=0.1,
        stable_s=1,
    )

    assert result["ready"] is True
    assert result["service_name"] == "testneta-super1"
    assert result["observed_ordinals"] == []
    assert calls["count"] >= 2


def test_add_node_preflight_refuses_changed_ordinal_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]

    monkeypatch.setattr(
        control,
        "service_items_for_client",
        lambda client, tried: [{"name": "testneta-super1", "uuid": "existing", "status": "running"}],
    )
    monkeypatch.setattr(control.time, "sleep", lambda seconds: None)

    result = control.wait_for_add_node_slot_preflight(
        object(),
        network_key="testnet",
        head=head,
        service_name="testneta-super1",
        expected_existing_ordinals=[],
        tried=[],
        wait_s=0,
        poll_s=0.1,
        stable_s=0,
    )

    assert result["ready"] is False
    assert "expected existing ordinal" in result["reason"] or "still reports target service" in result["reason"]


def test_synced_service_predeploy_requires_single_matching_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        control,
        "service_items_for_client",
        lambda client, tried: [{"name": "testneta-super1", "uuid": "expected", "status": "created"}],
    )

    result = control.require_synced_super_service_ready_for_deploy(
        object(),
        service_name="testneta-super1",
        service_uuid="expected",
        tried=[],
    )

    assert result["match_count"] == 1
    assert result["service_uuid"] == "expected"

    monkeypatch.setattr(
        control,
        "service_items_for_client",
        lambda client, tried: [
            {"name": "testneta-super1", "uuid": "one", "status": "created"},
            {"name": "testneta-super1", "uuid": "two", "status": "created"},
        ],
    )

    with pytest.raises(control.AllfatherControlError, match="matching service records"):
        control.require_synced_super_service_ready_for_deploy(
            object(),
            service_name="testneta-super1",
            service_uuid="one",
            tried=[],
        )


def test_super_compose_uses_stable_container_name_and_keeps_deployment_id_metadata(tmp_path: Path) -> None:
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
            "0",
            "--include-compose",
        ]
    )
    plan = control.build_plan_from_args(args)
    payload = control.add_node(plan, args)

    assert payload["manifest"]["deployment_id"] == "dry-run"
    assert 'container_name: "testneta-super1"' in payload["compose"]
    assert 'container_name: "testneta-super1-dry-run"' not in payload["compose"]
    assert "MC_ALLFATHER_DEPLOYMENT_ID:" in payload["compose"]


def test_super_container_name_matches_public_hub_route_backend() -> None:
    manifest = {
        "cell_id": "mainneta-super1",
        "deployment_id": "1784334999-4baa04c2",
        "network_key": "mainnet",
        "components": {"hub": "mainneta-hub1"},
        "ports": {"hub_container": 8785},
    }

    assert control.super_container_name_from_manifest(manifest) == "mainneta-super1"
    route = control.local_hub_route_records(
        "mainnet",
        control.HeadNode(
            head_id="head-a",
            service_name="allfather-head-coolify-a",
            coolify_server="coolify-a",
            slot="A",
            guard_container_port=41414,
            guard_host_port=41414,
            guard_publish_host="10.0.0.1",
            guard_url="http://10.0.0.1:41414",
            state_root="/data/allfather/head-a",
            peers=(),
        ),
        [
            {
                "service_name": "mainneta-super1",
                "ordinal": 1,
                "components": {"hub": "mainneta-hub1"},
            }
        ],
        domain_suffix="greatlibrary.io",
    )[0]

    assert route["backend_url"] == "http://mainneta-super1:8785"

def test_super_contract_fee_cap_and_gas_limit_are_bounded_at_runtime() -> None:
    super_script = control.super_server_command_script()

    assert "MC_ALLFATHER_CONTRACT_GAS_LIMIT" in super_script
    assert "MC_ALLFATHER_MAX_CONTRACT_GAS_PRICE_WEI" in super_script
    assert "transaction fee cap exceeded" in super_script
    assert "max_gas_price_wei" in super_script
    assert '"gas": gas_limit' in super_script
    assert "return cap_contract_gas_price(" in super_script
    assert "MC_ALLFATHER_MIN_CONTRACT_GAS_PRICE_WEI" in super_script
    assert "deployer_balance" in super_script
    assert "upfront_cost" in super_script



def test_ethereum_address_derivation_is_local_and_deterministic() -> None:
    assert control.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert (
        control.ethereum_address_from_private_key("0x" + "0" * 63 + "1")
        == "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"
    )


def test_add_node_materializes_wallet_addresses_with_private_keys(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    state = control.load_yaml_mapping(path)

    _, wallets, updates = control.materialize_private_state_for_add_node(
        state,
        path,
        "mainnet",
        ordinal=1,
        cell_id="mainneta-super1",
        no_contracts=False,
        dry_run=False,
    )

    written = control.load_yaml_mapping(path)
    network_wallets = written["networks"]["mainnet"]["wallets"]
    deployer = network_wallets["deployer"]
    captain = network_wallets["captain"]
    o1 = network_wallets["o1"]
    o2 = network_wallets["o2"]
    o3 = network_wallets["o3"]
    node_hub_admin = written["networks"]["mainnet"]["node_seed_material"]["mainneta-super1"]["wallets"]["hub_admin"]

    assert deployer["private_key"]
    assert deployer["address"] == control.ethereum_address_from_private_key(deployer["private_key"])
    assert deployer["metadata"]["address_derivation"] == "local-derive-from-private-key"
    assert node_hub_admin["private_key"]
    assert node_hub_admin["address"] == control.ethereum_address_from_private_key(node_hub_admin["private_key"])
    assert node_hub_admin["metadata"]["address_derivation"] == "local-derive-from-private-key"
    assert captain["private_key"] == node_hub_admin["private_key"]
    assert captain["address"] == node_hub_admin["address"]
    assert o1["private_key"] == deployer["private_key"]
    assert o1["address"] == deployer["address"]
    assert o2["private_key"] == control.deterministic_governance_office_private_key("mainnet", "mainneta-super1", 2)
    assert o2["address"] == control.ethereum_address_from_private_key(o2["private_key"])
    assert o3["private_key"] == control.deterministic_governance_office_private_key("mainnet", "mainneta-super1", 3)
    assert o3["address"] == control.ethereum_address_from_private_key(o3["private_key"])
    assert control.wallet_address(wallets, "deployer") == deployer["address"]
    assert control.wallet_address(wallets, "hub_admin") == node_hub_admin["address"]
    assert control.wallet_address(wallets, "captain") == captain["address"]
    assert control.wallet_address(wallets, "o1") == o1["address"]
    assert updates["written"] is True
    assert "deployer" in updates["wallets_generated"]
    assert "hub_admin" in updates["wallets_generated"]
    assert {"captain", "o1", "o2", "o3"}.issubset(set(updates["wallets_generated"]))


def test_add_node_backfills_addresses_for_existing_generated_private_keys(tmp_path: Path) -> None:
    path = tmp_path / "all_father.private.yaml"
    deployer_key = "0x" + "0" * 63 + "1"
    hub_admin_key = "0x" + "0" * 63 + "2"
    path.write_text(
        f"""
kind: main_computer.all_father.private_state.v1
networks:
  mainnet:
    wallets:
      deployer:
        address: null
        private_key: "{deployer_key}"
        metadata:
          address_derivation: runtime-derive-from-private-key
    node_seed_material:
      mainneta-super1:
        wallets:
          hub_admin:
            address: null
            private_key: "{hub_admin_key}"
            metadata:
              address_derivation: runtime-derive-from-private-key
""".lstrip(),
        encoding="utf-8",
    )
    state = control.load_yaml_mapping(path)

    _, _, updates = control.materialize_private_state_for_add_node(
        state,
        path,
        "mainnet",
        ordinal=1,
        cell_id="mainneta-super1",
        no_contracts=False,
        dry_run=False,
    )

    written = control.load_yaml_mapping(path)
    network_wallets = written["networks"]["mainnet"]["wallets"]
    deployer = network_wallets["deployer"]
    captain = network_wallets["captain"]
    o1 = network_wallets["o1"]
    o2 = network_wallets["o2"]
    o3 = network_wallets["o3"]
    node_hub_admin = written["networks"]["mainnet"]["node_seed_material"]["mainneta-super1"]["wallets"]["hub_admin"]

    assert deployer["address"] == control.ethereum_address_from_private_key(deployer_key)
    assert deployer["metadata"]["address_derivation"] == "local-derive-from-private-key"
    assert node_hub_admin["address"] == control.ethereum_address_from_private_key(hub_admin_key)
    assert node_hub_admin["metadata"]["address_derivation"] == "local-derive-from-private-key"
    assert captain["private_key"] == hub_admin_key
    assert captain["address"] == control.ethereum_address_from_private_key(hub_admin_key)
    assert o1["private_key"] == deployer_key
    assert o1["address"] == control.ethereum_address_from_private_key(deployer_key)
    assert o2["private_key"] == control.deterministic_governance_office_private_key("mainnet", "mainneta-super1", 2)
    assert o2["address"] == control.ethereum_address_from_private_key(o2["private_key"])
    assert o3["private_key"] == control.deterministic_governance_office_private_key("mainnet", "mainneta-super1", 3)
    assert o3["address"] == control.ethereum_address_from_private_key(o3["private_key"])
    assert updates["written"] is True
    wallet_updates = [item for item in updates["generated"] if str(item.get("kind", "")).startswith("wallet_")]
    assert {item["wallet"] for item in wallet_updates} >= {"deployer", "hub_admin", "captain", "o1", "o2", "o3"}


def test_add_node_reuses_preserved_office_wallets_for_new_first_node_cell(tmp_path: Path) -> None:
    path = tmp_path / "all_father.private.yaml"
    captain_key = "0x" + "1" * 64
    old_hub_admin_key = "0x" + "2" * 64
    deployer_key = "0x" + "3" * 64
    o2_key = "0x" + "4" * 64
    o3_key = "0x" + "5" * 64
    path.write_text(
        f"""
kind: main_computer.all_father.private_state.v1
networks:
  mainnet:
    wallets:
      deployer:
        private_key: "{deployer_key}"
      captain:
        private_key: "{captain_key}"
        address: "{control.ethereum_address_from_private_key(captain_key)}"
      o2:
        private_key: "{o2_key}"
        address: "{control.ethereum_address_from_private_key(o2_key)}"
      o3:
        private_key: "{o3_key}"
        address: "{control.ethereum_address_from_private_key(o3_key)}"
    node_seed_material:
      mainneta-super1:
        wallets:
          hub_admin:
            private_key: "{old_hub_admin_key}"
            address: "{control.ethereum_address_from_private_key(old_hub_admin_key)}"
""".lstrip(),
        encoding="utf-8",
    )
    state = control.load_yaml_mapping(path)

    _, wallets, updates = control.materialize_private_state_for_add_node(
        state,
        path,
        "mainnet",
        ordinal=1,
        cell_id="mainnetc-super1",
        no_contracts=False,
        dry_run=False,
    )

    written = control.load_yaml_mapping(path)
    new_hub_admin = written["networks"]["mainnet"]["node_seed_material"]["mainnetc-super1"]["wallets"]["hub_admin"]
    assert new_hub_admin["private_key"] == captain_key
    assert new_hub_admin["address"] == control.ethereum_address_from_private_key(captain_key)
    assert written["networks"]["mainnet"]["wallets"]["captain"]["private_key"] == captain_key
    assert written["networks"]["mainnet"]["wallets"]["o2"]["private_key"] == o2_key
    assert written["networks"]["mainnet"]["wallets"]["o3"]["private_key"] == o3_key
    assert control.wallet_private_key(wallets, "hub_admin") == captain_key
    assert any(item["wallet"] == "hub_admin" and item["kind"] == "wallet_private_key" for item in updates["generated"])


def test_super_compose_passes_private_state_governance_office_keys_to_runtime() -> None:
    key0 = "0x" + "1" * 64
    key2 = "0x" + "4" * 64
    compose = control.render_super_node_compose(
        {
            "cell_id": "mainneta-super1",
            "network_key": "mainnet",
            "deployment_id": "test",
            "ports": {
                "guard_container": 41414,
                "guard_host": 41600,
                "hub_container": 8785,
                "rpc_container": 8545,
                "fdb_container": 4550,
                "fdb_host": 44650,
                "p2p_container": 30303,
                "p2p_host": 46300,
            },
            "foundationdb": {},
            "bootstrap": {"contracts_requested": True},
            "state_root": "/data/main-computer/allfather/supernodes/mainnet/coolify-a/mainneta-super1",
        },
        hub_admin_private_key=key0,
        deployer_private_key="0x" + "3" * 64,
        governance_office_private_keys={"captain": key0, "o2": key2},
    )

    assert f'MC_ALLFATHER_GOVERNANCE_OFFICE_0_PRIVATE_KEY: "{key0}"' in compose
    assert f'MC_ALLFATHER_GOVERNANCE_OFFICE_2_PRIVATE_KEY: "{key2}"' in compose
    assert "governance_office_env_private_key" in control.super_server_command_script()
    assert "private-state-office" in control.super_server_command_script()


def test_hub_propagate_parser_replaces_traefik_name(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    args = control.parse_args(
        [
            "hub-propagate",
            "mainnet",
            "--allow-mainnet",
            "--dry-run",
            "--no-contract-admin-sync",
            "--private-state",
            str(path),
        ]
    )

    assert args.command == "hub-propagate"
    assert args.network == "mainnet"
    assert args.no_contract_admin_sync is True


def test_hub_propagate_syncs_full_hub_runtime_by_default(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    args = control.parse_args(
        [
            "hub-propagate",
            "mainnet",
            "--allow-mainnet",
            "--dry-run",
            "--private-state",
            str(path),
        ]
    )

    assert args.no_full_hub_runtime_sync is False
    assert args.full_hub_runtime_wait_s is None

def test_hub_propagate_full_runtime_sync_emits_operator_diagnostics() -> None:
    source = Path(control.REPO_ROOT / "tools" / "allfather_control.py").read_text(encoding="utf-8")

    assert "full-hub-sync: host=" in source
    assert "health-before-start" in source
    assert "service-sync-start" in source
    assert "service-detail-after-sync-start" in source
    assert "deploy-trigger-start" in source
    assert "wait-full-health-start" in source
    assert "full-hub-sync: Coolify deploy status" in source
    assert "coolify_service_after_sync" in source
    assert "stale-bootstrap-candidate" in source
    assert "stale image/container cache" in source
    assert '"diagnostics": {"steps": steps}' in source


def test_hub_propagate_full_runtime_status_interval_arg(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    args = control.parse_args(
        [
            "hub-propagate",
            "mainnet",
            "--allow-mainnet",
            "--dry-run",
            "--private-state",
            str(path),
            "--full-hub-runtime-status-interval-s",
            "7",
        ]
    )

    assert args.full_hub_runtime_status_interval_s == 7


def test_hub_propagate_full_runtime_stale_bootstrap_fail_arg(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    args = control.parse_args(
        [
            "hub-propagate",
            "mainnet",
            "--allow-mainnet",
            "--dry-run",
            "--private-state",
            str(path),
            "--full-hub-runtime-stale-bootstrap-fail-s",
            "9",
        ]
    )

    assert args.full_hub_runtime_stale_bootstrap_fail_s == 9


def test_coolify_snapshot_detects_running_full_runtime_compose() -> None:
    snapshot = {
        "service": {"status": "running:healthy"},
        "compose": {
            "any_contains_full_runtime": True,
            "any_contains_deployment_id": True,
        },
    }

    assert control.coolify_snapshot_has_running_full_runtime_compose(snapshot) is True


def test_coolify_service_detail_compose_diagnostics_detects_full_runtime() -> None:
    deployment_id = "deploy-abc"
    compose = f"""
services:
  mainneta-super1:
    build:
      dockerfile_inline: |
        COPY something /opt/main-computer-src.zip
        ENV MC_ALLFATHER_IMAGE_CAPABILITIES=hub-full
    environment:
      MC_ALLFATHER_DEPLOYMENT_ID: {deployment_id}
      MC_ALLFATHER_FULL_HUB_RUNTIME_REQUESTED: "1"
      MC_ALLFATHER_SUPER_MANIFEST_B64: full_hub_runtime_requested
"""
    encoded = base64.b64encode(compose.encode("utf-8")).decode("ascii")
    detail = {"ok": True, "body": {"docker_compose_raw": encoded}}

    diagnostics = control.service_detail_compose_diagnostics(detail, expected_deployment_id=deployment_id)

    assert diagnostics["observed"] is True
    assert diagnostics["any_contains_full_runtime"] is True
    assert diagnostics["any_contains_deployment_id"] is True


def test_hub_propagate_marker_waits_emit_poll_diagnostics() -> None:
    source = Path(control.REPO_ROOT / "tools" / "allfather_control.py").read_text(encoding="utf-8")

    assert "waiting Traefik marker" in source
    assert "waiting Hub admin sync marker" in source
    assert "last_status" in source
    assert "attempts" in source
    assert "args=args" in source


def test_hub_propagate_materializes_huddle_admin_records(tmp_path: Path) -> None:
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
  mainnet:
    wallets:
      hub_admin:
        address: "0x1111111111111111111111111111111111111111"
        private_key: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""".lstrip(),
        encoding="utf-8",
    )
    state = control.load_yaml_mapping(path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    nodes = [
        control.super_inventory_entry("mainnet", plan.heads[0], 1, source="test"),
        control.super_inventory_entry("mainnet", plan.heads[0], 2, source="test"),
    ]

    next_state, admins, updates = control.materialize_private_state_for_hub_propagate(
        state,
        path,
        "mainnet",
        nodes,
        dry_run=False,
    )

    assert updates["written"] is True
    assert updates["active_hub_admin_count"] == 2
    assert {item["cell_id"] for item in admins} == {"mainneta-super1", "mainneta-super2"}
    assert all(item["private_key_present"] for item in admins)
    assert all(re.fullmatch(r"0x[0-9A-Fa-f]{40}", str(item["address"])) for item in admins)
    assert updates["active_hub_admin_address_count"] == 2
    huddle = next_state["networks"]["mainnet"]["huddle"]["hub_admins"]
    assert set(huddle["active"]) == {"mainneta-super1", "mainneta-super2"}
    assert all(re.fullmatch(r"0x[0-9A-Fa-f]{40}", str(item["address"])) for item in huddle["active"].values())
    assert huddle["active"]["mainneta-super1"]["address"] == next_state["networks"]["mainnet"]["node_seed_material"]["mainneta-super1"]["wallets"]["hub_admin"]["address"]
    assert huddle["active"]["mainneta-super1"]["private_key_path"].endswith(".mainneta-super1.wallets.hub_admin.private_key")
    assert huddle["retired"][0]["kind"] == "legacy-network-hub-admin"
    assert huddle["retired"][0]["address"] == "0x1111111111111111111111111111111111111111"


def test_head_agent_hub_admin_sync_request_is_redacted_in_compose(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    request = {
        "network_key": "mainnet",
        "coolify_server": "coolify-a",
        "service_name": "allfather-hub-admin-sync-mainnet-coolify-a",
        "request_id": "abc123",
        "executor_service_name": "mainneta-super1",
        "contract_address": "0x2222222222222222222222222222222222222222",
        "owner_private_key": "0x" + "a" * 64,
        "admins": [
            {
                "cell_id": "mainneta-super1",
                "address": "",
                "private_key": "0x" + "b" * 64,
            }
        ],
    }

    compose = control.render_head_compose(
        plan,
        head,
        probe_targets=[],
        callback_api_url="https://coolify-a.example.invalid",
        callback_token="token-a",
        callback_service_uuid="head-uuid",
        hub_admin_sync_request=request,
    )

    assert "MC_ALLFATHER_HUB_ADMIN_SYNC_REQUEST_B64" in compose
    assert "ALLFATHER_HUB_ADMIN_SYNC_RESULT_B64:" in compose
    assert "/hub-propagate/status" in compose
    assert "0x" + "a" * 64 not in compose
    assert "0x" + "b" * 64 not in compose


def test_head_agent_reads_hub_admin_sync_request_env() -> None:
    script = control.head_server_command_script()

    assert 'HUB_ADMIN_SYNC_REQUEST_B64 = os.environ.get("MC_ALLFATHER_HUB_ADMIN_SYNC_REQUEST_B64", "")' in script
    assert script.index("HUB_ADMIN_SYNC_REQUEST_B64 = os.environ.get") < script.index("HUB_ADMIN_SYNC_REQUEST = decode_json_b64")


def test_hub_admin_sync_request_sends_addresses_not_hub_private_keys(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    admin_key = "0x" + "b" * 64

    request = control.hub_admin_sync_request_payload(
        "mainnet",
        head,
        {"service_name": "mainneta-super1"},
        [
            {
                "cell_id": "mainneta-super1",
                "service_name": "mainneta-super1",
                "private_key": admin_key,
            }
        ],
        contract_address="0x2222222222222222222222222222222222222222",
        owner_private_key="0x" + "a" * 64,
    )

    assert request["admins"][0]["address"] == control.ethereum_address_from_private_key(admin_key)
    assert "private_key" not in request["admins"][0]
    assert admin_key not in json.dumps(request, sort_keys=True)


def test_deploy_contracts_parser_requires_explicit_escrow_target(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    args = control.parse_args(
        [
            "deploy-contracts",
            "mainnet",
            "--allow-mainnet",
            "--deploy-escrow",
            "--dry-run",
            "--private-state",
            str(path),
        ]
    )

    assert args.command == "deploy-contracts"
    assert args.network == "mainnet"
    assert args.deploy_escrow is True
    assert args.dry_run is True


def test_contract_deploy_request_sends_controller_address_not_hub_private_key(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    admin_key = "0x" + "b" * 64
    admin_address = control.ethereum_address_from_private_key(admin_key)

    request = control.contract_deploy_request_payload(
        "mainnet",
        head,
        {"service_name": "mainneta-super1"},
        [
            {
                "cell_id": "mainneta-super1",
                "service_name": "mainneta-super1",
                "address": admin_address,
                "private_key": admin_key,
            }
        ],
        owner_private_key="0x" + "a" * 64,
        deploy_escrow=True,
    )

    assert request["targets"] == ["hub_credit_bridge_escrow"]
    assert request["hub_credit_bridge_escrow_controller"] == admin_address
    assert request["hub_admin_address"] == admin_address
    assert request["executor_service_name"] == "mainneta-super1"
    assert "contract_sources_b64" in request
    source = base64.b64decode(request["contract_sources_b64"]["src/HubCreditBridgeEscrow.sol"]).decode("utf-8")
    assert "setBridgeControllerAllowed" in source
    assert "isBridgeController" in source
    assert request["contract_source_sha256"]
    assert "private_key" not in json.dumps({k: v for k, v in request.items() if k != "owner_private_key"}, sort_keys=True)
    assert admin_key not in json.dumps(request, sort_keys=True)


def test_update_contract_config_for_network_writes_returned_escrow_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "mainnet_contracts.json"
    config.write_text(
        json.dumps(
            {
                "alpha-beta-lockout": "0x1111111111111111111111111111111111111111",
                "hub_credit_bridge_escrow": "0x2222222222222222222222222222222222222222",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(control.ALLFATHER_CONTRACT_CONFIG_FILES, "mainnet", config)

    update = control.update_contract_config_for_network(
        "mainnet",
        {"hub_credit_bridge_escrow": {"address": "0x3333333333333333333333333333333333333333"}},
        dry_run=False,
    )

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert update["written"] is True
    assert payload["alpha-beta-lockout"] == "0x1111111111111111111111111111111111111111"
    assert payload["hub_credit_bridge_escrow"] == "0x3333333333333333333333333333333333333333"


def test_head_agent_contract_deploy_executor_script_does_not_use_invalid_nonlocal_nonce() -> None:
    script = control.head_server_command_script()

    assert "nonlocal nonce" not in script
    assert 'nonce_state = {{"value": w3.eth.get_transaction_count(owner.address)}}' in script
    assert 'nonce_state["value"] = nonce + 1' in script


def test_head_agent_contract_transactions_force_legacy_gas_price() -> None:
    script = control.head_server_command_script()

    assert "def legacy_gas_price(w3):" in script
    assert 'gas_price = legacy_gas_price(w3)' in script
    assert 'setBridgeControllerAllowed(addr, True).build_transaction({{"from": owner.address, "nonce": nonce, "chainId": chain_id, "gasPrice": gas_price}})' in script
    assert 'contract.constructor(*constructor_args).build_transaction({{"from": owner.address, "nonce": nonce, "chainId": int(w3.eth.chain_id), "gasPrice": gas_price}})' in script
    assert 'tx.pop("maxFeePerGas", None)' in script
    assert 'tx.pop("maxPriorityFeePerGas", None)' in script


def test_head_agent_contract_deploy_request_env_and_status_route(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    compose = control.render_head_compose(
        plan,
        head,
        contract_deploy_request={
            "network_key": "mainnet",
            "request_id": "deploy123",
            "targets": ["hub_credit_bridge_escrow"],
            "owner_private_key": "0x" + "a" * 64,
        },
    )
    script = control.head_server_command_script()

    assert "MC_ALLFATHER_CONTRACT_DEPLOY_REQUEST_B64" in compose
    assert "ALLFATHER_CONTRACT_DEPLOY_RESULT_B64:" in script
    assert "CONTRACT_DEPLOY_REQUEST = decode_json_b64(CONTRACT_DEPLOY_REQUEST_B64, {})" in script
    assert "/deploy-contracts/status" in script
    assert "compile_contract_sources_from_request" in script
    assert "ExtraDataToPOAMiddleware" in script
    assert "request-contract-sources-solc" in script
    assert "0x" + "a" * 64 not in compose
    compile(script, "<head-agent-script>", "exec")


def test_head_agent_full_hub_runtime_diagnostics_marker_and_env_present() -> None:
    script = control.head_server_command_script()
    assert "ALLFATHER_FULL_HUB_RUNTIME_DIAG_RESULT_B64:" in script
    assert "MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64" in script
    assert "inspect_full_hub_runtime_container" in script
    assert "/opt/main-computer-src.zip" in script
    assert "/opt/main-computer-src/main_computer/hub.py" in script
    assert "main_computer.hub" in script


def test_full_hub_runtime_diag_summary_includes_container_runtime_facts() -> None:
    summary = control.compact_full_hub_runtime_diag_node_summary(
        {
            "service_name": "mainneta-super1",
            "ok": True,
            "container_id": "abc123",
            "runtime_requested_env": "1",
            "deployment_id_file": "dep-1",
            "docker": {
                "state_status": "running",
                "health_status": "healthy",
                "env": {"MC_ALLFATHER_DEPLOYMENT_ID": "dep-1"},
            },
            "runtime": {
                "paths": {
                    "/opt/main-computer-src.zip": {"exists": True},
                    "/opt/main-computer-src/main_computer/hub.py": {"exists": True},
                },
                "imports": {"main_computer.hub": {"ok": True, "has_serve_hub": True}},
                "local_health": {"json": {"bootstrap_hub": False, "full_main_computer_hub": True}},
            },
        }
    )

    assert "mainneta-super1" in summary
    assert "src_zip=True" in summary
    assert "hub_py=True" in summary
    assert "hub_import=True" in summary
    assert "serve_hub=True" in summary
    assert "local_full=True" in summary


def test_hub_propagate_source_contains_full_runtime_failure_skip() -> None:
    source = Path(control.__file__).read_text(encoding="utf-8")
    assert "skipping Traefik/admin handoff because full hub runtime sync failed" in source
    assert "see full_hub_runtime_container_diagnostics" in source



def test_full_hub_runtime_diag_request_selects_failed_nodes() -> None:
    head = control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/heads/coolify-a",
        peers=(),
    )
    request = control.full_hub_runtime_diag_request_payload(
        "mainnet",
        head,
        {
            "nodes": [
                {
                    "ok": False,
                    "service_name": "mainneta-super1",
                    "domain": "mainneta-hub1.greatlibrary.io",
                    "diagnostics": {"steps": [{"step": "manifest-build-done", "deployment_id": "dep-123"}]},
                },
                {"ok": True, "service_name": "mainneta-super2"},
            ]
        },
    )
    assert request["network_key"] == "mainnet"
    assert request["coolify_server"] == "coolify-a"
    assert request["nodes"] == [
        {
            "service_name": "mainneta-super1",
            "domain": "mainneta-hub1.greatlibrary.io",
            "expected_deployment_id": "dep-123",
            "reason": "",
        }
    ]


def test_sync_head_service_accepts_full_hub_runtime_diag_request_in_compose() -> None:
    head = control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/heads/coolify-a",
        peers=(),
    )
    plan = control.HeadPlan(
        kind="main_computer.all_father.head_plan.v1",
        private_state_path="runtime/state/all_father.private.yaml",
        heads=(head,),
        desired_counts={"allfather_heads": 1},
        guardrails={},
    )
    compose = control.render_head_compose(
        plan,
        head,
        full_hub_runtime_diag_request={
            "request_id": "req-1",
            "nodes": [{"service_name": "mainneta-super1"}],
        },
    )
    assert "MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64" in compose
    encoded = re.search(r"MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64: \"([^\"]+)\"", compose)
    assert encoded
    payload = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
    assert payload["request_id"] == "req-1"
    assert payload["nodes"][0]["service_name"] == "mainneta-super1"


def test_full_hub_runtime_diag_large_request_is_chunked_for_head_agent_env() -> None:
    head = control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/heads/coolify-a",
        peers=(),
    )
    plan = control.HeadPlan(
        kind="main_computer.all_father.head_plan.v1",
        private_state_path="runtime/state/all_father.private.yaml",
        heads=(head,),
        desired_counts={"allfather_heads": 1},
        guardrails={},
    )
    request = {
        "request_id": "req-large",
        "nodes": [{"service_name": "mainneta-super1"}],
        "repair_missing_runtime": True,
        "diagnostic_note": "x" * (control.DEFAULT_HEAD_AGENT_ENV_CHUNK_SIZE + 1234),
    }

    compose = control.render_head_compose(plan, head, full_hub_runtime_diag_request=request)

    assert "MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64_CHUNKS" in compose
    chunks = re.findall(r"MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64_\d{3}: \"([^\"]*)\"", compose)
    assert len(chunks) >= 2
    assert max(len(chunk) for chunk in chunks) <= control.DEFAULT_HEAD_AGENT_ENV_CHUNK_SIZE
    assert "MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64_SHA256" in compose
    assert "MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64_TOTAL_LENGTH" in compose


def test_full_hub_runtime_repair_archive_is_embedded_in_head_agent_script_not_diag_env() -> None:
    head = control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/heads/coolify-a",
        peers=(),
    )
    plan = control.HeadPlan(
        kind="main_computer.all_father.head_plan.v1",
        private_state_path="runtime/state/all_father.private.yaml",
        heads=(head,),
        desired_counts={"allfather_heads": 1},
        guardrails={},
    )
    archive = "runtime-archive-payload"
    compose = control.render_head_compose(
        plan,
        head,
        full_hub_runtime_diag_request={
            "request_id": "req-archive",
            "nodes": [{"service_name": "mainneta-super1"}],
            "repair_missing_runtime": True,
            "runtime_archive_b64": archive,
        },
    )

    encoded = re.search(r"MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64: \"([^\"]+)\"", compose)
    assert encoded
    request_payload = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
    assert request_payload["runtime_archive_b64"] == "<embedded-in-head-agent-script>"
    assert request_payload["runtime_archive_transport"] == "embedded-script"
    assert "FULL_HUB_RUNTIME_ARCHIVE_B64_BUILTIN = 'runtime-archive-payload'" in compose


def test_head_agent_uses_embedded_runtime_archive_when_request_has_sentinel() -> None:
    script = control.head_server_command_script(full_hub_runtime_archive_b64="runtime-archive-payload")

    assert "FULL_HUB_RUNTIME_ARCHIVE_B64_BUILTIN = 'runtime-archive-payload'" in script
    assert 'runtime_archive_b64 == "<embedded-in-head-agent-script>"' in script
    assert 'runtime_archive_source = "embedded-script"' in script
    compile(script, "<head-agent-script>", "exec")


def test_full_hub_runtime_diag_summary_uses_runtime_repair_result() -> None:
    compact = control._compact_full_hub_runtime_diag_node_for_operator(
        {
            "service_name": "mainneta-super1",
            "ok": False,
            "diagnostic_ok": True,
            "full_runtime_ok": False,
            "repair_requested": True,
            "runtime_archive_present": True,
            "runtime_archive_source": "embedded-script",
            "runtime_repair": {"ok": True, "installed": True, "woken": True},
            "after_repair": {"local_full_main_computer_hub": False},
            "error": "full hub runtime is not active in the running container",
        }
    )

    assert compact["repair_attempted"] is True
    assert compact["repair_installed"] is True
    assert compact["repair_woken"] is True
    assert compact["runtime_archive_present"] is True
    assert compact["runtime_archive_source"] == "embedded-script"


def test_head_agent_full_hub_runtime_diag_marks_bootstrap_only_nodes_failed() -> None:
    script = control.head_server_command_script()

    assert '"full_runtime_ok"' in script
    assert "full hub runtime is not active in the running container" in script
    assert "runtime_repair_requested" in script
    assert "publish_to_coolify_metadata()" in script


def test_head_agent_can_reconstruct_chunked_full_hub_runtime_diag_env() -> None:
    script = control.head_server_command_script()

    assert 'read_b64_env("MC_ALLFATHER_FULL_HUB_RUNTIME_DIAG_REQUEST_B64")' in script
    assert 'name + "_CHUNKS"' in script
    assert 'chunked payload sha256 mismatch' in script


def test_full_hub_runtime_diag_request_includes_repair_archive_payload() -> None:
    head = control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/heads/coolify-a",
        peers=(),
    )

    request = control.full_hub_runtime_diag_request_payload(
        "mainnet",
        head,
        {
            "nodes": [
                {
                    "ok": False,
                    "service_name": "mainneta-super1",
                    "domain": "mainneta-hub1.greatlibrary.io",
                    "diagnostics": {"steps": [{"step": "manifest-build-done", "deployment_id": "dep-123"}]},
                }
            ]
        },
    )

    assert request["repair_missing_runtime"] is True
    assert request["runtime_archive_b64"]
    raw_zip = zlib.decompress(base64.b64decode(request["runtime_archive_b64"]))
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        assert "main_computer/hub.py" in archive.namelist()
    redacted = control.redact_full_hub_runtime_diag_request_for_output(request)
    assert redacted["runtime_archive_b64"].startswith("<redacted:")
    assert request["runtime_archive_b64"] not in json.dumps(redacted, sort_keys=True)


def test_head_agent_full_hub_runtime_diag_can_repair_missing_runtime() -> None:
    script = control.head_server_command_script()

    assert "def install_full_hub_runtime_archive" in script
    assert "/containers/{urllib.parse.quote(container_id, safe='')}/archive?path=/opt" in script
    assert "repair_missing_runtime" in script
    assert "runtime_repair" in script
    assert "/wake" in script


def test_hub_propagate_continues_after_full_runtime_container_repair() -> None:
    source = Path(control.__file__).read_text(encoding="utf-8")

    assert "full_hub_runtime_diag_recovered(runtime_diag)" in source
    assert "full hub runtime recovered by container repair" in source
    assert "recovered_by_container_runtime_repair" in source

def test_full_hub_runtime_diag_pending_marker_uses_current_request_id() -> None:
    pending = control.full_hub_runtime_diag_pending_result(
        {
            "service_name": "allfather-full-hub-runtime-diagnostics-mainnet-coolify-a",
            "network_key": "mainnet",
            "coolify_server": "coolify-a",
            "request_id": "fresh-req",
            "nodes": [{"service_name": "mainneta-super1"}, {"service_name": "mainneta-super2"}],
        }
    )

    assert pending["phase"] == "pending"
    assert pending["status"] == "pending"
    assert pending["request_id"] == "fresh-req"
    assert pending["node_count"] == 2
    assert pending["nodes"] == []


def test_remove_callback_marker_lines_removes_only_full_hub_marker() -> None:
    description = "\n".join(
        [
            "Main Computer all-father host agent.",
            "ALLFATHER_PROBE_RESULT_B64:abc",
            "ALLFATHER_FULL_HUB_RUNTIME_DIAG_RESULT_B64:old",
            "ALLFATHER_TRAEFIK_PROPAGATE_RESULT_B64:def",
        ]
    )

    cleaned = control.remove_callback_marker_lines(description, control.FULL_HUB_RUNTIME_DIAG_CALLBACK_MARKER)

    assert "ALLFATHER_FULL_HUB_RUNTIME_DIAG_RESULT_B64" not in cleaned
    assert "ALLFATHER_PROBE_RESULT_B64:abc" in cleaned
    assert "ALLFATHER_TRAEFIK_PROPAGATE_RESULT_B64:def" in cleaned


def test_head_agent_full_hub_runtime_diag_publishes_pending_marker_on_startup() -> None:
    script = control.head_server_command_script()

    assert "def initial_full_hub_runtime_diag_state" in script
    assert "full-hub runtime diagnostics request accepted by head-agent" in script
    assert "publish_to_coolify_metadata()" in script
    assert "threading.Thread(target=full_hub_runtime_diag_thread" in script


def test_controller_primes_full_hub_runtime_diag_marker_before_deploy() -> None:
    source = Path(control.__file__).read_text(encoding="utf-8")

    assert "prime_head_full_hub_runtime_diag_marker" in source
    assert "full-hub-sync: primed container runtime diagnostics marker" in source
    assert "stale_marker_request_ids" in source
    assert "marker_prime" in source


def test_head_agent_full_hub_runtime_diag_initial_state_is_defined_before_use() -> None:
    script = control.head_server_command_script()

    definition_at = script.index("def initial_full_hub_runtime_diag_state")
    assignment_at = script.index("LATEST_FULL_HUB_RUNTIME_DIAG = initial_full_hub_runtime_diag_state()")
    assert definition_at < assignment_at
    compile(script, "<head-agent-script>", "exec")


def test_head_agent_full_hub_runtime_diag_thread_publishes_running_and_failure_markers() -> None:
    script = control.head_server_command_script()

    assert "full_hub_runtime_diag_started_result" in script
    assert "full-hub runtime diagnostics worker started" in script
    assert "full_hub_runtime_diag_failed_result" in script
    assert "except BaseException as exc" in script


def test_hub_propagate_operator_defaults_match_current_mainnet_runbook() -> None:
    args = control.parse_args(["hub-propagate", "mainnet", "--allow-mainnet"])

    assert args.coolify_timeout_s == 120.0
    assert args.coolify_retries == 5
    assert args.coolify_retry_sleep_s == 5.0
    assert args.hub_propagate_wait_s == 300.0
    assert args.full_hub_runtime_wait_s == 300.0
    assert args.full_hub_runtime_status_interval_s == 20.0
    assert args.full_hub_runtime_stale_bootstrap_fail_s == 60.0
    assert args.hub_admin_contract_sync_wait_s == 300.0
    assert args.full_hub_runtime_diag_wait_s == 120.0
    assert args.full_hub_runtime_diag_pending_fail_s == 45.0


def test_wait_for_full_hub_runtime_diag_fails_fresh_pending_marker_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "ok": False,
        "phase": "pending",
        "status": "pending",
        "request_id": "fresh-request",
        "nodes": [],
        "updated_at": 123.0,
    }
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    detail = {"ok": True, "body": {"description": control.FULL_HUB_RUNTIME_DIAG_CALLBACK_MARKER + encoded}}

    monkeypatch.setattr(control, "fetch_service_detail", lambda client, service_uuid, tried: detail)

    observed = control.wait_for_head_full_hub_runtime_diag_ready(
        object(),
        "head-service-uuid",
        [],
        wait_s=1.0,
        poll_s=0.001,
        expected_request_id="fresh-request",
        pending_fail_s=0.001,
    )

    assert observed["observed"] is True
    assert observed["ready"] is False
    assert "stayed pending" in observed["reason"]
    assert observed["expected_request_id"] == "fresh-request"

def test_compact_hub_propagate_default_output_removes_raw_coolify_spam() -> None:
    payload = {
        "ok": False,
        "operation": "hub-propagate",
        "network": "mainnet",
        "route_authority": "live-coolify-super-node-inventory",
        "domain_suffix": "greatlibrary.io",
        "selected_hosts": ["coolify-a"],
        "inventory": {
            "checked_hosts": ["coolify-a"],
            "super_node_count": 1,
            "super_nodes": [
                {
                    "service_name": "mainneta-super1",
                    "coolify_server": "coolify-a",
                    "host_slot": "A",
                    "ordinal": 1,
                    "status": "running:healthy",
                    "very_large_raw_field": "drop-me",
                }
            ],
            "errors": [],
        },
        "huddle": {"admin_count": 1, "private_state_updates": {"written": True}},
        "hub_admin_contract_sync": {"enabled": True, "ok": None, "reason": "skipped"},
        "propagations": [
            {
                "host": "coolify-a",
                "host_slot": "A",
                "ok": False,
                "local_super_node_count": 1,
                "coolify_api": {
                    "attempt_count": 55,
                    "failed_count": 2,
                    "tried": [{"body": "huge"}],
                },
                "full_hub_runtime_sync": {
                    "enabled": True,
                    "ok": False,
                    "reason": "still bootstrap",
                    "nodes": [
                        {
                            "service_name": "mainneta-super1",
                            "domain": "mainneta-hub1.greatlibrary.io",
                            "ok": False,
                            "reason": "still bootstrap",
                            "health_before": {"ok": True, "payload": {"huge": "drop"}},
                            "coolify_service_after_sync": {
                                "compose": {
                                    "candidate_count": 3,
                                    "any_contains_full_runtime": True,
                                    "candidates": [{"huge": "drop-me"}],
                                },
                                "service": {"status": "running:healthy"},
                            },
                            "wait": {
                                "ready": False,
                                "attempts": 3,
                                "last_probe": {"ok": True, "payload": {"huge": "drop"}},
                                "coolify_snapshots": [
                                    {
                                        "compose": {
                                            "candidate_count": 3,
                                            "any_contains_full_runtime": True,
                                            "candidates": [{"huge": "drop-me"}],
                                        },
                                        "service": {"status": "running:healthy"},
                                    }
                                ],
                            },
                            "diagnostics": {"steps": [{"step": "wait-full-health-done", "reason": "still bootstrap"}]},
                        }
                    ],
                },
                "full_hub_runtime_container_diagnostics": {
                    "enabled": True,
                    "ok": False,
                    "ready": False,
                    "reason": "not recovered",
                    "request": {"request_id": "req-1", "runtime_archive_b64": "drop-me"},
                    "wait": {"observed": True, "ready": False, "expected_request_id": "req-1"},
                    "result": {
                        "phase": "ready",
                        "request_id": "req-1",
                        "nodes": [
                            {
                                "service_name": "mainneta-super1",
                                "ok": False,
                                "container_id": "abc123",
                                "runtime": {
                                    "paths": {
                                        "/opt/main-computer-src.zip": {"exists": False},
                                        "/opt/main-computer-src/main_computer/hub.py": {"exists": False},
                                    },
                                    "imports": {
                                        "main_computer.hub": {
                                            "ok": False,
                                            "has_serve_hub": False,
                                            "error": "ModuleNotFoundError",
                                        }
                                    },
                                    "local_health": {"json": {"bootstrap_hub": True, "full_main_computer_hub": False}},
                                },
                                "docker": {
                                    "state_status": "running",
                                    "health_status": "healthy",
                                    "env": {"MC_ALLFATHER_DEPLOYMENT_ID": "dep-1"},
                                },
                            }
                        ],
                    },
                },
            }
        ],
        "errors": [{"host": "coolify-a", "error": "still bootstrap"}],
    }

    compact = control.compact_hub_propagate_for_operator(payload)
    raw = json.dumps(compact, sort_keys=True)

    assert compact["summary"]["super_nodes"] == 1
    assert compact["summary"]["failed_hosts"] == 1
    assert compact["propagations"][0]["full_hub_runtime_sync"]["node_count"] == 1
    assert compact["propagations"][0]["full_hub_runtime_sync"]["nodes"][0]["coolify"]["compose_full"] is True
    assert compact["propagations"][0]["full_hub_runtime_container_diagnostics"]["nodes"][0]["source_zip_exists"] is False
    assert compact["propagations"][0]["full_hub_runtime_container_diagnostics"]["nodes"][0]["hub_import_ok"] is False
    assert "runtime_archive_b64" not in raw
    assert "candidates" not in raw
    assert "coolify_snapshots" not in raw
    assert "payload" not in raw
    assert "tried" not in raw


def test_hub_propagate_main_prints_operator_summary_by_default(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "ok": True,
        "operation": "hub-propagate",
        "network": "mainnet",
        "inventory": {"super_node_count": 0, "super_nodes": []},
        "huddle": {"admin_count": 0},
        "propagations": [],
        "errors": [],
    }

    monkeypatch.setattr(control, "parse_args", lambda argv=None: argparse.Namespace(command="hub-propagate", json=False, verbose=False, dry_run=False))
    monkeypatch.setattr(control, "build_plan_from_args", lambda args: object())
    monkeypatch.setattr(control, "hub_propagate", lambda plan, args: payload)

    assert control.main(["hub-propagate", "mainnet", "--allow-mainnet"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["summary"]["hosts"] == 0
    assert printed["full_json"].startswith("rerun with --json")


def test_hub_propagate_operator_log_is_quiet_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(command="hub-propagate", quiet=False, progress=False, verbose=False)
    control.operator_log(args, "full-hub-sync: waiting mainneta-super1 at mainneta-hub1.greatlibrary.io: attempt=1 observed=True ok=True bootstrap=True full=False")
    assert capsys.readouterr().err == ""

    control.operator_log(args, "full-hub-sync: container runtime diagnostics pending timeout: marker stayed pending")
    assert "pending timeout" in capsys.readouterr().err


def test_hub_propagate_operator_log_progress_restores_trace(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(command="hub-propagate", quiet=False, progress=True, verbose=False)
    control.operator_log(args, "full-hub-sync: waiting mainneta-super1 at mainneta-hub1.greatlibrary.io: attempt=1")
    assert "attempt=1" in capsys.readouterr().err


def test_hub_propagate_main_preserves_full_json_with_json_flag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "ok": True,
        "operation": "hub-propagate",
        "network": "mainnet",
        "raw_marker": "kept-with-json",
    }

    monkeypatch.setattr(control, "parse_args", lambda argv=None: argparse.Namespace(command="hub-propagate", json=True, verbose=False, dry_run=False))
    monkeypatch.setattr(control, "build_plan_from_args", lambda args: object())
    monkeypatch.setattr(control, "hub_propagate", lambda plan, args: payload)

    assert control.main(["hub-propagate", "mainnet", "--allow-mainnet", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["raw_marker"] == "kept-with-json"



def test_add_node_zero_live_topology_skips_cleanup_by_default_local_policy(tmp_path: Path) -> None:
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
            "0",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    assert payload["network_inventory"]["join_context"] == "first-network-node"
    assert payload["zero_topology_prefunk"]["enabled"] is False
    assert payload["zero_topology_prefunk"]["ok"] is True
    assert "--cleanup-zero-topology-runtime" in payload["zero_topology_prefunk"]["reason"]
    assert payload["zero_topology_prefunk"]["runtime_cleanup"]["enabled"] is False
    assert payload["zero_topology_prefunk"]["private_state_cleanup"]["enabled"] is False


def test_add_node_zero_live_topology_cleanup_requires_explicit_opt_in(tmp_path: Path) -> None:
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
            "0",
            "--cleanup-zero-topology-runtime",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.add_node(plan, args)

    assert payload["network_inventory"]["join_context"] == "first-network-node"
    assert payload["zero_topology_prefunk"]["enabled"] is True
    assert payload["zero_topology_prefunk"]["ok"] is True
    assert payload["zero_topology_prefunk"]["runtime_cleanup"]["ok"] is True
    assert payload["zero_topology_prefunk"]["private_state_cleanup"]["remaining_node_count"] == 0


def test_remove_node_last_network_node_plans_network_wide_cleanup(tmp_path: Path) -> None:
    path = write_private_state_with_wallets(tmp_path)
    args = control.parse_args(
        [
            "remove-node",
            "testnet",
            "--host",
            "coolify-a",
            "--node",
            "testneta-super1",
            "--private-state",
            str(path),
            "--dry-run",
            "--existing-count",
            "1",
        ]
    )
    plan = control.build_plan_from_args(args)

    payload = control.remove_node(plan, args)

    assert payload["removed_last_network_node"] is True
    assert payload["remaining_network_super_nodes"] == 0
    assert payload["zero_node_cleanup"]["enabled"] is True
    assert payload["zero_node_cleanup"]["ok"] is True
    assert payload["network_pristine_after_remove"] is True


def test_generated_super_runtime_contains_remove_handoff_guards() -> None:
    script = control.super_server_command_script()

    assert 'removal_handoff = manifest.get("removal_handoff")' in script
    assert "fdb_coordinator_handoff" in script
    assert "validator_removal_handoff" in script
    assert 'qbft_proposeValidatorVote", [normalized, bool(add)]' in script
    compile(script, "<allfather-super-remove-handoff>", "exec")


def test_generated_head_runtime_cleanup_is_request_scoped() -> None:
    script = control.head_server_command_script()

    assert 'request_id = str(CLEANUP_REQUEST.get("request_id") or "").strip()' in script
    assert '"request_id": request_id' in script
    compile(script, "<allfather-head-runtime-cleanup>", "exec")


def _image_prefunk_test_head() -> control.HeadNode:
    return control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="10.116.0.3",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/control-plane/coolify-a",
        peers=(),
    )


def _image_prefunk_test_plan(head: control.HeadNode) -> control.HeadPlan:
    return control.HeadPlan(
        kind="main_computer.allfather_head_plan.v1",
        private_state_path="runtime/state/all_father.private.yaml",
        heads=(head,),
        desired_counts={"allfather_heads": 1},
        guardrails={},
    )


def test_add_node_runtime_image_presence_request_is_verify_only() -> None:
    head = _image_prefunk_test_head()
    tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-a-test"

    request = control.add_node_runtime_image_presence_request(
        {
            "tag": tag,
            "image_tags": {
                "system-base": "main-computer/allfather-system-base:system-test",
                "foundation-base": "main-computer/allfather-foundation-base:foundation-test",
                "python-base": "main-computer/allfather-python-base:python-test",
            },
        },
        network_key="mainnet",
        head=head,
    )

    assert request["image_tag"] == tag
    assert request["verification"]["verify_existing_only"] is True
    assert len(request["image_build_steps"]) == 4
    assert all(step["dockerfile_b64"] == "" for step in request["image_build_steps"])
    runtime_step = request["image_build_steps"][-1]
    assert runtime_step["name"] == "fullhub-runtime"
    assert runtime_step["tag"] == tag
    assert "main_computer.hub" in runtime_step["verify_script"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("exited", True),
        ("starting:unhealthy", True),
        ("restarting", True),
        ("running:healthy", False),
        ("", False),
    ],
)
def test_add_node_service_status_requires_resume(status: str, expected: bool) -> None:
    assert control.add_node_service_status_requires_resume(status) is expected


def test_add_node_prefunk_rebuilds_when_handoff_image_is_missing_on_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = _image_prefunk_test_head()
    plan = _image_prefunk_test_plan(head)
    output_root = tmp_path / "stage12"
    latest = output_root / "mainnet" / "latest-stage-1-2.json"
    latest.parent.mkdir(parents=True)
    old_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-a-old"
    new_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-a-rebuilt"

    def write_latest(tag: str) -> None:
        latest.write_text(
            json.dumps(
                {
                    "ok": True,
                    "network": "mainnet",
                    "run_id": "test",
                    "desired": {"fullhub_image_build_plan": "four-checkpoint-images"},
                    "safety": {"bootstrap_image": False},
                    "next_stage_inputs": {
                        "verified_fullhub_image_by_host": {
                            "coolify-a": {
                                "tag": tag,
                                "id": "sha256:test",
                                "image_build_plan": "four-checkpoint-images",
                                "image_tags": {
                                    "system-base": "main-computer/allfather-system-base:system-test",
                                    "foundation-base": "main-computer/allfather-foundation-base:foundation-test",
                                    "python-base": "main-computer/allfather-python-base:python-test",
                                },
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    write_latest(old_tag)
    monkeypatch.setattr(
        control,
        "verify_add_node_runtime_image_on_host",
        lambda *args, **kwargs: {
            "enabled": True,
            "ready": False,
            "reason": "docker image inspect failed status=404",
        },
    )

    def fake_run_stage_1_2_cli_args(_args: argparse.Namespace) -> dict[str, object]:
        write_latest(new_tag)
        return {
            "ok": True,
            "output": {"latest_file": str(latest)},
            "verified_fullhub_image_count": 1,
        }

    monkeypatch.setattr(control, "run_stage_1_2_cli_args", fake_run_stage_1_2_cli_args)
    args = argparse.Namespace(
        network="mainnet",
        allow_mainnet=True,
        command="add-node",
        stage_1_2_output_root=str(output_root),
        force_image_prefunk=False,
        dry_run=False,
        host=["coolify-a"],
        build_wait_s=300.0,
        docker_build_timeout_s=300,
        poll_s=1.0,
        operator_log_interval_s=1.0,
    )

    result = control.ensure_add_node_stage_1_2_prefunk(plan, args, head)

    assert result["ready"] is True
    assert result["reused_latest"] is False
    assert result["ran_stage_1_2"] is True
    assert result["image"]["tag"] == new_tag
    assert result["prior_image_presence"]["ready"] is False


def test_checkpoint_plan_marks_source_independent_layers_for_durable_retention() -> None:
    plan = control.allfather_checkpoint_image_build_plan(
        runtime_image_tag="main-computer/allfather-fullhub-runtime:test-runtime",
        build_id="test",
    )
    steps = {step["name"]: step for step in plan["steps"]}

    for name in ("system-base", "foundation-base", "python-base"):
        assert steps[name]["durable_cache"] is True
        assert steps[name]["checkpoint_restored"] == f"{name}-cache-restored"
    assert steps["fullhub-runtime"]["durable_cache"] is False


def test_head_compose_mounts_persistent_checkpoint_image_cache(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    compose = control.render_head_compose(plan, plan.heads[0])

    cache_root = control.DEFAULT_ALLFATHER_IMAGE_CACHE_ROOT
    assert f'MC_ALLFATHER_IMAGE_CACHE_ROOT: "{cache_root}"' in compose
    assert f'"{cache_root}:{cache_root}"' in compose


def test_head_agent_checkpoint_cache_uses_docker_export_and_load_streams() -> None:
    script = control.head_server_command_script()

    compile(script, "<allfather-head-agent>", "exec")
    assert '"/images/" + urllib.parse.quote(image_tag, safe="") + "/get"' in script
    assert '"/images/load?quiet=1"' in script
    assert "CHECKPOINT_CACHE_MANIFEST_KIND" in script
    assert "archive_sha256" in script
    assert "os.replace(temporary, destination)" in script


def test_add_node_presence_request_verifies_and_seeds_entire_checkpoint_ladder(tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    image = {
        "tag": "main-computer/allfather-fullhub-runtime:runtime-test",
        "image_tags": {
            "system-base": "main-computer/allfather-system-base:system-test",
            "foundation-base": "main-computer/allfather-foundation-base:foundation-test",
            "python-base": "main-computer/allfather-python-base:python-test",
        },
    }

    request = control.add_node_runtime_image_presence_request(
        image,
        network_key="mainnet",
        head=head,
    )
    steps = {step["name"]: step for step in request["image_build_steps"]}

    assert list(step["name"] for step in request["image_build_steps"]) == [
        "system-base",
        "foundation-base",
        "python-base",
        "fullhub-runtime",
    ]
    for name in ("system-base", "foundation-base", "python-base"):
        assert steps[name]["dockerfile_b64"] == ""
        assert steps[name]["durable_cache"] is True
        assert steps[name]["checkpoint_restored"] == f"{name}-cache-restored"
    assert steps["fullhub-runtime"]["durable_cache"] is False
    assert request["verification"]["requires_durable_checkpoint_archives"] == [
        "system-base",
        "foundation-base",
        "python-base",
    ]


def test_add_node_handoff_rejects_missing_checkpoint_tags() -> None:
    stage = {
        "ok": True,
        "network": "mainnet",
        "desired": {"fullhub_image_build_plan": "four-checkpoint-images"},
        "safety": {"bootstrap_image": False},
        "next_stage_inputs": {
            "verified_fullhub_image_by_host": {
                "coolify-a": {
                    "tag": "main-computer/allfather-fullhub-runtime:runtime-test",
                    "image_tags": {},
                }
            }
        },
    }

    valid, image, reason = control.stage_1_2_verified_image_for_add_node(
        stage,
        network_key="mainnet",
        host="coolify-a",
    )

    assert valid is False
    assert image == {}
    assert "missing system-base checkpoint tag" in reason


def test_hub_verify_treats_disabled_traefik_route_check_as_neutral(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)
    head = plan.heads[0]
    node = {
        "service_name": "testneta-super1",
        "coolify_server": head.coolify_server,
        "guard_url": "http://10.116.0.3:41800",
        "fdb_endpoint": "10.116.0.3:44650",
    }
    inventory = {
        "nodes": [node],
        "local_nodes_by_host": {head.coolify_server: [node]},
    }
    args = argparse.Namespace(
        skip_hub_verify=False,
        skip_traefik_route_verify=True,
        dry_run=False,
        no_deploy=False,
        hub_verify_wait_s=30.0,
        hub_domain_suffix="example.invalid",
        verbose=False,
        json=False,
    )

    class FakeVersion:
        ok = True
        status = 200
        body = {}

    class FakeFdbTool:
        @staticmethod
        def client_for_server(server: str, args: argparse.Namespace) -> tuple[object, str]:
            return object(), "test-token"

    class FakeHubServiceTool:
        @staticmethod
        def trigger_deploy_service(
            client: object,
            *,
            service_uuid: str,
            force: bool,
            tried: list[dict[str, object]],
        ) -> None:
            return None

    monkeypatch.setattr(control, "fdb_tool", lambda: FakeFdbTool())
    monkeypatch.setattr(control, "hub_service_tool", lambda: FakeHubServiceTool())
    monkeypatch.setattr(control, "request_coolify_version", lambda *a, **k: FakeVersion())
    monkeypatch.setattr(control, "resolve_context", lambda *a, **k: {})
    monkeypatch.setattr(control, "sync_probe_service", lambda *a, **k: ("probe-uuid", "head-agent-updated", {}))
    monkeypatch.setattr(control, "probe_target_records_for_plan", lambda *a, **k: [])
    monkeypatch.setattr(control, "wait_for_probe_metadata_result", lambda *a, **k: ({}, {"ok": True}))
    monkeypatch.setattr(control, "probe_result_covers_expected_super_targets", lambda *a, **k: True)
    monkeypatch.setattr(control, "super_statuses_from_probe_result", lambda *a, **k: {"testneta-super1": {}})
    monkeypatch.setattr(
        control,
        "hub_propagate_hub_verify_check",
        lambda *a, **k: {"ready": True, "reason": "full hub verified", "components": {}},
    )

    result = control.verify_hubs_before_hub_propagate(
        plan,
        args,
        "testnet",
        [head],
        inventory,
        {head.coolify_server: []},
    )

    assert result["ok"] is True
    assert result["failed_fast"] is False
    assert result["traefik_route_verify_enabled"] is False
    assert result["verified_hub_count"] == 1
    assert "route verification disabled" in result["reason"]
    assert result["errors"] == []


def test_remove_node_reads_live_runtime_image_without_stage12_prefunk() -> None:
    service_name = "mainneta-super1"
    image_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-a-test"
    compose = f"""
services:
  {service_name}:
    image: {image_tag}
    pull_policy: never
"""
    detail = {
        "ok": True,
        "body": {
            "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        },
    }

    result = control.service_runtime_image_from_detail(
        detail,
        service_name=service_name,
    )

    assert result["ok"] is True
    assert result["image"] == image_tag
    assert result["reason"] == "exact image read from live Coolify service compose"



def test_remove_node_resolves_live_runtime_image_from_compose_variable() -> None:
    service_name = "mainnetc-super1"
    image_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-c-test"
    compose = f"""
services:
  {service_name}:
    image: ${{MC_RUNTIME_IMAGE}}
    environment:
      MC_RUNTIME_IMAGE: {image_tag}
"""

    detail = {
        "ok": True,
        "body": {
            "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        },
    }

    result = control.service_runtime_image_from_detail(
        detail,
        service_name=service_name,
    )

    assert result["ok"] is True
    assert result["image"] == image_tag
    assert result["reason"] == "exact image read from live Coolify service compose"


def test_remove_node_resolves_live_runtime_image_from_super_manifest_env() -> None:
    service_name = "mainnetc-super1"
    image_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-c-test"
    manifest = {
        "runtime_image": {
            "tag": image_tag,
            "verified": True,
            "service_build_disabled": True,
        }
    }
    manifest_b64 = base64.b64encode(json.dumps(manifest).encode("utf-8")).decode("ascii")
    compose = f"""
services:
  {service_name}:
    build:
      context: .
      dockerfile_inline: |
        FROM scratch
    environment:
      MC_ALLFATHER_VERIFIED_FULLHUB_RUNTIME_IMAGE: "1"
      MC_ALLFATHER_SUPER_MANIFEST_B64: {manifest_b64}
      MC_ALLFATHER_RUNTIME_IMAGE: {image_tag}
"""

    detail = {
        "ok": True,
        "body": {
            "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        },
    }

    result = control.service_runtime_image_from_detail(
        detail,
        service_name=service_name,
    )

    assert result["ok"] is True
    assert result["image"] == image_tag
    assert result["reason"] == "exact image read from live super-node runtime environment"


def test_remove_node_resolves_live_runtime_image_from_service_detail_environment() -> None:
    service_name = "mainnetc-super1"
    image_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-c-test"

    detail = {
        "ok": True,
        "body": {
            "name": service_name,
            "environment_variables": [
                {"key": "MC_ALLFATHER_VERIFIED_FULLHUB_RUNTIME_IMAGE", "value": "1"},
                {"key": "MC_ALLFATHER_RUNTIME_IMAGE", "value": image_tag},
            ],
        },
    }

    result = control.service_runtime_image_from_detail(
        detail,
        service_name=service_name,
    )

    assert result["ok"] is True
    assert result["image"] == image_tag
    assert result["reason"] == "exact image read from live Coolify service metadata"


def test_remove_node_resolves_live_runtime_image_from_service_detail_registry_fields() -> None:
    service_name = "mainnetc-super1"
    image_name = "main-computer/allfather-fullhub-runtime"
    image_tag = "mainnet-coolify-c-test"

    detail = {
        "ok": True,
        "body": {
            "name": service_name,
            "application": {
                "docker_registry_image_name": image_name,
                "docker_registry_image_tag": image_tag,
            },
        },
    }

    result = control.service_runtime_image_from_detail(
        detail,
        service_name=service_name,
    )

    assert result["ok"] is True
    assert result["image"] == f"{image_name}:{image_tag}"
    assert result["reason"] == "exact image read from live Coolify service metadata"


def test_remove_node_resolves_live_runtime_image_from_service_detail_manifest_env() -> None:
    service_name = "mainnetc-super1"
    image_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-c-test"
    manifest = {
        "runtime_image": {
            "tag": image_tag,
            "verified": True,
            "service_build_disabled": True,
        }
    }
    manifest_b64 = base64.b64encode(json.dumps(manifest).encode("utf-8")).decode("ascii")

    detail = {
        "ok": True,
        "body": {
            "name": service_name,
            "environment": {
                "MC_ALLFATHER_SUPER_MANIFEST_B64": manifest_b64,
            },
        },
    }

    result = control.service_runtime_image_from_detail(
        detail,
        service_name=service_name,
    )

    assert result["ok"] is True
    assert result["image"] == image_tag
    assert result["reason"] == "exact image read from live Coolify service metadata"



def test_remove_survivor_handoff_never_invokes_image_build_prefunk() -> None:
    source = Path(control.REPO_ROOT / "tools" / "allfather_control.py").read_text(encoding="utf-8")
    start = source.index("def prepare_remove_survivor_handoff(")
    end = source.index("\ndef remove_node(", start)
    function_source = source[start:end]

    assert "ensure_add_node_stage_1_2_prefunk" not in function_source
    assert "verify_add_node_runtime_image_on_host" not in function_source
    assert "stage_1_2_reconcile_head_and_build_image" not in function_source
    assert "live_super_runtime_image_for_remove" in function_source
    assert "attach_live_runtime_image_to_remove_manifest" in function_source


def test_remove_manifest_pins_live_image_and_disables_build_fallback() -> None:
    manifest: dict[str, object] = {
        "bootstrap": {},
        "safety": {},
    }
    image_tag = "main-computer/allfather-fullhub-runtime:mainnet-coolify-a-test"

    selected = control.attach_live_runtime_image_to_remove_manifest(
        manifest,
        image_tag=image_tag,
        service_name="mainneta-super1",
    )

    assert selected == image_tag
    assert manifest["runtime_image"]["tag"] == image_tag
    assert manifest["runtime_image"]["source"] == "live-predelete-verified-service"
    assert manifest["runtime_image"]["service_build_disabled"] is True
    assert manifest["bootstrap"]["bootstrap_image_fallback"] is False
    assert manifest["safety"]["remove_node_image_rebuild_disabled"] is True


def test_remove_handoff_patches_existing_compose_without_requiring_image_tag() -> None:
    service_name = "mainnetc-super1"
    original_compose = f"""
services:
  {service_name}:
    build:
      context: .
      dockerfile_inline: |
        FROM already-live-hidden-coolify-source
    environment:
      MC_ALLFATHER_RUNTIME_IMAGE: ${{COOLIFY_RUNTIME_IMAGE}}
      MC_ALLFATHER_VERIFIED_FULLHUB_RUNTIME_IMAGE: "1"
      MC_ALLFATHER_CELL_ID: {service_name}
"""
    manifest = {
        "cell_id": service_name,
        "network_key": "mainnet",
        "deployment_id": "handoff-test",
        "bootstrap": {
            "full_hub_runtime_requested": True,
            "contracts_requested": False,
            "hub_admin_create_requested": False,
        },
        "foundationdb": {
            "target_cluster_file_after_reconfigure": "main_computer_mainnet_allfather:test@10.116.0.2:44850",
            "coordinator_reconfigure_required": True,
        },
        "ports": {},
        "safety": {},
        "removal_handoff": {"target_service_name": "mainneta-super1", "live_qbft_completed": True},
    }

    result = control.render_remove_survivor_handoff_compose_preserving_live_image(
        {"ok": True, "body": {"docker_compose_raw": base64.b64encode(original_compose.encode()).decode()}},
        {},
        manifest,
        service_name=service_name,
        hub_admin_private_key="0x" + "1" * 64,
        deployer_private_key="",
        governance_office_private_keys={},
    )

    assert result["ok"] is True
    patched = result["compose"]
    assert "FROM already-live-hidden-coolify-source" in patched
    assert "MC_ALLFATHER_SUPER_MANIFEST_B64" in patched
    assert "MC_ALLFATHER_FDB_PLAN_B64" in patched
    assert "MC_ALLFATHER_RUNTIME_IMAGE" in patched
    assert "COOLIFY_RUNTIME_IMAGE" in patched
    assert result["image_changed"] is False

def test_generated_super_runtime_removal_handoff_votes_all_participants_and_requires_new_block() -> None:
    script = control.super_server_command_script()

    assert 'removal_handoff.get("participant_guard_urls")' in script
    assert 'f"{guard_url}/qbft/propose-validator"' in script
    assert '"add": False' in script
    assert 'status = "waiting-validator-set-change"' in script
    assert 'status = "ready" if ready else "waiting-post-removal-block"' in script
    assert 'ready = bool(observed_block > baseline_block)' in script
    compile(script, "<allfather-super-removal-convergence>", "exec")


def test_remove_handoff_converges_live_qbft_before_any_super_redeploy() -> None:
    source = Path(control.REPO_ROOT / "tools" / "allfather_control.py").read_text(encoding="utf-8")
    start = source.index("def prepare_remove_survivor_handoff(")
    end = source.index("\ndef remove_node(", start)
    function_source = source[start:end]

    live_vote = function_source.index("run_live_qbft_remove_handoff(")
    convergence_gate = function_source.index('if live_qbft.get("ok") is not True')
    survivor_loop = function_source.index("for index, node in enumerate(survivor_nodes):")
    redeploy = function_source.index("sync_super_node_service(", survivor_loop)

    assert live_vote < convergence_gate < survivor_loop < redeploy
    assert "for node in current_nodes:" not in function_source
    assert '"target_redeployed": False' in function_source
    assert '"participant_guard_urls": participant_guard_urls' in function_source
    assert '"baseline_block_number": baseline_block_number' in function_source


def test_head_agent_live_qbft_remove_uses_running_guard_rpc_and_callback() -> None:
    script = control.head_server_command_script()

    assert 'MC_ALLFATHER_QBFT_REMOVE_REQUEST_B64' in script
    assert 'ALLFATHER_QBFT_REMOVE_RESULT_B64:' in script
    assert 'f"{clean}/qbft/propose-validator"' in script
    assert '"add": False' in script
    assert '"handoff": "remove-node-live-rpc"' in script
    assert '"waiting-validator-set-change"' in script
    assert '"waiting-post-removal-block"' in script
    assert '"post_removal_block_advanced"' in script
    compile(script, "<allfather-head-live-qbft-remove>", "exec")


def test_remove_handoff_does_not_redeploy_target_service() -> None:
    source = Path(control.REPO_ROOT / "tools" / "allfather_control.py").read_text(encoding="utf-8")
    start = source.index("def prepare_remove_survivor_handoff(")
    end = source.index("\ndef remove_node(", start)
    function_source = source[start:end]

    assert "for index, node in enumerate(survivor_nodes):" in function_source
    assert "for node in current_nodes:" not in function_source
    assert '"target": False' in function_source
    assert '"target_redeployed": False' in function_source


def _removal_verify_node(
    service_name: str,
    validator_address: str,
    target_validator_address: str,
    *,
    pending: bool,
    block_number: int = 2926,
    include_removal_handoff: bool = True,
) -> dict[str, object]:
    validator_status = "waiting-validator-removal-handoff" if pending else "running"
    functions: dict[str, object] = {
        "foundationdb": {
            "running": True,
            "status": "running",
        },
        "validator_rpc": {
            "running": True,
            "status": validator_status,
            "json_rpc_ok": True,
            "rpc_http_ok": True,
            "block_number": block_number,
            "validator_address": validator_address,
        },
    }
    if include_removal_handoff:
        functions["validator_removal_handoff"] = {
            "desired": pending,
            "ready": not pending,
            "status": "waiting-validator-set-change" if pending else "not-requested",
            "target_validator_address": target_validator_address if pending else "",
        }
    return {
        "host": "coolify-a",
        "nodes": [
            {
                "service_name": service_name,
                "host": "coolify-a",
                "internal_status": {
                    "functions": functions,
                },
            }
        ],
    }


def test_resumable_remove_preverify_accepts_only_matching_live_handoff() -> None:
    target_address = "0x" + "22" * 20
    survivor_address = "0x" + "11" * 20
    preverify = {
        "ok": False,
        "reason": "validator_rpc not healthy for hub propagation: waiting-validator-removal-handoff",
        "failed_fast": True,
        "errors": [
            {
                "service_name": "mainneta-super1",
                "error": "waiting-validator-removal-handoff",
            }
        ],
        "nodes": [
            _removal_verify_node(
                "mainneta-super1",
                survivor_address,
                target_address,
                pending=True,
            )
        ],
    }
    target_verify = {
        "ok": True,
        "errors": [],
        "nodes": [
            _removal_verify_node(
                "mainneta-super2",
                target_address,
                target_address,
                pending=False,
            )
        ],
    }

    resumed = control.resumable_remove_preverify(
        preverify,
        target_verify,
        target_service_name="mainneta-super2",
    )

    assert resumed is not None
    assert resumed["ok"] is True
    assert resumed["resume_detected"] is True
    assert resumed["resume_pending_services"] == ["mainneta-super1"]


def test_resumable_remove_preverify_accepts_target_self_pending_handoff_without_second_handoff_component() -> None:
    target_address = "0x" + "22" * 20
    preverify = {
        "ok": False,
        "reason": "validator_rpc not healthy for hub propagation: waiting-validator-removal-handoff block_number=4169",
        "failed_fast": True,
        "errors": [
            {
                "service_name": "mainneta-super1",
                "error": "validator_rpc not healthy for hub propagation: waiting-validator-removal-handoff block_number=4169",
            }
        ],
        "nodes": [
            _removal_verify_node(
                "mainneta-super1",
                target_address,
                target_address,
                pending=True,
                block_number=4169,
            )
        ],
    }
    target_verify = {
        "ok": False,
        "reason": "validator_rpc not healthy for hub propagation: waiting-validator-removal-handoff block_number=4177",
        "failed_fast": True,
        "errors": [
            {
                "service_name": "mainneta-super1",
                "error": "validator_rpc not healthy for hub propagation: waiting-validator-removal-handoff block_number=4177",
            }
        ],
        "nodes": [
            _removal_verify_node(
                "mainneta-super1",
                target_address,
                target_address,
                pending=True,
                block_number=4177,
                include_removal_handoff=False,
            )
        ],
    }

    resumed = control.resumable_remove_preverify(
        preverify,
        target_verify,
        target_service_name="mainneta-super1",
    )

    assert resumed is not None
    assert resumed["ok"] is True
    assert resumed["resume_detected"] is True
    assert resumed["resume_pending_services"] == ["mainneta-super1"]


def test_resumable_remove_preverify_rejects_mismatched_target() -> None:
    actual_target = "0x" + "22" * 20
    stale_target = "0x" + "33" * 20
    survivor_address = "0x" + "11" * 20
    preverify = {
        "ok": False,
        "errors": [{"service_name": "mainneta-super1", "error": "waiting-validator-removal-handoff"}],
        "nodes": [
            _removal_verify_node(
                "mainneta-super1",
                survivor_address,
                stale_target,
                pending=True,
            )
        ],
    }
    target_verify = {
        "ok": True,
        "errors": [],
        "nodes": [
            _removal_verify_node(
                "mainneta-super2",
                actual_target,
                actual_target,
                pending=False,
            )
        ],
    }

    assert (
        control.resumable_remove_preverify(
            preverify,
            target_verify,
            target_service_name="mainneta-super2",
        )
        is None
    )

def test_prune_allfather_helper_services_uses_live_inventory_names(monkeypatch) -> None:
    head = control.HeadNode(
        head_id="allfather-head-coolify-a",
        service_name="allfather-head-coolify-a",
        coolify_server="coolify-a",
        slot="A",
        guard_container_port=41414,
        guard_host_port=41400,
        guard_publish_host="10.116.0.3",
        guard_url="http://10.116.0.3:41400",
        state_root="/data/main-computer/allfather/head/coolify-a",
        peers=(),
    )
    args = argparse.Namespace(dry_run=False, delete_wait_s=0.0, delete_poll_s=0.0, verbose=True)
    tried: list[dict[str, object]] = []
    deleted: list[tuple[str, str]] = []

    def fake_delete(client, *, service_uuid: str, service_name: str, tried: list[dict[str, object]]) -> dict[str, object]:
        deleted.append((service_name, service_uuid))
        return {"ok": True}

    def fake_wait(client, *, service_name: str, tried: list[dict[str, object]], wait_s: float, poll_s: float, args) -> dict[str, object]:
        return {"confirmed_absent": True, "wait_s": wait_s, "poll_s": poll_s}

    monkeypatch.setattr(control, "delete_coolify_service", fake_delete)
    monkeypatch.setattr(control, "wait_for_coolify_service_absent", fake_wait)

    result = control.prune_allfather_helper_services(
        object(),
        "mainnet",
        head,
        args,
        tried,
        services=[
            {
                "name": "allfather-super-runtime-cleanup-mainnet-coolify-a",
                "uuid": "cleanup-uuid",
                "status": "exited",
            }
        ],
        host_super_nodes_remaining=True,
    )

    assert result["ready"] is True
    assert deleted == [("allfather-super-runtime-cleanup-mainnet-coolify-a", "cleanup-uuid")]
    assert result["missing"] == []
    assert result["deleted"][0]["service_name"] == "allfather-super-runtime-cleanup-mainnet-coolify-a"



