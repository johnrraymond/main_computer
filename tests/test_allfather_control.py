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
    assert "10.116.0.3:41400:41414/tcp" in compose
    assert "mainneta-hub1" not in compose
    assert "mainneta-rpc1" not in compose
    assert "traefik.http.routers" not in compose

    encoded = compose.split("MC_ALLFATHER_MANIFEST_B64: ", 1)[1].splitlines()[0].strip().strip('"')
    decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert decoded["processes"] == []


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


def test_discover_groups_only_live_non_control_networks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_private_state(tmp_path)
    plan = control.build_head_plan(control.load_private_hosts(path), private_state_path=path)

    def fake_fetch_json(url: str, *, timeout_s: float) -> dict[str, object]:
        if url.endswith("/identity"):
            return {"ok": True, "network_key": "control-plane", "url": url}
        if url.endswith("/topology"):
            return {"ok": True, "network_key": "control-plane", "url": url}
        return {"ok": True, "network_key": "control-plane", "url": url}

    monkeypatch.setattr(control, "fetch_json", fake_fetch_json)
    args = type("Args", (), {"timeout_s": 1.0})()

    payload = control.discover_from_heads(plan, args)

    assert payload["ok"] is True
    assert payload["networks"] == {}
    assert len(payload["heads"]) == 2
