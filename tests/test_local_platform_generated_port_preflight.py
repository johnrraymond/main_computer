from __future__ import annotations

import json
import os
import socket
import textwrap
from pathlib import Path

from main_computer.website_project_manifest import create_local_platform_website_project


def _write_archived_site_manifest(repo_root: Path, site_id: str, prod_port: int, dev_port: int) -> None:
    site_dir = repo_root / "runtime" / "websites-archive" / site_id
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "site_model": "2.0",
                "id": site_id,
                "name": site_id,
                "kind": "static-site",
                "archive": {"status": "archived"},
                "local_platform": {
                    "lanes": {
                        "local": {
                            "service": f"{site_id}-prod",
                            "port": prod_port,
                            "url": f"http://localhost:{prod_port}/",
                            "status_url": f"http://localhost:{prod_port}/api/site/status",
                        },
                        "dev": {
                            "service": f"{site_id}-dev",
                            "port": dev_port,
                            "url": f"http://localhost:{dev_port}/",
                            "status_url": f"http://localhost:{dev_port}/api/site/status",
                        },
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _reserve_listening_port(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


def _find_test_port_pair() -> tuple[int, int]:
    # Avoid the real app ranges so this test does not depend on the developer machine.
    for prod_port in range(31000, 33000, 2):
        first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            first.bind(("127.0.0.1", prod_port))
            second.bind(("127.0.0.1", prod_port + 1))
            return prod_port, prod_port + 1
        except OSError:
            continue
        finally:
            first.close()
            second.close()
    raise AssertionError("Could not find two adjacent free test ports")


def test_new_site_generation_skips_ports_reserved_by_archived_site_manifest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_SCAN_WSL_WEBSITES", "0")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START", "18100")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END", "18109")

    # Simulates a site that was archived and removed from runtime/local-platform/sites.json.
    # Its old ports must still be considered reserved.
    _write_archived_site_manifest(tmp_path, "old-bar", prod_port=18100, dev_port=18101)

    project, result = create_local_platform_website_project(
        tmp_path,
        "new-foo",
        "New Foo",
        allocate_unique_id=True,
        regenerate_compose=False,
    )

    assert project.id == "new-foo"
    assert result["registry"]["ports"] == {"prod": 18102, "dev": 18103}


def test_new_site_generation_probes_host_ports_and_skips_occupied_pair(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_SCAN_WSL_WEBSITES", "0")
    prod_port, dev_port = _find_test_port_pair()
    blocker = _reserve_listening_port(prod_port)

    try:
        monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START", str(prod_port))
        monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END", str(dev_port + 6))

        project, result = create_local_platform_website_project(
            tmp_path,
            "probe-test",
            "Probe Test",
            allocate_unique_id=True,
            regenerate_compose=False,
        )
    finally:
        blocker.close()

    assert project.id == "probe-test"
    assert result["registry"]["ports"] == {
        "prod": prod_port + 2,
        "dev": dev_port + 2,
    }


def test_new_site_generation_respects_wsl_discovered_website_ports(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START", "18100")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END", "18109")

    fake_wsl = tmp_path / ("wsl.exe" if os.name == "nt" else "wsl.exe")
    fake_wsl.write_text(
        textwrap.dedent(
            r"""
            #!/usr/bin/env python3
            import json

            # This is the contract for the patch:
            # the WSL scan should inspect actual website manifests from the WSL environment,
            # including other installs, and parse their local_platform lane ports.
            print(json.dumps([
                {
                    "path": "/home/some-other-install/runtime/websites-archive/bar/site.json",
                    "id": "bar",
                    "local_platform": {
                        "lanes": {
                            "local": {"port": 18100},
                            "dev": {"port": 18101}
                        }
                    }
                }
            ]))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    fake_wsl.chmod(0o755)

    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_WSL_COMMAND", str(fake_wsl))
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_SCAN_WSL_WEBSITES", "1")

    project, result = create_local_platform_website_project(
        tmp_path,
        "from-other-install-aware",
        "From Other Install Aware",
        allocate_unique_id=True,
        regenerate_compose=False,
    )

    assert project.id == "from-other-install-aware"
    assert result["registry"]["ports"] == {"prod": 18102, "dev": 18103}
