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
        "coolify_project_name": "",
        "coolify_server_uuid": "server-uuid",
        "coolify_server_name": "",
        "coolify_environment_name": "mainnet",
        "coolify_environment_uuid": "",
        "no_create_environment": False,
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
        "hub_implementation": coolify_hub_service.HUB_IMPLEMENTATION_REGULAR,
        "replace_regular_hub": False,
        "fdb_cluster_file": "",
        "fdb_namespace": "",
        "coolify_application_name": "",
        "rpc_check": "auto",
        "rpc_user_agent": coolify_hub_service.DEFAULT_JSON_RPC_USER_AGENT,
        "skip_rpc_check": False,
        "hub_health_check": "auto",
        "no_wait_hub": False,
        "hub_status_user_agent": coolify_hub_service.DEFAULT_JSON_RPC_USER_AGENT,
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
            return coolify_hub_service.CoolifyResponse(
                ok=False,
                status=404,
                method=method,
                path=path,
                body={"message": f"no fake route for {method} {path}"},
            )
        response = responses.pop(0)
        if isinstance(response, coolify_hub_service.CoolifyResponse):
            return response
        status = int(response.pop("_status", 200)) if isinstance(response, dict) else 200
        return coolify_hub_service.CoolifyResponse(
            ok=200 <= status < 300,
            status=status,
            method=method,
            path=path,
            body=response,
        )


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
        self.assertEqual(payload["domains"], "https://mainnet-hub.greatlibrary.io:8790")
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
        self.assertEqual(payload["domains"], "https://testnet-hub.greatlibrary.io:8785")

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
        self.assertEqual(plan["public_url"], "https://testnet-hub.greatlibrary.io")
        self.assertEqual(plan["application_payload"]["dockerfile_location"], "/Dockerfile.hub.testnet")
        self.assertNotIn("urls", plan["application_payload"])

    def test_exp_fdb_plan_uses_side_by_side_service_and_fdb_startup(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(hub_implementation=coolify_hub_service.HUB_IMPLEMENTATION_EXP_FDB)
        plan = coolify_hub_service.plan_result(profile, args)

        self.assertEqual(plan["hub_implementation"], "exp-fdb")
        self.assertEqual(plan["service_name"], "main-computer-mainnet-exp-fdb-hub")
        self.assertEqual(plan["runtime_dir"], "/data/main-computer/hub/mainnet-exp-fdb")
        self.assertEqual(plan["volume_name"], "mainnet_exp_fdb_hub_state")
        self.assertEqual(plan["fdb_cluster_file"], "/data/main-computer/hub/mainnet-exp-fdb/fdb.cluster")
        self.assertEqual(plan["fdb_namespace"], "main-computer-mainnet-exp-fdb")
        payload = plan["application_payload"]
        self.assertEqual(payload["dockerfile_location"], "/Dockerfile.hub.exp-fdb")
        self.assertEqual(payload["ports_exposes"], "8790")
        self.assertEqual(payload["domains"], "https://mainnet-hub.greatlibrary.io:8790")
        self.assertIn("--hub-root /data/main-computer/hub/mainnet-exp-fdb", payload["start_command"])
        self.assertIn("--cluster-file /data/main-computer/hub/mainnet-exp-fdb/fdb.cluster", payload["start_command"])
        self.assertIn("--namespace main-computer-mainnet-exp-fdb", payload["start_command"])
        self.assertIn("--network-key mainnet", payload["start_command"])
        self.assertIn("--chain-id 42424240", payload["start_command"])
        self.assertIn("--chain-rpc-url https://mainnet-rpc.greatlibrary.io", payload["start_command"])
        self.assertIn("--no-fdb-autostart", payload["start_command"])

    def test_exp_fdb_can_explicitly_replace_regular_hub_service_name(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            hub_implementation=coolify_hub_service.HUB_IMPLEMENTATION_EXP_FDB,
            replace_regular_hub=True,
            fdb_cluster_file="/data/main-computer/fdb/fdb.cluster",
            fdb_namespace="main-computer-mainnet-cutover",
        )
        plan = coolify_hub_service.plan_result(profile, args)

        self.assertEqual(plan["service_name"], "main-computer-mainnet-hub")
        self.assertTrue(plan["replace_regular_hub"])
        self.assertEqual(plan["fdb_cluster_file"], "/data/main-computer/fdb/fdb.cluster")
        self.assertEqual(plan["fdb_namespace"], "main-computer-mainnet-cutover")
        self.assertIn("--cluster-file /data/main-computer/fdb/fdb.cluster", plan["application_payload"]["start_command"])
        self.assertIn("--namespace main-computer-mainnet-cutover", plan["application_payload"]["start_command"])

    def test_replace_regular_hub_requires_exp_fdb_implementation(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(replace_regular_hub=True)

        with self.assertRaises(coolify_hub_service.CoolifyHubDeployError) as ctx:
            coolify_hub_service.validate_hub_deploy_args(profile, args)

        self.assertIn("--hub-implementation exp-fdb", str(ctx.exception))

    def test_resolve_context_creates_missing_hub_environment(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            coolify_project_uuid="",
            coolify_project_name="My first project",
            coolify_server_uuid="",
            coolify_environment_name="mainnet-hub",
        )
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/projects"): [
                    {"projects": [{"uuid": "project-uuid", "name": "My first project"}]}
                ],
                ("GET", "/api/v1/projects/project-uuid/environments"): [
                    {
                        "environments": [
                            {"uuid": "env-production", "name": "production"},
                            {"uuid": "env-mainnet", "name": "mainnet"},
                        ]
                    }
                ],
                ("POST", "/api/v1/projects/project-uuid/environments"): [
                    {"uuid": "env-mainnet-hub", "name": "mainnet-hub"}
                ],
                ("GET", "/api/v1/servers"): [
                    {"servers": [{"uuid": "server-only", "name": "localhost"}]}
                ],
            }
        )
        tried = []

        context = coolify_hub_service.resolve_coolify_context(client, profile, args, tried)

        self.assertEqual(args.coolify_environment_name, "mainnet-hub")
        self.assertEqual(args.coolify_environment_uuid, "env-mainnet-hub")
        self.assertEqual(context["environment"]["source"], "created")
        self.assertEqual(context["environment_uuid"], "env-mainnet-hub")
        self.assertIn(
            ("POST", "/api/v1/projects/project-uuid/environments", {"name": "mainnet-hub"}),
            client.requests,
        )

    def test_resolve_context_reuses_existing_hub_environment(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            coolify_project_uuid="project-uuid",
            coolify_server_uuid="server-only",
            coolify_environment_name="mainnet-hub",
        )
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/projects/project-uuid/environments"): [
                    {"environments": [{"uuid": "env-mainnet-hub", "name": "mainnet-hub"}]}
                ],
            }
        )

        context = coolify_hub_service.resolve_coolify_context(client, profile, args, [])

        self.assertEqual(context["environment"]["source"], "existing")
        self.assertEqual(args.coolify_environment_uuid, "env-mainnet-hub")
        self.assertFalse(any(request[0] == "POST" for request in client.requests))

    def test_resolve_context_can_refuse_missing_environment_creation(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            coolify_project_uuid="project-uuid",
            coolify_server_uuid="server-only",
            coolify_environment_name="mainnet-hub",
            no_create_environment=True,
        )
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/projects/project-uuid/environments"): [
                    {"environments": [{"uuid": "env-mainnet", "name": "mainnet"}]}
                ],
            }
        )

        with self.assertRaises(coolify_hub_service.CoolifyHubDeployError) as ctx:
            coolify_hub_service.resolve_coolify_context(client, profile, args, [])

        self.assertIn("mainnet-hub", str(ctx.exception))
        self.assertFalse(any(request[0] == "POST" for request in client.requests))

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
        exp_fdb_dockerfile = (REPO_ROOT / "Dockerfile.hub.exp-fdb").read_text(encoding="utf-8")

        self.assertIn("curl wget", testnet_dockerfile)
        self.assertIn("--network\", \"testnet", testnet_dockerfile)
        self.assertIn("--port\", \"8785", testnet_dockerfile)
        self.assertIn("/data/main-computer/hub/testnet", testnet_dockerfile)
        self.assertIn("curl wget", mainnet_dockerfile)
        self.assertIn("--network\", \"mainnet", mainnet_dockerfile)
        self.assertIn("--port\", \"8790", mainnet_dockerfile)
        self.assertIn("/data/main-computer/hub/mainnet", mainnet_dockerfile)

        self.assertIn("FoundationDB.Client.Native", exp_fdb_dockerfile)
        self.assertIn("foundationdb==${FDB_PYTHON_VERSION}", exp_fdb_dockerfile)
        self.assertIn("libfdb_c.so", exp_fdb_dockerfile)
        self.assertIn('ENTRYPOINT ["python", "/app/exp-fdb-hub.py"]', exp_fdb_dockerfile)
        self.assertIn("--no-fdb-autostart", exp_fdb_dockerfile)
        self.assertIn("--no-activate-cached-native-client", exp_fdb_dockerfile)
        self.assertIn("/api/hub/status", exp_fdb_dockerfile)


    def test_json_rpc_uses_operator_headers_for_https_edges(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"jsonrpc":"2.0","id":1,"result":"0x28757b0"}'

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        original_urlopen = coolify_hub_service.urllib.request.urlopen
        coolify_hub_service.urllib.request.urlopen = fake_urlopen
        try:
            result = coolify_hub_service.json_rpc(
                "https://mainnet-rpc.greatlibrary.io",
                "eth_chainId",
                timeout_s=3.0,
                user_agent="UnitTestAgent/1.0",
            )
        finally:
            coolify_hub_service.urllib.request.urlopen = original_urlopen

        self.assertEqual(result, "0x28757b0")
        self.assertEqual(captured["timeout"], 3.0)
        request = captured["request"]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), "UnitTestAgent/1.0")


    def test_hub_status_request_uses_operator_headers_for_https_edges(self) -> None:
        request = coolify_hub_service.hub_status_request(
            "https://mainnet-hub.greatlibrary.io/api/hub/status",
            user_agent="UnitTestHubAgent/1.0",
        )

        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), "UnitTestHubAgent/1.0")

    def test_wait_for_hub_uses_operator_headers_for_public_status_check(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            hub_wait_timeout_s=1.0,
            hub_wait_poll_s=0.01,
            hub_status_timeout_s=2.5,
            hub_status_user_agent="UnitTestHubAgent/2.0",
        )
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"network":{"network_key":"mainnet","chain_id":42424240}}'

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        original_urlopen = coolify_hub_service.urllib.request.urlopen
        coolify_hub_service.urllib.request.urlopen = fake_urlopen
        try:
            result = coolify_hub_service.wait_for_hub(profile, args)
        finally:
            coolify_hub_service.urllib.request.urlopen = original_urlopen

        self.assertTrue(result["ok"])
        self.assertEqual(captured["timeout"], 2.5)
        request = captured["request"]
        self.assertEqual(request.full_url, "https://mainnet-hub.greatlibrary.io/api/hub/status")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), "UnitTestHubAgent/2.0")


    def test_hub_coolify_runbook_documents_regular_and_exp_fdb_deploys(self) -> None:
        runbook = REPO_ROOT / "pretty_docs" / "hub-coolify-deploy-runbook.md"

        text = runbook.read_text(encoding="utf-8")

        self.assertIn("coolify_hub_service.py plan mainnet", text)
        self.assertIn("coolify_hub_service.py apply mainnet", text)
        self.assertIn("--hub-implementation exp-fdb", text)
        self.assertIn("--replace-regular-hub", text)
        self.assertIn("--fdb-cluster-file /data/main-computer/fdb/fdb.cluster", text)
        self.assertIn("/Dockerfile.hub.exp-fdb", text)
        self.assertIn("main-computer-mainnet-exp-fdb-hub", text)
        self.assertIn("main-computer-mainnet-hub", text)



if __name__ == "__main__":
    unittest.main()
