from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "coolify_fdb_cluster.py"

spec = importlib.util.spec_from_file_location("coolify_fdb_cluster", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
coolify_fdb_cluster = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coolify_fdb_cluster
spec.loader.exec_module(coolify_fdb_cluster)


def _args(**overrides):
    defaults = {
        "placement": REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json",
        "set_coolify_url": [
            "coolify-a:https://ipaddress1:8000",
            "coolify-b:https://ipaddress2:8000",
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
        "coolify_environment_name": "testnet-fdb",
        "coolify_environment_uuid": "",
        "set_coolify_environment_uuid": [],
        "no_create_environment": False,
        "coolify_server_name": "",
        "coolify_server_uuid": "",
        "set_coolify_server_name": [],
        "set_coolify_server_uuid": [],
        "coolify_destination_uuid": "",
        "set_coolify_destination_uuid": [],
        "coolify_timeout_s": 1.0,
        "coolify_retries": 0,
        "coolify_retry_sleep_s": 0.0,
        "no_deploy": False,
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
            return coolify_fdb_cluster.CoolifyResponse(
                ok=False,
                status=404,
                method=method,
                path=path,
                body={"message": f"no fake route for {method} {path}"},
            )
        response = responses.pop(0)
        status = int(response.pop("_status", 200)) if isinstance(response, dict) else 200
        return coolify_fdb_cluster.CoolifyResponse(
            ok=200 <= status < 300,
            status=status,
            method=method,
            path=path,
            body=response,
        )


class CoolifyFdbClusterTests(unittest.TestCase):
    def test_set_coolify_url_splits_on_first_colon(self) -> None:
        binding = coolify_fdb_cluster.split_name_value(
            "coolify-a:https://ipaddress1:8000",
            "--set-coolify-url",
        )

        self.assertEqual(binding.name, "coolify-a")
        self.assertEqual(binding.value, "https://ipaddress1:8000")

    def test_testnet_placement_renders_shared_cluster_contents(self) -> None:
        placement = coolify_fdb_cluster.load_fdb_placement(_args().placement)

        self.assertEqual(
            coolify_fdb_cluster.fdb_cluster_contents(placement),
            "main-computer-testnet:7f0396a2939ca9c6@10.10.0.5:4550,10.10.0.5:4551,10.124.0.3:4550",
        )
        self.assertEqual(placement.namespace, "main-computer-testnet-exp-fdb-stable-live-sessions")
        self.assertEqual(placement.cluster_file_path, "/data/main-computer/hub/testnet-exp-fdb/fdb.cluster")

    def test_plan_renders_one_service_per_coolify_host(self) -> None:
        placement = coolify_fdb_cluster.load_fdb_placement(_args().placement)
        plan = coolify_fdb_cluster.plan_result(placement, _args())

        self.assertEqual(plan["fdb"]["configure"], "double ssd")
        self.assertEqual(plan["fdb"]["cluster_dir"], "/data/main-computer/hub/testnet-exp-fdb")
        self.assertEqual([server["server"] for server in plan["servers"]], ["coolify-a", "coolify-b"])
        self.assertEqual(plan["servers"][0]["service_name"], "main-computer-testnet-fdb-coolify-a")
        self.assertEqual(plan["servers"][1]["service_name"], "main-computer-testnet-fdb-coolify-b")

    def test_coolify_a_compose_binds_only_vpn_ip_ports_for_local_instances(self) -> None:
        placement = coolify_fdb_cluster.load_fdb_placement(_args().placement)

        compose = coolify_fdb_cluster.render_server_fdb_compose(placement, "coolify-a")

        self.assertIn('"10.10.0.5:4550:4550/tcp"', compose)
        self.assertIn('"10.10.0.5:4551:4551/tcp"', compose)
        self.assertNotIn('"10.124.0.3:4550:4550/tcp"', compose)
        self.assertIn("public-address = 10.10.0.5:4550", compose)
        self.assertIn("listen-address = 0.0.0.0:4550", compose)
        self.assertIn("locality-machineid = coolify-a", compose)
        self.assertIn("locality-zoneid = coolify-a", compose)
        self.assertIn("entrypoint:", compose)
        self.assertIn("      - /bin/sh", compose)
        self.assertIn("      - -euc", compose)
        self.assertNotIn("    command:", compose)
        self.assertNotIn("/var/fdb/scripts/fdb.bash", compose)
        self.assertIn("Starting Main Computer FDB instance testnet-fdb1 on 10.10.0.5:4550", compose)
        self.assertIn("knob_disable_posix_kernel_aio = 1", compose)
        self.assertIn("fdbmonitor --conffile", compose)
        self.assertIn("configure new double ssd", compose)
        self.assertIn("/data/main-computer/hub/testnet-exp-fdb/fdb.cluster", compose)

    def test_service_payload_contains_base64_compose_and_context(self) -> None:
        placement = coolify_fdb_cluster.load_fdb_placement(_args().placement)

        payload = coolify_fdb_cluster.service_payload(
            placement,
            _args(),
            server_name="coolify-b",
            context={
                "server_uuid": "server-b",
                "project_uuid": "project-b",
                "environment_name": "testnet-fdb",
                "environment_uuid": "env-b",
            },
        )

        self.assertEqual(payload["name"], "main-computer-testnet-fdb-coolify-b")
        self.assertEqual(payload["server_uuid"], "server-b")
        self.assertEqual(payload["project_uuid"], "project-b")
        self.assertEqual(payload["environment_uuid"], "env-b")
        compose = base64.b64decode(payload["docker_compose_raw"]).decode("utf-8")
        self.assertIn("testnet-fdb3:", compose)
        self.assertIn('"10.124.0.3:4550:4550/tcp"', compose)
        self.assertIn("entrypoint:", compose)
        self.assertNotIn("    command:", compose)

    def test_missing_coolify_url_mapping_is_rejected(self) -> None:
        placement = coolify_fdb_cluster.load_fdb_placement(_args().placement)

        with self.assertRaises(coolify_fdb_cluster.CoolifyHubDeployError) as ctx:
            coolify_fdb_cluster.plan_result(
                placement,
                _args(set_coolify_url=["coolify-a:https://ipaddress1:8000"]),
            )

        self.assertIn("Missing Coolify API URL mapping", str(ctx.exception))
        self.assertIn("coolify-b", str(ctx.exception))

    def test_sync_service_create_uses_services_endpoint(self) -> None:
        placement = coolify_fdb_cluster.load_fdb_placement(_args().placement)
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services"): [
                    {"services": []}
                ],
                ("POST", "/api/v1/services"): [
                    {"uuid": "service-uuid", "name": "main-computer-testnet-fdb-coolify-a"}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        service_uuid, action, existing = coolify_fdb_cluster.sync_service_for_server(
            client,
            placement,
            _args(),
            server_name="coolify-a",
            context={
                "server_uuid": "server-a",
                "project_uuid": "project-a",
                "environment_name": "testnet-fdb",
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
        self.assertIn("testnet-fdb1:", decoded)
        self.assertIn("testnet-fdb2:", decoded)


if __name__ == "__main__":
    unittest.main()
