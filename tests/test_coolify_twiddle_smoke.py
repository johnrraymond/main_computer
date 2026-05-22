from __future__ import annotations

import base64
import importlib.util
from argparse import Namespace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools/local-prod/coolify-twiddle-smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("coolify_twiddle_smoke", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coolify_twiddle_smoke_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_smoke_script_uses_coolify_api_not_local_compose() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert '"/services"' in text
    assert '"/deploy"' in text
    assert '"/services/{uuid}"' in text
    assert "docker compose" not in text.lower()
    assert "subprocess" not in text


def test_smoke_compose_carries_special_marker() -> None:
    module = load_module()
    compose = module.smoke_compose("main-computer-twiddle-smoke-hub-site")
    assert "traefik/whoami:latest" in compose
    assert "main-computer.twiddle-smoke=true" in compose
    assert "main-computer.twiddle-smoke.name=main-computer-twiddle-smoke-hub-site" in compose


def test_smoke_compose_raw_is_base64_encoded_for_coolify() -> None:
    module = load_module()
    encoded = module.smoke_compose_base64("main-computer-twiddle-smoke-hub-site")
    decoded = base64.b64decode(encoded).decode("utf-8")
    assert decoded == module.smoke_compose("main-computer-twiddle-smoke-hub-site")


def test_api_url_builds_v1_paths_and_query() -> None:
    module = load_module()
    config = {"base_url": "http://127.0.0.1:17056/"}
    assert module.api_url(config, "/services") == "http://127.0.0.1:17056/api/v1/services"
    assert (
        module.api_url(config, "/deploy", {"uuid": "abc", "force": True})
        == "http://127.0.0.1:17056/api/v1/deploy?uuid=abc&force=true"
    )


def test_create_service_body_uses_project_environment_and_server(monkeypatch) -> None:
    module = load_module()

    def fake_list_endpoint(config, endpoint, timeout):
        if endpoint == "projects":
            return [
                {
                    "uuid": "project-1",
                    "name": "hub-site",
                    "environments": [{"uuid": "env-1", "name": "production"}],
                }
            ]
        if endpoint == "servers":
            return [{"uuid": "server-1", "name": "localhost", "is_usable": True}]
        raise AssertionError(endpoint)

    monkeypatch.setattr(module, "list_endpoint", fake_list_endpoint)
    config = {
        "project_name": "hub-site",
        "environment_name": "production",
    }

    body = module.create_service_body(config, "main-computer-twiddle-smoke-hub-site", 30.0, instant_deploy=False)

    assert "type" not in body
    assert body["name"] == "main-computer-twiddle-smoke-hub-site"
    assert body["project_uuid"] == "project-1"
    assert body["environment_name"] == "production"
    assert body["environment_uuid"] == "env-1"
    assert body["server_uuid"] == "server-1"
    assert body["instant_deploy"] is False
    assert "docker_compose_raw" in body
    decoded_compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
    assert "services:" in decoded_compose
    assert "main-computer.twiddle-smoke=true" in decoded_compose


def test_toggle_deletes_existing_service(monkeypatch, tmp_path: Path) -> None:
    module = load_module()
    calls: list[tuple[str, str, str]] = []

    def fake_load_config(args):
        return {
            "repo_root": tmp_path,
            "site_id": "hub-site",
            "controller_id": "coolify-local",
            "base_url": "http://127.0.0.1:17056",
            "token": "token",
            "state_path": tmp_path / "state.json",
        }

    def fake_find(config, name, timeout):
        return {"uuid": "service-1", "name": name}

    def fake_delete(config, service, args):
        calls.append(("delete", service["uuid"], args.command))
        return {"ok": True, "action": "deleted", "uuid": service["uuid"]}

    monkeypatch.setattr(module, "load_config", fake_load_config)
    monkeypatch.setattr(module, "find_smoke_service", fake_find)
    monkeypatch.setattr(module, "delete_smoke_service", fake_delete)

    args = Namespace(site_id="hub-site", name="", timeout=30.0, command="toggle")
    result = module.command_toggle(args)

    assert result["ok"] is True
    assert result["action"] == "deleted"
    assert calls == [("delete", "service-1", "toggle")]


def test_toggle_creates_then_deploys_when_missing(monkeypatch, tmp_path: Path) -> None:
    module = load_module()
    calls: list[str] = []

    def fake_load_config(args):
        return {
            "repo_root": tmp_path,
            "site_id": "hub-site",
            "controller_id": "coolify-local",
            "base_url": "http://127.0.0.1:17056",
            "token": "token",
            "state_path": tmp_path / "state.json",
        }

    def fake_find(config, name, timeout):
        return None

    def fake_create(config, name, args):
        calls.append(f"create:{name}")
        return {"ok": True, "action": "created", "uuid": "service-1"}

    def fake_deploy(config, uuid, args):
        calls.append(f"deploy:{uuid}")
        return {"ok": True, "action": "deploy_requested", "uuid": uuid}

    monkeypatch.setattr(module, "load_config", fake_load_config)
    monkeypatch.setattr(module, "find_smoke_service", fake_find)
    monkeypatch.setattr(module, "create_smoke_service", fake_create)
    monkeypatch.setattr(module, "deploy_uuid", fake_deploy)

    args = Namespace(site_id="hub-site", name="", timeout=30.0, command="toggle")
    result = module.command_toggle(args)

    assert result["ok"] is True
    assert result["action"] == "created_and_deployed"
    assert calls == ["create:main-computer-twiddle-smoke-hub-site", "deploy:service-1"]
