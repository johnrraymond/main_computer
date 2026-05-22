from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import main_computer.publishing.local_server_prepare as local_server_prepare
from main_computer.publishing.local_server_prepare import (
    LocalPublishSiteDescriptor,
    _applications_coolify_runtime_config,
    _load_coolify_local_docker,
    prepare_local_publish,
)
from main_computer.website_project_manifest import create_website_project, load_website_project, website_publish_plan


@pytest.fixture(autouse=True)
def _local_publish_port_probe_defaults_to_free(monkeypatch) -> None:
    """Keep unit tests deterministic on developer machines with busy local ports."""

    monkeypatch.setattr(local_server_prepare, "_localhost_port_is_free", lambda host, port: True)
    monkeypatch.setattr(local_server_prepare, "_local_publish_status_matches_site", lambda url, site_id: False)


class FakeCoolify:
    def __init__(
        self,
        *,
        infra_ok: bool = True,
        infra_sequence: list[tuple[bool, str]] | None = None,
        up_return_code: int = 0,
        dashboard_url: str = "http://127.0.0.1:8000",
    ) -> None:
        self.infra_ok = infra_ok
        self.infra_sequence = list(infra_sequence or [])
        self.up_return_code = up_return_code
        self.dashboard_url_value = dashboard_url
        self.write_initial_state_calls = 0
        self.up_calls = 0
        self.ensure_api_token_calls = 0
        self.find_service_calls = 0
        self.create_service_calls = 0
        self.ensure_service_calls = 0
        self.last_service_urls: list[str] = []
        self.last_service_name = ""
        self.last_docker_compose_raw = ""
        self.live_docker_compose_raw = ""
        self.service_uuid = "service-uuid"

    def env_file(self, root: Path) -> Path:
        return root / ".local" / "coolify" / ".env"

    def write_initial_state(self, root: Path) -> None:
        self.write_initial_state_calls += 1
        path = self.env_file(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("APP_PORT=8000\n", encoding="utf-8")

    def up(self, root: Path, *, force_init: bool = False) -> int:
        self.up_calls += 1
        return self.up_return_code

    def ensure_infra_status(self, root: Path) -> tuple[bool, str]:
        if self.infra_sequence:
            return self.infra_sequence.pop(0)
        if self.infra_ok:
            return True, "api token ready; localhost server ready; self-SSH deploy path ready"
        return False, "self-SSH deploy path failed"

    def read_api_token(self, root: Path) -> str:
        return "local-token"

    def ensure_api_token(self, root: Path) -> tuple[bool, str, str]:
        self.ensure_api_token_calls += 1
        path = self.api_token_file(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("token=local-token\n", encoding="utf-8")
        return True, f"created/reused local Coolify API token: {path}", "local-token"

    def api_token_file(self, root: Path) -> Path:
        return root / ".local" / "coolify" / "api-token.txt"

    def dashboard_url(self, root: Path) -> str:
        return self.dashboard_url_value

    def local_deploy_target_from_db(self, root: Path) -> tuple[bool, str, dict[str, str]]:
        return True, "local deployment target is ready", {
            "server_uuid": "server-uuid",
            "server_name": "Localhost",
            "server_ip": "127.0.0.1",
            "server_port": "22",
            "destination_uuid": "destination-uuid",
            "destination_name": "Local Docker",
            "network": "coolify",
        }

    def find_local_project_uuid_via_api(self, root: Path, token: str) -> tuple[bool, str, str]:
        return True, "local publish project already exists: project-uuid", "project-uuid"

    def ensure_project_environment_via_api_or_db(
        self,
        root: Path,
        token: str,
        project_uuid: str,
    ) -> tuple[bool, str]:
        return True, "project environment already exists: production (environment-uuid)"

    def find_service_uuid_via_api(self, root: Path, token: str, service_name: str) -> tuple[bool, str, str]:
        self.find_service_calls += 1
        return True, f"service {service_name} does not exist yet", ""

    def create_docker_compose_service_via_api(
        self,
        root: Path,
        token: str,
        project_uuid: str,
        target: dict[str, str],
        *,
        service_name: str,
        description: str,
        docker_compose_raw: str,
        urls: list[str] | None = None,
    ) -> tuple[bool, str, str]:
        self.create_service_calls += 1
        self.last_service_urls = list(urls or [])
        self.last_service_name = service_name
        self.last_docker_compose_raw = docker_compose_raw
        assert service_name.startswith("main-computer-")
        assert "services:" in docker_compose_raw
        assert "main-computer-site-client-a-site-prod:latest" in docker_compose_raw
        assert "pull_policy: build" in docker_compose_raw
        return True, f"created service {service_name}: {self.service_uuid}", self.service_uuid

    def ensure_docker_compose_service_via_api(
        self,
        root: Path,
        token: str,
        project_uuid: str,
        target: dict[str, str],
        *,
        service_name: str,
        description: str,
        docker_compose_raw: str,
        urls: list[str] | None = None,
    ) -> tuple[bool, str, str]:
        self.ensure_service_calls += 1
        return self.create_docker_compose_service_via_api(
            root,
            token,
            project_uuid,
            target,
            service_name=service_name,
            description=description,
            docker_compose_raw=docker_compose_raw,
            urls=urls,
        )

    def coolify_api_get(self, root: Path, path: str, token: str) -> tuple[bool, str, object]:
        if path != f"/v1/services/{self.service_uuid}":
            return False, f"unexpected service path: {path}", {}
        compose_raw = self.live_docker_compose_raw or self.last_docker_compose_raw
        return True, "service read-back ok", {
            "uuid": self.service_uuid,
            "name": self.last_service_name,
            "status": "running:unknown",
            "docker_compose_raw": compose_raw,
            "docker_compose": compose_raw,
        }



def test_windows_bind_sources_are_rendered_as_docker_host_paths() -> None:
    fixture_user = "FixtureUser"
    assert (
        local_server_prepare._windows_path_to_docker_desktop_host_path(
            f"C:/Users/{fixture_user}/dsl/main_computer_test/runtime/websites"
        )
        == f"/run/desktop/mnt/host/c/Users/{fixture_user}/dsl/main_computer_test/runtime/websites"
    )
    assert (
        local_server_prepare._windows_path_to_docker_desktop_host_path(
            r"D:\Projects\main_computer_test\runtime\websites",
            mount_root="/custom/mnt",
        )
        == "/custom/mnt/d/Projects/main_computer_test/runtime/websites"
    )


def test_local_publish_url_skips_occupied_non_site_port(monkeypatch) -> None:
    checked: list[str] = []

    def fake_candidate_is_usable(url: str, site_id: object) -> bool:
        checked.append(url)
        return not url.startswith("http://127.0.0.1:18084/")

    monkeypatch.setattr(local_server_prepare, "_local_publish_candidate_is_usable", fake_candidate_is_usable)

    assert (
        local_server_prepare._local_publish_url_for_view_url("http://127.0.0.1:18080/", "hub-site")
        == "http://127.0.0.1:18085/"
    )
    assert checked[:2] == ["http://127.0.0.1:18084/", "http://127.0.0.1:18085/"]


def test_local_publish_candidate_reuses_matching_site_status(monkeypatch) -> None:
    monkeypatch.setattr(local_server_prepare, "_localhost_port_is_free", lambda host, port: False)
    monkeypatch.setattr(local_server_prepare, "_local_publish_status_matches_site", lambda url, site_id: site_id == "hub-site")

    assert local_server_prepare._local_publish_candidate_is_usable("http://127.0.0.1:18084/", "hub-site") is True
    assert local_server_prepare._local_publish_candidate_is_usable("http://127.0.0.1:18084/", "blog-site") is False


def test_local_coolify_publish_compose_carries_current_site_server_contract(tmp_path: Path) -> None:
    site_server = tmp_path / "deploy" / "local-platform" / "site-server"
    site_server.mkdir(parents=True)
    (site_server / "Dockerfile").write_text("FROM python:3.12-slim\nCOPY app.py /app/app.py\n", encoding="utf-8")
    (site_server / "app.py").write_text("print('current site server')\n", encoding="utf-8")
    site = LocalPublishSiteDescriptor(
        site_id="client-a-site",
        name="Client A Site",
        kind="static-site",
        service_name="main-computer-client-a-site-local-publish",
        preview_url="http://127.0.0.1:18084/",
    )

    compose = local_server_prepare._site_publish_compose_raw(tmp_path, site)

    assert "build:" in compose
    assert 'context: "./site-server"' in compose
    assert f"context: \"{(site_server.resolve()).as_posix()}\"" not in compose
    assert "main-computer-site-client-a-site-prod:latest" in compose
    assert "pull_policy: build" in compose
    assert '127.0.0.1:18084:8080' in compose
    assert "/app/app.py:ro" not in compose
    assert "MC_SITE_SERVER_DIGEST" in compose
    assert "main-computer.site-server.digest=" in compose


def test_stage_local_publish_build_context_uses_root_and_container_sha256sum(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site_server = tmp_path / "deploy" / "local-platform" / "site-server"
    site_server.mkdir(parents=True)
    dockerfile = site_server / "Dockerfile"
    app = site_server / "app.py"
    dockerfile.write_bytes(b"FROM python:3.12-slim\nCOPY app.py /app/app.py\n")
    app.write_bytes(b"print('staged bytes must be verified directly')\n")

    class Adapter:
        def coolify_container_names(self, root: Path) -> dict[str, str]:
            return {"coolify": "coolify-test"}

    calls: list[list[str]] = []

    def fake_run(command, *, text: bool, capture_output: bool, timeout: float):
        calls.append(list(command))
        if command[:5] == ["docker", "exec", "--user", "root", "coolify-test"]:
            script = command[-1]
            if "sha256sum Dockerfile app.py" in script:
                stdout = (
                    f"{local_server_prepare.hashlib.sha256(dockerfile.read_bytes()).hexdigest()}  Dockerfile\n"
                    f"{local_server_prepare.hashlib.sha256(app.read_bytes()).hexdigest()}  app.py\n"
                )
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["docker", "cp"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="unexpected command")

    monkeypatch.setattr(local_server_prepare.subprocess, "run", fake_run)

    result = local_server_prepare._stage_local_publish_build_context(
        adapter=Adapter(),
        root=tmp_path,
        service_uuid="service-uuid",
    )

    assert result["ok"] is True
    assert result["staged_sha256"] == result["expected_sha256"]
    assert result["commands"][0]["op"] == "mkdir"
    assert result["commands"][0]["user"] == "root"
    exec_calls = [call for call in calls if call[:2] == ["docker", "exec"]]
    assert exec_calls[0][:5] == ["docker", "exec", "--user", "root", "coolify-test"]
    assert exec_calls[-1][:5] == ["docker", "exec", "--user", "root", "coolify-test"]
    assert "sha256sum Dockerfile app.py" in exec_calls[-1][-1]
    assert "cat " not in exec_calls[-1][-1]


def test_applications_coolify_runtime_config_reuses_applications_stack(tmp_path: Path) -> None:
    env_path = tmp_path / "runtime" / "applications_service" / "applications.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "\n".join(
            [
                "COOLIFY_COMPOSE_PROJECT=main-computer-applications",
                f"COOLIFY_LOCAL_STATE={tmp_path / 'runtime' / 'applications_service' / 'coolify'}",
                "APP_PORT=17056",
                "SOKETI_PORT=17156",
                "SOKETI_TERMINAL_PORT=17256",
                "COOLIFY_NETWORK_NAME=main-computer-applications_default",
                "COOLIFY_CONTAINER_NAME=mc-applications-coolify",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = _applications_coolify_runtime_config(tmp_path)

    assert config == {
        "project_name": "main-computer-applications",
        "state_dir": str(tmp_path / "runtime" / "applications_service" / "coolify"),
        "app_port": "17056",
        "soketi_port": "17156",
        "soketi_terminal_port": "17256",
        "network_name": "main-computer-applications_default",
        "container_prefix": "mc-applications-coolify",
    }


def test_loaded_coolify_helper_binds_to_applications_runtime_config(tmp_path: Path) -> None:
    tool = tmp_path / "tools" / "local-prod" / "coolify-local-docker.py"
    tool.parent.mkdir(parents=True)
    tool.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "_RUNTIME_CONFIG = {}",
                "def env_file(root: Path) -> Path:",
                "    return Path(root) / 'fake.env'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    env_path = tmp_path / "runtime" / "applications_service" / "applications.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "\n".join(
            [
                "COOLIFY_COMPOSE_PROJECT=main-computer-applications",
                "COOLIFY_LOCAL_STATE=runtime/applications_service/coolify",
                "APP_PORT=17056",
                "SOKETI_PORT=17156",
                "SOKETI_TERMINAL_PORT=17256",
                "",
            ]
        ),
        encoding="utf-8",
    )

    helper = _load_coolify_local_docker(tmp_path)

    assert helper._RUNTIME_CONFIG["project_name"] == "main-computer-applications"
    assert helper._RUNTIME_CONFIG["state_dir"] == "runtime/applications_service/coolify"
    assert helper._RUNTIME_CONFIG["app_port"] == "17056"
    assert helper._RUNTIME_CONFIG["soketi_port"] == "17156"
    assert helper._RUNTIME_CONFIG["soketi_terminal_port"] == "17256"
    assert helper._MAIN_COMPUTER_APPLICATIONS_RUNTIME_CONFIG["project_name"] == "main-computer-applications"


def test_prepare_local_publish_returns_structured_ready_result(tmp_path: Path, monkeypatch) -> None:
    coolify = FakeCoolify()
    monkeypatch.setattr(local_server_prepare, "_docker_host_bind_source", lambda path: f"/docker-host/{path.name}")
    result = prepare_local_publish(
        tmp_path,
        site=LocalPublishSiteDescriptor(
            site_id="client-a-site",
            name="Client A Site",
            kind="static-site",
            domain="client-a-site.localhost",
            source_path=str(tmp_path / "runtime" / "websites" / "client-a-site"),
            service_name="main-computer-client-a-site-local-publish",
            preview_url="http://127.0.0.1:18084/",
        ),
        coolify=coolify,
    )

    payload = result.to_dict()

    assert payload["ok"] is True
    assert payload["stage"] == "ready"
    assert payload["dashboard_url"] == "http://127.0.0.1:8000"
    assert payload["api_token_path"].endswith("api-token.txt")
    assert payload["project_uuid"] == "project-uuid"
    assert payload["environment_uuid"] == "environment-uuid"
    assert payload["service_uuid"] == "service-uuid"
    assert payload["ready_for_deploy"] is True
    assert payload["preview_url"] == "http://127.0.0.1:18084/"
    assert payload["details"]["service_reconciliation"] == "ready"
    assert payload["publish_ready_contract"]["deploy_method"] == "coolify_deploy_api_only"
    assert payload["publish_ready_contract"]["connected_to_local_server_surface"] is True
    assert payload["publish_ready_contract"]["live_service_verification"]["ok"] is True
    assert payload["publish_ready_contract"]["live_service_verification"]["checks"]["raw_mounts_host_runtime_websites"] is True
    assert payload["publish_ready_contract"]["live_service_verification"]["expected"]["runtime_websites_mount"] == "/docker-host/websites:/app/runtime/websites:ro"
    assert "/docker-host/websites:/app/runtime/websites:ro" in coolify.last_docker_compose_raw
    assert payload["publish_ready_contract"]["live_service_verification"]["checks"]["raw_uses_relative_build_context"] is True
    assert payload["publish_ready_contract"]["live_service_verification"]["checks"]["raw_has_pull_policy_build"] is True
    assert payload["publish_ready_contract"]["live_service_verification"]["expected"]["build_context"] == "./site-server"
    assert payload["publish_ready_contract"]["live_service_verification"]["expected"]["pull_policy"] == "build"
    assert payload["publish_ready_contract"]["build_context_staging"]["ok"] is True
    publish_button_contract = payload["publish_ready_contract"]["publish_button_contract"]
    assert publish_button_contract.startswith("POST /api/v1/deploy")
    assert "fallback GET /api/v1/deploy?uuid=<prepared_resource_uuid>&force=true" in publish_button_contract
    assert payload["publish_ready_contract"]["coolify_urls"] == []
    assert coolify.write_initial_state_calls == 1
    assert coolify.find_service_calls == 0
    assert coolify.ensure_service_calls == 1
    assert coolify.create_service_calls == 1
    assert coolify.last_service_urls == []

    state_path = Path(payload["details"]["state_path"])
    assert state_path.is_file()
    assert "client-a-site" in state_path.name
    state_text = state_path.read_text(encoding="utf-8")
    assert '"service_name": "main-computer-client-a-site-local-publish"' in state_text
    assert '"ready_for_deploy": true' in state_text
    assert '"service_uuid": "service-uuid"' in state_text
    assert '"deploy_method": "coolify_deploy_api_only"' in state_text
    assert '"connected_to_local_server_surface": true' in state_text





def test_prepare_local_publish_falls_back_to_registry_view_url_when_site_manifest_is_missing(tmp_path: Path) -> None:
    from main_computer.local_platform_registry import default_registry_data, save_local_platform_registry

    registry = default_registry_data()
    registry["sites"]["client-a-site"] = {
        "id": "client-a-site",
        "name": "Client A Site",
        "kind": "static-site",
        "repo_relative_path": "runtime/websites/client-a-site",
        "lanes": {
            "prod": {
                "service": "client-a-site-local",
                "port": 32123,
                "url": "http://localhost:32123/",
                "status_url": "http://localhost:32123/api/site/status",
            },
            "dev": {
                "service": "client-a-site-dev",
                "port": 32124,
                "url": "http://localhost:32124/",
                "status_url": "http://localhost:32124/api/site/status",
            },
        },
    }
    save_local_platform_registry(tmp_path, registry)
    coolify = FakeCoolify()

    result = prepare_local_publish(
        tmp_path,
        site={
            "site_id": "client-a-site",
            "name": "Client A Site",
            "kind": "static-site",
            "domain": "client-a-site.localhost",
            "source_path": str(tmp_path / "runtime" / "websites" / "client-a-site"),
        },
        coolify=coolify,
    )

    payload = result.to_dict()

    assert payload["ok"] is True
    assert payload["preview_url"] == "http://127.0.0.1:32127/"
    assert payload["site"]["domain"] == "http://127.0.0.1:32127/"
    assert payload["site"]["preview_url"] == "http://127.0.0.1:32127/"
    assert payload["publishing_setup"]["published_host_domain"] == "http://127.0.0.1:32127/"
    assert payload["publishing_setup"]["publish_url"] == "http://127.0.0.1:32127/"
    assert payload["publish_ready_contract"]["publish_url"] == "http://127.0.0.1:32127/"
    assert payload["publish_ready_contract"]["coolify_urls"] == []
    assert coolify.last_service_urls == []
    assert any(
        stage["stage"] == "binding_local_server_view_url" and stage["ok"] is True
        for stage in payload["details"]["stages"]
    )
    assert "client-a-site.localhost" not in json.dumps(payload)


def test_prepare_local_publish_uses_local_server_view_url_for_prepare_contract(tmp_path: Path) -> None:
    create_website_project(
        tmp_path,
        "client-a-site",
        "Client A Site",
        manifest={
            "id": "client-a-site",
            "name": "Client A Site",
            "kind": "static-site",
            "lane": "local",
            "local_platform": {
                "local_url": "http://0.0.0.0:32123/",
                "lanes": {
                    "local": {
                        "service": "client-a-site-local",
                        "port": 32123,
                        "url": "http://0.0.0.0:32123/",
                        "status_url": "http://0.0.0.0:32123/api/site/status",
                    }
                },
            },
            "publish_targets": {
                "local_prod": {
                    "controller_id": "",
                    "project": "client-a-site",
                    "environment": "local-prod",
                    "domain": "client-a-site.localhost",
                },
                "remote_prod": {
                    "controller_id": "coolify-local",
                    "project": "client-a-site",
                    "environment": "production",
                    "domain": "",
                },
            },
        },
        overwrite=True,
    )
    coolify = FakeCoolify()

    result = prepare_local_publish(
        tmp_path,
        site={
            "site_id": "client-a-site",
            "name": "Client A Site",
            "kind": "static-site",
            "domain": "client-a-site.localhost",
            "source_path": str(tmp_path / "runtime" / "websites" / "client-a-site"),
        },
        coolify=coolify,
    )

    payload = result.to_dict()
    project = load_website_project(tmp_path, "client-a-site")
    remote_target = project.to_dict(tmp_path)["publish_targets"]["remote_prod"]

    assert payload["ok"] is True
    assert payload["preview_url"] == "http://127.0.0.1:32127/"
    assert payload["site"]["domain"] == "http://127.0.0.1:32127/"
    assert payload["site"]["preview_url"] == "http://127.0.0.1:32127/"
    assert payload["publishing_setup"]["published_host_domain"] == "http://127.0.0.1:32127/"
    assert payload["publishing_setup"]["publish_url"] == "http://127.0.0.1:32127/"
    assert payload["publish_ready_contract"]["publish_url"] == "http://127.0.0.1:32127/"
    assert payload["accepted_publish_target"]["domain"] == "http://127.0.0.1:32127/"
    assert remote_target["domain"] == "http://127.0.0.1:32127/"
    assert website_publish_plan(tmp_path, "client-a-site", "remote-prod")["url"] == "http://127.0.0.1:32127/"
    assert coolify.last_service_urls == []
    assert "client-a-site.localhost" not in json.dumps(payload)
    assert any(
        stage["stage"] == "binding_local_server_view_url" and stage["ok"] is True
        for stage in payload["details"]["stages"]
    )


def test_prepare_local_publish_autoheals_coolify_dashboard_url_before_recovery(tmp_path: Path, monkeypatch) -> None:
    create_website_project(
        tmp_path,
        "client-a-site",
        "Client A Site",
        manifest={
            "id": "client-a-site",
            "name": "Client A Site",
            "kind": "static-site",
            "lane": "local",
            "local_platform": {
                "local_url": "http://127.0.0.1:18080/",
                "lanes": {
                    "local": {
                        "service": "client-a-site-local",
                        "port": 18080,
                        "url": "http://127.0.0.1:18080/",
                        "status_url": "http://127.0.0.1:18080/api/site/status",
                    }
                },
            },
            "publish_targets": {
                "remote_prod": {
                    "controller_id": "coolify-local",
                    "project": "client-a-site",
                    "environment": "production",
                    "domain": "",
                },
            },
        },
        overwrite=True,
    )

    configured_url = "http://127.0.0.1:17056"
    mapped_url = "http://127.0.0.1:27056"
    coolify = FakeCoolify(
        infra_sequence=[
            (
                False,
                "Coolify health failed: [WinError 10061] No connection could be made because the target machine actively refused it",
            ),
            (True, "api token ready after dashboard URL auto-heal; localhost server ready; self-SSH deploy path ready"),
        ],
        dashboard_url=configured_url,
    )

    def fake_probe(dashboard_url: object, *, timeout: float = 3.0) -> dict[str, object]:
        normalized = str(dashboard_url or "").rstrip("/")
        return {
            "ok": normalized == mapped_url,
            "status": 200 if normalized == mapped_url else None,
            "url": f"{normalized}/api/health",
            "error": "" if normalized == mapped_url else "connection refused",
        }

    def fake_docker_probe(
        adapter: object,
        root: Path,
        *,
        container_port: str = "8080/tcp",
    ) -> dict[str, object]:
        return {
            "ok": True,
            "container": "mc-applications-coolify",
            "container_port": container_port,
            "docker_port": {
                "ok": True,
                "returncode": 0,
                "stdout": "127.0.0.1:27056",
                "stderr": "",
                "command": ["docker", "port", "mc-applications-coolify", container_port],
            },
            "docker_mapped_port": 27056,
            "dashboard_url": mapped_url,
            "health": {"ok": True, "status": 200, "url": f"{mapped_url}/api/health"},
        }

    monkeypatch.setattr(local_server_prepare, "_probe_coolify_dashboard_health", fake_probe)
    monkeypatch.setattr(local_server_prepare, "_docker_mapped_coolify_dashboard_probe", fake_docker_probe)

    result = prepare_local_publish(
        tmp_path,
        site={
            "site_id": "client-a-site",
            "domain": "client-a-site.localhost",
            "source_path": str(tmp_path / "runtime" / "websites" / "client-a-site"),
        },
        coolify=coolify,
    )

    payload = result.to_dict()
    remote_plan = website_publish_plan(tmp_path, "client-a-site", "remote-prod")
    stages = payload["details"]["stages"]

    assert payload["ok"] is True
    assert payload["dashboard_url"] == mapped_url
    assert payload["credential"]["base_url"] == mapped_url
    assert payload["deployment_controller"]["base_url"] == mapped_url
    assert payload["publishing_setup"]["publishing_server_url"] == mapped_url
    assert remote_plan["controller"]["base_url"] == mapped_url
    assert remote_plan["deploy_url"] == f"{mapped_url}/api/v1/deploy?uuid=service-uuid&force=true"
    assert payload["publishing_setup"]["publish_url"] == "http://127.0.0.1:18084/"
    assert payload["publish_ready_contract"]["publish_url"] == "http://127.0.0.1:18084/"
    assert payload["accepted_publish_target"]["domain"] == "http://127.0.0.1:18084/"
    assert coolify.dashboard_url(tmp_path) == mapped_url
    assert coolify.up_calls == 0
    assert any(stage["stage"] == "autohealing_coolify_dashboard_url" and stage["ok"] is True for stage in stages)
    assert any(
        stage["stage"] == "checking_local_coolify_infrastructure_after_dashboard_url_autoheal"
        and stage["ok"] is True
        for stage in stages
    )


def test_prepare_local_publish_recovers_when_coolify_health_port_is_refused(tmp_path: Path) -> None:
    coolify = FakeCoolify(
        infra_sequence=[
            (False, "Coolify health failed: [WinError 10061] No connection could be made because the target machine actively refused it"),
            (True, "api token ready after recovery; localhost server ready; self-SSH deploy path ready"),
        ],
        up_return_code=0,
    )

    result = prepare_local_publish(
        tmp_path,
        site={"site_id": "client-a-site", "domain": "http://127.0.0.1:18084/", "preview_url": "http://127.0.0.1:18084/"},
        coolify=coolify,
    )

    payload = result.to_dict()
    stages = payload["details"]["stages"]

    assert payload["ok"] is True
    assert payload["stage"] == "ready"
    assert coolify.up_calls == 1
    assert any(stage["stage"] == "recovering_local_coolify_stack" and stage["ok"] is True for stage in stages)
    assert any(stage["stage"] == "checking_local_coolify_infrastructure_after_recovery" and stage["ok"] is True for stage in stages)


def test_prepare_local_publish_reports_recovery_failure(tmp_path: Path) -> None:
    coolify = FakeCoolify(
        infra_sequence=[
            (False, "Coolify health failed: timed out"),
        ],
        up_return_code=1,
    )

    result = prepare_local_publish(
        tmp_path,
        site={"site_id": "client-a-site", "domain": "http://127.0.0.1:18084/", "preview_url": "http://127.0.0.1:18084/"},
        coolify=coolify,
    )

    payload = result.to_dict()

    assert payload["ok"] is False
    assert payload["stage"] == "recovering_local_coolify_stack"
    assert "recovery failed with exit code 1" in payload["message"]
    assert coolify.up_calls == 1


def test_prepare_local_publish_surfaces_infra_stage_failure(tmp_path: Path) -> None:
    result = prepare_local_publish(
        tmp_path,
        site={"site_id": "client-a-site", "domain": "http://127.0.0.1:18084/", "preview_url": "http://127.0.0.1:18084/"},
        coolify=FakeCoolify(infra_ok=False),
    )

    payload = result.to_dict()

    assert payload["ok"] is False
    assert payload["stage"] == "checking_local_coolify_infrastructure"
    assert payload["ready_for_deploy"] is False
    assert "self-SSH deploy path failed" in payload["message"]
    assert payload["site"]["service_name"] == "main-computer-client-a-site-local-publish"


def test_prepare_local_publish_accepts_prepared_resource_for_publish_deploy(tmp_path: Path) -> None:
    create_website_project(tmp_path, "client-a-site", "Client A Site", overwrite=True)
    coolify = FakeCoolify()

    result = prepare_local_publish(
        tmp_path,
        site=LocalPublishSiteDescriptor(
            site_id="client-a-site",
            name="Client A Site",
            kind="static-site",
            domain="client-a-site.localhost",
            source_path=str(tmp_path / "runtime" / "websites" / "client-a-site"),
            service_name="main-computer-client-a-site-local-publish",
            preview_url="http://127.0.0.1:18084/",
        ),
        coolify=coolify,
    )

    payload = result.to_dict()
    project = load_website_project(tmp_path, "client-a-site")
    remote_target = project.to_dict(tmp_path)["publish_targets"]["remote_prod"]

    assert payload["ok"] is True
    assert payload["ready_for_deploy"] is True
    assert remote_target["controller_id"] == "coolify-local"
    assert remote_target["resource_uuid"] == "service-uuid"
    assert remote_target["service_uuid"] == "service-uuid"
    assert remote_target["uuid"] == "service-uuid"
    assert remote_target["domain"] == "http://127.0.0.1:18084/"
    assert payload["accepted_publish_target"]["resource_uuid"] == "service-uuid"
    assert any(
        stage["stage"] == "accepting_prepared_publish_target" and stage["ok"] is True
        for stage in payload["details"]["stages"]
    )


def test_prepare_local_publish_accepts_unquoted_live_coolify_yaml(tmp_path: Path) -> None:
    coolify = FakeCoolify()

    original_create = coolify.create_docker_compose_service_via_api

    def create_with_unquoted_readback(*args, **kwargs):
        ok, detail, uuid = original_create(*args, **kwargs)
        coolify.live_docker_compose_raw = (
            coolify.last_docker_compose_raw
            .replace('SITE_ID: "client-a-site"', 'SITE_ID: client-a-site')
            .replace('MC_SITE_ID: "client-a-site"', 'MC_SITE_ID: client-a-site')
            .replace('CONTENT_ROOT: "/app/runtime/websites"', 'CONTENT_ROOT: /app/runtime/websites')
        )
        return ok, detail, uuid

    coolify.create_docker_compose_service_via_api = create_with_unquoted_readback  # type: ignore[method-assign]

    result = prepare_local_publish(
        tmp_path,
        site=LocalPublishSiteDescriptor(
            site_id="client-a-site",
            name="Client A Site",
            kind="static-site",
            domain="client-a-site.localhost",
            source_path=str(tmp_path / "runtime" / "websites" / "client-a-site"),
            service_name="main-computer-client-a-site-local-publish",
            preview_url="http://127.0.0.1:18084/",
        ),
        coolify=coolify,
    )

    payload = result.to_dict()

    assert payload["ok"] is True
    verification = payload["publish_ready_contract"]["live_service_verification"]
    assert verification["ok"] is True
    assert verification["checks"]["raw_sets_site_id"] is True
    assert verification["checks"]["raw_sets_mc_site_id"] is True
    assert verification["checks"]["raw_sets_content_root"] is True
    assert verification["checks"]["raw_has_pull_policy_build"] is True




def test_prepare_local_publish_refuses_live_coolify_service_without_build_pull_policy(tmp_path: Path) -> None:
    create_website_project(tmp_path, "client-a-site", "Client A Site", overwrite=True)
    coolify = FakeCoolify()

    original_create = coolify.create_docker_compose_service_via_api

    def create_without_build_pull_policy(*args, **kwargs):
        ok, detail, uuid = original_create(*args, **kwargs)
        coolify.live_docker_compose_raw = coolify.last_docker_compose_raw.replace("    pull_policy: build\n", "")
        return ok, detail, uuid

    coolify.create_docker_compose_service_via_api = create_without_build_pull_policy  # type: ignore[method-assign]

    result = prepare_local_publish(
        tmp_path,
        site=LocalPublishSiteDescriptor(
            site_id="client-a-site",
            name="Client A Site",
            kind="static-site",
            domain="client-a-site.localhost",
            source_path=str(tmp_path / "runtime" / "websites" / "client-a-site"),
            service_name="main-computer-client-a-site-local-publish",
            preview_url="http://127.0.0.1:18084/",
        ),
        coolify=coolify,
    )

    payload = result.to_dict()
    project = load_website_project(tmp_path, "client-a-site")
    remote_target = project.to_dict(tmp_path)["publish_targets"]["remote_prod"]

    assert payload["ok"] is False
    assert payload["stage"] == "preparing_local_publish_service"
    assert "not connected to the Local Server publish surface" in payload["message"]
    verification = payload["details"]["stages"][-1]["publish_ready_contract"]["live_service_verification"]
    assert verification["checks"]["raw_has_pull_policy_build"] is False
    assert "raw_has_pull_policy_build" in verification["issues"]
    assert payload["ready_for_deploy"] is False
    assert remote_target.get("accepted_at", "") == ""
    assert remote_target.get("resource_uuid", "") == ""

def test_prepare_local_publish_refuses_detached_live_coolify_service(tmp_path: Path) -> None:
    create_website_project(tmp_path, "client-a-site", "Client A Site", overwrite=True)
    coolify = FakeCoolify()

    original_create = coolify.create_docker_compose_service_via_api

    def create_with_detached_readback(*args, **kwargs):
        ok, detail, uuid = original_create(*args, **kwargs)
        coolify.live_docker_compose_raw = """
services:
  main-computer-client-a-site-local-publish:
    image: "main-computer-site-client-a-site-prod:latest"
    environment:
      SITE_ID: "wrong-site"
      MC_SITE_ID: "wrong-site"
      CONTENT_ROOT: "/not-the-local-server-surface"
    volumes:
      - "/tmp/detached:/app/runtime/websites:ro"
"""
        return ok, detail, uuid

    coolify.create_docker_compose_service_via_api = create_with_detached_readback  # type: ignore[method-assign]

    result = prepare_local_publish(
        tmp_path,
        site=LocalPublishSiteDescriptor(
            site_id="client-a-site",
            name="Client A Site",
            kind="static-site",
            domain="client-a-site.localhost",
            source_path=str(tmp_path / "runtime" / "websites" / "client-a-site"),
            service_name="main-computer-client-a-site-local-publish",
            preview_url="http://127.0.0.1:18084/",
        ),
        coolify=coolify,
    )

    payload = result.to_dict()
    project = load_website_project(tmp_path, "client-a-site")
    remote_target = project.to_dict(tmp_path)["publish_targets"]["remote_prod"]

    assert payload["ok"] is False
    assert payload["stage"] == "preparing_local_publish_service"
    assert "not connected to the Local Server publish surface" in payload["message"]
    assert payload["ready_for_deploy"] is False
    assert remote_target.get("accepted_at", "") == ""
    assert remote_target.get("resource_uuid", "") == ""
    assert not (tmp_path / "runtime" / "publishing" / "local-server" / "client-a-site.json").exists()


def _create_blog_ready_site(tmp_path: Path) -> None:
    create_website_project(
        tmp_path,
        "client-a-site",
        "Client A Site",
        manifest={
            "id": "client-a-site",
            "name": "Client A Site",
            "kind": "hub-site",
            "lane": "local",
            "local_platform": {
                "local_url": "http://127.0.0.1:32123/",
                "lanes": {
                    "local": {
                        "service": "client-a-site-local",
                        "port": 32123,
                        "url": "http://127.0.0.1:32123/",
                        "status_url": "http://127.0.0.1:32123/api/site/status",
                    }
                },
            },
            "features": {
                "blog": {
                    "enabled": True,
                    "selected": True,
                    "install_status": "ready",
                    "page": {
                        "route": "/blog",
                        "path": "runtime/websites/client-a-site/blog/index.html",
                        "status": "ready",
                    },
                }
            },
            "blog_page": {
                "route": "/blog",
                "path": "runtime/websites/client-a-site/blog/index.html",
                "status": "ready",
            },
            "publish_targets": {
                "remote_prod": {
                    "controller_id": "coolify-local",
                    "project": "client-a-site",
                    "environment": "production",
                    "domain": "",
                },
            },
        },
        overwrite=True,
    )
    blog_dir = tmp_path / "runtime" / "websites" / "client-a-site" / "blog"
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "index.html").write_text("<h1>Blog</h1>\n", encoding="utf-8")


def test_prepare_local_publish_accepts_blog_site_without_pre_publish_route_probe(tmp_path: Path) -> None:
    _create_blog_ready_site(tmp_path)
    coolify = FakeCoolify()

    result = prepare_local_publish(tmp_path, "client-a-site", coolify=coolify)

    payload = result.to_dict()
    project = load_website_project(tmp_path, "client-a-site")
    remote_target = project.to_dict(tmp_path)["publish_targets"]["remote_prod"]
    stages = [stage["stage"] for stage in payload["details"]["stages"]]

    assert payload["ok"] is True
    assert payload["stage"] == "ready"
    assert payload["ready_for_deploy"] is True
    assert "verifying_local_server_visit_surface" not in stages
    assert "visit_surface_verification" not in payload["publish_ready_contract"]
    assert payload["publish_ready_contract"]["live_service_verification"]["checks"]["raw_has_pull_policy_build"] is True
    assert remote_target["resource_uuid"] == "service-uuid"
    assert (tmp_path / "runtime" / "publishing" / "local-server" / "client-a-site.json").exists()
