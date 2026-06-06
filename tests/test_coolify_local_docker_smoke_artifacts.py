from __future__ import annotations

import io
import sys
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wsl_coolify_smoke_files_were_removed() -> None:
    removed = [
        "tools/local-prod/README-coolify-wsl.md",
        "tools/local-prod/coolify-wsl-control.ps1",
        "tools/local-prod/wsl-preflight-coolify.sh",
        "tools/local-prod/wsl-install-coolify.sh",
        "tools/local-prod/wsl-status-coolify.sh",
        "tests/test_coolify_wsl_smoke_artifacts.py",
    ]
    for relative in removed:
        assert not (REPO_ROOT / relative).exists(), relative


def test_local_docker_smoke_artifacts_exist() -> None:
    expected = [
        "tools/local-prod/README-coolify-local-docker.md",
        "tools/local-prod/coolify-local-docker.py",
        "tools/local-prod/coolify-local-docker-control.ps1",
        "deploy/coolify/local-docker/docker-compose.yml",
        "deploy/coolify/local-docker/smoke-nginx.compose.yml",
    ]
    for relative in expected:
        assert (REPO_ROOT / relative).is_file(), relative


def test_local_docker_compose_avoids_wsl_and_linux_installer_assumptions() -> None:
    compose = (REPO_ROOT / "deploy/coolify/local-docker/docker-compose.yml").read_text(encoding="utf-8")
    assert "ghcr.io}/coollabsio/coolify" in compose
    assert "coolify-realtime" in compose
    assert "quay.io/soketi/soketi:${LATEST_REALTIME_VERSION:-1.4-16-debian}" in compose
    assert "1.0.13" not in compose
    assert "COOLIFY_LOCAL_STATE" in compose
    assert "/var/run/docker.sock" in compose
    assert "/data/coolify" not in compose
    assert "coolify-db" in compose
    assert "coolify-redis" in compose
    assert "REDIS_PASSWORD: ${REDIS_PASSWORD:?REDIS_PASSWORD is required}" in compose
    assert 'redis-cli -a "$$REDIS_PASSWORD" ping | grep PONG' in compose
    assert "systemctl" not in compose
    assert "sudo" not in compose
    assert "wsl" not in compose.lower()


def test_local_docker_control_script_does_not_shell_out_to_wsl() -> None:
    control = (REPO_ROOT / "tools/local-prod/coolify-local-docker-control.ps1").read_text(encoding="utf-8")
    assert "coolify-local-docker.py" in control
    assert "wsl" not in control.lower()
    assert "systemctl" not in control.lower()
    assert "sudo" not in control.lower()


def test_local_docker_python_script_can_render_state_without_docker(tmp_path: Path) -> None:
    script_path = REPO_ROOT / "tools/local-prod/coolify-local-docker.py"
    spec = importlib.util.spec_from_file_location("coolify_local_docker", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env_text = module.render_env(tmp_path, root_password="Local-test-password-1!")
    assert "COOLIFY_LOCAL_STATE=" in env_text
    assert "APP_KEY=base64:" in env_text
    assert "ROOT_USERNAME=maincomputer" in env_text
    assert "ROOT_USER_EMAIL=maincomputer.local@example.com" in env_text
    assert "ROOT_USER_PASSWORD=Local-test-password-1!" in env_text
    assert "LATEST_REALTIME_VERSION=1.4-16-debian" in env_text
    assert "DB_HOST=postgres" in env_text
    assert "DB_PORT=5432" in env_text
    assert "REDIS_HOST=redis" in env_text
    assert "REDIS_PORT=6379" in env_text


def load_local_docker_module():
    script_path = REPO_ROOT / "tools/local-prod/coolify-local-docker.py"
    spec = importlib.util.spec_from_file_location("coolify_local_docker", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_docker_compose_up_retries_stale_name_conflict_without_deleting_volumes(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    conflict_output = """
Container mc-applications-coolify Recreate
Container 5b358f655c61_mc-applications-coolify Error response from daemon: Error when allocating new name: Conflict. The container name "/mc-applications-coolify" is already in use by container "5b358f655c615f6f15094afb2566cf32b67f0e870d9830aa5ab671f0f0d4623b". You have to remove (or rename) that container to be able to reuse that name.
"""
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool = True, capture: bool = False, input_text=None, timeout_seconds=None):
        calls.append(command)
        if command[:2] == ["docker", "rm"]:
            return module.subprocess.CompletedProcess(command, 0, stdout="5b358f655c61\n", stderr="")
        if "down" in command:
            assert "-v" not in command
            return module.subprocess.CompletedProcess(command, 0, stdout="down ok\n", stderr="")
        if len([call for call in calls if "up" in call]) == 1:
            return module.subprocess.CompletedProcess(command, 1, stdout=conflict_output, stderr="")
        return module.subprocess.CompletedProcess(command, 0, stdout="up ok\n", stderr="")

    monkeypatch.setattr(module, "run", fake_run)

    module.docker_compose_up_with_stale_container_retry(tmp_path, ["up", "-d", "--build", "--force-recreate"])

    assert calls[0][-5:] == ["up", "-d", "--build", "--force-recreate", "--remove-orphans"]
    assert "down" in calls[1]
    assert "--remove-orphans" in calls[1]
    assert "-v" not in calls[1]
    assert calls[2] == [
        "docker",
        "rm",
        "-f",
        "5b358f655c615f6f15094afb2566cf32b67f0e870d9830aa5ab671f0f0d4623b",
        "mc-applications-coolify",
    ]
    assert calls[3][-5:] == ["up", "-d", "--build", "--force-recreate", "--remove-orphans"]


def test_local_docker_compose_up_still_retries_stale_missing_container(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool = True, capture: bool = False, input_text=None, timeout_seconds=None):
        calls.append(command)
        if "down" in command:
            return module.subprocess.CompletedProcess(command, 0, stdout="down ok\n", stderr="")
        if len([call for call in calls if "up" in call]) == 1:
            return module.subprocess.CompletedProcess(command, 1, stdout="No such container: stale-id\n", stderr="")
        return module.subprocess.CompletedProcess(command, 0, stdout="up ok\n", stderr="")

    monkeypatch.setattr(module, "run", fake_run)

    module.docker_compose_up_with_stale_container_retry(tmp_path, ["up", "-d", "--build"])

    assert "down" in calls[1]
    assert "--remove-orphans" in calls[1]
    assert all(call[:3] != ["docker", "rm", "-f"] for call in calls)
    assert calls[-1][-4:] == ["up", "-d", "--build", "--remove-orphans"]



def test_local_docker_print_check_tolerates_cp1252_stdout(monkeypatch) -> None:
    module = load_local_docker_module()
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")

    monkeypatch.setattr(sys, "stdout", stream)

    module.configure_console_output()
    module.print_check("Unicode detail survives", True, "Coolify marker ❌")

    stream.flush()
    rendered = buffer.getvalue().decode("cp1252")
    assert "[PASS] Unicode detail survives" in rendered
    assert "Coolify marker \\u274c" in rendered


def test_local_docker_python_script_repairs_existing_generated_env(tmp_path: Path) -> None:
    module = load_local_docker_module()

    env_text = "\n".join(
        [
            "APP_PORT=18000",
            "DB_USERNAME=coolify",
            "DB_DATABASE=coolify",
            "DB_PASSWORD=keep-this-db-secret",
            "REDIS_PASSWORD=keep-this-redis-secret",
            "LATEST_REALTIME_VERSION=1.0.13",
            "ROOT_USERNAME=maincomputer",
            "ROOT_USER_EMAIL=maincomputer.local@example.invalid",
            "ROOT_USER_PASSWORD=NoSymbol1",
            "",
        ]
    )

    repaired, changed = module.upsert_env_values(env_text)
    values = module.parse_env_values(repaired)

    assert changed == [
        "DB_HOST",
        "DB_PORT",
        "LATEST_REALTIME_VERSION",
        "MUX_ENABLED",
        "REDIS_HOST",
        "REDIS_PORT",
        "ROOT_USER_EMAIL",
        "ROOT_USER_PASSWORD",
        "SSH_MUX_ENABLED",
    ]
    assert "DB_PASSWORD=keep-this-db-secret" in repaired
    assert "REDIS_PASSWORD=keep-this-redis-secret" in repaired
    assert values["LATEST_REALTIME_VERSION"] == "1.4-16-debian"
    assert values["ROOT_USERNAME"] == "maincomputer"
    assert values["ROOT_USER_EMAIL"] == "maincomputer.local@example.com"
    assert values["ROOT_USER_PASSWORD"] != "NoSymbol1"
    assert module.valid_root_password(values["ROOT_USER_PASSWORD"])
    assert "DB_HOST=postgres" in repaired
    assert "DB_PORT=5432" in repaired
    assert "REDIS_HOST=redis" in repaired
    assert "REDIS_PORT=6379" in repaired


def test_local_docker_python_script_preserves_valid_existing_root_credentials(tmp_path: Path) -> None:
    module = load_local_docker_module()

    env_text = "\n".join(
        [
            "APP_PORT=18000",
            "DB_HOST=postgres",
            "DB_PORT=5432",
            "REDIS_HOST=redis",
            "REDIS_PORT=6379",
            "LATEST_REALTIME_VERSION=1.4-16-debian",
            "ROOT_USERNAME=maincomputer",
            "ROOT_USER_EMAIL=owner@example.com",
            "ROOT_USER_PASSWORD=Keep-This-Root-Secret1!",
            "",
        ]
    )

    repaired, changed = module.upsert_env_values(env_text)

    assert changed == ["MUX_ENABLED", "SSH_MUX_ENABLED"]
    assert "ROOT_USER_EMAIL=owner@example.com" in repaired
    assert "ROOT_USER_PASSWORD=Keep-This-Root-Secret1!" in repaired


def test_local_docker_python_script_generates_complex_root_password() -> None:
    module = load_local_docker_module()

    password = module.random_password()

    assert module.valid_root_password(password)
    assert any(ch in module.PASSWORD_SYMBOLS for ch in password)


def test_local_docker_python_script_detects_boot_user_setup_page() -> None:
    module = load_local_docker_module()

    assert module.is_boot_user_setup_page(
        "Create your account\nBoot User Setup\nThis user will be the root user with full admin access.",
        "http://127.0.0.1:18000",
    )
    assert not module.is_boot_user_setup_page(
        "<html><body>Login</body></html>",
        "http://127.0.0.1:18000/login",
    )


def test_local_docker_python_script_extracts_boot_user_register_form() -> None:
    module = load_local_docker_module()

    body = """
    <form action="/register" method="POST" class="flex flex-col gap-4">
      <input type="hidden" name="_token" value="test-csrf-token" autocomplete="off">
      <input name="name" type="text">
      <input name="email" type="email">
      <input name="password" type="password">
      <input name="password_confirmation" type="password">
      <button type="submit">Create Account</button>
    </form>
    """

    assert module.csrf_token_from_html(body) == "test-csrf-token"
    assert module.registration_action_from_html(body) == "/register"


def test_local_docker_python_script_builds_register_payload_from_env(tmp_path: Path) -> None:
    module = load_local_docker_module()

    state_source = module.local_state_dir(tmp_path) / "source"
    state_source.mkdir(parents=True)
    (state_source / ".env").write_text(
        "\n".join(
            [
                "ROOT_USERNAME=maincomputer",
                "ROOT_USER_EMAIL=owner@example.com",
                "ROOT_USER_PASSWORD=Valid-Root-Secret1!",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = module.root_registration_payload(tmp_path)

    assert payload == {
        "name": "maincomputer",
        "email": "owner@example.com",
        "password": "Valid-Root-Secret1!",
        "password_confirmation": "Valid-Root-Secret1!",
    }


def test_local_docker_python_script_accepts_bootstrap_action() -> None:
    module = load_local_docker_module()

    args = module.parse_args(["bootstrap"])

    assert args.action == "bootstrap"


def test_local_docker_python_script_detects_onboarding_and_login_pages() -> None:
    module = load_local_docker_module()

    assert module.is_onboarding_page(
        "Welcome to Coolify\nConnect your first server and start deploying in minutes\nSkip Setup",
        "http://127.0.0.1:18000/onboarding",
    )
    assert not module.is_onboarding_page("<html><body>Dashboard</body></html>", "http://127.0.0.1:18000")

    assert module.is_login_page(
        '<form action="/login" method="POST"><input name="email"><input name="password"></form>',
        "http://127.0.0.1:18000/login",
    )
    assert not module.is_login_page("<html><body>Welcome to Coolify</body></html>", "http://127.0.0.1:18000/onboarding")


def test_local_docker_python_script_builds_login_payload_from_env(tmp_path: Path) -> None:
    module = load_local_docker_module()

    state_source = module.local_state_dir(tmp_path) / "source"
    state_source.mkdir(parents=True)
    (state_source / ".env").write_text(
        "\n".join(
            [
                "ROOT_USERNAME=maincomputer",
                "ROOT_USER_EMAIL=owner@example.com",
                "ROOT_USER_PASSWORD=Valid-Root-Secret1!",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = module.root_login_payload(tmp_path)

    assert payload == {
        "email": "owner@example.com",
        "password": "Valid-Root-Secret1!",
    }


def test_local_docker_python_script_has_local_only_onboarding_and_api_helpers() -> None:
    module = load_local_docker_module()

    assert module.API_TOKEN_NAME == "main-computer-local-smoke"
    assert module.API_TOKEN_ABILITIES == ["*"]
    assert module.api_url(Path("/repo"), "/v1/projects").endswith("/api/v1/projects")
    assert module.api_url(Path("/repo"), "/health").endswith("/api/health")


def test_local_docker_python_script_accepts_next_phase_actions() -> None:
    module = load_local_docker_module()

    for action in ["onboard", "auth-smoke", "api-smoke", "ensure-infra", "deploy-smoke"]:
        args = module.parse_args([action])
        assert args.action == action


def test_local_docker_control_script_accepts_next_phase_actions() -> None:
    control = (REPO_ROOT / "tools/local-prod/coolify-local-docker-control.ps1").read_text(encoding="utf-8")

    assert "bootstrap" in control
    assert "migrate" in control
    assert "onboard" in control
    assert "auth-smoke" in control
    assert "api-smoke" in control
    assert "ensure-infra" in control
    assert "deploy-smoke" in control


def test_local_docker_python_script_has_schema_migration_smoke_contract() -> None:
    module = load_local_docker_module()

    assert ("users", "currentTeam") in module.REQUIRED_SCHEMA_COLUMNS
    assert "users.currentTeam" in module.LOCAL_SCHEMA_COMPATIBILITY_REPAIRS
    assert 'ADD COLUMN IF NOT EXISTS "currentTeam" jsonb' in module.LOCAL_SCHEMA_COMPATIBILITY_REPAIRS["users.currentTeam"]
    assert ("teams", "show_boarding") in module.REQUIRED_SCHEMA_COLUMNS
    assert ("instance_settings", "is_api_enabled") in module.REQUIRED_SCHEMA_COLUMNS
    assert ("personal_access_tokens", "team_id") in module.REQUIRED_SCHEMA_COLUMNS

    args = module.parse_args(["migrate"])
    assert args.action == "migrate"


def test_local_docker_enable_api_repairs_missing_instance_settings(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    sql_calls: list[str] = []
    php_calls: list[str] = []
    artisan_calls: list[list[str]] = []

    def fake_psql(root: Path, sql: str):
        sql_calls.append(sql)
        if len(sql_calls) == 1:
            return True, "missing"
        return True, "true"

    def fake_php(root: Path, code: str, *, timeout_seconds: int = 180):
        php_calls.append(code)
        return True, "settings repaired"

    def fake_artisan(root: Path, args: list[str], *, timeout_seconds: int = 180):
        artisan_calls.append(args)
        return True, "caches cleared"

    monkeypatch.setattr(module, "psql", fake_psql)
    monkeypatch.setattr(module, "coolify_php", fake_php)
    monkeypatch.setattr(module, "coolify_artisan", fake_artisan)

    ok, detail = module.enable_api_in_db(tmp_path)

    assert ok
    assert len(sql_calls) == 2
    assert len(php_calls) == 1
    assert artisan_calls == [["optimize:clear"]]
    assert "instance_settings" in php_calls[0]
    assert "is_api_enabled" in php_calls[0]
    assert "where('id', 0)" in php_calls[0]
    assert "WHERE id = 0" in sql_calls[0]
    assert "settings repaired" in detail
    assert "local Coolify API is enabled" in detail
    assert "cleared Coolify runtime caches" in detail


def test_local_docker_ensure_api_token_retries_after_dashboard_404(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    def fake_enable(root: Path):
        calls.append("enable")
        return True, "api enabled"

    def fake_read(root: Path):
        calls.append("read-token")
        return "token-value"

    def fake_usable(root: Path, token: str):
        calls.append(f"probe:{token}")
        if calls.count("probe:token-value") == 1:
            return False, 'HTTP 404: Oops! An Error Occurred The server returned a "404 Not Found".'
        return True, "existing local API token can list projects"

    def fake_clear(root: Path, reason: str):
        calls.append(f"clear:{reason}")
        return True, "cleared Coolify runtime caches after API 404"

    def fail_create(root: Path):
        raise AssertionError("existing token should be retried before creating a replacement")

    monkeypatch.setattr(module, "enable_api_in_db", fake_enable)
    monkeypatch.setattr(module, "read_api_token", fake_read)
    monkeypatch.setattr(module, "api_token_looks_usable", fake_usable)
    monkeypatch.setattr(module, "clear_coolify_runtime_caches", fake_clear)
    monkeypatch.setattr(module, "create_api_token_in_db", fail_create)

    ok, detail, token = module.ensure_api_token(tmp_path)

    assert ok
    assert token == "token-value"
    assert calls == [
        "enable",
        "read-token",
        "probe:token-value",
        "clear:a dashboard 404 from the Coolify API probe",
        "probe:token-value",
    ]
    assert "cleared Coolify runtime caches" in detail
    assert "can list projects" in detail


def test_local_docker_ensure_api_token_reports_route_diagnostics_after_api_404_retry_fails(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()

    monkeypatch.setattr(module, "enable_api_in_db", lambda root: (True, "api enabled"))
    monkeypatch.setattr(module, "read_api_token", lambda root: "")
    monkeypatch.setattr(module, "create_api_token_in_db", lambda root: (True, "created token", "token-value"))
    monkeypatch.setattr(
        module,
        "api_token_looks_usable",
        lambda root, token: (False, 'HTTP 404: Oops! An Error Occurred The server returned a "404 Not Found".'),
    )
    monkeypatch.setattr(
        module,
        "clear_coolify_runtime_caches",
        lambda root, reason: (True, "cleared Coolify runtime caches after API 404"),
    )
    monkeypatch.setattr(module, "coolify_api_route_diagnostics", lambda root: "API route-list diagnostic: GET api/v1/projects")

    ok, detail, token = module.ensure_api_token(tmp_path)

    assert not ok
    assert token == "token-value"
    assert "token was created but API smoke failed" in detail
    assert "retry failed" in detail
    assert "API route-list diagnostic" in detail


def test_local_docker_ensure_api_token_returns_created_token_when_immediately_usable(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()

    monkeypatch.setattr(module, "enable_api_in_db", lambda root: (True, "api enabled"))
    monkeypatch.setattr(module, "read_api_token", lambda root: "")
    monkeypatch.setattr(module, "create_api_token_in_db", lambda root: (True, "created token", "created-token-value"))
    monkeypatch.setattr(
        module,
        "api_token_looks_usable",
        lambda root, token: (True, "created local API token can list applications"),
    )

    ok, detail, token = module.ensure_api_token(tmp_path)

    assert ok
    assert token == "created-token-value"
    assert "created local API token can list applications" in detail


def test_local_docker_ensure_api_token_returns_created_token_after_cache_retry(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    monkeypatch.setattr(module, "enable_api_in_db", lambda root: (True, "api enabled"))
    monkeypatch.setattr(module, "read_api_token", lambda root: "")
    monkeypatch.setattr(module, "create_api_token_in_db", lambda root: (True, "created token", "created-token-value"))

    def fake_usable(root: Path, token: str):
        calls.append(f"probe:{token}")
        return False, 'HTTP 404: Oops! An Error Occurred The server returned a "404 Not Found".'

    monkeypatch.setattr(module, "api_token_looks_usable", fake_usable)
    monkeypatch.setattr(
        module,
        "retry_api_token_after_runtime_cache_clear",
        lambda root, token, reason: (True, "created local API token works after clearing caches"),
    )

    ok, detail, token = module.ensure_api_token(tmp_path)

    assert ok
    assert token == "created-token-value"
    assert calls == ["probe:created-token-value"]
    assert "created local API token works after clearing caches" in detail


def test_local_docker_api_request_falls_back_to_unversioned_route_after_dashboard_404(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()
    urls: list[str] = []

    def fake_http_get(url: str, **kwargs):
        urls.append(url)
        if url.endswith("/api/v1/projects"):
            return (
                False,
                'Oops! An Error Occurred The server returned a "404 Not Found".',
                url,
                "HTTP 404",
            )
        return True, '[{"uuid":"project-uuid","name":"Main Computer Local Smoke"}]', url, "200"

    monkeypatch.setattr(module, "http_get", fake_http_get)

    ok, detail, parsed = module.coolify_api_get(tmp_path, "/v1/projects", "token-value")

    assert ok
    assert isinstance(parsed, list)
    assert urls == [
        f"{module.dashboard_url(tmp_path)}/api/v1/projects",
        f"{module.dashboard_url(tmp_path)}/api/projects",
    ]
    assert "project-uuid" in detail


def test_local_docker_api_request_does_not_retry_auth_failures(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    urls: list[str] = []

    def fake_http_get(url: str, **kwargs):
        urls.append(url)
        return False, '{"message":"Unauthenticated."}', url, "HTTP 401"

    monkeypatch.setattr(module, "http_get", fake_http_get)

    ok, detail, parsed = module.coolify_api_get(tmp_path, "/v1/projects", "bad-token")

    assert not ok
    assert parsed == {"message": "Unauthenticated."}
    assert urls == [f"{module.dashboard_url(tmp_path)}/api/v1/projects"]
    assert "/v1/projects" in detail
    assert "Unauthenticated" in detail


def test_local_docker_api_token_smoke_prefers_applications_route(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    urls: list[str] = []

    def fake_http_get(url: str, **kwargs):
        urls.append(url)
        return True, "[]", url, "200"

    monkeypatch.setattr(module, "http_get", fake_http_get)

    ok, detail = module.api_token_looks_usable(tmp_path, "token-value")

    assert ok
    assert urls == [f"{module.dashboard_url(tmp_path)}/api/v1/applications"]
    assert "list applications" in detail


def test_local_docker_project_api_falls_back_to_db_when_projects_route_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    def fake_get(root: Path, path: str, token: str):
        calls.append(f"get:{path}:{token}")
        return False, '/v1/projects -> Oops! An Error Occurred The server returned a "404 Not Found".', None

    def fake_php(root: Path, code: str, *, timeout_seconds: int = 180):
        calls.append("php")
        assert "DB::table('projects')" in code
        assert "DB::table('environments')" in code
        return True, "project=project-uuid; environment=environment-uuid"

    monkeypatch.setattr(module, "coolify_api_get", fake_get)
    monkeypatch.setattr(module, "coolify_php", fake_php)

    ok, detail, uuid = module.find_local_project_uuid_via_api(tmp_path, "token-value")

    assert ok
    assert uuid == "project-uuid"
    assert calls == ["get:/v1/projects:token-value", "php"]
    assert "DB fallback" in detail


def test_local_docker_route_diagnostics_avoid_unsupported_columns_option(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    artisan_calls: list[list[str]] = []

    def fake_artisan(root: Path, args: list[str], *, timeout_seconds: int = 180):
        artisan_calls.append(args)
        return True, "GET|HEAD api/health health\nGET|HEAD api/v1/projects projects.index"

    monkeypatch.setattr(module, "coolify_artisan", fake_artisan)

    detail = module.coolify_api_route_diagnostics(tmp_path)

    assert artisan_calls == [["route:list", "--path=api"]]
    assert "--columns" not in " ".join(artisan_calls[0])
    assert "api/v1/projects" in detail


def test_local_docker_python_script_runs_migrations_when_schema_is_incomplete(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    checks: list[str] = []
    artisan_calls: list[list[str]] = []

    def fake_schema_mismatches(root: Path):
        checks.append(str(root))
        if len(checks) == 1:
            return True, ["users.currentTeam"]
        return True, []

    def fake_artisan(root: Path, args: list[str], *, timeout_seconds: int = 180):
        artisan_calls.append(args)
        return True, "migrated"

    monkeypatch.setattr(module, "coolify_schema_mismatches", fake_schema_mismatches)
    monkeypatch.setattr(module, "coolify_artisan", fake_artisan)

    ok, detail = module.ensure_coolify_schema_ready(tmp_path, auto_migrate=True)

    assert ok
    assert "schema is ready" in detail
    assert artisan_calls == [["migrate", "--force"]]
    assert len(checks) == 2


def test_local_docker_python_script_psql_uses_stdin_and_container_postgres_env(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    state_source = module.local_state_dir(tmp_path) / "source"
    state_source.mkdir(parents=True)
    (state_source / ".env").write_text("DB_PASSWORD=secret\n", encoding="utf-8")

    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "1\n"
        stderr = ""

    def fake_run(command, *, check=True, capture=False, input_text=None, timeout_seconds=None):
        captured["command"] = command
        captured["input_text"] = input_text
        return Completed()

    monkeypatch.setattr(module, "run", fake_run)

    ok, output = module.psql(tmp_path, "SELECT 1;")

    assert ok
    assert output == "1"
    assert captured["input_text"] == "SELECT 1;"
    command_text = " ".join(captured["command"])
    assert "POSTGRES_USER" in command_text
    assert "POSTGRES_PASSWORD" in command_text
    assert "POSTGRES_DB" in command_text
    assert 'psql -h 127.0.0.1 -U "$db_user" -d "$db_name"' in command_text


def test_local_docker_python_script_repairs_known_schema_gap_after_migrations(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    schema_checks: list[str] = []
    artisan_calls: list[list[str]] = []
    psql_calls: list[str] = []

    def fake_schema_mismatches(root: Path):
        schema_checks.append(str(root))
        if len(schema_checks) <= 2:
            return True, ["users.currentTeam"]
        return True, []

    def fake_artisan(root: Path, args: list[str], *, timeout_seconds: int = 180):
        artisan_calls.append(args)
        return True, "INFO Nothing to migrate."

    def fake_psql(root: Path, sql: str):
        psql_calls.append(sql)
        return True, ""

    monkeypatch.setattr(module, "coolify_schema_mismatches", fake_schema_mismatches)
    monkeypatch.setattr(module, "coolify_artisan", fake_artisan)
    monkeypatch.setattr(module, "psql", fake_psql)

    ok, detail = module.ensure_coolify_schema_ready(tmp_path, auto_migrate=True)

    assert ok
    assert "applied local schema compatibility repair: users.currentTeam" in detail
    assert "database schema is ready" in detail
    assert artisan_calls == [["migrate", "--force"]]
    assert len(schema_checks) == 3
    assert len(psql_calls) == 1
    assert 'ADD COLUMN IF NOT EXISTS "currentTeam" jsonb' in psql_calls[0]


def test_local_docker_python_script_refuses_unknown_schema_gap_after_migrations(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()

    def fake_schema_mismatches(root: Path):
        return True, ["applications.unknown_column"]

    def fake_artisan(root: Path, args: list[str], *, timeout_seconds: int = 180):
        return True, "INFO Nothing to migrate."

    monkeypatch.setattr(module, "coolify_schema_mismatches", fake_schema_mismatches)
    monkeypatch.setattr(module, "coolify_artisan", fake_artisan)

    ok, detail = module.ensure_coolify_schema_ready(tmp_path, auto_migrate=True)

    assert not ok
    assert "unrepairable schema mismatch" in detail
    assert "applications.unknown_column" in detail


def test_local_docker_onboarding_skip_repairs_team_and_user_session_snapshot(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    psql_calls: list[str] = []

    def fake_psql(root: Path, sql: str):
        psql_calls.append(sql)
        return True, "0,0\n"

    monkeypatch.setattr(module, "psql", fake_psql)

    ok, detail = module.skip_onboarding_in_db(tmp_path)

    assert ok
    assert "onboarding flag/session snapshot is disabled" in detail
    assert len(psql_calls) == 1
    sql = psql_calls[0]
    assert "UPDATE teams" in sql
    assert 'UPDATE users' in sql
    assert '"currentTeam"' in sql
    assert "jsonb_set" in sql
    assert "show_boarding" in sql


def test_local_docker_root_user_exists_probe_uses_generated_credentials(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    module.env_file(tmp_path).parent.mkdir(parents=True)
    module.env_file(tmp_path).write_text(
        "\n".join(
            [
                "ROOT_USERNAME=maincomputer",
                "ROOT_USER_EMAIL=owner@example.com",
                "ROOT_USER_PASSWORD=Valid-Root-Secret1!",
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, str] = {}

    def fake_psql(root: Path, sql: str):
        captured["sql"] = sql
        return True, "42"

    monkeypatch.setattr(module, "psql", fake_psql)

    ok, detail, exists = module.root_user_exists_in_db(tmp_path)

    assert ok
    assert exists
    assert "owner@example.com" in detail
    assert "FROM users" in captured["sql"]
    assert "lower(email)" in captured["sql"]
    assert "'owner@example.com'" in captured["sql"]


def test_local_docker_db_root_bootstrap_creates_local_root_identity(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    module.env_file(tmp_path).parent.mkdir(parents=True)
    module.env_file(tmp_path).write_text(
        "\n".join(
            [
                "APP_PORT=18000",
                "DB_HOST=postgres",
                "DB_PORT=5432",
                "REDIS_HOST=redis",
                "REDIS_PORT=6379",
                "LATEST_REALTIME_VERSION=1.4-16-debian",
                "ROOT_USERNAME=maincomputer",
                "ROOT_USER_EMAIL=owner@example.com",
                "ROOT_USER_PASSWORD=Valid-Root-Secret1!",
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, str] = {}

    def fake_coolify_php(root: Path, code: str, *, timeout_seconds: int = 180):
        captured["code"] = code
        captured["timeout"] = str(timeout_seconds)
        return True, "generated local Coolify root user is present in DB: owner@example.com"

    monkeypatch.setattr(module, "coolify_php", fake_coolify_php)

    ok, detail = module.bootstrap_root_user_via_db(tmp_path)

    assert ok
    assert "owner@example.com" in detail
    assert captured["timeout"] == "180"
    code = captured["code"]
    assert "owner@example.com" in code
    assert "Valid-Root-Secret1!" in code
    assert "Hash::make($password)" in code
    assert "DB::table('users')" in code
    assert "DB::table('teams')" in code
    assert "DB::table('team_user')" in code
    assert "$pivot['role'] = 'owner'" in code
    assert "$userData['currentTeam']" in code


def test_local_docker_dashboard_bootstrap_accepts_existing_db_user_when_root_route_404(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    def fake_root_user(root: Path):
        calls.append("root-user")
        return True, "generated local Coolify root user exists in DB: owner@example.com", True

    def fail_http_get(*args, **kwargs):
        raise AssertionError("root dashboard route should not be required once the DB user exists")

    monkeypatch.setattr(module, "root_user_exists_in_db", fake_root_user)
    monkeypatch.setattr(module, "http_get", fail_http_get)

    ok, detail = module.dashboard_bootstrap_status(tmp_path, auto_bootstrap=True)

    assert ok
    assert calls == ["root-user"]
    assert "root user exists in DB" in detail
    assert "browser bootstrap page is not required" in detail


def test_local_docker_dashboard_bootstrap_db_bootstraps_when_root_route_404(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    def fake_root_user(root: Path):
        calls.append("root-user")
        return True, "generated local Coolify root user is missing in DB: owner@example.com", False

    def fake_bootstrap_db(root: Path):
        calls.append("bootstrap-db")
        return True, "generated local Coolify root user is present in DB: owner@example.com"

    def fail_http_get(*args, **kwargs):
        raise AssertionError("dashboard route should not be required after DB root bootstrap succeeds")

    monkeypatch.setattr(module, "root_user_exists_in_db", fake_root_user)
    monkeypatch.setattr(module, "bootstrap_root_user_via_db", fake_bootstrap_db)
    monkeypatch.setattr(module, "http_get", fail_http_get)

    ok, detail = module.dashboard_bootstrap_status(tmp_path, auto_bootstrap=True)

    assert ok
    assert calls == ["root-user", "bootstrap-db"]
    assert "root user is present in DB" in detail


def test_local_docker_dashboard_bootstrap_reports_root_404_when_db_bootstrap_fails(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    def fake_root_user(root: Path):
        calls.append("root-user")
        return True, "generated local Coolify root user is missing in DB: owner@example.com", False

    def fake_bootstrap_db(root: Path):
        calls.append("bootstrap-db")
        return False, "failed to create generated local Coolify root user in DB: boom"

    def fake_http_get(url: str, **kwargs):
        calls.append(f"get:{url}")
        return False, "Oops! An Error Occurred The server returned a 404.", url, "HTTP 404"

    monkeypatch.setattr(module, "root_user_exists_in_db", fake_root_user)
    monkeypatch.setattr(module, "bootstrap_root_user_via_db", fake_bootstrap_db)
    monkeypatch.setattr(module, "http_get", fake_http_get)

    ok, detail = module.dashboard_bootstrap_status(tmp_path, auto_bootstrap=True)

    assert not ok
    assert calls == ["root-user", "bootstrap-db", "get:http://127.0.0.1:18000"]
    assert "root user is missing in DB" in detail
    assert "failed to create generated local Coolify root user in DB" in detail
    assert "dashboard bootstrap probe failed" in detail
    assert "HTTP 404" in detail


def test_local_docker_api_smoke_uses_db_onboarding_skip_without_web_login(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    monkeypatch.setattr(module, "http_ok", lambda url, **kwargs: (True, "OK"))

    def fake_schema(root: Path, *, auto_migrate: bool = True):
        calls.append("schema")
        return True, "schema ready"

    def fake_bootstrap(root: Path, *, auto_bootstrap: bool = False):
        calls.append("bootstrap")
        return True, "bootstrap hidden"

    def fake_skip(root: Path):
        calls.append("skip-db-onboarding")
        return True, "onboarding skipped in DB"

    def fake_token(root: Path):
        calls.append("token")
        return True, "token usable", "token-value"

    def fake_project(root: Path, token: str):
        calls.append(f"project:{token}")
        return True, "project ready"

    def fail_login(*args, **kwargs):
        raise AssertionError("api-smoke should not use browser login")

    monkeypatch.setattr(module, "ensure_coolify_schema_ready", fake_schema)
    monkeypatch.setattr(module, "dashboard_bootstrap_status", fake_bootstrap)
    monkeypatch.setattr(module, "skip_onboarding_in_db", fake_skip)
    monkeypatch.setattr(module, "ensure_api_token", fake_token)
    monkeypatch.setattr(module, "ensure_local_project_via_api", fake_project)
    monkeypatch.setattr(module, "login_root_user", fail_login)
    monkeypatch.setattr(module, "onboarding_status", fail_login)

    ok, detail = module.api_smoke_status(tmp_path)

    assert ok
    assert calls == ["schema", "bootstrap", "skip-db-onboarding", "token", "project:token-value"]
    assert "schema ready" in detail
    assert "onboarding skipped in DB" in detail


def test_local_docker_api_smoke_uses_relaxed_health_timeout(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    observed: dict[str, object] = {}

    def fake_http_ok(url: str, *, timeout: float = 2.0):
        observed["url"] = url
        observed["timeout"] = timeout
        return False, "timed out"

    monkeypatch.setattr(module, "http_ok", fake_http_ok)

    ok, detail = module.api_smoke_status(tmp_path)

    assert not ok
    assert observed["url"] == module.health_url(tmp_path)
    assert observed["timeout"] == module.COOLIFY_HEALTH_TIMEOUT_SECONDS
    assert observed["timeout"] >= 10.0
    assert detail == "Coolify health failed: timed out"


def test_local_docker_self_ssh_prereqs_repair_broken_storage_ssh_path() -> None:
    script = (REPO_ROOT / "tools/local-prod/coolify-local-docker.py").read_text(encoding="utf-8")

    assert 'storage_app_dir="$storage_dir/app"' in script
    assert 'mkdir -p "$storage_dir" "$storage_app_dir"' in script
    assert 'if [ -L "$storage_ssh_dir" ] && [ ! -d "$storage_ssh_dir" ]; then' in script
    assert 'elif [ -e "$storage_ssh_dir" ] && [ ! -d "$storage_ssh_dir" ]; then' in script
    assert 'rm -f "$storage_ssh_dir"' in script
    assert 'mkdir -p "$storage_ssh_dir" "$storage_ssh_keys_dir" "$storage_ssh_mux_dir" "$storage_tmp_dir"' in script


def test_local_docker_login_retries_once_on_rate_limit(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    attempts: list[str] = []
    sleeps: list[int] = []

    monkeypatch.setattr(module, "ensure_env_contract", lambda root: None)
    monkeypatch.setattr(module, "ensure_coolify_schema_ready", lambda root, *, auto_migrate=True: (True, "schema ready"))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_login_once(root: Path):
        attempts.append(str(root))
        if len(attempts) == 1:
            return False, "root login POST /login failed (HTTP 429): slow down", None
        return True, "root login succeeded", object()

    monkeypatch.setattr(module, "login_root_user_once", fake_login_once)

    ok, detail, opener = module.login_root_user(tmp_path)

    assert ok
    assert opener is not None
    assert detail == "root login succeeded"
    assert len(attempts) == 2
    assert sleeps == [module.LOGIN_RATE_LIMIT_WAIT_SECONDS]

def test_local_docker_onboarding_status_uses_projects_route_as_smoke_gate(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    http_urls: list[str] = []

    def fake_skip(root: Path):
        return True, "local Coolify onboarding flag/session snapshot is disabled"

    def fake_login(root: Path):
        return True, "root login succeeded", object()

    def fake_http_get(url: str, **kwargs):
        http_urls.append(url)
        return True, "<html><body>Projects</body></html>", url, 200

    monkeypatch.setattr(module, "skip_onboarding_in_db", fake_skip)
    monkeypatch.setattr(module, "login_root_user", fake_login)
    monkeypatch.setattr(module, "http_get", fake_http_get)

    ok, detail = module.onboarding_status(tmp_path, auto_onboard=True)

    assert ok
    assert "first-run onboarding is not blocking local smoke" in detail
    assert http_urls == [f"{module.dashboard_url(tmp_path)}/projects"]


def test_local_docker_status_reuses_onboarding_login_check_for_auth(monkeypatch, tmp_path: Path, capsys) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    state_source = module.local_state_dir(tmp_path) / "source"
    state_source.mkdir(parents=True)
    (state_source / ".env").write_text(module.render_env(tmp_path), encoding="utf-8")
    module.credentials_file(tmp_path).write_text("credentials", encoding="utf-8")

    monkeypatch.setattr(module, "docker_compose_command", lambda root, args: ["docker", "compose", *args])
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "http_ok", lambda url, **kwargs: (True, "OK"))
    monkeypatch.setattr(module, "ensure_coolify_schema_ready", lambda root, *, auto_migrate=True: (True, "schema ready"))
    monkeypatch.setattr(module, "dashboard_bootstrap_status", lambda root, *, auto_bootstrap=False: (True, "bootstrap hidden"))

    def fake_onboarding(root: Path, *, auto_onboard: bool = False):
        calls.append("onboarding")
        return True, "authenticated Coolify projects route is reachable"

    def fail_auth(root: Path):
        raise AssertionError("status should not perform a second browser login after onboarding_status passes")

    monkeypatch.setattr(module, "onboarding_status", fake_onboarding)
    monkeypatch.setattr(module, "authenticated_session_status", fail_auth)

    assert module.status(tmp_path) == 0
    output = capsys.readouterr().out

    assert calls == ["onboarding"]
    assert "Coolify first-run onboarding is not blocking local smoke" in output
    assert "generated credentials authenticated while checking the Coolify projects route" in output


def test_local_docker_onboarding_status_accepts_api_smoke_when_projects_route_is_noisy(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    class Opener:
        pass

    def fake_skip(root: Path):
        calls.append("skip")
        return True, "onboarding flags disabled"

    def fake_login(root: Path):
        calls.append("login")
        return True, "login ok", Opener()

    def fake_get(url: str, **kwargs):
        calls.append(f"get:{url}")
        return (
            True,
            "Welcome to Coolify\nConnect your first server\nSkip Setup",
            "http://127.0.0.1:18000/onboarding",
            "200",
        )

    def fake_token(root: Path):
        calls.append("token")
        return True, "token can list projects", "token-value"

    def fake_project(root: Path, token: str):
        calls.append(f"project:{token}")
        return True, "project ready"

    monkeypatch.setattr(module, "skip_onboarding_in_db", fake_skip)
    monkeypatch.setattr(module, "login_root_user", fake_login)
    monkeypatch.setattr(module, "http_get", fake_get)
    monkeypatch.setattr(module, "ensure_api_token", fake_token)
    monkeypatch.setattr(module, "ensure_local_project_via_api", fake_project)

    ok, detail = module.onboarding_status(tmp_path, auto_onboard=True)

    assert ok
    assert calls == [
        "skip",
        "login",
        "get:http://127.0.0.1:18000/projects",
        "token",
        "project:token-value",
    ]
    assert "browser projects route still renders onboarding" in detail
    assert "local bearer-token API smoke is usable" in detail
    assert "project ready" in detail


def test_local_docker_onboarding_status_accepts_api_smoke_when_login_route_is_404(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    def fake_skip(root: Path):
        calls.append("skip")
        return True, "onboarding flags disabled"

    def fake_login(root: Path):
        calls.append("login")
        return False, "HTTP 404: Not Found", None

    def fake_token(root: Path):
        calls.append("token")
        return True, "token can list projects", "token-value"

    def fake_project(root: Path, token: str):
        calls.append(f"project:{token}")
        return True, "project ready"

    monkeypatch.setattr(module, "skip_onboarding_in_db", fake_skip)
    monkeypatch.setattr(module, "login_root_user", fake_login)
    monkeypatch.setattr(module, "ensure_api_token", fake_token)
    monkeypatch.setattr(module, "ensure_local_project_via_api", fake_project)

    ok, detail = module.onboarding_status(tmp_path, auto_onboard=True)

    assert ok
    assert calls == ["skip", "login", "token", "project:token-value"]
    assert "browser login route is not usable" in detail
    assert "local bearer-token API smoke is usable" in detail
    assert "project ready" in detail


def test_local_docker_smoke_nginx_compose_is_a_runtime_rendered_template() -> None:
    compose = (REPO_ROOT / "deploy/coolify/local-docker/smoke-nginx.compose.yml").read_text(encoding="utf-8")

    assert "__SERVICE_NAME__" in compose
    assert "__HOST_PORT__" in compose
    assert "__SMOKE_MARKER__" in compose
    assert "nginx:1.27-alpine" in compose
    assert "127.0.0.1:__HOST_PORT__:80" in compose


def test_local_docker_python_script_has_deploy_smoke_contract() -> None:
    module = load_local_docker_module()

    assert module.LOCAL_SMOKE_SERVICE_NAME_PREFIX == "main-computer-local-smoke-nginx"
    assert module.LOCAL_SMOKE_SERVICE_DEFAULT_PORT == 19080
    assert module.LOCAL_SMOKE_SERVICE_MAX_PORT == 19120
    assert module.LOCAL_SMOKE_SERVICE_EXPECTED_TEXT == "main-computer-local-coolify-smoke-ok"
    assert module.LOCAL_SMOKE_QUEUE_NAMES == "high,default,low"
    assert module.LOCAL_SMOKE_QUEUE_DRAIN_TIMEOUT_SECONDS >= 300
    assert module.valid_docker_network_name("coolify")
    assert module.valid_docker_network_name("main-computer-coolify-local_default")
    assert not module.valid_docker_network_name("../coolify")
    assert module.smoke_compose_file(Path("/repo")).as_posix().endswith(
        "/deploy/coolify/local-docker/smoke-nginx.compose.yml"
    )
    assert module.smoke_site_url(19080) == "http://127.0.0.1:19080"
    assert module.deploy_smoke_state_file(Path("/repo")).as_posix().endswith(
        "/runtime/coolify-local-docker/deploy-smoke.json"
    )

    args = module.parse_args(["deploy-smoke"])
    assert args.action == "deploy-smoke"


def test_local_docker_deploy_smoke_ensures_destination_network(monkeypatch) -> None:
    module = load_local_docker_module()
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, check: bool = True, capture: bool = False, input_text=None, timeout_seconds=None):
        calls.append(cmd)
        if cmd == ["docker", "network", "inspect", "coolify"]:
            return module.subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
        if cmd == ["docker", "network", "create", "coolify"]:
            return module.subprocess.CompletedProcess(cmd, 0, stdout="coolify\n", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(module, "run", fake_run)

    ok, detail = module.ensure_docker_network_exists("coolify")

    assert ok
    assert "created local Docker destination network" in detail
    assert calls == [
        ["docker", "network", "inspect", "coolify"],
        ["docker", "network", "create", "coolify"],
    ]


def test_local_docker_deploy_smoke_drains_coolify_queue_once(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    captured: dict[str, object] = {}

    def fake_artisan(root: Path, args: list[str], timeout_seconds: int = 180):
        captured["root"] = root
        captured["args"] = args
        captured["timeout_seconds"] = timeout_seconds
        return True, "No jobs"

    monkeypatch.setattr(module, "coolify_artisan", fake_artisan)

    ok, detail = module.drain_local_coolify_deployment_queue(tmp_path)

    assert ok
    assert captured["root"] == tmp_path
    assert "queue:work" in captured["args"]
    assert "--stop-when-empty" in captured["args"]
    assert f"--queue={module.LOCAL_SMOKE_QUEUE_NAMES}" in captured["args"]
    assert "-vvv" in captured["args"]
    assert captured["timeout_seconds"] == module.LOCAL_SMOKE_QUEUE_DRAIN_TIMEOUT_SECONDS + 60
    assert "drained local Coolify queue once" in detail


def test_local_docker_deploy_smoke_queue_failure_is_terminal(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()

    def fake_artisan(root: Path, args: list[str], timeout_seconds: int = 180):
        return True, "App\\Actions\\Service\\StartService RUNNING\nApp\\Actions\\Service\\StartService FAIL"

    monkeypatch.setattr(module, "coolify_artisan", fake_artisan)

    ok, detail = module.drain_local_coolify_deployment_queue(tmp_path)

    assert not ok
    assert "failed deployment job" in detail
    assert "StartService" in detail



def test_local_docker_deploy_smoke_bootstraps_missing_localhost_server_target(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    captured: dict[str, object] = {}

    def fake_php(root: Path, code: str, *, timeout_seconds: int = 180):
        captured["root"] = root
        captured["code"] = code
        captured["timeout_seconds"] = timeout_seconds
        return True, "local Coolify localhost server target is ready: server=server-uuid; destination=destination-uuid; network=coolify"

    monkeypatch.setattr(module, "coolify_php", fake_php)

    ok, detail = module.ensure_local_server_usable_in_db(tmp_path)

    assert ok
    assert "localhost server target is ready" in detail
    assert captured["root"] == tmp_path
    assert captured["timeout_seconds"] == 180
    assert "Schema::hasTable('servers')" in captured["code"]
    assert "PrivateKey::generateNewKeyPair" in captured["code"]
    assert "new PrivateKey()" in captured["code"]
    assert "$localKey->private_key = $privateKey" in captured["code"]
    assert "DB::table('private_keys')->insert" not in captured["code"]
    assert "standalone_dockers" in captured["code"]
    assert "'private_key_id', 0" in captured["code"]
    assert "mc_put_if_column($serverValues, $serverColumns, 'is_reachable', true)" in captured["code"]
    assert "mc_put_if_column($serverValues, $serverColumns, 'is_usable', true)" in captured["code"]
    assert "mc_put_if_column($serverValues, $serverColumns, 'force_disabled', false)" in captured["code"]
    assert "'server_id', $serverId" in captured["code"]
    assert "'network', $mainComputerLocalNetwork" in captured["code"]
    assert "global $mainComputerLocalNetwork;" in captured["code"]
    assert "DB::transaction(function () use ($mainComputerLocalNetwork) {" in captured["code"]
    assert "server_settings" in captured["code"]



def test_local_docker_deploy_smoke_repairs_missing_localhost_private_key(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    captured: dict[str, object] = {}

    def fake_php(root: Path, code: str, *, timeout_seconds: int = 180):
        captured["root"] = root
        captured["code"] = code
        captured["timeout_seconds"] = timeout_seconds
        return True, "local Coolify localhost PrivateKey id 0 is ready"

    monkeypatch.setattr(module, "coolify_php", fake_php)

    ok, detail = module.ensure_localhost_private_key_in_db(tmp_path)

    assert ok
    assert "PrivateKey id 0 is ready" in detail
    assert captured["root"] == tmp_path
    assert captured["timeout_seconds"] == 180
    assert "PrivateKey::generateNewKeyPair" in captured["code"]
    assert "private_key_id" in captured["code"]
    assert "where('id', 0)" in captured["code"]
    assert "public_key" in captured["code"]
    assert "Storage::disk('local')" in captured["code"]
    assert "ssh/keys/ssh_key@" in captured["code"]
    assert "delete($keyFilename)" in captured["code"]
    assert "storeInFileSystem" in captured["code"]


def test_local_docker_coolify_php_retries_transient_postgres_shutdown(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[list[str]] = []
    psql_calls: list[str] = []

    transient = (
        'In Connector.php line 67: SQLSTATE[08006] [7] connection to server at "postgres" '
        '(172.21.0.4), port 5432 failed: FATAL: the database system is shutting down'
    )

    def fake_run(command: list[str], *, check: bool = True, capture: bool = False, input_text=None, timeout_seconds=None):
        calls.append(command)
        php_calls = [call for call in calls if call[-1:] == ["php"]]
        if len(php_calls) == 1:
            return module.subprocess.CompletedProcess(command, 137, stdout=transient, stderr="")
        return module.subprocess.CompletedProcess(command, 0, stdout="php repair ok", stderr="")

    def fake_psql(root: Path, sql: str):
        psql_calls.append(sql)
        return True, "1"

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(module, "psql", fake_psql)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    ok, detail = module.coolify_php(tmp_path, "<?php echo 'ok';", timeout_seconds=180)

    assert ok
    assert "php repair ok" in detail
    assert "retried after transient Coolify DB readiness failure" in detail
    assert psql_calls == ["SELECT 1;"]
    assert len([call for call in calls if call[-1:] == ["php"]]) == 2


def test_local_docker_ensure_infra_status_sets_up_ssh_ready_local_target(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []

    def fake_api(root: Path):
        calls.append("api")
        return True, "api ready"

    def fake_server(root: Path):
        calls.append("server")
        return True, "localhost server target is ready"

    def fake_key(root: Path):
        calls.append("key")
        return True, "localhost PrivateKey id 0 is ready"

    def fake_target(root: Path):
        calls.append("target")
        return True, "local deployment target is ready", {"network": "coolify"}

    def fake_network(root: Path, target: dict[str, str]):
        calls.append(f"network:{target['network']}")
        return True, "local Docker destination network exists: coolify"

    monkeypatch.setattr(module, "api_smoke_status", fake_api)
    monkeypatch.setattr(module, "ensure_local_server_usable_in_db", fake_server)
    monkeypatch.setattr(module, "ensure_localhost_private_key_in_db", fake_key)
    monkeypatch.setattr(module, "local_deploy_target_from_db", fake_target)
    monkeypatch.setattr(module, "ensure_local_deploy_network", fake_network)

    ok, detail = module.ensure_infra_status(tmp_path)

    assert ok is True
    assert calls == ["api", "server", "key", "target", "network:coolify"]
    assert "PrivateKey id 0 is ready" in detail
    assert "local Docker destination network exists" in detail


def test_local_docker_smoke_compose_payload_is_base64_encoded(tmp_path: Path) -> None:
    module = load_local_docker_module()
    path = module.smoke_compose_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
services:
  __SERVICE_NAME__:
    image: nginx:1.27-alpine
    command: ["sh", "-c", "printf '%s\\n' '__SMOKE_MARKER__' > /usr/share/nginx/html/index.html && nginx -g 'daemon off;'"]
    ports:
      - "127.0.0.1:__HOST_PORT__:80"
""",
        encoding="utf-8",
    )

    ok, detail, encoded = module.smoke_compose_base64(
        tmp_path,
        "main-computer-local-smoke-nginx-19080-abcd1234",
        19080,
        "main-computer-local-coolify-smoke-ok-abcd1234",
    )

    assert ok
    assert "smoke compose template is ready" in detail
    decoded = module.base64.b64decode(encoded).decode("utf-8")
    assert "main-computer-local-smoke-nginx-19080-abcd1234" in decoded
    assert "127.0.0.1:19080:80" in decoded
    assert "main-computer-local-coolify-smoke-ok-abcd1234" in decoded
    assert "__HOST_PORT__" not in decoded


def test_local_docker_smoke_compose_payload_requires_placeholders(tmp_path: Path) -> None:
    module = load_local_docker_module()
    path = module.smoke_compose_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
services:
  main-computer-local-smoke-nginx:
    image: nginx:1.27-alpine
    ports:
      - "127.0.0.1:18081:80"
""",
        encoding="utf-8",
    )

    ok, detail, encoded = module.smoke_compose_base64(
        tmp_path,
        "main-computer-local-smoke-nginx-19080-abcd1234",
        19080,
        "main-computer-local-coolify-smoke-ok-abcd1234",
    )

    assert not ok
    assert "missing placeholders" in detail
    assert encoded == ""


def test_local_docker_smoke_site_status_reports_body_excerpt(monkeypatch) -> None:
    module = load_local_docker_module()

    def fake_get(url: str, timeout: float = 2.0, read_limit: int = 65536, **kwargs):
        return True, "<html>Blog Site (local)</html>", url, "200"

    monkeypatch.setattr(module, "http_get", fake_get)

    ok, detail = module.smoke_site_status(19081, "main-computer-local-coolify-smoke-ok-abcd1234")

    assert not ok
    assert "main-computer-local-coolify-smoke-ok-abcd1234" in detail
    assert "response excerpt" in detail
    assert "Blog Site" in detail


def test_local_docker_deploy_smoke_state_rejects_stale_fixed_port_response(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    module.deploy_smoke_state_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    module.deploy_smoke_state_file(tmp_path).write_text(
        module.json.dumps(
            {
                "service_name": "main-computer-local-smoke-nginx-18081-old",
                "port": 18081,
                "marker": "main-computer-local-coolify-smoke-ok-old",
                "service_uuid": "old-service",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "port_is_open", lambda port: port == 18081)
    ok, detail, state = module.new_deploy_smoke_state()

    assert ok
    assert state["port"] != 18081
    assert "selected free local smoke port" in detail


def test_local_docker_deploy_smoke_status_uses_api_and_coolify_deploy(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []
    module.api_token_file(tmp_path).parent.mkdir(parents=True)
    module.api_token_file(tmp_path).write_text("token=token-value\n", encoding="utf-8")

    def fake_api_smoke(root: Path):
        calls.append("api-smoke")
        return True, "api ready"

    def fake_server_usable(root: Path):
        calls.append("server-usable")
        return True, "server usable"

    def fake_private_key(root: Path):
        calls.append("private-key")
        return True, "localhost private key ready"

    def fake_target(root: Path):
        calls.append("target")
        return True, "target ready", {
            "server_uuid": "server-uuid",
            "destination_uuid": "destination-uuid",
            "network": "coolify",
        }

    def fake_network(root: Path, target: dict[str, str]):
        calls.append(f"network:{target['network']}")
        return True, "network ready"

    def fake_project(root: Path, token: str):
        calls.append(f"project:{token}")
        return True, "project ready", "project-uuid"

    def fake_environment(root: Path, token: str, project_uuid: str):
        calls.append(f"environment:{token}:{project_uuid}")
        return True, "environment ready"

    def fake_new_state():
        calls.append("new-state")
        return True, "selected free local smoke port 19080", {
            "service_name": "main-computer-local-smoke-nginx-19080-abcd1234",
            "port": 19080,
            "marker": "main-computer-local-coolify-smoke-ok-abcd1234",
        }

    def fake_find_service(root: Path, token: str, service_name: str):
        calls.append(f"find-service:{token}:{service_name}")
        return True, "service missing", ""

    def fake_create_service(root: Path, token: str, project_uuid: str, target: dict[str, str], state: dict[str, object]):
        calls.append(f"create-service:{token}:{project_uuid}:{target['server_uuid']}:{target['destination_uuid']}:{state['port']}")
        return True, "service created", "service-uuid"

    def fake_compatible(root: Path, service_uuid: str, state: dict[str, object]):
        calls.append(f"compatible:{service_uuid}:{state['port']}")
        return True, "service record compatible"

    def fake_deploy(root: Path, token: str, service_uuid: str):
        calls.append(f"deploy:{token}:{service_uuid}")
        return True, "deploy requested", "deployment-uuid"

    def fake_queue(root: Path):
        calls.append("queue")
        return True, "queue drained"

    def fake_wait(root: Path, token: str, port: int, marker: str, deployment_uuid: str = "", *, timeout_seconds: int = 180):
        calls.append(f"wait:{token}:{port}:{marker}:{deployment_uuid}:{timeout_seconds}")
        return True, "site reachable"

    monkeypatch.setattr(module, "api_smoke_status", fake_api_smoke)
    monkeypatch.setattr(module, "ensure_local_server_usable_in_db", fake_server_usable)
    monkeypatch.setattr(module, "ensure_localhost_private_key_in_db", fake_private_key)
    monkeypatch.setattr(module, "local_deploy_target_from_db", fake_target)
    monkeypatch.setattr(module, "ensure_local_deploy_network", fake_network)
    monkeypatch.setattr(module, "find_local_project_uuid_via_api", fake_project)
    monkeypatch.setattr(module, "ensure_project_environment_via_api_or_db", fake_environment)
    monkeypatch.setattr(module, "new_deploy_smoke_state", fake_new_state)
    monkeypatch.setattr(module, "find_smoke_service_uuid_via_api", fake_find_service)
    monkeypatch.setattr(module, "create_smoke_service_via_api", fake_create_service)
    monkeypatch.setattr(module, "smoke_service_record_compatible", fake_compatible)
    monkeypatch.setattr(module, "trigger_smoke_service_deploy_via_api", fake_deploy)
    monkeypatch.setattr(module, "drain_local_coolify_deployment_queue", fake_queue)
    monkeypatch.setattr(module, "wait_for_smoke_deployment", fake_wait)

    ok, detail = module.deploy_smoke_status(tmp_path)

    assert ok
    assert "queue drained" in detail
    assert "site reachable" in detail
    assert calls == [
        "api-smoke",
        "server-usable",
        "private-key",
        "target",
        "network:coolify",
        "project:token-value",
        "environment:token-value:project-uuid",
        "new-state",
        "find-service:token-value:main-computer-local-smoke-nginx-19080-abcd1234",
        "create-service:token-value:project-uuid:server-uuid:destination-uuid:19080",
        "compatible:service-uuid:19080",
        "deploy:token-value:service-uuid",
        "queue",
        "wait:token-value:19080:main-computer-local-coolify-smoke-ok-abcd1234:deployment-uuid:180",
    ]


def test_local_docker_deploy_smoke_discards_stale_service_record(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []
    module.api_token_file(tmp_path).parent.mkdir(parents=True)
    module.api_token_file(tmp_path).write_text("token=token-value\n", encoding="utf-8")
    module.deploy_smoke_state_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    module.deploy_smoke_state_file(tmp_path).write_text(
        module.json.dumps(
            {
                "service_name": "main-computer-local-smoke-nginx-19080-old",
                "port": 19080,
                "marker": "main-computer-local-coolify-smoke-ok-old",
                "service_uuid": "stale-service-uuid",
            }
        ),
        encoding="utf-8",
    )

    def fake_api_smoke(root: Path):
        return True, "api ready"

    def fake_server_usable(root: Path):
        return True, "server usable"

    def fake_private_key(root: Path):
        return True, "localhost private key ready"

    def fake_target(root: Path):
        return True, "target ready", {
            "server_uuid": "server-uuid",
            "destination_uuid": "destination-uuid",
            "network": "coolify",
        }

    def fake_network(root: Path, target: dict[str, str]):
        return True, "network ready"

    def fake_project(root: Path, token: str):
        return True, "project ready", "project-uuid"

    def fake_environment(root: Path, token: str, project_uuid: str):
        return True, "environment ready"

    def fake_site_status(port: int, marker: str):
        return False, "URL error: connection refused"

    def fake_new_state():
        calls.append("new-state")
        return True, "selected free local smoke port 19081", {
            "service_name": "main-computer-local-smoke-nginx-19081-new",
            "port": 19081,
            "marker": "main-computer-local-coolify-smoke-ok-new",
        }

    def fake_find_service(root: Path, token: str, service_name: str):
        calls.append(f"find:{service_name}")
        if service_name.endswith("-old"):
            return True, "old service exists", "stale-service-uuid"
        return True, "new service missing", ""

    def fake_compatible(root: Path, service_uuid: str, state: dict[str, object]):
        calls.append(f"compatible:{service_uuid}:{state['service_name']}")
        if service_uuid == "stale-service-uuid":
            return False, "stale/incompatible local smoke service record: connect_to_docker_network=false"
        return True, "local smoke service record is compatible"

    def fake_create_service(root: Path, token: str, project_uuid: str, target: dict[str, str], state: dict[str, object]):
        calls.append(f"create:{state['service_name']}:{state['port']}")
        return True, "service created", "fresh-service-uuid"

    def fake_deploy(root: Path, token: str, service_uuid: str):
        calls.append(f"deploy:{service_uuid}")
        return True, "deploy requested", ""

    def fake_queue(root: Path):
        return True, "queue drained"

    def fake_wait(root: Path, token: str, port: int, marker: str, deployment_uuid: str = "", *, timeout_seconds: int = 180):
        calls.append(f"wait:{port}:{marker}")
        return True, "site reachable"

    monkeypatch.setattr(module, "api_smoke_status", fake_api_smoke)
    monkeypatch.setattr(module, "ensure_local_server_usable_in_db", fake_server_usable)
    monkeypatch.setattr(module, "ensure_localhost_private_key_in_db", fake_private_key)
    monkeypatch.setattr(module, "local_deploy_target_from_db", fake_target)
    monkeypatch.setattr(module, "ensure_local_deploy_network", fake_network)
    monkeypatch.setattr(module, "find_local_project_uuid_via_api", fake_project)
    monkeypatch.setattr(module, "ensure_project_environment_via_api_or_db", fake_environment)
    monkeypatch.setattr(module, "smoke_site_status", fake_site_status)
    monkeypatch.setattr(module, "new_deploy_smoke_state", fake_new_state)
    monkeypatch.setattr(module, "find_smoke_service_uuid_via_api", fake_find_service)
    monkeypatch.setattr(module, "smoke_service_record_compatible", fake_compatible)
    monkeypatch.setattr(module, "create_smoke_service_via_api", fake_create_service)
    monkeypatch.setattr(module, "trigger_smoke_service_deploy_via_api", fake_deploy)
    monkeypatch.setattr(module, "drain_local_coolify_deployment_queue", fake_queue)
    monkeypatch.setattr(module, "wait_for_smoke_deployment", fake_wait)

    ok, detail = module.deploy_smoke_status(tmp_path)

    assert ok
    assert "discarded stale deploy smoke Coolify service stale-service-uuid" in detail
    assert "connect_to_docker_network=false" in detail
    assert calls == [
        "find:main-computer-local-smoke-nginx-19080-old",
        "compatible:stale-service-uuid:main-computer-local-smoke-nginx-19080-old",
        "new-state",
        "find:main-computer-local-smoke-nginx-19081-new",
        "create:main-computer-local-smoke-nginx-19081-new:19081",
        "compatible:fresh-service-uuid:main-computer-local-smoke-nginx-19081-new",
        "deploy:fresh-service-uuid",
        "wait:19081:main-computer-local-coolify-smoke-ok-new",
    ]


def test_local_docker_service_create_payload_uses_official_services_api(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    path = module.smoke_compose_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
services:
  __SERVICE_NAME__:
    image: nginx:1.27-alpine
    command: ["sh", "-c", "printf '%s\\n' '__SMOKE_MARKER__' > /usr/share/nginx/html/index.html && nginx -g 'daemon off;'"]
    ports:
      - "127.0.0.1:__HOST_PORT__:80"
""",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_post(root: Path, path: str, token: str, payload: dict[str, object]):
        captured["path"] = path
        captured["token"] = token
        captured["payload"] = payload
        return True, '{"uuid":"service-uuid"}', {"uuid": "service-uuid"}

    def fake_patch(root: Path, path: str, token: str, payload: dict[str, object]):
        captured["patch_path"] = path
        captured["patch_token"] = token
        captured["patch_payload"] = payload
        return True, '{"uuid":"service-uuid"}', {"uuid": "service-uuid"}

    monkeypatch.setattr(module, "coolify_api_post", fake_post)
    monkeypatch.setattr(module, "coolify_api_patch", fake_patch)

    state = {
        "service_name": "main-computer-local-smoke-nginx-19080-abcd1234",
        "port": 19080,
        "marker": "main-computer-local-coolify-smoke-ok-abcd1234",
    }

    ok, detail, uuid = module.create_smoke_service_via_api(
        tmp_path,
        "token-value",
        "project-uuid",
        {"server_uuid": "server-uuid", "destination_uuid": "destination-uuid"},
        state,
    )

    assert ok
    assert uuid == "service-uuid"
    assert module.deploy_smoke_state_file(tmp_path).exists()
    assert captured["path"] == "/v1/services"
    payload = captured["payload"]
    assert payload["name"] == state["service_name"]
    assert payload["project_uuid"] == "project-uuid"
    assert payload["environment_name"] == module.LOCAL_PROJECT_ENVIRONMENT
    assert payload["server_uuid"] == "server-uuid"
    assert payload["destination_uuid"] == "destination-uuid"
    assert payload["instant_deploy"] is False
    assert "connect_to_docker_network" not in payload
    assert "type" not in payload
    assert isinstance(payload["docker_compose_raw"], str)
    decoded = module.base64.b64decode(payload["docker_compose_raw"]).decode("utf-8")
    assert "main-computer-local-smoke-nginx-19080-abcd1234" in decoded
    assert "127.0.0.1:19080:80" in decoded
    assert "main-computer-local-coolify-smoke-ok-abcd1234" in decoded
    assert captured["patch_path"] == "/v1/services/service-uuid"
    assert captured["patch_token"] == "token-value"
    assert captured["patch_payload"] == {"connect_to_docker_network": True}
    assert "enabled local smoke service Docker network" in detail


def test_local_docker_service_network_enable_falls_back_to_local_db(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    captured: dict[str, object] = {}

    def fake_patch(root: Path, path: str, token: str, payload: dict[str, object]):
        captured["patch_path"] = path
        captured["patch_payload"] = payload
        return False, '{"message":"Validation failed."}', {"message": "Validation failed."}

    def fake_psql(root: Path, sql: str):
        captured["sql"] = sql
        return True, "service-uuid"

    monkeypatch.setattr(module, "coolify_api_patch", fake_patch)
    monkeypatch.setattr(module, "psql", fake_psql)

    ok, detail = module.enable_smoke_service_docker_network(tmp_path, "token-value", "service-uuid")

    assert ok
    assert captured["patch_path"] == "/v1/services/service-uuid"
    assert captured["patch_payload"] == {"connect_to_docker_network": True}
    assert "UPDATE services" in captured["sql"]
    assert "connect_to_docker_network = true" in captured["sql"]
    assert "enabled local smoke service Docker network through local DB fallback" in detail


def test_local_docker_smoke_uses_local_docker_fallback_when_coolify_ssh_fails(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    path = module.smoke_compose_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
services:
  __SERVICE_NAME__:
    image: nginx:1.27-alpine
    command: ["sh", "-c", "printf '%s\\n' '__SMOKE_MARKER__' > /usr/share/nginx/html/index.html && nginx -g 'daemon off;'"]
    ports:
      - "127.0.0.1:__HOST_PORT__:80"
""",
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, check: bool = True, capture: bool = False, input_text=None, timeout_seconds=None):
        calls.append(cmd)
        if cmd[:2] == ["docker", "compose"]:
            return module.subprocess.CompletedProcess(cmd, 0, stdout="Container started\n", stderr="")
        if cmd[:3] == ["docker", "ps", "-q"]:
            return module.subprocess.CompletedProcess(cmd, 0, stdout="container-id\n", stderr="")
        if cmd[:3] == ["docker", "network", "connect"]:
            return module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(module, "run", fake_run)

    state = {
        "service_name": "main-computer-local-smoke-nginx-19080-abcd1234",
        "port": 19080,
        "marker": "main-computer-local-coolify-smoke-ok-abcd1234",
    }

    ok, detail = module.start_smoke_service_with_local_docker(
        tmp_path,
        "service-uuid",
        state,
        {"network": "coolify"},
        "root@host.docker.internal: Permission denied (publickey,password).",
    )

    assert ok
    assert "Docker Desktop fallback" in detail
    assert "root@host.docker.internal" in detail
    rendered = (module.local_state_dir(tmp_path) / "deploy-smoke-local-compose.yml").read_text(encoding="utf-8")
    assert "main-computer-local-smoke-nginx-19080-abcd1234" in rendered
    assert "127.0.0.1:19080:80" in rendered
    assert "main-computer-local-coolify-smoke-ok-abcd1234" in rendered
    assert any(cmd[:2] == ["docker", "compose"] and "up" in cmd for cmd in calls)
    assert any(cmd[:3] == ["docker", "network", "connect"] for cmd in calls)


def test_local_docker_deploy_smoke_falls_back_after_coolify_queue_ssh_failure(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    calls: list[str] = []
    module.api_token_file(tmp_path).parent.mkdir(parents=True)
    module.api_token_file(tmp_path).write_text("token=token-value\n", encoding="utf-8")

    def fake_api_smoke(root: Path):
        return True, "api ready"

    def fake_server_usable(root: Path):
        return True, "server usable"

    def fake_private_key(root: Path):
        return True, "localhost private key ready"

    def fake_target(root: Path):
        return True, "target ready", {
            "server_uuid": "server-uuid",
            "destination_uuid": "destination-uuid",
            "network": "coolify",
        }

    def fake_network(root: Path, target: dict[str, str]):
        return True, "network ready"

    def fake_project(root: Path, token: str):
        return True, "project ready", "project-uuid"

    def fake_environment(root: Path, token: str, project_uuid: str):
        return True, "environment ready"

    def fake_new_state():
        return True, "selected free local smoke port 19080", {
            "service_name": "main-computer-local-smoke-nginx-19080-abcd1234",
            "port": 19080,
            "marker": "main-computer-local-coolify-smoke-ok-abcd1234",
        }

    def fake_find_service(root: Path, token: str, service_name: str):
        return True, "service missing", ""

    def fake_create_service(root: Path, token: str, project_uuid: str, target: dict[str, str], state: dict[str, object]):
        return True, "service created", "service-uuid"

    def fake_compatible(root: Path, service_uuid: str, state: dict[str, object]):
        return True, "service record compatible"

    def fake_deploy(root: Path, token: str, service_uuid: str):
        calls.append("deploy")
        return True, "service start requested through Coolify API", ""

    def fake_queue(root: Path):
        calls.append("queue")
        return False, "root@host.docker.internal: Permission denied (publickey,password)."

    def fake_local_fallback(root: Path, service_uuid: str, state: dict[str, object], target: dict[str, str], reason: str = ""):
        calls.append(f"fallback:{reason}")
        return True, "started local smoke service through Docker Desktop fallback"

    def fake_wait(root: Path, token: str, port: int, marker: str, deployment_uuid: str = "", *, timeout_seconds: int = 180):
        calls.append(f"wait:{timeout_seconds}")
        return True, "site reachable"

    monkeypatch.setattr(module, "api_smoke_status", fake_api_smoke)
    monkeypatch.setattr(module, "ensure_local_server_usable_in_db", fake_server_usable)
    monkeypatch.setattr(module, "ensure_localhost_private_key_in_db", fake_private_key)
    monkeypatch.setattr(module, "local_deploy_target_from_db", fake_target)
    monkeypatch.setattr(module, "ensure_local_deploy_network", fake_network)
    monkeypatch.setattr(module, "find_local_project_uuid_via_api", fake_project)
    monkeypatch.setattr(module, "ensure_project_environment_via_api_or_db", fake_environment)
    monkeypatch.setattr(module, "new_deploy_smoke_state", fake_new_state)
    monkeypatch.setattr(module, "find_smoke_service_uuid_via_api", fake_find_service)
    monkeypatch.setattr(module, "create_smoke_service_via_api", fake_create_service)
    monkeypatch.setattr(module, "smoke_service_record_compatible", fake_compatible)
    monkeypatch.setattr(module, "trigger_smoke_service_deploy_via_api", fake_deploy)
    monkeypatch.setattr(module, "drain_local_coolify_deployment_queue", fake_queue)
    monkeypatch.setattr(module, "start_smoke_service_with_local_docker", fake_local_fallback)
    monkeypatch.setattr(module, "wait_for_smoke_deployment", fake_wait)

    ok, detail = module.deploy_smoke_status(tmp_path)

    assert ok
    assert "started local smoke service through Docker Desktop fallback" in detail
    assert "site reachable" in detail
    assert calls == [
        "deploy",
        "queue",
        "fallback:root@host.docker.internal: Permission denied (publickey,password).",
        "wait:180",
    ]


def test_local_docker_deploy_smoke_reports_coolify_failed_job_diagnostics() -> None:
    source = (REPO_ROOT / "tools/local-prod/coolify-local-docker.py").read_text(encoding="utf-8")

    assert "latest_failed_job_diagnostics" in source
    assert "failed_jobs" in source
    assert "Coolify service record" in source
    assert "recent Coolify container logs" in source
    assert "local Coolify queue reported a failed deployment job" in source
    assert "coolify_deploy_failure_diagnostics" in source
    assert "smoke_service_record_compatible" in source
    assert "discarded stale deploy smoke Coolify service" in source
    assert "start_smoke_service_with_local_docker" in source
    assert "Docker Desktop fallback" in source


def test_local_docker_service_urls_payload_uses_compose_service_name_for_coolify_selector() -> None:
    module = load_local_docker_module()

    payload = module._coolify_service_urls_payload(
        [
            "http://hub-site.localhost",
            "",
        ],
        service_name="main-computer-hub-site-local-publish",
    )

    assert payload == [
        {
            "name": "main-computer-hub-site-local-publish",
            "url": "http://hub-site.localhost",
        },
    ]
    assert all(set(item) == {"name", "url"} for item in payload)


def test_local_docker_service_urls_payload_preserves_hostname_fallback_without_service_name() -> None:
    module = load_local_docker_module()

    payload = module._coolify_service_urls_payload([
        "https://example.test/blog",
    ])

    assert payload == [
        {"name": "example.test", "url": "https://example.test/blog"},
    ]
    assert all(set(item) == {"name", "url"} for item in payload)


def test_create_docker_compose_service_url_name_matches_compose_service(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    captured: dict[str, object] = {}

    def fake_post(root: Path, path: str, token: str, payload: dict[str, object]):
        captured["path"] = path
        captured["payload"] = payload
        return True, '{"uuid":"service-uuid"}', {"uuid": "service-uuid"}

    def fake_enable_network(root: Path, token: str, service_uuid: str):
        captured["network_uuid"] = service_uuid
        return True, "network enabled"

    monkeypatch.setattr(module, "coolify_api_post", fake_post)
    monkeypatch.setattr(module, "enable_smoke_service_docker_network", fake_enable_network)

    ok, detail, uuid = module.create_docker_compose_service_via_api(
        tmp_path,
        "token-value",
        "project-uuid",
        {"server_uuid": "server-uuid", "destination_uuid": "destination-uuid"},
        service_name="main-computer-hub-site-local-publish",
        description="Hub Site local publish",
        docker_compose_raw="services:\n  main-computer-hub-site-local-publish:\n    image: test:latest\n",
        urls=["http://hub-site.localhost"],
    )

    assert ok is True
    assert uuid == "service-uuid"
    assert captured["path"] == "/v1/services"
    assert captured["network_uuid"] == "service-uuid"
    payload = captured["payload"]
    assert payload["urls"] == [
        {
            "name": "main-computer-hub-site-local-publish",
            "url": "http://hub-site.localhost",
        },
    ]
    assert "network enabled" in detail



def test_ensure_docker_compose_service_updates_existing_resource_before_deploy(monkeypatch, tmp_path: Path) -> None:
    module = load_local_docker_module()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        module,
        "find_service_uuid_via_api",
        lambda root, token, service_name: (True, f"service already exists: {service_name} (service-uuid)", "service-uuid"),
    )

    def fake_patch(root: Path, path: str, token: str, payload: dict[str, object]):
        captured["path"] = path
        captured["payload"] = payload
        return True, '{"uuid":"service-uuid"}', {"uuid": "service-uuid"}

    monkeypatch.setattr(module, "coolify_api_patch", fake_patch)

    ok, detail, uuid = module.ensure_docker_compose_service_via_api(
        tmp_path,
        "token-value",
        "project-uuid",
        {"server_uuid": "server-uuid", "destination_uuid": "destination-uuid"},
        service_name="main-computer-hub-site-local-publish",
        description="Hub Site local publish",
        docker_compose_raw="services:\n  main-computer-hub-site-local-publish:\n    image: test:latest\n",
        urls=["http://hub-site.localhost"],
    )

    assert ok is True
    assert uuid == "service-uuid"
    assert captured["path"] == "/v1/services/service-uuid"
    payload = captured["payload"]
    assert payload == {
        "name": "main-computer-hub-site-local-publish",
        "description": "Hub Site local publish",
        "docker_compose_raw": payload["docker_compose_raw"],
        "connect_to_docker_network": True,
        "urls": [
            {
                "name": "main-computer-hub-site-local-publish",
                "url": "http://hub-site.localhost",
            },
        ],
        "is_container_label_escape_enabled": True,
    }
    for create_only in (
        "project_uuid",
        "environment_name",
        "environment_uuid",
        "server_uuid",
        "destination_uuid",
        "instant_deploy",
    ):
        assert create_only not in payload
    decoded = module.base64.b64decode(payload["docker_compose_raw"]).decode("utf-8")
    assert "main-computer-hub-site-local-publish" in decoded
    assert "updated service main-computer-hub-site-local-publish" in detail
    assert "enabled local Docker network" in detail
