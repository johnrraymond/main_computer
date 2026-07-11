from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
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
        "runtime_env_file": "",
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
        "private_state": None,
        "hub_id": "",
        "local_coolify_token_file": "",
        "local_coolify_state_dir": "",
        "applications_service_env_file": "",
        "local_source_dir": "",
        "local_hub_runtime_host_dir": "",
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

    def test_wait_for_hub_uses_public_url_without_backend_port(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(
            network="testnet",
            hub_wait_timeout_s=0.2,
            hub_wait_poll_s=0.0,
            hub_status_timeout_s=0.2,
        )
        captured: list[str] = []

        class FakeHubStatusResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "network": {
                            "network_key": "testnet",
                            "chain_id": 42424241,
                        }
                    }
                ).encode("utf-8")

        original_urlopen = coolify_hub_service.urllib.request.urlopen

        def fake_urlopen(request, timeout=0):
            del timeout
            captured.append(request.full_url)
            return FakeHubStatusResponse()

        coolify_hub_service.urllib.request.urlopen = fake_urlopen
        try:
            result = coolify_hub_service.wait_for_hub(profile, args)
        finally:
            coolify_hub_service.urllib.request.urlopen = original_urlopen

        self.assertTrue(result["ok"])
        self.assertEqual(
            captured,
            ["https://testnet-hub.greatlibrary.io/api/hub/status"],
        )
        self.assertEqual(
            coolify_hub_service.coolify_domain_with_backend_port(profile),
            "https://testnet-hub.greatlibrary.io:8785",
        )

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

    def test_testnet_exp_fdb_plan_uses_remote_service_with_fdb_sidecar(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(
            network="testnet",
            coolify_environment_name="testnet-hub",
            git_repo="https://github.com/johnrraymond/main_computer",
            fdb_cluster_file="/data/main-computer/hub/testnet-exp-fdb/fdb.cluster",
            fdb_namespace="main-computer-testnet-exp-fdb",
        )
        plan = coolify_hub_service.plan_result(profile, args)

        self.assertEqual(plan["coolify_resource_kind"], "service")
        self.assertEqual(plan["sidecar_fdb"]["service"], "main-computer-testnet-hub-fdb")
        self.assertEqual(plan["sidecar_fdb"]["cluster_contents"], "docker:docker@main-computer-testnet-hub-fdb:4550")
        self.assertEqual(plan["remote_build_context"]["compose_context"], "https://github.com/johnrraymond/main_computer.git#main")
        self.assertTrue(plan["remote_build_context"]["commit_required"])
        compose = plan["docker_compose"]
        self.assertIn("main-computer-testnet-hub-fdb", compose)
        self.assertIn("foundationdb/foundationdb:7.4.6", compose)
        self.assertIn("FDB_CLUSTER_FILE_CONTENTS: \"docker:docker@main-computer-testnet-hub-fdb:4550\"", compose)
        self.assertIn("printf", compose)
        self.assertIn("docker:docker@main-computer-testnet-hub-fdb:4550 > /data/main-computer/hub/testnet-exp-fdb/fdb.cluster", compose)
        self.assertIn("configure new single memory", compose)
        self.assertIn("context: \"https://github.com/johnrraymond/main_computer.git#main\"", compose)
        self.assertIn("MAIN_COMPUTER_HUB_NETWORK: \"testnet\"", compose)
        self.assertIn("traefik.http.services.main-computer-testnet-hub.loadbalancer.server.port=8785", compose)
        self.assertIn("No manual fdb.cluster seed file", plan["operator_note"])

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
        self.assertEqual(
            payload["start_command"],
            "python /app/run-exp-fdb-hub.py --network test --port 8780 --runtime-env-file /srv/main-computer/hub/test-exp-fdb/hub-runtime.env",
        )
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
        self.assertEqual(plan["runtime_env_file"], "/srv/main-computer/hub/test-exp-fdb/hub-runtime.env")
        self.assertIn("MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE: \"/srv/main-computer/hub/test-exp-fdb/hub-runtime.env\"", compose)
        self.assertIn("MAIN_COMPUTER_HUB_ROOT: \"/srv/main-computer/hub/test-exp-fdb\"", compose)
        self.assertIn("MAIN_COMPUTER_HUB_CHAIN_RPC_URL: \"http://host.docker.internal:30010\"", compose)
        self.assertIn("touch /srv/main-computer/hub/test-exp-fdb/hub-runtime.env", compose)
        self.assertIn("run-exp-fdb-hub.py --network test --port 8780 --runtime-env-file /srv/main-computer/hub/test-exp-fdb/hub-runtime.env", compose)
        self.assertIn("local_fdb", plan)
        self.assertEqual(plan["local_fdb"]["cluster_contents"], "docker:docker@main-computer-test-hub-fdb:4550")
        self.assertIn("No manual fdb.cluster seed file", plan["operator_note"])

        self.assertEqual(plan["bridge_backend"], "dev-chain")
        self.assertEqual(plan["dev_chain_deployment_path"], "/app/runtime/deployments/test/latest.json")
        self.assertIn("MAIN_COMPUTER_HUB_BRIDGE_BACKEND: \"dev-chain\"", compose)
        self.assertIn("run-exp-fdb-hub.py --network test --port 8780 --runtime-env-file", compose)
        self.assertIn("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH: \"/app/runtime/deployments/test/latest.json\"", compose)
        self.assertIn(":ro", compose)
        self.assertIn("/app/runtime/deployments", compose)

    def test_mock_bridge_backend_is_explicit_override_for_lab_runs(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("test")
        args = _args(network="test", bridge_backend="mock-chain", git_repo="")
        plan = coolify_hub_service.plan_result(profile, args)
        command = plan["hub_start_command"]

        self.assertEqual(plan["bridge_backend"], "mock-chain")
        self.assertEqual(
            plan["application_payload"]["start_command"],
            "python /app/run-exp-fdb-hub.py --network test --port 8780 --runtime-env-file /srv/main-computer/hub/test-exp-fdb/hub-runtime.env",
        )
        self.assertIn("--runtime-env-file /srv/main-computer/hub/test-exp-fdb/hub-runtime.env", command)
        self.assertIn("MAIN_COMPUTER_HUB_BRIDGE_BACKEND: \"mock-chain\"", plan["docker_compose"])
        self.assertNotIn("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", plan["docker_compose"])

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
        self.assertIn("--require-multisession-auth", command)

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

    def test_testnet_exp_fdb_sync_uses_services_endpoint_not_applications_public(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(
            network="testnet",
            coolify_project_uuid="project-uuid",
            coolify_server_uuid="server-uuid",
            coolify_environment_name="testnet-hub",
            coolify_environment_uuid="env-uuid",
            git_repo="https://github.com/johnrraymond/main_computer",
        )
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services"): [
                    {"services": []}
                ],
                ("POST", "/api/v1/services"): [
                    {"uuid": "service-uuid", "name": "main-computer-testnet-hub"}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        service_uuid, action, existing = coolify_hub_service.sync_fdb_sidecar_service(
            client,
            profile,
            args,
            service_name="main-computer-testnet-hub",
            runtime_dir="/data/main-computer/hub/testnet-exp-fdb",
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
        compose = coolify_hub_service.render_fdb_sidecar_hub_compose(
            profile,
            args,
            service_name="main-computer-testnet-hub",
            runtime_dir="/data/main-computer/hub/testnet-exp-fdb",
        )
        self.assertIn("MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER: \"true\"", compose)
        self.assertIn("MAIN_COMPUTER_HUB_CONTRACTS_PATH: \"/app/main_computer/config/testnet_contracts.json\"", compose)
        self.assertIn("--allow-missing-bridge-signer", compose)
        self.assertIn("--contracts-path /app/main_computer/config/testnet_contracts.json", compose)
        self.assertNotIn("--enable-smoke-bridge", compose)
        self.assertNotIn("MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE", compose)
        self.assertNotIn("--dev-chain-deployment-path /app/runtime/deployments/testnet/latest.json", compose)

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
        self.assertEqual(request.full_url, "https://mainnet-hub.greatlibrary.io/api/hub/status")
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
        self.assertEqual(command[command.index("--contracts-path") + 1], "/app/main_computer/config/testnet_contracts.json")
        self.assertIn("--require-multisession-auth", command)

    def test_runtime_launcher_can_enable_unsigned_contract_startup_from_env(self) -> None:
        args = run_exp_fdb_hub.parse_args([])
        command = run_exp_fdb_hub.build_exp_fdb_hub_command(
            args,
            environ={
                "PORT": "8785",
                "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER": "true",
            },
        )

        self.assertIn("--allow-missing-bridge-signer", command)
        self.assertEqual(command[command.index("--contracts-path") + 1], "/app/main_computer/config/testnet_contracts.json")
        self.assertNotIn("--enable-smoke-bridge", command)
        self.assertNotIn("--dev-chain-deployment-path", command)
        self.assertFalse(any(part.endswith("/runtime/deployments/testnet/latest.json") for part in command))

    def test_runtime_launcher_preserves_explicit_signer_manifest_when_unsigned_startup_is_enabled(self) -> None:
        args = run_exp_fdb_hub.parse_args(["--dev-chain-deployment-path", "/secrets/testnet-deployment.json"])
        command = run_exp_fdb_hub.build_exp_fdb_hub_command(
            args,
            environ={
                "PORT": "8785",
                "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER": "true",
            },
        )

        self.assertIn("--allow-missing-bridge-signer", command)
        self.assertEqual(command[command.index("--dev-chain-deployment-path") + 1], "/secrets/testnet-deployment.json")
        self.assertEqual(command[command.index("--contracts-path") + 1], "/app/main_computer/config/testnet_contracts.json")


    def test_runtime_launcher_can_enable_explicit_smoke_bridge_from_env(self) -> None:
        args = run_exp_fdb_hub.parse_args([])
        command = run_exp_fdb_hub.build_exp_fdb_hub_command(
            args,
            environ={
                "PORT": "8785",
                "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER": "true",
                "MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE": "true",
            },
        )

        self.assertIn("--allow-missing-bridge-signer", command)
        self.assertIn("--enable-smoke-bridge", command)

    def test_coolify_testnet_smoke_bridge_is_explicit_opt_in(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(enable_smoke_bridge=True)
        compose = coolify_hub_service.render_fdb_sidecar_hub_compose(
            profile,
            args,
            service_name="main-computer-testnet-hub",
            runtime_dir="/data/main-computer/hub/testnet-exp-fdb",
        )

        self.assertIn("MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE: \"true\"", compose)
        self.assertIn("--enable-smoke-bridge", compose)

    def test_coolify_testnet_bridge_writes_use_signer_bundle_env_not_smoke(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        args = _args(enable_bridge_writes=True)
        compose = coolify_hub_service.render_fdb_sidecar_hub_compose(
            profile,
            args,
            service_name="main-computer-testnet-hub",
            runtime_dir="/data/main-computer/hub/testnet-exp-fdb",
        )

        self.assertIn("MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES: \"true\"", compose)
        self.assertIn("MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64: ${MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64:?missing bridge signer bundle}", compose)
        self.assertIn("base64 -d > /data/main-computer/hub/testnet-exp-fdb/private/bridge-signer/bridge-signer-bundle.json.tmp", compose)
        self.assertIn("--dev-chain-deployment-path", compose)
        self.assertIn("/data/main-computer/hub/testnet-exp-fdb/private/bridge-signer/bridge-signer-bundle.json", compose)
        self.assertNotIn("--allow-missing-bridge-signer", compose)
        self.assertNotIn("--enable-smoke-bridge", compose)
        self.assertNotIn("MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE", compose)

    def test_build_bridge_signer_bundle_uses_hub_admin_only(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        temp_dir = self.enterContext(__import__("tempfile").TemporaryDirectory())
        base = Path(temp_dir)
        wallet_path = base / "hub-admin-wallet.json"
        wallet_path.write_text(
            json.dumps(
                {
                    "address": "0x1D23F92c6AcF4c47A26aB48Fd3F3075AD619Baf6",
                    "private_key": "0x" + "1" * 64,
                }
            ),
            encoding="utf-8",
        )
        manifest_path = base / "latest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "chain": {"chain_id": 42424241, "rpc_url": "http://rpc.internal:8545"},
                    "contracts": {
                        "hub_credit_bridge_escrow": {
                            "address": "0x2279B7A0a67DB372996a5FaB50D91eAA73d2eBe6",
                            "bridge_controller_address": "0x1D23F92c6AcF4c47A26aB48Fd3F3075AD619Baf6",
                            "chain_id": 42424241,
                        }
                    },
                    "hub_admin": {
                        "address": "0x1D23F92c6AcF4c47A26aB48Fd3F3075AD619Baf6",
                        "wallet_path": str(wallet_path),
                    },
                    "smoke_client": {
                        "address": "0x161891A95c99966416492baF3f31Ff2cff93ac4C",
                        "wallet_path": "should-not-be-read.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        args = _args(
            network="testnet",
            bridge_signer_source_manifest=str(manifest_path),
            hub_chain_rpc_url="https://testnet-rpc.greatlibrary.io",
        )

        bundle = coolify_hub_service.build_bridge_signer_bundle(profile, args)
        decoded = json.loads(__import__("base64").b64decode(bundle["bundle_b64"]).decode("utf-8"))

        self.assertEqual(decoded["schema"], "main-computer.bridge-signer.v1")
        self.assertIn("bridge_controller", decoded)
        self.assertEqual(decoded["bridge_controller"]["address"], "0x1D23F92c6AcF4c47A26aB48Fd3F3075AD619Baf6")
        self.assertNotIn("smoke_client", decoded)
        self.assertEqual(decoded["chain_rpc_url"], "https://testnet-rpc.greatlibrary.io")
        self.assertEqual(bundle["bridge_controller_address"], "0x1D23F92c6AcF4c47A26aB48Fd3F3075AD619Baf6")
        self.assertNotIn("private_key", json.dumps({k: v for k, v in bundle.items() if k != "bundle_b64"}))

    def test_build_bridge_signer_bundle_prefers_private_state_hub_key(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        base = Path(temp_dir)
        manifest_path = base / "latest.json"
        private_state_path = base / "main_computer.private.yaml"
        escrow = "0x8A791620dd6260079BF849Dc5567aDC3F2FdC318"
        shared_admin = "0x8B122051325fD185ec17Fd5dF39deBC1c250A021"
        manifest_path.write_text(
            json.dumps(
                {
                    "chain": {"chain_id": 42424241, "rpc_url": "http://stale-rpc.invalid"},
                    "contracts": {
                        "hub_credit_bridge_escrow": {
                            "address": escrow,
                            "bridge_controller_address": shared_admin,
                            "chain_id": 42424241,
                        }
                    },
                    "hub_admin": {
                        "address": "0x9d3B686Da68b3DC312AEC0f9dcD29A5955b65C69",
                        "private_key": "0x" + "9" * 64,
                    },
                }
            ),
            encoding="utf-8",
        )
        private_state_path.write_text(
            "\n".join(
                [
                    "networks:",
                    "  testnet:",
                    "    hubs:",
                    "      testnet-hub1:",
                    "        hub_admin_keys:",
                    "          address1:",
                    f"            address: '{shared_admin}'",
                    f"            private_key: '{'0x' + '8' * 64}'",
                    "            state: active",
                    "            deployed_to_hub: true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        args = _args(
            network="testnet",
            hub_id="testnet-hub1",
            private_state=private_state_path,
            bridge_signer_source_manifest=str(manifest_path),
            hub_chain_rpc_url="https://testnet-rpc.greatlibrary.io",
        )

        bundle = coolify_hub_service.build_bridge_signer_bundle(profile, args)
        decoded = json.loads(__import__("base64").b64decode(bundle["bundle_b64"]).decode("utf-8"))

        self.assertEqual(decoded["contracts"]["hub_credit_bridge_escrow"]["address"], escrow)
        self.assertEqual(decoded["bridge_controller"]["address"], shared_admin)
        self.assertEqual(decoded["bridge_controller"]["private_key"], "0x" + "8" * 64)
        self.assertIn("main_computer.private.yaml", decoded["source"]["wallet_path"])
        self.assertEqual(bundle["bridge_controller_address"], shared_admin)


    def test_sync_service_env_var_uses_service_env_endpoint_and_redacts_value(self) -> None:
        secret_value = "not-a-real-secret"
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services/service-uuid/envs"): [
                    {"envs": []}
                ],
                ("POST", "/api/v1/services/service-uuid/envs"): [
                    {"uuid": "env-uuid", "key": "MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64", "value": secret_value}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_service.sync_service_env_var(
            client,
            service_uuid="service-uuid",
            key="MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64",
            value=secret_value,
            tried=tried,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "created")
        self.assertIn(("GET", "/api/v1/services/service-uuid/envs", None), client.requests)
        post = next(request for request in client.requests if request[0] == "POST")
        self.assertEqual(post[1], "/api/v1/services/service-uuid/envs")
        self.assertEqual(post[2]["value"], secret_value)
        self.assertNotIn(secret_value, json.dumps(tried))
        self.assertIn("<redacted>", json.dumps(tried))

    def test_sync_service_env_var_patches_after_create_conflict(self) -> None:
        secret_value = "rotated-secret"
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/services/service-uuid/envs"): [
                    {"envs": []}
                ],
                ("POST", "/api/v1/services/service-uuid/envs"): [
                    {"_status": 409, "message": "Environment variable already exists. Use PATCH request to update it."}
                ],
                ("PATCH", "/api/v1/services/service-uuid/envs/MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64"): [
                    {"_status": 404, "message": "not found"}
                ],
                ("PATCH", "/api/v1/services/service-uuid/envs"): [
                    {"uuid": "env-uuid", "key": "MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64", "value": secret_value}
                ],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_service.sync_service_env_var(
            client,
            service_uuid="service-uuid",
            key="MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64",
            value=secret_value,
            tried=tried,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "updated")
        self.assertTrue(any(method == "POST" and path == "/api/v1/services/service-uuid/envs" for method, path, _ in client.requests))
        self.assertTrue(any(method == "PATCH" and path == "/api/v1/services/service-uuid/envs" for method, path, _ in client.requests))
        self.assertNotIn(secret_value, json.dumps(tried))
        self.assertIn("<redacted>", json.dumps(tried))

    def test_runtime_launcher_loads_dev_runtime_env_file_before_building_command(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "hub-runtime.env"
            env_path.write_text(
                "\n".join(
                    [
                        "MAIN_COMPUTER_HUB_NETWORK=dev",
                        "MAIN_COMPUTER_HUB_PORT=8879",
                        "MAIN_COMPUTER_HUB_ROOT=runtime/hub/dev-runtime-file",
                        "MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE=.foundationdb/dev-runtime.cluster",
                        "MAIN_COMPUTER_HUB_FDB_NAMESPACE=main-computer-dev-runtime-file",
                        "MAIN_COMPUTER_HUB_CHAIN_RPC_URL=http://127.0.0.1:18555",
                        "MAIN_COMPUTER_HUB_CHAIN_ID=42424242",
                        "MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER=true",
                    ]
                ),
                encoding="utf-8",
            )

            args = run_exp_fdb_hub.parse_args(["--runtime-env-file", str(env_path)])
            command = run_exp_fdb_hub.build_exp_fdb_hub_command(
                args,
                environ={"PORT": "8785", "MAIN_COMPUTER_HUB_NETWORK": "testnet"},
            )

        self.assertEqual(command[command.index("--network-key") + 1], "dev")
        self.assertEqual(command[command.index("--port") + 1], "8879")
        self.assertEqual(command[command.index("--hub-root") + 1], "runtime/hub/dev-runtime-file")
        self.assertEqual(command[command.index("--cluster-file") + 1], ".foundationdb/dev-runtime.cluster")
        self.assertEqual(command[command.index("--namespace") + 1], "main-computer-dev-runtime-file")
        self.assertEqual(command[command.index("--chain-rpc-url") + 1], "http://127.0.0.1:18555")
        self.assertIn("--allow-missing-bridge-signer", command)

    def test_test_runtime_env_file_drives_local_hub_launcher_but_not_hosted_defaults(self) -> None:
        local_profile = coolify_hub_service.load_hub_network_registry().get("test")
        local_args = _args(network="test", git_repo="")
        local_plan = coolify_hub_service.plan_result(local_profile, local_args)

        self.assertEqual(local_plan["runtime_env_file"], "/srv/main-computer/hub/test-exp-fdb/hub-runtime.env")
        self.assertIn("--runtime-env-file /srv/main-computer/hub/test-exp-fdb/hub-runtime.env", local_plan["hub_start_command"])
        self.assertIn("MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE", local_plan["docker_compose"])

        hosted_profile = coolify_hub_service.load_hub_network_registry().get("testnet")
        hosted_args = _args(network="testnet")
        hosted_plan = coolify_hub_service.plan_result(hosted_profile, hosted_args)

        self.assertEqual(hosted_plan["runtime_env_file"], "")
        self.assertNotIn("MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE", hosted_plan["docker_compose"])

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
        self.assertIn("--coolify-environment-name \"testnet-hub\"", text)
        self.assertIn("--coolify-server-uuid \"c11j1nrxs7m2q6of6jmbxoxm\"", text)
        self.assertIn("--git-repo https://github.com/johnrraymond/main_computer", text)
        self.assertIn("--fdb-cluster-file /data/main-computer/hub/testnet-exp-fdb/fdb.cluster", text)
        self.assertIn("--enable-bridge-writes", text)
        self.assertIn("--sync-bridge-signer", text)
        self.assertIn("/Dockerfile.hub.exp-fdb", text)
        self.assertIn("main-computer-mainnet-hub", text)
        self.assertNotIn("Dockerfile.hub.mainnet", text)
        self.assertNotIn("Dockerfile.hub.testnet", text)



    def test_verify_action_is_read_only_and_does_not_require_git_repo(self) -> None:
        args = coolify_hub_service.parse_args(
            [
                "verify",
                "testnet",
                "--verify-chain-rpc-url",
                "https://rpc.example",
                "--rpc-check",
                "skip",
                "--hub-health-check",
                "skip",
            ]
        )

        result = coolify_hub_service.verify(args)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "verify")
        self.assertEqual(result["network"], "testnet")
        self.assertEqual(result["chain_rpc_url"], "https://rpc.example")
        self.assertEqual(result["phases"][0]["phase"], "verify-rpc")
        self.assertEqual(result["phases"][0]["result"]["skipped"], True)
        self.assertEqual(result["phases"][1]["phase"], "wait-hub")
        self.assertEqual(result["phases"][1]["result"]["skipped"], True)


    def test_explicit_contracts_path_allows_mainnet_status_only_startup_without_signer(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            bridge_backend="credit-bridge-contract",
            contracts_path="main_computer/config/mainnet_contracts.json",
        )

        self.assertTrue(coolify_hub_service.hub_allow_missing_bridge_signer(profile, args))

    def test_bridge_writes_keep_explicit_contracts_path_signer_required(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            bridge_backend="credit-bridge-contract",
            contracts_path="main_computer/config/mainnet_contracts.json",
            enable_bridge_writes=True,
        )

        self.assertFalse(coolify_hub_service.hub_allow_missing_bridge_signer(profile, args))


    def test_bridge_signer_defaults_infer_writes_from_hub_admin_manifest(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "latest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "hub_admin": {
                            "address": "0x" + "1" * 40,
                            "private_key": "0x" + "2" * 64,
                        },
                        "contracts": {
                            "hub_credit_bridge_escrow": {
                                "address": "0x" + "3" * 40,
                                "bridge_controller_address": "0x" + "1" * 40,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = _args(
                bridge_backend="credit-bridge-contract",
                contracts_path="main_computer/config/mainnet_contracts.json",
                bridge_signer_source_manifest=str(manifest),
                hub_chain_rpc_url="https://mainnet-rpc.greatlibrary.io",
            )

            coolify_hub_service.apply_bridge_signer_defaults(profile, args)

            self.assertTrue(args.enable_bridge_writes)
            self.assertTrue(args.sync_bridge_signer)
            self.assertFalse(coolify_hub_service.hub_allow_missing_bridge_signer(profile, args))

    def test_bridge_signer_bundle_accepts_inline_hub_admin_private_key(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "latest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "hub_admin": {
                            "address": "0x" + "1" * 40,
                            "private_key": "0x" + "2" * 64,
                        },
                        "contracts": {
                            "hub_credit_bridge_escrow": {
                                "address": "0x" + "3" * 40,
                                "bridge_controller_address": "0x" + "1" * 40,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = _args(
                bridge_backend="credit-bridge-contract",
                bridge_signer_source_manifest=str(manifest),
                hub_chain_rpc_url="https://mainnet-rpc.greatlibrary.io",
            )

            bundle = coolify_hub_service.build_bridge_signer_bundle(profile, args)

            self.assertTrue(bundle["ok"])
            self.assertEqual(bundle["bridge_controller_address"], "0x" + "1" * 40)
            self.assertEqual(bundle["escrow_address"], "0x" + "3" * 40)
            self.assertEqual(bundle["wallet_path"], "hub_admin.private_key")

    def test_sync_hub_runtime_application_envs_sets_launcher_defaults_for_signer_mode(self) -> None:
        profile = coolify_hub_service.load_hub_network_registry().get("mainnet")
        args = _args(
            bridge_backend="credit-bridge-contract",
            contracts_path="main_computer/config/mainnet_contracts.json",
            enable_bridge_writes=True,
            hub_chain_rpc_url="https://mainnet-rpc.greatlibrary.io",
        )
        expected = coolify_hub_service.hub_runtime_env_defaults(
            profile,
            args,
            runtime_dir="/data/main-computer/hub/mainnet-exp-fdb",
        )
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/applications/app-uuid/envs"): [{"envs": []} for _ in expected],
                ("POST", "/api/v1/applications/app-uuid/envs"): [{"uuid": f"env-{index}"} for index, _key in enumerate(expected)],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_service.sync_hub_runtime_application_envs(
            client,
            profile,
            args,
            application_uuid="app-uuid",
            runtime_dir="/data/main-computer/hub/mainnet-exp-fdb",
            tried=tried,
        )

        self.assertTrue(result["ok"])
        self.assertIn("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", result["keys"])
        self.assertIn("MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES", result["keys"])
        self.assertIn("MAIN_COMPUTER_HUB_CHAIN_RPC_URL", result["keys"])
        post_payloads = [request[2] for request in client.requests if request[0] == "POST"]
        self.assertEqual(set(post_payloads[0]), {"key", "value"})
        self.assertNotIn("https://mainnet-rpc.greatlibrary.io", json.dumps(tried))


    def test_sync_application_env_var_creates_runtime_secret_without_leaking_value(self) -> None:
        client = RouteCoolifyClient(
            {
                ("GET", "/api/v1/applications/app-uuid/envs"): [{"envs": []}],
                ("POST", "/api/v1/applications/app-uuid/envs"): [{"uuid": "env-uuid"}],
            }
        )
        tried: list[dict[str, object]] = []

        result = coolify_hub_service.sync_application_env_var(
            client,
            application_uuid="app-uuid",
            key="MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64",
            value="secret-value",
            tried=tried,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "created")
        self.assertNotIn("secret-value", json.dumps(tried))



if __name__ == "__main__":
    unittest.main()
