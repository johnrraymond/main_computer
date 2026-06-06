from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer.deployment_controllers import (
    DEFAULT_LOCAL_COOLIFY_ID,
    DeploymentControllerError,
    deployment_controllers_path,
    load_deployment_controller_registry,
    save_deployment_controller_registry,
    site_publish_targets,
    upsert_deployment_controller,
)
from main_computer.website_project_manifest import create_website_project, save_website_publish_target


def test_deployment_controller_registry_seeds_one_local_coolify_smoke_controller(tmp_path: Path) -> None:
    registry = load_deployment_controller_registry(tmp_path)

    assert deployment_controllers_path(tmp_path).exists()
    assert registry.controllers[0].id == DEFAULT_LOCAL_COOLIFY_ID
    assert registry.controllers[0].kind == "coolify"
    assert "dev-services" in registry.controllers[0].roles
    assert "remote-prod" in registry.controllers[0].roles
    assert "local-prod" not in registry.controllers[0].roles
    assert registry.defaults_for("local-prod") == []
    assert registry.defaults_for("remote-prod")[0].id == DEFAULT_LOCAL_COOLIFY_ID


def test_deployment_controller_registry_allows_many_remote_coolify_targets(tmp_path: Path) -> None:
    upsert_deployment_controller(
        tmp_path,
        {
            "id": "coolify-client-a",
            "kind": "coolify",
            "name": "Client A Coolify",
            "base_url": "https://deploy.client-a.example",
            "token_ref": "MAIN_COMPUTER_COOLIFY_CLIENT_A_TOKEN",
            "roles": ["remote-prod"],
        },
    )
    upsert_deployment_controller(
        tmp_path,
        {
            "id": "coolify-client-b",
            "kind": "coolify",
            "name": "Client B Coolify",
            "base_url": "https://deploy.client-b.example",
            "token_ref": "MAIN_COMPUTER_COOLIFY_CLIENT_B_TOKEN",
            "roles": ["remote-prod"],
        },
    )

    registry = load_deployment_controller_registry(tmp_path)
    assert [controller.id for controller in registry.controllers] == [
        "coolify-local",
        "coolify-client-a",
        "coolify-client-b",
    ]
    assert [controller.id for controller in registry.controllers_for_role("remote-prod")] == [
        "coolify-local",
        "coolify-client-a",
        "coolify-client-b",
    ]

    payload = json.loads(deployment_controllers_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["controllers"][1]["base_url"] == "https://deploy.client-a.example"


@pytest.mark.parametrize("bad_url", ["deploy.example.com", "ftp://deploy.example.com", ""])
def test_remote_coolify_url_validation_allows_empty_only_for_intentional_missing_urls(
    tmp_path: Path, bad_url: str
) -> None:
    controller = {
        "id": "coolify-client-a",
        "kind": "coolify",
        "name": "Client A Coolify",
        "base_url": bad_url,
        "roles": ["remote-prod"],
    }

    if bad_url:
        with pytest.raises(DeploymentControllerError):
            upsert_deployment_controller(tmp_path, controller)
    else:
        registry = upsert_deployment_controller(tmp_path, controller)
        assert registry.get("coolify-client-a").base_url == ""


def test_site_publish_targets_default_publish_to_local_coolify_smoke(tmp_path: Path) -> None:
    targets = site_publish_targets({"id": "client-a-site"}, tmp_path)

    assert targets["local_prod"]["controller_id"] == ""
    assert targets["local_prod"]["domain"] == "client-a-site.localhost"
    assert targets["remote_prod"]["controller_id"] == DEFAULT_LOCAL_COOLIFY_ID
    assert targets["remote_prod"]["project"] == "client-a-site"
    assert targets["remote_prod"]["environment"] == "production"


def test_save_website_publish_target_marks_remote_target_accepted(tmp_path: Path) -> None:
    create_website_project(tmp_path, "client-a-site", "Client A Site")

    project = save_website_publish_target(
        tmp_path,
        "client-a-site",
        "remote_prod",
        controller_id=DEFAULT_LOCAL_COOLIFY_ID,
        project="client-a-site",
        environment="production",
        domain="",
    )
    target = project.to_dict(tmp_path)["publish_targets"]["remote_prod"]

    assert target["controller_id"] == DEFAULT_LOCAL_COOLIFY_ID
    assert target["project"] == "client-a-site"
    assert target["environment"] == "production"
    assert target["domain"] == ""
    assert target["accepted_at"]


def test_site_publish_targets_prefer_explicit_non_local_remote_default(tmp_path: Path) -> None:
    upsert_deployment_controller(
        tmp_path,
        {
            "id": "coolify-client-a",
            "kind": "coolify",
            "name": "Client A Coolify",
            "base_url": "https://deploy.client-a.example",
            "roles": ["remote-prod"],
            "default_for": ["remote-prod"],
        },
    )

    targets = site_publish_targets({"id": "client-a-site"}, tmp_path)

    assert targets["local_prod"]["controller_id"] == ""
    assert targets["local_prod"]["domain"] == "client-a-site.localhost"
    assert targets["remote_prod"]["controller_id"] == "coolify-client-a"
    assert targets["remote_prod"]["project"] == "client-a-site"


def test_registry_rejects_duplicate_controller_ids(tmp_path: Path) -> None:
    with pytest.raises(DeploymentControllerError):
        save_deployment_controller_registry(
            tmp_path,
            {
                "controllers": [
                    {
                        "id": "coolify-local",
                        "kind": "coolify",
                        "base_url": "http://localhost:8000",
                        "roles": ["local-prod"],
                    },
                    {
                        "id": "coolify-local",
                        "kind": "coolify",
                        "base_url": "http://localhost:8001",
                        "roles": ["local-prod"],
                    },
                ]
            },
        )


def test_deployment_controller_routes_are_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    routes = (root / "main_computer" / "viewport_route_dispatch.py").read_text(encoding="utf-8")
    handlers = (root / "main_computer" / "viewport_routes_applications.py").read_text(encoding="utf-8")
    app = (root / "main_computer" / "web" / "applications" / "apps" / "website-builder.html").read_text(encoding="utf-8")
    script = (root / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")
    bindings = (root / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "websites.js").read_text(encoding="utf-8")

    assert '"/api/applications/deployment/controllers"' in routes
    assert '"/api/applications/deployment/controller/save"' in routes
    assert '"/api/applications/websites/site/publish-target"' in routes
    assert "def _handle_deployment_controllers" in handlers
    assert "def _handle_deployment_controller_save" in handlers
    assert "def _handle_websites_site_publish_target" in handlers
    assert "Coolify Targets" in app
    assert "website-builder-remote-prod-target" in app
    assert "websiteBuilderRemoteProdTarget" in bindings
    assert "loadWebsiteBuilderDeploymentControllers" in script
    assert "saveWebsiteBuilderRemoteProdTarget" in script
    assert "saveWebsiteBuilderCoolifyRemote" in script
