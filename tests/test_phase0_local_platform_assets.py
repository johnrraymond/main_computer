from __future__ import annotations

import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_phase0_local_platform_compose_uses_isolated_project_images_and_direct_ports() -> None:
    compose = (ROOT / "deploy" / "local-platform" / "docker-compose.yml").read_text(encoding="utf-8")

    assert "name: main-computer-local-platform" in compose
    assert "0.0.0.0:18080:8080" in compose
    assert "0.0.0.0:18081:8080" in compose
    assert "0.0.0.0:18082:8080" in compose
    assert "0.0.0.0:18083:8080" in compose

    assert '- "80:80"' not in compose
    assert '- "443:443"' not in compose
    assert '- "8080:8080"' not in compose
    assert '- "8443:8443"' not in compose
    assert "8765:8765" not in compose
    assert "8770:8770" not in compose
    assert "image: main-computer-dev:latest" not in compose

    for image in (
        "main-computer-phase0-hub-local:latest",
        "main-computer-phase0-blog-local:latest",
        "main-computer-phase0-hub-dev:latest",
        "main-computer-phase0-blog-dev:latest",
    ):
        assert image in compose

    assert "main-computer-phase0-site-server:latest" not in compose
    assert "traefik" not in compose.lower()
    assert "caddy" not in compose.lower()


def test_phase0_local_platform_has_local_and_dev_hub_blog_services() -> None:
    compose = (ROOT / "deploy" / "local-platform" / "docker-compose.yml").read_text(encoding="utf-8")

    for service in ("hub-local", "blog-local", "hub-dev", "blog-dev"):
        assert f"  {service}:" in compose

    assert "SITE_ID: hub-site" in compose
    assert "SITE_ID: blog-site" in compose
    assert "SITE_KIND: hub" in compose
    assert "SITE_KIND: blog" in compose
    assert "SITE_LANE: local" in compose
    assert "SITE_LANE: dev" in compose

    for host in ("hub.local", "blog.local", "dev.hub.local", "dev.blog.local"):
        assert host not in compose
    assert "Host(`" not in compose


def test_phase0_site_server_exposes_hub_and_site_status_contracts() -> None:
    app = (ROOT / "deploy" / "local-platform" / "site-server" / "app.py").read_text(encoding="utf-8")

    assert 'path == "/api/site/status"' in app
    assert 'path == "/api/hub/status" and SITE_KIND == "hub"' in app
    assert 'SITE_KIND = os.environ.get("SITE_KIND", "site")' in app
    assert '"phase": "0"' in app
    assert 'self.send_header("Cache-Control", "no-store, max-age=0")' in app
    assert "def safe_static_file" in app
    assert "mimetypes.guess_type" in app


def test_phase0_tooling_scripts_exist_and_compile() -> None:
    scripts = [
        ROOT / "tools" / "local-platform" / "verify-docker.py",
        ROOT / "tools" / "local-platform" / "verify-local-platform.py",
    ]
    for script in scripts:
        assert script.exists()
        py_compile.compile(str(script), doraise=True)

    for script_name in (
        "up-local-platform.ps1",
        "down-local-platform.ps1",
    ):
        script = ROOT / "tools" / "local-platform" / script_name
        assert script.exists()
        text = script.read_text(encoding="utf-8")
        assert "main-computer-local-platform" in text
        assert "\\Q" not in text

    assert not (ROOT / "tools" / "local-platform" / "install-local-hostnames.ps1").exists()


def test_phase0_readme_documents_direct_ports_and_no_proxy_dependency() -> None:
    readme = (ROOT / "deploy" / "local-platform" / "README.md").read_text(encoding="utf-8")

    assert "does **not** use WSL, SSH, Coolify, Caddy, Traefik" in readme
    assert "reverse proxy can be added later" in readme
    assert "http://0.0.0.0:18080/" in readme
    assert "http://0.0.0.0:18081/" in readme
    assert "http://0.0.0.0:18082/" in readme
    assert "http://0.0.0.0:18083/" in readme
    assert "https://hub.local" not in readme
    assert "dev.hub.local" not in readme



def test_phase0_assets_are_kept_by_export_script() -> None:
    script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

    assert '"deploy/local-platform"' in script
    assert '"tools/local-platform"' in script
