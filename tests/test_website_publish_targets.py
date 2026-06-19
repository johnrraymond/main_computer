from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer.deployment_controllers import DEFAULT_LOCAL_COOLIFY_ID, upsert_deployment_controller
from main_computer.website_project_manifest import (
    WebsiteProjectError,
    create_website_project,
    load_website_project,
    save_website_directus_connection,
    save_website_publish_target,
)


def test_new_website_manifest_keeps_local_prod_direct_and_publish_command_blank_by_default(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "client-a-site", "Client A Site")
    payload = project.to_dict(tmp_path)

    assert payload["publish_targets"]["local_prod"]["controller_id"] == ""
    assert payload["publish_targets"]["local_prod"]["domain"] == "client-a-site.localhost"
    assert payload["publish_targets"]["remote_prod"]["controller_id"] == ""
    assert payload["publish_targets"]["remote_prod"]["environment"] == "production"
    assert payload["publish_targets"]["remote_prod"]["publish_mode"] == "scp"
    assert payload["publish_targets"]["remote_prod"]["site_slug"] == "client-a-site"
    assert payload["publish_targets"]["remote_prod"]["source_path"] == "runtime/websites/client-a-site"


def test_site_can_choose_a_remote_coolify_publish_target(tmp_path: Path) -> None:
    create_website_project(tmp_path, "client-a-site", "Client A Site")
    upsert_deployment_controller(
        tmp_path,
        {
            "id": "coolify-client-a",
            "kind": "coolify",
            "name": "Client A Coolify",
            "base_url": "https://deploy.client-a.example",
            "roles": ["remote-prod"],
        },
    )

    save_website_publish_target(
        tmp_path,
        "client-a-site",
        "remote_prod",
        controller_id="coolify-client-a",
        project="client-a-site",
        environment="production",
        domain="www.client-a.example",
    )

    project = load_website_project(tmp_path, "client-a-site")
    payload = project.to_dict(tmp_path)
    remote = payload["publish_targets"]["remote_prod"]
    assert remote["controller_id"] == "coolify-client-a"
    assert remote["project"] == "client-a-site"
    assert remote["environment"] == "production"
    assert remote["domain"] == "www.client-a.example"


def test_site_saves_scp_publish_command_config_without_conflating_slug_and_source(tmp_path: Path) -> None:
    create_website_project(tmp_path, "hub-site", "Hub Site")

    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        publish_mode="scp",
        site_slug="johnrraymond",
        project="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
        ssh_password="secret-password",
        environment="production",
        domain="https://johnrraymond.example.com",
    )

    payload = load_website_project(tmp_path, "hub-site").to_dict(tmp_path)
    remote = payload["publish_targets"]["remote_prod"]
    assert remote["publish_mode"] == "scp"
    assert remote["use_local_server"] is False
    assert remote["site_slug"] == "johnrraymond"
    assert remote["project"] == "johnrraymond"
    assert remote["source_path"] == "runtime/websites/hub-site"
    assert remote["remote_host"] == ""
    assert remote["remote_root"] == "/srv/main-computer/sites"
    assert remote["ssh_password_file"] == "runtime/websites/hub-site/ssh_password.local"
    assert "ssh_password" not in remote
    raw_manifest = json.loads((tmp_path / "runtime" / "websites" / "hub-site" / "site.json").read_text(encoding="utf-8"))
    assert "remote_host" not in raw_manifest["publish_targets"]["remote_prod"]
    local_secret = json.loads((tmp_path / "runtime" / "websites" / "hub-site" / "ssh_password.local").read_text(encoding="utf-8"))
    assert local_secret == {
        "remote_host": "root@publish.greatlibrary.io",
        "ssh_password": "secret-password",
    }



def test_site_ignores_legacy_inline_ssh_password(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "hub-site", "Hub Site")
    manifest = dict(project.manifest)
    manifest["publish_targets"] = {
        "remote_prod": {
            "accepted_at": "2026-01-01T00:00:00+00:00",
            "publish_mode": "scp",
            "site_slug": "johnrraymond",
            "source_path": "runtime/websites/hub-site",
            "remote_host": "root@publish.greatlibrary.io",
            "remote_root": "/srv/main-computer/sites",
            "ssh_password": "legacy-secret",
        }
    }
    (project.path / "site.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    remote = load_website_project(tmp_path, "hub-site").to_dict(tmp_path)["publish_targets"]["remote_prod"]

    assert "ssh_password" not in remote
    assert remote["ssh_password_file"] == "runtime/websites/hub-site/ssh_password.local"

def test_site_saves_local_server_publish_command_mode_without_remote_ssh_fields(tmp_path: Path) -> None:
    create_website_project(tmp_path, "hub-site", "Hub Site")

    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        publish_mode="local_server",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_root="/srv/main-computer/sites",
    )

    remote = load_website_project(tmp_path, "hub-site").to_dict(tmp_path)["publish_targets"]["remote_prod"]
    assert remote["publish_mode"] == "local_server"
    assert remote["use_local_server"] is True
    assert remote["site_slug"] == "johnrraymond"
    assert remote["source_path"] == "runtime/websites/hub-site"
    assert remote["remote_host"] == ""




def test_remote_publish_target_saves_published_site_directus_url(tmp_path: Path) -> None:
    create_website_project(tmp_path, "client-a-site", "Client A Site")

    save_website_publish_target(
        tmp_path,
        "client-a-site",
        "remote_prod",
        controller_id="coolify-client-a",
        project="client-a-site",
        environment="production",
        domain="www.client-a.example",
        publish_directus_url="https://cms.client-a.example/",
    )

    payload = load_website_project(tmp_path, "client-a-site").to_dict(tmp_path)
    assert payload["backend"]["cms"]["provider"] == "directus"
    assert payload["backend"]["cms"]["publish"]["url"] == "https://cms.client-a.example"


def test_remote_publish_target_rejects_invalid_directus_url(tmp_path: Path) -> None:
    create_website_project(tmp_path, "client-a-site", "Client A Site")

    with pytest.raises(WebsiteProjectError, match="Published Site Directus URL"):
        save_website_publish_target(
            tmp_path,
            "client-a-site",
            "remote_prod",
            controller_id="coolify-client-a",
            project="client-a-site",
            publish_directus_url="cms.client-a.example",
        )


def test_site_directus_connection_records_durable_volumes_before_local_publish(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "client-a-site", "Client A Site")
    manifest = dict(project.manifest)
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "service": {
                "kind": "directus",
                "image": "directus/directus:11.5.1",
                "internal_url": "",
                "public_url": "",
            },
            "storage": {},
        }
    }
    (project.path / "site.json").write_text(json.dumps(manifest), encoding="utf-8")

    updated = save_website_directus_connection(
        tmp_path,
        "client-a-site",
        {
            "mode": "use_existing",
            "service_name": "client-a-site-directus",
            "database_volume": "client-a-site_directus_database",
            "uploads_volume": "client-a-site_directus_uploads",
            "public_port": 28200,
        },
    )

    cms = updated.to_dict(tmp_path)["backend"]["cms"]
    assert cms["service"]["internal_url"] == "http://client-a-site-directus:8055"
    assert cms["service"]["public_url"] == "http://127.0.0.1:28200"
    assert cms["storage"]["database_volume"] == "client-a-site_directus_database"
    assert cms["storage"]["uploads_volume"] == "client-a-site_directus_uploads"
    assert cms["local_connection"]["mode"] == "use_existing"
    assert cms["local_connection"]["confirmed_at"]


def test_site_directus_connection_refuses_unsafe_volume_names(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "client-a-site", "Client A Site")
    manifest = dict(project.manifest)
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "service": {"kind": "directus"},
        }
    }
    (project.path / "site.json").write_text(json.dumps(manifest), encoding="utf-8")

    try:
        save_website_directus_connection(
            tmp_path,
            "client-a-site",
            {
                "service_name": "client-a-site-directus",
                "database_volume": "../bad",
                "uploads_volume": "client-a-site_directus_uploads",
                "public_port": 28200,
            },
        )
    except WebsiteProjectError as exc:
        assert "Directus volume names" in str(exc)
    else:
        raise AssertionError("unsafe Directus volume names should be rejected")


def test_site_directus_connection_can_request_explicit_overwrite_reset(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "client-a-site", "Client A Site")
    manifest = dict(project.manifest)
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "service": {"kind": "directus"},
        }
    }
    (project.path / "site.json").write_text(json.dumps(manifest), encoding="utf-8")

    updated = save_website_directus_connection(
        tmp_path,
        "client-a-site",
        {
            "mode": "overwrite_existing",
            "service_name": "client-a-site-directus",
            "database_volume": "client-a-site_directus_database",
            "uploads_volume": "client-a-site_directus_uploads",
            "public_port": 28200,
            "destructive_confirmation": True,
        },
    )

    connection = updated.to_dict(tmp_path)["backend"]["cms"]["local_connection"]
    assert connection["mode"] == "overwrite_existing"
    assert connection["reset_requested"] is True
    assert connection["reset_scope"] == "directus_container_and_named_volumes"
    assert connection["database_volume"] == "client-a-site_directus_database"
    assert connection["uploads_volume"] == "client-a-site_directus_uploads"


def test_site_directus_connection_overwrite_requires_explicit_confirmation(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "client-a-site", "Client A Site")
    manifest = dict(project.manifest)
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "service": {"kind": "directus"},
        }
    }
    (project.path / "site.json").write_text(json.dumps(manifest), encoding="utf-8")

    try:
        save_website_directus_connection(
            tmp_path,
            "client-a-site",
            {
                "mode": "overwrite_existing",
                "service_name": "client-a-site-directus",
                "database_volume": "client-a-site_directus_database",
                "uploads_volume": "client-a-site_directus_uploads",
                "public_port": 28200,
            },
        )
    except WebsiteProjectError as exc:
        assert "destructive_confirmation" in str(exc)
    else:
        raise AssertionError("overwrite_existing should require destructive confirmation")
