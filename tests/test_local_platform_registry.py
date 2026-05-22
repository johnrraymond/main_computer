from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.local_platform_registry import (
    GENERATED_SITE_PORT_END,
    GENERATED_SITE_PORT_START,
    LocalPlatformRegistryError,
    allocate_site_ports,
    default_registry_data,
    list_managed_sites,
    load_local_platform_registry,
    normalize_registry_lane,
    registry_path,
    resolve_site_lane,
    save_local_platform_registry,
)


def test_local_platform_registry_seeds_builtin_hub_and_blog_lanes(tmp_path: Path) -> None:
    registry = load_local_platform_registry(tmp_path)

    assert registry_path(tmp_path).exists()
    assert [site.id for site in registry.list_sites()] == ["blog-site", "hub-site"]

    hub_prod = registry.resolve("hub-site", "prod")
    assert hub_prod.service == "hub-local"
    assert hub_prod.port == 18080
    assert hub_prod.url == "http://localhost:18080/"
    assert hub_prod.status_url == "http://localhost:18080/api/site/status"

    for alias in ("local", "local-prod", "prod", "production"):
        assert normalize_registry_lane(alias) == "prod"
        assert registry.resolve("hub-site", alias).service == "hub-local"

    hub_dev = resolve_site_lane(tmp_path, "hub-site", "dev")
    assert hub_dev.service == "hub-dev"
    assert hub_dev.port == 18082


def test_local_platform_registry_can_save_and_round_trip_custom_site(tmp_path: Path) -> None:
    data = default_registry_data()
    data["sites"]["portfolio"] = {
        "id": "portfolio",
        "name": "Portfolio",
        "kind": "static-site",
        "repo_relative_path": "runtime/websites/portfolio",
        "lanes": {
            "prod": {
                "service": "portfolio-prod",
                "port": 18100,
                "url": "http://0.0.0.0:18100/",
                "status_url": "http://0.0.0.0:18100/api/site/status",
            },
            "dev": {
                "service": "portfolio-dev",
                "port": 18101,
                "url": "http://0.0.0.0:18101/",
                "status_url": "http://0.0.0.0:18101/api/site/status",
            },
        },
    }

    saved = save_local_platform_registry(tmp_path, data)
    assert saved.resolve("portfolio", "local-prod").port == 18100

    loaded = load_local_platform_registry(tmp_path)
    assert loaded.resolve("portfolio", "dev").service == "portfolio-dev"
    assert json.loads(registry_path(tmp_path).read_text(encoding="utf-8"))["sites"]["portfolio"]["lanes"]["prod"]["port"] == 18100


@pytest.mark.parametrize("bad_path", ["../escape", "/absolute/path", "runtime/../escape"])
def test_local_platform_registry_rejects_unsafe_repo_relative_paths(tmp_path: Path, bad_path: str) -> None:
    data = default_registry_data()
    data["sites"]["bad-site"] = {
        "id": "bad-site",
        "name": "Bad Site",
        "kind": "static-site",
        "repo_relative_path": bad_path,
        "lanes": {
            "prod": {
                "service": "bad-prod",
                "port": 18100,
                "url": "http://0.0.0.0:18100/",
                "status_url": "http://0.0.0.0:18100/api/site/status",
            }
        },
    }

    with pytest.raises(LocalPlatformRegistryError):
        save_local_platform_registry(tmp_path, data)


def test_local_platform_registry_cli_resolves_lanes(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "main_computer.local_platform_registry",
            "resolve",
            "blog-site",
            "--lane",
            "local-prod",
            "--repo-root",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["service"] == "blog-local"
    assert payload["port"] == 18081


def test_list_managed_sites_returns_seeded_registry_sites(tmp_path: Path) -> None:
    assert [site.id for site in list_managed_sites(tmp_path)] == ["blog-site", "hub-site"]


def test_website_publish_plan_prefers_registry_lanes_over_legacy_manifest_data(tmp_path: Path) -> None:
    from main_computer.website_project_manifest import list_website_projects, website_publish_plan

    list_website_projects(tmp_path)
    data = default_registry_data()
    data["sites"]["hub-site"]["lanes"]["dev"] = {
        "service": "registry-hub-dev",
        "port": 18182,
        "url": "http://0.0.0.0:18182/",
        "status_url": "http://0.0.0.0:18182/api/site/status",
    }
    save_local_platform_registry(tmp_path, data)

    plan = website_publish_plan(tmp_path, "hub-site", "dev")
    assert plan["service"] == "registry-hub-dev"
    assert plan["port"] == 18182
    assert plan["url"] == "http://localhost:18182/"
    assert plan["status_url"] == "http://localhost:18182/api/site/status"
    assert plan["command"][-1] == "registry-hub-dev"


def _add_registry_site(data: dict, site_id: str, prod_port: int | None = None, dev_port: int | None = None) -> None:
    lanes: dict[str, dict[str, object]] = {}
    if prod_port is not None:
        lanes["prod"] = {
            "service": f"{site_id}-prod",
            "port": prod_port,
            "url": f"http://0.0.0.0:{prod_port}/",
            "status_url": f"http://0.0.0.0:{prod_port}/api/site/status",
        }
    if dev_port is not None:
        lanes["dev"] = {
            "service": f"{site_id}-dev",
            "port": dev_port,
            "url": f"http://0.0.0.0:{dev_port}/",
            "status_url": f"http://0.0.0.0:{dev_port}/api/site/status",
        }
    data["sites"][site_id] = {
        "id": site_id,
        "name": site_id.replace("-", " ").title(),
        "kind": "static-site",
        "repo_relative_path": f"runtime/websites/{site_id}",
        "lanes": lanes,
    }


def test_allocate_site_ports_starts_generated_sites_at_first_even_pair(tmp_path: Path) -> None:
    registry = load_local_platform_registry(tmp_path)

    assert allocate_site_ports(registry) == {"prod": 18100, "dev": 18101}
    assert registry.resolve("hub-site", "local-prod").port == 18080
    assert registry.resolve("blog-site", "dev").port == 18083


def test_allocate_site_ports_skips_occupied_generated_pairs(tmp_path: Path) -> None:
    data = default_registry_data()
    _add_registry_site(data, "portfolio", prod_port=18100, dev_port=18101)
    registry = save_local_platform_registry(tmp_path, data)

    assert allocate_site_ports(registry) == {"prod": 18102, "dev": 18103}


def test_allocate_site_ports_skips_pair_when_only_one_port_is_occupied(tmp_path: Path) -> None:
    data = default_registry_data()
    _add_registry_site(data, "odd-blocker", dev_port=18101)
    registry = save_local_platform_registry(tmp_path, data)

    assert allocate_site_ports(registry) == {"prod": 18102, "dev": 18103}


def test_local_platform_registry_rejects_duplicate_manual_ports(tmp_path: Path) -> None:
    data = default_registry_data()
    _add_registry_site(data, "portfolio", prod_port=18100, dev_port=18101)
    _add_registry_site(data, "docs", prod_port=18100, dev_port=18103)

    with pytest.raises(LocalPlatformRegistryError, match="Duplicate local platform registry port 18100"):
        save_local_platform_registry(tmp_path, data)


def test_local_platform_registry_rejects_duplicate_lane_aliases(tmp_path: Path) -> None:
    data = default_registry_data()
    data["sites"]["hub-site"]["lanes"]["local"] = {
        "service": "duplicate-hub-local",
        "port": 18100,
        "url": "http://0.0.0.0:18100/",
        "status_url": "http://0.0.0.0:18100/api/site/status",
    }

    with pytest.raises(LocalPlatformRegistryError, match="Duplicate registry lane"):
        save_local_platform_registry(tmp_path, data)


def test_allocate_site_ports_raises_when_generated_range_is_exhausted(tmp_path: Path) -> None:
    data = default_registry_data()
    for prod_port in range(GENERATED_SITE_PORT_START, GENERATED_SITE_PORT_END + 1, 2):
        site_id = f"generated-{prod_port}"
        _add_registry_site(data, site_id, prod_port=prod_port, dev_port=prod_port + 1)

    registry = save_local_platform_registry(tmp_path, data)

    with pytest.raises(LocalPlatformRegistryError, match="No generated website port pair"):
        allocate_site_ports(registry)


def test_local_platform_registry_cli_allocates_next_ports(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "main_computer.local_platform_registry",
            "allocate-ports",
            "--repo-root",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload == {"dev": 18101, "prod": 18100}




def test_local_platform_registry_honors_mode_scoped_environment(monkeypatch, tmp_path: Path) -> None:
    registry_file = tmp_path / "debug-state" / "local-platform" / "sites.json"
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH", str(registry_file))
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START", "28080")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START", "28100")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END", "28199")

    registry = load_local_platform_registry(tmp_path)

    assert registry_path(tmp_path) == registry_file
    assert registry.resolve("hub-site", "local").port == 28080
    assert registry.resolve("blog-site", "local").port == 28081
    assert registry.resolve("hub-site", "dev").port == 28082
    assert registry.resolve("blog-site", "dev").port == 28083
    assert allocate_site_ports(registry) == {"prod": 28100, "dev": 28101}
    assert registry_file.exists()
