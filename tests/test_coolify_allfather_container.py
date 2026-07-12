from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "coolify_allfather_container.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("coolify_allfather_container", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_testnet_plan_compiles_guarded_function_cells_from_existing_topologies() -> None:
    module = _load_module()

    plan = module.build_allfather_plan("testnet")

    assert plan.network_key == "testnet"
    assert plan.placement_path == "deploy/hub-topology/testnet-coolify-deployment.json"
    assert [cell.cell_id for cell in plan.cells] == ["testnet-coolify-a", "testnet-coolify-b"]
    assert [cell.guard_host_port for cell in plan.cells] == [41410, 41411]
    assert all(cell.guard_container_port == 41414 for cell in plan.cells)
    assert {cell.guard_host_port for cell in plan.cells}.isdisjoint({40010, 40321, 47000})

    first, second = plan.cells
    assert [instance.id for instance in first.fdb_instances] == ["testnet-fdb1", "testnet-fdb2"]
    assert [hub.hub_id for hub in first.hubs] == ["testnet-hub1", "testnet-hub2"]
    assert [service.id for service in first.qbft_services] == ["validator-1"]

    assert [instance.id for instance in second.fdb_instances] == ["testnet-fdb3"]
    assert [hub.hub_id for hub in second.hubs] == ["testnet-hub3"]
    assert [service.id for service in second.qbft_services] == []


def test_manifest_identity_uses_network_and_function_role_not_host_role() -> None:
    module = _load_module()

    cell = module.build_allfather_plan("testnet").cells[0]
    manifest = cell.to_manifest()

    assert manifest["network_key"] == "testnet"
    assert manifest["set_id"] == "testnet"
    assert manifest["cell_id"] == "testnet-coolify-a"
    assert manifest["identity"]["role"] == "function"
    assert manifest["identity"]["set_id"] == "testnet"
    assert manifest["desired_counts"] == {"foundationdb": 2, "hub": 2, "qbft": 1, "processes": 6}
    assert manifest["set_desired_counts"]["allfather_cells"] == 2
    assert manifest["topology"]["peer_hosts"][0]["cell_id"] == "testnet-coolify-b"
    assert "role" not in manifest["foundationdb"]["instances"][0]
    assert "process-guard" in manifest["identity"]["capabilities"]
    assert "foundationdb" in manifest["identity"]["capabilities"]
    assert "hub" in manifest["identity"]["capabilities"]
    assert "qbft" in manifest["identity"]["capabilities"]
    assert manifest["guard"]["container_port"] == 41414
    assert manifest["guard"]["host_port"] == 41410
    assert manifest["guard"]["tick_s"] == 10.0
    assert manifest["guard"]["restart_budget_per_tick"] == 1

    ports = {(port["group"], port["name"]): port for port in manifest["port_inventory"]}
    assert ports[("process-guard", "allfather-guard")]["container_port"] == 41414
    assert ports[("foundationdb", "testnet-fdb1")]["host_port"] == 4550
    assert ports[("foundationdb", "testnet-fdb2")]["host_port"] == 4551
    assert ports[("hub", "testnet-hub1")]["container_port"] == 8785
    assert ports[("hub", "testnet-hub2")]["container_port"] == 8786
    assert ports[("qbft", "validator-1-rpc")]["container_port"] == 8545
    assert manifest["identity"]["ports"] == manifest["port_inventory"]


def test_split_qbft_seed_maps_services_by_host_suffix_without_specialized_roles() -> None:
    module = _load_module()

    plan = module.build_allfather_plan("testnet", qbft_seed="testnet-split-example")
    by_cell = {cell.cell_id: [service.id for service in cell.qbft_services] for cell in plan.cells}

    assert by_cell["testnet-coolify-a"] == ["validator-1", "validator-2", "rpc-1"]
    assert by_cell["testnet-coolify-b"] == ["validator-3", "validator-4"]


def test_compose_renders_one_guarded_service_without_custom_networks() -> None:
    module = _load_module()

    cell = module.build_allfather_plan("testnet").cells[0]
    compose = module.render_compose_for_cell(cell)

    assert "services:" in compose
    assert "main-computer-allfather-testnet-coolify-a:" in compose
    assert "networks:" not in compose
    assert '"10.116.0.3:41410:41414/tcp"' in compose
    assert '"10.116.0.3:4550:4550/tcp"' in compose
    assert '"10.116.0.3:4551:4551/tcp"' in compose
    assert '"10.116.0.3:8785:8785/tcp"' in compose
    assert '"10.116.0.3:8786:8786/tcp"' in compose
    assert '"127.0.0.1:30010:8545/tcp"' in compose
    assert "MC_ALLFATHER_SET_ID:" in compose
    assert "MC_ALLFATHER_DESIRED_COUNTS:" in compose
    assert "MC_ALLFATHER_SET_DESIRED_COUNTS:" in compose
    assert "MC_ALLFATHER_PEER_GUARDS:" in compose
    assert "MC_ALLFATHER_GUARD_PORTS:" in compose
    assert "MC_ALLFATHER_FDB_PORTS:" in compose
    assert "MC_ALLFATHER_HUB_PORTS:" in compose
    assert "MC_ALLFATHER_QBFT_PORTS:" in compose
    assert "testnet-hub1=10.116.0.3:8785->8785/tcp" in compose
    assert "MC_ALLFATHER_PORT_INVENTORY_B64:" in compose
    assert "MC_ALLFATHER_MANIFEST_B64:" in compose

    encoded = compose.split("MC_ALLFATHER_MANIFEST_B64: ", 1)[1].splitlines()[0]
    manifest = json.loads(base64.b64decode(json.loads(encoded)).decode("utf-8"))
    assert manifest["identity"]["role"] == "function"
    assert manifest["guard"]["container_port"] == 41414
    assert {port["group"] for port in manifest["port_inventory"]} >= {"process-guard", "foundationdb", "hub", "qbft"}


def test_multiple_sets_on_same_hosts_get_distinct_identity_peers_and_ports() -> None:
    module = _load_module()

    testnet = module.build_allfather_plan("testnet", set_id="testnet-1")
    mainnet_1 = module.build_allfather_plan("mainnet", set_id="mainnet-1", allow_mainnet=True)
    mainnet_2 = module.build_allfather_plan("mainnet", set_id="mainnet-2", allow_mainnet=True)

    assert [cell.coolify_server for cell in testnet.cells] == ["coolify-a", "coolify-b"]
    assert [cell.coolify_server for cell in mainnet_1.cells] == ["coolify-a", "coolify-b"]
    assert [cell.coolify_server for cell in mainnet_2.cells] == ["coolify-a", "coolify-b"]

    assert [cell.guard_host_port for cell in testnet.cells] == [41410, 41411]
    assert [cell.guard_host_port for cell in mainnet_1.cells] == [41420, 41421]
    assert [cell.guard_host_port for cell in mainnet_2.cells] == [41440, 41441]

    assert {cell.host_port_offset for cell in testnet.cells} == {0}
    assert {cell.host_port_offset for cell in mainnet_1.cells} == {1000}
    assert {cell.host_port_offset for cell in mainnet_2.cells} == {1100}

    first_mainnet = mainnet_1.cells[0].to_manifest()
    second_mainnet = mainnet_2.cells[0].to_manifest()

    assert first_mainnet["network_key"] == "mainnet"
    assert first_mainnet["set_id"] == "mainnet-1"
    assert first_mainnet["foundationdb"]["instances"][0]["source_port"] == 4550
    assert first_mainnet["foundationdb"]["instances"][0]["port"] == 5550
    assert "10.116.0.3:5550" in first_mainnet["foundationdb"]["cluster_contents"]
    assert "http://10.124.0.3:41421" in first_mainnet["topology"]["all_guard_urls"]

    assert second_mainnet["set_id"] == "mainnet-2"
    assert second_mainnet["foundationdb"]["instances"][0]["port"] == 5650
    assert "mainnet-2" in second_mainnet["state_root"]
    assert second_mainnet["foundationdb"]["namespace"] == "main-computer-mainnet-2-exp-fdb-stable-live-sessions"
    assert "http://10.124.0.3:41441" in second_mainnet["topology"]["all_guard_urls"]

    all_guard_ports = {
        cell.guard_host_port
        for plan in (testnet, mainnet_1, mainnet_2)
        for cell in plan.cells
    }
    assert all_guard_ports == {41410, 41411, 41420, 41421, 41440, 41441}


def test_write_plan_outputs_manifests_and_compose_files() -> None:
    module = _load_module()

    plan = module.build_allfather_plan("testnet")
    with tempfile.TemporaryDirectory() as tmp:
        written = module.write_plan(plan, Path(tmp))

        names = sorted(path.name for path in written)
        assert "README.md" in names
        assert "allfather-plan.json" in names
        assert "testnet-coolify-a.compose.yml" in names
        assert "testnet-coolify-a.manifest.json" in names
        assert "testnet-coolify-b.compose.yml" in names
        assert "testnet-coolify-b.manifest.json" in names

        manifest = json.loads((Path(tmp) / "testnet-coolify-a.manifest.json").read_text(encoding="utf-8"))
        assert manifest["network_key"] == "testnet"
        assert manifest["identity"]["role"] == "function"
        assert any(port["group"] == "hub" and port["host_port"] == 8785 for port in manifest["port_inventory"])
        assert "MC_ALLFATHER_HUB_PORTS" in (Path(tmp) / "testnet-coolify-a.compose.yml").read_text(encoding="utf-8")
        assert "Compiled port inventory" in (Path(tmp) / "README.md").read_text(encoding="utf-8")


def test_coolify_remote_plan_uses_same_network_set_id_and_payload_shape() -> None:
    module = _load_module()

    args = module.parse_args(
        [
            "plan",
            "testnet",
            "--set-id",
            "testnet-1",
            "--coolify",
            "--set-coolify-url",
            "coolify-a:https://coolify-a.example.invalid",
            "--set-coolify-url",
            "coolify-b:https://coolify-b.example.invalid",
            "--coolify-project-name",
            "main-computer",
            "--coolify-server-name",
            "local-docker",
        ]
    )
    plan = module._plan_from_args(args)
    remote = module.coolify_plan_result(plan, args)

    assert remote["ok"] is True
    assert remote["mode"] == "coolify"
    assert remote["network_key"] == "testnet"
    assert remote["set_id"] == "testnet-1"
    assert remote["environment_name"] == "testnet-1-allfather"

    first = remote["cells"][0]
    assert first["server"] == "coolify-a"
    assert first["service_name"] == "main-computer-allfather-testnet-1-coolify-a"
    assert first["coolify_url"] == "https://coolify-a.example.invalid"
    assert first["service_payload"]["docker_compose_raw"] == "<base64>"
    assert first["service_payload"]["docker_compose_raw_bytes"] > 100
    assert first["service_payload"]["environment_name"] == "testnet-1-allfather"
    assert first["desired_counts"]["foundationdb"] == 2
    assert any(port["group"] == "hub" for port in first["port_inventory"])


def test_apply_dry_run_renders_unified_remote_coolify_plan_without_tokens_or_api_calls() -> None:
    module = _load_module()

    args = module.parse_args(
        [
            "apply",
            "mainnet",
            "--set-id",
            "mainnet-2",
            "--allow-mainnet",
            "--dry-run",
            "--set-coolify-url",
            "coolify-a:https://coolify-a.example.invalid",
            "--set-coolify-url",
            "coolify-b:https://coolify-b.example.invalid",
            "--coolify-project-name",
            "main-computer",
            "--coolify-server-name",
            "local-docker",
        ]
    )
    plan = module._plan_from_args(args)
    result = module.coolify_apply_result(plan, args)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["plan"]["set_id"] == "mainnet-2"
    assert result["plan"]["environment_name"] == "mainnet-2-allfather"
    assert result["plan"]["cells"][0]["service_name"] == "main-computer-allfather-mainnet-2-coolify-a"
    assert result["plan"]["cells"][0]["service_payload"]["docker_compose_raw"] == "<base64>"

