from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.viewport import ViewportServer
from main_computer.website_project_manifest import list_website_projects


def _post_json(base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_publishing_setup_endpoint_saves_publish_command_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    list_website_projects(tmp_path)
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)

    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        payload = _post_json(
            base,
            "/api/applications/websites/site/publish-target",
            {
                "site_id": "hub-site",
                "lane": "remote_prod",
                "publish_mode": "scp",
                "site_slug": "johnrraymond",
                "project": "johnrraymond",
                "source_path": "runtime/websites/hub-site",
                "remote_host": "root@publish.greatlibrary.io",
                "remote_root": "/srv/main-computer/sites",
                "ssh_password": "secret-password",
                "domain": "https://johnrraymond.example.com",
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert payload["ok"] is True
    remote = payload["site"]["publish_targets"]["remote_prod"]
    assert remote["publish_mode"] == "scp"
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
    assert remote["domain"] == "https://johnrraymond.example.com"
    assert remote["accepted_at"]


def test_publishing_local_server_prepare_route_is_removed() -> None:
    routes = Path("main_computer/viewport_route_dispatch.py").read_text(encoding="utf-8")
    handlers = Path("main_computer/viewport_routes_applications.py").read_text(encoding="utf-8")

    assert "/api/publishing/local-server/prepare" not in routes
    assert "_handle_publishing_local_server_prepare" not in handlers
