from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.local_platform_compose import (
    GENERATED_COMPOSE_RELATIVE_PATH,
    LocalPlatformComposeError,
    cms_dependency_service_names_for_site,
    generated_compose_path,
    image_name_for_site_lane,
    render_generated_site_compose,
    render_generated_websites_compose,
    site_generated_compose_path,
    site_generated_compose_relative_path,
    write_generated_site_compose,
    write_generated_websites_compose,
)
from main_computer.local_platform_registry import default_registry_data, save_local_platform_registry


_LOCAL_PLATFORM_ENV_PREFIX = "MAIN_COMPUTER_LOCAL_PLATFORM_"


@pytest.fixture(autouse=True)
def _clear_local_platform_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith(_LOCAL_PLATFORM_ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)


def _add_registry_site(data: dict, site_id: str, prod_port: int, dev_port: int) -> None:
    data["sites"][site_id] = {
        "id": site_id,
        "name": site_id.replace("-", " ").title(),
        "kind": "static-site",
        "repo_relative_path": f"runtime/websites/{site_id}",
        "lanes": {
            "prod": {
                "service": f"{site_id}-prod",
                "port": prod_port,
                "url": f"http://0.0.0.0:{prod_port}/",
                "status_url": f"http://0.0.0.0:{prod_port}/api/site/status",
            },
            "dev": {
                "service": f"{site_id}-dev",
                "port": dev_port,
                "url": f"http://0.0.0.0:{dev_port}/",
                "status_url": f"http://0.0.0.0:{dev_port}/api/site/status",
            },
        },
    }


def _write_site_manifest(repo_root: Path, site_id: str, *, name: str, kind: str = "static-site") -> None:
    site_dir = repo_root / "runtime" / "websites" / site_id
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": site_id,
                "name": name,
                "kind": kind,
            }
        ),
        encoding="utf-8",
    )


def test_generated_compose_file_renders_seeded_hub_and_blog_services(tmp_path: Path) -> None:
    text = render_generated_websites_compose(tmp_path)

    assert 'name: "main-computer-local-platform-unleashed"' in text
    assert "hub-local:" in text
    assert "hub-dev:" in text
    assert "blog-local:" in text
    assert "blog-dev:" in text
    assert 'image: "main-computer-site-hub-site-prod:latest"' in text
    assert 'image: "main-computer-site-blog-site-dev:latest"' in text
    assert 'SITE_LANE: "local"' in text
    assert 'SITE_LANE: "dev"' in text
    assert '- "0.0.0.0:18080:8080"' in text
    assert '- "0.0.0.0:18083:8080"' in text
    assert "../site-server" in text
    assert "../../../runtime/websites:/app/runtime/websites:ro" in text


def test_generated_compose_uses_registry_services_and_runtime_site_metadata(tmp_path: Path) -> None:
    data = default_registry_data()
    _add_registry_site(data, "portfolio", 18100, 18101)
    save_local_platform_registry(tmp_path, data)
    _write_site_manifest(tmp_path, "portfolio", name="My Portfolio", kind="portfolio-site")

    text = render_generated_websites_compose(tmp_path)

    assert "portfolio-prod:" in text
    assert "portfolio-dev:" in text
    assert 'SITE_ID: "portfolio"' in text
    assert 'SITE_NAME: "My Portfolio"' in text
    assert 'SITE_KIND: "portfolio-site"' in text
    assert 'image: "main-computer-site-portfolio-prod:latest"' in text
    assert '- "0.0.0.0:18100:8080"' in text
    assert '- "0.0.0.0:18101:8080"' in text


def test_write_generated_compose_is_deterministic(tmp_path: Path) -> None:
    first = write_generated_websites_compose(tmp_path)
    first_text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    second = write_generated_websites_compose(tmp_path)
    second_text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["repo_relative_path"] == GENERATED_COMPOSE_RELATIVE_PATH.as_posix()
    assert first["service_count"] == 4
    assert first_text == second_text


def test_generated_site_compose_scopes_to_one_site_and_uses_site_relative_paths(tmp_path: Path) -> None:
    data = default_registry_data()
    _add_registry_site(data, "portfolio", 18100, 18101)
    _add_registry_site(data, "greatlibrary", 18102, 18103)
    save_local_platform_registry(tmp_path, data)
    _write_site_manifest(tmp_path, "portfolio", name="My Portfolio", kind="portfolio-site")
    _write_site_manifest(tmp_path, "greatlibrary", name="The Great Library", kind="static-site")

    text = render_generated_site_compose(tmp_path, "portfolio")

    assert "portfolio-prod:" in text
    assert "portfolio-dev:" in text
    assert "greatlibrary-prod:" not in text
    assert "hub-local:" not in text
    assert "blog-local:" not in text
    assert 'SITE_ID: "portfolio"' in text
    assert 'SITE_NAME: "My Portfolio"' in text
    assert '"../../../../../deploy/local-platform/site-server"' in text
    assert '"../../..:/app/runtime/websites:ro"' in text
    assert site_generated_compose_relative_path(tmp_path, "portfolio").as_posix() == (
        "runtime/websites/portfolio/.main-computer/local-platform/docker-compose.yml"
    )


def test_write_generated_site_compose_writes_selected_site_only(tmp_path: Path) -> None:
    data = default_registry_data()
    _add_registry_site(data, "portfolio", 18100, 18101)
    save_local_platform_registry(tmp_path, data)
    _write_site_manifest(tmp_path, "portfolio", name="My Portfolio", kind="portfolio-site")

    result = write_generated_site_compose(tmp_path, "portfolio")
    path = site_generated_compose_path(tmp_path, "portfolio")
    text = path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["site_id"] == "portfolio"
    assert result["path"] == str(path)
    assert result["repo_relative_path"] == "runtime/websites/portfolio/.main-computer/local-platform/docker-compose.yml"
    assert result["service_count"] == 2
    assert result["services"] == ["portfolio-prod", "portfolio-dev"]
    assert "portfolio-prod:" in text
    assert "portfolio-dev:" in text
    assert "hub-local:" not in text
    assert "blog-local:" not in text


def test_generated_site_compose_materializes_only_selected_directus_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "alpha", 18104, 18105)
    _add_registry_site(data, "bravo", 18106, 18107)
    save_local_platform_registry(tmp_path, data)
    for site_id in ("alpha", "bravo"):
        site_dir = tmp_path / "runtime" / "websites" / site_id
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "site.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "id": site_id,
                    "name": site_id.title(),
                    "kind": "static-site",
                    "backend": {
                        "cms": {
                            "provider": "directus",
                            "required": True,
                            "runtime": "deployed",
                            "service": {
                                "kind": "directus",
                                "image": "directus/directus:11.5.1",
                                "internal_url": f"http://{site_id}-directus:8055",
                                "public_url": "",
                            },
                            "storage": {
                                "database_volume": f"{site_id}_directus_database",
                                "uploads_volume": f"{site_id}_directus_uploads",
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28200")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28210")

    result = write_generated_site_compose(tmp_path, "alpha")
    text = site_generated_compose_path(tmp_path, "alpha").read_text(encoding="utf-8")

    assert result["services"] == ["alpha-directus", "alpha-prod", "alpha-dev"]
    assert result["cms_services"] == ["alpha-directus"]
    assert "alpha-directus:" in text
    assert "alpha-prod:" in text
    assert "alpha-dev:" in text
    assert "bravo-directus:" not in text
    assert "bravo-prod:" not in text




def test_generated_compose_materializes_directus_cms_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = default_registry_data()
    _add_registry_site(data, "zzzzz", 18104, 18105)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "zzzzz"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "zzzzz",
                "name": "zzzzz",
                "kind": "static-site",
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://zzzzz-directus:8055",
                            "public_url": "",
                            "admin_secret_ref": "directus_admin_token",
                        },
                        "storage": {
                            "database_volume": "zzzzz_directus_database",
                            "uploads_volume": "zzzzz_directus_uploads",
                        },
                        "schema": {"collection": "posts", "status": "pending_deploy"},
                        "permissions": {
                            "public_read_published_posts": True,
                            "public_read_files": True,
                            "status": "pending_deploy",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28200")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28210")

    result = write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert result["service_count"] == 7
    assert result["cms_services"] == ["zzzzz-directus"]
    assert "zzzzz-directus:" in text
    assert 'image: "directus/directus:11.5.1"' in text
    assert 'DB_CLIENT: "sqlite3"' in text
    assert 'DB_FILENAME: "/directus/database/data.db"' in text
    assert 'STORAGE_LOCAL_ROOT: "/directus/uploads"' in text
    assert '- "zzzzz_directus_database:/directus/database"' in text
    assert '- "zzzzz_directus_uploads:/directus/uploads"' in text
    assert '- "127.0.0.1:28200:8055"' in text
    assert "name: \"zzzzz_directus_database\"" in text
    assert "name: \"zzzzz_directus_uploads\"" in text
    assert "zzzzz-dev:" in text
    assert "depends_on:" in text
    assert '- "zzzzz-directus"' in text
    assert 'MC_SITE_ID: "zzzzz"' in text
    assert 'MC_RUNTIME_LANE: "dev"' in text
    assert 'MC_DIRECTUS_SERVICE: "zzzzz-directus"' in text
    assert 'DIRECTUS_URL: "http://zzzzz-directus:8055"' in text
    assert 'DIRECTUS_PUBLIC_URL: "http://127.0.0.1:28200"' in text
    assert 'BLOG_ENABLED: "true"' not in text
    assert 'BLOG_PROVIDER: "directus"' not in text
    assert 'BLOG_CONTENT_RUNTIME: "deployed"' not in text
    persisted = json.loads((site_dir / "site.json").read_text(encoding="utf-8"))
    assert persisted["backend"]["cms"]["service"]["public_url"] == "http://127.0.0.1:28200"
    assert persisted["backend"]["cms"]["service"]["internal_url"] == "http://zzzzz-directus:8055"
    assert "ADMIN_TOKEN" not in text


def test_generated_compose_treats_blog_selected_disabled_as_intent_only_even_with_stale_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "zzzzz", 18104, 18105)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "zzzzz"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "zzzzz",
                "name": "zzzzz",
                "kind": "static-site",
                "features": {
                    "blog": {
                        "selected": True,
                        "enabled": False,
                        "cms": "directus",
                        "database": "sqlite",
                        "install_status": "pending_deploy",
                    }
                },
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "database_connection": "content",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://zzzzz-directus:8055",
                            "public_url": "http://127.0.0.1:28200",
                        },
                        "schema": {"collection": "posts", "status": "pending_deploy"},
                    }
                },
                "runtime": {"content_runtime": "deployed"},
                "runtime_config": {
                    "content": {
                        "provider": "directus",
                        "content_runtime": "deployed",
                        "collection": "posts",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28200")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28210")

    result = write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert result["cms_services"] == []
    assert "zzzzz-directus:" not in text
    assert 'MC_DIRECTUS_SERVICE: "zzzzz-directus"' not in text
    assert 'DIRECTUS_URL: "http://zzzzz-directus:8055"' not in text
    assert 'BLOG_ENABLED: "true"' not in text
    assert 'BLOG_CONTENT_RUNTIME: "deployed"' not in text



def test_generated_compose_materializes_directus_for_blog_cms_prep_without_enabling_blog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "zzzzz", 18104, 18105)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "zzzzz"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "zzzzz",
                "name": "zzzzz",
                "kind": "static-site",
                "features": {
                    "blog": {
                        "selected": True,
                        "enabled": False,
                        "cms": "directus",
                        "database": "sqlite",
                        "install_status": "pending_deploy",
                    }
                },
                "blog_install": {
                    "layers": {"cms": {"status": "configured"}},
                    "runtime_preparation": {
                        "directus_service": {
                            "status": "pending_deploy",
                            "requested": True,
                            "verified": False,
                        }
                    },
                },
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "database_connection": "content",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://zzzzz-directus:8055",
                            "public_url": "",
                            "admin_secret_ref": "directus_admin_token",
                        },
                        "storage": {
                            "database_volume": "zzzzz_directus_database",
                            "uploads_volume": "zzzzz_directus_uploads",
                        },
                        "schema": {"collection": "posts", "status": "pending_deploy"},
                        "permissions": {
                            "public_read_published_posts": True,
                            "public_read_files": True,
                            "status": "pending_deploy",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28200")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28210")

    result = write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert result["cms_services"] == ["zzzzz-directus"]
    assert "zzzzz-directus:" in text
    assert "zzzzz-dev:" in text
    assert "depends_on:" in text
    assert '- "zzzzz-directus"' in text
    assert 'MC_DIRECTUS_SERVICE: "zzzzz-directus"' in text
    assert 'DIRECTUS_URL: "http://zzzzz-directus:8055"' in text
    assert 'BLOG_ENABLED: "true"' not in text
    assert 'BLOG_CONTENT_RUNTIME: "deployed"' not in text


def test_generated_compose_skips_occupied_directus_ports_and_persists_choice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "zzzzz", 18104, 18105)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "zzzzz"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "id": "zzzzz",
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://zzzzz-directus:8055",
                            "public_url": "",
                        },
                        "storage": {
                            "database_volume": "zzzzz_directus_database",
                            "uploads_volume": "zzzzz_directus_uploads",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28200")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28202")
    blocked = {28200}
    monkeypatch.setattr(
        "main_computer.local_platform_compose._host_port_can_bind",
        lambda port: int(port) not in blocked,
    )

    result = write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")
    persisted = json.loads((site_dir / "site.json").read_text(encoding="utf-8"))

    assert result["cms_services"] == ["zzzzz-directus"]
    assert '- "127.0.0.1:28201:8055"' in text
    assert persisted["backend"]["cms"]["service"]["public_url"] == "http://127.0.0.1:28201"


def test_generated_compose_reuses_persisted_directus_public_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "zzzzz", 18104, 18105)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "zzzzz"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "id": "zzzzz",
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://zzzzz-directus:8055",
                            "public_url": "http://127.0.0.1:28444",
                        },
                        "storage": {
                            "database_volume": "zzzzz_directus_database",
                            "uploads_volume": "zzzzz_directus_uploads",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28200")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28202")

    write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert '- "127.0.0.1:28444:8055"' in text
    assert 'DIRECTUS_PUBLIC_URL: "http://127.0.0.1:28444"' in text



def test_generated_compose_prefers_configured_local_directus_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "hub-site", 19180, 19181)
    save_local_platform_registry(tmp_path, data)
    _write_site_manifest(tmp_path, "hub-site", name="Hub Site", kind="hub")
    site_path = tmp_path / "runtime" / "websites" / "hub-site" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest["features"] = {
        "blog": {
            "selected": True,
            "enabled": True,
            "cms": "directus",
            "database": "sqlite",
            "install_status": "pending_deploy",
        }
    }
    manifest.setdefault("backend", {})["cms"] = {
        "provider": "directus",
        "required": True,
        "runtime": "deployed",
        "service": {
            "kind": "directus",
            "image": "directus/directus:11.5.1",
            "internal_url": "http://hub-site-directus:8055",
            "public_url": "http://127.0.0.1:28201",
        },
        "storage": {
            "database_volume": "hub-site_directus_database",
            "uploads_volume": "hub-site_directus_uploads",
        },
        "local_connection": {
            "mode": "create_new",
            "service_name": "hub-site-directus",
            "public_port": 28200,
            "public_url": "http://127.0.0.1:28200",
            "internal_url": "http://hub-site-directus:8055",
            "database_volume": "hub-site_directus_database_20260518182134",
            "uploads_volume": "hub-site_directus_uploads_20260518182134",
        },
    }
    site_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28200")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28210")
    monkeypatch.setattr("main_computer.local_platform_compose._host_port_can_bind", lambda port: int(port) != 28200)
    monkeypatch.setattr(
        "main_computer.local_platform_compose._docker_port_owners",
        lambda port: [
            "main-computer-local-platform-hub-site-directus-1 directus/directus:11.5.1 Up 1 minute 127.0.0.1:28200->8055/tcp"
        ],
    )

    result = write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert result["cms_services"] == ["hub-site-directus"]
    assert '- "127.0.0.1:28200:8055"' in text
    assert '- "hub-site_directus_database_20260518182134:/directus/database"' in text
    assert '- "hub-site_directus_uploads_20260518182134:/directus/uploads"' in text
    assert 'DIRECTUS_PUBLIC_URL: "http://127.0.0.1:28200"' in text
    assert 'PUBLIC_URL: "http://127.0.0.1:28200"' in text
    assert "hub-site_directus_database_20260518182134:" in text
    persisted = json.loads(site_path.read_text(encoding="utf-8"))
    assert persisted["backend"]["cms"]["service"]["public_url"] == "http://127.0.0.1:28200"



def test_generated_compose_reuses_existing_shared_directus_without_rendering_duplicate_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "johnrraymond", 18118, 18119)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "johnrraymond"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "johnrraymond",
                "name": "John R. Raymond",
                "kind": "static-site",
                "features": {
                    "blog": {
                        "selected": True,
                        "enabled": True,
                        "cms": "directus",
                        "database": "sqlite",
                    }
                },
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://johnrraymond-directus:8055",
                            "public_url": "http://127.0.0.1:28200",
                        },
                        "storage": {
                            "database_volume": "johnrraymond_directus_database",
                            "uploads_volume": "johnrraymond_directus_uploads",
                        },
                        "local_connection": {
                            "mode": "use_existing",
                            "service_name": "johnrraymond-directus",
                            "public_port": 28200,
                            "public_url": "http://127.0.0.1:28200",
                            "internal_url": "http://johnrraymond-directus:8055",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("main_computer.local_platform_compose._host_port_can_bind", lambda port: int(port) != 28200)
    monkeypatch.setattr(
        "main_computer.local_platform_compose._docker_port_owners",
        lambda port: [
            "main-computer-local-platform-hub-site-directus-1 directus/directus:11.5.1 Up 4 days 127.0.0.1:28200->8055/tcp"
        ],
    )

    result = write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert result["cms_services"] == []
    assert "\n  hub-site-directus:\n" not in text
    assert "\n  johnrraymond-directus:\n" not in text
    assert 'MC_DIRECTUS_SERVICE: "hub-site-directus"' in text
    assert 'DIRECTUS_URL: "http://hub-site-directus:8055"' in text
    assert 'DIRECTUS_PUBLIC_URL: "http://127.0.0.1:28200"' in text
    assert 'BLOG_ENABLED: "true"' in text
    assert cms_dependency_service_names_for_site(tmp_path, "johnrraymond") == []
    john_section = text.split("  johnrraymond-dev:", 1)[1].split("\n\n", 1)[0]
    assert "depends_on:" not in john_section


def test_generated_compose_rejects_persisted_directus_port_owned_by_another_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "zzzzz", 18104, 18105)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "zzzzz"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "id": "zzzzz",
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://zzzzz-directus:8055",
                            "public_url": "http://127.0.0.1:28105",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("main_computer.local_platform_compose._host_port_can_bind", lambda port: False)
    monkeypatch.setattr(
        "main_computer.local_platform_compose._docker_port_owners",
        lambda port: [
            "directus-633d4c17-a5p87jbwec2sbymmx5slshjj directus/directus:11.5.1 Up 16 hours 127.0.0.1:28105->8055/tcp"
        ],
    )

    with pytest.raises(LocalPlatformComposeError, match="already used by directus-633d4c17"):
        write_generated_websites_compose(tmp_path)


def test_generated_compose_rejects_unsafe_registry_service_names(tmp_path: Path) -> None:
    data = default_registry_data()
    data["sites"]["hub-site"]["lanes"]["prod"]["service"] = "bad service name"
    save_local_platform_registry(tmp_path, data)

    with pytest.raises(LocalPlatformComposeError, match="Unsafe generated Compose service name"):
        render_generated_websites_compose(tmp_path)


def test_image_names_use_safe_site_slug() -> None:
    assert image_name_for_site_lane("My Site!!", "prod") == "main-computer-site-my-site-prod:latest"
    assert image_name_for_site_lane("My Site!!", "dev") == "main-computer-site-my-site-dev:latest"


def test_generate_websites_compose_cli_writes_file(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "tools/local-platform/generate-websites-compose.py",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["service_count"] == 4
    assert "hub-local" in payload["services"]
    assert (tmp_path / GENERATED_COMPOSE_RELATIVE_PATH).exists()


def test_generate_websites_compose_cli_check_detects_stale_file(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "tools/local-platform/generate-websites-compose.py",
            "--repo-root",
            str(tmp_path),
            "--check",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["stale"] is True



def test_generated_compose_honors_mode_scoped_project_path_and_ports(monkeypatch, tmp_path: Path) -> None:
    compose_path = tmp_path / "debug-state" / "local-platform" / "docker-compose.websites.yml"
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT", "main-computer-local-platform-debug")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH", str(tmp_path / "debug-state" / "local-platform" / "sites.json"))
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH", str(compose_path))
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START", "28080")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START", "28100")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END", "28199")

    result = write_generated_websites_compose(tmp_path)
    text = compose_path.read_text(encoding="utf-8")

    assert result["path"] == str(compose_path)
    assert 'name: "main-computer-local-platform-debug"' in text
    assert '- "0.0.0.0:28080:8080"' in text
    assert '- "0.0.0.0:28083:8080"' in text
    assert (tmp_path / "deploy" / "local-platform" / "site-server").resolve().as_posix() in text
    assert (tmp_path / "runtime" / "websites").resolve().as_posix() in text


def test_generated_compose_backfills_directus_dependency_from_blog_feature_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = default_registry_data()
    _add_registry_site(data, "legacy-blog", 18120, 18121)
    save_local_platform_registry(tmp_path, data)
    site_dir = tmp_path / "runtime" / "websites" / "legacy-blog"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "legacy-blog",
                "name": "Legacy Blog",
                "kind": "static-site",
                "features": {
                    "blog": {
                        "enabled": True,
                        "cms": "directus",
                        "database": "sqlite",
                        "content": {"provider": "directus", "collection": "posts"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_START", "28300")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_DIRECTUS_PORT_END", "28310")

    result = write_generated_websites_compose(tmp_path)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")
    persisted = json.loads((site_dir / "site.json").read_text(encoding="utf-8"))

    assert "legacy-blog-directus:" in text
    assert 'BLOG_ENABLED: "true"' in text
    assert 'BLOG_PROVIDER: "directus"' in text
    assert 'DIRECTUS_URL: "http://legacy-blog-directus:8055"' in text
    assert 'DIRECTUS_PUBLIC_URL: "http://127.0.0.1:28300"' in text
    assert result["cms_services"] == ["legacy-blog-directus"]
    assert persisted["backend"]["cms"]["provider"] == "directus"
    assert persisted["backend"]["cms"]["required"] is True
    assert persisted["backend"]["cms"]["service"]["public_url"] == "http://127.0.0.1:28300"


def test_publish_scopes_directus_port_validation_to_selected_site(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from main_computer.website_project_manifest import publish_website

    data = default_registry_data()
    _add_registry_site(data, "zzzzz", 18104, 18105)
    save_local_platform_registry(tmp_path, data)

    hub_dir = tmp_path / "runtime" / "websites" / "hub-site"
    hub_dir.mkdir(parents=True, exist_ok=True)
    (hub_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "hub-site",
                "name": "Hub Site",
                "kind": "hub-site",
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://hub-site-directus:8055",
                            "public_url": "http://127.0.0.1:28200",
                        },
                        "storage": {
                            "database_volume": "hub-site_directus_database",
                            "uploads_volume": "hub-site_directus_uploads",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    zzzzz_dir = tmp_path / "runtime" / "websites" / "zzzzz"
    zzzzz_dir.mkdir(parents=True, exist_ok=True)
    (zzzzz_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "zzzzz",
                "name": "Zzzzz",
                "kind": "static-site",
                "backend": {
                    "cms": {
                        "provider": "directus",
                        "required": True,
                        "runtime": "deployed",
                        "service": {
                            "kind": "directus",
                            "image": "directus/directus:11.5.1",
                            "internal_url": "http://zzzzz-directus:8055",
                            "public_url": "http://127.0.0.1:28201",
                        },
                        "storage": {
                            "database_volume": "zzzzz_directus_database",
                            "uploads_volume": "zzzzz_directus_uploads",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    checked_ports: list[int] = []

    def fake_can_bind(port: int) -> bool:
        checked_ports.append(int(port))
        return int(port) != 28201

    def fake_owners(port: int) -> list[str]:
        if int(port) == 28201:
            return [
                "main-computer-local-platform-unleashed-hub-site-directus-1 directus/directus:11.5.1 Up 3 hours 127.0.0.1:28201->8055/tcp"
            ]
        return []

    monkeypatch.setattr("main_computer.local_platform_compose._host_port_can_bind", fake_can_bind)
    monkeypatch.setattr("main_computer.local_platform_compose._docker_port_owners", fake_owners)

    result = publish_website(tmp_path, "hub-site", dry_run=True, verify=False)
    text = generated_compose_path(tmp_path).read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["generated_compose"]["cms_services"] == ["hub-site-directus"]
    assert "hub-site-directus:" in text
    assert "zzzzz-directus:" not in text
    assert '- "127.0.0.1:28200:8055"' in text
    assert 28201 not in checked_ports

