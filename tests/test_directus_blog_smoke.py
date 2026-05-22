from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "local-prod" / "directus-blog-e2e-smoke.py"


def load_directus_smoke_module():
    spec = importlib.util.spec_from_file_location("directus_blog_e2e_smoke", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_directus_blog_smoke_script_exists() -> None:
    assert SCRIPT.is_file()


def test_directus_blog_compose_uses_persistent_directus_sqlite_and_upload_volumes() -> None:
    module = load_directus_smoke_module()
    state = {
        "service_name": "main-computer-directus-blog-e2e-test",
        "directus_service_name": "directus-test",
        "website_service_name": "directus-blog-site-test",
        "db_volume_name": "main_computer_directus_blog_e2e_test_database",
        "uploads_volume_name": "main_computer_directus_blog_e2e_test_uploads",
        "site_port": 28180,
        "directus_port": 28181,
        "site_id": "directus-blog-e2e-test",
        "owner": "main-computer-directus-blog-e2e-v1",
        "directus_image": "directus/directus:11.5.1",
        "admin_email": "directus-smoke-admin@example.com",
        "admin_password": "Admin-password-1!",
        "admin_token": "admin-token",
        "secret": "directus-secret",
    }

    compose = module.render_compose(state)

    assert "directus/directus:11.5.1" in compose
    assert 'DB_CLIENT: "sqlite3"' in compose
    assert 'DB_FILENAME: "/directus/database/data.db"' in compose
    assert 'STORAGE_LOCATIONS: "local"' in compose
    assert 'STORAGE_LOCAL_ROOT: "/directus/uploads"' in compose
    assert "- directus-database:/directus/database" in compose
    assert "- directus-uploads:/directus/uploads" in compose
    assert "name: main_computer_directus_blog_e2e_test_database" in compose
    assert "name: main_computer_directus_blog_e2e_test_uploads" in compose
    assert 'BLOG_PROVIDER: "directus"' in compose
    assert 'DIRECTUS_URL: "http://directus-test:8055"' in compose
    assert '"blogReadOk": blog_read_ok' in compose
    assert 'later public-access assertions prove the blog API contract' in compose
    assert '127.0.0.1:28181:8055' in compose
    assert '127.0.0.1:28180:8080' in compose

    website_section = compose.split("  directus-blog-site-test:", 1)[1]
    assert "ADMIN_TOKEN" not in website_section
    assert "ADMIN_PASSWORD" not in website_section


def test_directus_blog_smoke_has_schema_permission_seed_and_redeploy_assertions() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "ensure_collection" in source
    assert "ensure_fields" in source
    assert "ensure_public_read_permission" in source
    assert "list_permissions(state, policy_mode=policy_mode)" in source
    assert 'fields = "id,collection,action,policy,fields,permissions"' in source
    assert 'fields = "id,collection,action,role,fields,permissions"' in source
    assert '"permissions": {"status": {"_eq": "published"}}' in source
    assert "DIRECTUS_DRAFT_SLUG" in source
    assert "assert_public_directus_permissions" in source
    assert "assert_site_renders_blog" in source
    assert "assert_directus_content_survived" in source
    assert "persistent_volumes_exist" in source
    assert "inspect_volume_mount_by_destination" in source
    assert "directus_container_ids_for_state" in source
    assert "actual_db_volume_name" in source
    assert "trigger_and_wait" in source
    assert "second deploy preserves Directus database and upload state" in source


def test_directus_blog_volume_check_accepts_coolify_resolved_volume_names(monkeypatch) -> None:
    module = load_directus_smoke_module()
    state = {
        "service_name": "main-computer-directus-blog-e2e-test",
        "service_uuid": "coolify-service-uuid",
        "directus_service_name": "directus-test",
        "db_volume_name": "requested_database_volume",
        "uploads_volume_name": "requested_uploads_volume",
    }

    def fake_run_docker_command(args, *, timeout_seconds=240):
        command = " ".join(args)
        if args[:3] == ["docker", "ps", "-a"] and "label=com.docker.compose.project=coolify-service-uuid" in command:
            return True, "directus-container"
        if args[:3] == ["docker", "ps", "-a"]:
            return True, ""
        if args[:2] == ["docker", "inspect"] and args[2] == "directus-container":
            return True, json.dumps(
                [
                    {
                        "Type": "volume",
                        "Name": "coolify-service-uuid_directus-database",
                        "Destination": "/directus/database",
                    },
                    {
                        "Type": "volume",
                        "Name": "coolify-service-uuid_directus-uploads",
                        "Destination": "/directus/uploads",
                    },
                ]
            )
        return False, f"unexpected command: {command}"

    monkeypatch.setattr(module, "run_docker_command", fake_run_docker_command)

    ok, detail = module.persistent_volumes_exist(state)

    assert ok, detail
    assert state["actual_db_volume_name"] == "coolify-service-uuid_directus-database"
    assert state["actual_uploads_volume_name"] == "coolify-service-uuid_directus-uploads"
    assert "requested 'requested_database_volume'" in detail
    assert "resolved by Coolify/Compose" in detail


def test_directus_blog_smoke_cli_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0
    assert "--reset-state" in result.stdout
    assert "--require-coolify-runner" in result.stdout
    assert "--print-compose" in result.stdout
    assert "--directus-image" in result.stdout
    assert "Directus-backed blog smoke" in result.stdout


def test_directus_blog_print_compose_does_not_require_docker(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--print-compose",
            "--reset-state",
            "--directus-image",
            "directus/directus:test",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "services:" in result.stdout
    assert "directus/directus:test" in result.stdout
    assert 'DB_CLIENT: "sqlite3"' in result.stdout
    assert 'STORAGE_LOCAL_ROOT: "/directus/uploads"' in result.stdout
    assert 'BLOG_PROVIDER: "directus"' in result.stdout


def test_directus_blog_public_policy_uses_anonymous_access_row(monkeypatch) -> None:
    module = load_directus_smoke_module()
    calls: list[str] = []

    def fake_directus_request(state, path, **kwargs):
        calls.append(path)
        if path.startswith("/policies?"):
            return True, 200, "", {
                "data": [
                    {
                        "id": "admin-policy",
                        "name": "Administrator",
                        "icon": "verified",
                        "app_access": True,
                        "admin_access": True,
                    },
                    {
                        "id": "anonymous-policy",
                        "name": "$t:public_label",
                        "icon": "public",
                        "app_access": False,
                        "admin_access": False,
                    },
                    {
                        "id": "unassigned-created-policy",
                        "name": "Public",
                        "icon": "public",
                        "app_access": False,
                        "admin_access": False,
                    },
                ]
            }
        if path.startswith("/access?"):
            return True, 200, "", {
                "data": [
                    {
                        "id": "admin-access",
                        "role": "admin-role",
                        "user": None,
                        "policy": "admin-policy",
                    },
                    {
                        "id": "anonymous-access",
                        "role": None,
                        "user": None,
                        "policy": "anonymous-policy",
                    },
                ]
            }
        raise AssertionError(f"unexpected Directus request: {path}")

    monkeypatch.setattr(module, "directus_request", fake_directus_request)

    ok, policy, detail = module.public_policy_id({})

    assert ok, detail
    assert policy == "anonymous-policy"
    assert "anonymous public policy" in detail
    assert not any(path == "/policies" for path in calls)


def test_directus_blog_public_policy_refuses_unassigned_public_policy(monkeypatch) -> None:
    module = load_directus_smoke_module()

    def fake_directus_request(state, path, **kwargs):
        if path.startswith("/policies?"):
            return True, 200, "", {
                "data": [
                    {
                        "id": "unassigned-created-policy",
                        "name": "Public",
                        "icon": "public",
                        "app_access": False,
                        "admin_access": False,
                    },
                ]
            }
        if path.startswith("/access?"):
            return True, 200, "", {"data": []}
        raise AssertionError(f"unexpected Directus request: {path}")

    monkeypatch.setattr(module, "directus_request", fake_directus_request)

    ok, policy, detail = module.public_policy_id({})

    assert not ok
    assert policy == ""
    assert "no Directus anonymous public access row" in detail
