from __future__ import annotations

import argparse
import base64
import importlib.util
import sys
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
        "health_path": "/api/hub/status",
        "hub_chain_rpc_url": "",
        "bridge_backend": "",
        "dev_chain_deployment_path": "",
        "contracts_path": "",
        "allow_missing_bridge_signer": False,
        "enable_smoke_bridge": False,
        "enable_bridge_writes": False,
        "sync_bridge_signer": False,
        "bridge_signer_source_manifest": "",
        "bridge_controller_wallet_path": "",
        "bridge_signer_env_key": "",
        "bridge_signer_remote_path": "",
        "coolify_timeout_s": 1.0,
        "coolify_retries": 0,
        "coolify_retry_sleep_s": 0.0,
        "no_deploy": False,
        "install_traefik_dynamic_config": False,
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
        self.assertNotIn("testnet-hub3:", compose)
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
        compose = base64.b64decode(payload["docker_compose_raw"]).decode("utf-8")
        self.assertIn("testnet-hub3:", compose)
        self.assertIn("testnet-hub3.greatlibrary.io", compose)
        self.assertNotIn("testnet-hub1:", compose)

    def test_render_traefik_dynamic_config_uses_public_entry_urls_and_local_hubs(self) -> None:
        args = _args()
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        config = coolify_hub_cluster.render_server_traefik_dynamic_config(placement, profile, args, "coolify-a")

        self.assertIn("Generated by tools/coolify_hub_cluster.py --install-traefik-dynamic-config", config)
        self.assertIn("Host(`testnet-hub.greatlibrary.io`)", config)
        self.assertIn("certResolver: letsencrypt", config)
        self.assertIn("path: \"/api/hub/status\"", config)
        self.assertIn('url: "http://testnet-hub1:8785"', config)
        self.assertIn('url: "http://testnet-hub2:8785"', config)
        self.assertNotIn('url: "http://testnet-hub3:8785"', config)

    def test_install_traefik_dynamic_config_adds_writer_service_to_compose(self) -> None:
        args = _args(install_traefik_dynamic_config=True)
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        compose = coolify_hub_cluster.render_server_hub_compose(placement, profile, args, "coolify-b")

        self.assertIn("testnet-hub3:", compose)
        self.assertIn("testnet-hub-public-entry-config-coolify-b:", compose)
        self.assertIn("alpine:3.20", compose)
        self.assertIn("/data/coolify/proxy/dynamic:/data/coolify/proxy/dynamic", compose)
        self.assertIn("main-computer-testnet-hub-public-entry-coolify-b.yml", compose)
        self.assertIn("Host(`testnet-hub.greatlibrary.io`)", compose)
        self.assertIn("http://testnet-hub3:8785", compose)
        self.assertNotIn("http://testnet-hub1:8785", compose)

    def test_plan_reports_traefik_dynamic_config_preview(self) -> None:
        args = _args(install_traefik_dynamic_config=True)
        placement = coolify_hub_cluster.load_hub_cluster_placement(args.placement)
        profile = coolify_hub_cluster.load_network_profile(placement, args)

        plan = coolify_hub_cluster.plan_result(placement, profile, args)

        config = plan["servers"][0]["traefik_dynamic_config"]
        self.assertEqual(config["path"], "/data/coolify/proxy/dynamic/main-computer-testnet-hub-public-entry-coolify-a.yml")
        self.assertTrue(config["installed"])
        self.assertIn("testnet-hub.greatlibrary.io", config["contents"])
        self.assertIn("testnet-hub1:8785", config["contents"])
        self.assertIn("testnet-hub2:8785", config["contents"])

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

        service_uuid, action, existing = coolify_hub_cluster.sync_service_for_server(
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
        post = next(request for request in client.requests if request[0] == "POST")
        self.assertEqual(post[1], "/api/v1/services")
        self.assertEqual(post[2]["server_uuid"], "server-a")
        self.assertEqual(post[2]["project_uuid"], "project-a")
        self.assertIn("docker_compose_raw", post[2])
        decoded = base64.b64decode(post[2]["docker_compose_raw"]).decode("utf-8")
        self.assertIn("testnet-hub1:", decoded)
        self.assertIn("testnet-hub2:", decoded)


if __name__ == "__main__":
    unittest.main()
