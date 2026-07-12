from __future__ import annotations

import argparse
import base64
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "coolify_hub_cluster.py"

spec = importlib.util.spec_from_file_location("coolify_hub_cluster", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
coolify_hub_cluster = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coolify_hub_cluster
spec.loader.exec_module(coolify_hub_cluster)


def _args(**overrides):
    defaults = {
        "placement": REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json",
        "network_config": None,
        "packet": None,
        "network": "",
        "private_state": None,
        "set_coolify_url": [
            "coolify-a:http://ipaddress1:8000",
            "coolify-b:http://ipaddress2:8000",
        ],
        "coolify_token": "",
        "coolify_token_env": "MAIN_COMPUTER_TEST_MISSING_COOLIFY_TOKEN",
        "coolify_token_file": "",
        "set_coolify_token": [],
        "set_coolify_token_env": [],
        "set_coolify_token_file": [],
        "coolify_project_uuid": "",
        "coolify_project_name": "Main Computer Testnet",
        "set_coolify_project_uuid": [],
        "coolify_environment_name": "testnet-hubs",
        "coolify_environment_uuid": "",
        "set_coolify_environment_uuid": [],
        "no_create_environment": False,
        "coolify_server_name": "",
        "coolify_server_uuid": "",
        "set_coolify_server_name": [],
        "set_coolify_server_uuid": [],
        "coolify_destination_uuid": "",
        "set_coolify_destination_uuid": [],
        "git_repo": "https://github.com/example/main-computer",
        "git_branch": "main",
        "git_commit_sha": "",
        "base_directory": "/",
        "dockerfile_location": "",
        "health_path": "/api/hub/v1/health",
        "hub_chain_rpc_url": "",
        "bridge_backend": "",
        "dev_chain_deployment_path": "",
        "contracts_path": "",
        "allow_missing_bridge_signer": False,
        "enable_smoke_bridge": False,
        "enable_bridge_writes": False,
        "no_bridge_writes": False,
        "sync_bridge_signer": False,
        "bridge_signer_source_manifest": "",
        "bridge_controller_wallet_path": "",
        "bridge_signer_env_key": "",
        "bridge_signer_remote_path": "",
        "coolify_timeout_s": 1.0,
        "coolify_retries": 0,
        "coolify_retry_sleep_s": 0.0,
        "no_deploy": False,
        "no_traefik_sidecar": False,
        "force_deploy": False,
        "dry_run": False,
        "json": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class RouteCoolifyClient:
    def __init__(self, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.requests = []

    def request(self, method: str, path: str, payload=None):
        method = method.upper()
        self.requests.append((method, path, payload))
        key = (method, path)
        responses = self.routes.get(key)
        if not responses:
            return coolify_hub_cluster.CoolifyResponse(
                ok=False,
                status=404,
                method=method,
                path=path,
                body={"message": f"no fake route for {method} {path}"},
            )
        response = responses.pop(0)
        status = int(response.pop("_status", 200)) if isinstance(response, dict) else 200
        return coolify_hub_cluster.CoolifyResponse(
            ok=200 <= status < 300,
            status=status,
            method=method,
            path=path,
            body=response,
        )


class CoolifyHubClusterTests(unittest.TestCase):
    def test_placement_loads_three_hubs_across_two_symbolic_servers(self) -> None:
        placement = coolify_hub_cluster.load_hub_cluster_placement(_args().placement)

        self.assertEqual(placement.network_key, "testnet")
        self.assertEqual(sorted(placement.servers), ["coolify-a", "coolify-b"])
        self.assertEqual([hub.hub_id for hub in placement.hubs], ["testnet-hub1", "testnet-hub2", "testnet-hub3"])
        self.assertEqual(placement.cluster_file_path, "/data/main-computer/hub/testnet-exp-fdb/fdb.cluster")
        self.assertEqual(placement.namespace, "main-computer-testnet-exp-fdb-stable-live-sessions")
        self.assertEqual(placement.topology_container_path, "/app/deploy/hub-topology/testnet-topology.json")

    def test_plan_resolves_coolify_bindings_from_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "main_computer.private.yaml"
            state_path.write_text(
                """
coolify:
  hosts:
    A:
      name: coolify-a
      url: http://198.51.100.10:8000
      api_token: token-a
    B:
      name: coolify-b
      coolify_url: http://198.51.100.11:8000
      api_token: token-b
""".lstrip(),
                encoding="utf-8",
            )
            args = _args(set_coolify_url=[], private_state=state_path)
            placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
            profile = coolify_hub_cluster.load_network_profile(placement, args)
            plan = coolify_hub_cluster.plan_result(placement, profile, args)

        self.assertEqual([server["coolify_url"] for server in plan["servers"]], [
            "http://198.51.100.10:8000",
            "http://198.51.100.11:8000",
        ])
        self.assertEqual(plan["servers"][0]["coolify_url_source"], "private-state:coolify.hosts.A.url")

    def test_mainnet_placement_loads_three_mainnet_hubs_across_a_b(self) -> None:
        placement = coolify_hub_cluster.load_hub_cluster_placement(
            REPO_ROOT / "deploy" / "hub-topology" / "mainnet-coolify-deployment.json"
        )

        self.assertEqual(placement.network_key, "mainnet")
        self.assertEqual(sorted(placement.servers), ["coolify-a", "coolify-b"])
        self.assertEqual([hub.hub_id for hub in placement.hubs], ["mainnet-hub1", "mainnet-hub2", "mainnet-hub3"])
        self.assertEqual(placement.cluster_file_path, "/data/main-computer/hub/mainnet-exp-fdb/fdb.cluster")
        self.assertEqual(placement.namespace, "main-computer-mainnet-exp-fdb-stable-live-sessions")
        self.assertEqual(placement.topology_container_path, "/app/deploy/hub-topology/mainnet-topology.json")
        self.assertEqual(placement.public_entry_urls, ("https://mainnet-hub.greatlibrary.io",))

    def test_mainnet_placement_plan_renders_mainnet_hub_services(self) -> None:
        args = _args(
            placement=REPO_ROOT / "deploy" / "hub-topology" / "mainnet-coolify-deployment.json",
            set_coolify_url=["coolify-a:http://mainnet-a-coolify:8000", "coolify-b:http://mainnet-b-coolify:8000"],
            coolify_project_name="Main Computer",
            coolify_environment_name="mainnet-hubs",
        )
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        plan = coolify_hub_cluster.plan_result(placement, profile, args)
        compose = coolify_hub_cluster.render_server_hub_compose(placement, profile, args, "coolify-a")

        self.assertEqual(plan["network_key"], "mainnet")
        self.assertEqual(plan["servers"][0]["service_name"], "main-computer-mainnet-hubs-coolify-a")
        self.assertEqual(plan["servers"][1]["service_name"], "main-computer-mainnet-hubs-coolify-b")
        self.assertEqual(
            plan["servers"][0]["coolify_service_domains"],
            {
                "mainnet-hub1": {"domain": "https://mainnet-hub1.greatlibrary.io:8790"},
                "mainnet-hub2": {"domain": "https://mainnet-hub2.greatlibrary.io:8790"},
            },
        )
        self.assertEqual(
            plan["servers"][1]["coolify_service_domains"],
            {"mainnet-hub3": {"domain": "https://mainnet-hub3.greatlibrary.io:8790"}},
        )
        self.assertIn("mainnet-hub1:", compose)
        self.assertIn("mainnet-hub2:", compose)
        self.assertIn("https://mainnet-hub1.greatlibrary.io", compose)
        self.assertIn("--chain-id", compose)
        self.assertIn("42424240", compose)
        self.assertIn("/data/main-computer/hub/mainnet-exp-fdb/fdb.cluster", compose)
        self.assertIn("main-computer-mainnet-exp-fdb-stable-live-sessions", compose)

    def test_plan_renders_one_hub_compose_service_per_symbolic_host(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        plan = coolify_hub_cluster.plan_result(placement, profile, args)

        self.assertEqual([server["server"] for server in plan["servers"]], ["coolify-a", "coolify-b"])
        self.assertEqual(plan["servers"][0]["service_name"], "main-computer-testnet-hubs-coolify-a")
        self.assertEqual(plan["servers"][1]["service_name"], "main-computer-testnet-hubs-coolify-b")
        self.assertEqual([hub["hub_id"] for hub in plan["servers"][0]["hubs"]], ["testnet-hub1", "testnet-hub2"])
        self.assertEqual([hub["hub_id"] for hub in plan["servers"][1]["hubs"]], ["testnet-hub3"])

    def test_coolify_a_compose_uses_shared_fdb_cluster_file_and_concrete_hub_ids(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        compose = coolify_hub_cluster.render_server_hub_compose(placement, profile, args, "coolify-a")

        self.assertIn("testnet-hub1:", compose)
        self.assertIn("testnet-hub2:", compose)
        self.assertNotIn("  testnet-hub3:", compose)
        self.assertIn("--topology", compose)
        self.assertIn("/app/deploy/hub-topology/testnet-topology.json", compose)
        self.assertIn("--hub-id", compose)
        self.assertIn("testnet-hub1", compose)
        self.assertIn("testnet-hub2", compose)
        self.assertIn("--cluster-file", compose)
        self.assertIn("/data/main-computer/hub/testnet-exp-fdb/fdb.cluster", compose)
        self.assertIn("--namespace", compose)
        self.assertIn("main-computer-testnet-exp-fdb-stable-live-sessions", compose)
        self.assertIn("testnet-hub1.greatlibrary.io", compose)
        self.assertIn("testnet-hub2.greatlibrary.io", compose)
        self.assertNotIn("foundationdb/foundationdb", compose)
        self.assertNotIn("testnet-fdb", compose)

    def test_service_payload_contains_base64_compose(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        payload = coolify_hub_cluster.service_payload(
            placement,
            profile,
            args,
            server_name="coolify-b",
            context={
                "server_uuid": "server-b",
                "project_uuid": "project-b",
                "environment_name": "testnet-hubs",
                "environment_uuid": "env-b",
            },
        )

        self.assertEqual(payload["name"], "main-computer-testnet-hubs-coolify-b")
        self.assertEqual(payload["server_uuid"], "server-b")
        self.assertEqual(payload["project_uuid"], "project-b")
        self.assertEqual(payload["environment_uuid"], "env-b")
        self.assertEqual(
            payload["docker_compose_domains"],
            {"testnet-hub3": {"domain": "https://testnet-hub3.greatlibrary.io:8785"}},
        )
        compose = base64.b64decode(payload["docker_compose_raw"]).decode("utf-8")
        self.assertIn("testnet-hub3:", compose)
        self.assertIn("testnet-hub3.greatlibrary.io", compose)
        self.assertNotIn("testnet-hub1:", compose)

    def test_render_traefik_dynamic_config_uses_public_entry_urls_and_local_hubs(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        config = coolify_hub_cluster.render_server_traefik_dynamic_config(placement, profile, args, "coolify-a")

        self.assertIn("Generated by tools/coolify_hub_cluster.py Traefik sidecar", config)
        self.assertIn("Host(`testnet-hub.greatlibrary.io`)", config)
        self.assertIn("certResolver: letsencrypt", config)
        self.assertNotIn("healthCheck:", config)
        self.assertNotIn("path: \"/api/hub/status\"", config)
        self.assertIn('url: "http://testnet-hub1:8785"', config)
        self.assertIn('url: "http://testnet-hub2:8785"', config)
        self.assertNotIn('url: "http://testnet-hub3:8785"', config)

    def test_default_traefik_sidecar_adds_writer_service_to_compose(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        compose = coolify_hub_cluster.render_server_hub_compose(placement, profile, args, "coolify-b")

        self.assertIn("testnet-hub3:", compose)
        self.assertIn("testnet-hub-public-entry-config-coolify-b:", compose)
        self.assertIn("alpine:3.20", compose)
        self.assertIn("/data/coolify/proxy/dynamic:/data/coolify/proxy/dynamic", compose)
        self.assertIn("main-computer-testnet-hub-public-entry-coolify-b.yml", compose)
        self.assertIn("traefik.enable=false", compose)
        self.assertIn("REFRESH_SECONDS=300", compose)
        self.assertIn("$$CONFIG_DIR", compose)
        self.assertIn("$${CONFIG_PATH}.tmp", compose)
        self.assertIn("$$tmp", compose)
        self.assertIn("$$REFRESH_SECONDS", compose)
        self.assertNotIn('mkdir -p "$CONFIG_DIR"', compose)
        self.assertIn("Host(`testnet-hub.greatlibrary.io`)", compose)
        self.assertIn("http://testnet-hub3:8785", compose)
        self.assertNotIn("healthCheck:", compose)
        self.assertIn("healthcheck:", compose)
        self.assertIn("test -s /data/coolify/proxy/dynamic/main-computer-testnet-hub-public-entry-coolify-b.yml", compose)
        self.assertIn("grep -Fq -- testnet-hub.greatlibrary.io", compose)
        self.assertIn("start_period: 10s", compose)
        self.assertNotIn("http://testnet-hub1:8785", compose)

    def test_public_entry_service_payload_renders_standalone_config_stack(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)
        context = {
            "server_uuid": "server-b",
            "project_uuid": "project-b",
            "environment_name": "testnet-hubs",
            "environment_uuid": "env-b",
        }

        payload = coolify_hub_cluster.public_entry_service_payload(
            placement,
            profile,
            args,
            server_name="coolify-b",
            context=context,
        )
        compose = base64.b64decode(payload["docker_compose_raw"]).decode("utf-8")

        self.assertEqual(payload["name"], "main-computer-testnet-hub-public-entry-config-coolify-b")
        self.assertIn("testnet-hub-public-entry-config-coolify-b:", compose)
        self.assertIn("alpine:3.20", compose)
        self.assertIn("main-computer-testnet-hub-public-entry-coolify-b.yml", compose)
        self.assertIn("Host(`testnet-hub.greatlibrary.io`)", compose)
        self.assertIn("Application-mode Traefik public-entry sidecar", compose)
        self.assertIn("passHostHeader: false", compose)
        self.assertIn("https://testnet-hub3.greatlibrary.io", compose)
        self.assertNotIn("http://testnet-hub3:8785", compose)
        self.assertNotIn("  testnet-hub3:", compose)

    def test_sync_public_entry_service_for_server_creates_and_deploys_service(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services"): [{"data": []}],
                ("POST", "/api/v1/services"): [{"uuid": "public-entry-service-uuid"}],
                ("GET", "/api/v1/deploy?uuid=public-entry-service-uuid&force=false"): [{"deployment_uuid": "deploy-public-entry"}],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_cluster.sync_public_entry_service_for_server(
            client,
            placement,
            profile,
            args,
            server_name="coolify-b",
            context={
                "server_uuid": "server-b",
                "project_uuid": "project-b",
                "environment_name": "testnet-hubs",
                "environment_uuid": "env-b",
            },
            tried=tried,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["service_action"], "created")
        self.assertEqual(result["service_name"], "main-computer-testnet-hub-public-entry-config-coolify-b")
        self.assertTrue(result["deployed"])
        create_payload = next(request[2] for request in client.requests if request[0] == "POST" and request[1] == "/api/v1/services")
        self.assertEqual(create_payload["name"], "main-computer-testnet-hub-public-entry-config-coolify-b")
        compose = base64.b64decode(create_payload["docker_compose_raw"]).decode("utf-8")
        self.assertIn("testnet-hub-public-entry-config-coolify-b:", compose)
        self.assertIn("Host(`testnet-hub.greatlibrary.io`)", compose)

    def test_no_traefik_sidecar_disables_public_entry_manager(self) -> None:
        args = _args(no_traefik_sidecar=True)
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        compose = coolify_hub_cluster.render_server_hub_compose(placement, profile, args, "coolify-b")
        plan = coolify_hub_cluster.plan_result(placement, profile, args)

        self.assertIn("testnet-hub3:", compose)
        self.assertNotIn("testnet-hub-public-entry-config-coolify-b:", compose)
        self.assertFalse(plan["servers"][1]["traefik_dynamic_config"]["installed"])

    def test_default_traefik_sidecar_removes_stale_file_on_unselected_host(self) -> None:
        packet = coolify_hub_cluster.packet_tool.build_packet(
            network="testnet",
            placement_path=REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json",
            topology_path=None,
            selected_hubs=["testnet-hub1", "testnet-hub2"],
            selected_fdb=["testnet-fdb1"],
            generation="testnet-public-entry-cleanup",
        )
        packet_path = REPO_ROOT / "runtime" / "testnet-public-entry-cleanup-packet.json"
        try:
            packet_path.write_text(coolify_hub_cluster.packet_tool.canonical_packet_json(packet), encoding="utf-8")
            args = _args()
            placement = coolify_hub_cluster.load_hub_cluster_placement_from_packet(packet_path)
            profile = coolify_hub_cluster.load_network_profile(placement, args)

            compose_b = coolify_hub_cluster.render_server_hub_compose(placement, profile, args, "coolify-b")

            self.assertIn("testnet-hubs-disabled:", compose_b)
            self.assertIn("testnet-hub-public-entry-config-coolify-b:", compose_b)
            self.assertIn("Removed stale Traefik dynamic config", compose_b)
            self.assertIn("$$CONFIG_PATH", compose_b)
            self.assertIn("$$REFRESH_SECONDS", compose_b)
            self.assertNotIn('sleep "$REFRESH_SECONDS"', compose_b)
            self.assertIn("test ! -e /data/coolify/proxy/dynamic/main-computer-testnet-hub-public-entry-coolify-b.yml", compose_b)
            self.assertNotIn("http://testnet-hub3:8785", compose_b)
        finally:
            packet_path.unlink(missing_ok=True)

    def test_plan_reports_default_traefik_sidecar_preview(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        plan = coolify_hub_cluster.plan_result(placement, profile, args)

        config = plan["servers"][0]["traefik_dynamic_config"]
        self.assertEqual(config["path"], "/data/coolify/proxy/dynamic/main-computer-testnet-hub-public-entry-coolify-a.yml")
        self.assertTrue(config["installed"])
        self.assertIn("testnet-hub.greatlibrary.io", config["contents"])
        self.assertIn("testnet-hub1:8785", config["contents"])
        self.assertIn("testnet-hub2:8785", config["contents"])

    def test_packet_selects_enabled_hubs_and_renders_config_layer_for_unselected_host(self) -> None:
        packet = coolify_hub_cluster.packet_tool.build_packet(
            network="testnet",
            placement_path=REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json",
            topology_path=None,
            selected_hubs=["testnet-hub1", "testnet-hub2"],
            selected_fdb=["testnet-fdb1"],
            generation="testnet-unit",
        )
        packet_path = REPO_ROOT / "runtime" / "testnet-unit-packet.json"
        try:
            packet_path.write_text(coolify_hub_cluster.packet_tool.canonical_packet_json(packet), encoding="utf-8")
            placement = coolify_hub_cluster.load_hub_cluster_placement_from_packet(packet_path)
            profile = coolify_hub_cluster.load_network_profile(placement, _args())

            compose_a = coolify_hub_cluster.render_server_hub_compose(placement, profile, _args(), "coolify-a")
            compose_b = coolify_hub_cluster.render_server_hub_compose(placement, profile, _args(), "coolify-b")

            self.assertEqual([hub.hub_id for hub in placement.hubs], ["testnet-hub1", "testnet-hub2"])
            self.assertIn("deploy-packet-topology.json", compose_a)
            self.assertIn("testnet-unit", compose_a)
            self.assertIn("testnet-hub1:", compose_a)
            self.assertIn("testnet-hub2:", compose_a)
            self.assertNotIn("testnet-hub3:", compose_a)
            self.assertIn("main_computer_testnet:7f0396a2939ca9c6@10.116.0.3:4550", compose_a)
            self.assertIn("testnet-hubs-disabled:", compose_b)
            self.assertIn("No Hub instances are enabled for testnet on coolify-b.", compose_b)
        finally:
            packet_path.unlink(missing_ok=True)

    def test_network_argument_loads_default_packet_path(self) -> None:
        packet = coolify_hub_cluster.packet_tool.build_packet(
            network="testnet",
            placement_path=REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json",
            topology_path=None,
            selected_hubs=["testnet-hub1", "testnet-hub2"],
            selected_fdb=["testnet-fdb1"],
            generation="testnet-default-path",
        )
        packet_path = REPO_ROOT / "deploy" / "packets" / "testnet-packet.json"
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            packet_path.write_text(coolify_hub_cluster.packet_tool.canonical_packet_json(packet), encoding="utf-8")
            placement = coolify_hub_cluster.load_hub_cluster_placement_from_args(_args(network="testnet"))

            self.assertEqual([hub.hub_id for hub in placement.hubs], ["testnet-hub1", "testnet-hub2"])
        finally:
            packet_path.unlink(missing_ok=True)

    def test_missing_coolify_url_mapping_is_rejected(self) -> None:
        args = _args(set_coolify_url=["coolify-a:http://ipaddress1:8000"])
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        with self.assertRaises(coolify_hub_cluster.CoolifyHubDeployError) as ctx:
            coolify_hub_cluster.plan_result(placement, profile, args)

        self.assertIn("Missing Coolify API URL mapping", str(ctx.exception))
        self.assertIn("coolify-b", str(ctx.exception))

    def test_sync_service_create_uses_services_endpoint(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services"): [
                    {"services": []}
                ],
                ("POST", "/api/v1/services"): [
                    {"uuid": "service-uuid", "name": "main-computer-testnet-hubs-coolify-a"}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        service_uuid, action, existing, update_result = coolify_hub_cluster.sync_service_for_server(
            client,
            placement,
            profile,
            args,
            server_name="coolify-a",
            context={
                "server_uuid": "server-a",
                "project_uuid": "project-a",
                "environment_name": "testnet-hubs",
                "environment_uuid": "env-a",
            },
            tried=tried,
        )

        self.assertEqual(service_uuid, "service-uuid")
        self.assertEqual(action, "created")
        self.assertEqual(existing["source"], "missing")
        self.assertTrue(update_result["domains_included"])
        post = next(request for request in client.requests if request[0] == "POST")
        self.assertEqual(post[1], "/api/v1/services")
        self.assertEqual(post[2]["server_uuid"], "server-a")
        self.assertEqual(post[2]["project_uuid"], "project-a")
        self.assertIn("docker_compose_raw", post[2])
        self.assertEqual(
            post[2]["docker_compose_domains"],
            {
                "testnet-hub1": {"domain": "https://testnet-hub1.greatlibrary.io:8785"},
                "testnet-hub2": {"domain": "https://testnet-hub2.greatlibrary.io:8785"},
            },
        )
        decoded = base64.b64decode(post[2]["docker_compose_raw"]).decode("utf-8")
        self.assertIn("testnet-hub1:", decoded)
        self.assertIn("testnet-hub2:", decoded)


    def test_sync_service_update_sends_compose_domains_before_plain_compose_fallback(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services"): [
                    {
                        "services": [
                            {
                                "uuid": "service-uuid",
                                "name": "main-computer-testnet-hubs-coolify-a",
                            }
                        ]
                    }
                ],
                ("PATCH", "/api/v1/services/service-uuid"): [
                    {"uuid": "service-uuid", "name": "main-computer-testnet-hubs-coolify-a"}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        service_uuid, action, existing, update_result = coolify_hub_cluster.sync_service_for_server(
            client,
            placement,
            profile,
            args,
            server_name="coolify-a",
            context={
                "server_uuid": "server-a",
                "project_uuid": "project-a",
                "environment_name": "testnet-hubs",
                "environment_uuid": "env-a",
            },
            tried=tried,
        )

        self.assertEqual(service_uuid, "service-uuid")
        self.assertEqual(action, "updated")
        self.assertEqual(existing["source"], "name")
        self.assertTrue(update_result["domains_included"])
        patch = next(request for request in client.requests if request[0] == "PATCH")
        self.assertEqual(patch[1], "/api/v1/services/service-uuid")
        self.assertEqual(
            patch[2]["docker_compose_domains"],
            {
                "testnet-hub1": {"domain": "https://testnet-hub1.greatlibrary.io:8785"},
                "testnet-hub2": {"domain": "https://testnet-hub2.greatlibrary.io:8785"},
            },
        )
        self.assertIn("docker_compose_raw", patch[2])


    def test_reconcile_service_application_domains_patches_application_domains(self) -> None:
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services/service-uuid"): [
                    {
                        "uuid": "service-uuid",
                        "applications": [
                            {"uuid": "app-hub1", "name": "testnet-hub1", "fqdn": ""},
                            {"uuid": "app-hub2", "name": "testnet-hub2", "fqdn": ""},
                        ],
                    }
                ],
                ("PATCH", "/api/v1/applications/app-hub1"): [
                    {"uuid": "app-hub1", "fqdn": "https://testnet-hub1.greatlibrary.io:8785"}
                ],
                ("PATCH", "/api/v1/applications/app-hub2"): [
                    {"uuid": "app-hub2", "fqdn": "https://testnet-hub2.greatlibrary.io:8785"}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_cluster.reconcile_service_application_domains(
            client,
            service_uuid="service-uuid",
            domains={
                "testnet-hub1": {"domain": "https://testnet-hub1.greatlibrary.io:8785"},
                "testnet-hub2": {"domain": "https://testnet-hub2.greatlibrary.io:8785"},
            },
            tried=tried,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        patches = [request for request in client.requests if request[0] == "PATCH"]
        self.assertEqual(
            [(method, path, payload) for method, path, payload in patches],
            [
                (
                    "PATCH",
                    "/api/v1/applications/app-hub1",
                    {"domains": "https://testnet-hub1.greatlibrary.io:8785"},
                ),
                (
                    "PATCH",
                    "/api/v1/applications/app-hub2",
                    {"domains": "https://testnet-hub2.greatlibrary.io:8785"},
                ),
            ],
        )

    def test_reconcile_service_application_domains_skips_already_current_domain(self) -> None:
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services/service-uuid"): [
                    {
                        "uuid": "service-uuid",
                        "applications": [
                            {
                                "uuid": "app-hub1",
                                "name": "testnet-hub1",
                                "fqdn": "https://testnet-hub1.greatlibrary.io:8785",
                            }
                        ],
                    }
                ],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_cluster.reconcile_service_application_domains(
            client,
            service_uuid="service-uuid",
            domains={"testnet-hub1": {"domain": "https://testnet-hub1.greatlibrary.io:8785"}},
            tried=tried,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertFalse([request for request in client.requests if request[0] == "PATCH"])

    def test_reconcile_service_application_domains_matches_coolify_display_name(self) -> None:
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services/service-uuid"): [
                    {
                        "uuid": "service-uuid",
                        "applications": [
                            {
                                "uuid": "app-hub1",
                                "name": "Mainnet Hub1",
                                "image": "main-computer-mainnet-mainnet-hub1:remote",
                                "fqdn": "",
                            }
                        ],
                    }
                ],
                ("PATCH", "/api/v1/applications/app-hub1"): [
                    {"uuid": "app-hub1", "fqdn": "https://mainnet-hub1.greatlibrary.io:8790"}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_cluster.reconcile_service_application_domains(
            client,
            service_uuid="service-uuid",
            domains={"mainnet-hub1": {"domain": "https://mainnet-hub1.greatlibrary.io:8790"}},
            tried=tried,
        )

        self.assertTrue(result["changed"])
        patch = next(request for request in client.requests if request[0] == "PATCH")
        self.assertEqual(patch[1], "/api/v1/applications/app-hub1")
        self.assertEqual(patch[2], {"domains": "https://mainnet-hub1.greatlibrary.io:8790"})



    def test_create_hub_application_retries_with_minimal_payload_after_500(self) -> None:
        client = RouteCoolifyClient(
            {
                ("POST", "/api/v1/applications/public"): [
                    {"_status": 500, "message": "Server Error"},
                    {"uuid": "app-uuid"},
                ],
                ("GET", "/api/v1/applications"): [
                    {"applications": []}
                ],
            }
        )
        tried: list[dict[str, object]] = []
        payload = {
            "project_uuid": "project-uuid",
            "server_uuid": "server-uuid",
            "environment_name": "mainnet-hubs",
            "environment_uuid": "environment-uuid",
            "git_repository": "https://github.com/example/main-computer",
            "git_branch": "main",
            "build_pack": "dockerfile",
            "ports_exposes": "8790",
            "base_directory": "/",
            "dockerfile_location": "/Dockerfile.hub.exp-fdb",
            "name": "main-computer-mainnet-hub1",
            "description": "Hub 1",
            "domains": "https://mainnet-hub1.greatlibrary.io:8790",
            "start_command": "python /app/exp-fdb-hub.py --port 8790",
            "health_check_enabled": True,
            "health_check_path": "/api/hub/v1/health",
            "instant_deploy": False,
        }

        uuid = coolify_hub_cluster.create_hub_application(client, payload, _args(), tried)

        self.assertEqual(uuid, "app-uuid")
        posts = [request for request in client.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][2]["domains"], "https://mainnet-hub1.greatlibrary.io:8790")
        self.assertIn("start_command", posts[0][2])
        self.assertNotIn("domains", posts[1][2])
        self.assertIn("start_command", posts[1][2])
        self.assertEqual(tried[0]["variant"], "full")
        self.assertEqual(tried[-1]["variant"], "domains-and-health-deferred")



    def test_update_hub_application_splits_domain_and_command_after_full_500(self) -> None:
        client = RouteCoolifyClient(
            {
                ("PATCH", "/api/v1/applications/app-uuid"): [
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                    {"uuid": "app-uuid", "domains": "https://mainnet-hub1.greatlibrary.io:8790"},
                    {"uuid": "app-uuid", "start_command": "python /app/exp-fdb-hub.py --port 8790"},
                    {"uuid": "app-uuid", "health_check_path": "/api/hub/v1/health"},
                ],
            }
        )
        tried: list[dict[str, object]] = []
        payload = {
            "name": "main-computer-mainnet-hub1",
            "description": "Hub 1",
            "domains": "https://mainnet-hub1.greatlibrary.io:8790",
            "ports_exposes": "8790",
            "start_command": "python /app/exp-fdb-hub.py --port 8790",
            "health_check_enabled": True,
            "health_check_path": "/api/hub/v1/health",
        }

        result = coolify_hub_cluster.update_hub_application(client, "app-uuid", payload, tried)

        self.assertTrue(result["ok"])
        self.assertEqual(result["strategy"], "split-updates")
        patches = [request for request in client.requests if request[0] == "PATCH"]
        self.assertEqual(patches[0][2], payload)
        self.assertEqual(patches[1][2], {
            "domains": "https://mainnet-hub1.greatlibrary.io:8790",
            "ports_exposes": "8790",
            "start_command": "python /app/exp-fdb-hub.py --port 8790",
        })
        self.assertEqual(patches[2][2], {"domains": "https://mainnet-hub1.greatlibrary.io:8790", "ports_exposes": "8790"})
        self.assertEqual(patches[3][2], {"domains": "https://mainnet-hub1.greatlibrary.io:8790"})
        self.assertEqual(patches[4][2], {"start_command": "python /app/exp-fdb-hub.py --port 8790", "ports_exposes": "8790"})
        self.assertEqual(patches[5][2], {"health_check_enabled": True, "health_check_path": "/api/hub/v1/health"})


    def test_update_hub_application_keeps_domain_port_when_start_command_update_fails(self) -> None:
        client = RouteCoolifyClient(
            {
                ("PATCH", "/api/v1/applications/app-uuid"): [
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                    {"uuid": "app-uuid", "domains": "https://mainnet-hub1.greatlibrary.io:8790"},
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                ],
            }
        )
        tried: list[dict[str, object]] = []
        payload = {
            "name": "main-computer-mainnet-hub1",
            "description": "Hub 1",
            "domains": "https://mainnet-hub1.greatlibrary.io:8790",
            "ports_exposes": "8790",
            "start_command": "python /app/exp-fdb-hub.py --port 8790",
        }

        result = coolify_hub_cluster.update_hub_application(client, "app-uuid", payload, tried)

        self.assertTrue(result["ok"])
        self.assertEqual(result["strategy"], "split-updates")
        self.assertEqual(result["domains"], "https://mainnet-hub1.greatlibrary.io:8790")
        self.assertTrue(result["command_update_failed"])
        self.assertEqual(result["warnings"][0]["operation"], "start-command-update")
        patches = [request for request in client.requests if request[0] == "PATCH"]
        self.assertEqual(patches[2][2], {"domains": "https://mainnet-hub1.greatlibrary.io:8790", "ports_exposes": "8790"})
        self.assertEqual(patches[3][2], {"start_command": "python /app/exp-fdb-hub.py --port 8790", "ports_exposes": "8790"})
        self.assertEqual(patches[4][2], {"start_command": "python /app/exp-fdb-hub.py --port 8790"})

    def test_update_hub_application_still_fails_start_command_without_domain_success(self) -> None:
        client = RouteCoolifyClient(
            {
                ("PATCH", "/api/v1/applications/app-uuid"): [
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                    {"_status": 500, "message": "Server Error"},
                ],
            }
        )
        tried: list[dict[str, object]] = []
        payload = {
            "ports_exposes": "8790",
            "start_command": "python /app/exp-fdb-hub.py --port 8790",
        }

        with self.assertRaises(coolify_hub_cluster.CoolifyHubDeployError):
            coolify_hub_cluster.update_hub_application(client, "app-uuid", payload, tried)


    def test_hub_command_parts_explicit_mainnet_contracts_path_omits_missing_signer_manifest(self) -> None:
        placement = coolify_hub_cluster.load_hub_cluster_placement(
            REPO_ROOT / "deploy" / "hub-topology" / "mainnet-coolify-deployment.json"
        )
        profile = coolify_hub_cluster.load_network_profile(
            placement,
            _args(network="mainnet", bridge_backend="credit-bridge-contract"),
        )
        hub = placement.hubs[0]
        args = _args(
            network="mainnet",
            bridge_backend="credit-bridge-contract",
            contracts_path="main_computer/config/mainnet_contracts.json",
            hub_chain_rpc_url="https://mainnet-rpc.greatlibrary.io",
        )

        command = coolify_hub_cluster.hub_command_parts(profile, placement, hub, args)

        self.assertIn("--contracts-path", command)
        self.assertIn("main_computer/config/mainnet_contracts.json", command)
        self.assertIn("--allow-missing-bridge-signer", command)
        self.assertNotIn("--dev-chain-deployment-path", command)

    def test_hub_command_parts_mainnet_bridge_writes_require_signer_manifest(self) -> None:
        placement = coolify_hub_cluster.load_hub_cluster_placement(
            REPO_ROOT / "deploy" / "hub-topology" / "mainnet-coolify-deployment.json"
        )
        profile = coolify_hub_cluster.load_network_profile(
            placement,
            _args(network="mainnet", bridge_backend="credit-bridge-contract"),
        )
        hub = placement.hubs[0]
        args = _args(
            network="mainnet",
            bridge_backend="credit-bridge-contract",
            contracts_path="main_computer/config/mainnet_contracts.json",
            hub_chain_rpc_url="https://mainnet-rpc.greatlibrary.io",
            enable_bridge_writes=True,
        )

        command = coolify_hub_cluster.hub_command_parts(profile, placement, hub, args)

        self.assertIn("--contracts-path", command)
        self.assertIn("main_computer/config/mainnet_contracts.json", command)
        self.assertIn("--dev-chain-deployment-path", command)
        self.assertNotIn("--allow-missing-bridge-signer", command)

    def test_wait_for_hub_ready_uses_concrete_hub_public_url(self) -> None:
        placement = coolify_hub_cluster.load_hub_cluster_placement(_args().placement)
        profile = coolify_hub_cluster.load_network_profile(placement, _args(network="testnet"))
        hub = placement.hubs[0]
        args = _args(
            hub_wait_timeout_s=1.0,
            hub_wait_poll_s=0.0,
            hub_status_timeout_s=0.2,
            hub_status_user_agent="unit-test",
        )
        tried: list[dict[str, object]] = []
        captured: list[str] = []

        class FakeHubStatusResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"network":{"network_key":"testnet","chain_id":42424241}}'

        original_urlopen = coolify_hub_cluster.urllib.request.urlopen

        def fake_urlopen(request, timeout=0):
            del timeout
            captured.append(request.full_url)
            return FakeHubStatusResponse()

        coolify_hub_cluster.urllib.request.urlopen = fake_urlopen
        try:
            result = coolify_hub_cluster.wait_for_hub_ready(
                placement,
                profile,
                args,
                hub=hub,
                tried=tried,
            )
        finally:
            coolify_hub_cluster.urllib.request.urlopen = original_urlopen

        self.assertTrue(result["ok"])
        self.assertEqual(captured, [hub.public_url.rstrip("/") + "/api/hub/v1/health"])
        self.assertEqual(tried[-1]["operation"], "wait-hub-ready")

    def test_wait_for_hub_ready_can_be_disabled_for_emergency_rollout(self) -> None:
        placement = coolify_hub_cluster.load_hub_cluster_placement(_args().placement)
        profile = coolify_hub_cluster.load_network_profile(placement, _args(network="testnet"))
        result = coolify_hub_cluster.wait_for_hub_ready(
            placement,
            profile,
            _args(no_wait_hubs=True),
            hub=placement.hubs[0],
            tried=[],
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "--no-wait-hubs")





if __name__ == "__main__":
    unittest.main()
