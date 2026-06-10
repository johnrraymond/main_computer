from __future__ import annotations

import argparse
import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "coolify_hub_service.py"

spec = importlib.util.spec_from_file_location("coolify_hub_service", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
coolify_hub_service = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coolify_hub_service
spec.loader.exec_module(coolify_hub_service)


def _args(**overrides):
    defaults = {
        "coolify_project_uuid": "project-uuid",
        "coolify_server_uuid": "server-uuid",
        "coolify_server_name": "",
        "coolify_environment_name": "mainnet",
        "coolify_environment_uuid": "",
        "coolify_destination_uuid": "",
        "git_repo": "https://github.com/example/main_computer.git",
        "git_branch": "main",
        "git_commit_sha": "",
        "base_directory": "/",
        "dockerfile_location": "",
        "health_path": "/api/hub/status",
        "github_app_uuid": "",
        "deploy_key_uuid": "",
        "hub_runtime_dir": "",
        "coolify_application_name": "",
        "rpc_check": "auto",
        "skip_rpc_check": False,
        "hub_health_check": "auto",
        "no_wait_hub": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class FakeCoolifyClient:
    def __init__(self, body):
        self.body = body
        self.requests = []

    def request(self, method: str, path: str, payload=None):
        self.requests.append((method, path, payload))
        return coolify_hub_service.CoolifyResponse(ok=True, status=200, method=method, path=path, body=self.body)


class CoolifyHubServiceTests(unittest.TestCase):
    def test_application_payload_uses_remote_profile_and_persistent_runtime_dir(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args()
        payload = coolify_hub_service.application_payload(
            profile,
            args,
            service_name="main-computer-mainnet-hub",
            runtime_dir="/data/main-computer/hub/mainnet",
        )

        self.assertEqual(payload["name"], "main-computer-mainnet-hub")
        self.assertEqual(payload["git_repository"], "https://github.com/example/main_computer.git")
        self.assertEqual(payload["git_branch"], "main")
        self.assertEqual(payload["build_pack"], "dockerfile")
        self.assertEqual(payload["dockerfile_location"], "/Dockerfile.hub.mainnet")
        self.assertEqual(payload["ports_exposes"], "8790")
        self.assertEqual(payload["domains"], "https://mainnet.greatlibrary.io")
        self.assertNotIn("urls", payload)
        self.assertEqual(
            payload["start_command"],
            "--network mainnet --host 0.0.0.0 --port 8790 --hub-runtime-dir /data/main-computer/hub/mainnet",
        )
        self.assertTrue(payload["health_check_enabled"])
        self.assertEqual(payload["health_check_path"], "/api/hub/status")



    def test_explicit_dockerfile_location_override_is_respected(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(dockerfile_location="/Dockerfile.hub")
        payload = coolify_hub_service.application_payload(
            profile,
            args,
            service_name="main-computer-testnet-hub",
            runtime_dir="/data/main-computer/hub/testnet",
        )

        self.assertEqual(payload["dockerfile_location"], "/Dockerfile.hub")

    def test_update_payload_does_not_try_to_move_application_between_contexts(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(coolify_environment_name="testnet")
        payload = coolify_hub_service.application_update_payload(
            profile,
            args,
            service_name="main-computer-testnet-hub",
            runtime_dir="/data/main-computer/hub/testnet",
        )

        self.assertNotIn("project_uuid", payload)
        self.assertNotIn("server_uuid", payload)
        self.assertNotIn("environment_name", payload)
        self.assertNotIn("environment_uuid", payload)
        self.assertNotIn("git_repository", payload)
        self.assertNotIn("urls", payload)
        self.assertEqual(payload["ports_exposes"], "8785")
        self.assertEqual(payload["domains"], "https://testnet.greatlibrary.io")

    def test_storage_payload_is_persistent_and_network_scoped(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        payload = coolify_hub_service.storage_payload(profile, runtime_dir="/data/main-computer/hub/mainnet")

        self.assertEqual(payload["type"], "persistent")
        self.assertEqual(payload["name"], "mainnet_hub_state")
        self.assertEqual(payload["mount_path"], "/data/main-computer/hub/mainnet")
        self.assertEqual(payload["host_path"], "/data/main-computer/hub/mainnet")

    def test_select_by_exact_name_refuses_duplicate_names(self) -> None:
        items = [
            {"uuid": "a", "name": "main-computer-mainnet-hub"},
            {"uuid": "b", "name": "main-computer-mainnet-hub"},
        ]

        uuid, matches = coolify_hub_service.select_by_exact_name(items, "main-computer-mainnet-hub")

        self.assertEqual(uuid, "")
        self.assertEqual(len(matches), 2)

    def test_plan_result_uses_stable_service_name_and_volume(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(coolify_environment_name="testnet")
        plan = coolify_hub_service.plan_result(profile, args)

        self.assertEqual(plan["service_name"], "main-computer-testnet-hub")
        self.assertEqual(plan["runtime_dir"], "/data/main-computer/hub/testnet")
        self.assertEqual(plan["volume_name"], "testnet_hub_state")
        self.assertEqual(plan["chain_id"], 42424241)
        self.assertEqual(plan["public_url"], "https://testnet.greatlibrary.io")
        self.assertEqual(plan["application_payload"]["dockerfile_location"], "/Dockerfile.hub.testnet")
        self.assertNotIn("urls", plan["application_payload"])

    def test_resolve_server_infers_single_server_when_uuid_and_name_omitted(self) -> None:
        client = FakeCoolifyClient({"servers": [{"uuid": "server-only", "name": "primary"}]})
        tried = []

        uuid = coolify_hub_service.resolve_exact_resource_uuid(
            client,
            path="/api/v1/servers",
            preferred_keys=("servers",),
            resource_kind="server",
            explicit_uuid="",
            explicit_name="",
            tried=tried,
            infer_if_single=True,
        )

        self.assertEqual(uuid, "server-only")
        self.assertEqual(client.requests[0][1], "/api/v1/servers")
        self.assertEqual(tried[0]["resolver"], "single")

    def test_resolve_server_refuses_to_infer_when_multiple_servers_exist(self) -> None:
        client = FakeCoolifyClient(
            {"servers": [{"uuid": "server-a", "name": "alpha"}, {"uuid": "server-b", "name": "beta"}]}
        )

        with self.assertRaises(coolify_hub_service.CoolifyHubDeployError) as ctx:
            coolify_hub_service.resolve_exact_resource_uuid(
                client,
                path="/api/v1/servers",
                preferred_keys=("servers",),
                resource_kind="server",
                explicit_uuid="",
                explicit_name="",
                tried=[],
                infer_if_single=True,
            )

        self.assertIn("Multiple Coolify servers were returned", str(ctx.exception))
        self.assertIn("--coolify-server-uuid", str(ctx.exception))

    def test_testnet_check_modes_warn_by_default(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(rpc_check="auto", hub_health_check="auto")

        self.assertEqual(coolify_hub_service.rpc_check_mode(profile, args), "warn")
        self.assertEqual(coolify_hub_service.hub_health_check_mode(profile, args), "warn")

    def test_mainnet_check_modes_require_by_default(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(rpc_check="auto", hub_health_check="auto")

        self.assertEqual(coolify_hub_service.rpc_check_mode(profile, args), "require")
        self.assertEqual(coolify_hub_service.hub_health_check_mode(profile, args), "require")

    def test_legacy_skip_flags_override_check_modes(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(rpc_check="require", hub_health_check="require", skip_rpc_check=True, no_wait_hub=True)

        self.assertEqual(coolify_hub_service.rpc_check_mode(profile, args), "skip")
        self.assertEqual(coolify_hub_service.hub_health_check_mode(profile, args), "skip")


    def test_network_dockerfiles_have_matching_safe_defaults_and_healthcheck_client(self) -> None:
        testnet_dockerfile = (REPO_ROOT / "Dockerfile.hub.testnet").read_text(encoding="utf-8")
        mainnet_dockerfile = (REPO_ROOT / "Dockerfile.hub.mainnet").read_text(encoding="utf-8")

        self.assertIn("curl wget", testnet_dockerfile)
        self.assertIn("--network\", \"testnet", testnet_dockerfile)
        self.assertIn("--port\", \"8785", testnet_dockerfile)
        self.assertIn("/data/main-computer/hub/testnet", testnet_dockerfile)
        self.assertIn("curl wget", mainnet_dockerfile)
        self.assertIn("--network\", \"mainnet", mainnet_dockerfile)
        self.assertIn("--port\", \"8790", mainnet_dockerfile)
        self.assertIn("/data/main-computer/hub/mainnet", mainnet_dockerfile)


if __name__ == "__main__":
    unittest.main()
