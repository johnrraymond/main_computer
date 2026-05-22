from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools/local-prod/coolify-stack-target-twiddle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("coolify_stack_target_twiddle", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stack_target_twiddle_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_stack_target_twiddle_asks_start_code_for_generated_env() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "ApplicationsService" in text
    assert "_ensure_env_files" in text
    assert "main_computer.applications_service" in text


def test_stack_target_twiddle_targets_env_derived_container_names() -> None:
    module = load_module()
    env = {
        "COOLIFY_COMPOSE_PROJECT": "main-computer-applications",
        "COOLIFY_LOCAL_STATE": r"C:\repo\runtime\coolify-local-docker",
        "COOLIFY_SOURCE_ENV_FILE": r"C:\repo\runtime\applications_service\coolify\source\.env",
        "COOLIFY_CONTAINER_NAME": "mc-applications-coolify",
        "COOLIFY_POSTGRES_CONTAINER_NAME": "mc-applications-coolify-db",
        "COOLIFY_REDIS_CONTAINER_NAME": "mc-applications-coolify-redis",
        "COOLIFY_SOKETI_CONTAINER_NAME": "mc-applications-coolify-realtime",
        "COOLIFY_NETWORK_NAME": "main-computer-applications",
        "APP_PORT": "17056",
        "SOKETI_PORT": "17057",
        "SOKETI_TERMINAL_PORT": "17058",
    }

    target = module.expected_targets_from_env(env)

    assert target["compose_project"] == "main-computer-applications"
    assert target["app_port"] == "17056"
    assert target["containers"] == {
        "coolify": "mc-applications-coolify",
        "postgres": "mc-applications-coolify-db",
        "redis": "mc-applications-coolify-redis",
        "soketi": "mc-applications-coolify-realtime",
    }
    assert target["missing_container_env_keys"] == []


def test_stack_target_twiddle_static_compose_check_matches_repo_compose() -> None:
    module = load_module()

    report = module.build_compose_static_report(REPO_ROOT, REPO_ROOT / "docker-compose.applications.yml")

    assert report["ok"] is True
    assert report["missing"] == []


def test_stack_target_twiddle_destroy_requires_explicit_yes(monkeypatch, tmp_path: Path) -> None:
    module = load_module()

    monkeypatch.setattr(
        module,
        "derive_start_targets",
        lambda *args, **kwargs: {
            "target": {
                "container_names": ["mc-applications-coolify"],
                "local_state": str(tmp_path / "state"),
            }
        },
    )

    class Args:
        repo_root = str(tmp_path)
        docker_command = "docker"
        no_generate_env = True
        yes_destroy = False
        remove_state_dir = False
        allow_outside_root = False
        timeout = 1.0
        report = ""

    try:
        module.command_destroy(Args())
    except module.TwiddleError as exc:
        assert "--yes-destroy" in str(exc)
    else:
        raise AssertionError("destroy should require --yes-destroy")


def test_stack_target_twiddle_destroy_is_idempotent_when_containers_are_absent(monkeypatch, tmp_path: Path) -> None:
    module = load_module()

    monkeypatch.setattr(
        module,
        "derive_start_targets",
        lambda *args, **kwargs: {
            "target": {
                "compose_project": "main-computer-applications",
                "container_names": ["mc-applications-coolify", "mc-applications-coolify-db"],
                "containers": {
                    "coolify": "mc-applications-coolify",
                    "postgres": "mc-applications-coolify-db",
                },
                "local_state": str(tmp_path / "state"),
            }
        },
    )
    monkeypatch.setattr(
        module,
        "docker_container_presence",
        lambda name, **kwargs: {"ok": True, "name": name, "exists": False, "state": "already-absent"},
    )

    class Args:
        repo_root = str(tmp_path)
        docker_command = "docker"
        no_generate_env = True
        yes_destroy = True
        remove_state_dir = False
        allow_outside_root = False
        timeout = 1.0
        report = ""

    payload = module.command_destroy(Args())

    assert payload["ok"] is True
    assert payload["removed_containers"]["state"] == "already-absent"
    assert payload["summary"]["already_absent_target_containers"] == [
        "mc-applications-coolify",
        "mc-applications-coolify-db",
    ]


def test_stack_target_twiddle_remove_state_dir_allows_derived_tools_state_outside_repo(tmp_path: Path) -> None:
    module = load_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    state_dir = tmp_path / "home" / ".main-computer-tools" / "instances" / "main-computer-test" / "unleashed" / "coolify-local-docker"
    state_dir.mkdir(parents=True)
    (state_dir / "marker.txt").write_text("delete me", encoding="utf-8")

    result = module.remove_state_dir(repo_root, str(state_dir), allow_outside_root=False)

    assert result["ok"] is True
    assert result["state"] == "removed"
    assert result["derived_external_state"] is True
    assert not state_dir.exists()


def test_stack_target_twiddle_boot_once_uses_long_boot_timeout(monkeypatch, tmp_path: Path) -> None:
    module = load_module()
    seen: dict[str, float] = {}

    monkeypatch.setattr(
        module,
        "derive_start_targets",
        lambda *args, **kwargs: {
            "target": {
                "compose_project": "main-computer-applications",
                "container_names": ["mc-applications-coolify"],
                "containers": {"coolify": "mc-applications-coolify"},
                "local_state": str(tmp_path / "state"),
            }
        },
    )

    def fake_run_command(command, *, cwd, timeout=30.0):
        seen["timeout"] = timeout
        return {
            "ok": False,
            "returncode": 124,
            "command": command,
            "display": module.command_display(command),
            "stdout": "",
            "stderr": "timed out",
        }

    monkeypatch.setattr(module, "run_command", fake_run_command)

    class Args:
        repo_root = str(tmp_path)
        docker_command = "docker"
        no_generate_env = True
        timeout = 1.0
        boot_timeout = 123.0
        report = ""

    payload = module.command_boot_once(Args())

    assert payload["ok"] is False
    assert seen["timeout"] == 123.0
    assert payload["summary"]["boot_timeout_s"] == 123.0
    assert payload["summary"]["boot_failure"]["stderr_tail"] == "timed out"


def test_stack_target_twiddle_inspect_uses_container_inspect(monkeypatch, tmp_path: Path) -> None:
    module = load_module()
    seen: list[list[str]] = []

    def fake_run_command(command, *, cwd, timeout=30.0):
        seen.append(command)
        return {
            "ok": False,
            "returncode": 1,
            "command": command,
            "display": module.command_display(command),
            "stdout": "",
            "stderr": "Error: No such container: missing",
        }

    monkeypatch.setattr(module, "run_command", fake_run_command)

    result = module.docker_inspect_container(
        "missing",
        repo_root=tmp_path,
        docker_command="docker",
        timeout=1.0,
    )

    assert seen == [["docker", "container", "inspect", "--format", module.DOCKER_INSPECT_SUMMARY_FORMAT, "missing"]]
    assert result["exists"] is False
    assert result["state"] == "missing"


def test_stack_target_twiddle_parses_compact_inspect_summary() -> None:
    module = load_module()

    parsed = module.parse_docker_inspect_summary(
        "\n".join(
            [
                "ID=abcdef1234567890",
                "IMAGE=ghcr.io/coollabsio/coolify:latest",
                "STATUS=running",
                "RUNNING=true",
                "HEALTH=healthy",
                "NETWORKS=main-computer-applications,bridge,",
                "MOUNTS=bind|<no value>|C:/state|/data/coolify;;volume|coolify-db|/var/lib/docker/volumes/coolify-db/_data|/var/lib/postgresql/data;;",
            ]
        )
    )

    assert parsed["id"] == "abcdef123456"
    assert parsed["image"] == "ghcr.io/coollabsio/coolify:latest"
    assert parsed["status"] == "running"
    assert parsed["running"] is True
    assert parsed["health"] == "healthy"
    assert parsed["networks"] == ["bridge", "main-computer-applications"]
    assert parsed["mounts"][0]["Name"] == ""
    assert parsed["mounts"][0]["Source"] == "C:/state"
    assert parsed["mounts"][1]["Name"] == "coolify-db"


def test_stack_target_twiddle_current_inspect_template_avoids_optional_docker_fields() -> None:
    module = load_module()

    assert ".State.Health" not in module.DOCKER_INSPECT_SUMMARY_FORMAT
    assert ".Name" not in module.DOCKER_INSPECT_SUMMARY_FORMAT
    assert "{{.Source}}" in module.DOCKER_INSPECT_SUMMARY_FORMAT
    assert "{{.Destination}}" in module.DOCKER_INSPECT_SUMMARY_FORMAT


def test_stack_target_twiddle_parses_current_three_column_mount_summary() -> None:
    module = load_module()

    parsed = module.parse_docker_inspect_summary(
        "\n".join(
            [
                "ID=abcdef1234567890",
                "IMAGE=ghcr.io/coollabsio/coolify:latest",
                "STATUS=running",
                "RUNNING=true",
                "NETWORKS=main-computer-applications,",
                "MOUNTS=bind|C:/state|/data/coolify;;",
            ]
        )
    )

    assert parsed["health"] == ""
    assert parsed["mounts"] == [
        {
            "Type": "bind",
            "Name": "",
            "Source": "C:/state",
            "Destination": "/data/coolify",
        }
    ]


def test_stack_target_twiddle_verify_reports_inspect_failures_without_claiming_port_observed_container_missing(monkeypatch, tmp_path: Path) -> None:
    module = load_module()
    target = {
        "app_port": "17056",
        "network": "main-computer-applications",
        "container_names": ["mc-applications-coolify"],
        "containers": {"coolify": "mc-applications-coolify"},
    }
    planned_commands = {
        "compose_config": ["docker", "compose", "config", "--quiet"],
        "compose_ps": ["docker", "compose", "ps", "--format", "json"],
    }

    def fake_run_command(command, *, cwd, timeout=30.0):
        if command == ["docker", "version"]:
            return {"ok": True, "returncode": 0, "command": command, "display": module.command_display(command), "stdout": "ok", "stderr": ""}
        if command == planned_commands["compose_config"]:
            return {"ok": True, "returncode": 0, "command": command, "display": module.command_display(command), "stdout": "", "stderr": ""}
        if command == planned_commands["compose_ps"]:
            return {"ok": True, "returncode": 0, "command": command, "display": module.command_display(command), "stdout": "[]", "stderr": ""}
        if command == ["docker", "container", "inspect", "--format", module.DOCKER_INSPECT_SUMMARY_FORMAT, "mc-applications-coolify"]:
            return {
                "ok": False,
                "returncode": 1,
                "command": command,
                "display": module.command_display(command),
                "stdout": "",
                "stderr": "temporary inspect failure",
            }
        if command == ["docker", "port", "mc-applications-coolify"]:
            return {
                "ok": True,
                "returncode": 0,
                "command": command,
                "display": module.command_display(command),
                "stdout": "80/tcp -> 127.0.0.1:17056\n",
                "stderr": "",
            }
        raise AssertionError(f"unexpected command: {command!r}")

    monkeypatch.setattr(module, "run_command", fake_run_command)

    report = module.docker_live_report(
        tmp_path,
        target,
        planned_commands,
        docker_command="docker",
        timeout=1.0,
        check_health=False,
    )

    assert report["ok"] is False
    assert report["coolify_app_port_bound"] is True
    assert report["observed_containers"] == ["mc-applications-coolify"]
    assert report["missing_containers"] == []
    assert "mc-applications-coolify" in report["inspect_failures"]

    summary = module.build_summary({"target": target}, report)
    assert summary["observed_live_containers"] == ["mc-applications-coolify"]
    assert summary["missing_live_containers"] == []
    assert "mc-applications-coolify" in summary["inspect_failures"]


def test_stack_target_twiddle_verify_summary_names_failed_required_checks(monkeypatch, tmp_path: Path) -> None:
    module = load_module()

    start_chain = {"ok": True, "files": {}, "markers": {}}
    derived = {
        "ok": True,
        "env_file": str(tmp_path / "runtime" / "applications.env"),
        "compose_file": str(tmp_path / "docker-compose.applications.yml"),
        "target": {
            "compose_project": "main-computer-applications",
            "app_port": "17056",
            "network": "main-computer-applications",
            "container_names": ["mc-applications-coolify"],
            "containers": {"coolify": "mc-applications-coolify"},
            "missing_container_env_keys": [],
        },
        "missing_required_env_keys": [],
        "planned_commands": {
            "compose_config": ["docker", "compose", "config", "--quiet"],
            "compose_ps": ["docker", "compose", "ps", "--format", "json"],
        },
    }
    compose_static = {"ok": True, "compose_file": str(tmp_path / "docker-compose.applications.yml"), "missing": []}
    docker = {
        "ok": False,
        "docker_version": {"ok": True},
        "compose_config": {
            "ok": False,
            "returncode": 1,
            "command": ["docker", "compose", "config", "--quiet"],
            "display": "docker compose config --quiet",
            "stdout": "",
            "stderr": "bad compose",
        },
        "compose_ps": {"ok": True},
        "missing_containers": [],
        "inspect_failures": {},
        "non_running_containers": {},
        "coolify_app_port_bound": True,
        "health": {"ok": True, "status": 200},
    }

    checks = module.build_verify_checks(
        start_chain,
        derived,
        compose_static,
        docker,
        check_docker=True,
    )
    failed = module.failed_required_checks(checks)

    assert module.required_checks_ok(checks) is False
    assert [item["name"] for item in failed] == ["compose_config"]
    assert failed[0]["failure"]["stderr_tail"] == "bad compose"

    summary = module.build_summary(derived, docker)
    summary.update(
        {
            "failed_checks": module.failed_required_checks(checks),
            "check_status": {check["name"]: check["ok"] for check in checks},
            "docker_ok": docker["ok"],
        }
    )

    assert summary["compose_config_ok"] is False
    assert summary["compose_config_failure"]["stderr_tail"] == "bad compose"
    assert summary["failed_checks"][0]["name"] == "compose_config"


def test_stack_target_twiddle_skipped_health_is_not_required(tmp_path: Path) -> None:
    module = load_module()
    checks = module.build_verify_checks(
        {"ok": True, "files": {}, "markers": {}},
        {
            "ok": True,
            "env_file": str(tmp_path / "applications.env"),
            "compose_file": str(tmp_path / "docker-compose.applications.yml"),
            "target": {
                "app_port": "17056",
                "containers": {"coolify": "mc-applications-coolify"},
                "missing_container_env_keys": [],
            },
            "missing_required_env_keys": [],
        },
        {"ok": True, "compose_file": str(tmp_path / "docker-compose.applications.yml"), "missing": []},
        {
            "ok": True,
            "docker_version": {"ok": True},
            "compose_config": {"ok": True},
            "compose_ps": {"ok": False},
            "missing_containers": [],
            "inspect_failures": {},
            "non_running_containers": {},
            "coolify_app_port_bound": True,
            "health": {"ok": None, "state": "skipped"},
        },
        check_docker=True,
    )

    health_checks = [check for check in checks if check["name"] == "coolify_health"]
    compose_ps_checks = [check for check in checks if check["name"] == "compose_ps"]

    assert health_checks == [
        {
            "name": "coolify_health",
            "ok": True,
            "required": False,
            "details": {
                "state": "skipped",
                "url": None,
                "status": None,
                "error": None,
            },
        }
    ]
    assert compose_ps_checks[0]["required"] is False
    assert module.required_checks_ok(checks) is True


def test_stack_target_twiddle_ignores_null_inspect_failure_details() -> None:
    module = load_module()

    failures = module.meaningful_inspect_failures(
        {
            "coolify": {
                "name": "mc-applications-coolify",
                "exists": False,
                "state": "found",
                "inspect": {"ok": True, "stdout": "[]", "stderr": ""},
            },
            "postgres": {
                "name": "mc-applications-coolify-db",
                "exists": True,
                "state": "found",
                "inspect": {"ok": True, "stdout": "[]", "stderr": ""},
            },
        }
    )

    assert failures == {}


def test_stack_target_twiddle_keeps_actionable_inspect_failure_details() -> None:
    module = load_module()

    failures = module.meaningful_inspect_failures(
        {
            "coolify": {
                "name": "mc-applications-coolify",
                "exists": False,
                "state": "inspect-failed",
                "inspect": {
                    "ok": False,
                    "returncode": 1,
                    "command": ["docker", "container", "inspect", "mc-applications-coolify"],
                    "display": "docker container inspect mc-applications-coolify",
                    "stdout": "",
                    "stderr": "daemon unavailable",
                },
            }
        }
    )

    assert failures["mc-applications-coolify"]["state"] == "inspect-failed"
    assert failures["mc-applications-coolify"]["stderr_tail"] == "daemon unavailable"



def test_stack_target_twiddle_prefers_derived_state_token(tmp_path: Path) -> None:
    module = load_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    state_dir = tmp_path / "home" / ".main-computer-tools" / "instances" / "main-computer-test" / "unleashed" / "coolify-local-docker"
    state_dir.mkdir(parents=True)
    (state_dir / "api-token.txt").write_text("token=derived-token\n", encoding="utf-8")
    repo_token_dir = repo_root / "runtime" / "coolify-local-docker"
    repo_token_dir.mkdir(parents=True)
    (repo_token_dir / "api-token.txt").write_text("repo-token\n", encoding="utf-8")

    report = module.coolify_token_report(repo_root, {"local_state": str(state_dir)})

    assert report["ok"] is True
    assert report["selected_source"] == "derived_local_state"
    assert report["token_length"] == len("derived-token")
    assert report["_token"] == "derived-token"


def test_stack_target_twiddle_repo_token_fallback_is_not_clean_for_fresh_stack(tmp_path: Path) -> None:
    module = load_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_token_dir = repo_root / "runtime" / "coolify-local-docker"
    repo_token_dir.mkdir(parents=True)
    (repo_token_dir / "api-token.txt").write_text("repo-token\n", encoding="utf-8")

    report = module.coolify_token_report(
        repo_root,
        {"local_state": str(tmp_path / "missing" / "coolify-local-docker")},
    )

    assert report["ok"] is False
    assert report["selected_source"] == "repo_runtime_fallback"
    assert report["_token"] == "repo-token"


def test_stack_target_twiddle_summarizes_server_readiness() -> None:
    module = load_module()

    readiness = module.summarize_coolify_servers(
        [
            {
                "uuid": "server-1",
                "name": "bad",
                "ip": "127.0.0.1",
                "user": "root",
                "port": 22,
                "is_reachable": False,
                "is_usable": False,
            },
            {
                "uuid": "server-2",
                "name": "good",
                "ip": "127.0.0.1",
                "user": "root",
                "port": 22,
                "is_reachable": True,
                "is_usable": True,
            },
        ]
    )

    assert readiness["ok"] is True
    assert readiness["reachable_count"] == 1
    assert readiness["usable_count"] == 1
    assert readiness["servers"][1]["uuid"] == "server-2"


def test_stack_target_twiddle_api_checks_add_endpoint_and_server_checks(tmp_path: Path) -> None:
    module = load_module()
    start_chain = {"ok": True, "files": {}, "markers": {}}
    derived = {
        "ok": True,
        "env_file": str(tmp_path / "applications.env"),
        "compose_file": str(tmp_path / "docker-compose.applications.yml"),
        "target": {
            "app_port": "17056",
            "containers": {"coolify": "mc-applications-coolify"},
            "missing_container_env_keys": [],
        },
        "missing_required_env_keys": [],
    }
    compose_static = {"ok": True, "compose_file": str(tmp_path / "docker-compose.applications.yml"), "missing": []}
    docker = {
        "ok": True,
        "docker_version": {"ok": True},
        "compose_config": {"ok": True},
        "compose_ps": {"ok": True},
        "missing_containers": [],
        "inspect_failures": {},
        "non_running_containers": {},
        "coolify_app_port_bound": True,
        "health": {"ok": True, "status": 200},
    }
    api = {
        "ok": True,
        "token": {
            "ok": True,
            "selected_source": "derived_local_state",
            "selected_path": str(tmp_path / "state" / "api-token.txt"),
            "state_token_exists": True,
            "repo_token_exists": True,
            "token_length": 50,
        },
        "controller": {
            "ok": True,
            "base_url": "http://127.0.0.1:17056",
            "expected_base_url": "http://127.0.0.1:17056",
            "token_ref": "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN",
            "token_env_loaded": True,
        },
        "endpoints": {
            "/api/v1/projects": {"ok": True, "status": 200, "count": 1},
            "/api/v1/servers": {"ok": True, "status": 200, "count": 1},
            "/api/v1/services": {"ok": True, "status": 200, "count": 0},
            "/api/v1/deployments": {"ok": True, "status": 200, "count": 0},
        },
        "server_readiness": {
            "ok": True,
            "usable_count": 1,
            "reachable_count": 1,
            "servers": [{"uuid": "server-1", "is_usable": True, "is_reachable": True}],
        },
    }

    checks = module.build_verify_checks(
        start_chain,
        derived,
        compose_static,
        docker,
        api,
        check_docker=True,
        check_api=True,
        check_server_readiness=True,
    )

    assert module.required_checks_ok(checks) is True
    names = [check["name"] for check in checks]
    assert "coolify_token_source" in names
    assert "coolify_api_projects" in names
    assert "coolify_api_servers" in names
    assert "coolify_server_readiness" in names


def test_stack_target_twiddle_repair_server_readiness_runs_ensure_infra_with_derived_state(monkeypatch, tmp_path: Path) -> None:
    module = load_module()
    commands: list[list[str]] = []

    target = {
        "compose_project": "main-computer-coolify-main-computer-test-unleashed",
        "local_state": str(tmp_path / "coolify-local-docker"),
        "app_port": "17056",
        "soketi_port": "17057",
        "soketi_terminal_port": "17058",
        "containers": {"coolify": "mc-applications-coolify"},
        "container_names": ["mc-applications-coolify"],
    }

    monkeypatch.setattr(
        module,
        "derive_start_targets",
        lambda *args, **kwargs: {
            "ok": True,
            "target": target,
            "compose_file": str(tmp_path / "docker-compose.applications.yml"),
            "planned_commands": {},
        },
    )

    def fake_run_command(command, *, cwd, timeout=30.0):
        commands.append(command)
        assert cwd == tmp_path
        assert timeout == 420.0
        return {
            "ok": True,
            "returncode": 0,
            "command": command,
            "display": module.command_display(command),
            "stdout": "local Coolify localhost server target is ready",
            "stderr": "",
        }

    monkeypatch.setattr(module, "run_command", fake_run_command)
    monkeypatch.setattr(
        module,
        "coolify_api_report",
        lambda repo_root, derived_target, timeout=30.0: {
            "ok": True,
            "base_url": "http://127.0.0.1:17056",
            "token": {"ok": True, "selected_source": "derived_local_state"},
            "controller": {"ok": True, "base_url": "http://127.0.0.1:17056"},
            "endpoint_statuses": {},
            "server_readiness": {
                "ok": True,
                "usable_count": 1,
                "reachable_count": 1,
                "servers": [{"uuid": "server-1", "is_usable": True, "is_reachable": True}],
            },
        },
    )

    class Args:
        repo_root = str(tmp_path)
        docker_command = "docker"
        no_generate_env = True
        timeout = 5.0
        repair_timeout = 420.0
        report = ""

    payload = module.command_repair_server_readiness(Args())

    assert payload["ok"] is True
    assert payload["summary"]["repair_ok"] is True
    assert payload["summary"]["server_readiness_ok"] is True
    command = commands[0]
    assert "ensure-infra" in command
    assert "--project-name" in command
    assert "main-computer-coolify-main-computer-test-unleashed" in command
    assert "--state-dir" in command
    assert str(tmp_path / "coolify-local-docker") in command
    assert "--app-port" in command
    assert "17056" in command


def test_stack_target_twiddle_has_repair_server_readiness_subcommand() -> None:
    module = load_module()

    parser = module.build_parser()
    args = parser.parse_args(["repair-server-readiness"])

    assert args.func is module.command_repair_server_readiness
    assert args.repair_timeout == 420.0
