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

LAUNCHER_PATH = REPO_ROOT / "run-exp-fdb-hub.py"
launcher_spec = importlib.util.spec_from_file_location("run_exp_fdb_hub", LAUNCHER_PATH)
assert launcher_spec is not None and launcher_spec.loader is not None
run_exp_fdb_hub = importlib.util.module_from_spec(launcher_spec)
sys.modules[launcher_spec.name] = run_exp_fdb_hub
launcher_spec.loader.exec_module(run_exp_fdb_hub)


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
        "hub_implementation": coolify_hub_service.HUB_IMPLEMENTATION_EXP_FDB,
        "replace_regular_hub": False,
        "fdb_cluster_file": "",
        "fdb_namespace": "",
        "coolify_application_name": "",
        "coolify_application_uuid": "",
        "rpc_check": "auto",
        "rpc_user_agent": coolify_hub_service.DEFAULT_JSON_RPC_USER_AGENT,
        "skip_rpc_check": False,
        "hub_health_check": "auto",
        "no_wait_hub": False,
        "hub_status_user_agent": coolify_hub_service.DEFAULT_JSON_RPC_USER_AGENT,
        "network": "mainnet",
        "network_config": None,
        "local_coolify_token_file": "",
        "local_coolify_state_dir": "",
        "applications_service_env_file": "",
        "local_source_dir": "",
        "local_hub_runtime_host_dir": "",
        "hub_chain_rpc_url": "",
        "bridge_backend": "",
        "dev_chain_deployment_path": "",
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
            runtime_dir="/data/main-computer/hub/mainnet-exp-fdb",
        )

        self.assertEqual(payload["name"], "main-computer-mainnet-hub")
        self.assertEqual(payload["git_repository"], "https://github.com/example/main_computer.git")
        self.assertEqual(payload["git_branch"], "main")
        self.assertEqual(payload["build_pack"], "dockerfile")
        self.assertEqual(payload["dockerfile_location"], "/Dockerfile.hub.exp-fdb")
        self.assertEqual(payload["ports_exposes"], "8790")
        self.assertEqual(payload["domains"], "https://mainnet-hub.greatlibrary.io:8790")
        self.assertNotIn("urls", payload)
        self.assertEqual(
            payload["start_command"],
            "python /app/run-exp-fdb-hub.py --network mainnet --port 8790",
        )
        self.assertLessEqual(len(payload["start_command"]), 255)
        self.assertTrue(payload["health_check_enabled"])
        self.assertEqual(payload["health_check_path"], "/api/hub/status")



    def test_explicit_dockerfile_location_override_is_respected(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(dockerfile_location="/Dockerfile.custom")
        payload = coolify_hub_service.application_payload(
            profile,
            args,
            service_name="main-computer-testnet-hub",
            runtime_dir="/data/main-computer/hub/testnet",
        )

        self.assertEqual(payload["dockerfile_location"], "/Dockerfile.custom")

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
        payload = coolify_hub_service.storage_payload(profile, runtime_dir="/data/main-computer/hub/mainnet-exp-fdb")

        self.assertEqual(payload["type"], "persistent")
        self.assertEqual(payload["name"], "mainnet_exp_fdb_hub_state")
        self.assertEqual(payload["mount_path"], "/data/main-computer/hub/mainnet-exp-fdb")
        self.assertEqual(payload["host_path"], "/data/main-computer/hub/mainnet-exp-fdb")

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
        self.assertEqual(plan["runtime_dir"], "/data/main-computer/hub/testnet-exp-fdb")
        self.assertEqual(plan["volume_name"], "testnet_exp_fdb_hub_state")
        self.assertEqual(plan["chain_id"], 42424241)
        self.assertEqual(plan["public_url"], "https://testnet-hub.greatlibrary.io")
        self.assertEqual(plan["application_payload"]["dockerfile_location"], "/Dockerfile.hub.exp-fdb")
        self.assertNotIn("urls", plan["application_payload"])

    def test_apply_test_profile_targets_local_coolify_surface(self) -> None:
        args = _args(
            network="test",
            coolify_url="",
            coolify_project_uuid="",
            coolify_project_name="",
            coolify_environment_name="",
            coolify_server_uuid="",
            coolify_server_name="",
            hub_runtime_dir="",
            git_repo="",
        )

        profile = coolify_hub_service.load_profile(args)
        plan = coolify_hub_service.plan_result(profile, args)

        self.assertEqual(profile.network_key, "test")
        self.assertEqual(profile.kind, "test")
        self.assertEqual(profile.hub_bind_host, "0.0.0.0")
        self.assertEqual(args.coolify_url, "http://127.0.0.1:8000")
        self.assertEqual(args.coolify_project_name, "Main Computer Local Smoke")
        self.assertEqual(args.coolify_environment_name, "production")
        self.assertEqual(args.coolify_server_name, "localhost")
        self.assertEqual(plan["service_name"], "main-computer-test-hub")
        self.assertEqual(plan["runtime_dir"], "/srv/main-computer/hub/test-exp-fdb")
        self.assertEqual(plan["fdb_cluster_file"], "/srv/main-computer/hub/test-exp-fdb/fdb.cluster")
        self.assertEqual(plan["fdb_namespace"], "main-computer-test-exp-fdb")
        self.assertEqual(plan["chain_rpc_url"], "http://127.0.0.1:30010")
        self.assertEqual(plan["hub_chain_rpc_url"], "http://host.docker.internal:30010")
        payload = plan["application_payload"]
        self.assertNotIn("git_repository", payload)
        self.assertEqual(payload["ports_exposes"], "8780")
        self.assertEqual(payload["domains"], "http://127.0.0.1:8780")
        self.assertEqual(payload["start_command"], "python /app/run-exp-fdb-hub.py --network test --port 8780")
        self.assertLessEqual(len(payload["start_command"]), 255)
        self.assertEqual(plan["coolify_resource_kind"], "service")
        self.assertEqual(plan["service_payload"]["name"], "main-computer-test-hub")
        self.assertEqual(plan["service_payload"]["docker_compose_raw"], "<base64>")
        compose = plan["docker_compose"]
        self.assertIn("dockerfile: \"Dockerfile.hub.exp-fdb\"", compose)
        self.assertIn("context: \"./hub-src\"", compose)
        self.assertNotIn("https://github.com/example/main_computer.git#main", compose)
        self.assertIn("image: \"main-computer-test-hub:local\"", compose)
        self.assertIn("pull_policy: build", compose)
        self.assertIn("/srv/main-computer/hub/test-exp-fdb", compose)
        self.assertEqual(plan["local_build_context"]["compose_context"], "./hub-src")
        self.assertFalse(plan["local_build_context"]["commit_required"])
        self.assertIn("Dockerfile.hub.exp-fdb", plan["local_build_context"]["source_files"])
        self.assertIn("main_computer/", plan["local_build_context"]["source_dirs"])
        self.assertIn("\"127.0.0.1:8780:8780\"", compose)
        self.assertIn("host.docker.internal:host-gateway", compose)
        self.assertIn("main-computer-test-hub-fdb", compose)
        self.assertIn("foundationdb/foundationdb:7.4.6", compose)
        self.assertIn("FDB_NETWORKING_MODE: \"container\"", compose)
        self.assertIn("FDB_CLUSTER_FILE_CONTENTS: \"docker:docker@main-computer-test-hub-fdb:4550\"", compose)
        self.assertIn("fdbcli -C", compose)
        self.assertIn("configure new single memory", compose)
        self.assertIn("local_fdb", plan)
        self.assertEqual(plan["local_fdb"]["cluster_contents"], "docker:docker@main-computer-test-hub-fdb:4550")
        self.assertIn("No manual fdb.cluster seed file", plan["operator_note"])

        self.assertEqual(plan["bridge_backend"], "dev-chain")
        self.assertEqual(plan["dev_chain_deployment_path"], "/app/runtime/deployments/test/latest.json")
        self.assertIn("--bridge-backend", compose)
        self.assertIn("dev-chain", compose)
        self.assertIn("--dev-chain-deployment-path", compose)
        self.assertIn("/app/runtime/deployments/test/latest.json", compose)
        self.assertIn(":ro", compose)
        self.assertIn("/app/runtime/deployments", compose)

    def test_mock_bridge_backend_is_explicit_override_for_lab_runs(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("test")
        args = _args(network="test", bridge_backend="mock-chain", git_repo="")
        plan = coolify_hub_service.plan_result(profile, args)
        command = plan["hub_start_command"]

        self.assertEqual(plan["bridge_backend"], "mock-chain")
        self.assertEqual(plan["application_payload"]["start_command"], "python /app/run-exp-fdb-hub.py --network test --port 8780")
        self.assertIn("--bridge-backend mock-chain", command)
        self.assertNotIn("--dev-chain-deployment-path", command)
        self.assertIn("--bridge-backend", plan["docker_compose"])
        self.assertIn("mock-chain", plan["docker_compose"])

    def test_local_build_context_can_stage_custom_uncommitted_source_dir(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "Dockerfile.hub.exp-fdb").write_text("FROM scratch\n", encoding="utf-8")
            (source / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")
            (source / "requirements.txt").write_text("", encoding="utf-8")
            (source / "exp-fdb-hub.py").write_text("print('local edit')\n", encoding="utf-8")
            (source / "run-exp-fdb-hub.py").write_text("print('launcher')\n", encoding="utf-8")
            (source / "main_computer").mkdir()
            (source / "main_computer" / "__init__.py").write_text("", encoding="utf-8")

            args = _args(network="test", local_source_dir=str(source), git_repo="")
            source_root, files, dirs = coolify_hub_service.hub_build_context_sources(args)

            self.assertEqual(source_root, source)
            self.assertEqual([path.name for path in files], [
                "Dockerfile.hub.exp-fdb",
                "pyproject.toml",
                "requirements.txt",
                "exp-fdb-hub.py",
                "run-exp-fdb-hub.py",
            ])
            self.assertEqual([path.name for path in dirs], ["main_computer"])

    def test_remote_network_requires_git_repo_but_local_test_does_not(self) -> None:
        local_args = _args(network="test", git_repo="")
        local_profile = coolify_hub_service.load_profile(local_args)
        self.assertEqual(local_profile.network_key, "test")

        remote_args = _args(network="mainnet", git_repo="")
        with self.assertRaises(coolify_hub_service.CoolifyHubDeployError):
            coolify_hub_service.load_profile(remote_args)

    def test_local_test_token_file_is_used_when_operator_env_token_is_missing(self) -> None:
        with self.subTest("local token fallback"):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                token_file = Path(tmp) / "api-token.txt"
                token_file.write_text(
                    "# Main Computer local Coolify API token\n"
                    "dashboard=http://127.0.0.1:27066\n"
                    "token=123|local-token\n",
                    encoding="utf-8",
                )
                args = _args(
                    network="test",
                    coolify_url="",
                    coolify_token="",
                    coolify_token_env="MAIN_COMPUTER_TEST_MISSING_TOKEN_ENV",
                    coolify_token_file="",
                    local_coolify_token_file=str(token_file),
                )

                token, source = coolify_hub_service.resolve_token(args)

                self.assertEqual(token, "123|local-token")
                self.assertTrue(source.startswith("local-file:"))
                self.assertEqual(coolify_hub_service.local_coolify_url(args), "http://127.0.0.1:27066")

    def test_local_test_prefers_applications_service_local_coolify_state(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "install-scoped" / "coolify-local-docker"
            state_dir.mkdir(parents=True)
            (state_dir / "api-token.txt").write_text("token=derived-token\n", encoding="utf-8")
            app_env = tmp_path / "applications.env"
            app_env.write_text(
                f"COOLIFY_LOCAL_STATE={state_dir}\n"
                "APP_PORT=27066\n",
                encoding="utf-8",
            )
            repo_state = tmp_path / "repo-runtime" / "coolify-local-docker"
            repo_state.mkdir(parents=True)
            (repo_state / "api-token.txt").write_text("token=stale-repo-token\n", encoding="utf-8")

            args = _args(
                network="test",
                coolify_url="",
                coolify_token="",
                coolify_token_env="MAIN_COMPUTER_TEST_MISSING_TOKEN_ENV",
                coolify_token_file="",
                local_coolify_state_dir="",
                local_coolify_token_file="",
                applications_service_env_file=str(app_env),
            )

            token, source = coolify_hub_service.resolve_token(args)

            self.assertEqual(token, "derived-token")
            self.assertIn("install-scoped", source)
            self.assertEqual(coolify_hub_service.local_coolify_url(args), "http://127.0.0.1:27066")

    def test_exp_fdb_plan_uses_public_service_name_and_fdb_startup(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(hub_implementation=coolify_hub_service.HUB_IMPLEMENTATION_EXP_FDB)
        plan = coolify_hub_service.plan_result(profile, args)

        self.assertEqual(plan["hub_implementation"], "exp-fdb")
        self.assertEqual(plan["service_name"], "main-computer-mainnet-hub")
        self.assertEqual(plan["runtime_dir"], "/data/main-computer/hub/mainnet-exp-fdb")
        self.assertEqual(plan["volume_name"], "mainnet_exp_fdb_hub_state")
        self.assertEqual(plan["fdb_cluster_file"], "/data/main-computer/hub/mainnet-exp-fdb/fdb.cluster")
        self.assertEqual(plan["fdb_namespace"], "main-computer-mainnet-exp-fdb")
        payload = plan["application_payload"]
        self.assertEqual(payload["dockerfile_location"], "/Dockerfile.hub.exp-fdb")
        self.assertEqual(payload["ports_exposes"], "8790")
        self.assertEqual(payload["domains"], "https://mainnet-hub.greatlibrary.io:8790")
        self.assertEqual(payload["start_command"], "python /app/run-exp-fdb-hub.py --network mainnet --port 8790")
        self.assertLessEqual(len(payload["start_command"]), 255)
        command = plan["hub_start_command"]
        self.assertIn("--hub-root /data/main-computer/hub/mainnet-exp-fdb", command)
        self.assertIn("--cluster-file /data/main-computer/hub/mainnet-exp-fdb/fdb.cluster", command)
        self.assertIn("--namespace main-computer-mainnet-exp-fdb", command)
        self.assertIn("--network-key mainnet", command)
        self.assertIn("--chain-id 42424240", command)
        self.assertIn("--chain-rpc-url https://mainnet-rpc.greatlibrary.io", command)
        self.assertIn("--no-fdb-autostart", command)

    def test_replace_regular_hub_flag_is_deprecated_noop_for_exp_fdb(self) -> None:
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
        self.assertIn("--cluster-file /data/main-computer/fdb/fdb.cluster", plan["hub_start_command"])
        self.assertIn("--namespace main-computer-mainnet-cutover", plan["hub_start_command"])

    def test_regular_hub_implementation_is_deprecated(self) -> None:
        args = _args(hub_implementation=coolify_hub_service.HUB_IMPLEMENTATION_REGULAR)

        with self.assertRaises(coolify_hub_service.CoolifyHubDeployError) as ctx:
            coolify_hub_service.hub_implementation(args)

        self.assertIn("regular Hub implementation has been deprecated", str(ctx.exception))

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

    def test_local_test_apply_uses_services_endpoint_not_applications_public(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("test")
        args = _args(
            network="test",
            coolify_project_uuid="project-uuid",
            coolify_server_uuid="server-uuid",
            coolify_environment_name="production",
            coolify_environment_uuid="env-uuid",
            git_repo="https://github.com/example/main_computer.git",
        )
        profile = coolify_hub_service.coolify_deploy_profile(profile, args)
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services"): [
                    {"services": []}
                ],
                ("POST", "/api/v1/services"): [
                    {"uuid": "service-uuid", "name": "main-computer-test-hub"}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        service_uuid, action, existing = coolify_hub_service.sync_local_test_service(
            client,
            profile,
            args,
            service_name="main-computer-test-hub",
            runtime_dir="/srv/main-computer/hub/test-exp-fdb",
            tried=tried,
        )

        self.assertEqual(service_uuid, "service-uuid")
        self.assertEqual(action, "created")
        self.assertEqual(existing["source"], "missing")
        self.assertFalse(any("/api/v1/applications/public" in request[1] for request in client.requests))
        post = next(request for request in client.requests if request[0] == "POST")
        self.assertEqual(post[1], "/api/v1/services")
        payload = post[2]
        self.assertEqual(payload["server_uuid"], "server-uuid")
        self.assertEqual(payload["project_uuid"], "project-uuid")
        self.assertEqual(payload["environment_uuid"], "env-uuid")
        self.assertIn("docker_compose_raw", payload)
        self.assertNotIn("start_command", payload)


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


    def test_exp_fdb_dockerfile_has_safe_defaults_and_healthcheck_client(self) -> None:
        self.assertFalse((REPO_ROOT / "Dockerfile.hub").exists())
        self.assertFalse((REPO_ROOT / "Dockerfile.hub.mainnet").exists())
        self.assertFalse((REPO_ROOT / "Dockerfile.hub.testnet").exists())

        exp_fdb_dockerfile = (REPO_ROOT / "Dockerfile.hub.exp-fdb").read_text(encoding="utf-8")

        self.assertIn("curl wget", exp_fdb_dockerfile)
        self.assertIn("FoundationDB.Client.Native", exp_fdb_dockerfile)
        self.assertIn("foundationdb==${FDB_PYTHON_VERSION}", exp_fdb_dockerfile)
        self.assertIn("libfdb_c.so", exp_fdb_dockerfile)
        self.assertIn('CMD ["python", "/app/run-exp-fdb-hub.py"]', exp_fdb_dockerfile)
        self.assertNotIn('CMD ["python", "/app/exp-fdb-hub.py"', exp_fdb_dockerfile)
        self.assertNotIn('ENTRYPOINT ["python", "/app/exp-fdb-hub.py"]', exp_fdb_dockerfile)
        self.assertIn("EXPOSE 8790 8785", exp_fdb_dockerfile)
        self.assertNotIn("/data/main-computer/hub/mainnet-exp-fdb/fdb.cluster", exp_fdb_dockerfile)
        self.assertIn("${HUB_HEALTH_PORT:-${PORT:-8790}}", exp_fdb_dockerfile)
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
        self.assertEqual(request.full_url, "https://mainnet-hub.greatlibrary.io:8790/api/hub/status")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), "UnitTestHubAgent/2.0")


    def test_runtime_launcher_infers_testnet_from_coolify_port(self) -> None:
        args = run_exp_fdb_hub.parse_args([])
        command = run_exp_fdb_hub.build_exp_fdb_hub_command(args, environ={"PORT": "8785"})

        self.assertIn("--network-key", command)
        self.assertEqual(command[command.index("--network-key") + 1], "testnet")
        self.assertEqual(command[command.index("--port") + 1], "8785")
        self.assertEqual(command[command.index("--hub-root") + 1], "/data/main-computer/hub/testnet-exp-fdb")
        self.assertEqual(command[command.index("--cluster-file") + 1], "/data/main-computer/hub/testnet-exp-fdb/fdb.cluster")
        self.assertEqual(command[command.index("--namespace") + 1], "main-computer-testnet-exp-fdb")
        self.assertEqual(command[command.index("--dev-chain-deployment-path") + 1], "/app/runtime/deployments/testnet/latest.json")

    def test_runtime_launcher_cli_network_overrides_port_inference(self) -> None:
        args = run_exp_fdb_hub.parse_args(["--network", "mainnet"])
        command = run_exp_fdb_hub.build_exp_fdb_hub_command(args, environ={"PORT": "8785"})

        self.assertEqual(command[command.index("--network-key") + 1], "mainnet")
        self.assertEqual(command[command.index("--port") + 1], "8785")
        self.assertEqual(command[command.index("--hub-root") + 1], "/data/main-computer/hub/mainnet-exp-fdb")

    def test_hub_coolify_runbook_documents_exp_fdb_only_deploys(self) -> None:
        runbook = REPO_ROOT / "pretty_docs" / "hub-coolify-deploy-runbook.md"

        text = runbook.read_text(encoding="utf-8")

        self.assertIn("coolify_hub_service.py plan test", text)
        self.assertIn("coolify_hub_service.py apply test", text)
        self.assertIn("main-computer-test-hub", text)
        self.assertIn("runtime/coolify-local-docker/api-token.txt", text)
        self.assertIn("coolify_hub_service.py plan mainnet", text)
        self.assertIn("coolify_hub_service.py apply mainnet", text)
        self.assertIn("--hub-implementation exp-fdb", text)
        self.assertIn("--fdb-cluster-file /data/main-computer/fdb/fdb.cluster", text)
        self.assertIn("/Dockerfile.hub.exp-fdb", text)
        self.assertIn("main-computer-mainnet-hub", text)
        self.assertNotIn("Dockerfile.hub.mainnet", text)
        self.assertNotIn("Dockerfile.hub.testnet", text)



if __name__ == "__main__":
    unittest.main()
